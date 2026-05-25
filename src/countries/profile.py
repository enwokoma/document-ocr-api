"""Shared country-profile data shape.

Country packages define their own profiles with this class. The registry in
`registry.py` imports those profiles and makes them available to the generic
document processors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Set


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
