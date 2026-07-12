"""Jurisdiction detection and the public business-profile registry facade.

Country hints are advisory: strong registry evidence in OCR text wins, while a
matching hint raises confidence.  Unknown countries remain valid inputs and use
the generic profile only during field/identifier extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.document_ocr.business_document.identifiers import (
    IdentifierPattern,
    IdentifierType,
    RegistrationPattern,
)
from src.document_ocr.business_document.profiles import (
    BUSINESS_PROFILE_REGISTRY,
    GENERIC_BUSINESS_PROFILE,
    AuthorityMarker,
    BusinessJurisdictionProfile,
    SubdivisionProfile,
    get_business_profile,
    list_business_profiles,
    register_business_profile,
    resolve_business_country_code,
    unregister_business_profile,
)
from src.document_ocr.business_document.schema import JurisdictionResult, confidence_level


@dataclass(frozen=True)
class SubdivisionResult:
    """Detected state, province, or equivalent company-registry jurisdiction."""

    country_code: str
    code: Optional[str]
    name: Optional[str]
    registry_name: Optional[str]
    confidence: float
    source: str
    matched_terms: tuple[str, ...] = ()
    ambiguous: bool = False
    alternatives: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        score = round(max(0.0, min(float(self.confidence), 1.0)), 3)
        return {
            "country_code": self.country_code,
            "jurisdiction_code": self.code,
            "jurisdiction_name": self.name,
            "registry_name": self.registry_name,
            "confidence": score,
            "confidence_level": confidence_level(score),
            "source": self.source,
            "matched_terms": list(self.matched_terms),
            "ambiguous": self.ambiguous,
            "alternatives": [dict(item) for item in self.alternatives],
        }


# Existing callers import this mapping directly.  It is the registry's live
# mapping, so profiles registered at application startup become detectable
# without rebuilding a second cache.
BUSINESS_JURISDICTIONS: Dict[str, BusinessJurisdictionProfile] = BUSINESS_PROFILE_REGISTRY.profiles


def register_business_jurisdiction(
    profile: BusinessJurisdictionProfile,
    *,
    replace: bool = False,
) -> BusinessJurisdictionProfile:
    """Compatibility-named facade for registering a business profile."""
    return register_business_profile(profile, replace=replace)


def unregister_business_jurisdiction(country_code: str) -> Optional[BusinessJurisdictionProfile]:
    """Remove a registered business-jurisdiction profile."""
    return unregister_business_profile(country_code)


def get_business_jurisdiction(country_code: Optional[str]) -> Optional[BusinessJurisdictionProfile]:
    """Return one registered profile, preserving the historical ``None`` fallback."""
    return get_business_profile(country_code, fallback=False)


def get_business_jurisdiction_or_default(country_code: Optional[str]) -> BusinessJurisdictionProfile:
    """Return a profile or the jurisdiction-neutral generic fallback."""
    profile = get_business_profile(country_code, fallback=True)
    return profile or GENERIC_BUSINESS_PROFILE


def list_business_jurisdictions() -> Dict[str, BusinessJurisdictionProfile]:
    """Return registered jurisdiction profiles in deterministic order."""
    return list_business_profiles()


def serialize_business_jurisdiction(profile: BusinessJurisdictionProfile) -> dict[str, object]:
    """Return non-regex jurisdiction metadata suitable for API discovery."""
    identifier_types = {_canonical_type(pattern.identifier_type) for pattern in profile.identifier_patterns}
    identifier_types.update(_canonical_type(pattern.identifier_type) for pattern in profile.registration_patterns)
    return {
        "country_code": profile.code,
        "country_name": profile.name,
        "registry_name": profile.registry_name,
        "aliases": sorted(profile.aliases),
        "registration_number_types": sorted({pattern.number_type for pattern in profile.registration_patterns}),
        "identifier_types": sorted(identifier_types),
        "subdivisions": [
            {
                "code": item.code,
                "name": item.name,
                "registry_name": item.registry_name,
            }
            for item in profile.subdivisions
        ],
    }


def normalize_country_code(value: Optional[str]) -> Optional[str]:
    """Resolve known country aliases and accept unprofiled ISO alpha-3 codes."""
    return resolve_business_country_code(value)


def detect_business_jurisdiction(text: str, country_hint: Optional[str] = None) -> JurisdictionResult:
    """Reconcile a caller country hint with registry evidence in OCR text."""
    normalized = _normalize_detection_text(text)
    requested = normalize_country_code(country_hint)
    scored = [_score_profile(normalized, profile) for profile in BUSINESS_JURISDICTIONS.values()]
    scored.sort(
        key=lambda item: (float(item["confidence"]), float(item["raw_score"]), str(item["code"])),
        reverse=True,
    )

    best = scored[0] if scored else None
    detected_profile: Optional[BusinessJurisdictionProfile] = None
    matched_terms: tuple[str, ...] = ()
    text_confidence = 0.0
    if best and float(best["raw_score"]) >= float(best["minimum_score"]):
        detected_profile = best["profile"]
        matched_terms = tuple(best["matched_terms"])
        text_confidence = float(best["confidence"])

    requested_profile = get_business_jurisdiction(requested)
    detected = detected_profile.code if detected_profile else None
    conflict = bool(requested and detected and requested != detected)

    if detected_profile and requested == detected:
        selected = detected_profile
        source = "country_hint_and_document"
        confidence = max(0.90, text_confidence)
    elif detected_profile:
        selected = detected_profile
        source = "document_text_conflict" if conflict else "document_text"
        confidence = text_confidence
    elif requested_profile:
        selected = requested_profile
        source = "country_hint"
        confidence = 0.65
    elif requested:
        return JurisdictionResult(
            country_code=requested,
            country_name=None,
            registry_name=None,
            source="country_hint_unprofiled",
            confidence=0.45,
            requested_country_code=requested,
        )
    else:
        return JurisdictionResult(
            country_code=None,
            country_name=None,
            registry_name=None,
            source="undetermined",
            confidence=0.0,
        )

    subdivision = detect_business_subdivision(normalized, selected.code)
    if subdivision and subdivision.code:
        matched_terms = tuple(dict.fromkeys((*matched_terms, *subdivision.matched_terms)))
    registry_name = subdivision.registry_name if subdivision and not subdivision.ambiguous else selected.registry_name
    return JurisdictionResult(
        country_code=selected.code,
        country_name=selected.name,
        registry_name=registry_name,
        source=source,
        confidence=confidence,
        requested_country_code=requested,
        detected_country_code=detected,
        matched_terms=matched_terms,
        conflict=conflict,
    )


def detect_business_subdivision(
    text: str,
    country_code: Optional[str] = None,
) -> Optional[SubdivisionResult]:
    """Detect a state/province for a profile that defines subdivisions.

    The built-in United States profile covers all states and the District of
    Columbia.  Other profiles can opt in by registering subdivisions with
    weighted authority markers.
    """
    normalized = _normalize_detection_text(text)
    profile = get_business_jurisdiction(country_code)
    if profile is None or not profile.subdivisions or not normalized:
        return None

    scored = [_score_subdivision(normalized, item) for item in profile.subdivisions]
    scored.sort(
        key=lambda item: (float(item["confidence"]), float(item["raw_score"]), str(item["code"])),
        reverse=True,
    )
    qualified = [item for item in scored if float(item["raw_score"]) >= float(item["minimum_score"])]
    if not qualified:
        return None

    best = qualified[0]
    close = [item for item in qualified[1:] if abs(float(best["confidence"]) - float(item["confidence"])) <= 0.08]
    ambiguous = bool(close)
    subdivision: SubdivisionProfile = best["profile"]
    alternatives = tuple(
        {
            "jurisdiction_code": item["code"],
            "jurisdiction_name": item["name"],
            "confidence": round(float(item["confidence"]), 3),
            "matched_terms": list(item["matched_terms"]),
        }
        for item in close[:3]
    )
    return SubdivisionResult(
        country_code=profile.code,
        code=subdivision.code,
        name=subdivision.name,
        registry_name=subdivision.registry_name,
        confidence=float(best["confidence"]),
        source="document_text_ambiguous" if ambiguous else "document_text",
        matched_terms=tuple(best["matched_terms"]),
        ambiguous=ambiguous,
        alternatives=alternatives,
    )


def jurisdiction_warnings(
    result: JurisdictionResult,
    subdivision: Optional[SubdivisionResult] = None,
) -> tuple[str, ...]:
    """Return review warnings without changing the stable jurisdiction schema."""
    warnings = []
    if result.country_code is None:
        warnings.append("The country of incorporation could not be reliably determined.")
    elif result.conflict:
        warnings.append("The supplied country hint conflicts with the country indicated by registry evidence.")
    elif result.source == "country_hint_unprofiled":
        warnings.append("The supplied country has no registered extraction profile; generic rules will be used.")
    elif result.source == "country_hint":
        warnings.append("The country is based only on the caller hint and was not confirmed by OCR text.")
    if subdivision and subdivision.ambiguous:
        warnings.append("The state or subnational incorporation jurisdiction is ambiguous.")
    return tuple(warnings)


def jurisdiction_keywords() -> tuple[str, ...]:
    """Return marker labels for PDF OCR page-quality scoring."""
    return tuple(
        dict.fromkeys(
            marker.label.upper() for profile in BUSINESS_JURISDICTIONS.values() for marker in profile.authority_markers
        )
    )


def _score_profile(normalized: str, profile: BusinessJurisdictionProfile) -> dict[str, Any]:
    raw_score = 0.0
    matched_terms = []
    for marker in profile.authority_markers:
        if re.search(marker.pattern, normalized, flags=re.IGNORECASE):
            raw_score += marker.weight
            matched_terms.append(marker.label)
    # A state/province authority is also strong evidence for its parent
    # country.  Count only the strongest subdivision marker to avoid inflating
    # documents that repeat an official header several times.
    subdivision_matches = [
        marker
        for subdivision in profile.subdivisions
        for marker in subdivision.authority_markers
        if re.search(marker.pattern, normalized, flags=re.IGNORECASE)
    ]
    if subdivision_matches:
        strongest = max(subdivision_matches, key=lambda marker: marker.weight)
        raw_score += strongest.weight
        matched_terms.append(strongest.label)
    confidence = min(raw_score / profile.high_score, 1.0)
    return {
        "profile": profile,
        "code": profile.code,
        "raw_score": round(raw_score, 3),
        "confidence": round(confidence, 4),
        "confidence_level": confidence_level(confidence),
        "minimum_score": profile.minimum_score,
        "matched_terms": matched_terms,
    }


def _score_subdivision(normalized: str, profile: SubdivisionProfile) -> dict[str, Any]:
    raw_score = 0.0
    matched_terms = []
    for marker in profile.authority_markers:
        if re.search(marker.pattern, normalized, flags=re.IGNORECASE):
            raw_score += marker.weight
            matched_terms.append(marker.label)
    confidence = min(raw_score / profile.high_score, 1.0)
    return {
        "profile": profile,
        "code": profile.code,
        "name": profile.name,
        "raw_score": round(raw_score, 3),
        "confidence": round(confidence, 4),
        "minimum_score": profile.minimum_score,
        "matched_terms": matched_terms,
    }


def _normalize_detection_text(text: str) -> str:
    value = str(text or "").upper().replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", value).strip()


def _canonical_type(value: IdentifierType | str) -> str:
    return value.value if isinstance(value, IdentifierType) else str(value)


__all__ = [
    "AuthorityMarker",
    "BUSINESS_JURISDICTIONS",
    "BusinessJurisdictionProfile",
    "GENERIC_BUSINESS_PROFILE",
    "IdentifierPattern",
    "IdentifierType",
    "JurisdictionResult",
    "RegistrationPattern",
    "SubdivisionProfile",
    "SubdivisionResult",
    "detect_business_jurisdiction",
    "detect_business_subdivision",
    "get_business_jurisdiction",
    "get_business_jurisdiction_or_default",
    "jurisdiction_keywords",
    "jurisdiction_warnings",
    "list_business_jurisdictions",
    "normalize_country_code",
    "register_business_jurisdiction",
    "serialize_business_jurisdiction",
    "unregister_business_jurisdiction",
]
