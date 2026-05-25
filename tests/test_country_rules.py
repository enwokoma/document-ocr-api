"""Tests for country-specific document rules.

These tests do not run OCR. They verify the reusable rules that processors call
after OCR has already produced text or structured fields.
"""

from src.countries.registry import (
    country_validation_summary,
    get_country_profile,
    list_country_profiles,
    normalize_mrz_country,
    serialize_country_profile,
)
from src.countries.nigeria.rules import validate_nin


def test_nigeria_profile_is_registered():
    """Nigeria should be available as the first supported country profile."""
    profile = get_country_profile("NGA")

    assert profile is not None
    assert profile.name == "Nigeria"
    assert "NIN_SLIP" in profile.supported_identity_documents
    assert "VOTER_CARD" in profile.supported_identity_documents
    assert "DRIVERS_LICENSE" in profile.supported_identity_documents


def test_ghana_profile_is_registered_with_default_ids():
    """Ghana should demonstrate how a second country can register local IDs."""
    profile = get_country_profile("GHA")

    assert profile is not None
    assert profile.name == "Ghana"
    assert "GHANA_CARD" in profile.supported_identity_documents
    assert "VOTER_ID" in profile.supported_identity_documents


def test_mrz_country_aliases_normalize_to_nigeria():
    """Known OCR mistakes for `NGA` should normalize to the canonical code."""
    assert normalize_mrz_country("N6A", "NGA") == "NGA"
    assert normalize_mrz_country("NG4", "") == "NGA"


def test_mrz_country_aliases_normalize_to_ghana():
    """Known OCR mistakes for `GHA` should normalize to the canonical code."""
    assert normalize_mrz_country("6HA", "GHA") == "GHA"


def test_unknown_country_passes_through_when_no_profile_matches():
    """Unknown countries should not be rewritten by the registry."""
    assert normalize_mrz_country("USA", "USA") == "USA"


def test_nigerian_nin_format_validation():
    """Nigerian NIN validation should accept exactly 11 digits."""
    assert validate_nin("12345678901") is True
    assert validate_nin("12345") is False
    assert validate_nin("1234567890A") is False


def test_country_validation_summary_reports_document_and_nin_checks():
    """Country validation summaries should expose document and NIN checks."""
    summary = country_validation_summary(
        country_code="NGA",
        document_type="NIN_SLIP",
        extracted_data={"nin": "12345678901"},
    )

    assert summary["supported"] is True
    assert summary["checks"]["document_type_supported"] is True
    assert summary["checks"]["nin_format_valid"] is True


def test_country_profiles_can_be_serialized_for_api_discovery():
    """The registry should expose profiles in a JSON-friendly shape."""
    profiles = list_country_profiles()
    nigeria = serialize_country_profile(profiles["NGA"])

    assert nigeria["country_code"] == "NGA"
    assert any(doc["code"] == "VOTER_CARD" for doc in nigeria["supported_identity_documents"])
