"""Small parsing helpers shared by country-specific modules."""

from __future__ import annotations

import re
from typing import Optional


def first_match(text: str, pattern: str) -> Optional[str]:
    """Return the first regex group from OCR text with whitespace normalized."""
    match = re.search(pattern, text or "", flags=re.IGNORECASE)
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(1)).strip(" :;,.|-")


def normalize_gender(value: Optional[str]) -> Optional[str]:
    """Normalize gender tokens to full uppercase labels."""
    if not value:
        return None
    value = value.upper()
    if value == "M":
        return "MALE"
    if value == "F":
        return "FEMALE"
    return value
