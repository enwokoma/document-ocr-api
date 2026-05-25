"""Country-profile registry used by the OCR processors.

The document processors should stay focused on OCR and parsing. Country-specific
logic lives in country folders such as `src/countries/nigeria/`; this module only
routes lookups to registered countries.
"""

from __future__ import annotations

from typing import Dict, Optional

from src.countries.ghana.rules import GHANA_PROFILE
from src.countries.nigeria.rules import NIGERIA_PROFILE, validate_nin as validate_nigerian_nin
from src.countries.profile import CountryProfile

COUNTRY_PROFILES: Dict[str, CountryProfile] = {
    GHANA_PROFILE.code: GHANA_PROFILE,
    NIGERIA_PROFILE.code: NIGERIA_PROFILE,
}


def get_country_profile(country_code: Optional[str]) -> Optional[CountryProfile]:
    """Look up a country profile by ISO-3166 alpha-3 code."""
    if not country_code:
        return None
    return COUNTRY_PROFILES.get(country_code.upper())


def list_country_profiles() -> Dict[str, CountryProfile]:
    """Return all registered country profiles keyed by country code."""
    return dict(sorted(COUNTRY_PROFILES.items()))


def serialize_country_profile(profile: CountryProfile) -> Dict[str, object]:
    """Convert a country profile to simple JSON-friendly values."""
    return {
        "country_code": profile.code,
        "country_name": profile.name,
        "mrz_code_aliases": sorted(profile.mrz_code_aliases),
        "supported_identity_documents": [
            {"code": code, "name": name}
            for code, name in sorted(profile.supported_identity_documents.items())
        ],
        "passport_personal_number_label": profile.passport_personal_number_label,
    }


def infer_country_profile(issuing_country: str, nationality: str = "") -> Optional[CountryProfile]:
    """Find the best profile for parsed passport country values."""
    for profile in COUNTRY_PROFILES.values():
        if profile.matches_mrz_country(issuing_country, nationality):
            return profile
    return None


def normalize_mrz_country(issuing_country: str, nationality: str = "") -> str:
    """Correct a noisy MRZ country code when a profile recognizes it."""
    profile = infer_country_profile(issuing_country, nationality)
    return profile.code if profile else (issuing_country or "")


def country_validation_summary(
    *,
    country_code: str,
    document_type: str,
    extracted_data: Dict[str, Optional[str]],
) -> Dict[str, object]:
    """Return country-specific validation details for an OCR response."""
    profile = get_country_profile(country_code)
    if profile is None:
        return {
            "country_code": country_code,
            "country_name": None,
            "supported": False,
            "checks": {},
        }

    checks: Dict[str, object] = {
        "document_type_supported": document_type in profile.supported_identity_documents,
    }

    if country_code == "NGA" and document_type in profile.supported_identity_documents:
        checks["nin_format_valid"] = validate_nigerian_nin(extracted_data.get("nin"))

    return {
        "country_code": profile.code,
        "country_name": profile.name,
        "supported": True,
        "supported_identity_documents": [
            {"code": code, "name": name}
            for code, name in sorted(profile.supported_identity_documents.items())
        ],
        "checks": checks,
    }
