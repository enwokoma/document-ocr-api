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


def test_numbered_memorandum_objects_heading_is_supported():
    text = """3. The objects for which the Company is established are:
(a) To provide software services
(b) To carry on business consulting
4. The nominal share capital of the Company is ₦1,000,000
"""

    objects, _ = extract_business_objects(text)

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


def test_numbered_nominal_capital_accepts_real_naira_symbol():
    capital, _ = extract_share_capital(
        "4. The nominal share capital of the Company is ₦1,000,000 divided into 1,000,000 ordinary shares of ₦1 each",
        country_code="NGA",
    )

    assert capital["authorized_amount"] == "1000000"
    assert capital["amount"] == "1000000"
    assert capital["currency"] == "NGN"
    assert capital["share_count"] == "1000000"
    assert capital["nominal_value_per_share"] == "1"


def test_status_report_capital_uses_selected_country_currency_when_symbol_is_absent():
    capital, _ = extract_share_capital("Total Share Capital 1,000,000", country_code="NGA")

    assert capital["amount"] == "1000000"
    assert capital["currency"] == "NGN"


def test_articles_directors_prose_is_not_parsed_as_people():
    text = """SUBSCRIBERS
FULL NAME    SHARES
Alex Example    10 SHARES
ARTICLES OF ASSOCIATION
DIRECTORS
Directors' General Authority
Subject to the articles, the directors are responsible for management of the company.
DIRECTORS
Directors to Take Decisions Collectively
The directors may make decisions at a properly convened meeting.
DIRECTORS
Corporate Affairs Commission
Model articles for private companies limited by shares
"""

    parties, _ = extract_parties(text)

    assert parties == [{"name": "Alex Example", "roles": ["SUBSCRIBER"], "shares": "10"}]


def test_cac_role_type_records_extract_named_company_parties_safely():
    text = """DIRECTOR'SDETAILS
1. ROLE TYPE DIRECTOR
SURNAME Example
FIRSTNAME Ada
OTHERNAME NIL
STATUS ACTIVE
EMAIL ada@example.invalid
IDENTIFICATION NUMBER 123456789
2.F ROLE TYPE DIRECTOR
SURNAME Sample
FIRSTNAME Ben
OTHERNAME Chidi
STATUS ACTIVE
SECRETARY'SDETAILS
3. ROLE TYPE SECRETARYCOMPANY
COMPANY NAME Example Secretarial Services Limited
STATUS ACTIVE
SHAREHOLDERS
4. ROLE TYPE SHAREHOLDER
SURNAME Holder
FIRSTNAME Chi
TOTALNUMBEROFSHARES 250
SHAREPERCENTAGE 25%
PERSONSWITHSIGNIFICANTCONTROL
5. ROLE TYPE PERSONWITHSIGNIFICANTCONTROL
SURNAME Control
FIRSTNAME Dele
PHONE 08000000000
"""

    parties, _ = extract_parties(text)

    assert parties == [
        {"name": "Example Ada", "roles": ["DIRECTOR"], "status": "ACTIVE"},
        {"name": "Sample Ben Chidi", "roles": ["DIRECTOR"], "status": "ACTIVE"},
        {
            "name": "Example Secretarial Services Limited",
            "roles": ["SECRETARY"],
            "status": "ACTIVE",
        },
        {
            "name": "Holder Chi",
            "roles": ["SHAREHOLDER"],
            "shares": "250",
            "share_percentage": "25",
        },
        {"name": "Control Dele", "roles": ["PERSON_WITH_SIGNIFICANT_CONTROL"]},
    ]
    assert all("email" not in party and "phone" not in party and "identifiers" not in party for party in parties)


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
