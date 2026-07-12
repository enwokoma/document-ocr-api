"""OCR-tolerant normalization helpers for company documents."""

from __future__ import annotations

import re
from datetime import date
from typing import Iterable, Optional

_MONTHS = {
    "JAN": 1,
    "JANUARY": 1,
    "FEB": 2,
    "FEBRUARY": 2,
    "MAR": 3,
    "MARCH": 3,
    "APR": 4,
    "APRIL": 4,
    "MAY": 5,
    "JUN": 6,
    "JUNE": 6,
    "JUL": 7,
    "JULY": 7,
    "AUG": 8,
    "AUGUST": 8,
    "SEP": 9,
    "SEPT": 9,
    "SEPTEMBER": 9,
    "OCT": 10,
    "OCTOBER": 10,
    "NOV": 11,
    "NOVEMBER": 11,
    "DEC": 12,
    "DECEMBER": 12,
}

_JOINED_LABELS = {
    "COMPANYNAME": "COMPANY NAME",
    "ENTITYNAME": "ENTITY NAME",
    "BUSINESSNAME": "BUSINESS NAME",
    "REGISTRATIONNUMBER": "REGISTRATION NUMBER",
    "REGISTRATIONNO": "REGISTRATION NO",
    "REGISTEREDNUMBER": "REGISTERED NUMBER",
    "COMPANYNUMBER": "COMPANY NUMBER",
    "DATEOFINCORPORATION": "DATE OF INCORPORATION",
    "DATEOFREGISTRATION": "DATE OF REGISTRATION",
    "REGISTRATIONDATE": "REGISTRATION DATE",
    "REGISTEREDADDRESS": "REGISTERED ADDRESS",
    "REGISTEREDOFFICE": "REGISTERED OFFICE",
    "HEADOFFICEADDRESS": "HEAD OFFICE ADDRESS",
    "HEAD_OFFICEADDRESS": "HEAD OFFICE ADDRESS",
    "COMPANYSTATUS": "COMPANY STATUS",
    "ENTITYSTATUS": "ENTITY STATUS",
    "SHARECAPITAL": "SHARE CAPITAL",
    "NATUREOFBUSINESS": "NATURE OF BUSINESS",
    "PRINCIPALACTIVITY": "PRINCIPAL ACTIVITY",
    "TAXIDENTIFICATIONNUMBER": "TAX IDENTIFICATION NUMBER",
}


def normalize_business_text(text: str, *, preserve_columns: bool = False) -> str:
    """Normalize OCR text while preserving page and line breaks.

    ``preserve_columns`` retains repeated spaces and pipe separators used by
    registry tables.  The default remains the compact representation expected
    by label and prose extractors.
    """
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\u2013", "-").replace("\u2014", "-").replace("\u00a0", " ")
    lines = []
    for raw_line in value.split("\n"):
        if preserve_columns:
            line = re.sub(r"\t+", "    ", raw_line)
            line = re.sub(r"\s*\|+\s*", " | ", line)
            line = re.sub(r"[\f\v]+", " ", line).strip()
        else:
            line = re.sub(r"[|]+", " ", raw_line)
            line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if not line:
            continue
        compact_upper = re.sub(r"[^A-Z_]", "", line.upper())
        for joined, replacement in _JOINED_LABELS.items():
            if compact_upper.startswith(joined):
                consumed = _joined_prefix_length(line, joined)
                if not _is_joined_label_prefix(line, consumed):
                    continue
                line = replacement + " " + line[consumed:].lstrip(" :-._")
                break
        lines.append(line.strip())
    return "\n".join(lines)


def normalize_company_name(value: str) -> Optional[str]:
    """Clean a legal entity name without changing its legal casing or words."""
    if not value:
        return None
    cleaned = " ".join(str(value).replace("\n", " ").split())
    cleaned = re.sub(
        r"^(?:NAME\s+OF\s+(?:THE\s+)?COMPANY|LEGAL\s+(?:COMPANY|ENTITY)\s+NAME|COMPANY\s+NAME|ENTITY\s+NAME|BUSINESS\s+NAME)\s*[:#-]?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\s+(?:IS|WAS|HAS\s+BEEN)\s+(?:HEREBY\s+)?(?:INCORPORATED|REGISTERED)\b.*$", "", cleaned, flags=re.IGNORECASE
    )
    cleaned = cleaned.strip(" :;,.-_\"'“”")
    return cleaned[:180] or None


def normalize_identifier(value: str) -> Optional[str]:
    """Normalize a registry identifier while retaining meaningful separators."""
    if not value:
        return None
    cleaned = str(value).upper().strip(" :;,.#")
    cleaned = re.sub(r"\s*([/-])\s*", r"\1", cleaned)
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = re.sub(r"[^A-Z0-9/-]", "", cleaned)
    return cleaned[:64] or None


def parse_date_to_iso(raw: str, *, country_code: Optional[str] = None) -> Optional[str]:
    """Parse common registry date formats into ISO-8601 calendar dates."""
    if not raw:
        return None
    value = str(raw).upper().replace(",", " ")
    value = re.sub(r"\b(?:MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY)\b", " ", value)
    value = re.sub(r"\b(\d{1,2})(?:ST|ND|RD|TH)\b", r"\1", value)
    value = re.sub(r"\b(?:THIS|THE|DAY\s+OF|DAY|DATED|DATE)\b", " ", value)
    value = re.sub(r"(\d)([A-Z])", r"\1 \2", value)
    value = re.sub(r"([A-Z])(\d)", r"\1 \2", value)
    value = re.sub(r"\s+", " ", value).strip()

    match = re.search(r"\b(19\d{2}|20\d{2})[./-](\d{1,2})[./-](\d{1,2})\b", value)
    if match:
        return _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    month_names = "|".join(sorted(_MONTHS, key=len, reverse=True))
    match = re.search(rf"\b(\d{{1,2}})\s+({month_names})\s+(\d{{2,4}})\b", value)
    if match:
        return _safe_date(_normalize_year(int(match.group(3))), _MONTHS[match.group(2)], int(match.group(1)))

    match = re.search(rf"\b({month_names})\s+(\d{{1,2}})\s+(\d{{2,4}})\b", value)
    if match:
        return _safe_date(_normalize_year(int(match.group(3))), _MONTHS[match.group(1)], int(match.group(2)))

    match = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b", value)
    if match:
        first, second, year = int(match.group(1)), int(match.group(2)), _normalize_year(int(match.group(3)))
        if first > 12:
            day, month = first, second
        elif second > 12:
            month, day = first, second
        elif (country_code or "").upper() == "USA":
            month, day = first, second
        else:
            day, month = first, second
        return _safe_date(year, month, day)
    return None


def normalize_entity_type(value: str) -> Optional[str]:
    """Map printed company-type wording to a stable entity code."""
    upper = re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()
    if not upper:
        return None
    rules = (
        ("COMPANY_LIMITED_BY_GUARANTEE", r"\b(?:COMPANY\s+)?LIMITED\s+BY\s+GUARANTEE\b|\bLTD\s*/?\s*GTE\b"),
        (
            "PUBLIC_COMPANY_LIMITED_BY_SHARES",
            r"\bPUBLIC\s+(?:LIMITED\s+)?COMPANY\b|\bPUBLIC\s+COMPANY\s+LIMITED\s+BY\s+SHARES\b|\bPLC\b",
        ),
        (
            "PRIVATE_COMPANY_LIMITED_BY_SHARES",
            r"\bPRIVATE\s+(?:LIMITED\s+)?COMPANY\b|\bPRIVATE\s+COMPANY\s+LIMITED\s+BY\s+SHARES\b",
        ),
        ("LIMITED_LIABILITY_COMPANY", r"\bLIMITED\s+LIABILITY\s+COMPANY\b|\bLLC\b"),
        ("LIMITED_LIABILITY_PARTNERSHIP", r"\bLIMITED\s+LIABILITY\s+PARTNERSHIP\b|\bLLP\b"),
        ("UNLIMITED_COMPANY", r"\bUNLIMITED\s+COMPANY\b"),
        ("INCORPORATED_TRUSTEE", r"\bINCORPORATED\s+TRUSTEE"),
        ("BUSINESS_NAME", r"\bBUSINESS\s+NAME\b|\bSOLE\s+PROPRIETOR"),
        ("PARTNERSHIP", r"\bPARTNERSHIP\b"),
        ("COMPANY_LIMITED_BY_SHARES", r"\bLIMITED\s+BY\s+SHARES\b"),
    )
    for code, pattern in rules:
        if re.search(pattern, upper):
            return code
    if re.search(r"\b(?:LTD|LIMITED)\b", upper):
        return "LIMITED_COMPANY"
    if re.search(r"\b(?:INC|INCORPORATED|CORPORATION|CORP)\b", upper):
        return "CORPORATION"
    return None


def normalize_company_status(value: str) -> Optional[str]:
    """Map registry status wording to a small set of comparable values."""
    upper = re.sub(r"[^A-Z]+", " ", str(value or "").upper()).strip()
    rules = (
        ("IN_LIQUIDATION", r"\b(?:IN\s+)?LIQUIDATION\b|\bWINDING\s+UP\b"),
        ("DISSOLVED", r"\bDISSOLVED\b"),
        ("STRUCK_OFF", r"\bSTRUCK\s+OFF\b"),
        ("INACTIVE", r"\bINACTIVE\b|\bNOT\s+ACTIVE\b"),
        ("ACTIVE", r"\bACTIVE\b|\bLIVE\b|\bIN\s+GOOD\s+STANDING\b"),
        ("REGISTERED", r"\bREGISTERED\b"),
    )
    for status, pattern in rules:
        if re.search(pattern, upper):
            return status
    return None


def unique_clean_strings(values: Iterable[str], *, limit: int = 100) -> list[str]:
    """Return ordered, deduplicated non-empty strings."""
    output = []
    seen = set()
    for value in values:
        cleaned = " ".join(str(value or "").split()).strip(" :;,.-")
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        output.append(cleaned)
        seen.add(key)
        if len(output) >= limit:
            break
    return output


def _joined_prefix_length(line: str, joined: str) -> int:
    compact = ""
    for index, character in enumerate(line):
        if character.isalpha() or character == "_":
            compact += character.upper()
        if compact == joined:
            return index + 1
        if not joined.startswith(compact):
            break
    return 0


def _is_joined_label_prefix(line: str, consumed: int) -> bool:
    """Reject ordinary title-cased words such as ``CompanyName Holdings``."""
    if consumed <= 0:
        return False
    prefix = line[:consumed]
    following = line[consumed : consumed + 1]
    if following and following in ":#.-_":
        return True
    # OCR labels are normally uppercase. Requiring that signal keeps title-case
    # legal names beginning with CompanyName/BusinessName intact.
    return prefix.isupper()


def _normalize_year(year: int) -> int:
    if year >= 100:
        return year
    return 2000 + year if year <= 40 else 1900 + year


def _safe_date(year: int, month: int, day: int) -> Optional[str]:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None
