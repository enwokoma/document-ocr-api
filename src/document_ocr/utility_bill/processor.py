"""Utility bill and utility payment receipt extraction.

The processor focuses on the fields usually needed for proof-of-address checks:
the service address, the receipt/bill date, and the age of that date. It also
extracts supporting electricity receipt fields when they are present.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, Optional

import pdfplumber

from src.core.ocr_engine import clean_text, get_document_engine, get_image_from_stream, improve_image_quality


engine = get_document_engine()


SUPPORTED_COUNTRIES = {"NGA"}
RECENCY_WINDOW_DAYS = 90

CANONICAL_UTILITY_BILL_KEYS = (
    "country_code",
    "utility_type",
    "receipt_type",
    "provider",
    "provider_code",
    "customer_name",
    "service_address",
    "meter_number",
    "meter_type",
    "token",
    "transaction_reference",
    "units_purchased_kwh",
    "amount_paid",
    "document_date",
    "document_date_label",
    "days_old",
    "months_old",
    "is_recent",
    "recency_window_days",
    "confidence",
    "reasons",
)

_DISCO_ALIASES = {
    "ABUJA ELECTRICITY": "AEDC",
    "ABUJA ELECTRICITY DISTRIBUTION": "AEDC",
    "ABUJA ELECTRICITY DISTRIBUTION PREPAID": "AEDC",
    "AEDC": "AEDC",
    "IKEJA ELECTRICITY": "IKEDC",
    "IKEJA ELECTRIC": "IKEDC",
    "IKEDC": "IKEDC",
    "EKO ELECTRICITY": "EKEDC",
    "EKEDC": "EKEDC",
    "IBADAN ELECTRICITY": "IBEDC",
    "IBEDC": "IBEDC",
    "PORT HARCOURT ELECTRICITY": "PHEDC",
    "PHEDC": "PHEDC",
    "JOS ELECTRICITY": "JEDC",
    "JEDC": "JEDC",
    "KANO ELECTRICITY": "KEDCO",
    "KEDCO": "KEDCO",
    "BENIN ELECTRICITY": "BEDC",
    "BEDC": "BEDC",
    "ENUGU ELECTRICITY": "EEDC",
    "EEDC": "EEDC",
    "YOLA ELECTRICITY": "YEDC",
    "YEDC": "YEDC",
}

_MONTHS = {
    "JAN": 1, "JANUARY": 1,
    "FEB": 2, "FEBRUARY": 2,
    "MAR": 3, "MARCH": 3,
    "APR": 4, "APRIL": 4,
    "MAY": 5,
    "JUN": 6, "JUNE": 6,
    "JUL": 7, "JULY": 7,
    "AUG": 8, "AUGUST": 8,
    "SEP": 9, "SEPT": 9, "SEPTEMBER": 9,
    "OCT": 10, "OCTOBER": 10,
    "NOV": 11, "NOVEMBER": 11,
    "DEC": 12, "DECEMBER": 12,
}

_DATE_VALUE = (
    r"(?:[A-Za-z]+,\s*)?"
    r"(?:\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}|[A-Za-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?)"
    r",?\s+\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)?"
    r"|\d{4}[./-]\d{1,2}[./-]\d{1,2}"
    r"|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}"
)

_LABELS = {
    "provider": ("provider", "biller"),
    "customer_name": ("customer name", "meter name", "business name", "account name"),
    "service_address": ("service address", "address", "premises"),
    "meter_number": ("meter number", "meter no", "beneficiary id"),
    "meter_type": ("meter type", "purchase type"),
    "token": ("meter token", "token"),
    "transaction_reference": ("transaction reference", "transaction no", "transaction id", "receipt number", "transaction d"),
    "units_purchased_kwh": ("units purchased", "unit", "units"),
    "amount_paid": ("amount paid", "amount", "total amount paid", "payment amount"),
    "document_date": ("transaction date", "payment date", "receipt date", "vending date", "date"),
}


@dataclass
class ParsedUtilityBill:
    """Structured parser output plus the raw OCR text used to produce it."""

    data: Dict[str, Any]
    raw_text: str


def extract_utility_bill_data(file_path_or_stream: Any, *, is_pdf: bool = False, country_code: str = "NGA") -> Dict[str, Any]:
    """Extract proof-of-address fields from a utility bill or payment receipt."""
    country_code = (country_code or "NGA").upper()
    if country_code not in SUPPORTED_COUNTRIES:
        return _error(f"Unsupported country code for utility bill extraction: {country_code}", country_code=country_code)

    text = _extract_text(file_path_or_stream, is_pdf=is_pdf)
    if not text:
        return _error("Could not extract text from document.", country_code=country_code)

    parsed = parse_utility_bill_text(text, country_code=country_code)
    data = parsed.data
    success = bool(data.get("service_address")) and bool(data.get("document_date"))
    message = None if success else "Could not extract both utility bill address and receipt date."
    return {
        "success": success,
        "message": message,
        "document_type": "UTILITY_BILL",
        "data": _canonical_data(data),
        "raw_text": None if success else parsed.raw_text,
    }


def parse_utility_bill_text(text: str, *, country_code: str = "NGA", today: Optional[date] = None) -> ParsedUtilityBill:
    """Parse utility bill fields from OCR or embedded PDF text."""
    normalized_text = _normalize_ocr_text(text)
    lines = [_clean_line(line) for line in normalized_text.splitlines()]
    lines = [line for line in lines if line]
    data: Dict[str, Any] = {
        "country_code": country_code.upper(),
        "utility_type": "ELECTRICITY" if _looks_like_electricity_receipt(normalized_text) else "UNKNOWN",
        "receipt_type": _detect_receipt_type(normalized_text),
        "reasons": [],
        "recency_window_days": RECENCY_WINDOW_DAYS,
    }

    label_values = _collect_label_values(lines)
    _apply_label_values(data, label_values)
    _apply_fallbacks(data, normalized_text, lines)
    _normalize_provider_fields(data, normalized_text)
    _normalize_amount_and_units(data)
    _apply_date_age(data, today=today)
    data["confidence"] = _score_confidence(data)

    if not data.get("service_address"):
        data["reasons"].append("missing_service_address")
    if not data.get("document_date"):
        data["reasons"].append("missing_document_date")
    if data.get("document_date") and not data.get("is_recent"):
        data["reasons"].append("outside_recency_window")

    return ParsedUtilityBill(data=data, raw_text=normalized_text)


def _extract_text(file_path_or_stream: Any, *, is_pdf: bool) -> str:
    """Read embedded PDF text or run OCR for image uploads."""
    if is_pdf:
        text = _extract_pdf_text(file_path_or_stream)
    else:
        image = get_image_from_stream(file_path_or_stream)
        text = _ocr_image(image) if image is not None else ""
    return _normalize_ocr_text(text)


def _extract_pdf_text(file_path_or_stream: Any) -> str:
    """Extract text from the first pages of a PDF utility bill."""
    try:
        if hasattr(file_path_or_stream, "seek"):
            file_path_or_stream.seek(0)
        with pdfplumber.open(file_path_or_stream) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages[:3])
    except Exception:
        return ""


def _ocr_image(image: Any) -> str:
    """Run the shared OCR engine and group text boxes into readable lines."""
    boxes = engine.read_text_from_image(improve_image_quality(image))
    return engine.group_boxes_into_lines(boxes)


def _normalize_ocr_text(text: str) -> str:
    """Normalize OCR spacing while preserving line boundaries."""
    if not text:
        return ""
    cleaned_lines = [_clean_line(line) for line in str(text).replace("\r", "\n").split("\n")]
    return "\n".join(line for line in cleaned_lines if line)


def _clean_line(line: str) -> str:
    """Clean a single OCR line."""
    line = re.sub(r"[|]+", " ", line or "")
    return clean_text(line)


def _collect_label_values(lines: Iterable[str]) -> Dict[str, str]:
    """Collect values that appear beside or immediately under known labels."""
    items = list(lines)
    values: Dict[str, str] = {}
    for idx, line in enumerate(items):
        key, value = _split_label_value(line)
        if not key:
            continue
        if not value and idx + 1 < len(items) and not _split_label_value(items[idx + 1])[0]:
            value = items[idx + 1]
            if key == "service_address" and idx + 2 < len(items) and not _split_label_value(items[idx + 2])[0]:
                value = f"{value} {items[idx + 2]}"
        if value:
            values.setdefault(key, value)
    return values


def _split_label_value(line: str) -> tuple[Optional[str], Optional[str]]:
    """Return the canonical key and value when a line begins with a known label."""
    compact_line = clean_text(line)
    label_source = compact_line.lower().replace(".", "")
    for key, labels in _LABELS.items():
        for label in labels:
            if not label_source.startswith(label):
                continue
            value = compact_line[len(label):].strip(" :-")
            return key, value or None
    return None, None


def _apply_label_values(data: Dict[str, Any], values: Dict[str, str]) -> None:
    """Move collected label values into the response data."""
    for key, value in values.items():
        if key == "document_date":
            iso = _parse_date_to_iso(value)
            if iso:
                data["document_date"] = iso
                data["document_date_label"] = "transaction_date"
        else:
            data[key] = clean_text(value).upper() if key in {"customer_name", "service_address"} else clean_text(value)


def _apply_fallbacks(data: Dict[str, Any], text: str, lines: list[str]) -> None:
    """Use whole-document scans when label extraction misses a field."""
    if not data.get("document_date"):
        found = _find_best_date(text)
        if found:
            data["document_date"] = found
            data["document_date_label"] = "receipt_date"

    if not data.get("service_address"):
        address = _find_address_from_lines(lines)
        if address:
            data["service_address"] = address

    if not data.get("token"):
        token = _first_match(text, r"\b(\d{4}[-\s]\d{4}[-\s]\d{4}[-\s]\d{4}[-\s]\d{4})\b")
        if token:
            data["token"] = clean_text(token)

    if not data.get("meter_number"):
        meter = _first_match(text, r"(?:Meter\s*Number|Beneficiary\s*ID)\s*[:\-]?\s*(\d{8,16})")
        if not meter:
            meter = _first_match(text, r"\b(\d{10,13})\b")
        if meter:
            data["meter_number"] = meter


def _find_address_from_lines(lines: list[str]) -> Optional[str]:
    """Find likely service-address text when OCR separates label and value."""
    address_words = r"(STREET|ESTATE|ROAD|CLOSE|AVENUE|ZONE|PLOT|PLT|LUGBE|KUBWA|LAGOS|ABUJA|LOKOGOMA)"
    for idx, line in enumerate(lines):
        upper = line.upper()
        if "ADDRESS" not in upper:
            continue
        candidates = []
        inline = re.sub(r"^.*?\b(?:SERVICE\s+)?ADDRESS\b[:\s-]*", "", line, flags=re.IGNORECASE).strip()
        if inline and inline.upper() != upper:
            candidates.append(inline)
        for offset in (1, 2):
            if idx + offset < len(lines):
                candidates.append(lines[idx + offset])
        address = _join_address_lines(candidates)
        if address:
            return address

    for line in lines:
        if re.search(address_words, line, flags=re.IGNORECASE) and re.search(r"\d", line):
            return _clean_address(line)
    return None


def _join_address_lines(candidates: Iterable[str]) -> Optional[str]:
    """Join one or two address lines while stopping before the next receipt label."""
    parts = []
    stop_labels = {label for labels in _LABELS.values() for label in labels}
    for candidate in candidates:
        cleaned = _clean_address(candidate)
        if not cleaned:
            continue
        if cleaned.lower().replace(".", "") in stop_labels:
            break
        if _split_label_value(cleaned)[0]:
            break
        parts.append(cleaned)
        if len(parts) >= 2:
            break
    return _clean_address(" ".join(parts)) if parts else None


def _clean_address(value: str) -> str:
    """Normalize a utility receipt address without changing its wording."""
    value = clean_text(value).upper()
    value = re.sub(r"\s*,\s*", ", ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ,")


def _normalize_provider_fields(data: Dict[str, Any], text: str) -> None:
    """Normalize provider names to a known Nigerian DISCO code when possible."""
    provider = data.get("provider") or _find_provider(text)
    if provider:
        provider = clean_text(provider)
        data["provider"] = provider
    provider_code = _provider_code(provider or text)
    if provider_code:
        data["provider_code"] = provider_code


def _find_provider(text: str) -> Optional[str]:
    """Find a provider name from text when no direct provider label was parsed."""
    for provider in _DISCO_ALIASES:
        if provider in (text or "").upper():
            return provider.title()
    return None


def _provider_code(value: str) -> Optional[str]:
    """Map OCR provider text to a known distribution-company code."""
    if not value:
        return None
    upper = re.sub(r"[^A-Z ]", " ", value.upper())
    upper = clean_text(upper)
    for alias, code in _DISCO_ALIASES.items():
        if alias in upper:
            return code
    compact = upper.replace(" ", "")
    best_code = None
    best_score = 0.0
    for alias, code in _DISCO_ALIASES.items():
        score = SequenceMatcher(a=compact, b=alias.replace(" ", "")).ratio()
        if score > best_score:
            best_score = score
            best_code = code
    return best_code if best_score >= 0.72 else None


def _normalize_amount_and_units(data: Dict[str, Any]) -> None:
    """Clean amount, unit, token, and meter values."""
    if data.get("amount_paid"):
        amount = re.sub(r"[^0-9.]", "", str(data["amount_paid"]))
        data["amount_paid"] = amount or None
    if data.get("units_purchased_kwh"):
        units = re.sub(r"[^0-9.]", "", str(data["units_purchased_kwh"]))
        data["units_purchased_kwh"] = units or None
    if data.get("meter_number"):
        data["meter_number"] = re.sub(r"[^A-Z0-9]", "", str(data["meter_number"]).upper())
    if data.get("token"):
        data["token"] = re.sub(r"\s+", "-", str(data["token"]).replace("_", "-")).strip("-")


def _apply_date_age(data: Dict[str, Any], *, today: Optional[date]) -> None:
    """Calculate document age and freshness from the extracted document date."""
    iso = data.get("document_date")
    if not iso:
        data["is_recent"] = False
        return
    try:
        doc_date = datetime.strptime(str(iso), "%Y-%m-%d").date()
    except Exception:
        data["is_recent"] = False
        return

    now = today or date.today()
    days_old = (now - doc_date).days
    data["days_old"] = days_old
    data["months_old"] = _completed_months_between(doc_date, now)
    data["is_recent"] = 0 <= days_old <= RECENCY_WINDOW_DAYS


def _completed_months_between(start: date, end: date) -> int:
    """Return completed calendar months between two dates."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(months, 0)


def _find_best_date(text: str) -> Optional[str]:
    """Find a receipt date, preferring labelled transaction dates."""
    labelled = re.search(rf"(?:Transaction|Payment|Receipt|Vending)\s*Date\s*[:\-]?\s*({_DATE_VALUE})", text, re.IGNORECASE)
    if labelled:
        parsed = _parse_date_to_iso(labelled.group(1))
        if parsed:
            return parsed
    for line in (text or "").splitlines():
        upper = line.upper()
        if "DATE" in upper or any(month in upper for month in _MONTHS):
            parsed = _parse_date_to_iso(line)
            if parsed:
                return parsed
    for match in re.finditer(_DATE_VALUE, text, flags=re.IGNORECASE):
        parsed = _parse_date_to_iso(match.group(0))
        if parsed:
            return parsed
    return None


def _parse_date_to_iso(raw: str) -> Optional[str]:
    """Parse common receipt date formats into ISO date strings."""
    if not raw:
        return None
    value = clean_text(raw)
    value = re.sub(r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"(\d{1,2})(st|nd|rd|th)\b", r"\1", value, flags=re.IGNORECASE)
    value = re.sub(r"(\d)([A-Za-z])", r"\1 \2", value)
    value = re.sub(r"([A-Za-z])(\d)", r"\1 \2", value)
    value = re.sub(r"(\d{4})(\d{1,2}:\d{2})", r"\1 \2", value)
    value = clean_text(value.replace(",", " "))
    value = re.sub(r"\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?$", "", value, flags=re.IGNORECASE)

    patterns = (
        r"\b(\d{4})[./-](\d{1,2})[./-](\d{1,2})\b",
        r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b",
        r"\b([A-Za-z]{3,9})\s+(\d{1,2})\s+(\d{2,4})\b",
        r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{2,4})\b",
    )
    for idx, pattern in enumerate(patterns):
        match = re.search(pattern, value)
        if not match:
            continue
        if idx == 0:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        elif idx == 1:
            day, month, year = int(match.group(1)), int(match.group(2)), _normalize_year(int(match.group(3)))
        elif idx == 2:
            month = _MONTHS.get(match.group(1).upper())
            day, year = int(match.group(2)), _normalize_year(int(match.group(3)))
            if not month:
                continue
        else:
            day = int(match.group(1))
            month = _MONTHS.get(match.group(2).upper())
            year = _normalize_year(int(match.group(3)))
            if not month:
                continue
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            continue
    return None


def _normalize_year(year: int) -> int:
    """Expand two-digit receipt years."""
    if year >= 100:
        return year
    return 2000 + year if year <= 40 else 1900 + year


def _looks_like_electricity_receipt(text: str) -> bool:
    """Return True when the text looks like an electricity receipt or bill."""
    upper = (text or "").upper()
    return any(term in upper for term in ("ELECTRICITY", "METER", "TOKEN", "KWH", "PREPAID", "DISCO"))


def _detect_receipt_type(text: str) -> str:
    """Classify the upload as prepaid receipt, postpaid bill, or unknown."""
    upper = (text or "").upper()
    prepaid_hits = sum(term in upper for term in ("TOKEN", "PREPAID", "VENDING", "UNITS PURCHASED", "KWH"))
    postpaid_hits = sum(term in upper for term in ("BILL DATE", "DUE DATE", "CURRENT CHARGES", "ARREARS"))
    if prepaid_hits >= max(2, postpaid_hits + 1):
        return "PREPAID_RECEIPT"
    if postpaid_hits >= max(2, prepaid_hits + 1):
        return "POSTPAID_BILL"
    return "UNKNOWN"


def _score_confidence(data: Dict[str, Any]) -> str:
    """Return a simple confidence label from required and supporting fields."""
    score = 0
    if data.get("service_address"):
        score += 4
    if data.get("document_date"):
        score += 4
    if data.get("provider") or data.get("provider_code"):
        score += 2
    if data.get("meter_number"):
        score += 2
    if data.get("token") or data.get("transaction_reference"):
        score += 1
    if data.get("customer_name"):
        score += 1
    if score >= 10:
        return "HIGH"
    if score >= 7:
        return "MEDIUM"
    if score >= 4:
        return "LOW"
    return "REJECT"


def _canonical_data(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return all expected response keys in a stable order."""
    source = data or {}
    output: Dict[str, Any] = {}
    for key in CANONICAL_UTILITY_BILL_KEYS:
        value = source.get(key)
        if key == "reasons":
            output[key] = value if isinstance(value, list) else []
        elif isinstance(value, str):
            output[key] = value.strip() or None
        else:
            output[key] = value
    return output


def _first_match(text: str, pattern: str) -> Optional[str]:
    """Return the first regex capture from text."""
    match = re.search(pattern, text or "", flags=re.IGNORECASE)
    return clean_text(match.group(1)) if match else None


def _error(message: str, *, country_code: str = "NGA", raw_text: str = "") -> Dict[str, Any]:
    """Return a consistent utility bill extraction error payload."""
    return {
        "success": False,
        "message": message,
        "document_type": "UTILITY_BILL",
        "data": _canonical_data({"country_code": country_code, "reasons": []}),
        "raw_text": raw_text,
    }
