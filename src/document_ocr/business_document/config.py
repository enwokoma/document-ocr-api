"""Environment-backed limits for business-document extraction."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BusinessDocumentSettings:
    """Bounded settings used by the business-document route and processor."""

    max_pages: int = 20
    max_upload_bytes: int = 20 * 1024 * 1024
    max_image_pixels: int = 25_000_000
    max_page_text_chars: int = 100_000
    compare_rendered_pdf_text: bool = True


def get_business_document_settings() -> BusinessDocumentSettings:
    """Load business-document settings while rejecting unsafe values."""
    return BusinessDocumentSettings(
        max_pages=_bounded_int("BUSINESS_DOCUMENT_MAX_PAGES", default=20, minimum=1, maximum=100),
        max_upload_bytes=_bounded_int(
            "BUSINESS_DOCUMENT_MAX_UPLOAD_BYTES",
            default=20 * 1024 * 1024,
            minimum=1024,
            maximum=100 * 1024 * 1024,
        ),
        max_image_pixels=_bounded_int(
            "BUSINESS_DOCUMENT_MAX_IMAGE_PIXELS",
            default=25_000_000,
            minimum=1_000_000,
            maximum=50_000_000,
        ),
        max_page_text_chars=_bounded_int(
            "BUSINESS_DOCUMENT_MAX_PAGE_TEXT_CHARS",
            default=100_000,
            minimum=10_000,
            maximum=500_000,
        ),
        compare_rendered_pdf_text=_boolean_env("BUSINESS_DOCUMENT_COMPARE_RENDERED_PDF_TEXT", default=True),
    )


def _bounded_int(name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _boolean_env(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
