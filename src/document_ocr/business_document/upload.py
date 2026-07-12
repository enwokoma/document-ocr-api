"""Bounded upload inspection for the business-document processor."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from src.core.document_source import detect_document_file_type


@dataclass(frozen=True)
class BusinessUploadInspection:
    """Validated upload metadata without retaining document bytes."""

    valid: bool
    file_type: Optional[str]
    is_pdf: bool
    size_bytes: Optional[int]
    message: Optional[str] = None
    warning: Optional[str] = None


_IMAGE_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


def inspect_business_upload(
    file_stream: Any,
    *,
    filename: Optional[str] = None,
    max_upload_bytes: int,
) -> BusinessUploadInspection:
    """Validate size and file signature, then rewind the upload stream."""
    name = filename or getattr(file_stream, "filename", "") or ""
    extension = Path(name).suffix.lower()
    size = _stream_size(file_stream)
    if size is not None and size <= 0:
        return BusinessUploadInspection(False, None, False, size, "The uploaded document is empty.")
    if size is not None and size > max_upload_bytes:
        return BusinessUploadInspection(
            False,
            None,
            False,
            size,
            f"The uploaded document exceeds the {max_upload_bytes}-byte limit.",
        )

    header = _read_header(file_stream)
    detected_file_type = detect_document_file_type(header)
    if detected_file_type is None:
        return BusinessUploadInspection(
            False,
            None,
            False,
            size,
            "Unsupported document format. Upload a PDF or a supported image.",
        )

    expected_is_pdf = extension == ".pdf"
    detected = "pdf" if detected_file_type == "pdf" else "image"
    detected_is_pdf = detected_file_type == "pdf"
    if expected_is_pdf != detected_is_pdf and extension in ({".pdf"} | _IMAGE_EXTENSIONS):
        return BusinessUploadInspection(
            False,
            detected,
            detected_is_pdf,
            size,
            "The filename extension does not match the uploaded document content.",
        )
    warning = None
    if extension and extension not in ({".pdf"} | _IMAGE_EXTENSIONS):
        warning = "unrecognized_filename_extension"
    file_type = "pdf" if detected_is_pdf else extension.lstrip(".") if extension in _IMAGE_EXTENSIONS else "image"
    return BusinessUploadInspection(
        True,
        file_type,
        detected_is_pdf,
        size,
        warning=warning,
    )


def _stream_size(file_stream: Any) -> Optional[int]:
    try:
        original = file_stream.tell()
        file_stream.seek(0, 2)
        size = int(file_stream.tell())
        file_stream.seek(original)
        return size
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _read_header(file_stream: Any, length: int = 16) -> bytes:
    try:
        original = file_stream.tell()
    except (AttributeError, OSError, TypeError, ValueError):
        original = None
    try:
        if hasattr(file_stream, "seek"):
            file_stream.seek(0)
        header = file_stream.read(length) or b""
        return bytes(header)
    except (AttributeError, OSError, TypeError, ValueError):
        return b""
    finally:
        if hasattr(file_stream, "seek"):
            try:
                file_stream.seek(0 if original is None else original)
            except (OSError, TypeError, ValueError):
                pass
