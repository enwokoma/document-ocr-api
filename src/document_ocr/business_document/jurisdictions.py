"""Country and registry detection for business registration documents.

The parser remains usable for an unknown country.  Profiles add stronger
authority markers and registration-number patterns for common English-language
registry documents; they are not a claim that every layout from that country is
fully supported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, Optional

from src.document_ocr.business_document.schema import JurisdictionResult, confidence_level


@dataclass(frozen=True)
class AuthorityMarker:
    """Weighted registry/country phrase used for jurisdiction inference."""

    label: str
    pattern: str
    weight: float


@dataclass(frozen=True)
class RegistrationPattern:
    """Country-specific company identifier pattern."""

    pattern: str
    number_type: str
    confidence: float = 0.90


@dataclass(frozen=True)
class BusinessJurisdictionProfile:
    """Business-registry hints for one incorporation jurisdiction."""

    code: str
    name: str
    registry_name: str
    authority_markers: tuple[AuthorityMarker, ...]
    registration_patterns: tuple[RegistrationPattern, ...] = ()
    high_score: float = 8.0
    minimum_score: float = 3.0


BUSINESS_JURISDICTIONS: Dict[str, BusinessJurisdictionProfile] = {
    "NGA": BusinessJurisdictionProfile(
        code="NGA",
        name="Nigeria",
        registry_name="Corporate Affairs Commission",
        authority_markers=(
            AuthorityMarker("Corporate Affairs Commission", r"\bCORPORATE\s+AFFAIRS\s+COMMISSION\b", 6.0),
            AuthorityMarker("Federal Republic of Nigeria", r"\bFEDERAL\s+REPUBLIC\s+OF\s+NIGERIA\b", 4.0),
            AuthorityMarker("Companies and Allied Matters Act", r"\bCOMPANIES\s+AND\s+ALLIED\s+MATTERS\s+ACT\b", 4.0),
            AuthorityMarker("CAMA", r"\bCAMA(?:\s+20\d{2})?\b", 2.0),
            AuthorityMarker("CAC identifier", r"\b(?:RC|BN|IT|LLP|LP)\s*(?:NO\.?|NUMBER)?\s*[:#-]?\s*\d{4,12}\b", 2.0),
        ),
        registration_patterns=(
            RegistrationPattern(
                r"\b(?P<prefix>RC|BN|IT|LLP|LP)\s*(?:NO\.?|NUMBER)?\s*[:#-]?\s*(?P<number>\d{4,12})\b",
                "CAC_{prefix}",
                0.97,
            ),
            RegistrationPattern(
                r"\b(?:REGISTRATION|REGISTERED)\s+(?:NO\.?|NUMBER)\s*[:#-]?\s*(?P<number>\d{4,12})\b",
                "CAC_REGISTRATION_NUMBER",
                0.90,
            ),
        ),
    ),
    "GHA": BusinessJurisdictionProfile(
        code="GHA",
        name="Ghana",
        registry_name="Office of the Registrar of Companies",
        authority_markers=(
            AuthorityMarker("Office of the Registrar of Companies", r"\bOFFICE\s+OF\s+THE\s+REGISTRAR\s+OF\s+COMPANIES\b", 6.0),
            AuthorityMarker("Registrar-General's Department", r"\bREGISTRAR[ -]GENERAL(?:'S)?\s+DEPARTMENT\b", 5.0),
            AuthorityMarker("Republic of Ghana", r"\bREPUBLIC\s+OF\s+GHANA\b", 4.0),
            AuthorityMarker("Companies Act 2019", r"\bCOMPANIES\s+ACT\s*,?\s*2019\s*\(?ACT\s*992\)?", 4.0),
            AuthorityMarker("Ghana company number", r"\b(?:CS|BN|CG|PL)\d{5,12}\b", 2.0),
        ),
        registration_patterns=(
            RegistrationPattern(
                r"\b(?P<number>(?:CS|BN|CG|PL)[-\s]?\d{5,12})\b",
                "GHANA_REGISTRATION_NUMBER",
                0.94,
            ),
        ),
    ),
    "GBR": BusinessJurisdictionProfile(
        code="GBR",
        name="United Kingdom",
        registry_name="Companies House",
        authority_markers=(
            AuthorityMarker("Companies House", r"\bCOMPANIES\s+HOUSE\b", 6.0),
            AuthorityMarker("Registrar of Companies", r"\bREGISTRAR\s+OF\s+COMPANIES\b", 4.0),
            AuthorityMarker("Companies Act 2006", r"\bCOMPANIES\s+ACT\s+2006\b", 4.0),
            AuthorityMarker("United Kingdom", r"\bUNITED\s+KINGDOM\b", 2.5),
            AuthorityMarker("UK registered office jurisdiction", r"\b(?:ENGLAND\s+AND\s+WALES|SCOTLAND|NORTHERN\s+IRELAND|WALES)\b", 1.5),
        ),
        registration_patterns=(
            RegistrationPattern(
                r"\bCOMPANY\s+(?:NO\.?|NUMBER)\s*[:#-]?\s*(?P<number>[A-Z0-9]{6,10})\b",
                "UK_COMPANY_NUMBER",
                0.92,
            ),
            RegistrationPattern(
                r"\b(?P<number>(?:SC|NI|OC|SO|FC|LP|SL|R0|RS)\d{6,8})\b",
                "UK_COMPANY_NUMBER",
                0.92,
            ),
        ),
    ),
    "KEN": BusinessJurisdictionProfile(
        code="KEN",
        name="Kenya",
        registry_name="Business Registration Service",
        authority_markers=(
            AuthorityMarker("Business Registration Service", r"\bBUSINESS\s+REGISTRATION\s+SERVICE\b", 6.0),
            AuthorityMarker("Republic of Kenya", r"\bREPUBLIC\s+OF\s+KENYA\b", 4.0),
            AuthorityMarker("Kenyan Registrar of Companies", r"\bREGISTRAR\s+OF\s+COMPANIES\b", 3.0),
            AuthorityMarker("Kenya Companies Act 2015", r"\bCOMPANIES\s+ACT\s*,?\s*2015\b", 4.0),
        ),
        registration_patterns=(
            RegistrationPattern(
                r"\b(?P<number>PVT[-/][A-Z0-9/-]{5,24}|CPR[/A-Z0-9-]{5,24})\b",
                "KENYA_COMPANY_NUMBER",
                0.94,
            ),
        ),
    ),
    "ZAF": BusinessJurisdictionProfile(
        code="ZAF",
        name="South Africa",
        registry_name="Companies and Intellectual Property Commission",
        authority_markers=(
            AuthorityMarker("Companies and Intellectual Property Commission", r"\bCOMPANIES\s+AND\s+INTELLECTUAL\s+PROPERTY\s+COMMISSION\b", 6.0),
            AuthorityMarker("CIPC", r"\bCIPC\b", 4.0),
            AuthorityMarker("Republic of South Africa", r"\bREPUBLIC\s+OF\s+SOUTH\s+AFRICA\b", 4.0),
            AuthorityMarker("Companies Act 71 of 2008", r"\bCOMPANIES\s+ACT\s+(?:NO\.?\s*)?71\s+OF\s+2008\b", 4.0),
        ),
        registration_patterns=(
            RegistrationPattern(
                r"\b(?P<number>\d{4}/\d{5,10}/\d{2})\b",
                "CIPC_ENTERPRISE_NUMBER",
                0.97,
            ),
        ),
    ),
    "CAN": BusinessJurisdictionProfile(
        code="CAN",
        name="Canada",
        registry_name="Corporations Canada",
        authority_markers=(
            AuthorityMarker("Corporations Canada", r"\bCORPORATIONS\s+CANADA\b", 6.0),
            AuthorityMarker("Canada Business Corporations Act", r"\bCANADA\s+BUSINESS\s+CORPORATIONS\s+ACT\b", 5.0),
            AuthorityMarker("Government of Canada", r"\bGOVERNMENT\s+OF\s+CANADA\b", 3.0),
            AuthorityMarker("Canadian federal corporation", r"\bFEDERAL\s+CORPORATION\b", 2.0),
        ),
        registration_patterns=(
            RegistrationPattern(
                r"\b(?:CORPORATION\s+(?:NO\.?|NUMBER)\s*[:#-]?\s*)?(?P<number>\d{6,10}-\d)\b",
                "CANADIAN_CORPORATION_NUMBER",
                0.94,
            ),
        ),
    ),
    "USA": BusinessJurisdictionProfile(
        code="USA",
        name="United States",
        registry_name="State corporate registry",
        authority_markers=(
            AuthorityMarker("Secretary of State", r"\bSECRETARY\s+OF\s+STATE\b", 4.0),
            AuthorityMarker("State Department", r"\bDEPARTMENT\s+OF\s+STATE\b", 3.0),
            AuthorityMarker("State of", r"\bSTATE\s+OF\s+[A-Z]{4,20}\b", 2.5),
            AuthorityMarker("US corporation law", r"\b(?:GENERAL\s+)?CORPORATION\s+LAW\b", 2.0),
        ),
        registration_patterns=(
            RegistrationPattern(
                r"\b(?:FILE|ENTITY|DOCUMENT|CHARTER)\s+(?:NO\.?|NUMBER|ID)\s*[:#-]?\s*(?P<number>[A-Z0-9-]{5,20})\b",
                "US_STATE_ENTITY_NUMBER",
                0.86,
            ),
        ),
        minimum_score=5.0,
    ),
    "AUS": BusinessJurisdictionProfile(
        code="AUS",
        name="Australia",
        registry_name="Australian Securities and Investments Commission",
        authority_markers=(
            AuthorityMarker("Australian Securities and Investments Commission", r"\bAUSTRALIAN\s+SECURITIES\s+AND\s+INVESTMENTS\s+COMMISSION\b", 6.0),
            AuthorityMarker("ASIC", r"\bASIC\b", 4.0),
            AuthorityMarker("Corporations Act 2001", r"\bCORPORATIONS\s+ACT\s+2001\b", 4.0),
            AuthorityMarker("Commonwealth of Australia", r"\bCOMMONWEALTH\s+OF\s+AUSTRALIA\b", 3.0),
        ),
        registration_patterns=(
            RegistrationPattern(r"\bACN\s*[:#-]?\s*(?P<number>\d{3}\s?\d{3}\s?\d{3})\b", "AUSTRALIAN_COMPANY_NUMBER", 0.94),
        ),
    ),
}


def get_business_jurisdiction(country_code: Optional[str]) -> Optional[BusinessJurisdictionProfile]:
    """Return one registered business-jurisdiction profile."""
    code = normalize_country_code(country_code)
    return BUSINESS_JURISDICTIONS.get(code) if code else None


def list_business_jurisdictions() -> Dict[str, BusinessJurisdictionProfile]:
    """Return registered jurisdiction profiles in deterministic order."""
    return dict(sorted(BUSINESS_JURISDICTIONS.items()))


def serialize_business_jurisdiction(profile: BusinessJurisdictionProfile) -> dict[str, object]:
    """Return non-regex jurisdiction metadata suitable for API discovery."""
    return {
        "country_code": profile.code,
        "country_name": profile.name,
        "registry_name": profile.registry_name,
        "registration_number_types": sorted({pattern.number_type for pattern in profile.registration_patterns}),
    }


def normalize_country_code(value: Optional[str]) -> Optional[str]:
    """Normalize an ISO-3166 alpha-3 hint without accepting arbitrary strings."""
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    return code if re.fullmatch(r"[A-Z]{3}", code) else None


def detect_business_jurisdiction(text: str, country_hint: Optional[str] = None) -> JurisdictionResult:
    """Reconcile a caller hint with registry authority phrases in OCR text."""
    normalized = re.sub(r"\s+", " ", str(text or "").upper()).strip()
    requested = normalize_country_code(country_hint)
    scored = [_score_profile(normalized, profile) for profile in BUSINESS_JURISDICTIONS.values()]
    scored.sort(key=lambda item: (item["confidence"], item["raw_score"]), reverse=True)
    best = scored[0] if scored else None

    detected_profile: Optional[BusinessJurisdictionProfile] = None
    matched_terms: tuple[str, ...] = ()
    text_confidence = 0.0
    if best and float(best["raw_score"]) >= float(best["minimum_score"]):
        detected_profile = best["profile"]  # type: ignore[assignment]
        matched_terms = tuple(best["matched_terms"])  # type: ignore[arg-type]
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

    registry_name = _dynamic_registry_name(selected, normalized)
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


def jurisdiction_keywords() -> tuple[str, ...]:
    """Return marker labels for PDF OCR page-quality scoring."""
    return tuple(
        dict.fromkeys(
            marker.label.upper()
            for profile in BUSINESS_JURISDICTIONS.values()
            for marker in profile.authority_markers
        )
    )


def _score_profile(normalized: str, profile: BusinessJurisdictionProfile) -> dict[str, object]:
    raw_score = 0.0
    matched_terms = []
    for marker in profile.authority_markers:
        if re.search(marker.pattern, normalized, flags=re.IGNORECASE):
            raw_score += marker.weight
            matched_terms.append(marker.label)
    confidence = min(raw_score / profile.high_score, 1.0)
    return {
        "profile": profile,
        "raw_score": round(raw_score, 3),
        "confidence": round(confidence, 4),
        "confidence_level": confidence_level(confidence),
        "minimum_score": profile.minimum_score,
        "matched_terms": matched_terms,
    }


def _dynamic_registry_name(profile: BusinessJurisdictionProfile, normalized: str) -> str:
    if profile.code != "USA":
        return profile.registry_name
    match = re.search(r"\bSTATE\s+OF\s+([A-Z][A-Z ]{2,24}?)(?:\s{2,}|\s+SECRETARY|\s+DEPARTMENT|\s+CERTIFICATE|$)", normalized)
    if not match:
        return profile.registry_name
    state = " ".join(match.group(1).split()).title()
    return f"{state} Secretary of State"
