"""Tests for utility bill and utility receipt field extraction."""

from datetime import date

from src.document_ocr.utility_bill.processor import parse_utility_bill_text


def test_opay_utility_receipt_extracts_address_date_and_age():
    """OPay-style receipts should expose proof-of-address fields."""
    text = """
    OPay Transaction Receipt
    Successful
    Feb 14th, 2026 19:05:49
    Provider Ikeja Electricity
    Meter Number 0001112223
    Customer Name SAMPLE CUSTOMER
    Service Address 23/25 SAMPLE STREET, SAMPLE DISTRICT, LAGOS.
    Purchase Type Prepaid
    Units Purchased 40.1 kWh
    Token 4774-0499-2699-6259-8058
    Transaction No. 260214090100199512101882
    """

    parsed = parse_utility_bill_text(text, today=date(2026, 6, 3))
    data = parsed.data

    assert data["service_address"] == "23/25 SAMPLE STREET, SAMPLE DISTRICT, LAGOS."
    assert data["document_date"] == "2026-02-14"
    assert data["months_old"] == 3
    assert data["days_old"] == 109
    assert data["provider_code"] == "IKEDC"
    assert data["receipt_type"] == "PREPAID_RECEIPT"


def test_moniepoint_utility_receipt_extracts_labelled_address_and_date():
    """Moniepoint-style receipts should parse labels split across lines."""
    text = """
    Moniepoint
    Transaction Type
    Bill Payments
    Biller
    Abuja Electricity Distribution Prepaid
    Beneficiary ID
    015900000000
    Meter Token
    1369-3834-3745-9389-4830
    Unit
    14.7
    Address
    PLOT 2101 CADASTRAL ZONE SAMPLE,
    LUGBE
    Transaction Date
    Tuesday, February 10th, 2026
    Transaction Reference
    BPT|SAMPLE|20260210120000
    """

    parsed = parse_utility_bill_text(text, today=date(2026, 6, 3))
    data = parsed.data

    assert data["service_address"] == "PLOT 2101 CADASTRAL ZONE SAMPLE, LUGBE"
    assert data["document_date"] == "2026-02-10"
    assert data["months_old"] == 3
    assert data["provider_code"] == "AEDC"
    assert data["token"] == "1369-3834-3745-9389-4830"


def test_utility_receipt_parser_handles_compact_ocr_dates():
    """OCR-glued month/day/year/time text should still produce dates."""
    text = """
    TransactionDate 21April,20267:53Am
    Provider Abuja Electricity
    Address PLT 75 SAMPLE ESTATE LOKOGOMA
    Meter Number 4514210000
    Token 1683-8868-1360-6422-2674
    """

    parsed = parse_utility_bill_text(text, today=date(2026, 6, 3))
    data = parsed.data

    assert data["document_date"] == "2026-04-21"
    assert data["months_old"] == 1
    assert data["days_old"] == 43
