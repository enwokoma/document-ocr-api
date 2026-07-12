"""Shared text extraction helpers for document processors.

These helpers keep upload decoding, PDF text extraction, OCR, and response
normalization out of country-specific parsers.

Call path example:
`/api/voter-id` -> `document_ocr.voter_id.processor` ->
`extract_text_from_upload` -> `countries.<country>.voter_id`.
"""

from __future__ import annotations

import math
import re
import warnings
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Callable, Dict, Optional

import cv2
import numpy as np
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, UnidentifiedImageError

from src.core.ocr_engine import get_document_engine, get_image_from_stream, improve_image_quality

engine = get_document_engine()


@dataclass(frozen=True)
class DocumentTextPage:
    """Text selected for one uploaded document page."""

    page_number: int
    text: str
    source: str
    ocr_confidence: Optional[float] = None


@dataclass(frozen=True)
class ExtractedDocumentText:
    """Page-aware extraction result for multi-page document processors."""

    pages: tuple[DocumentTextPage, ...]
    total_pages: int
    truncated: bool = False
    warnings: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        """Return all non-empty page text in reading order."""
        return "\n".join(page.text for page in self.pages if page.text).strip()

    @property
    def page_texts(self) -> tuple[str, ...]:
        """Return page text without transport metadata for parser evidence lookup."""
        return tuple(page.text for page in self.pages)


def canonical_document_error(document_type: str, message: str, raw_text: str = "") -> Dict[str, Any]:
    """Build the common error shape used by simple document processors."""
    return {
        "success": False,
        "message": message,
        "document_type": document_type,
        "data": {},
        "raw_text": raw_text,
    }


def extract_text_from_upload(file_stream, *, is_pdf: bool = False, enhance_image: bool = True) -> str:
    """Extract text from an uploaded PDF or image file.

    PDF support reads embedded/selectable text. Scanned PDFs should be uploaded
    as images for now, or converted to images before calling the API.
    """
    if is_pdf:
        return _extract_text_from_pdf(file_stream)

    image = get_image_from_stream(file_stream)
    if image is None:
        return ""

    target = improve_image_quality(image) if enhance_image else image
    boxes = engine.read_text_from_image(target)
    return engine.group_boxes_into_lines(boxes)


def extract_document_text_pages(
    file_stream: Any,
    *,
    is_pdf: bool = False,
    enhance_image: bool = True,
    max_pages: int = 20,
    max_image_pixels: int = 25_000_000,
    max_page_text_chars: int = 100_000,
    page_scorer: Optional[Callable[[str], int]] = None,
    compare_rendered_pdf_text: bool = True,
) -> ExtractedDocumentText:
    """Extract page-aware text without changing the legacy string-only API.

    Business records can span many pages and need page numbers for field
    evidence. Embedded PDF text and rendered OCR are compared per page when
    requested; image uploads produce one page with mean OCR confidence.
    """
    page_limit = max(1, int(max_pages))
    scorer = page_scorer or _readability_score
    if is_pdf:
        try:
            pdf_bytes = file_stream.read()
        except (AttributeError, OSError, ValueError):
            return ExtractedDocumentText((), 0, warnings=("document_stream_unreadable",))
        return _extract_page_aware_pdf_text(
            pdf_bytes,
            max_pages=page_limit,
            max_image_pixels=max_image_pixels,
            max_page_text_chars=max_page_text_chars,
            page_scorer=scorer,
            compare_rendered_text=compare_rendered_pdf_text,
        )

    image, image_warning = _decode_bounded_image(file_stream, max_image_pixels=max_image_pixels)
    if image is None:
        return ExtractedDocumentText((), 0, warnings=(image_warning or "invalid_image_format",))
    target = improve_image_quality(image) if enhance_image else image
    boxes = engine.read_text_from_image(target)
    text = engine.group_boxes_into_lines(boxes)
    text_truncated = len(text) > max_page_text_chars
    text = text[:max_page_text_chars]
    confidence = None
    if boxes:
        confidence = round(sum(box.confidence for box in boxes) / len(boxes), 4)
    page = DocumentTextPage(
        page_number=1,
        text=text,
        source="image_ocr",
        ocr_confidence=confidence,
    )
    extraction_warnings = []
    if text_truncated:
        extraction_warnings.append("page_text_limit_reached")
    if not text:
        extraction_warnings.append("no_text_extracted")
    return ExtractedDocumentText((page,), 1, warnings=tuple(extraction_warnings))


def _extract_page_aware_pdf_text(
    pdf_bytes: bytes,
    *,
    max_pages: int,
    page_scorer: Callable[[str], int],
    compare_rendered_text: bool,
    max_image_pixels: int = 25_000_000,
    max_page_text_chars: int = 100_000,
) -> ExtractedDocumentText:
    """Select embedded text or rendered OCR independently for each PDF page."""
    if not pdf_bytes:
        return ExtractedDocumentText((), 0, warnings=("empty_pdf",))

    embedded_pages: list[str] = []
    total_pages = 0
    text_limit_reached = False
    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            total_pages = len(pdf.pages)
            for page in pdf.pages[:max_pages]:
                try:
                    page_text = (page.extract_text() or "").strip()
                    if len(page_text) > max_page_text_chars:
                        text_limit_reached = True
                    embedded_pages.append(page_text[:max_page_text_chars])
                except Exception:
                    embedded_pages.append("")
    except Exception:
        embedded_pages = []

    rendered_pages: list[tuple[str, Optional[float]]] = []
    rendered_total = 0
    needs_rendered_pages = compare_rendered_text or not embedded_pages or any(not page for page in embedded_pages)
    if needs_rendered_pages:
        if compare_rendered_text:
            skip_page_indexes = frozenset(
                index
                for index, page_text in enumerate(embedded_pages)
                if _embedded_text_is_sufficient(page_text, page_scorer=page_scorer)
            )
        else:
            skip_page_indexes = frozenset(index for index, page_text in enumerate(embedded_pages) if page_text)
        rendered_pages, rendered_total = _extract_rendered_pdf_pages(
            pdf_bytes,
            max_pages=max_pages,
            max_image_pixels=max_image_pixels,
            page_scorer=page_scorer,
            skip_page_indexes=skip_page_indexes,
        )
        total_pages = max(total_pages, rendered_total)

    processed_count = min(max(total_pages, len(embedded_pages), len(rendered_pages)), max_pages)
    pages: list[DocumentTextPage] = []
    for index in range(processed_count):
        embedded = embedded_pages[index] if index < len(embedded_pages) else ""
        rendered, ocr_confidence = rendered_pages[index] if index < len(rendered_pages) else ("", None)
        selected, source, confidence = _select_page_text(
            embedded,
            rendered,
            ocr_confidence=ocr_confidence,
            page_scorer=page_scorer,
        )
        if len(selected) > max_page_text_chars:
            text_limit_reached = True
        selected = selected[:max_page_text_chars]
        pages.append(
            DocumentTextPage(
                page_number=index + 1,
                text=selected,
                source=source,
                ocr_confidence=confidence,
            )
        )

    warnings: list[str] = []
    if total_pages == 0:
        warnings.append("invalid_or_unreadable_pdf")
    if total_pages > max_pages:
        warnings.append("page_limit_reached")
    if text_limit_reached:
        warnings.append("page_text_limit_reached")
    if not any(page.text for page in pages):
        warnings.append("no_text_extracted")
    return ExtractedDocumentText(
        pages=tuple(pages),
        total_pages=total_pages,
        truncated=total_pages > max_pages,
        warnings=tuple(warnings),
    )


def _extract_rendered_pdf_pages(
    pdf_bytes: bytes,
    *,
    max_pages: int,
    max_image_pixels: int,
    page_scorer: Callable[[str], int],
    skip_page_indexes: frozenset[int] = frozenset(),
) -> tuple[list[tuple[str, Optional[float]]], int]:
    """Render and OCR PDF pages while keeping page boundaries."""
    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
    except Exception:
        return [], 0

    pages: list[tuple[str, Optional[float]]] = []
    total_pages = len(pdf)
    try:
        for page_index in range(min(total_pages, max_pages)):
            if page_index in skip_page_indexes:
                pages.append(("", None))
                continue
            try:
                page = pdf[page_index]
                render_scale = _bounded_pdf_render_scale(page, max_image_pixels=max_image_pixels)
                bitmap = page.render(scale=render_scale).to_pil()
                image = cv2.cvtColor(np.array(bitmap), cv2.COLOR_RGB2BGR)
                pages.append(_ocr_best_orientation_with_confidence(image, page_scorer=page_scorer))
            except Exception:
                pages.append(("", None))
    finally:
        try:
            pdf.close()
        except Exception:
            pass
    return pages, total_pages


def _ocr_best_orientation_with_confidence(
    image: Any,
    *,
    page_scorer: Callable[[str], int],
) -> tuple[str, Optional[float]]:
    """OCR common page orientations and retain text plus mean OCR confidence."""
    best_text = ""
    best_confidence: Optional[float] = None
    best_rank = (-1, -1, -1.0)
    for rotation in (None, cv2.ROTATE_180, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
        variant = image if rotation is None else cv2.rotate(image, rotation)
        boxes = engine.read_text_from_image(improve_image_quality(variant))
        text = engine.group_boxes_into_lines(boxes)
        confidence = sum(box.confidence for box in boxes) / len(boxes) if boxes else 0.0
        rank = (page_scorer(text), _readability_score(text), confidence)
        if rank > best_rank:
            best_text = text
            best_confidence = round(confidence, 4) if boxes else None
            best_rank = rank
        if rotation is None and _ocr_text_is_sufficient(text, confidence=confidence, page_scorer=page_scorer):
            break
    return best_text, best_confidence


def _decode_bounded_image(file_stream: Any, *, max_image_pixels: int) -> tuple[Any, Optional[str]]:
    """Inspect compressed image dimensions before allowing OpenCV to decode it."""
    try:
        image_bytes = file_stream.read()
    except (AttributeError, OSError, TypeError, ValueError):
        return None, "document_stream_unreadable"
    if not image_bytes:
        return None, "invalid_image_format"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(image_bytes)) as probe:
                width, height = probe.size
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None, "image_pixel_limit_exceeded"
    except (UnidentifiedImageError, OSError, TypeError, ValueError):
        return None, "invalid_image_format"
    if width <= 0 or height <= 0 or width * height > max(1, int(max_image_pixels)):
        return None, "image_pixel_limit_exceeded"
    encoded = np.frombuffer(image_bytes, np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None:
        return None, "invalid_image_format"
    if int(image.shape[0]) * int(image.shape[1]) > max(1, int(max_image_pixels)):
        return None, "image_pixel_limit_exceeded"
    return image, None


def _bounded_pdf_render_scale(page: Any, *, max_image_pixels: int) -> float:
    """Choose the highest useful PDF render scale within the decoded-pixel budget."""
    try:
        width, height = page.get_size()
        area = float(width) * float(height)
    except (AttributeError, TypeError, ValueError):
        return 2.0
    if area <= 0:
        return 2.0
    scale = min(4.0, math.sqrt(max(1, int(max_image_pixels)) / area))
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("PDF page dimensions cannot be rendered safely")
    return scale


def _embedded_text_is_sufficient(text: str, *, page_scorer: Callable[[str], int]) -> bool:
    """Avoid expensive rendered OCR when selectable page text is already useful."""
    return page_scorer(text) >= 4 and _readability_score(text) >= 60


def _ocr_text_is_sufficient(
    text: str,
    *,
    confidence: float,
    page_scorer: Callable[[str], int],
) -> bool:
    """Stop orientation retries once upright OCR is confidently document-like."""
    return confidence >= 0.65 and page_scorer(text) >= 4 and _readability_score(text) >= 50


def _select_page_text(
    embedded: str,
    rendered: str,
    *,
    ocr_confidence: Optional[float],
    page_scorer: Callable[[str], int],
) -> tuple[str, str, Optional[float]]:
    """Choose the more useful representation of one PDF page."""
    if not embedded:
        return rendered, "rendered_ocr", ocr_confidence
    if not rendered:
        return embedded, "embedded_pdf_text", None
    embedded_rank = (page_scorer(embedded), _readability_score(embedded))
    rendered_rank = (page_scorer(rendered), _readability_score(rendered))
    if rendered_rank > embedded_rank:
        return rendered, "rendered_ocr", ocr_confidence
    return embedded, "embedded_pdf_text", None


def _readability_score(text: str) -> int:
    """Score useful text volume without assuming a particular document family."""
    value = str(text or "")
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'&./-]*", value)
    lines = [line for line in value.splitlines() if line.strip()]
    replacement_penalty = value.count("\ufffd") * 5
    return max(0, min(len(words), 200) + min(len(lines), 50) * 2 - replacement_penalty)


def _extract_text_from_pdf(file_stream) -> str:
    """Extract text from PDF, preferring rendered OCR when embedded text is poor."""
    pdf_bytes = file_stream.read()
    embedded_text = _extract_embedded_pdf_text(pdf_bytes)
    rendered_text = _extract_rendered_pdf_text(pdf_bytes)

    if _identity_text_score(rendered_text) > _identity_text_score(embedded_text):
        return rendered_text
    return embedded_text


def _extract_embedded_pdf_text(pdf_bytes: bytes) -> str:
    """Extract selectable text from the first few PDF pages."""
    text = ""
    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:3]:
                text += (page.extract_text() or "") + "\n"
    except Exception:
        return ""
    return text.strip()


def _extract_rendered_pdf_text(pdf_bytes: bytes) -> str:
    """Render PDF pages as images and OCR the best page orientation."""
    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
    except Exception:
        return ""

    page_texts = []
    try:
        page_count = min(len(pdf), 3)
        for page_index in range(page_count):
            page = pdf[page_index]
            bitmap = page.render(scale=4).to_pil()
            image = cv2.cvtColor(np.array(bitmap), cv2.COLOR_RGB2BGR)
            page_text = _ocr_best_orientation(image)
            if page_text:
                page_texts.append(page_text)
    except Exception:
        return ""
    finally:
        try:
            pdf.close()
        except Exception:
            pass
    return "\n".join(page_texts).strip()


def _ocr_best_orientation(image) -> str:
    """OCR the page in common orientations and keep the most document-like text."""
    variants = (
        image,
        cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE),
        cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE),
        cv2.rotate(image, cv2.ROTATE_180),
    )
    best_text = ""
    best_score = -1
    for variant in variants:
        boxes = engine.read_text_from_image(improve_image_quality(variant))
        text = engine.group_boxes_into_lines(boxes)
        score = _identity_text_score(text)
        if score > best_score:
            best_text = text
            best_score = score
    return best_text


def _identity_text_score(text: str) -> int:
    """Score whether OCR text looks like a readable identity document."""
    upper = (text or "").upper()
    score = 0
    for keyword in (
        "FEDERAL",
        "REPUBLIC",
        "VOTER",
        "CODE",
        "VIN",
        "DELIM",
        "DATE",
        "BIRTH",
        "GENDER",
        "OCCUPATION",
        "ADDRESS",
    ):
        if keyword in upper:
            score += 2
    score += 8 if re.search(r"\bCODE\s*:\s*\d{2}-\d{2}-\d{2}-\d{3}", upper) else 0
    score += 8 if re.search(r"\bVIN\s*:\s*[A-Z0-9 ]{10,30}", upper) else 0
    score += 5 if re.search(r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", upper) else 0
    score += 5 if re.search(r"\b(MALE|FEMALE)\b", upper) else 0
    return score
