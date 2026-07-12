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
    "FIRST",
    "FORENAME",
    "GENDER",
    "IDENTIFICATION",
    "NAME",
    "NATIONALITY",
    "NATURE",
    "NUMBER",
    "OCCUPATION",
    "OTHER",
    "PARTICULARS",
    "PHONE",
    "ROLE",
    "SECRETARY",
    "SHAREHOLDER",
    "SHAREHOLDERS",
    "SHARES",
    "STATUS",
    "SUBSCRIBER",
    "SURNAME",
    "TRUSTEE",
    "TYPE",
}

_NON_PARTY_PHRASES = {
    "UNITED KINGDOM",
    "UNITED STATES",
    "UNITED STATES OF AMERICA",
    "FEDERAL REPUBLIC OF NIGERIA",
    "REPUBLIC OF GHANA",
    "SOUTH AFRICA",
    "NIGERIA",
    "GHANA",
    "CANADA",
    "AUSTRALIA",
}

_ADDRESS_WORD_RE = re.compile(
    r"\b(?:STREET|ROAD|AVENUE|CLOSE|DRIVE|LANE|BOULEVARD|HIGHWAY|PLOT|SUITE|FLOOR|"
    r"BUILDING|ESTATE|DISTRICT|POSTAL|POSTCODE|ZIP|LGA)\b",
    re.IGNORECASE,
)

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

_COUNTRY_DEFAULT_CURRENCIES = {
    "AUS": "AUD",
    "CAN": "CAD",
    "GHA": "GHS",
    "GBR": "GBP",
    "KEN": "KES",
    "NGA": "NGN",
    "USA": "USD",
    "ZAF": "ZAR",
}


def parse_business_sections(
    text: str,
    *,
    document_type: str,
    page_texts: Optional[Sequence[str]] = None,
    country_code: Optional[str] = None,
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

    share_capital, capital_excerpt = extract_share_capital(normalized, country_code=country_code)
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
        r"^(?:\(?(?:\d+|[IVX]+)[.)]\s*)?(?:"
        r"(?:THE\s+)?OBJECTS?\s+(?:FOR\s+WHICH\s+THE\s+COMPANY\s+IS\s+ESTABLISHED|OF\s+THE\s+COMPANY)"
        r"\s*(?:ARE|IS)?\s*[:.-]?\s*(.*)"
        r"|(?:THE\s+)?OBJECTS?\s+(?:ARE|IS)\s*[:.-]?\s*(.*)"
        r"|(?:THE\s+)?OBJECTS?\s*[:.-]\s*(.*)"
        r"|(?:THE\s+)?OBJECTS?\s*"
        r")$",
        re.IGNORECASE,
    )
    for index, line in enumerate(lines):
        match = heading_pattern.match(line)
        if match:
            start = index
            inline_value = next((group.strip() for group in match.groups() if group and group.strip()), "")
            break
    if start is None:
        return [], ""

    block_lines = [inline_value] if inline_value else []
    for line in lines[start + 1 : start + 80]:
        if re.match(
            r"^(?:(?:\d+|[IVX]+)[.)]\s*)?(?:LIABILITY\s+OF|THE\s+LIABILITY|LIABILITY\b|"
            r"(?:THE\s+)?(?:NOMINAL\s+)?SHARE\s+CAPITAL|CAPITAL\s+OF|ARTICLES\s+OF|"
            r"SUBSCRIBERS?|WE\s+THE\s+SEVERAL|REGISTERED\s+OFFICE|"
            r"DIRECTORS?|SHAREHOLDERS?|CERTIFICATION|DECLARATION|SIGNATURES?|IN\s+WITNESS)",
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

    objects = unique_clean_strings(clause[:1200] for clause in clauses if len(re.sub(r"[^A-Z]", "", clause.upper())) >= 8)[:30]
    excerpt = " | ".join(block_lines[:8])[:1200]
    return objects, excerpt


def extract_share_capital(text: str, *, country_code: Optional[str] = None) -> tuple[dict[str, Any], str]:
    """Extract distinct authorized, issued, paid-up, and stated capital values."""
    normalized = normalize_business_text(text)
    currency = (
        r"(?P<currency>NGN|NAIRA|N(?=\s?\d)|₦|GHS|GH₵|KES|KSH|USD|US\$|CAD|CA?\$|"
        r"AUD|AU?\$|\$|GBP|£|EUR|€|ZAR)?"
    )
    amount = r"(?P<amount>\d+(?:[ ,]\d{3})*(?:\.\d{1,4})?)(?![\d,])"
    definitions = (
        (
            "issued_amount",
            rf"\bISSUED\s+(?:SHARE\s+)?CAPITAL(?:\s+OF\s+THE\s+COMPANY)?\s*(?:IS|OF|:|-)?\s*{currency}\s*{amount}",
        ),
        (
            "authorized_amount",
            rf"\b(?:AUTHORIZED|AUTHORISED|NOMINAL)\s+(?:SHARE\s+)?CAPITAL"
            rf"(?:\s+OF\s+THE\s+COMPANY)?\s*(?:IS|OF|:|-)?\s*{currency}\s*{amount}",
        ),
        (
            "paid_up_amount",
            rf"\bPAID[- ]UP\s+(?:SHARE\s+)?CAPITAL\s*(?:IS|OF|:|-)?\s*{currency}\s*{amount}",
        ),
        (
            "stated_amount",
            rf"(?<!ISSUED\s)(?<!AUTHORIZED\s)(?<!AUTHORISED\s)(?<!NOMINAL\s)"
            rf"(?<!PAID-UP\s)(?<!PAID UP\s)\bSHARE\s+CAPITAL"
            rf"(?:\s+OF\s+THE\s+COMPANY)?\s*(?:IS|OF|:|-)?\s*{currency}\s*{amount}",
        ),
        (
            "stated_amount",
            rf"(?<!SHARE\s)(?<!ISSUED\s)(?<!AUTHORIZED\s)(?<!AUTHORISED\s)(?<!NOMINAL\s)"
            rf"(?<!PAID-UP\s)(?<!PAID UP\s)\bCAPITAL\s*(?:IS|OF|:|-)?\s*{currency}\s*{amount}"
            rf"\s+DIVIDED\s+INTO\b",
        ),
    )

    matches: list[tuple[str, re.Match[str]]] = []
    for field, pattern in definitions:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            matches.append((field, match))
    if not matches:
        return {}, ""

    result: dict[str, Any] = {}
    currency_values: list[str] = []
    raw_currency_values: list[str] = []
    for field, match in matches:
        result.setdefault(field, _normalize_decimal(match.group("amount")))
        raw_currency = (match.groupdict().get("currency") or "").upper()
        if raw_currency:
            raw_currency_values.append(raw_currency)
            resolved = _resolve_currency(raw_currency, country_code=country_code)
            if resolved:
                currency_values.append(resolved)

    primary_amount = next(
        (result.get(field) for field in ("issued_amount", "authorized_amount", "stated_amount") if result.get(field)),
        None,
    )
    if primary_amount:
        result["amount"] = primary_amount
    unique_currencies = list(dict.fromkeys(currency_values))
    if len(unique_currencies) == 1:
        result["currency"] = unique_currencies[0]
    elif len(unique_currencies) > 1:
        result["currency_candidates"] = unique_currencies
    if raw_currency_values and not result.get("currency"):
        result["currency_raw"] = raw_currency_values[0]
    if not result.get("currency") and not result.get("currency_raw") and country_code:
        default_currency = _COUNTRY_DEFAULT_CURRENCIES.get(country_code.upper())
        if default_currency:
            result["currency"] = default_currency

    start = min(match.start() for _, match in matches)
    end = max(match.end() for _, match in matches)
    nearby = normalized[start : min(len(normalized), end + 800)]
    result["amount_text"] = " | ".join(dict.fromkeys(" ".join(match.group(0).split()) for _, match in matches))

    share_classes = _extract_share_classes(nearby, country_code=country_code)
    if share_classes:
        result["share_classes"] = share_classes
        first_class = share_classes[0]
        total_count = sum(int(item["share_count"]) for item in share_classes)
        count_field = _division_count_field(nearby)
        result[count_field] = str(total_count)
        if first_class.get("share_class"):
            result["share_class"] = first_class["share_class"]
        if first_class.get("nominal_value_per_share"):
            result["nominal_value_per_share"] = first_class["nominal_value_per_share"]
        if not result.get("currency"):
            class_currencies = list(dict.fromkeys(item["currency"] for item in share_classes if item.get("currency")))
            if len(class_currencies) == 1:
                result["currency"] = class_currencies[0]

    return {key: value for key, value in result.items() if value not in (None, "")}, nearby[:1200]


def extract_parties(text: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Extract public role/name records without parsing personal ID numbers."""
    lines = normalize_business_text(text, preserve_columns=True).splitlines()
    parties: list[dict[str, Any]] = []
    excerpts: dict[str, str] = {}

    cac_parties, cac_excerpts = _extract_cac_role_records(lines)
    parties.extend(cac_parties)
    excerpts.update(cac_excerpts)

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
        for block, excerpt in _find_role_blocks(lines, heading_patterns):
            if not _looks_like_party_list(block, role):
                continue
            excerpts[role] = " | ".join(filter(None, (excerpts.get(role), excerpt)))[:1200]
            current_party: Optional[dict[str, Any]] = None
            for line in block:
                if _looks_like_role_prose(line, role):
                    break
                if _is_party_metadata_line(line):
                    if current_party is not None:
                        _apply_party_metadata(current_party, line)
                    continue
                party = _party_from_line(line, role)
                if not party:
                    continue
                parties.append(party)
                current_party = party

    return _merge_parties(parties), excerpts


_CAC_ROLE_RECORD_RE = re.compile(
    r"^\s*(?:(?:\d+(?:\.[A-Z]|[A-Z]|[.)])?|[A-Z]\d+)\s+)?"
    r"ROLE\s*TYPE\s*[:.-]?\s*(?P<role>[A-Z ]+)\s*$",
    re.IGNORECASE,
)

_CAC_NAME_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("full_name", (r"FULL\s+NAME", r"NAME")),
    ("company_name", (r"COMPANY\s+NAME", r"CORPORATE\s+NAME")),
    ("surname", (r"SURNAME", r"LAST\s+NAME")),
    ("first_name", (r"FIRST\s+NAME", r"FORENAME")),
    ("other_name", (r"OTHER\s+NAMES?", r"MIDDLE\s+NAMES?")),
)


def _extract_cac_role_records(lines: Sequence[str]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Extract bounded CAC status-report records headed by ``ROLE TYPE``."""
    starts = [(index, match) for index, line in enumerate(lines) if (match := _CAC_ROLE_RECORD_RE.match(line))]
    parties: list[dict[str, Any]] = []
    excerpts: dict[str, str] = {}
    for position, (start, match) in enumerate(starts):
        role = _normalize_cac_role(match.group("role"))
        if role is None:
            continue
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        record = list(lines[start + 1 : min(end, start + 61)])
        party = _party_from_cac_record(record, role)
        if not party:
            continue
        parties.append(party)
        excerpt_lines = [lines[start]] + record[:12]
        excerpts[role] = " | ".join(filter(None, (excerpts.get(role), " | ".join(excerpt_lines))))[:1200]
    return parties, excerpts


def _normalize_cac_role(value: str) -> Optional[str]:
    compact = re.sub(r"[^A-Z]", "", str(value or "").upper())
    mapping = {
        "DIRECTOR": "DIRECTOR",
        "SECRETARY": "SECRETARY",
        "COMPANYSECRETARY": "SECRETARY",
        "SECRETARYCOMPANY": "SECRETARY",
        "SHAREHOLDER": "SHAREHOLDER",
        "SUBSCRIBER": "SUBSCRIBER",
        "BENEFICIALOWNER": "BENEFICIAL_OWNER",
        "PSC": "PERSON_WITH_SIGNIFICANT_CONTROL",
        "PERSONWITHSIGNIFICANTCONTROL": "PERSON_WITH_SIGNIFICANT_CONTROL",
    }
    return mapping.get(compact)


def _party_from_cac_record(lines: Sequence[str], role: str) -> Optional[dict[str, Any]]:
    name_parts: dict[str, str] = {}
    for field, labels in _CAC_NAME_LABELS:
        value = _record_label_value(lines, labels)
        if value:
            name_parts[field] = value

    name = name_parts.get("company_name") or name_parts.get("full_name")
    if not name:
        # Preserve the registry's labelled surname/given/other-name order rather
        # than imposing a jurisdiction-specific display-name convention.
        name = " ".join(filter(None, (name_parts.get("surname"), name_parts.get("first_name"), name_parts.get("other_name"))))
    if not name or not _looks_like_party_name(name):
        return None

    party: dict[str, Any] = {"name": name, "roles": [role]}
    for line in lines:
        _apply_party_metadata(party, line)
    return party


def _record_label_value(lines: Sequence[str], labels: Sequence[str]) -> Optional[str]:
    label_pattern = "|".join(f"(?:{label})" for label in labels)
    for index, line in enumerate(lines):
        match = re.match(rf"^\s*(?:{label_pattern})(?:\s*[:#.-]\s*|\s+)(.*)$", line, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip(" |:;,.-")
        if not value and index + 1 < len(lines):
            value = lines[index + 1].strip(" |:;,.-")
        value = " ".join(value.split())[:140]
        if not value or value.upper() in {"NIL", "NONE", "N/A", "NA", "NOT APPLICABLE"}:
            continue
        if "@" in value or re.search(r"\d", value):
            continue
        return value
    return None


def _looks_like_party_list(block: Sequence[str], role: str) -> bool:
    if any(_CAC_ROLE_RECORD_RE.match(line) for line in block[:12]):
        return False
    for line in block[:12]:
        if _is_party_metadata_line(line) or _looks_like_party_table_header(line):
            continue
        if _looks_like_role_prose(line, role):
            return False
        if _party_from_line(line, role):
            return True
    return False


def _looks_like_party_table_header(line: str) -> bool:
    upper = re.sub(r"[^A-Z]+", " ", str(line or "").upper()).strip()
    words = set(upper.split())
    header_words = {
        "ADDRESS",
        "FULL",
        "NAME",
        "NAMES",
        "NUMBER",
        "ROLE",
        "SHAREHOLDER",
        "SHARES",
        "SIGNATURE",
        "STATUS",
        "SUBSCRIBER",
        "SUBSCRIBERS",
        "TYPE",
    }
    return bool(words) and words.issubset(header_words)


def _looks_like_role_prose(line: str, role: str) -> bool:
    candidate = " ".join(str(line or "").split())
    upper = candidate.upper()
    if not candidate:
        return False
    if re.match(r"^(?:[\u2022*]|\(?\d+[.)])\s*", candidate):
        return True
    if re.match(r"^(?:ALTERNATE\s+)?DIRECTORS?\b", upper) and role == "DIRECTOR":
        return True
    if len(candidate.split()) > 12 or candidate.endswith((".", ";")):
        return True
    return bool(
        re.search(
            r"\b(?:SUBJECT\s+TO|GENERAL\s+AUTHORITY|TAKE\s+DECISIONS?|DECISION-MAKING|MEETINGS?|"
            r"QUORUM|DELEGAT(?:E|ION)|APPOINTING|APPOINTMENT|REMUNERATION|EXPENSES?|"
            r"CORPORATE\s+AFFAIRS\s+COMMISSION|IS\s+RESPONSIBLE|ARE\s+RESPONSIBLE|"
            r"MAY\s+APPOINT|MUST\s+ENSURE)\b",
            upper,
        )
    )


def _find_role_blocks(lines: Sequence[str], heading_patterns: Sequence[str]) -> list[tuple[list[str], str]]:
    heading_re = re.compile(r"^(?:" + "|".join(heading_patterns) + r")\s*[:.-]?$", re.IGNORECASE)
    results: list[tuple[list[str], str]] = []
    for index, line in enumerate(lines):
        if not heading_re.match(line):
            continue
        block = []
        for candidate in lines[index + 1 : index + 101]:
            if _ALL_ROLE_HEADING_PATTERN.match(candidate) or _SECTION_STOP_RE.match(candidate):
                break
            block.append(candidate)
        if block:
            results.append((block, " | ".join([line] + block[:10])[:1200]))
    return results


def _find_role_block(lines: Sequence[str], heading_patterns: Sequence[str]) -> tuple[list[str], str]:
    """Return the first block for compatibility with older internal callers."""
    blocks = _find_role_blocks(lines, heading_patterns)
    return blocks[0] if blocks else ([], "")


def _party_from_line(line: str, role: str) -> Optional[dict[str, Any]]:
    candidate = str(line or "").strip()
    candidate = re.sub(r"^\s*(?:\d+[.)-]?|[A-Z][.)])\s*", "", candidate)
    candidate = re.sub(r"^(?:FULL\s+)?NAME\s*[:.-]\s*", "", candidate, flags=re.IGNORECASE)
    columns = _split_party_columns(candidate)
    candidate = columns[0] if columns else candidate
    candidate = re.split(
        r"\s+\b(?:ACTIVE|INACTIVE|CEASED|RESIGNED|APPOINTED|NATIONALITY|ADDRESS|EMAIL|PHONE|"
        r"SHARES?|PERCENTAGE|DIRECTOR|SHAREHOLDER|SECRETARY|SUBSCRIBER|PROPRIETOR|PARTNER|TRUSTEE)\b",
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


def _split_party_columns(line: str) -> list[str]:
    return [part.strip(" |:;,.-") for part in re.split(r"\s*\|\s*|\t+|\s{2,}", line) if part.strip(" |:;,.-")]


def _is_party_metadata_line(line: str) -> bool:
    return bool(
        re.match(
            r"^\s*(?:STATUS|SHARES?|ALLOTMENT|SHARE\s*PERCENTAGE|PERCENTAGE|SHAREHOLDING|"
            r"NATIONALITY|ADDRESS|EMAIL|PHONE|TELEPHONE|APPOINTED|CEASED)\s*[:.-]",
            str(line or ""),
            flags=re.IGNORECASE,
        )
    )


def _apply_party_metadata(party: dict[str, Any], line: str) -> None:
    upper = str(line or "").upper()
    status_match = re.search(r"\b(ACTIVE|INACTIVE|CEASED|RESIGNED)\b", upper)
    if status_match:
        party["status"] = status_match.group(1)
    percentage_match = re.search(r"\b(\d{1,3}(?:\.\d+)?)\s*%", line)
    if percentage_match and float(percentage_match.group(1)) <= 100:
        party["share_percentage"] = percentage_match.group(1)
    shares_match = re.search(
        r"(?:\b(?:SHARES?|ALLOTMENT)\s*[:.-]?\s*(\d{1,3}(?:[ ,]\d{3})+|\d+)\b|"
        r"\b(\d{1,3}(?:[ ,]\d{3})+|\d+)\s+SHARES?\b)",
        line,
        re.IGNORECASE,
    )
    if shares_match:
        party["shares"] = re.sub(r"[ ,]", "", shares_match.group(1) or shares_match.group(2))


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
    if upper in _NON_PARTY_PHRASES or _ADDRESS_WORD_RE.search(upper):
        return False
    if re.match(r"^(?:NATIONALITY|ADDRESS|STATUS|EMAIL|PHONE|APPOINTED|CEASED)\b", upper):
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


def _resolve_currency(raw_currency: str, *, country_code: Optional[str]) -> Optional[str]:
    token = str(raw_currency or "").upper().replace(" ", "")
    country = str(country_code or "").upper()
    if token in {"US$"}:
        return "USD"
    if token in {"C$", "CA$"}:
        return "CAD"
    if token in {"A$", "AU$"}:
        return "AUD"
    if token == "$":
        return {"USA": "USD", "CAN": "CAD", "AUS": "AUD"}.get(country)
    return _CURRENCY_CODES.get(token)


def _extract_share_classes(text: str, *, country_code: Optional[str]) -> list[dict[str, str]]:
    division = re.search(r"\bDIVIDED\s+INTO\b", text, flags=re.IGNORECASE)
    if not division:
        return []
    clause = text[division.end() : division.end() + 700]
    currency = r"NGN|NAIRA|N(?=\s?\d)|₦|GHS|GH₵|KES|KSH|USD|US\$|CAD|CA?\$|AUD|AU?\$|\$|GBP|£|EUR|€|ZAR"
    entry_re = re.compile(
        rf"(?P<count>\d+(?:[ ,]\d{{3}})*)(?![\d,])\s+"
        rf"(?:(?P<class>[A-Z][A-Z &/-]{{0,40}}?)\s+)?SHARES?\b"
        rf"(?:\s+OF\s+(?P<currency>{currency})?\s*(?P<nominal>\d+(?:\.\d{{1,4}})?)\s+EACH)?",
        flags=re.IGNORECASE,
    )
    output: list[dict[str, str]] = []
    seen = set()
    for match in entry_re.finditer(clause):
        count = re.sub(r"[ ,]", "", match.group("count"))
        share_class = " ".join((match.groupdict().get("class") or "").split()).upper()
        if share_class in {"OF", "THE", "ISSUED", "AND"}:
            share_class = ""
        item: dict[str, str] = {"share_count": count}
        if share_class:
            item["share_class"] = share_class
        nominal = match.groupdict().get("nominal")
        if nominal:
            normalized_nominal = _normalize_decimal(nominal)
            if normalized_nominal:
                item["nominal_value_per_share"] = normalized_nominal
        raw_currency = match.groupdict().get("currency") or ""
        resolved_currency = _resolve_currency(raw_currency, country_code=country_code)
        if resolved_currency:
            item["currency"] = resolved_currency
        elif raw_currency:
            item["currency_raw"] = raw_currency.upper()
        key = tuple(sorted(item.items()))
        if key not in seen:
            output.append(item)
            seen.add(key)
    return output[:20]


def _division_count_field(text: str) -> str:
    division = re.search(r"\bDIVIDED\s+INTO\b", text, flags=re.IGNORECASE)
    if not division:
        return "share_count"
    prefix = text[max(0, division.start() - 180) : division.start()]
    capital_labels = list(
        re.finditer(
            r"\b(?P<kind>ISSUED|AUTHORIZED|AUTHORISED|NOMINAL|PAID[- ]UP)?\s*(?:SHARE\s+)?CAPITAL\b",
            prefix,
            flags=re.IGNORECASE,
        )
    )
    if capital_labels and (capital_labels[-1].groupdict().get("kind") or "").upper() == "ISSUED":
        return "issued_share_count"
    return "share_count"


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
        re.sub(r"\s+", " ", fragment).strip().casefold() for fragment in excerpt.split("|") if len(fragment.strip()) >= 4
    ]
    for page_number, page_text in enumerate(page_texts, start=1):
        haystack = re.sub(r"\s+", " ", page_text or "").casefold()
        if any(fragment in haystack for fragment in fragments):
            return page_number
    return None
