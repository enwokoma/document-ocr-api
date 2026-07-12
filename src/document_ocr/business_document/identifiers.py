"""Typed identifier extraction for business and company documents.

Identifiers are deliberately modelled separately from a company's legal name
or address.  Registry, tax, employer, formation, and document-reference
numbers are not interchangeable, and one document can legitimately contain
several of them.

The extraction functions in this module are useful on their own, but profiles
normally supply the high-confidence, jurisdiction-specific patterns.  Generic
patterns remain available as a conservative fallback for unprofiled countries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING, Any, Iterable, Optional, Sequence

if TYPE_CHECKING:  # Avoid a profiles -> identifiers import cycle at runtime.
    from src.document_ocr.business_document.profiles import BusinessJurisdictionProfile
    from src.document_ocr.business_document.schema import JurisdictionResult


class IdentifierType(str, Enum):
    """Canonical, jurisdiction-neutral business identifier categories."""

    COMPANY_REGISTRATION_NUMBER = "COMPANY_REGISTRATION_NUMBER"
    BUSINESS_REGISTRATION_NUMBER = "BUSINESS_REGISTRATION_NUMBER"
    TAX_IDENTIFIER = "TAX_IDENTIFIER"
    EMPLOYER_IDENTIFIER = "EMPLOYER_IDENTIFIER"
    REGISTRY_NUMBER = "REGISTRY_NUMBER"
    STATE_FORMATION_IDENTIFIER = "STATE_FORMATION_IDENTIFIER"
    DOCUMENT_REFERENCE_NUMBER = "DOCUMENT_REFERENCE_NUMBER"
    OTHER = "OTHER"


CANONICAL_IDENTIFIER_TYPES = tuple(item.value for item in IdentifierType)


@dataclass(frozen=True)
class IdentifierPattern:
    """A typed regular expression supplied by a jurisdiction profile.

    Patterns should expose either a named ``value`` or ``number`` group.  The
    entire match is used only when neither group exists.  ``number_type`` keeps
    the local name (for example ``CAC_RC`` or ``EIN``) alongside the canonical
    type.
    """

    pattern: str
    identifier_type: IdentifierType | str
    number_type: Optional[str] = None
    confidence: float = 0.90
    label: Optional[str] = None
    issuing_authority: Optional[str] = None
    value_group: Optional[str] = None
    include_prefix: bool = True

    def __post_init__(self) -> None:
        canonical_identifier_type(self.identifier_type)
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("identifier-pattern confidence must be between 0 and 1")
        try:
            re.compile(self.pattern, flags=re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"invalid identifier regular expression: {exc}") from exc


@dataclass(frozen=True)
class RegistrationPattern:
    """Legacy-compatible company-registration pattern.

    ``fields.py`` consumes the first three attributes directly.  The additional
    metadata lets the same pattern participate in the typed identifier layer.
    """

    pattern: str
    number_type: str
    confidence: float = 0.90
    identifier_type: IdentifierType | str = IdentifierType.COMPANY_REGISTRATION_NUMBER
    label: Optional[str] = None
    issuing_authority: Optional[str] = None
    include_prefix: bool = True

    def __post_init__(self) -> None:
        canonical_identifier_type(self.identifier_type)
        if not self.number_type or not str(self.number_type).strip():
            raise ValueError("registration-pattern number_type is required")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("registration-pattern confidence must be between 0 and 1")
        try:
            re.compile(self.pattern, flags=re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"invalid registration regular expression: {exc}") from exc


@dataclass(frozen=True)
class IdentifierEvidence:
    """Bounded OCR evidence for an identifier candidate.

    Character offsets make the evidence page-resolvable when processors retain
    page boundaries.  ``page`` can be populated by a later pipeline stage.
    """

    text: str
    start: int
    end: int
    method: str
    pattern_label: Optional[str] = None
    page: Optional[int] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": " ".join(str(self.text or "").split())[:240] or None,
            "start": max(0, int(self.start)),
            "end": max(0, int(self.end)),
            "page": self.page,
            "method": self.method,
            "pattern_label": self.pattern_label,
        }


@dataclass(frozen=True)
class BusinessIdentifier:
    """One normalized, typed identifier with provenance and confidence."""

    identifier_type: str
    value: str
    normalized_value: str
    confidence: float
    evidence: tuple[IdentifierEvidence, ...]
    number_type: Optional[str] = None
    country_code: Optional[str] = None
    jurisdiction: Optional[str] = None
    issuing_authority: Optional[str] = None
    source: str = "generic_fallback"

    @property
    def type(self) -> str:
        """Return the canonical type using the concise API vocabulary."""
        return self.identifier_type

    def as_dict(self) -> dict[str, Any]:
        score = round(max(0.0, min(float(self.confidence), 1.0)), 3)
        return {
            "type": self.identifier_type,
            "number_type": self.number_type,
            "value": self.value,
            "normalized_value": self.normalized_value,
            "country_code": self.country_code,
            "jurisdiction": self.jurisdiction,
            "issuing_authority": self.issuing_authority,
            "confidence": score,
            "evidence": [item.as_dict() for item in self.evidence],
            "source": self.source,
        }


@dataclass(frozen=True)
class IdentifierConflict:
    """Several values competing for one local identifier designation."""

    identifier_type: str
    number_type: Optional[str]
    candidate_values: tuple[str, ...]
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.identifier_type,
            "number_type": self.number_type,
            "candidate_values": list(self.candidate_values),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class IdentifierExtractionResult:
    """Identifiers plus retained candidates, conflicts, and review warnings."""

    identifiers: tuple[BusinessIdentifier, ...] = ()
    candidates: tuple[BusinessIdentifier, ...] = ()
    conflicts: tuple[IdentifierConflict, ...] = ()
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "identifiers": [item.as_dict() for item in self.identifiers],
            "candidates": [item.as_dict() for item in self.candidates],
            "conflicts": [item.as_dict() for item in self.conflicts],
            "warnings": list(self.warnings),
        }


def canonical_identifier_type(value: IdentifierType | str) -> str:
    """Return a supported canonical identifier type or raise ``ValueError``."""
    raw = value.value if isinstance(value, IdentifierType) else str(value or "").strip().upper()
    aliases = {
        "COMPANY": IdentifierType.COMPANY_REGISTRATION_NUMBER.value,
        "COMPANY_REGISTRATION": IdentifierType.COMPANY_REGISTRATION_NUMBER.value,
        "BUSINESS": IdentifierType.BUSINESS_REGISTRATION_NUMBER.value,
        "BUSINESS_REGISTRATION": IdentifierType.BUSINESS_REGISTRATION_NUMBER.value,
        "TAX": IdentifierType.TAX_IDENTIFIER.value,
        "TIN": IdentifierType.TAX_IDENTIFIER.value,
        "EMPLOYER": IdentifierType.EMPLOYER_IDENTIFIER.value,
        "EIN": IdentifierType.EMPLOYER_IDENTIFIER.value,
        "REGISTRY": IdentifierType.REGISTRY_NUMBER.value,
        "STATE_FORMATION": IdentifierType.STATE_FORMATION_IDENTIFIER.value,
        "DOCUMENT_REFERENCE": IdentifierType.DOCUMENT_REFERENCE_NUMBER.value,
    }
    raw = aliases.get(raw, raw)
    if raw not in CANONICAL_IDENTIFIER_TYPES:
        raise ValueError(f"unsupported business identifier type: {value!r}")
    return raw


def extract_business_identifiers(
    text: str,
    *,
    country_code: Optional[str] = None,
    country_hint: Optional[str] = None,
    jurisdiction: Optional["JurisdictionResult | str"] = None,
    profile: Optional["BusinessJurisdictionProfile"] = None,
    include_generic: bool = True,
) -> IdentifierExtractionResult:
    """Extract all typed business identifiers from OCR text.

    A supplied hint is reconciled with the document text using the normal
    jurisdiction detector.  Passing a concrete ``profile`` is useful for a
    caller that has already made that decision itself.
    """
    raw_text = str(text or "")
    if not raw_text.strip():
        return IdentifierExtractionResult(
            warnings=("No OCR text was available for identifier extraction.",),
        )

    # Local imports keep pattern models reusable by profiles without a cycle.
    from src.document_ocr.business_document.jurisdictions import (
        detect_business_jurisdiction,
        detect_business_subdivision,
    )
    from src.document_ocr.business_document.profiles import (
        GENERIC_BUSINESS_PROFILE,
        get_business_profile,
    )

    warnings: list[str] = []
    jurisdiction_result: Optional[JurisdictionResult] = None
    explicit_code = country_hint or country_code
    if jurisdiction is not None and not isinstance(jurisdiction, str):
        jurisdiction_result = jurisdiction
    elif profile is None:
        hint = jurisdiction if isinstance(jurisdiction, str) else explicit_code
        jurisdiction_result = detect_business_jurisdiction(raw_text, hint)

    if jurisdiction_result is not None:
        resolved_code = jurisdiction_result.country_code
        if jurisdiction_result.conflict:
            warnings.append(
                "Country hint conflicts with registry evidence in the document; "
                f"using detected country {jurisdiction_result.country_code}."
            )
    else:
        resolved_code = profile.code if profile is not None and profile.code != "XXX" else explicit_code

    selected_profile = profile or get_business_profile(resolved_code, fallback=True)
    if selected_profile is None:  # Defensive: fallback=True normally prevents this.
        selected_profile = GENERIC_BUSINESS_PROFILE

    if selected_profile.code == GENERIC_BUSINESS_PROFILE.code:
        warnings.append("No country-specific identifier profile was selected; generic identifier patterns were used.")

    subdivision = detect_business_subdivision(raw_text, selected_profile.code)
    jurisdiction_name = subdivision.name if subdivision and subdivision.confidence >= 0.5 else None
    authority = (
        subdivision.registry_name
        if subdivision and subdivision.confidence >= 0.5
        else selected_profile.registry_name
        if selected_profile.code != "XXX"
        else None
    )
    if subdivision and subdivision.ambiguous:
        warnings.append("The state or subnational incorporation jurisdiction is ambiguous; review the matched evidence.")

    candidates: list[BusinessIdentifier] = []
    candidate_country_code = selected_profile.code if selected_profile.code != GENERIC_BUSINESS_PROFILE.code else resolved_code
    profile_source = "generic_fallback" if selected_profile.code == GENERIC_BUSINESS_PROFILE.code else "jurisdiction_profile"
    for pattern in selected_profile.identifier_patterns:
        candidates.extend(
            _extract_pattern_candidates(
                raw_text,
                pattern,
                country_code=candidate_country_code,
                jurisdiction=jurisdiction_name,
                default_authority=authority,
                source=profile_source,
            )
        )

    for registration_pattern in selected_profile.registration_patterns:
        candidates.extend(
            _extract_registration_candidates(
                raw_text,
                registration_pattern,
                country_code=candidate_country_code,
                jurisdiction=jurisdiction_name,
                default_authority=authority,
                source=profile_source,
            )
        )

    if include_generic and selected_profile.code != GENERIC_BUSINESS_PROFILE.code:
        for pattern in GENERIC_BUSINESS_PROFILE.identifier_patterns:
            candidates.extend(
                _extract_pattern_candidates(
                    raw_text,
                    pattern,
                    country_code=selected_profile.code,
                    jurisdiction=jurisdiction_name,
                    default_authority=authority,
                    source="generic_fallback",
                )
            )

    candidates = _deduplicate_same_type(candidates)
    identifiers, ambiguity_warnings = _resolve_type_ambiguity(candidates)
    warnings.extend(ambiguity_warnings)
    conflicts = _find_conflicts(identifiers)
    for conflict in conflicts:
        designation = conflict.number_type or conflict.identifier_type
        warnings.append(f"Conflicting values were found for {designation}: {', '.join(conflict.candidate_values)}.")

    if not identifiers:
        warnings.append("No reliable business, registry, tax, or document identifier was found.")

    return IdentifierExtractionResult(
        identifiers=tuple(_sort_identifiers(identifiers)),
        candidates=tuple(_sort_identifiers(candidates)),
        conflicts=tuple(conflicts),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def extract_identifiers(*args: Any, **kwargs: Any) -> IdentifierExtractionResult:
    """Concise alias for :func:`extract_business_identifiers`."""
    return extract_business_identifiers(*args, **kwargs)


def _extract_pattern_candidates(
    text: str,
    pattern: IdentifierPattern,
    *,
    country_code: Optional[str],
    jurisdiction: Optional[str],
    default_authority: Optional[str],
    source: str,
) -> list[BusinessIdentifier]:
    return _extract_candidates(
        text,
        pattern.pattern,
        identifier_type=canonical_identifier_type(pattern.identifier_type),
        number_type=pattern.number_type,
        confidence=pattern.confidence,
        label=pattern.label,
        issuing_authority=pattern.issuing_authority or default_authority,
        value_group=pattern.value_group,
        include_prefix=pattern.include_prefix,
        country_code=country_code,
        jurisdiction=jurisdiction,
        source=source,
    )


def _extract_registration_candidates(
    text: str,
    pattern: RegistrationPattern,
    *,
    country_code: Optional[str],
    jurisdiction: Optional[str],
    default_authority: Optional[str],
    source: str,
) -> list[BusinessIdentifier]:
    return _extract_candidates(
        text,
        pattern.pattern,
        identifier_type=canonical_identifier_type(pattern.identifier_type),
        number_type=pattern.number_type,
        confidence=pattern.confidence,
        label=pattern.label or pattern.number_type,
        issuing_authority=pattern.issuing_authority or default_authority,
        value_group=None,
        include_prefix=pattern.include_prefix,
        country_code=country_code,
        jurisdiction=jurisdiction,
        source=source,
    )


def _extract_candidates(
    text: str,
    regex: str,
    *,
    identifier_type: str,
    number_type: Optional[str],
    confidence: float,
    label: Optional[str],
    issuing_authority: Optional[str],
    value_group: Optional[str],
    include_prefix: bool,
    country_code: Optional[str],
    jurisdiction: Optional[str],
    source: str,
) -> list[BusinessIdentifier]:
    output = []
    for match in re.finditer(regex, text, flags=re.IGNORECASE | re.MULTILINE):
        value = _matched_value(match, value_group=value_group, include_prefix=include_prefix)
        normalized = normalize_identifier_value(value, identifier_type=identifier_type, number_type=number_type)
        if not normalized or not _plausible_identifier(normalized):
            continue

        local_type = str(number_type).strip().upper() if number_type else None
        prefix = str(match.groupdict().get("prefix") or "").strip().upper()
        if local_type and "{PREFIX}" in local_type:
            local_type = local_type.format(PREFIX=prefix or "REGISTRATION")
        if local_type and "{prefix}" in local_type:
            local_type = local_type.format(prefix=prefix or "REGISTRATION")

        evidence = IdentifierEvidence(
            text=_evidence_excerpt(text, match.start(), match.end()),
            start=match.start(),
            end=match.end(),
            method="jurisdiction_identifier_pattern" if source == "jurisdiction_profile" else "generic_identifier_pattern",
            pattern_label=label or local_type,
        )
        output.append(
            BusinessIdentifier(
                identifier_type=identifier_type,
                number_type=local_type,
                value=_display_identifier(value),
                normalized_value=normalized,
                country_code=country_code,
                jurisdiction=jurisdiction,
                issuing_authority=issuing_authority,
                confidence=round(max(0.0, min(float(confidence), 1.0)), 3),
                evidence=(evidence,),
                source=source,
            )
        )
    return output


def _matched_value(match: re.Match[str], *, value_group: Optional[str], include_prefix: bool) -> str:
    groups = match.groupdict()
    selected_group = value_group if value_group and groups.get(value_group) is not None else None
    if selected_group is None:
        selected_group = "value" if groups.get("value") is not None else "number" if groups.get("number") is not None else None
    value = groups.get(selected_group) if selected_group else match.group(0)
    prefix = str(groups.get("prefix") or "").strip()
    if include_prefix and prefix and value and not str(value).strip().upper().startswith(prefix.upper()):
        value = f"{prefix} {value}"
    return str(value or "")


def normalize_identifier_value(
    value: str,
    *,
    identifier_type: IdentifierType | str = IdentifierType.OTHER,
    number_type: Optional[str] = None,
) -> Optional[str]:
    """Normalize an identifier without assuming every country uses digits."""
    canonical_type = canonical_identifier_type(identifier_type)
    cleaned = str(value or "").upper()
    cleaned = cleaned.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    cleaned = re.sub(r"^[\s:#.,;]+|[\s:#.,;]+$", "", cleaned)
    cleaned = re.sub(r"\s*([/-])\s*", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None

    compact_type = str(number_type or "").upper()
    if canonical_type == IdentifierType.EMPLOYER_IDENTIFIER.value or compact_type in {"EIN", "FEIN"}:
        digits = re.sub(r"\D", "", cleaned)
        if len(digits) == 9:
            return f"{digits[:2]}-{digits[2:]}"
    if compact_type in {"ACN", "ABN"}:
        digits = re.sub(r"\D", "", cleaned)
        return digits or None
    if re.fullmatch(r"(?:RC|BN|IT|LLP|LP)\s+\d{4,12}", cleaned):
        return cleaned.replace(" ", "")
    return cleaned


def _display_identifier(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n:#.,;")


def _plausible_identifier(value: str) -> bool:
    compact = re.sub(r"[^A-Z0-9]", "", value.upper())
    if len(compact) < 4 or len(compact) > 40:
        return False
    if compact in {"NONE", "NULL", "UNKNOWN", "NOTAVAILABLE", "PENDING"}:
        return False
    return bool(re.search(r"\d", compact))


def _evidence_excerpt(text: str, start: int, end: int, radius: int = 70) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return " ".join(text[left:right].split())[:240]


def _deduplicate_same_type(items: Iterable[BusinessIdentifier]) -> list[BusinessIdentifier]:
    deduplicated: dict[tuple[str, str], BusinessIdentifier] = {}
    for item in items:
        key = (item.identifier_type, _identifier_key(item.normalized_value))
        current = deduplicated.get(key)
        if current is None:
            deduplicated[key] = item
            continue
        preferred = item if _candidate_rank(item) > _candidate_rank(current) else current
        evidence = _merge_evidence(current.evidence, item.evidence)
        deduplicated[key] = replace(preferred, evidence=evidence, confidence=max(current.confidence, item.confidence))
    return list(deduplicated.values())


def _resolve_type_ambiguity(
    items: Sequence[BusinessIdentifier],
) -> tuple[list[BusinessIdentifier], list[str]]:
    by_value: dict[str, list[BusinessIdentifier]] = {}
    for item in items:
        by_value.setdefault(_identifier_key(item.normalized_value), []).append(item)

    resolved: list[BusinessIdentifier] = []
    warnings = []
    for same_value in by_value.values():
        types = {item.identifier_type for item in same_value}
        if len(types) == 1:
            resolved.extend(same_value)
            continue
        winner = max(same_value, key=_candidate_rank)
        resolved.append(winner)
        generic_losers = [item for item in same_value if item is not winner]
        if (
            winner.source == "jurisdiction_profile"
            and winner.number_type
            and generic_losers
            and all(item.source == "generic_fallback" for item in generic_losers)
            and winner.confidence >= max(item.confidence for item in generic_losers) + 0.1
        ):
            continue
        warnings.append(
            f"Identifier {winner.normalized_value} matched multiple types "
            f"({', '.join(sorted(types))}); classified as {winner.identifier_type}."
        )
    return resolved, warnings


def _find_conflicts(items: Sequence[BusinessIdentifier]) -> list[IdentifierConflict]:
    grouped: dict[tuple[str, str, str, str], list[BusinessIdentifier]] = {}
    for item in items:
        # OTHER identifiers and differently named local identifiers can
        # legitimately be numerous and are not treated as conflicts.
        if item.identifier_type == IdentifierType.OTHER.value:
            continue
        key = (
            item.identifier_type,
            item.number_type or item.identifier_type,
            item.country_code or "",
            item.jurisdiction or "",
        )
        grouped.setdefault(key, []).append(item)

    conflicts = []
    for (identifier_type, number_type, _, _), candidates in grouped.items():
        values = tuple(dict.fromkeys(item.normalized_value for item in _sort_identifiers(candidates)))
        if len(values) < 2:
            continue
        conflicts.append(
            IdentifierConflict(
                identifier_type=identifier_type,
                number_type=number_type if number_type != identifier_type else None,
                candidate_values=values,
                reason="multiple_distinct_values_for_same_identifier_designation",
            )
        )
    return conflicts


def _candidate_rank(item: BusinessIdentifier) -> tuple[int, float, int]:
    source_score = 1 if item.source == "jurisdiction_profile" else 0
    specificity = 1 if item.number_type else 0
    return source_score, float(item.confidence), specificity


def _identifier_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _merge_evidence(
    first: Sequence[IdentifierEvidence], second: Sequence[IdentifierEvidence]
) -> tuple[IdentifierEvidence, ...]:
    output: list[IdentifierEvidence] = []
    seen = set()
    for item in (*first, *second):
        key = (item.start, item.end, item.method, item.pattern_label)
        if key not in seen:
            seen.add(key)
            output.append(item)
    return tuple(output)


def _sort_identifiers(items: Iterable[BusinessIdentifier]) -> list[BusinessIdentifier]:
    order = {value: index for index, value in enumerate(CANONICAL_IDENTIFIER_TYPES)}
    return sorted(
        items,
        key=lambda item: (
            order.get(item.identifier_type, len(order)),
            item.number_type or "",
            item.normalized_value,
        ),
    )
