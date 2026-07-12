"""Generic labelled-field extraction and unknown-field preservation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from src.document_ocr.business_document.normalization import (
    normalize_business_text,
    normalize_company_name,
    normalize_company_status,
    normalize_entity_type,
    parse_date_to_iso,
)
from src.document_ocr.business_document.schema import FieldEvidence, ParsedBusinessDocument


@dataclass(frozen=True)
class LabelValue:
    """One label/value pair recovered from OCR text."""

    label: str
    value: str
    excerpt: str
    confidence: float


_KNOWN_LABELS: dict[str, tuple[str, ...]] = {
    "legal_company_name": (
        "LEGAL COMPANY NAME",
        "LEGAL ENTITY NAME",
        "NAME OF COMPANY",
        "COMPANY NAME",
        "ENTITY NAME",
        "REGISTERED NAME",
    ),
    "trading_name": (
        "TRADING NAME",
        "TRADE NAME",
        "DBA",
        "DOING BUSINESS AS",
    ),
    "principal_business_address": (
        "PRINCIPAL BUSINESS ADDRESS",
        "PRINCIPAL PLACE OF BUSINESS",
        "HEAD OFFICE ADDRESS",
        "BUSINESS ADDRESS",
    ),
    "issuing_authority": (
        "ISSUING AUTHORITY",
        "ISSUED BY",
        "REGISTRY AUTHORITY",
        "REGISTERING AUTHORITY",
        "REGISTRAR NAME",
    ),
    "document_reference_number": (
        "DOCUMENT REFERENCE NUMBER",
        "DOCUMENT REFERENCE",
        "REFERENCE NUMBER",
        "REFERENCE NO",
        "CERTIFICATE NUMBER",
        "CERTIFICATE NO",
    ),
    "country_of_incorporation": (
        "COUNTRY OF INCORPORATION",
        "COUNTRY OF FORMATION",
        "COUNTRY OF REGISTRATION",
        "REGISTERED COUNTRY",
    ),
    "jurisdiction_of_incorporation": (
        "JURISDICTION OF INCORPORATION",
        "STATE OF INCORPORATION",
        "FORMATION STATE",
        "REGISTERED IN",
    ),
    "document_issue_date": (
        "DOCUMENT ISSUE DATE",
        "ISSUE DATE",
        "DATE OF ISSUE",
        "CERTIFICATE DATE",
    ),
    "incorporation_date": (
        "DATE OF FORMATION",
        "FORMATION DATE",
        "DATE OF INCORPORATION",
        "INCORPORATION DATE",
    ),
    "company_status": (
        "COMPANY STATUS",
        "ENTITY STATUS",
        "REGISTRATION STATUS",
        "STATUS",
    ),
    "entity_type": (
        "COMPANY TYPE",
        "ENTITY TYPE",
        "TYPE OF COMPANY",
    ),
    "business_activities": (
        "BUSINESS ACTIVITIES",
        "NATURE OF BUSINESS",
        "PRINCIPAL ACTIVITY",
        "LINE OF BUSINESS",
    ),
}

_KNOWN_LABEL_LOOKUP = {
    re.sub(r"[^A-Z0-9]+", " ", alias).strip(): field for field, aliases in _KNOWN_LABELS.items() for alias in aliases
}

_EXCLUDED_LABEL_PREFIXES = (
    "ADDRESS OF REGISTERED",
    "COMPANY NAME",
    "ENTITY NAME",
    "NAME OF COMPANY",
    "REGISTERED NAME",
    "REGISTRATION NUMBER",
    "COMPANY NUMBER",
    "ENTITY NUMBER",
    "DATE OF INCORPORATION",
    "DATE OF REGISTRATION",
    "REGISTERED OFFICE",
    "COMPANY STATUS",
    "ENTITY STATUS",
    "TAX IDENTIFICATION",
    "EMPLOYER IDENTIFICATION",
    "ENTITY ID",
    "ENTITY NUMBER",
    "STATE FILING",
    "REGISTRY IDENTIFIER",
    "REGISTRY NUMBER",
    "DOCUMENT REFERENCE",
    "CERTIFICATE NUMBER",
    "DIRECTOR",
    "SHAREHOLDER",
    "BENEFICIAL OWNER",
    "SHARE CAPITAL",
    "PAID UP CAPITAL",
    "PAID-UP CAPITAL",
    "OBJECTS OF THE COMPANY",
    "OBJECTS",
    "NAME",
    "PERCENTAGE",
    "SHARES",
)

_AUTHORITY_PATTERNS = (
    r"\bCORPORATE\s+AFFAIRS\s+COMMISSION\b",
    r"\bOFFICE\s+OF\s+THE\s+REGISTRAR\s+OF\s+COMPANIES\b",
    r"\bREGISTRAR[ -]GENERAL(?:'S)?\s+DEPARTMENT\b",
    r"\bCOMPANIES\s+HOUSE\b",
    r"\b(?:[A-Z][A-Z ]{2,30}\s+)?SECRETARY\s+OF\s+STATE\b",
    r"\bAUSTRALIAN\s+SECURITIES\s+AND\s+INVESTMENTS\s+COMMISSION\b",
    r"\bCOMPANIES\s+AND\s+INTELLECTUAL\s+PROPERTY\s+COMMISSION\b",
)


def parse_generic_business_fields(
    text: str,
    *,
    country_code: Optional[str] = None,
    page_texts: Optional[Sequence[str]] = None,
) -> ParsedBusinessDocument:
    """Extract portable fields and retain unknown labelled values."""
    normalized = normalize_business_text(text)
    pairs = extract_label_values(normalized)
    data: dict[str, object] = {}
    evidence: list[FieldEvidence] = []
    warnings: list[str] = []

    for pair in pairs:
        field = _canonical_field_for_label(pair.label)
        if not field or field in data:
            continue
        value = _normalize_known_value(field, pair.value, country_code=country_code)
        if value in (None, "", []):
            continue
        data[field] = value
        evidence.append(_field_evidence(field, value, pair, page_texts))

    if not data.get("issuing_authority"):
        authority = _first_pattern(normalized, _AUTHORITY_PATTERNS)
        if not authority:
            authority = _infer_authority_from_lines(normalized.splitlines())
        if authority:
            pair = LabelValue("Issuing authority", authority, authority, 0.88)
            data["issuing_authority"] = authority
            evidence.append(_field_evidence("issuing_authority", authority, pair, page_texts))

    additional_fields = []
    for pair in pairs:
        if _canonical_field_for_label(pair.label) or _is_core_label(pair.label):
            continue
        item = {
            "label": pair.label,
            "value": pair.value,
            "confidence": round(pair.confidence, 3),
            "evidence": {
                "method": "generic_label_value",
                "page": _page_for_excerpt(pair.excerpt, page_texts),
                "text": pair.excerpt[:240],
            },
        }
        additional_fields.append(item)
        evidence.append(
            FieldEvidence(
                field="additional_fields",
                value={"label": pair.label, "value": pair.value},
                method="generic_label_value",
                confidence=pair.confidence,
                page=_page_for_excerpt(pair.excerpt, page_texts),
                text=pair.excerpt,
            )
        )
        if len(additional_fields) >= 50:
            warnings.append("additional_fields_truncated")
            break
    data["additional_fields"] = additional_fields
    return ParsedBusinessDocument(data=data, evidence=evidence, warnings=warnings)


def extract_label_values(text: str) -> list[LabelValue]:
    """Collect conservative inline or adjacent label/value pairs."""
    lines = normalize_business_text(text).splitlines()
    output: list[LabelValue] = []
    seen: set[tuple[str, str]] = set()
    for index, line in enumerate(lines):
        inline = re.match(
            r"^(?P<label>[A-Za-z][A-Za-z0-9 &'()./-]{1,60}?)\s*(?:[:#]|\s[-–—]\s)\s*(?P<value>.+)$",
            line,
        )
        if inline:
            _append_pair(
                output,
                seen,
                label=inline.group("label"),
                value=inline.group("value"),
                excerpt=line,
                confidence=0.76,
            )
            continue

        normalized_label = _normalize_label(line)
        if not _looks_like_label(line, normalized_label) or index + 1 >= len(lines):
            continue
        next_line = lines[index + 1]
        if _looks_like_label(next_line, _normalize_label(next_line)) or _looks_like_section_heading(next_line):
            continue
        _append_pair(
            output,
            seen,
            label=line,
            value=next_line,
            excerpt=f"{line} | {next_line}",
            confidence=0.66,
        )
    return output


def _append_pair(
    output: list[LabelValue],
    seen: set[tuple[str, str]],
    *,
    label: str,
    value: str,
    excerpt: str,
    confidence: float,
) -> None:
    clean_label = " ".join(label.split()).strip(" :#.-")
    clean_value = " ".join(value.split()).strip(" :#.-")
    if not _valid_pair(clean_label, clean_value):
        return
    key = (clean_label.casefold(), clean_value.casefold())
    if key in seen:
        return
    seen.add(key)
    output.append(LabelValue(clean_label, clean_value[:1000], excerpt[:1200], confidence))


def _valid_pair(label: str, value: str) -> bool:
    if not label or not value or len(label) > 60 or len(value) > 1000:
        return False
    if len(re.sub(r"[^A-Za-z]", "", label)) < 2:
        return False
    if label.casefold() == value.casefold():
        return False
    return bool(re.search(r"[A-Za-z0-9]", value))


def _looks_like_label(line: str, normalized_label: str) -> bool:
    if normalized_label in _KNOWN_LABEL_LOOKUP:
        return True
    if normalized_label in {"OF", "THE", "AND", "NAME", "STATUS", "PERCENTAGE"}:
        return False
    if re.search(r"\d", line):
        return False
    if re.search(r"\b(?:ACTIVE|INACTIVE|CEASED|RESIGNED|DISSOLVED)\b$", line, flags=re.IGNORECASE):
        return False
    if re.search(
        r"\b(?:LIMITED|LTD|LLC|PLC|INCORPORATED|INC|CORPORATION|CORP|REGISTRY|REGISTRAR|"
        r"COMMISSION|SECRETARY\s+OF\s+STATE|CERTIFICATE|ARTICLES|MEMORANDUM)\b",
        line,
        flags=re.IGNORECASE,
    ):
        return False
    if re.match(
        r"^(?:FEDERAL\s+REPUBLIC|REPUBLIC\s+OF|STATE\s+OF|DIVISION\s+OF|THIS\s+IS\s+TO\s+CERTIFY|"
        r"OBJECTS?\s+OF|SHAREHOLDERS?|DIRECTORS?|BENEFICIAL\s+OWNERS?)\b",
        line,
        flags=re.IGNORECASE,
    ):
        return False
    words = re.findall(r"[A-Za-z0-9]+", line)
    if not 1 <= len(words) <= 7 or len(line) > 60:
        return False
    letters = re.sub(r"[^A-Za-z]", "", line)
    return bool(letters) and (line == line.upper() or line.istitle()) and not re.search(r"[.;]$", line)


def _looks_like_section_heading(line: str) -> bool:
    return bool(
        re.match(
            r"^(?:OBJECTS?(?:\s+OF\s+THE\s+COMPANY)?|SHARE\s+CAPITAL|DIRECTORS?|SHAREHOLDERS?|"
            r"BENEFICIAL\s+OWNERS?|SUBSCRIBERS?|MEMORANDUM|ARTICLES)\s*[:.-]?$",
            line,
            flags=re.IGNORECASE,
        )
    )


def _canonical_field_for_label(label: str) -> Optional[str]:
    normalized = _normalize_label(label)
    direct = _KNOWN_LABEL_LOOKUP.get(normalized)
    if direct:
        return direct
    for alias, field in _KNOWN_LABEL_LOOKUP.items():
        if normalized.startswith(alias):
            return field
    return None


def _normalize_known_value(field: str, value: str, *, country_code: Optional[str]) -> object:
    if field in {"legal_company_name", "trading_name"}:
        return normalize_company_name(value)
    if field in {"document_issue_date", "incorporation_date"}:
        return parse_date_to_iso(value, country_code=country_code)
    if field == "company_status":
        return normalize_company_status(value)
    if field == "entity_type":
        return normalize_entity_type(value)
    if field == "business_activities":
        return [" ".join(value.split())]
    return " ".join(value.split()).strip(" :;,.-") or None


def _is_core_label(label: str) -> bool:
    normalized = _normalize_label(label)
    return normalized.startswith(_EXCLUDED_LABEL_PREFIXES) or normalized.endswith(" SHARES")


def _normalize_label(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()


def _first_pattern(text: str, patterns: Sequence[str]) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return " ".join(match.group(0).split()).strip()
    return None


def _infer_authority_from_lines(lines: Sequence[str]) -> Optional[str]:
    for line in lines[:20]:
        upper = line.upper()
        if any(term in upper for term in ("IDENTIFIER", "NUMBER", "REFERENCE")):
            continue
        if (
            re.search(
                r"\b(?:REGISTRY|REGISTRAR(?:\s+OF\s+COMPANIES)?|SECRETARY\s+OF\s+STATE|COMMISSION)\b$",
                upper,
            )
            and 4 <= len(line) <= 100
        ):
            return " ".join(line.split())
    return None


def _field_evidence(
    field: str,
    value: object,
    pair: LabelValue,
    page_texts: Optional[Sequence[str]],
) -> FieldEvidence:
    return FieldEvidence(
        field=field,
        value=value,
        method="generic_label_value",
        confidence=pair.confidence,
        page=_page_for_excerpt(pair.excerpt, page_texts),
        text=pair.excerpt,
    )


def _page_for_excerpt(excerpt: str, page_texts: Optional[Sequence[str]]) -> Optional[int]:
    if not excerpt or not page_texts:
        return None
    fragments = [re.sub(r"\s+", " ", item).strip().casefold() for item in excerpt.split("|") if len(item.strip()) >= 3]
    for page_number, page_text in enumerate(page_texts, start=1):
        haystack = re.sub(r"\s+", " ", page_text or "").casefold()
        if any(fragment in haystack for fragment in fragments):
            return page_number
    return None
