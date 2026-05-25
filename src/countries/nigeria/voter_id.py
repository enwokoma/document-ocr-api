"""Nigeria voter card parsing rules."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from src.countries.registry import country_validation_summary


def parse_nigeria_voter_card(text: str) -> Dict[str, Any]:
    """Parse OCR text from a Nigerian Permanent Voter Card."""
    data = {
        "code": _first_match(text, r"\bCODE[:\s-]*([0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{3})"),
        "vin": _normalize_vin(_first_match(text, r"\bVIN[:\s-]*([A-Z0-9 ]{10,30})")),
        "delimitation": _extract_delimitation(text),
        "full_name": _extract_name(text),
        "date_of_birth": _first_match(text, r"DATE\s+OF\s+BIRTH\s*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})"),
        "gender": _normalize_gender(_first_match(text, r"\bGENDER\s*(MALE|FEMALE|M|F)\b")),
        "occupation": _extract_after_label(text, "OCCUPATION", stop_labels=("ADDRESS",)),
        "address": _extract_after_label(text, "ADDRESS", stop_labels=()),
    }
    data = {key: value for key, value in data.items() if value}
    success = bool(data.get("vin") or data.get("full_name"))
    return {
        "success": success,
        "message": None if success else "Could not extract Nigerian voter card data.",
        "document_type": "VOTER_CARD",
        "country": country_validation_summary(country_code="NGA", document_type="VOTER_CARD", extracted_data=data),
        "data": data,
        "raw_text": text if not success else None,
    }


def _first_match(text: str, pattern: str) -> Optional[str]:
    """Return the first regex group from OCR text."""
    match = re.search(pattern, text or "", flags=re.IGNORECASE)
    return _clean(match.group(1)) if match else None


def _clean(value: str) -> str:
    """Collapse whitespace and punctuation around a parsed value."""
    return re.sub(r"\s+", " ", value or "").strip(" :;,.|-")


def _normalize_vin(value: Optional[str]) -> Optional[str]:
    """Normalize a voter identification number while preserving grouping."""
    if not value:
        return None
    cleaned = re.sub(r"[^A-Z0-9]", "", value.upper())
    return cleaned or None


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


def _extract_name(text: str) -> Optional[str]:
    """Find the all-caps name line normally printed after delimitation."""
    lines = [_clean(line).upper() for line in (text or "").splitlines() if _clean(line)]
    skip_words = {
        "FEDERAL",
        "REPUBLIC",
        "NIGERIA",
        "INDEPENDENT",
        "NATIONAL",
        "ELECTORAL",
        "COMMISSION",
        "VOTER",
        "CARD",
        "CODE",
        "VIN",
        "DELIM",
        "DATE",
        "BIRTH",
        "GENDER",
        "OCCUPATION",
        "ADDRESS",
    }
    for line in lines:
        if "," not in line:
            continue
        words = re.findall(r"[A-Z]{2,}", line)
        if len(words) >= 2 and not any(word in skip_words for word in words):
            return line
    return None


def _extract_delimitation(text: str) -> Optional[str]:
    """Extract the electoral delimitation section after `DELIM`."""
    lines = [_clean(line) for line in (text or "").splitlines() if _clean(line)]
    captured = []
    collecting = False
    for line in lines:
        upper = line.upper()
        if "DELIM" in upper:
            collecting = True
            value = re.sub(r"^.*?\bDELIM\b[:\s]*", "", line, flags=re.IGNORECASE)
            if value:
                captured.append(_clean(value))
            continue
        if not collecting:
            continue
        if "," in line or re.search(r"\b(DATE OF BIRTH|GENDER|OCCUPATION|ADDRESS)\b", upper):
            break
        captured.append(line)
    return _clean(" ".join(captured)) or None


def _extract_after_label(text: str, label: str, *, stop_labels: tuple[str, ...]) -> Optional[str]:
    """Extract text after one label until another known label appears."""
    normalized = re.sub(r"\s+", " ", text or "")
    pattern = rf"{re.escape(label)}[:\s]*(.+)"
    match = re.search(pattern, normalized, flags=re.IGNORECASE)
    if not match:
        return None
    value = match.group(1)
    for stop_label in stop_labels:
        stop = re.search(rf"\b{re.escape(stop_label)}\b", value, flags=re.IGNORECASE)
        if stop:
            value = value[: stop.start()]
            break
    return _clean(value) or None
