"""Structured section extraction for MEMART and company status reports."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Sequence

from src.document_ocr.business_document.normalization import (
    normalize_business_text,
    unique_clean_strings,
)
from src.document_ocr.business_document.schema import FieldEvidence, ParsedBusinessDocument


_ROLE_HEADINGS = {
    "DIRECTOR": (r"DIRECTORS?", r"PARTICULARS\s+OF\s+DIRECTORS?"),
    "SHAREHOLDER": (r"SHAREHOLDERS?", r"SHAREHOLDING", r"RETURN\s+OF\s+ALLOTMENT"),
    "BENEFICIAL_OWNER": (r"BENEFICIAL\s+OWNERS?",),
    "PERSON_WITH_SIGNIFICANT_CONTROL": (
        r"PERSONS?\s+WITH\s+SIGNIFICANT\s+CONTROL",
        r"SIGNIFICANT\s+CONTROLLERS?",
        r"\bPSC\b",
    ),
    "SECRETARY": (r"COMPANY\s+SECRETAR(?:Y|IES)", r"SECRETAR(?:Y|IES)"),
    "SUBSCRIBER": (r"SUBSCRIBERS?", r"SUBSCRIBERS?\s+TO\s+THE\s+MEMORANDUM"),
    "PROPRIETOR": (r"PROPRIETORS?", r"BUSINESS\s+OWNERS?"),
    "PARTNER": (r"PARTNERS?",),
    "TRUSTEE": (r"TRUSTEES?",),
}

_ALL_ROLE_HEADING_PATTERN = re.compile(
    r"^(?:" + "|".join(pattern for patterns in _ROLE_HEADINGS.values() for pattern in patterns) + r")\s*[:.-]?$",
    re.IGNORECASE,
)

_SECTION_STOP_RE = re.compile(
    r"^(?:COMPANY|GENERAL|REGISTRATION|ADDRESS|SHARE\s+CAPITAL|OBJECTS?|NATURE\s+OF\s+BUSINESS|"
    r"ANNUAL\s+RETURNS?|CHARGES?|MORTGAGES?|ARTICLES?|MEMORANDUM|DECLARATION|CERTIFICATION|"
    r"GENERATED|DISCLAIMER|FILING\s+HISTORY)\b",
    re.IGNORECASE,
)

_NAME_STOP_WORDS = {
    "ADDRESS",
    "APPOINTED",
    "BUSINESS",
    "CEASED",
    "COMPANY",
    "DATE",
    "DESIGNATION",
    "DIRECTOR",
    "DIRECTORS",
    "EMAIL",
    "GENDER",
    "IDENTIFICATION",
    "NAME",
    "NATIONALITY",
    "NATURE",
    "NUMBER",
    "OCCUPATION",
    "PARTICULARS",
    "PHONE",
    "ROLE",
    "SECRETARY",
    "SHAREHOLDER",
    "SHAREHOLDERS",
    "SHARES",
    "STATUS",
    "SUBSCRIBER",
    "TRUSTEE",
    "TYPE",
}

_CURRENCY_CODES = {
    "N": "NGN",
    "NGN": "NGN",
    "NAIRA": "NGN",
    "₦": "NGN",
    "GHS": "GHS",
    "GH₵": "GHS",
    "KES": "KES",
    "KSH": "KES",
    "USD": "USD",
    "$": "USD",
    "GBP": "GBP",
    "£": "GBP",
    "EUR": "EUR",
    "€": "EUR",
    "ZAR": "ZAR",
    "CAD": "CAD",
    "AUD": "AUD",
}


def parse_business_sections(
    text: str,
    *,
    document_type: str,
    page_texts: Optional[Sequence[str]] = None,
) -> ParsedBusinessDocument:
    """Extract objects, capital, and role-bearing parties from long documents."""
    normalized = normalize_business_text(text)
    data: dict[str, Any] = {}
    evidence: list[FieldEvidence] = []
    warnings: list[str] = []

    objects, objects_excerpt = extract_business_objects(normalized)
    if objects:
        data["business_objects"] = objects
        evidence.append(
            FieldEvidence(
                field="business_objects",
                value=objects,
                method="objects_section",
                confidence=0.88,
                page=_page_for_excerpt(objects_excerpt, page_texts),
                text=objects_excerpt,
            )
        )

    share_capital, capital_excerpt = extract_share_capital(normalized)
    if share_capital:
        data["share_capital"] = share_capital
        evidence.append(
            FieldEvidence(
                field="share_capital",
                value=share_capital,
                method="share_capital_clause",
                confidence=0.90,
                page=_page_for_excerpt(capital_excerpt, page_texts),
                text=capital_excerpt,
            )
        )

    parties, party_excerpts = extract_parties(normalized)
    if parties:
        data["parties"] = parties
        for role, excerpt in party_excerpts.items():
            role_parties = [party for party in parties if role in party.get("roles", [])]
            evidence.append(
                FieldEvidence(
                    field="parties",
                    value=[party["name"] for party in role_parties],
                    method=f"{role.lower()}_section",
                    confidence=0.80,
                    page=_page_for_excerpt(excerpt, page_texts),
                    text=excerpt,
                )
            )

    if document_type == "MEMORANDUM_AND_ARTICLES_OF_ASSOCIATION" and not objects:
        warnings.append("business_objects_not_found")
    if document_type == "COMPANY_STATUS_REPORT" and not parties:
        warnings.append("company_parties_not_found")
    return ParsedBusinessDocument(data=data, evidence=evidence, warnings=warnings)


def extract_business_objects(text: str) -> tuple[list[str], str]:
    """Extract bounded company-object clauses from a memorandum or report."""
    lines = normalize_business_text(text).splitlines()
    start = None
    inline_value = ""
    heading_pattern = re.compile(
        r"^(?:THE\s+)?OBJECTS?(?:\s+FOR\s+WHICH\s+THE\s+COMPANY\s+IS\s+ESTABLISHED|\s+OF\s+THE\s+COMPANY)?\s*(?:ARE|IS)?\s*[:.-]?\s*(.*)$",
        re.IGNORECASE,
    )
    for index, line in enumerate(lines):
        match = heading_pattern.match(line)
        if match:
            start = index
            inline_value = match.group(1).strip()
            break
    if start is None:
        return [], ""

    block_lines = [inline_value] if inline_value else []
    for line in lines[start + 1 : start + 80]:
        if re.match(
            r"^(?:LIABILITY\s+OF|THE\s+LIABILITY|SHARE\s+CAPITAL|CAPITAL\s+OF|ARTICLES\s+OF|SUBSCRIBERS?|WE\s+THE\s+SEVERAL)",
            line,
            flags=re.IGNORECASE,
        ):
            break
        block_lines.append(line)

    clauses: list[str] = []
    current = ""
    for line in block_lines:
        cleaned = re.sub(r"^\s*(?:\(?\d+[.)]|\(?[A-Z][.)]|\(?[IVX]+[.)])\s*", "", line, flags=re.IGNORECASE).strip()
        numbered = cleaned != line.strip()
        if numbered and current:
            clauses.append(current)
            current = cleaned
        else:
            current = f"{current} {cleaned}".strip()
        if len(current) >= 1200:
            clauses.append(current[:1200].rstrip())
            current = ""
    if current:
        clauses.append(current)

    if len(clauses) == 1 and len(clauses[0]) > 500:
        split_clauses = [item.strip() for item in re.split(r";\s+(?=(?:TO|THE\s+COMPANY)\b)", clauses[0], flags=re.IGNORECASE)]
        if len(split_clauses) > 1:
            clauses = split_clauses

    objects = unique_clean_strings(
        clause[:1200]
        for clause in clauses
        if len(re.sub(r"[^A-Z]", "", clause.upper())) >= 8
    )[:30]
    excerpt = " | ".join(block_lines[:8])[:1200]
    return objects, excerpt


def extract_share_capital(text: str) -> tuple[dict[str, Any], str]:
    """Extract stated capital, issued shares, class, and nominal share value."""
    normalized = normalize_business_text(text)
    currency = r"(?P<currency>NGN|NAIRA|N(?=\s?\d)|₦|GHS|GH₵|KES|KSH|USD|\$|GBP|£|EUR|€|ZAR|CAD|AUD)?"
    amount = r"(?P<amount>\d{1,3}(?:[ ,]\d{3})+(?:\.\d{1,2})?|\d{4,}(?:\.\d{1,2})?)"
    patterns = (
        rf"\b(?:ISSUED\s+)?SHARE\s+CAPITAL(?:\s+OF\s+THE\s+COMPANY)?\s*(?:IS|OF|:|-)?\s*{currency}\s*{amount}",
        rf"\b(?:AUTHORIZED|AUTHORISED|NOMINAL|PAID[- ]UP)\s+CAPITAL\s*(?:IS|OF|:|-)?\s*{currency}\s*{amount}",
        rf"\bCAPITAL\s*(?:IS|OF|:|-)?\s*{currency}\s*{amount}\s+DIVIDED\s+INTO\b",
    )
    match = next((found for pattern in patterns if (found := re.search(pattern, normalized, re.IGNORECASE))), None)
    if not match:
        return {}, ""

    raw_currency = (match.groupdict().get("currency") or "").upper()
    raw_amount = match.group("amount")
    result: dict[str, Any] = {
        "currency": _CURRENCY_CODES.get(raw_currency) if raw_currency else None,
        "amount": _normalize_decimal(raw_amount),
        "amount_text": " ".join(match.group(0).split()),
    }
    nearby = normalized[match.start() : min(len(normalized), match.end() + 400)]

    shares_match = re.search(
        r"\bDIVIDED\s+INTO\s+(?P<count>\d{1,3}(?:[ ,]\d{3})+|\d{2,})\s+(?:(?P<class>[A-Z][A-Z -]{1,30})\s+)?SHARES?",
        nearby,
        re.IGNORECASE,
    )
    if shares_match:
        result["issued_share_count"] = re.sub(r"[ ,]", "", shares_match.group("count"))
        share_class = " ".join((shares_match.groupdict().get("class") or "").split()).upper()
        if share_class and share_class not in {"OF", "THE", "ISSUED"}:
            result["share_class"] = share_class

    nominal_match = re.search(
        rf"\bSHARES?\s+OF\s+{currency}\s*(?P<nominal>\d+(?:\.\d{{1,2}})?)\s+EACH\b",
        nearby,
        re.IGNORECASE,
    )
    if nominal_match:
        nominal_currency = (nominal_match.groupdict().get("currency") or "").upper()
        if not result.get("currency") and nominal_currency:
            result["currency"] = _CURRENCY_CODES.get(nominal_currency)
        result["nominal_value_per_share"] = _normalize_decimal(nominal_match.group("nominal"))

    paid_up_match = re.search(rf"\bPAID[- ]UP\s+(?:SHARE\s+)?CAPITAL\s*(?:IS|OF|:|-)?\s*{currency}\s*{amount}", normalized, re.IGNORECASE)
    if paid_up_match:
        result["paid_up_amount"] = _normalize_decimal(paid_up_match.group("amount"))

    return {key: value for key, value in result.items() if value not in (None, "")}, nearby[:800]


def extract_parties(text: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Extract public role/name records without parsing personal ID numbers."""
    lines = normalize_business_text(text).splitlines()
    parties: list[dict[str, Any]] = []
    excerpts: dict[str, str] = {}

    direct_pattern = re.compile(
        r"^(?P<role>DIRECTOR|SHAREHOLDER|BENEFICIAL\s+OWNER|PERSON\s+WITH\s+SIGNIFICANT\s+CONTROL|PSC|"
        r"COMPANY\s+SECRETARY|SECRETARY|SUBSCRIBER|PROPRIETOR|PARTNER|TRUSTEE)\s*[:.-]\s*(?P<name>.+)$",
        re.IGNORECASE,
    )
    for line in lines:
        match = direct_pattern.match(line)
        if not match:
            continue
        role = _normalize_role(match.group("role"))
        party = _party_from_line(match.group("name"), role)
        if party:
            parties.append(party)
            excerpts.setdefault(role, line)

    for role, heading_patterns in _ROLE_HEADINGS.items():
        block, excerpt = _find_role_block(lines, heading_patterns)
        if not block:
            continue
        excerpts.setdefault(role, excerpt)
        for index, line in enumerate(block):
            name_match = re.match(r"^(?:FULL\s+)?NAME\s*[:.-]\s*(.+)$", line, re.IGNORECASE)
            candidate_line = name_match.group(1) if name_match else line
            party = _party_from_line(candidate_line, role)
            if not party:
                continue
            if index + 1 < len(block):
                _apply_party_metadata(party, block[index + 1])
            parties.append(party)

    return _merge_parties(parties), excerpts


def _find_role_block(lines: Sequence[str], heading_patterns: Sequence[str]) -> tuple[list[str], str]:
    heading_re = re.compile(r"^(?:" + "|".join(heading_patterns) + r")\s*[:.-]?$", re.IGNORECASE)
    for index, line in enumerate(lines):
        if not heading_re.match(line):
            continue
        block = []
        for candidate in lines[index + 1 : index + 45]:
            if _ALL_ROLE_HEADING_PATTERN.match(candidate) or (_SECTION_STOP_RE.match(candidate) and block):
                break
            block.append(candidate)
        return block, " | ".join([line] + block[:10])[:1200]
    return [], ""


def _party_from_line(line: str, role: str) -> Optional[dict[str, Any]]:
    candidate = str(line or "").strip()
    candidate = re.sub(r"^\s*(?:\d+[.)-]?|[A-Z][.)])\s*", "", candidate)
    candidate = re.sub(r"^(?:FULL\s+)?NAME\s*[:.-]\s*", "", candidate, flags=re.IGNORECASE)
    candidate = re.split(
        r"\s{2,}|\s+\b(?:ACTIVE|INACTIVE|CEASED|APPOINTED|NATIONALITY|ADDRESS|EMAIL|PHONE|SHARES?|PERCENTAGE)\b",
        candidate,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    candidate = candidate.strip(" |:;,.-")
    if not _looks_like_party_name(candidate):
        return None
    party: dict[str, Any] = {"name": candidate, "roles": [role]}
    _apply_party_metadata(party, line)
    return party


def _apply_party_metadata(party: dict[str, Any], line: str) -> None:
    upper = str(line or "").upper()
    status_match = re.search(r"\b(ACTIVE|INACTIVE|CEASED|RESIGNED)\b", upper)
    if status_match:
        party["status"] = status_match.group(1)
    percentage_match = re.search(r"\b(\d{1,3}(?:\.\d+)?)\s*%", line)
    if percentage_match and float(percentage_match.group(1)) <= 100:
        party["share_percentage"] = percentage_match.group(1)
    shares_match = re.search(r"\b(?:SHARES?|ALLOTMENT)\s*[:.-]?\s*(\d{1,3}(?:[ ,]\d{3})+|\d+)\b", line, re.IGNORECASE)
    if shares_match:
        party["shares"] = re.sub(r"[ ,]", "", shares_match.group(1))


def _merge_parties(parties: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order = []
    for party in parties:
        key = re.sub(r"[^A-Z0-9]", "", str(party.get("name") or "").upper())
        if not key:
            continue
        if key not in merged:
            merged[key] = dict(party)
            order.append(key)
            continue
        existing = merged[key]
        existing["roles"] = sorted(set(existing.get("roles", [])) | set(party.get("roles", [])))
        for field in ("status", "shares", "share_percentage"):
            if not existing.get(field) and party.get(field):
                existing[field] = party[field]
    return [merged[key] for key in order][:100]


def _looks_like_party_name(value: str) -> bool:
    candidate = " ".join(str(value or "").split())
    upper = candidate.upper()
    if len(candidate) < 4 or len(candidate) > 140 or "@" in candidate:
        return False
    if re.search(r"\b(?:NIN|BVN|PASSPORT|IDENTIFICATION|DATE\s+OF\s+BIRTH)\b", upper):
        return False
    if _ALL_ROLE_HEADING_PATTERN.match(candidate) or _SECTION_STOP_RE.match(candidate):
        return False
    words = re.findall(r"[A-Z][A-Z'&.-]*", upper)
    meaningful = [word for word in words if word.strip(".&-") and word not in _NAME_STOP_WORDS]
    has_legal_suffix = bool(re.search(r"\b(?:LIMITED|LTD|LLC|INCORPORATED|INC|PLC|CORPORATION|CORP)\b", upper))
    if has_legal_suffix:
        return len(meaningful) >= 2
    return len(meaningful) >= 2 and not re.search(r"\d", candidate)


def _normalize_role(value: str) -> str:
    upper = re.sub(r"\s+", " ", value.upper()).strip()
    mapping = {
        "BENEFICIAL OWNER": "BENEFICIAL_OWNER",
        "PERSON WITH SIGNIFICANT CONTROL": "PERSON_WITH_SIGNIFICANT_CONTROL",
        "PSC": "PERSON_WITH_SIGNIFICANT_CONTROL",
        "COMPANY SECRETARY": "SECRETARY",
    }
    return mapping.get(upper, upper)


def _normalize_decimal(value: str) -> Optional[str]:
    cleaned = re.sub(r"[ ,]", "", str(value or ""))
    try:
        number = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    return format(number, "f")


def _page_for_excerpt(excerpt: str, page_texts: Optional[Sequence[str]]) -> Optional[int]:
    if not excerpt or not page_texts:
        return None
    fragments = [
        re.sub(r"\s+", " ", fragment).strip().casefold()
        for fragment in excerpt.split("|")
        if len(fragment.strip()) >= 4
    ]
    for page_number, page_text in enumerate(page_texts, start=1):
        haystack = re.sub(r"\s+", " ", page_text or "").casefold()
        if any(fragment in haystack for fragment in fragments):
            return page_number
    return None
