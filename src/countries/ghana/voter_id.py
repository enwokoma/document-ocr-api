"""Ghana voter ID parsing rules."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from src.countries.registry import country_validation_summary


def parse_ghana_voter_id(text: str) -> Dict[str, Any]:
    """Parse OCR text from a Ghana voter ID card."""
    data = {
        "voter_id": _first_match(text, r"\b(?:VOTER\s*ID|ID\s*NO|CARD\s*NO)[:\s-]*([A-Z0-9-]{6,30})"),
        "full_name": _first_match(text, r"\b(?:NAME|FULL\s*NAME)[:\s-]*([A-Z][A-Z ,'-]{3,80})"),
        "date_of_birth": _first_match(text, r"\b(?:DATE\s*OF\s*BIRTH|DOB)[:\s-]*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})"),
        "gender": _normalize_gender(_first_match(text, r"\b(?:SEX|GENDER)[:\s-]*(MALE|FEMALE|M|F)\b")),
        "polling_station": _first_match(text, r"\bPOLLING\s*STATION[:\s-]*([A-Z0-9 ,'-]{3,80})"),
    }
    data = {key: value for key, value in data.items() if value}
    success = bool(data.get("voter_id") or data.get("full_name"))
    return {
        "success": success,
        "message": None if success else "Could not extract Ghana voter ID data.",
        "document_type": "VOTER_ID",
        "country": country_validation_summary(country_code="GHA", document_type="VOTER_ID", extracted_data=data),
        "data": data,
        "raw_text": text if not success else None,
    }


def _first_match(text: str, pattern: str) -> Optional[str]:
    """Return the first regex group from OCR text."""
    match = re.search(pattern, text or "", flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", match.group(1)).strip(" :;,.|-") if match else None


def _normalize_gender(value: Optional[str]) -> Optional[str]:
    """Return a full gender label when OCR reads a gender token."""
    if not value:
        return None
    value = value.upper()
    if value == "M":
        return "MALE"
    if value == "F":
        return "FEMALE"
    return value
