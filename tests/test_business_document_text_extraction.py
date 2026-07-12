"""Tests for the page-aware OCR boundary used by business documents."""

from __future__ import annotations

import io

import cv2
import numpy as np

from src.core.ocr_engine import OCRBox
from src.document_ocr import text_extraction


def test_page_aware_image_extraction_retains_source_and_mean_confidence(monkeypatch):
    """Image uploads should expose one page and aggregate OCR confidence."""
    image = np.zeros((10, 10, 3), dtype=np.uint8)
    boxes = [OCRBox("SAMPLE", 0.8), OCRBox("COMPANY", 1.0)]
    monkeypatch.setattr(text_extraction, "_decode_bounded_image", lambda stream, **kwargs: (image, None))
    monkeypatch.setattr(text_extraction, "improve_image_quality", lambda value: value)
    monkeypatch.setattr(text_extraction.engine, "read_text_from_image", lambda value: boxes)
    monkeypatch.setattr(text_extraction.engine, "group_boxes_into_lines", lambda value: "SAMPLE COMPANY")

    result = text_extraction.extract_document_text_pages(object())

    assert result.text == "SAMPLE COMPANY"
    assert result.total_pages == 1
    assert result.pages[0].source == "image_ocr"
    assert result.pages[0].ocr_confidence == 0.9


def test_page_selection_prefers_business_specific_text():
    """A document-aware scorer should beat longer but irrelevant embedded text."""
    embedded = "Unrelated selectable text " * 20
    rendered = "CERTIFICATE OF INCORPORATION\nCOMPANY NAME: SAMPLE LIMITED"

    selected, source, confidence = text_extraction._select_page_text(
        embedded,
        rendered,
        ocr_confidence=0.91,
        page_scorer=lambda value: 10 if "INCORPORATION" in value else 0,
    )

    assert selected == rendered
    assert source == "rendered_ocr"
    assert confidence == 0.91


def test_page_aware_pdf_reports_page_limit_without_losing_selected_pages(monkeypatch):
    """Long PDFs should be bounded and return an explicit truncation warning."""

    class FakePage:
        def __init__(self, text: str):
            self.text = text

        def extract_text(self):
            return self.text

    class FakePdf:
        pages = [FakePage(f"Embedded page {number}") for number in range(1, 5)]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(text_extraction.pdfplumber, "open", lambda value: FakePdf())
    monkeypatch.setattr(
        text_extraction,
        "_extract_rendered_pdf_pages",
        lambda *args, **kwargs: ([("Rendered page 1", 0.9), ("Rendered page 2", 0.8)], 4),
    )

    result = text_extraction._extract_page_aware_pdf_text(
        b"%PDF-synthetic",
        max_pages=2,
        page_scorer=lambda value: 1 if "Rendered" in value else 0,
        compare_rendered_text=True,
    )

    assert result.total_pages == 4
    assert result.truncated is True
    assert result.page_texts == ("Rendered page 1", "Rendered page 2")
    assert "page_limit_reached" in result.warnings


def test_page_aware_image_rejects_decoded_pixel_bombs_before_ocr(monkeypatch):
    image = np.zeros((20, 20, 3), dtype=np.uint8)
    encoded_ok, encoded = cv2.imencode(".png", image)
    assert encoded_ok is True
    monkeypatch.setattr(
        text_extraction.engine,
        "read_text_from_image",
        lambda value: (_ for _ in ()).throw(AssertionError("OCR should not run")),
    )

    result = text_extraction.extract_document_text_pages(
        io.BytesIO(encoded.tobytes()),
        max_image_pixels=100,
    )

    assert result.pages == ()
    assert result.warnings == ("image_pixel_limit_exceeded",)


def test_page_text_is_bounded_and_warns(monkeypatch):
    image = np.zeros((10, 10, 3), dtype=np.uint8)
    monkeypatch.setattr(text_extraction, "_decode_bounded_image", lambda stream, **kwargs: (image, None))
    monkeypatch.setattr(text_extraction, "improve_image_quality", lambda value: value)
    monkeypatch.setattr(text_extraction.engine, "read_text_from_image", lambda value: [OCRBox("TEXT", 0.9)])
    monkeypatch.setattr(text_extraction.engine, "group_boxes_into_lines", lambda value: "A" * 50)

    result = text_extraction.extract_document_text_pages(object(), max_page_text_chars=10)

    assert result.text == "A" * 10
    assert "page_text_limit_reached" in result.warnings


def test_sufficient_embedded_business_text_skips_rendered_ocr(monkeypatch):
    page_text = "CERTIFICATE OF INCORPORATION COMPANY NAME " + "readable registry content " * 30

    class FakePage:
        def extract_text(self):
            return page_text

    class FakePdf:
        pages = [FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    captured: dict[str, object] = {}

    def fake_render(*args, **kwargs):
        captured.update(kwargs)
        return [("", None)], 1

    monkeypatch.setattr(text_extraction.pdfplumber, "open", lambda value: FakePdf())
    monkeypatch.setattr(text_extraction, "_extract_rendered_pdf_pages", fake_render)

    result = text_extraction._extract_page_aware_pdf_text(
        b"%PDF-synthetic",
        max_pages=1,
        page_scorer=lambda value: 8 if "INCORPORATION" in value else 0,
        compare_rendered_text=True,
    )

    assert captured["skip_page_indexes"] == frozenset({0})
    assert result.page_texts == (page_text.strip(),)
