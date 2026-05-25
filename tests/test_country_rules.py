"""Tests for country-specific document rules.

These tests do not run OCR. They verify the reusable rules that processors call
after OCR has already produced text or structured fields.
"""

from src.document_ocr.country_rules import (
    country_validation_summary,
    get_country_profile,
    normalize_mrz_country,
    validate_nigerian_nin,
)


def test_nigeria_profile_is_registered():
    profile = get_country_profile("NGA")

    assert profile is not None
    assert profile.name == "Nigeria"
    assert "NIN_SLIP" in profile.supported_identity_documents


def test_mrz_country_aliases_normalize_to_nigeria():
    assert normalize_mrz_country("N6A", "NGA") == "NGA"
    assert normalize_mrz_country("NG4", "") == "NGA"


def test_unknown_country_passes_through_when_no_profile_matches():
    assert normalize_mrz_country("USA", "USA") == "USA"


def test_nigerian_nin_format_validation():
    assert validate_nigerian_nin("12345678901") is True
    assert validate_nigerian_nin("12345") is False
    assert validate_nigerian_nin("1234567890A") is False


def test_country_validation_summary_reports_document_and_nin_checks():
    summary = country_validation_summary(
        country_code="NGA",
        document_type="NIN_SLIP",
        extracted_data={"nin": "12345678901"},
    )

    assert summary["supported"] is True
    assert summary["checks"]["document_type_supported"] is True
    assert summary["checks"]["nin_format_valid"] is True
