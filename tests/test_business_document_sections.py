"""Focused regressions for structured business-document section parsing."""

from src.document_ocr.business_document.normalization import normalize_business_text
from src.document_ocr.business_document.sections import (
    extract_business_objects,
    extract_parties,
    extract_share_capital,
)


def test_party_rows_keep_metadata_atomic():
    text = """DIRECTORS
Alex Example    ACTIVE    100 SHARES
Morgan Sample    INACTIVE    50 SHARES
"""

    parties, _ = extract_parties(text)

    assert parties == [
        {
            "name": "Alex Example",
            "roles": ["DIRECTOR"],
            "status": "ACTIVE",
            "shares": "100",
        },
        {
            "name": "Morgan Sample",
            "roles": ["DIRECTOR"],
            "status": "INACTIVE",
            "shares": "50",
        },
    ]


def test_party_sections_reject_address_and_country_lines():
    text = """DIRECTORS
Alex Example
Victoria Street Central
United Kingdom
Morgan Sample
"""

    parties, _ = extract_parties(text)

    assert [party["name"] for party in parties] == ["Alex Example", "Morgan Sample"]


def test_repeated_role_sections_are_all_parsed():
    text = """DIRECTORS
Alex Example
SHARE CAPITAL
DIRECTORS
Morgan Sample
GENERAL DETAILS
"""

    parties, _ = extract_parties(text)

    assert [party["name"] for party in parties] == ["Alex Example", "Morgan Sample"]


def test_objects_requires_a_real_heading_and_stops_at_next_section():
    false_heading = """OBJECTS LIMITED
Company Number: 123456
Registered Office: 1 Example Road
"""
    real_section = """OBJECTS:
1. To provide software services
2. To carry on business consulting
REGISTERED OFFICE
1 Example Road
"""

    false_objects, _ = extract_business_objects(false_heading)
    objects, _ = extract_business_objects(real_section)

    assert false_objects == []
    assert objects == ["To provide software services", "To carry on business consulting"]


def test_small_capital_and_one_share_are_supported():
    capital, _ = extract_share_capital("Share capital USD 100 divided into 1 share of USD 100 each")

    assert capital["stated_amount"] == "100"
    assert capital["amount"] == "100"
    assert capital["currency"] == "USD"
    assert capital["share_count"] == "1"
    assert capital["nominal_value_per_share"] == "100"


def test_authorized_issued_and_paid_up_capital_remain_distinct():
    capital, _ = extract_share_capital(
        "Authorized capital USD 1000. Issued share capital USD 600. Paid-up share capital USD 400."
    )

    assert capital["authorized_amount"] == "1000"
    assert capital["issued_amount"] == "600"
    assert capital["paid_up_amount"] == "400"
    assert capital["amount"] == "600"


def test_multiple_share_classes_are_preserved():
    capital, _ = extract_share_capital(
        "Authorized share capital USD 1000 divided into "
        "500 ordinary shares of USD 1 each and "
        "500 preference shares of USD 1 each"
    )

    assert capital["share_count"] == "1000"
    assert capital["share_classes"] == [
        {
            "share_count": "500",
            "share_class": "ORDINARY",
            "nominal_value_per_share": "1",
            "currency": "USD",
        },
        {
            "share_count": "500",
            "share_class": "PREFERENCE",
            "nominal_value_per_share": "1",
            "currency": "USD",
        },
    ]


def test_bare_dollar_requires_country_context():
    text = "Share capital $100 divided into 1 share of $100 each"

    ambiguous, _ = extract_share_capital(text)
    us_capital, _ = extract_share_capital(text, country_code="USA")
    canadian_capital, _ = extract_share_capital(text, country_code="CAN")

    assert "currency" not in ambiguous
    assert ambiguous["currency_raw"] == "$"
    assert ambiguous["share_classes"][0]["currency_raw"] == "$"
    assert us_capital["currency"] == "USD"
    assert canadian_capital["currency"] == "CAD"


def test_joined_labels_do_not_rewrite_title_case_legal_names():
    normalized = normalize_business_text(
        "CompanyName Holdings Limited\nCOMPANYNAME: EXAMPLE HOLDINGS LIMITED\nHEADOFFICEADDRESS: 1 Example Road"
    )

    assert normalized.splitlines() == [
        "CompanyName Holdings Limited",
        "COMPANY NAME EXAMPLE HOLDINGS LIMITED",
        "HEAD OFFICE ADDRESS 1 Example Road",
    ]
