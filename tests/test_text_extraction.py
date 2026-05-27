"""Tests for shared upload text extraction helpers."""

from src.document_ocr.text_extraction import _identity_text_score


def test_identity_text_score_prefers_readable_pdf_render_over_scrambled_text():
    """Rendered OCR should beat scrambled embedded PDF text for identity PDFs."""
    scrambled_embedded_text = """
    GENDER
    SCRAMBLED SAMPLE HOLDER,
    MALE
    SOUTH
    0084129
    OF 0000
    ELECTORAL
    STREET,
    1000
    BODIAFRT TEH
    ABC1 01-02-1990
    DELIM:
    VIN:
    FEDERAL
    99-88-77-666
    CODE:
    """
    readable_rendered_text = """
    FEDERAL REPUBLICOF NIGERIA
    INDEPENDENTNATIONALELECTORALCOMMISSION
    VOTER'S CARD
    CODE: 99-88-77-666 VIN:ABC1000000000000001
    DELIM:ANAMBRALAGOSSOUTH
    SAMPLE MAINLAND
    SAMPLE.HOLDERNAME
    DATEOFBIRTH GENDER
    01-02-1990 MALE
    OCCUPATION
    BUSINESS
    ADDRESS
    4SAMPLESTREET.LAGOS
    """

    assert _identity_text_score(readable_rendered_text) > _identity_text_score(scrambled_embedded_text)
