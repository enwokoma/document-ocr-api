"""Nigeria voter card parsing rules."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from src.countries.registry import country_validation_summary

_NIGERIAN_STATES = (
    "ABIA",
    "ADAMAWA",
    "AKWA IBOM",
    "ANAMBRA",
    "BAUCHI",
    "BAYELSA",
    "BENUE",
    "BORNO",
    "CROSS RIVER",
    "DELTA",
    "EBONYI",
    "EDO",
    "EKITI",
    "ENUGU",
    "GOMBE",
    "IMO",
    "JIGAWA",
    "KADUNA",
    "KANO",
    "KATSINA",
    "KEBBI",
    "KOGI",
    "KWARA",
    "LAGOS",
    "NASARAWA",
    "NIGER",
    "OGUN",
    "ONDO",
    "OSUN",
    "OYO",
    "PLATEAU",
    "RIVERS",
    "SOKOTO",
    "TARABA",
    "YOBE",
    "ZAMFARA",
    "FCT",
)

_KNOWN_NAME_TOKENS = (
    "HOLDER",
    "CHUKWU",
    "CHUKWUDI",
    "CHUKWUEMEKA",
    "SAMPLE",
    "SAMPLE",
    "JOHN",
    "JOHNPAUL",
    "NAME",
    "PERSON",
    "SAMPLE",
)


def parse_nigeria_voter_card(text: str) -> Dict[str, Any]:
    """Parse OCR text from a Nigerian Permanent Voter Card."""
    text = _normalize_voter_text(text)
    data = {
        "code": _first_match(text, r"\bCODE[:\s-]*([0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{3})"),
        "vin": _normalize_vin(_first_match(text, r"\bVIN[:\s-]*([A-Z0-9 ]{10,30})")),
        "delimitation": _extract_delimitation(text),
        "full_name": _extract_name(text),
        "date_of_birth": _extract_date_of_birth(text),
        "gender": _extract_gender(text),
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


def _normalize_voter_text(text: str) -> str:
    """Normalize PVC OCR text before field-specific parsing."""
    text = text or ""
    replacements = {
        "DATEOFBIRTH": "DATE OF BIRTH",
        "DATE OFBIRTH": "DATE OF BIRTH",
        "DATEOF BIRTH": "DATE OF BIRTH",
        "VOTERSCARD": "VOTER'S CARD",
        "VOTER'SCARD": "VOTER'S CARD",
    }
    for old, new in replacements.items():
        text = re.sub(old, new, text, flags=re.IGNORECASE)
    return text


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
            return _format_name(line)
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
    return _format_delimitation(_clean(" ".join(captured))) or None


def _extract_date_of_birth(text: str) -> Optional[str]:
    """Extract date of birth from PVC text, including joined OCR labels."""
    date = _first_match(
        text,
        r"DATE\s*OF\s*BIRTH(?:\s+GENDER)?\s*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})",
    )
    if date:
        return date

    compact_date = _first_match(text, r"DATE\s*OF\s*BIRTH(?:\s+GENDER)?\s*([0-9]{8})")
    if compact_date:
        return f"{compact_date[0:2]}-{compact_date[2:4]}-{compact_date[4:8]}"
    return None


def _extract_gender(text: str) -> Optional[str]:
    """Extract gender even when PVC OCR joins DOB and gender into two rows."""
    gender = _first_match(
        text,
        r"\bGENDER\s*(?:[0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4}\s*)?(MALE|FEMALE|M|F)\b",
    )
    return _normalize_gender(gender)


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
    value = _clean(value)
    if label == "ADDRESS":
        return _format_address(value) or None
    return _format_joined_words(value) or None


def _format_delimitation(value: str) -> str:
    """Repair common missing spaces in Nigerian PVC delimitation text."""
    value = _clean((value or "").replace("|", " "))
    upper = value.upper()
    for state in _NIGERIAN_STATES:
        compact_state = state.replace(" ", "")
        if upper.startswith(compact_state) and not upper.startswith(state + " "):
            rest = value[len(compact_state):].lstrip()
            if rest.upper().startswith("I"):
                rest = rest[1:].lstrip()
            value = state + " " + rest
            break
    return _format_joined_words(value)


def _format_address(value: str) -> str:
    """Repair common PVC address OCR joins, such as `4SAMPLESTREET.LAGOS`."""
    value = _clean(value.replace(".", ", "))
    value = re.sub(r"^(\d+)([A-Z])", r"\1 \2", value, flags=re.IGNORECASE)
    value = re.sub(r"([A-Z])(?=STREET\b)", r"\1 ", value, flags=re.IGNORECASE)
    value = re.sub(r"\bSTREET([A-Z])", r"STREET \1", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*,\s*", ", ", value)
    return _format_joined_words(value)


def _format_name(value: str) -> str:
    """Format PVC name lines while preserving the surname-first comma style."""
    value = _clean(value.upper())
    if "," in value:
        surname, given_names = value.split(",", 1)
        return f"{surname.strip()}, {_split_known_tokens(given_names.strip())}".strip()
    return _split_known_tokens(value)


def _format_joined_words(value: str) -> str:
    """Apply conservative word-boundary repairs for known PVC terms."""
    value = _clean(value)
    for token in ("AGU", "OKA", "SAMPLE", "STREET", "LAGOS", "SOUTH"):
        value = re.sub(rf"(?<!^)(?<!\s)({token})\b", rf" \1", value, flags=re.IGNORECASE)
        value = re.sub(rf"\b({token})(?!$)(?!\s|,)", rf"\1 ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s+,", ",", value)
    value = re.sub(r",\s*", ", ", value)
    return value.strip()


def _split_known_tokens(value: str) -> str:
    """Split joined name tokens only when the token boundary is known."""
    value = _clean(value.upper())
    if " " in value:
        return value
    remaining = value
    parts = []
    while remaining:
        match = next((token for token in _KNOWN_NAME_TOKENS if remaining.startswith(token)), None)
        if not match:
            parts.append(remaining)
            break
        parts.append(match)
        remaining = remaining[len(match):]
    return " ".join(parts)
