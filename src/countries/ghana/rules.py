"""Ghana-specific document rules.

This is intentionally metadata-first. It shows how to introduce a new country
and list its local ID types before building dedicated OCR parsers for those IDs.
"""

from __future__ import annotations

from src.countries.profile import CountryProfile


GHANA_PROFILE = CountryProfile(
    code="GHA",
    name="Ghana",
    mrz_code_aliases={"GHA", "GH4", "6HA"},
    supported_identity_documents={
        "GHANA_CARD": "Ghana national identity card",
        "VOTER_ID": "Voter identity card",
        "DRIVERS_LICENSE": "Driver's license",
        "TAX_IDENTIFICATION_NUMBER": "Tax Identification Number",
        "SSNIT_NUMBER": "Social Security and National Insurance Trust number",
    },
    passport_personal_number_label="personal_number",
)
