"""Extensible jurisdiction profiles for business-document extraction.

Profiles contain evidence markers and identifier patterns, not parsing code.
Applications can register an additional ISO-3166 alpha-3 jurisdiction at
startup without modifying the core extractor.  The generic profile is used as
a fallback and intentionally avoids guessing a country.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from threading import RLock
from typing import Dict, Iterable, Optional

from src.document_ocr.business_document.identifiers import (
    IdentifierPattern,
    IdentifierType,
    RegistrationPattern,
)


def normalize_profile_code(value: Optional[str]) -> Optional[str]:
    """Return an ISO alpha-3 code without treating free text as a code."""
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    return code if re.fullmatch(r"[A-Z]{3}", code) else None


def normalize_profile_hint(value: Optional[str]) -> Optional[str]:
    """Normalize alpha-2 aliases and country names for registry lookups."""
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[^A-Z0-9]+", " ", value.upper()).strip()
    return re.sub(r"\s+", " ", normalized) or None


@dataclass(frozen=True)
class AuthorityMarker:
    """Weighted registry, statute, or country phrase used for detection."""

    label: str
    pattern: str
    weight: float

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("authority-marker label is required")
        if float(self.weight) <= 0:
            raise ValueError("authority-marker weight must be positive")
        try:
            re.compile(self.pattern, flags=re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"invalid authority-marker regular expression: {exc}") from exc


@dataclass(frozen=True)
class SubdivisionProfile:
    """State, province, or other subnational registry jurisdiction."""

    code: str
    name: str
    registry_name: str
    authority_markers: tuple[AuthorityMarker, ...]
    aliases: tuple[str, ...] = ()
    high_score: float = 5.0
    minimum_score: float = 3.5

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Z0-9-]{2,12}", self.code.upper()):
            raise ValueError(f"invalid subdivision code: {self.code!r}")
        if not self.name.strip() or not self.registry_name.strip():
            raise ValueError("subdivision name and registry name are required")
        if self.minimum_score <= 0 or self.high_score < self.minimum_score:
            raise ValueError("subdivision scoring thresholds are invalid")


@dataclass(frozen=True)
class BusinessJurisdictionProfile:
    """Registry and identifier hints for one incorporation jurisdiction."""

    code: str
    name: str
    registry_name: str
    authority_markers: tuple[AuthorityMarker, ...]
    registration_patterns: tuple[RegistrationPattern, ...] = ()
    identifier_patterns: tuple[IdentifierPattern, ...] = ()
    aliases: tuple[str, ...] = ()
    subdivisions: tuple[SubdivisionProfile, ...] = ()
    high_score: float = 8.0
    minimum_score: float = 3.0

    def __post_init__(self) -> None:
        code = self.code.upper()
        if code != "XXX" and not re.fullmatch(r"[A-Z]{3}", code):
            raise ValueError(f"business profile code must be ISO alpha-3: {self.code!r}")
        if not self.name.strip() or not self.registry_name.strip():
            raise ValueError("business profile name and registry name are required")
        if self.minimum_score <= 0 or self.high_score < self.minimum_score:
            raise ValueError("business profile scoring thresholds are invalid")
        subdivision_codes = [item.code.upper() for item in self.subdivisions]
        if len(subdivision_codes) != len(set(subdivision_codes)):
            raise ValueError(f"duplicate subdivision code in {self.code} profile")


class BusinessProfileRegistry:
    """Thread-safe registry for built-in and application-supplied profiles."""

    def __init__(self, profiles: Iterable[BusinessJurisdictionProfile] = ()) -> None:
        self._profiles: Dict[str, BusinessJurisdictionProfile] = {}
        self._lock = RLock()
        for profile in profiles:
            self.register(profile)

    @property
    def profiles(self) -> Dict[str, BusinessJurisdictionProfile]:
        """Return the live mapping retained for backwards compatibility."""
        return self._profiles

    def register(
        self,
        profile: BusinessJurisdictionProfile,
        *,
        replace: bool = False,
    ) -> BusinessJurisdictionProfile:
        if not isinstance(profile, BusinessJurisdictionProfile):
            raise TypeError("profile must be a BusinessJurisdictionProfile")
        if profile.code.upper() == GENERIC_PROFILE_CODE:
            raise ValueError("the built-in generic profile cannot be replaced")
        code = profile.code.upper()
        with self._lock:
            if code in self._profiles and not replace:
                raise ValueError(f"business profile {code} is already registered")
            self._validate_aliases(profile, replacing=code if replace else None)
            self._profiles[code] = profile
        return profile

    def unregister(self, country_code: str) -> Optional[BusinessJurisdictionProfile]:
        code = normalize_profile_code(country_code)
        if not code:
            return None
        with self._lock:
            return self._profiles.pop(code, None)

    def get(
        self,
        country_code: Optional[str],
        *,
        fallback: bool = False,
    ) -> Optional[BusinessJurisdictionProfile]:
        code = self.resolve_code(country_code)
        profile = self._profiles.get(code) if code else None
        return profile or (GENERIC_BUSINESS_PROFILE if fallback else None)

    def list(self) -> Dict[str, BusinessJurisdictionProfile]:
        with self._lock:
            return dict(sorted(self._profiles.items()))

    def resolve_code(self, value: Optional[str]) -> Optional[str]:
        normalized = normalize_profile_hint(value)
        if not normalized:
            return None
        if normalized in self._profiles:
            return normalized
        with self._lock:
            for code, profile in self._profiles.items():
                aliases = {normalize_profile_hint(alias) for alias in profile.aliases}
                aliases.add(normalize_profile_hint(profile.name))
                if normalized in aliases:
                    return code
        return normalize_profile_code(value)

    def _validate_aliases(
        self,
        profile: BusinessJurisdictionProfile,
        *,
        replacing: Optional[str],
    ) -> None:
        proposed: set[str] = set()
        for item in (*profile.aliases, profile.name):
            normalized = normalize_profile_hint(item)
            if normalized:
                proposed.add(normalized)
        for code, current in self._profiles.items():
            if code == replacing:
                continue
            existing: set[str] = set()
            for item in (*current.aliases, current.name):
                normalized = normalize_profile_hint(item)
                if normalized:
                    existing.add(normalized)
            overlap = proposed.intersection(existing)
            if overlap:
                raise ValueError(f"business profile aliases conflict with {code}: {', '.join(sorted(overlap))}")


GENERIC_PROFILE_CODE = "XXX"

GENERIC_IDENTIFIER_PATTERNS = (
    IdentifierPattern(
        r"\b(?:COMPANY\s+REGISTRATION|COMPANY|CORPORATION)\s+"
        r"(?:NO\.?|NUMBER|ID)\s*[:#-]?\s*(?P<value>[A-Z0-9][A-Z0-9./-]{3,30})\b",
        IdentifierType.COMPANY_REGISTRATION_NUMBER,
        "COMPANY_REGISTRATION_NUMBER",
        0.84,
        "company registration number",
    ),
    IdentifierPattern(
        r"\bBUSINESS\s+(?:REGISTRATION\s+)?(?:NO\.?|NUMBER|ID)\s*[:#-]?\s*"
        r"(?P<value>[A-Z0-9][A-Z0-9./-]{3,30})\b",
        IdentifierType.BUSINESS_REGISTRATION_NUMBER,
        "BUSINESS_REGISTRATION_NUMBER",
        0.85,
        "business registration number",
    ),
    IdentifierPattern(
        r"\b(?:TAX\s+IDENTIFICATION\s+(?:NO\.?|NUMBER)|TAX\s+ID(?:ENTIFICATION)?|TIN|"
        r"VAT\s+(?:REGISTRATION\s+)?(?:NO\.?|NUMBER))\s*[:#-]?\s*"
        r"(?P<value>[A-Z0-9][A-Z0-9./-]{4,30})\b",
        IdentifierType.TAX_IDENTIFIER,
        "TAX_IDENTIFIER",
        0.86,
        "tax identifier",
    ),
    IdentifierPattern(
        r"\b(?:(?:FEDERAL\s+)?EMPLOYER\s+IDENTIFICATION\s+(?:NO\.?|NUMBER)|FEIN|EIN)"
        r"\s*[:#-]?\s*(?P<value>\d{2}[- ]?\d{7})\b",
        IdentifierType.EMPLOYER_IDENTIFIER,
        "EIN",
        0.92,
        "employer identification number",
    ),
    IdentifierPattern(
        r"\b(?:REGISTRY|REGISTER)\s+(?:NO\.?|NUMBER|ID)\s*[:#-]?\s*"
        r"(?P<value>[A-Z0-9][A-Z0-9./-]{3,30})\b",
        IdentifierType.REGISTRY_NUMBER,
        "REGISTRY_NUMBER",
        0.82,
        "registry number",
    ),
    IdentifierPattern(
        r"\b(?:REGISTRY|REGISTER)\s+IDENTIFIER\s*[:#-]?\s*"
        r"(?P<value>[A-Z0-9][A-Z0-9./-]{3,30})\b",
        IdentifierType.REGISTRY_NUMBER,
        "REGISTRY_NUMBER",
        0.82,
        "registry identifier",
    ),
    IdentifierPattern(
        r"\b(?:ENTITY\s+)?REGISTRATION\s+(?:NO\.?|NUMBER|ID|IDENTIFIER)\s*[:#-]?\s*"
        r"(?P<value>[A-Z0-9][A-Z0-9./-]{3,30})\b",
        IdentifierType.REGISTRY_NUMBER,
        "REGISTRATION_NUMBER",
        0.80,
        "unclassified registration number",
    ),
    IdentifierPattern(
        r"\b(?:STATE\s+)?(?:ENTITY|FORMATION|CHARTER|FILE)\s+"
        r"(?:NO\.?|NUMBER|ID)\s*[:#-]?\s*(?P<value>[A-Z0-9][A-Z0-9./-]{3,30})\b",
        IdentifierType.STATE_FORMATION_IDENTIFIER,
        "STATE_FORMATION_IDENTIFIER",
        0.82,
        "state formation identifier",
    ),
    IdentifierPattern(
        r"\b(?:DOCUMENT|CERTIFICATE|REFERENCE|FILING)\s+"
        r"(?:NO\.?|NUMBER|ID|REF(?:ERENCE)?)\s*[:#-]?\s*"
        r"(?P<value>[A-Z0-9][A-Z0-9./-]{3,30})\b",
        IdentifierType.DOCUMENT_REFERENCE_NUMBER,
        "DOCUMENT_REFERENCE_NUMBER",
        0.80,
        "document reference number",
    ),
    IdentifierPattern(
        r"\b(?:LICEN[CS]E|PERMIT|UNIQUE\s+ENTITY)\s+(?:NO\.?|NUMBER|ID)\s*[:#-]?\s*"
        r"(?P<value>[A-Z0-9][A-Z0-9./-]{3,30})\b",
        IdentifierType.OTHER,
        "OTHER_IDENTIFIER",
        0.72,
        "other labelled identifier",
    ),
)

GENERIC_BUSINESS_PROFILE = BusinessJurisdictionProfile(
    code=GENERIC_PROFILE_CODE,
    name="Unknown jurisdiction",
    registry_name="Unknown business registry",
    authority_markers=(),
    identifier_patterns=GENERIC_IDENTIFIER_PATTERNS,
    high_score=8.0,
    minimum_score=3.0,
)


_US_STATE_NAMES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}

_US_SPECIAL_REGISTRIES = {
    "DE": "Delaware Division of Corporations",
    "DC": "District of Columbia Department of Licensing and Consumer Protection",
    "FL": "Florida Division of Corporations",
    "MA": "Massachusetts Secretary of the Commonwealth",
    "NY": "New York Department of State, Division of Corporations",
}


def _us_subdivision(code: str, name: str) -> SubdivisionProfile:
    escaped = re.escape(name).replace(r"\ ", r"\s+")
    registry = _US_SPECIAL_REGISTRIES.get(code, f"{name} Secretary of State")
    authority_pattern = re.escape(registry).replace(r"\ ", r"\s+")
    markers = [
        AuthorityMarker(f"State of {name}", rf"\bSTATE\s+OF\s+{escaped}\b", 5.0),
        AuthorityMarker(
            f"{name} Secretary of State",
            rf"\b{escaped}\s+(?:OFFICE\s+OF\s+THE\s+)?SECRETARY\s+OF\s+STATE\b",
            5.0,
        ),
    ]
    if registry != f"{name} Secretary of State":
        markers.append(AuthorityMarker(registry, rf"\b{authority_pattern}\b", 6.0))
    return SubdivisionProfile(
        code=f"US-{code}",
        name=name,
        registry_name=registry,
        authority_markers=tuple(markers),
        aliases=(code, f"US-{code}", name),
        high_score=6.0,
    )


US_SUBDIVISIONS = tuple(_us_subdivision(code, name) for code, name in _US_STATE_NAMES.items())
_US_STATE_ALTERNATION = "|".join(
    re.escape(name).replace(r"\ ", r"\s+") for name in sorted(_US_STATE_NAMES.values(), key=len, reverse=True)
)


NIGERIA_PROFILE = BusinessJurisdictionProfile(
    code="NGA",
    name="Nigeria",
    registry_name="Corporate Affairs Commission",
    aliases=("NG", "NIGERIA", "FEDERAL REPUBLIC OF NIGERIA"),
    authority_markers=(
        AuthorityMarker("Corporate Affairs Commission", r"\bCORPORATE\s+AFFAIRS\s+COMMISSION\b", 6.0),
        AuthorityMarker("Federal Republic of Nigeria", r"\bFEDERAL\s+REPUBLIC\s+OF\s+NIGERIA\b", 4.0),
        AuthorityMarker("Companies and Allied Matters Act", r"\bCOMPANIES\s+AND\s+ALLIED\s+MATTERS\s+ACT\b", 4.0),
        AuthorityMarker("CAMA", r"\bCAMA(?:\s+20\d{2})?\b", 2.0),
        AuthorityMarker("CAC identifier", r"\b(?:RC|BN|IT|LLP|LP)\s*(?:NO\.?|NUMBER)?\s*[:#-]?\s*\d{4,12}\b", 2.0),
    ),
    registration_patterns=(
        RegistrationPattern(
            r"\bCOMPANY\s+REGISTRATION\s+(?:NO\.?|NUMBER)\s*[:#-]?\s*(?P<number>\d{4,12})\b",
            "CAC_COMPANY_REGISTRATION_NUMBER",
            0.96,
            IdentifierType.COMPANY_REGISTRATION_NUMBER,
            "CAC company registration number",
        ),
        RegistrationPattern(
            r"\b(?P<prefix>RC)\s*(?:NO\.?|NUMBER)?\s*[:#-]?\s*(?P<number>\d{4,12})\b",
            "CAC_RC",
            0.97,
            IdentifierType.COMPANY_REGISTRATION_NUMBER,
            "CAC registered company number",
        ),
        RegistrationPattern(
            r"\b(?P<prefix>BN)\s*(?:NO\.?|NUMBER)?\s*[:#-]?\s*(?P<number>\d{4,12})\b",
            "CAC_BN",
            0.97,
            IdentifierType.BUSINESS_REGISTRATION_NUMBER,
            "CAC business-name number",
        ),
        RegistrationPattern(
            r"\b(?P<prefix>IT)\s*(?:NO\.?|NUMBER)?\s*[:#-]?\s*(?P<number>\d{4,12})\b",
            "CAC_IT",
            0.96,
            IdentifierType.REGISTRY_NUMBER,
            "CAC incorporated-trustee number",
        ),
        RegistrationPattern(
            r"\b(?P<prefix>LLP)\s*(?:NO\.?|NUMBER)?\s*[:#-]?\s*(?P<number>\d{4,12})\b",
            "CAC_LLP",
            0.96,
            IdentifierType.COMPANY_REGISTRATION_NUMBER,
            "CAC limited-liability partnership number",
        ),
        RegistrationPattern(
            r"\b(?P<prefix>LP)\s*(?:NO\.?|NUMBER)?\s*[:#-]?\s*(?P<number>\d{4,12})\b",
            "CAC_LP",
            0.95,
            IdentifierType.COMPANY_REGISTRATION_NUMBER,
            "CAC limited-partnership number",
        ),
        RegistrationPattern(
            r"\b(?<!COMPANY\s)(?:REGISTRATION|REGISTERED)\s+(?:NO\.?|NUMBER)\s*[:#-]?\s*(?P<number>\d{4,12})\b",
            "CAC_REGISTRATION_NUMBER",
            0.88,
            IdentifierType.REGISTRY_NUMBER,
            "CAC registration number",
        ),
    ),
    identifier_patterns=(
        IdentifierPattern(
            r"\b(?:FIRS\s+)?(?:TAX\s+IDENTIFICATION\s+(?:NO\.?|NUMBER)|TIN)\s*[:#-]?\s*"
            r"(?P<value>(?:\d{8}(?:-\d{4})?|\d{9,14}))\b",
            IdentifierType.TAX_IDENTIFIER,
            "NIGERIAN_TIN",
            0.94,
            "Nigerian tax identification number",
            "Federal Inland Revenue Service",
        ),
        IdentifierPattern(
            r"\b(?:CAC\s+)?(?:DOCUMENT|CERTIFICATE|REFERENCE)\s+(?:NO\.?|NUMBER|REF)\s*[:#-]?\s*"
            r"(?P<value>[A-Z0-9][A-Z0-9/-]{4,30})\b",
            IdentifierType.DOCUMENT_REFERENCE_NUMBER,
            "CAC_DOCUMENT_REFERENCE",
            0.88,
            "CAC document reference",
            "Corporate Affairs Commission",
        ),
    ),
)


GHANA_PROFILE = BusinessJurisdictionProfile(
    code="GHA",
    name="Ghana",
    registry_name="Office of the Registrar of Companies",
    aliases=("GH", "GHANA", "REPUBLIC OF GHANA"),
    authority_markers=(
        AuthorityMarker("Office of the Registrar of Companies", r"\bOFFICE\s+OF\s+THE\s+REGISTRAR\s+OF\s+COMPANIES\b", 6.0),
        AuthorityMarker("Registrar-General's Department", r"\bREGISTRAR[ -]GENERAL(?:'S)?\s+DEPARTMENT\b", 5.0),
        AuthorityMarker("Republic of Ghana", r"\bREPUBLIC\s+OF\s+GHANA\b", 4.0),
        AuthorityMarker("Companies Act 2019", r"\bCOMPANIES\s+ACT\s*,?\s*2019\s*\(?ACT\s*992\)?", 4.0),
        AuthorityMarker("Ghana company number", r"\b(?:CS|BN|CG|PL)[-\s]?\d{5,12}\b", 2.0),
    ),
    registration_patterns=(
        RegistrationPattern(
            r"\b(?P<number>(?:CS|BN|CG|PL)[-\s]?\d{5,12})\b",
            "GHANA_REGISTRATION_NUMBER",
            0.94,
        ),
    ),
    identifier_patterns=(
        IdentifierPattern(
            r"\b(?:GHANA\s+)?TIN\s*[:#-]?\s*(?P<value>[A-Z0-9-]{8,15})\b",
            IdentifierType.TAX_IDENTIFIER,
            "GHANA_TIN",
            0.90,
            "Ghana tax identification number",
            "Ghana Revenue Authority",
        ),
    ),
)


UNITED_KINGDOM_PROFILE = BusinessJurisdictionProfile(
    code="GBR",
    name="United Kingdom",
    registry_name="Companies House",
    aliases=("GB", "UK", "UNITED KINGDOM", "GREAT BRITAIN"),
    authority_markers=(
        AuthorityMarker("Companies House", r"\bCOMPANIES\s+HOUSE\b", 6.0),
        AuthorityMarker("UK Registrar of Companies", r"\bREGISTRAR\s+OF\s+COMPANIES\s+(?:FOR|IN)\b", 4.0),
        AuthorityMarker("Companies Act 2006", r"\bCOMPANIES\s+ACT\s+2006\b", 4.0),
        AuthorityMarker("United Kingdom", r"\bUNITED\s+KINGDOM\b", 2.5),
        AuthorityMarker(
            "UK registered office jurisdiction", r"\b(?:ENGLAND\s+AND\s+WALES|SCOTLAND|NORTHERN\s+IRELAND|WALES)\b", 1.5
        ),
    ),
    registration_patterns=(
        RegistrationPattern(
            r"\bCOMPANY\s+(?:NO\.?|NUMBER)\s*[:#-]?\s*(?P<number>[A-Z0-9]{6,10})\b",
            "UK_COMPANY_NUMBER",
            0.94,
        ),
        RegistrationPattern(
            r"\b(?P<number>(?:SC|NI|OC|SO|FC|LP|SL|R0|RS)\d{6,8})\b",
            "UK_COMPANY_NUMBER",
            0.92,
        ),
    ),
    identifier_patterns=(
        IdentifierPattern(
            r"\b(?:UNIQUE\s+TAXPAYER\s+REFERENCE|UTR)\s*[:#-]?\s*(?P<value>\d{10})\b",
            IdentifierType.TAX_IDENTIFIER,
            "UK_UTR",
            0.92,
            "unique taxpayer reference",
            "HM Revenue and Customs",
        ),
        IdentifierPattern(
            r"\b(?:VAT\s+(?:REGISTRATION\s+)?(?:NO\.?|NUMBER))\s*[:#-]?\s*(?P<value>GB\s?\d{9})\b",
            IdentifierType.TAX_IDENTIFIER,
            "UK_VAT_NUMBER",
            0.91,
            "UK VAT registration number",
            "HM Revenue and Customs",
        ),
    ),
)


KENYA_PROFILE = BusinessJurisdictionProfile(
    code="KEN",
    name="Kenya",
    registry_name="Business Registration Service",
    aliases=("KE", "KENYA", "REPUBLIC OF KENYA"),
    authority_markers=(
        AuthorityMarker("Business Registration Service", r"\bBUSINESS\s+REGISTRATION\s+SERVICE\b", 6.0),
        AuthorityMarker("Republic of Kenya", r"\bREPUBLIC\s+OF\s+KENYA\b", 4.0),
        AuthorityMarker("Kenyan Registrar of Companies", r"\bREGISTRAR\s+OF\s+COMPANIES\s+KENYA\b", 3.0),
        AuthorityMarker("Kenya Companies Act 2015", r"\bCOMPANIES\s+ACT\s*,?\s*2015\b", 4.0),
    ),
    registration_patterns=(
        RegistrationPattern(
            r"\b(?P<number>PVT[-/][A-Z0-9/-]{5,24}|CPR[/A-Z0-9-]{5,24})\b",
            "KENYA_COMPANY_NUMBER",
            0.94,
        ),
    ),
    identifier_patterns=(
        IdentifierPattern(
            r"\b(?:KRA\s+)?PIN\s*[:#-]?\s*(?P<value>[A-Z]\d{9}[A-Z])\b",
            IdentifierType.TAX_IDENTIFIER,
            "KRA_PIN",
            0.95,
            "Kenya Revenue Authority PIN",
            "Kenya Revenue Authority",
        ),
    ),
)


SOUTH_AFRICA_PROFILE = BusinessJurisdictionProfile(
    code="ZAF",
    name="South Africa",
    registry_name="Companies and Intellectual Property Commission",
    aliases=("ZA", "SOUTH AFRICA", "REPUBLIC OF SOUTH AFRICA"),
    authority_markers=(
        AuthorityMarker(
            "Companies and Intellectual Property Commission",
            r"\bCOMPANIES\s+AND\s+INTELLECTUAL\s+PROPERTY\s+COMMISSION\b",
            6.0,
        ),
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
    identifier_patterns=(
        IdentifierPattern(
            r"\b(?:INCOME\s+TAX|VAT|PAYE)\s+(?:NO\.?|NUMBER|REFERENCE)\s*[:#-]?\s*"
            r"(?P<value>\d{8,12})\b",
            IdentifierType.TAX_IDENTIFIER,
            "SARS_TAX_REFERENCE",
            0.90,
            "South African tax reference",
            "South African Revenue Service",
        ),
    ),
)


CANADA_PROFILE = BusinessJurisdictionProfile(
    code="CAN",
    name="Canada",
    registry_name="Corporations Canada",
    aliases=("CA", "CANADA", "GOVERNMENT OF CANADA"),
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
    identifier_patterns=(
        IdentifierPattern(
            r"\b(?:BUSINESS\s+NUMBER|BN)\s*[:#-]?\s*(?P<value>\d{9}(?:\s?[A-Z]{2}\s?\d{4})?)\b",
            IdentifierType.TAX_IDENTIFIER,
            "CANADIAN_BUSINESS_NUMBER",
            0.93,
            "Canada Revenue Agency business number",
            "Canada Revenue Agency",
        ),
    ),
)


UNITED_STATES_PROFILE = BusinessJurisdictionProfile(
    code="USA",
    name="United States",
    registry_name="State corporate registry",
    aliases=("US", "U.S.", "UNITED STATES", "UNITED STATES OF AMERICA"),
    authority_markers=(
        AuthorityMarker("Secretary of State", r"\bSECRETARY\s+OF\s+STATE\b", 4.0),
        AuthorityMarker("State Department", r"\bDEPARTMENT\s+OF\s+STATE\b", 3.0),
        AuthorityMarker("Named US state", rf"\bSTATE\s+OF\s+(?:{_US_STATE_ALTERNATION})\b", 4.0),
        AuthorityMarker("US corporation law", r"\b(?:GENERAL\s+)?CORPORATION\s+LAW\b", 2.0),
        AuthorityMarker("United States", r"\bUNITED\s+STATES(?:\s+OF\s+AMERICA)?\b", 3.0),
    ),
    registration_patterns=(
        RegistrationPattern(
            r"\bENTITY\s+(?:NO\.?|NUMBER|ID)\s*[:#-]?\s*(?P<number>[A-Z0-9-]{5,20})\b",
            "US_STATE_ENTITY_NUMBER",
            0.91,
            IdentifierType.STATE_FORMATION_IDENTIFIER,
            "state entity number",
        ),
        RegistrationPattern(
            r"\bFILE\s+(?:NO\.?|NUMBER|ID)\s*[:#-]?\s*(?P<number>[A-Z0-9-]{5,20})\b",
            "US_STATE_FILE_NUMBER",
            0.90,
            IdentifierType.STATE_FORMATION_IDENTIFIER,
            "state file number",
        ),
        RegistrationPattern(
            r"\bCHARTER\s+(?:NO\.?|NUMBER|ID)\s*[:#-]?\s*(?P<number>[A-Z0-9-]{5,20})\b",
            "US_STATE_CHARTER_NUMBER",
            0.90,
            IdentifierType.STATE_FORMATION_IDENTIFIER,
            "state charter number",
        ),
    ),
    identifier_patterns=(
        IdentifierPattern(
            r"\b(?:(?:FEDERAL\s+)?EMPLOYER\s+IDENTIFICATION\s+(?:NO\.?|NUMBER)|FEIN|EIN)"
            r"\s*[:#-]?\s*(?P<value>\d{2}[- ]?\d{7})\b",
            IdentifierType.EMPLOYER_IDENTIFIER,
            "EIN",
            0.98,
            "US employer identification number",
            "Internal Revenue Service",
        ),
        IdentifierPattern(
            r"\b(?:FEDERAL\s+TAX\s+ID(?:ENTIFICATION)?(?:\s+(?:NO\.?|NUMBER))?)\s*[:#-]?\s*"
            r"(?P<value>\d{2}[- ]?\d{7})\b",
            IdentifierType.TAX_IDENTIFIER,
            "US_FEDERAL_TAX_ID",
            0.90,
            "US federal tax identifier",
            "Internal Revenue Service",
        ),
        IdentifierPattern(
            r"\b(?:DOCUMENT|FILING)\s+(?:NO\.?|NUMBER|ID)\s*[:#-]?\s*"
            r"(?P<value>[A-Z0-9-]{5,24})\b",
            IdentifierType.DOCUMENT_REFERENCE_NUMBER,
            "US_FILING_REFERENCE",
            0.87,
            "US filing reference",
        ),
    ),
    subdivisions=US_SUBDIVISIONS,
    minimum_score=4.0,
)


AUSTRALIA_PROFILE = BusinessJurisdictionProfile(
    code="AUS",
    name="Australia",
    registry_name="Australian Securities and Investments Commission",
    aliases=("AU", "AUSTRALIA", "COMMONWEALTH OF AUSTRALIA"),
    authority_markers=(
        AuthorityMarker(
            "Australian Securities and Investments Commission",
            r"\bAUSTRALIAN\s+SECURITIES\s+AND\s+INVESTMENTS\s+COMMISSION\b",
            6.0,
        ),
        AuthorityMarker("ASIC", r"\bASIC\b", 4.0),
        AuthorityMarker("Corporations Act 2001", r"\bCORPORATIONS\s+ACT\s+2001\b", 4.0),
        AuthorityMarker("Commonwealth of Australia", r"\bCOMMONWEALTH\s+OF\s+AUSTRALIA\b", 3.0),
    ),
    registration_patterns=(
        RegistrationPattern(
            r"\bACN\s*[:#-]?\s*(?P<number>\d{3}\s?\d{3}\s?\d{3})\b",
            "ACN",
            0.96,
        ),
    ),
    identifier_patterns=(
        IdentifierPattern(
            r"\bABN\s*[:#-]?\s*(?P<value>\d{2}\s?\d{3}\s?\d{3}\s?\d{3})\b",
            IdentifierType.TAX_IDENTIFIER,
            "ABN",
            0.96,
            "Australian business number",
            "Australian Taxation Office",
        ),
        IdentifierPattern(
            r"\bTFN\s*[:#-]?\s*(?P<value>\d{8,9})\b",
            IdentifierType.TAX_IDENTIFIER,
            "TFN",
            0.90,
            "Australian tax file number",
            "Australian Taxation Office",
        ),
    ),
)


BUILTIN_BUSINESS_PROFILES = (
    NIGERIA_PROFILE,
    GHANA_PROFILE,
    UNITED_KINGDOM_PROFILE,
    KENYA_PROFILE,
    SOUTH_AFRICA_PROFILE,
    CANADA_PROFILE,
    UNITED_STATES_PROFILE,
    AUSTRALIA_PROFILE,
)

BUSINESS_PROFILE_REGISTRY = BusinessProfileRegistry(BUILTIN_BUSINESS_PROFILES)


def register_business_profile(
    profile: BusinessJurisdictionProfile,
    *,
    replace: bool = False,
) -> BusinessJurisdictionProfile:
    """Register an application-supplied profile.

    Duplicate country codes fail unless the caller explicitly asks to replace
    an existing profile.  This keeps accidental import-order overrides visible.
    """
    return BUSINESS_PROFILE_REGISTRY.register(profile, replace=replace)


def unregister_business_profile(country_code: str) -> Optional[BusinessJurisdictionProfile]:
    """Remove a registered profile and return it, if present."""
    return BUSINESS_PROFILE_REGISTRY.unregister(country_code)


def get_business_profile(
    country_code: Optional[str],
    *,
    fallback: bool = False,
) -> Optional[BusinessJurisdictionProfile]:
    """Look up a profile by ISO code, alpha-2 alias, or country name."""
    return BUSINESS_PROFILE_REGISTRY.get(country_code, fallback=fallback)


def list_business_profiles() -> Dict[str, BusinessJurisdictionProfile]:
    """Return registered country profiles in deterministic order."""
    return BUSINESS_PROFILE_REGISTRY.list()


def resolve_business_country_code(value: Optional[str]) -> Optional[str]:
    """Resolve a profile alias while preserving a valid unprofiled alpha-3 code."""
    return BUSINESS_PROFILE_REGISTRY.resolve_code(value)
