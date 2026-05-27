"""Tests for bank statement field extraction."""

from src.document_ocr.bank_statement.processor import _extract_bank_statement_fields


def test_grey_statement_parser_extracts_customer_address_and_header_fields():
    """Grey statements should expose customer address and summary fields."""
    text = """
    Statement of Account
    Grey Inc. Sample Customer
    Provider Address, Suite 000 12, Sample Avenue, by Sample Landmark
    Middletown, DE 19700 USA. Sample District
    SAMPLE CITY
    TIME PERIOD: ACCOUNT NUMBER BANK NAME
    01/08/2025 → 18/11/2025 0001112223 Wema Bank
    CURRENCY
    NGN
    OPENING BALANCE
    ₦980,460.32
    Balance as at November 18, 2025 ₦38.60
    """

    data = _extract_bank_statement_fields(text)

    assert data["account_number"] == "0001112223"
    assert data["bank_name"] == "Wema Bank"
    assert data["address"] == "12, Sample Avenue, by Sample Landmark Sample District SAMPLE CITY"
    assert data["start_date"] == "01/08/2025"
    assert data["end_date"] == "18/11/2025"
    assert data["closing_balance"] == "38.60"


def test_uba_statement_parser_extracts_compact_address_and_period():
    """UBA statements should parse compact header dates and address."""
    text = """
    BankStatement
    SAMPLECUSTOMERHOLDER
    40SAMPLESTREETLAGOS
    27-Mar-2025to27-Mar-2026
    Hello SAMPLE CUSTOMER HOLDER,
    Here is your summary of account
    Account Number 0001112223
    Account Type: SAVINGS
    Opening Balance: 313.70
    Currency: NGN
    Closing Balance: 403.61
    """

    data = _extract_bank_statement_fields(text)

    assert data["account_number"] == "0001112223"
    assert data["account_name"] == "SAMPLE CUSTOMER HOLDER"
    assert data["address"] == "40 SAMPLE STREET LAGOS"
    assert data["start_date"] == "27-Mar-2025"
    assert data["end_date"] == "27-Mar-2026"
    assert data["closing_balance"] == "403.61"


def test_uba_statement_parser_prefers_spaced_greeting_name():
    """A readable greeting name should win over a compact header name."""
    text = """
    BankStatement
    FIRSTNAMEMIDDLENAMELASTNAME
    40DISTRICTSTRSAMPLECITY
    27-Mar-2025to27-Mar-2026
    Account Name FIRSTNAMEMIDDLENAMELASTNAME
    Hello FIRSTNAME MIDDLENAME LASTNAME,
    Account Number 0001112223
    Opening Balance: 313.70
    Closing Balance: 403.61
    """

    data = _extract_bank_statement_fields(text)

    assert data["account_name"] == "FIRSTNAME MIDDLENAME LASTNAME"
    assert data["address"] == "40 DISTRICT STR SAMPLE CITY"
