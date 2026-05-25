"""Country-specific document rules used by the OCR processors.

The processors should stay focused on OCR and parsing. Rules that vary by
country, such as country-code aliases or local ID validation, live here so new
countries can be added without rewriting the passport or ID-card pipelines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Optional, Set


@dataclass(frozen=True)
class CountryProfile:
    """Configuration for one country's supported document behavior."""

    code: str
    name: str
    mrz_code_aliases: Set[str] = field(default_factory=set)
    supported_identity_documents: Set[str] = field(default_factory=set)
    passport_personal_number_label: str = "personal_number"

    def matches_mrz_country(self, issuing_country: str, nationality: str = "") -> bool:
        """Return True when OCR output appears to belong to this country.

        MRZ country codes can be misread by OCR. For example, `NGA` can look like
        `N6A` or `NG4`. The alias list lets a profile correct those common cases.
        """
        issuing_country = (issuing_country or "").upper()
        nationality = (nationality or "").upper()
        return issuing_country in self.mrz_code_aliases or nationality == self.code


NIGERIA_PROFILE = CountryProfile(
    code="NGA",
    name="Nigeria",
    mrz_code_aliases={"NGA", "NGE", "NG4", "N6A", "N64", "NGR"},
    supported_identity_documents={"NIN_CARD", "NIN_SLIP"},
    passport_personal_number_label="nin",
)

COUNTRY_PROFILES: Dict[str, CountryProfile] = {
    NIGERIA_PROFILE.code: NIGERIA_PROFILE,
}


def get_country_profile(country_code: Optional[str]) -> Optional[CountryProfile]:
    """Look up a country profile by ISO-3166 alpha-3 code."""
    if not country_code:
        return None
    return COUNTRY_PROFILES.get(country_code.upper())


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


def validate_nigerian_nin(value: Optional[str]) -> bool:
    """Validate the basic Nigerian NIN shape.

    This only checks the public format: exactly 11 digits. It does not verify the
    number against any government database.
    """
    return bool(re.fullmatch(r"\d{11}", value or ""))


def country_validation_summary(
    *,
    country_code: str,
    document_type: str,
    extracted_data: Dict[str, Optional[str]],
) -> Dict[str, object]:
    """Return country-specific validation details for an OCR response.

    The result is intentionally simple JSON so API clients can display or store
    it without understanding Python classes.
    """
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
        "checks": checks,
    }
