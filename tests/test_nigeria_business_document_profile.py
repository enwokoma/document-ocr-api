"""Focused regression tests for Nigerian CAC document layouts."""

from src.document_ocr.business_document.classification import classify_business_document
from src.document_ocr.business_document.fields import parse_core_business_fields
from src.document_ocr.business_document.generic import parse_generic_business_fields
from src.document_ocr.business_document.identifiers import extract_business_identifiers
from src.document_ocr.business_document.jurisdictions import detect_business_jurisdiction
from src.document_ocr.business_document.language import detect_document_language
from src.document_ocr.business_document.normalization import normalize_business_text


def test_cac_company_registration_number_has_company_identifier_type():
    text = """FEDERAL REPUBLIC OF NIGERIA
    CORPORATE AFFAIRS COMMISSION
    CERTIFICATE OF INCORPORATION
    COMPANY REGISTRATION NO. 7654321
    TAX IDENTIFICATION NUMBER: 12345678-0001
    """

    result = extract_business_identifiers(
        text,
        jurisdiction=detect_business_jurisdiction(text),
    )
    company_number = next(item for item in result.identifiers if item.normalized_value == "7654321")

    assert company_number.identifier_type == "COMPANY_REGISTRATION_NUMBER"
    assert company_number.number_type == "CAC_COMPANY_REGISTRATION_NUMBER"
    assert not any("matched multiple types" in warning for warning in result.warnings)


def test_cac_certificate_certifies_that_phrase_selects_legal_company_name():
    text = """FEDERAL REPUBLIC OF NIGERIA
    CERTIFICATE OF INCORPORATION
    OF A
    PRIVATE COMPANY LIMITED BY SHARES
    The Registrar - General of Corporate Affairs Commission
    hereby certifies that
    ALPHA PLATFORM NIGERIA LTD
    is this day incorporated under the
    COMPANIES AND ALLIED MATTERS ACT 2020
    """

    result = parse_core_business_fields(
        text,
        jurisdiction=detect_business_jurisdiction(text),
        document_type="CERTIFICATE_OF_INCORPORATION",
    )

    assert result.data["company_name"] == "ALPHA PLATFORM NIGERIA LTD"
    assert next(item for item in result.evidence if item.field == "company_name").method == "certificate_or_title_phrase"


def test_company_name_fallback_rejects_legal_form_and_narrative_lines():
    text = """CERTIFICATE OF INCORPORATION
    COMPANY LIMITED BY SHARES
    BETA SERVICES NIGERIA LTD
    is this day incorporated under the governing law
    """

    result = parse_core_business_fields(
        text,
        jurisdiction=detect_business_jurisdiction(text, "NGA"),
        document_type="CERTIFICATE_OF_INCORPORATION",
    )

    assert result.data["company_name"] == "BETA SERVICES NIGERIA LTD"


def test_separate_memorandum_and_articles_headings_classify_as_combined_document():
    text = """FEDERAL REPUBLIC OF NIGERIA
    COMPANIES AND ALLIED MATTERS ACT, 2020
    MEMORANDUM OF ASSOCIATION
    OF
    EXAMPLE NIGERIA LTD
    ARTICLES OF ASSOCIATION
    OF
    EXAMPLE NIGERIA LTD
    """

    result = classify_business_document(text)

    assert result.document_type == "MEMORANDUM_AND_ARTICLES_OF_ASSOCIATION"
    assert result.confidence >= 0.9
    assert "memorandum and articles headings" in result.matched_terms
    assert result.ambiguous is False
    assert not {
        "MEMORANDUM_OF_ASSOCIATION",
        "ARTICLES_OF_ASSOCIATION",
    }.intersection(item["document_type"] for item in result.alternatives)


def test_joined_status_report_values_keep_labelled_company_type_and_head_office():
    text = """COMPANY STATUS REPORT
    CORPORATE AFFAIRS COMMISSION
    Company Name EXAMPLE NIGERIA LTD
    Company Type PRIVATECOMPANYLIMITEDBYSHARES
    HeadOfficeAddress 12 Example Road, Abuja
    PERSONS WITH SIGNIFICANT CONTROL
    Is the company a public company limited by shares? No
    """

    result = parse_core_business_fields(
        text,
        jurisdiction=detect_business_jurisdiction(text, "NGA"),
        document_type="COMPANY_STATUS_REPORT",
    )

    assert result.data["entity_type"] == "PRIVATE_COMPANY_LIMITED_BY_SHARES"
    assert result.data["head_office_address"] == "12 Example Road, Abuja"
    assert "HEAD OFFICE ADDRESS 12 Example Road, Abuja" in normalize_business_text(text)


def test_unqualified_personal_contacts_after_party_heading_are_not_company_contacts():
    text = """COMPANY STATUS REPORT
    Company Name EXAMPLE NIGERIA LTD
    Company Email office@example.test
    DIRECTORS
    Email Address director@example.test
    Phone Number 08012345678
    """

    result = parse_core_business_fields(
        text,
        jurisdiction=detect_business_jurisdiction(text, "NGA"),
        document_type="COMPANY_STATUS_REPORT",
    )

    assert result.data["contact_email"] == "office@example.test"
    assert "contact_phone" not in result.data


def test_cac_status_report_markers_identify_english_language():
    result = detect_document_language("STATUS REPORT\nDIRECTOR'SDETAILS\nROLE TYPE DIRECTOR\nPERSONSWITHSIGNIFICANTCONTROL")

    assert result.code == "en"
    assert result.confidence >= 0.6


def test_generic_additional_fields_drop_layout_and_personal_contact_noise():
    text = """PERSONS WITH SIGNIFICANT CONTROL
    Is the company a public company limited by shares? No
    Email Address: person@example.test
    SIGNATURE: John Example
    Industry Classification: Education Services
    """

    result = parse_generic_business_fields(text, country_code="NGA")
    additional = {item["label"]: item["value"] for item in result.data["additional_fields"]}

    assert additional == {"Industry Classification": "Education Services"}


def test_cac_certificate_layout_fragments_are_not_additional_fields():
    text = """OF A
    PRIVATE COMPANY LIMITED BY SHARES
    The Registrar - General of Corporate Affairs Commission
    A. G. Example
    Registrar - General
    Local Filing Category: COMPANY-A
    """

    result = parse_generic_business_fields(text, country_code="NGA")
    additional = {item["label"]: item["value"] for item in result.data["additional_fields"]}

    assert additional == {"Local Filing Category": "COMPANY-A"}
