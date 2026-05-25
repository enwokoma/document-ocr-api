"""Shared voter ID processor.

Different countries use slightly different public names, such as Nigeria's
"Voter's Card" and Ghana's "Voter ID". This processor uses the canonical folder
name `voter_id` and delegates country-specific field parsing to `src.countries`.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from src.countries.ghana.voter_id import parse_ghana_voter_id
from src.countries.nigeria.voter_id import parse_nigeria_voter_card
from src.document_ocr.text_extraction import canonical_document_error, extract_text_from_upload

COUNTRY_PARSERS: Dict[str, Callable[[str], Dict[str, Any]]] = {
    "GHA": parse_ghana_voter_id,
    "NGA": parse_nigeria_voter_card,
}


def extract_voter_id_data(file_stream, *, country_code: str = "NGA", is_pdf: bool = False) -> Dict[str, Any]:
    """Extract voter identity data for the requested country.

    This is where `text_extraction.py` runs: the shared processor first converts
    the upload to text, then passes that text to the selected country parser.
    """
    country_code = (country_code or "NGA").upper()
    parser = COUNTRY_PARSERS.get(country_code)
    if parser is None:
        return canonical_document_error("VOTER_ID", f"Voter ID OCR is not implemented for {country_code}")

    text = extract_text_from_upload(file_stream, is_pdf=is_pdf)
    if not text:
        return canonical_document_error(
            "VOTER_ID",
            "Could not extract text from voter ID document.",
        )

    return parser(text)
