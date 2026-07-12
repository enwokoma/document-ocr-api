"""Focused tests for business-document taxonomy, models, and profiles.

These tests use synthetic OCR text only.  OCR backends, uploads, and external
services are intentionally outside their scope.
"""

from pathlib import Path

import pytest

from src.document_ocr.business_document.classification import classify_business_document
from src.document_ocr.business_document.config import get_business_document_settings
from src.document_ocr.business_document.identifiers import (
    IdentifierType,
    RegistrationPattern,
    extract_business_identifiers,
)
from src.document_ocr.business_document.jurisdictions import (
    AuthorityMarker,
    BusinessJurisdictionProfile,
    detect_business_jurisdiction,
    detect_business_subdivision,
    get_business_jurisdiction,
    register_business_jurisdiction,
    unregister_business_jurisdiction,
)
from src.document_ocr.business_document.language import detect_document_language
from src.document_ocr.business_document.schema import (
    BUSINESS_DOCUMENT_TYPES,
    CANONICAL_BUSINESS_DATA_KEYS,
    CANONICAL_IDENTIFIER_KEYS,
    UNKNOWN_BUSINESS_DOCUMENT,
    canonical_business_data,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "business_document"

CLASSIFICATION_SAMPLES = {
    "ARTICLES_OF_INCORPORATION": ("ARTICLES OF INCORPORATION OF SAMPLE COMPANY. INCORPORATOR. DIVISION OF CORPORATIONS."),
    "ARTICLES_OF_ORGANIZATION": ("ARTICLES OF ORGANIZATION OF SAMPLE VENTURES LLC. LIMITED LIABILITY COMPANY. ORGANIZER."),
    "TAX_REGISTRATION_CERTIFICATE": (
        "TAX REGISTRATION CERTIFICATE FOR SAMPLE COMPANY. TAX IDENTIFICATION NUMBER. REVENUE SERVICE."
    ),
    "BUSINESS_REGISTRATION_CERTIFICATE": (
        "BUSINESS REGISTRATION CERTIFICATE FOR SAMPLE TRADERS. BUSINESS REGISTRY. TRADING NAME."
    ),
    "CERTIFICATE_OF_FORMATION": ("CERTIFICATE OF FORMATION OF SAMPLE COMPANY. DATE OF FORMATION. SECRETARY OF STATE."),
    "CERTIFICATE_OF_GOOD_STANDING": ("CERTIFICATE OF GOOD STANDING FOR SAMPLE COMPANY. IN GOOD STANDING. SECRETARY OF STATE."),
    "CAC_CERTIFICATE": ("CAC CERTIFICATE FOR SAMPLE COMPANY. CORPORATE AFFAIRS COMMISSION. CAMA. RC 1234567."),
    "CERTIFICATE_OF_CHANGE_OF_NAME": (
        "CERTIFICATE OF CHANGE OF NAME FOR SAMPLE COMPANY. FORMERLY KNOWN AS OLD SAMPLE LIMITED. REGISTRAR."
    ),
    "CERTIFICATE_OF_INCORPORATION": (
        "CERTIFICATE OF INCORPORATION OF SAMPLE COMPANY. THIS IS TO CERTIFY. DATE OF INCORPORATION. REGISTRAR."
    ),
    "CERTIFICATE_OF_REGISTRATION": (
        "CERTIFICATE OF REGISTRATION OF SAMPLE BUSINESS. IS HEREBY REGISTERED. REGISTRATION NUMBER. REGISTRAR."
    ),
    "COMPANY_STATUS_REPORT": ("COMPANY STATUS REPORT. COMPANY DETAILS. COMPANY STATUS ACTIVE. REGISTERED ADDRESS. DIRECTORS."),
    "MEMORANDUM_AND_ARTICLES_OF_ASSOCIATION": (
        "MEMORANDUM AND ARTICLES OF ASSOCIATION OF SAMPLE COMPANY. OBJECTS OF THE COMPANY. SUBSCRIBERS HERETO."
    ),
    "MEMORANDUM_OF_ASSOCIATION": ("MEMORANDUM OF ASSOCIATION OF SAMPLE COMPANY. OBJECTS OF THE COMPANY. SUBSCRIBERS HERETO."),
    "ARTICLES_OF_ASSOCIATION": ("ARTICLES OF ASSOCIATION OF SAMPLE COMPANY. MODEL ARTICLES. POWERS OF DIRECTORS."),
    "COMPANY_REGISTRY_EXTRACT": ("COMPANY REGISTRY EXTRACT FOR SAMPLE COMPANY. REGISTERED PARTICULARS."),
    "CERTIFIED_REGISTRY_EXTRACT": ("CERTIFIED EXTRACT. REGISTRY EXTRACT FOR SAMPLE COMPANY. EXTRACTED FROM THE REGISTER."),
}


def _fixture_text(name: str) -> str:
    return (FIXTURE_ROOT / name).read_text(encoding="utf-8")


def test_business_document_taxonomy_covers_every_requested_family():
    """The public taxonomy should cover all implemented global document families."""
    assert set(BUSINESS_DOCUMENT_TYPES) == {
        *CLASSIFICATION_SAMPLES,
        UNKNOWN_BUSINESS_DOCUMENT,
    }


@pytest.mark.parametrize(("expected_type", "text"), CLASSIFICATION_SAMPLES.items())
def test_each_business_document_type_has_a_working_classifier_signature(expected_type, text):
    """Every advertised non-fallback taxonomy entry should be classifiable."""
    result = classify_business_document(text)

    assert result.document_type == expected_type
    assert result.confidence >= 0.65
    assert result.matched_terms
    assert result.ambiguous is False


def test_unknown_business_text_uses_the_explicit_fallback_type():
    result = classify_business_document("Synthetic ledger row 42 with no registry heading")

    assert result.document_type == UNKNOWN_BUSINESS_DOCUMENT
    assert result.confidence < 0.40


def test_canonical_schema_preserves_multiple_typed_identifiers_and_all_keys():
    raw = {
        "company_name": "  Sample Global Services Limited ",
        "registered_address": "10 Example Road, Sample City",
        "identifiers": [
            {
                "type": "COMPANY_REGISTRATION_NUMBER",
                "number_type": "CAC_RC",
                "value": "RC 1234567",
                "normalized_value": "RC1234567",
                "country_code": "NGA",
                "confidence": 0.97,
                "evidence": [{"text": "RC 1234567", "page": 1}],
            },
            {
                "type": "TAX_IDENTIFIER",
                "number_type": "NIGERIAN_TIN",
                "value": "12345678-0001",
                "confidence": 0.94,
            },
            {
                "identifier_type": "EMPLOYER_IDENTIFIER",
                "number_type": "EIN",
                "value": "12-3456789",
                "confidence": 0.98,
            },
        ],
        "additional_fields": [
            {
                "label": "Local Filing Category",
                "value": "MERCHANT-A",
                "confidence": 0.72,
                "evidence": {"text": "Local Filing Category: MERCHANT-A"},
            }
        ],
    }

    data = canonical_business_data(raw)

    assert tuple(data) == CANONICAL_BUSINESS_DATA_KEYS
    assert data["legal_company_name"] == "Sample Global Services Limited"
    assert data["registered_office_address"] == "10 Example Road, Sample City"
    assert [item["type"] for item in data["identifiers"]] == [
        "COMPANY_REGISTRATION_NUMBER",
        "TAX_IDENTIFIER",
        "EMPLOYER_IDENTIFIER",
    ]
    assert all(tuple(item) == CANONICAL_IDENTIFIER_KEYS for item in data["identifiers"])
    assert data["identifiers"][0]["evidence"][0]["page"] == 1
    assert data["additional_fields"][0]["value"] == "MERCHANT-A"


@pytest.mark.parametrize(
    ("text", "expected_code"),
    (
        (
            "CERTIFICATE OF INCORPORATION. THIS IS TO CERTIFY. REGISTERED OFFICE. COMPANY NAME SAMPLE LIMITED.",
            "en",
        ),
        (
            "CERTIFICADO DE REGISTRO. SOCIEDAD SAMPLE. DOMICILIO SOCIAL. REGISTRO MERCANTIL.",
            "es",
        ),
        ("Synthetic reference 12345 without linguistic registry wording", None),
    ),
)
def test_document_language_detection_is_explainable_and_conservative(text, expected_code):
    result = detect_document_language(text)

    assert result.code == expected_code
    if expected_code is None:
        assert result.confidence < 0.40
        assert result.name is None
    else:
        assert result.confidence >= 0.65
        assert result.name
        assert result.matched_terms


def test_nigeria_profile_keeps_full_cac_tin_and_document_reference_values():
    text = _fixture_text("nigeria_cac_certificate.txt") + "\nRegistry Identifier: ACTIVE\n"
    jurisdiction = detect_business_jurisdiction(text)
    result = extract_business_identifiers(text, jurisdiction=jurisdiction)
    by_type = {item.identifier_type: item for item in result.identifiers}

    assert jurisdiction.country_code == "NGA"
    assert by_type["COMPANY_REGISTRATION_NUMBER"].value == "RC 1234567"
    assert by_type["COMPANY_REGISTRATION_NUMBER"].normalized_value == "RC1234567"
    assert by_type["COMPANY_REGISTRATION_NUMBER"].number_type == "CAC_RC"
    assert by_type["TAX_IDENTIFIER"].value == "12345678-0001"
    assert by_type["TAX_IDENTIFIER"].normalized_value == "12345678-0001"
    assert by_type["DOCUMENT_REFERENCE_NUMBER"].normalized_value == "CAC/2020/ABC123"
    assert all(item.normalized_value != "ACTIVE" for item in result.identifiers)
    assert all(item.confidence >= 0.80 and item.evidence for item in result.identifiers)


def test_us_profile_extracts_ein_and_state_formation_identifiers():
    text = _fixture_text("us_delaware_articles.txt")
    jurisdiction = detect_business_jurisdiction(text)
    subdivision = detect_business_subdivision(text, jurisdiction.country_code)
    result = extract_business_identifiers(text, jurisdiction=jurisdiction)
    identifiers = {(item.identifier_type, item.number_type): item for item in result.identifiers}

    assert jurisdiction.country_code == "USA"
    assert subdivision is not None
    assert subdivision.code == "US-DE"
    assert subdivision.name == "Delaware"
    assert subdivision.registry_name == "Delaware Division of Corporations"
    assert identifiers[("EMPLOYER_IDENTIFIER", "EIN")].normalized_value == "12-3456789"
    state_id = identifiers[("STATE_FORMATION_IDENTIFIER", "US_STATE_ENTITY_NUMBER")]
    assert state_id.normalized_value == "7654321"
    assert state_id.jurisdiction == "Delaware"
    assert state_id.issuing_authority == "Delaware Division of Corporations"


def test_generic_profile_retains_registry_identifier_without_guessing_country():
    text = _fixture_text("unknown_registry_document.txt")
    jurisdiction = detect_business_jurisdiction(text)
    result = extract_business_identifiers(text, jurisdiction=jurisdiction)

    assert jurisdiction.country_code is None
    assert len(result.identifiers) == 1
    identifier = result.identifiers[0]
    assert identifier.identifier_type == "REGISTRY_NUMBER"
    assert identifier.normalized_value == "XY-009988"
    assert identifier.country_code is None
    assert identifier.source == "generic_fallback"
    assert any("generic identifier patterns" in warning for warning in result.warnings)


def test_generic_identifier_extraction_rejects_status_words_and_requires_a_digit():
    result = extract_business_identifiers(
        "Registry Identifier: ACTIVE\nRegistry Identifier: PENDING\nRegistry Identifier: ABCD"
    )

    assert result.identifiers == ()
    assert any("No reliable" in warning for warning in result.warnings)


def test_country_hint_conflict_prefers_strong_registry_evidence():
    text = _fixture_text("nigeria_cac_certificate.txt")
    result = detect_business_jurisdiction(text, country_hint="USA")

    assert result.country_code == "NGA"
    assert result.requested_country_code == "USA"
    assert result.detected_country_code == "NGA"
    assert result.conflict is True
    assert result.source == "document_text_conflict"
    assert result.confidence >= 0.85


def test_runtime_profile_registration_is_detectable_and_reversible():
    profile = BusinessJurisdictionProfile(
        code="XTS",
        name="Synthetic Test State",
        registry_name="Synthetic Company Registry",
        aliases=("XT",),
        authority_markers=(AuthorityMarker("Synthetic Company Registry", r"\bSYNTHETIC\s+COMPANY\s+REGISTRY\b", 6.0),),
        registration_patterns=(
            RegistrationPattern(
                r"\b(?P<number>XTS-\d{5})\b",
                "XTS_REGISTRY_NUMBER",
                0.95,
                IdentifierType.REGISTRY_NUMBER,
            ),
        ),
        high_score=6.0,
        minimum_score=3.0,
    )

    register_business_jurisdiction(profile)
    try:
        assert get_business_jurisdiction("XT") is profile
        text = "SYNTHETIC COMPANY REGISTRY\nRegistry Certificate\nXTS-12345"
        jurisdiction = detect_business_jurisdiction(text)
        extracted = extract_business_identifiers(text, jurisdiction=jurisdiction)

        assert jurisdiction.country_code == "XTS"
        assert extracted.identifiers[0].identifier_type == "REGISTRY_NUMBER"
        assert extracted.identifiers[0].normalized_value == "XTS-12345"
    finally:
        removed = unregister_business_jurisdiction("XTS")

    assert removed is profile
    assert get_business_jurisdiction("XTS") is None


def test_conflicting_identifier_candidates_are_retained_for_review():
    result = extract_business_identifiers("Company Number: SAMPLE-1001\nCompany Number: SAMPLE-2002")

    assert {item.normalized_value for item in result.identifiers} == {
        "SAMPLE-1001",
        "SAMPLE-2002",
    }
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.identifier_type == "COMPANY_REGISTRATION_NUMBER"
    assert set(conflict.candidate_values) == {"SAMPLE-1001", "SAMPLE-2002"}
    assert any("Conflicting values" in warning for warning in result.warnings)


def test_business_document_text_fixtures_are_obviously_synthetic_and_sanitized():
    files = sorted(FIXTURE_ROOT.glob("*.txt"))

    assert {item.name for item in files} == {
        "memart_status_report.txt",
        "nigeria_cac_certificate.txt",
        "unknown_registry_document.txt",
        "us_delaware_articles.txt",
    }
    for fixture in files:
        content = fixture.read_text(encoding="utf-8")
        assert "SAMPLE" in content.upper() or "EXAMPLE" in content.upper()
        assert "@" not in content
        assert "HTTP://" not in content.upper()
        assert "HTTPS://" not in content.upper()
        assert "BEGIN PRIVATE KEY" not in content.upper()


def test_business_document_settings_clamp_resource_limits(monkeypatch):
    monkeypatch.setenv("BUSINESS_DOCUMENT_MAX_PAGES", "999")
    monkeypatch.setenv("BUSINESS_DOCUMENT_MAX_UPLOAD_BYTES", "invalid")
    monkeypatch.setenv("BUSINESS_DOCUMENT_MAX_IMAGE_PIXELS", "1")
    monkeypatch.setenv("BUSINESS_DOCUMENT_MAX_PAGE_TEXT_CHARS", "999999")
    monkeypatch.setenv("BUSINESS_DOCUMENT_COMPARE_RENDERED_PDF_TEXT", "off")

    settings = get_business_document_settings()

    assert settings.max_pages == 100
    assert settings.max_upload_bytes == 20 * 1024 * 1024
    assert settings.max_image_pixels == 1_000_000
    assert settings.max_page_text_chars == 500_000
    assert settings.compare_rendered_pdf_text is False
