"""OCR backend abstraction.

Processors should not import RapidOCR or EasyOCR directly. They call this module
so the app can choose the best installed OCR backend, reuse one engine instance,
and return text boxes in one consistent shape.
"""

import cv2
import numpy as np
import re
import os
import warnings
import threading
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Tuple, Dict

@dataclass
class OCRBox:
    """Single OCR result with text, confidence, and optional polygon points."""

    text: str
    confidence: float
    box: Optional[List[List[float]]] = None

    @property
    def x_min(self) -> float:
        """Left-most x coordinate, used when sorting words into reading order."""
        if self.box is None or len(self.box) == 0: return 0.0
        return min(float(p[0]) for p in self.box)

    @property
    def y_mid(self) -> float:
        """Vertical midpoint, used to decide which words belong on one line."""
        if self.box is None or len(self.box) == 0: return 0.0
        return sum(float(p[1]) for p in self.box) / max(len(self.box), 1)

    @property
    def height(self) -> float:
        """Approximate text-box height for line grouping tolerance."""
        if self.box is None or len(self.box) == 0: return 12.0
        ys = [float(p[1]) for p in self.box]
        return max(ys) - min(ys)

class DocumentEngine:
    """
    Wrapper around the available OCR backend.

    Important: we lazy-initialize the underlying RapidOCR engine so the model
    loads once per process and only when first needed.
    """

    def __init__(self):
        """Create a lazy OCR wrapper without loading any model yet."""
        self.engine = None
        self.backend = None
        self._init_attempted = False
        self._init_lock = threading.Lock()

    def _ensure_engine(self) -> bool:
        """Load the first available OCR backend exactly once per process."""
        if self.engine is not None:
            return True
        if self._init_attempted:
            return False

        with self._init_lock:
            if self.engine is not None:
                return True
            if self._init_attempted:
                return False
            self._init_attempted = True
            try:
                from rapidocr import RapidOCR
                self.engine = RapidOCR()
                self.backend = "rapidocr"
                return True
            except ImportError:
                pass

            try:
                from rapidocr_onnxruntime import RapidOCR
                self.engine = RapidOCR()
                self.backend = "rapidocr_onnxruntime"
                warnings.warn(
                    "Using legacy rapidocr_onnxruntime backend. Install 'rapidocr' for the maintained backend."
                )
                return True
            except ImportError:
                pass

            if os.getenv("ENABLE_EASYOCR_FALLBACK") != "1":
                self.engine = None
                self.backend = None
                warnings.warn(
                    "RapidOCR not installed. Install 'rapidocr', or "
                    "set ENABLE_EASYOCR_FALLBACK=1 to use the slower EasyOCR fallback."
                )
                return False

            try:
                import easyocr
                self.engine = easyocr.Reader(["en"], gpu=False, verbose=False)
                self.backend = "easyocr"
                warnings.warn(
                    "RapidOCR not installed. Falling back to EasyOCR. "
                    "Install 'rapidocr' for the preferred backend."
                )
                return True
            except Exception as exc:
                self.engine = None
                self.backend = None
                warnings.warn(
                    "No OCR backend available. Install 'rapidocr' "
                    f"or EasyOCR. Last error: {exc}"
                )
                return False

    def is_available(self) -> bool:
        """Return whether at least one OCR backend can be used."""
        return self._ensure_engine()

    def read_text_from_image(self, image_np: Any) -> List[OCRBox]:
        """Run OCR on an image and normalize backend-specific results."""
        if not self._ensure_engine():
            return []

        if self.backend == "rapidocr_onnxruntime":
            results, _ = self.engine(image_np, use_cls=False)
        elif self.backend == "rapidocr":
            output = self.engine(image_np)
            boxes = output.boxes if output.boxes is not None else []
            txts = output.txts if output.txts is not None else []
            scores = output.scores if output.scores is not None else []
            results = [
                (box, text, score)
                for box, text, score in zip(boxes, txts, scores)
            ]
        else:
            results = self.engine.readtext(image_np, detail=1, paragraph=False)

        if not results:
            return []

        parsed_boxes = []
        for res in results:
            if len(res) >= 3:
                parsed_boxes.append(OCRBox(
                    text=str(res[1]),
                    confidence=float(res[2]),
                    box=res[0]
                ))
        return parsed_boxes

    def warmup(self) -> None:
        """Run a tiny OCR request so model initialization happens before traffic."""
        if not self._ensure_engine():
            return
        dummy = np.zeros((64, 64, 3), dtype=np.uint8)
        try:
            if self.backend == "rapidocr_onnxruntime":
                self.engine(dummy, use_cls=False)
            elif self.backend == "rapidocr":
                self.engine(dummy)
            else:
                self.engine.readtext(dummy, detail=1, paragraph=False)
        except Exception:
            pass

    def group_boxes_into_lines(self, boxes: Sequence[OCRBox]) -> str:
        """Convert OCR word boxes into newline-separated text lines."""
        if not boxes:
            return ""

        boxes_sorted = sorted(boxes, key=lambda b: (b.y_mid, b.x_min))

        lines = []
        if not boxes_sorted:
            return ""

        current_row = [boxes_sorted[0]]
        for i in range(1, len(boxes_sorted)):
            box = boxes_sorted[i]
            prev_box = current_row[-1]

            avg_h = sum(max(b.height, 10) for b in current_row) / len(current_row)
            if abs(box.y_mid - prev_box.y_mid) <= (avg_h * 0.7):
                current_row.append(box)
            else:
                current_row.sort(key=lambda b: b.x_min)
                lines.append(" ".join(b.text for b in current_row))
                current_row = [box]

        if current_row:
            current_row.sort(key=lambda b: b.x_min)
            lines.append(" ".join(b.text for b in current_row))

        return "\n".join(lines)

def get_image_from_stream(file_stream):
    """Decode a Flask upload stream into an OpenCV BGR image."""
    file_bytes = np.frombuffer(file_stream.read(), np.uint8)
    image = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
    return image

def improve_image_quality(image):
    """Improve contrast before OCR using CLAHE on the grayscale channel."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    return cv2.cvtColor(clahe, cv2.COLOR_GRAY2BGR)

def clean_text(text: str) -> str:
    """Collapse repeated whitespace while preserving normal word spacing."""
    if not text: return ""
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


_ENGINE_SINGLETON: Optional[DocumentEngine] = None
_ENGINE_SINGLETON_LOCK = threading.Lock()


def get_document_engine() -> DocumentEngine:
    """
    Process-wide shared OCR engine.

    This avoids initializing multiple RapidOCR instances (one per module).
    """
    global _ENGINE_SINGLETON
    if _ENGINE_SINGLETON is not None:
        return _ENGINE_SINGLETON
    with _ENGINE_SINGLETON_LOCK:
        if _ENGINE_SINGLETON is None:
            _ENGINE_SINGLETON = DocumentEngine()
        return _ENGINE_SINGLETON

