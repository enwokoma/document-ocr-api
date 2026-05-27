"""Tests for country-specific voter ID and driver's license parsers."""

from src.countries.ghana.drivers_license import parse_ghana_drivers_license
from src.countries.ghana.voter_id import parse_ghana_voter_id
from src.countries.nigeria.drivers_license import parse_nigeria_drivers_license
from src.countries.nigeria.voter_id import parse_nigeria_voter_card


def test_nigeria_voter_card_parser_extracts_sample_fields():
    """Nigerian voter cards should expose code, VIN, name, and demographics."""
    text = """
    FEDERAL REPUBLIC OF NIGERIA
    INDEPENDENT NATIONAL ELECTORAL COMMISSION
    VOTER'S CARD
    CODE: 99-88-77-666
    VIN: ABC1 0000 0000 0000 000
    DELIM: ANAMBRA | LAGOS SOUTH SAMPLE MAINLAND
    SAMPLE, PERSON NAME
    DATE OF BIRTH
    01-02-1990
    GENDER
    MALE
    OCCUPATION
    BUSINESS
    ADDRESS
    NO. 1 SAMPLE STREET, LAGOS
    """

    result = parse_nigeria_voter_card(text)

    assert result["success"] is True
    assert result["document_type"] == "VOTER_CARD"
    assert result["data"]["code"] == "99-88-77-666"
    assert result["data"]["vin"] == "ABC1000000000000000"
    assert result["data"]["full_name"] == "SAMPLE, PERSON NAME"


def test_nigeria_voter_card_parser_repairs_joined_sample_fields():
    """Nigerian voter card parser should repair common OCR joins from PVC photos."""
    text = """
    CODE: 99-88-77-666
    VIN: ABC1 0000 0000 0000 001
    DELIM: ANAMBRALAGOS SOUTH SAMPLEMAINLAND
    SAMPLE,HOLDERNAME
    DATEOFBIRTH
    01-02-1990
    GENDER
    MALE
    OCCUPATION
    BUSINESS
    ADDRESS
    4SAMPLESTREET.LAGOS
    """

    result = parse_nigeria_voter_card(text)

    assert result["success"] is True
    assert result["data"]["vin"] == "ABC1000000000000001"
    assert result["data"]["delimitation"] == "ANAMBRA LAGOS SOUTH SAMPLE MAINLAND"
    assert result["data"]["full_name"] == "SAMPLE, HOLDER NAME"
    assert result["data"]["date_of_birth"] == "01-02-1990"
    assert result["data"]["gender"] == "MALE"
    assert result["data"]["address"] == "4 SAMPLE STREET, LAGOS"


def test_nigeria_voter_card_parser_handles_two_label_demographic_row():
    """Nigerian voter card parser should read DOB/gender from paired OCR rows."""
    text = """
    FEDERALREPUBLICOFNIGERIA
    INDEPENDENTNATIONALELEGTORALCOMMISSION
    VOTER'SCARD
    CODE: 99-88-77-666 VIN:ABC1000000000000001
    DELIM:ANAMBRAILAGOSSOUTH
    SAMPLEMAINLAND
    SAMPLE,HOLDERNAME
    DATEOF BIRTH GENDER
    01-02-1990 MALE
    OCCUPATION
    BUSINESS
    ADDRESS
    4SAMPLESTREET.LAGOS
    """

    result = parse_nigeria_voter_card(text)

    assert result["success"] is True
    assert result["data"]["date_of_birth"] == "01-02-1990"
    assert result["data"]["gender"] == "MALE"
    assert result["data"]["delimitation"] == "ANAMBRA LAGOS SOUTH SAMPLE MAINLAND"


def test_nigeria_voter_card_parser_handles_rendered_pdf_ocr_text():
    """Nigerian voter card parser should handle OCR text from rotated PDF rendering."""
    text = """
    FEDERAL REPUBLICOF NIGERIA
    INDEPENDENTNATIONALELECTORALCOMMISSION
    VOTER'S CARD
    CODE: 99-88-77-666 VIN:ABC1000000000000001
    A DELIM:ANAMBRALAGOSSOUTH
    SAMPLE MAINLAND
    SAMPLE.HOLDERNAME
    DATEOFBIRTH GENDER
    01-02-1990 MALE
    OCCUPATION
    BUSINESS
    ADDRESS
    4SAMPLESTREET.LAGOS
    """

    result = parse_nigeria_voter_card(text)

    assert result["success"] is True
    assert result["data"]["delimitation"] == "ANAMBRA LAGOS SOUTH SAMPLE MAINLAND"
    assert result["data"]["full_name"] == "SAMPLE, HOLDER NAME"
    assert result["data"]["date_of_birth"] == "01-02-1990"
    assert result["data"]["gender"] == "MALE"


def test_ghana_voter_id_parser_extracts_common_fields():
    """Ghana voter ID parser should support typical label/value OCR text."""
    text = """
    REPUBLIC OF GHANA
    VOTER ID: GHA-123456789
    NAME: SAMPLE PERSON
    DATE OF BIRTH: 01-02-1990
    SEX: M
    POLLING STATION: SAMPLE STATION
    """

    result = parse_ghana_voter_id(text)

    assert result["success"] is True
    assert result["document_type"] == "VOTER_ID"
    assert result["data"]["voter_id"] == "GHA-123456789"
    assert result["data"]["gender"] == "MALE"


def test_nigeria_drivers_license_parser_extracts_common_fields():
    """Nigerian driver's license parser should read standard license labels."""
    text = """
    FEDERAL REPUBLIC OF NIGERIA
    DRIVER'S LICENSE
    LICENSE NO: ABC123456789
    NAME: SAMPLE PERSON
    DOB: 01-02-1990
    ISSUE DATE: 03-04-2020
    EXPIRY DATE: 03-04-2025
    CLASS: B
    SEX: F
    ADDRESS: 1 SAMPLE ROAD
    """

    result = parse_nigeria_drivers_license(text)

    assert result["success"] is True
    assert result["document_type"] == "DRIVERS_LICENSE"
    assert result["data"]["license_number"] == "ABC123456789"
    assert result["data"]["gender"] == "FEMALE"


def test_ghana_drivers_license_parser_extracts_common_fields():
    """Ghana driver's license parser should read standard license labels."""
    text = """
    REPUBLIC OF GHANA
    DRIVER'S LICENSE
    LICENCE NUMBER: GHA987654321
    FULL NAME: SAMPLE PERSON
    DATE OF BIRTH: 01-02-1990
    EXP: 03-04-2027
    CATEGORY: C
    GENDER: MALE
    """

    result = parse_ghana_drivers_license(text)

    assert result["success"] is True
    assert result["document_type"] == "DRIVERS_LICENSE"
    assert result["data"]["license_number"] == "GHA987654321"
    assert result["data"]["license_class"] == "C"
