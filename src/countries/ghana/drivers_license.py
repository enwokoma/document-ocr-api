"""Ghana driver's license parsing rules."""

from __future__ import annotations

from typing import Any, Dict

from src.countries.registry import country_validation_summary
from src.countries.shared_parsing import first_match, normalize_gender


def parse_ghana_drivers_license(text: str) -> Dict[str, Any]:
    """Parse common fields from a Ghana driver's license."""
    data = {
        "license_number": first_match(text, r"\b(?:LICENSE\s*(?:NO|NUMBER)|LICENCE\s*(?:NO|NUMBER))[:\s-]*([A-Z0-9-]{6,30})"),
        "full_name": first_match(text, r"\b(?:NAME|FULL\s*NAME|NAMES)[:\s-]*([A-Z][A-Z ,'-]{3,80})"),
        "date_of_birth": first_match(text, r"\b(?:DOB|DATE\s*OF\s*BIRTH)[:\s-]*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})"),
        "issue_date": first_match(text, r"\b(?:ISSUE\s*DATE|DATE\s*ISSUED)[:\s-]*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})"),
        "expiry_date": first_match(text, r"\b(?:EXPIRY\s*DATE|EXPIRES|EXP)[:\s-]*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})"),
        "license_class": first_match(text, r"\b(?:CLASS|CATEGORY)[:\s-]*([A-Z0-9]{1,4})\b"),
        "gender": normalize_gender(first_match(text, r"\b(?:SEX|GENDER)[:\s-]*(MALE|FEMALE|M|F)\b")),
    }
    data = {key: value for key, value in data.items() if value}
    success = bool(data.get("license_number") or data.get("full_name"))
    return {
        "success": success,
        "message": None if success else "Could not extract Ghana driver's license data.",
        "document_type": "DRIVERS_LICENSE",
        "country": country_validation_summary(country_code="GHA", document_type="DRIVERS_LICENSE", extracted_data=data),
        "data": data,
        "raw_text": text if not success else None,
    }
