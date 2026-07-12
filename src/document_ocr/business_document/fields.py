"""Core company field extraction shared by all business documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from src.document_ocr.business_document.jurisdictions import (
    JurisdictionResult,
    get_business_jurisdiction,
)
from src.document_ocr.business_document.normalization import (
    normalize_business_text,
    normalize_company_name,
    normalize_company_status,
    normalize_entity_type,
    normalize_identifier,
    parse_date_to_iso,
)
from src.document_ocr.business_document.schema import FieldEvidence, ParsedBusinessDocument


@dataclass(frozen=True)
class _ValueMatch:
    value: str
    excerpt: str
    method: str
    confidence: float


_LEGAL_SUFFIX_RE = re.compile(
    r"\b(?:LIMITED|LTD\.?|PLC|LTD\s*/?\s*GTE|LLC|L\.L\.C\.?|INCORPORATED|INC\.?|CORPORATION|CORP\.?|LLP)\b",
    re.IGNORECASE,
)

_NON_NAME_MARKERS = (
    "CERTIFICATE OF",
    "STATUS REPORT",
    "MEMORANDUM",
    "ARTICLES OF ASSOCIATION",
    "CORPORATE AFFAIRS COMMISSION",
    "REGISTRAR OF COMPANIES",
    "REGISTRAR-GENERAL",
    "FEDERAL REPUBLIC",
    "REPUBLIC OF",
    "COMPANIES HOUSE",
    "BUSINESS REGISTRATION SERVICE",
)

_LEGAL_FORM_ONLY_RE = re.compile(
    r"^(?:(?:PRIVATE|PUBLIC)\s+)?COMPANY\s+LIMITED\s+BY\s+(?:SHARES|GUARANTEE)$|"
    r"^LIMITED\s+LIABILITY\s+(?:COMPANY|PARTNERSHIP)$|"
    r"^INCORPORATED\s+TRUSTEES?$",
    re.IGNORECASE,
)

_CERTIFICATE_NARRATIVE_RE = re.compile(
    r"^(?:IS|WAS|HAS\s+BEEN|HEREBY|THIS\s+DAY|THAT\s+DAY)\b.*\b(?:INCORPORATED|REGISTERED)\b",
    re.IGNORECASE,
)

_GENERIC_LABEL_RE = re.compile(
    r"^(?:COMPANY|ENTITY|BUSINESS|REGISTERED|HEAD\s+OFFICE|REGISTRATION|INCORPORATION|DOCUMENT|REPORT|"
    r"NATURE\s+OF\s+BUSINESS|PRINCIPAL\s+ACTIVITY|STATUS|DATE|EMAIL|PHONE|TELEPHONE|TIN|TAX|SHARE|"
    r"DIRECTORS?|SHAREHOLDERS?|SECRETARY|SUBSCRIBERS?|OBJECTS?)\b",
    re.IGNORECASE,
)


def parse_core_business_fields(
    text: str,
    *,
    jurisdiction: JurisdictionResult,
    document_type: str,
    page_texts: Optional[Sequence[str]] = None,
) -> ParsedBusinessDocument:
    """Extract canonical company identity, registry, date, and address fields."""
    normalized = normalize_business_text(text)
    lines = normalized.splitlines()
    data = {
        "country_code": jurisdiction.country_code,
        "jurisdiction": jurisdiction.country_name,
        "registry_name": jurisdiction.registry_name,
    }
    evidence: list[FieldEvidence] = []
    warnings: list[str] = []

    company_name = _extract_company_name(normalized, lines, document_type=document_type)
    _add_match(data, evidence, "company_name", company_name, page_texts)

    registration = _extract_registration_number(normalized, jurisdiction.country_code)
    if registration:
        number_match, number_type = registration
        _add_match(data, evidence, "registration_number", number_match, page_texts)
        data["registration_number_type"] = number_type
        evidence.append(
            _evidence(
                "registration_number_type",
                number_type,
                number_match,
                page_texts,
                confidence=max(0.75, number_match.confidence - 0.05),
            )
        )

    entity_type = _extract_entity_type(normalized, lines)
    _add_match(data, evidence, "entity_type", entity_type, page_texts)

    company_status = _extract_company_status(normalized, lines)
    _add_match(data, evidence, "company_status", company_status, page_texts)

    incorporation_date = _extract_date(
        normalized,
        lines,
        labels=(r"DATE\s+OF\s+INCORPORATION", r"INCORPORATION\s+DATE", r"DATE\s+INCORPORATED"),
        fallback_patterns=(
            r"\bINCORPORATED\s+ON\s+([^\n.;]{5,50})",
            r"\bINCORPORATED\s+THIS\s+([^\n.;]{5,60})",
        ),
        country_code=jurisdiction.country_code,
        method="incorporation_date_label",
    )
    _add_match(data, evidence, "incorporation_date", incorporation_date, page_texts)

    registration_date = _extract_date(
        normalized,
        lines,
        labels=(r"DATE\s+OF\s+REGISTRATION", r"REGISTRATION\s+DATE", r"DATE\s+REGISTERED"),
        fallback_patterns=(r"\bREGISTERED\s+ON\s+([^\n.;]{5,50})",),
        country_code=jurisdiction.country_code,
        method="registration_date_label",
    )
    _add_match(data, evidence, "registration_date", registration_date, page_texts)

    document_date = _extract_document_date(
        normalized,
        lines,
        country_code=jurisdiction.country_code,
        document_type=document_type,
    )
    _add_match(data, evidence, "document_date", document_date, page_texts)

    registered_address = _extract_address(
        lines,
        labels=(
            r"REGISTERED\s+OFFICE\s+ADDRESS",
            r"REGISTERED\s+ADDRESS",
            r"REGISTERED\s+OFFICE",
            r"ADDRESS\s+OF\s+(?:THE\s+)?REGISTERED\s+OFFICE",
        ),
        method="registered_address_label",
    )
    _add_match(data, evidence, "registered_address", registered_address, page_texts)

    head_office_address = _extract_address(
        lines,
        labels=(r"HEAD\s+OFFICE\s+ADDRESS", r"PRINCIPAL\s+PLACE\s+OF\s+BUSINESS", r"BUSINESS\s+ADDRESS"),
        method="head_office_address_label",
    )
    _add_match(data, evidence, "head_office_address", head_office_address, page_texts)

    governing_law = _extract_governing_law(normalized)
    _add_match(data, evidence, "governing_law", governing_law, page_texts)

    tax_id = _extract_labeled_value(
        lines,
        labels=(r"TAX\s+IDENTIFICATION\s+NUMBER", r"TAX\s+ID(?:ENTIFICATION)?\s+NUMBER", r"TIN"),
        validator=lambda value: bool(re.search(r"[A-Z0-9]{5,}", value, re.IGNORECASE)),
        method="tax_identifier_label",
    )
    if tax_id:
        tax_id = _ValueMatch(
            value=normalize_identifier(tax_id.value) or tax_id.value,
            excerpt=tax_id.excerpt,
            method=tax_id.method,
            confidence=tax_id.confidence,
        )
    _add_match(data, evidence, "tax_identification_number", tax_id, page_texts)

    email = _extract_labeled_value(
        lines,
        labels=(r"REGISTERED\s+EMAIL(?:\s+ADDRESS)?", r"COMPANY\s+EMAIL"),
        validator=lambda value: bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value.strip())),
        method="email_label",
    )
    if not email:
        email = _extract_labeled_value(
            _company_details_scope(lines),
            labels=(r"EMAIL(?:\s+ADDRESS)?",),
            validator=lambda value: bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value.strip())),
            method="email_label",
        )
    _add_match(data, evidence, "contact_email", email, page_texts)

    phone = _extract_labeled_value(
        lines,
        labels=(r"COMPANY\s+PHONE", r"REGISTERED\s+PHONE(?:\s+NUMBER)?"),
        validator=lambda value: len(re.sub(r"\D", "", value)) >= 7,
        method="phone_label",
    )
    if not phone:
        phone = _extract_labeled_value(
            _company_details_scope(lines),
            labels=(r"PHONE(?:\s+NUMBER)?", r"TELEPHONE", r"MOBILE"),
            validator=lambda value: len(re.sub(r"\D", "", value)) >= 7,
            method="phone_label",
        )
    _add_match(data, evidence, "contact_phone", phone, page_texts)

    nature = _extract_labeled_value(
        lines,
        labels=(r"NATURE\s+OF\s+BUSINESS", r"PRINCIPAL\s+(?:BUSINESS\s+)?ACTIVITY", r"LINE\s+OF\s+BUSINESS"),
        continuation_lines=2,
        validator=lambda value: len(re.sub(r"[^A-Z]", "", value.upper())) >= 4,
        method="business_activity_label",
    )
    _add_match(data, evidence, "nature_of_business", nature, page_texts)

    if not data.get("company_name"):
        warnings.append("company_name_not_found")
    if not data.get("registration_number"):
        warnings.append("registration_number_not_found")
    if jurisdiction.conflict:
        warnings.append("country_hint_conflicts_with_document")

    return ParsedBusinessDocument(data=data, evidence=evidence, warnings=warnings)


def _extract_company_name(text: str, lines: Sequence[str], *, document_type: str) -> Optional[_ValueMatch]:
    phrase_patterns = (
        r"(?:THIS\s+IS\s+TO|I\s+HEREBY|HEREBY)\s+CERTIF(?:Y|IES)\s+THAT\s+"
        r"([A-Z0-9][A-Z0-9 &'(),.\-/]{2,180}?)\s+(?:IS|WAS|HAS\s+BEEN)\s+"
        r"(?:(?:THIS|THAT)\s+DAY\s+)?(?:HEREBY\s+)?(?:INCORPORATED|REGISTERED)",
        r"\bCERTIFICATE\s+OF\s+(?:INCORPORATION|REGISTRATION)\s+(?:OF|FOR)\s+([A-Z0-9][A-Z0-9 &'(),.\-/]{2,180})",
        r"\bMEMORANDUM(?:\s+AND\s+ARTICLES)?\s+OF\s+ASSOCIATION\s+OF\s+([A-Z0-9][A-Z0-9 &'(),.\-/]{2,180})",
    )
    for pattern in phrase_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        candidate = normalize_company_name(match.group(1))
        if candidate and _looks_like_company_name(candidate):
            return _ValueMatch(candidate, match.group(0), "certificate_or_title_phrase", 0.94)

    labelled = _extract_labeled_value(
        lines,
        labels=(
            r"NAME\s+OF\s+(?:THE\s+)?COMPANY",
            r"REGISTERED\s+(?:COMPANY\s+)?NAME",
            r"LEGAL\s+(?:COMPANY|ENTITY)\s+NAME",
            r"COMPANY\s+NAME",
            r"ENTITY\s+NAME",
            r"BUSINESS\s+NAME",
        ),
        validator=_looks_like_company_name,
        method="company_name_label",
        confidence=0.98,
    )
    if labelled:
        cleaned = normalize_company_name(labelled.value)
        if cleaned:
            return _ValueMatch(cleaned, labelled.excerpt, labelled.method, labelled.confidence)

    candidates: list[tuple[int, str]] = []
    title_indexes = [
        idx
        for idx, line in enumerate(lines)
        if re.search(r"CERTIFICATE|STATUS\s+REPORT|MEMORANDUM|ARTICLES\s+OF\s+ASSOCIATION", line, re.IGNORECASE)
    ]
    for idx, line in enumerate(lines):
        candidate = normalize_company_name(line)
        if not candidate or not _looks_like_company_name(candidate) or not _LEGAL_SUFFIX_RE.search(candidate):
            continue
        score = 20
        if len(candidate) <= 100:
            score += 5
        if title_indexes:
            distance = min(abs(idx - title_idx) for title_idx in title_indexes)
            score += max(0, 12 - distance)
        if document_type == "COMPANY_STATUS_REPORT" and idx < 25:
            score += 3
        candidates.append((score, candidate))
    if candidates:
        _, candidate = max(candidates, key=lambda item: item[0])
        return _ValueMatch(candidate, candidate, "legal_name_line", 0.80)
    return None


def _extract_registration_number(text: str, country_code: Optional[str]) -> Optional[tuple[_ValueMatch, str]]:
    profile = get_business_jurisdiction(country_code)
    if profile:
        for registration_pattern in profile.registration_patterns:
            match = re.search(registration_pattern.pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            number = normalize_identifier(match.groupdict().get("number") or match.group(0))
            if not number:
                continue
            number_type = registration_pattern.number_type
            prefix = (match.groupdict().get("prefix") or "").upper()
            if "{prefix}" in number_type:
                number_type = number_type.format(prefix=prefix or "REGISTRATION")
            return (
                _ValueMatch(number, match.group(0), "jurisdiction_identifier_pattern", registration_pattern.confidence),
                number_type,
            )

    generic_patterns = (
        (
            r"\b(?P<label>COMPANY|REGISTRATION|REGISTERED|ENTITY|CORPORATION|FILE|DOCUMENT)\s+"
            r"(?P<kind>NO\.?|NUMBER|ID)\s*[:#-]?\s*(?P<number>[A-Z0-9][A-Z0-9/-]{3,30})\b",
            "REGISTRATION_NUMBER",
            0.85,
        ),
        (r"\b(?P<prefix>RC|BN|IT|LLP|LP)\s*[:#-]?\s*(?P<number>\d{4,12})\b", "REGISTRATION_NUMBER", 0.84),
    )
    for pattern, number_type, confidence in generic_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        prefix = (match.groupdict().get("prefix") or "").upper()
        number = normalize_identifier((prefix if prefix else "") + match.group("number"))
        if number:
            return _ValueMatch(number, match.group(0), "generic_identifier_pattern", confidence), number_type
    return None


def _extract_entity_type(text: str, lines: Sequence[str]) -> Optional[_ValueMatch]:
    labelled = _extract_labeled_value(
        lines,
        labels=(r"COMPANY\s+TYPE", r"ENTITY\s+TYPE", r"TYPE\s+OF\s+COMPANY", r"CLASS\s+OF\s+COMPANY"),
        validator=lambda value: normalize_entity_type(value) is not None,
        method="entity_type_label",
        confidence=0.94,
    )
    if labelled:
        normalized = normalize_entity_type(labelled.value)
        if normalized:
            return _ValueMatch(normalized, labelled.excerpt, labelled.method, labelled.confidence)

    phrases = (
        r"\bPRIVATE\s+COMPANY\s+LIMITED\s+BY\s+SHARES\b",
        r"\bPUBLIC\s+COMPANY\s+LIMITED\s+BY\s+SHARES\b",
        r"\bCOMPANY\s+LIMITED\s+BY\s+GUARANTEE\b",
        r"\bLIMITED\s+LIABILITY\s+(?:COMPANY|PARTNERSHIP)\b",
        r"\bUNLIMITED\s+COMPANY\b",
        r"\bINCORPORATED\s+TRUSTEES?\b",
        r"\bBUSINESS\s+NAME\b",
    )
    for pattern in phrases:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            normalized = normalize_entity_type(match.group(0))
            if normalized:
                return _ValueMatch(normalized, match.group(0), "entity_type_phrase", 0.86)

    name_line = next((line for line in lines if _looks_like_company_name(line) and _LEGAL_SUFFIX_RE.search(line)), None)
    normalized = normalize_entity_type(name_line or "")
    return _ValueMatch(normalized, name_line or "", "legal_suffix_inference", 0.62) if normalized else None


def _extract_company_status(text: str, lines: Sequence[str]) -> Optional[_ValueMatch]:
    labelled = _extract_labeled_value(
        lines,
        labels=(r"COMPANY\s+STATUS", r"ENTITY\s+STATUS", r"REGISTRATION\s+STATUS", r"CURRENT\s+STATUS", r"STATUS"),
        validator=lambda value: normalize_company_status(value) is not None,
        method="company_status_label",
        confidence=0.94,
    )
    if labelled:
        normalized = normalize_company_status(labelled.value)
        if normalized:
            return _ValueMatch(normalized, labelled.excerpt, labelled.method, labelled.confidence)
    match = re.search(
        r"\b(?:COMPANY|ENTITY|REGISTRATION)\s+(?:IS\s+)?(ACTIVE|INACTIVE|DISSOLVED|STRUCK\s+OFF|IN\s+LIQUIDATION)\b",
        text,
        re.IGNORECASE,
    )
    if match:
        normalized = normalize_company_status(match.group(1))
        if normalized:
            return _ValueMatch(normalized, match.group(0), "company_status_phrase", 0.82)
    return None


def _extract_date(
    text: str,
    lines: Sequence[str],
    *,
    labels: Sequence[str],
    fallback_patterns: Sequence[str],
    country_code: Optional[str],
    method: str,
) -> Optional[_ValueMatch]:
    labelled = _extract_labeled_value(
        lines,
        labels=labels,
        validator=lambda value: parse_date_to_iso(value, country_code=country_code) is not None,
        method=method,
        confidence=0.96,
    )
    if labelled:
        parsed = parse_date_to_iso(labelled.value, country_code=country_code)
        if parsed:
            return _ValueMatch(parsed, labelled.excerpt, labelled.method, labelled.confidence)
    for pattern in fallback_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        parsed = parse_date_to_iso(match.group(1), country_code=country_code)
        if parsed:
            return _ValueMatch(parsed, match.group(0), f"{method}_phrase", 0.84)
    return None


def _extract_document_date(
    text: str,
    lines: Sequence[str],
    *,
    country_code: Optional[str],
    document_type: str,
) -> Optional[_ValueMatch]:
    labelled = _extract_date(
        text,
        lines,
        labels=(
            r"DATE\s+OF\s+REPORT",
            r"REPORT\s+DATE",
            r"DOCUMENT\s+DATE",
            r"GENERATED\s+(?:ON|DATE)",
            r"PRINTED\s+(?:ON|DATE)",
            r"AS\s+AT",
        ),
        fallback_patterns=(),
        country_code=country_code,
        method="document_date_label",
    )
    if labelled:
        return labelled
    if document_type.startswith("CERTIFICATE"):
        patterns = (
            r"\bGIVEN\s+UNDER\s+MY\s+HAND[^\n.]{0,100}?\bTHIS\s+([^\n.]{5,55})",
            r"\bDATED\s+(?:AT\s+[A-Z ]+\s+)?(?:THIS\s+)?([^\n.]{5,55})",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                parsed = parse_date_to_iso(match.group(1), country_code=country_code)
                if parsed:
                    return _ValueMatch(parsed, match.group(0), "certificate_issue_phrase", 0.82)
    return None


def _extract_address(lines: Sequence[str], *, labels: Sequence[str], method: str) -> Optional[_ValueMatch]:
    labelled = _extract_labeled_value(
        lines,
        labels=labels,
        continuation_lines=3,
        validator=_looks_like_address,
        method=method,
        confidence=0.92,
    )
    if not labelled:
        return None
    cleaned = _clean_address(labelled.value)
    return _ValueMatch(cleaned, labelled.excerpt, labelled.method, labelled.confidence) if cleaned else None


def _extract_governing_law(text: str) -> Optional[_ValueMatch]:
    patterns = (
        r"\bCOMPANIES\s+AND\s+ALLIED\s+MATTERS\s+ACT(?:\s*,?\s*20\d{2})?\b",
        r"\bCOMPANIES\s+ACT(?:\s*,?\s*(?:NO\.?\s*)?\d{1,4}(?:\s+OF)?\s*20\d{2}|\s+20\d{2})\b",
        r"\bCANADA\s+BUSINESS\s+CORPORATIONS\s+ACT\b",
        r"\bCORPORATIONS\s+ACT\s+2001\b",
        r"\b(?:GENERAL\s+)?CORPORATION\s+LAW\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _ValueMatch(" ".join(match.group(0).split()), match.group(0), "governing_law_pattern", 0.90)
    return None


def _extract_labeled_value(
    lines: Sequence[str],
    *,
    labels: Sequence[str],
    validator: Callable[[str], bool],
    method: str,
    continuation_lines: int = 0,
    confidence: float = 0.90,
) -> Optional[_ValueMatch]:
    label_pattern = "|".join(f"(?:{label})" for label in labels)
    for index, line in enumerate(lines):
        match = re.match(rf"^\s*(?:{label_pattern})\s*(?:[:#.-]\s*|\s+)?(.*)$", line, flags=re.IGNORECASE)
        if not match:
            continue
        inline = match.group(1).strip(" :#.-")
        parts = [inline] if inline else []
        excerpt_lines = [line]
        next_index = index + 1
        if not parts and next_index < len(lines) and not _looks_like_new_label(lines[next_index]):
            parts.append(lines[next_index])
            excerpt_lines.append(lines[next_index])
            next_index += 1
        for continuation_index in range(next_index, min(next_index + continuation_lines, len(lines))):
            candidate = lines[continuation_index]
            if _looks_like_new_label(candidate) or _looks_like_heading(candidate):
                break
            parts.append(candidate)
            excerpt_lines.append(candidate)
        value = " ".join(parts).strip(" :#.-")
        if value and validator(value):
            return _ValueMatch(value, " | ".join(excerpt_lines), method, confidence)
    return None


def _company_details_scope(lines: Sequence[str]) -> Sequence[str]:
    """Exclude officer/owner sections when resolving unqualified contacts."""
    for index, line in enumerate(lines):
        if re.match(
            r"^(?:PARTICULARS\s+OF\s+)?(?:DIRECTORS?|SHAREHOLDERS?|SUBSCRIBERS?|"
            r"BENEFICIAL\s+OWNERS?|PERSONS?\s+WITH\s+SIGNIFICANT\s+CONTROL|PSC\s+DETAILS|SECRETARY)\b",
            line.strip(),
            flags=re.IGNORECASE,
        ):
            return lines[:index]
    return lines


def _add_match(
    data: dict,
    evidence: list[FieldEvidence],
    field: str,
    match: Optional[_ValueMatch],
    page_texts: Optional[Sequence[str]],
) -> None:
    if not match or match.value in (None, ""):
        return
    data[field] = match.value
    evidence.append(_evidence(field, match.value, match, page_texts))


def _evidence(
    field: str,
    value: object,
    match: _ValueMatch,
    page_texts: Optional[Sequence[str]],
    *,
    confidence: Optional[float] = None,
) -> FieldEvidence:
    return FieldEvidence(
        field=field,
        value=value,
        method=match.method,
        confidence=match.confidence if confidence is None else confidence,
        page=_page_for_excerpt(match.excerpt, page_texts),
        text=match.excerpt,
    )


def _page_for_excerpt(excerpt: str, page_texts: Optional[Sequence[str]]) -> Optional[int]:
    if not excerpt or not page_texts:
        return None
    needle = re.sub(r"\s+", " ", excerpt).strip().casefold()
    fragments = [fragment for fragment in re.split(r"\s*\|\s*", needle) if len(fragment) >= 4]
    for page_number, page_text in enumerate(page_texts, start=1):
        haystack = re.sub(r"\s+", " ", page_text or "").casefold()
        if needle in haystack or any(fragment in haystack for fragment in fragments):
            return page_number
    return None


def _looks_like_company_name(value: str) -> bool:
    cleaned = normalize_company_name(value) or ""
    upper = cleaned.upper()
    if len(cleaned) < 3 or len(cleaned) > 180 or len(re.findall(r"[A-Z]", upper)) < 3:
        return False
    if any(marker in upper for marker in _NON_NAME_MARKERS):
        return False
    if _LEGAL_FORM_ONLY_RE.fullmatch(cleaned) or _CERTIFICATE_NARRATIVE_RE.search(cleaned):
        return False
    if re.fullmatch(r"[\d /.-]+", cleaned):
        return False
    return True


def _looks_like_address(value: str) -> bool:
    upper = str(value or "").upper()
    if len(upper) < 8:
        return False
    return bool(
        re.search(r"\d", upper)
        or re.search(
            r"\b(?:STREET|ROAD|AVENUE|CLOSE|DRIVE|LANE|PLOT|SUITE|FLOOR|BUILDING|DISTRICT|STATE|CITY|LGA|POSTAL|OFFICE)\b",
            upper,
        )
    )


def _clean_address(value: str) -> Optional[str]:
    cleaned = " ".join(str(value or "").replace("|", " ").split())
    cleaned = re.sub(r"\s*,\s*", ", ", cleaned)
    cleaned = re.sub(r"(,\s*){2,}", ", ", cleaned)
    return cleaned.strip(" :;,.#-")[:500] or None


def _looks_like_new_label(line: str) -> bool:
    return bool(_GENERIC_LABEL_RE.search(str(line or "").strip()))


def _looks_like_heading(line: str) -> bool:
    cleaned = str(line or "").strip()
    return bool(cleaned and len(cleaned) <= 70 and cleaned.isupper() and not re.search(r"\d", cleaned))
