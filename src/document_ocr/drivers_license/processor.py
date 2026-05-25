"""Shared driver's license processor.

The upload/OCR flow is shared, while country-specific field parsing lives under
`src.countries.<country>.drivers_license`.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from src.countries.ghana.drivers_license import parse_ghana_drivers_license
from src.countries.nigeria.drivers_license import parse_nigeria_drivers_license
from src.document_ocr.text_extraction import canonical_document_error, extract_text_from_upload

COUNTRY_PARSERS: Dict[str, Callable[[str], Dict[str, Any]]] = {
    "GHA": parse_ghana_drivers_license,
    "NGA": parse_nigeria_drivers_license,
}


def extract_drivers_license_data(file_stream, *, country_code: str = "NGA", is_pdf: bool = False) -> Dict[str, Any]:
    """Extract driver's license data for the requested country."""
    country_code = (country_code or "NGA").upper()
    parser = COUNTRY_PARSERS.get(country_code)
    if parser is None:
        return canonical_document_error(
            "DRIVERS_LICENSE",
            f"Driver's license OCR is not implemented for {country_code}",
        )

    text = extract_text_from_upload(file_stream, is_pdf=is_pdf)
    if not text:
        return canonical_document_error(
            "DRIVERS_LICENSE",
            "Could not extract text from driver's license document.",
        )

    return parser(text)
