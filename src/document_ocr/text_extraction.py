"""Shared text extraction helpers for document processors.

These helpers keep upload decoding, PDF text extraction, OCR, and response
normalization out of country-specific parsers.

Call path example:
`/api/voter-id` -> `document_ocr.voter_id.processor` ->
`extract_text_from_upload` -> `countries.<country>.voter_id`.
"""

from __future__ import annotations

import re
from io import BytesIO
from typing import Any, Dict

import cv2
import numpy as np
import pdfplumber
import pypdfium2 as pdfium

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
