"""Shared text extraction helpers for document processors.

These helpers keep upload decoding, PDF text extraction, OCR, and response
normalization out of country-specific parsers.
"""

from __future__ import annotations

from typing import Any, Dict

import pdfplumber

from src.core.ocr_engine import get_document_engine, get_image_from_stream, improve_image_quality

engine = get_document_engine()


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


def _extract_text_from_pdf(file_stream) -> str:
    """Extract embedded text from the first few pages of a PDF."""
    text = ""
    try:
        with pdfplumber.open(file_stream) as pdf:
            for page in pdf.pages[:3]:
                text += (page.extract_text() or "") + "\n"
    except Exception:
        return ""
    return text.strip()
