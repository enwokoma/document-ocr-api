"""Integration coverage for the jurisdiction-aware business-document OCR API."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from app import app
from src.core.ocr_engine import OCRBox
from src.document_ocr import text_extraction
from src.document_ocr.business_document import processor as business_processor
from src.document_ocr.business_document.processor import parse_business_document_text
from src.document_ocr.business_document.schema import CANONICAL_BUSINESS_DATA_KEYS
from src.document_ocr.text_extraction import DocumentTextPage, ExtractedDocumentText

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "business_document"
EXPECTED_RESPONSE_KEYS = {
    "success",
    "message",
    "document_type",
    "overall_confidence",
    "confidence_level",
    "classification",
    "jurisdiction",
    "data",
    "field_confidence",
    "evidence",
    "warnings",
    "conflicts",
    "extraction",
    "raw_text",
}
SUMMARY_RESPONSE_KEYS = {
    "success",
    "message",
    "response_detail",
    "document_type",
    "document_type_confidence",
    "overall_confidence",
    "jurisdiction",
    "data",
    "field_details",
    "warnings",
    "conflicts",
    "extraction",
    "raw_text",
    "request_id",
}


@pytest.fixture
def client():
    """Return a Flask client with exceptions surfaced to the test runner."""
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def _fixture_text(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _parse_fixture(name: str, **hints: Any) -> dict[str, Any]:
    text = _fixture_text(name)
    return parse_business_document_text(text, page_texts=(text,), **hints)


def _assert_stable_success_response(result: dict[str, Any], raw_text: str) -> None:
    assert EXPECTED_RESPONSE_KEYS.issubset(result)
    assert result["success"] is True
    assert result["message"] is None
    assert result["raw_text"] == raw_text.strip()
    assert set(result["data"]) == set(CANONICAL_BUSINESS_DATA_KEYS)
    assert 0.0 <= result["overall_confidence"] <= 1.0
    assert result["confidence_level"] in {"REJECT", "LOW", "MEDIUM", "HIGH"}
    assert isinstance(result["warnings"], list)
    assert isinstance(result["conflicts"], list)
    assert isinstance(result["evidence"], dict)
    assert isinstance(result["field_confidence"], dict)


def _assert_pretty_json_response(response: Any) -> None:
    body = response.get_data(as_text=True)
    assert response.mimetype == "application/json"
    assert response.content_length == len(response.get_data())
    assert body.startswith("{\n")
    assert body.endswith("\n")
    assert '\n  "' in body
    assert json.loads(body) == response.get_json()


def _assert_evidence(result: dict[str, Any], field: str, *, page: int = 1) -> None:
    candidates = result["evidence"][field]
    assert candidates
    assert any(candidate["page"] == page for candidate in candidates)
    assert all(0.0 <= candidate["confidence"] <= 1.0 for candidate in candidates)
    assert all(candidate["confidence_level"] in {"REJECT", "LOW", "MEDIUM", "HIGH"} for candidate in candidates)
    assert 0.0 <= result["field_confidence"][field]["score"] <= 1.0


def test_parse_nigeria_certificate_has_stable_schema_identifiers_and_evidence():
    text = _fixture_text("nigeria_cac_certificate.txt")
    result = parse_business_document_text(text, page_texts=(text,))

    _assert_stable_success_response(result, text)
    assert result["document_type"] == "CERTIFICATE_OF_INCORPORATION"
    assert result["jurisdiction"]["country_code"] == "NGA"
    assert result["data"]["legal_company_name"] == "SAMPLE GLOBAL SERVICES LIMITED"
    assert result["data"]["registered_office_address"] == "10 Example Road, Ikeja, Lagos"
    assert result["data"]["incorporation_date"] == "2020-03-12"
    assert result["data"]["document_issue_date"] == "2020-03-13"
    assert result["data"]["document_reference_number"] == "CAC/2020/ABC123"
    assert result["data"]["document_language"]["code"] == "en"

    identifiers = result["data"]["identifiers"]
    assert {item["type"] for item in identifiers} >= {
        "COMPANY_REGISTRATION_NUMBER",
        "TAX_IDENTIFIER",
        "DOCUMENT_REFERENCE_NUMBER",
    }
    assert sum(bool(item["is_primary"]) for item in identifiers) == 1
    assert all(0.0 <= item["confidence"] <= 1.0 for item in identifiers)
    _assert_evidence(result, "legal_company_name")
    _assert_evidence(result, "identifiers")
    _assert_evidence(result, "registered_office_address")


def test_parse_us_articles_uses_country_and_subdivision_hints_without_overriding_evidence():
    text = _fixture_text("us_delaware_articles.txt")
    result = parse_business_document_text(
        text,
        country_hint="USA",
        jurisdiction_hint="Delaware",
        page_texts=(text,),
    )

    _assert_stable_success_response(result, text)
    assert result["document_type"] == "ARTICLES_OF_ORGANIZATION"
    assert result["jurisdiction"]["source"] == "country_hint_and_document"
    assert result["jurisdiction"]["subdivision"]["jurisdiction_code"] == "US-DE"
    assert result["data"]["jurisdiction_of_incorporation"] == "Delaware"
    assert result["data"]["legal_company_name"] == "SAMPLE VENTURES LLC"
    assert result["data"]["entity_type"] == "LIMITED_LIABILITY_COMPANY"
    assert result["data"]["incorporation_date"] == "2021-04-05"
    assert {item["type"] for item in result["data"]["identifiers"]} >= {
        "STATE_FORMATION_IDENTIFIER",
        "EMPLOYER_IDENTIFIER",
        "DOCUMENT_REFERENCE_NUMBER",
    }
    assert not any("hint conflicts" in warning.lower() for warning in result["warnings"])
    _assert_evidence(result, "identifiers")


def test_parse_memart_populates_capital_objects_and_role_specific_party_lists():
    text = _fixture_text("memart_status_report.txt")
    result = parse_business_document_text(text, page_texts=(text,))

    _assert_stable_success_response(result, text)
    assert result["document_type"] == "MEMORANDUM_AND_ARTICLES_OF_ASSOCIATION"
    assert result["data"]["objects_or_purpose"] == [
        "To manufacture and distribute sample industrial products",
        "To carry on any lawful activities incidental to those objects",
    ]
    assert result["data"]["share_capital"]["currency"] == "NGN"
    assert result["data"]["share_capital"]["issued_amount"] == "500"
    assert result["data"]["share_capital"]["paid_up_amount"] == "250"

    assert {party["name"] for party in result["data"]["directors"]} == {
        "Alex Example",
        "Jordan Sample",
    }
    assert {party["name"] for party in result["data"]["shareholders"]} == {
        "Alex Example",
        "Sample Holdings Limited",
    }
    assert [party["name"] for party in result["data"]["beneficial_owners"]] == ["Alex Example"]
    alex = next(party for party in result["data"]["parties"] if party["name"] == "Alex Example")
    assert set(alex["roles"]) == {"DIRECTOR", "SHAREHOLDER", "BENEFICIAL_OWNER"}
    assert any("country of incorporation" in warning.lower() for warning in result["warnings"])
    _assert_evidence(result, "objects_or_purpose")
    _assert_evidence(result, "share_capital")
    _assert_evidence(result, "parties")


def test_generic_unknown_country_preserves_unclassified_fields_and_warns():
    text = _fixture_text("unknown_registry_document.txt")
    result = parse_business_document_text(text, page_texts=(text,))

    _assert_stable_success_response(result, text)
    assert result["jurisdiction"]["country_code"] is None
    assert result["jurisdiction"]["source"] == "undetermined"
    assert result["data"]["legal_company_name"] == "SAMPLE CROSS-BORDER TRADERS"
    assert result["data"]["trading_name"] == "Sample Traders"
    assert result["data"]["principal_business_address"] == "42 Example Market Road"
    assert result["data"]["registration_date"] == "2024-02-03"
    assert result["data"]["identifiers"][0]["type"] == "REGISTRY_NUMBER"
    additional = {item["label"]: item for item in result["data"]["additional_fields"]}
    assert additional["Custom Sector Code"]["value"] == "QX-17"
    assert additional["Local Filing Category"]["value"] == "MERCHANT-A"
    assert all(item["evidence"]["page"] == 1 for item in additional.values())
    assert any("country of incorporation" in warning.lower() for warning in result["warnings"])
    _assert_evidence(result, "additional_fields")


def test_generic_fallback_preserves_explicit_unprofiled_country_and_jurisdiction():
    text = """BUSINESS REGISTRY RECORD
Legal Entity Name: SAMPLE INTERNATIONAL TRADING LIMITED
Country of Incorporation: Ireland
Jurisdiction of Incorporation: Leinster
Registry Identifier: IE-EXAMPLE-1234
"""

    result = parse_business_document_text(text, page_texts=(text,))

    assert result["jurisdiction"]["country_code"] is None
    assert result["data"]["country_code"] is None
    assert result["data"]["legal_company_name"] == "SAMPLE INTERNATIONAL TRADING LIMITED"
    assert result["data"]["country_of_incorporation"] == "Ireland"
    assert result["data"]["jurisdiction_code"] is None
    assert result["data"]["jurisdiction_of_incorporation"] == "Leinster"
    assert "country_of_incorporation" in result["evidence"]
    assert "jurisdiction_of_incorporation" in result["evidence"]
    assert not any(
        item["label"] in {"Country of Incorporation", "Jurisdiction of Incorporation"}
        for item in result["data"]["additional_fields"]
    )


def test_identifier_evidence_uses_offsets_at_page_boundaries():
    page_one = """FEDERAL REPUBLIC OF NIGERIA
CORPORATE AFFAIRS COMMISSION
CERTIFICATE OF INCORPORATION
THIS IS TO CERTIFY THAT SAMPLE BOUNDARY LIMITED IS HEREBY INCORPORATED"""
    page_two = "RC 7654321\nDate of Incorporation: 12 March 2020"
    text = f"{page_one}\n{page_two}"

    result = parse_business_document_text(text, page_texts=(page_one, page_two))

    identifier = next(item for item in result["data"]["identifiers"] if item["normalized_value"] == "RC7654321")
    assert identifier["evidence"][0]["page"] == 2
    field_evidence = [item for item in result["evidence"]["identifiers"] if item["value"] == "RC7654321"]
    assert field_evidence[0]["page"] == 2


def test_ambiguous_document_titles_retain_alternatives_and_warning():
    text = """ARTICLES OF INCORPORATION
ARTICLES OF ORGANIZATION
SAMPLE AMBIGUOUS COMPANY
REGISTERED OFFICE ADDRESS: 1 Test Road
"""
    result = parse_business_document_text(text, page_texts=(text,))

    assert result["classification"]["ambiguous"] is True
    assert result["classification"]["alternatives"]
    assert {
        result["document_type"],
        result["classification"]["alternatives"][0]["document_type"],
    } == {"ARTICLES_OF_INCORPORATION", "ARTICLES_OF_ORGANIZATION"}
    assert any("document type is ambiguous" in warning.lower() for warning in result["warnings"])


def test_conflicting_field_candidates_are_retained_with_selected_value_and_warning():
    text = """FEDERAL REPUBLIC OF NIGERIA
CORPORATE AFFAIRS COMMISSION
CERTIFICATE OF INCORPORATION
THIS IS TO CERTIFY THAT ALPHA HOLDINGS LIMITED IS HEREBY INCORPORATED
Legal Entity Name: BETA HOLDINGS LIMITED
RC 1112223
Date of Incorporation: 12 March 2020
"""
    result = parse_business_document_text(text, page_texts=(text,))

    assert result["data"]["legal_company_name"] == "ALPHA HOLDINGS LIMITED"
    conflict = next(item for item in result["conflicts"] if item["field"] == "legal_company_name")
    assert conflict["selected_value"] == "ALPHA HOLDINGS LIMITED"
    assert set(conflict["candidate_values"]) == {"ALPHA HOLDINGS LIMITED", "BETA HOLDINGS LIMITED"}
    assert conflict["resolution"] == "highest_confidence_evidence"
    assert any("conflicting values" in warning.lower() for warning in result["warnings"])
    candidates = result["evidence"]["legal_company_name"]
    assert {candidate["selected"] for candidate in candidates} == {False, True}


def test_country_hint_conflict_prefers_registry_evidence_and_warns():
    result = _parse_fixture("us_delaware_articles.txt", country_hint="NGA")

    assert result["jurisdiction"]["country_code"] == "USA"
    assert result["jurisdiction"]["requested_country_code"] == "NGA"
    assert result["jurisdiction"]["detected_country_code"] == "USA"
    assert result["jurisdiction"]["conflict"] is True
    assert any("country hint conflicts" in warning.lower() for warning in result["warnings"])


def test_invalid_country_and_document_type_hints_are_warning_only():
    result = _parse_fixture(
        "unknown_registry_document.txt",
        country_hint="not-a-country",
        document_type_hint="NOT_A_REAL_DOCUMENT",
    )

    assert result["success"] is True
    assert result["jurisdiction"]["country_code"] is None
    assert result["document_type"] == "BUSINESS_REGISTRATION_CERTIFICATE"
    assert any("country hint is invalid" in warning.lower() for warning in result["warnings"])
    assert any("document type hint is unsupported" in warning.lower() for warning in result["warnings"])


def test_business_document_endpoint_rejects_missing_upload(client):
    response = client.post("/api/business-document")

    assert response.status_code == 400
    _assert_pretty_json_response(response)
    result = response.get_json()
    assert set(result) == SUMMARY_RESPONSE_KEYS
    assert result["success"] is False
    assert result["message"] == "No file provided and no document URL provided."
    assert result["request_id"]


def test_business_document_endpoint_returns_canonical_unhandled_error(client, monkeypatch, caplog):
    def fail_extraction(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("sensitive internal detail")

    monkeypatch.setattr("src.api.routes.extract_business_document_data", fail_extraction)
    with caplog.at_level(logging.ERROR, logger="src.api.routes"):
        response = client.post(
            "/api/business-document",
            data={"file": (io.BytesIO(b"\x89PNG\r\n\x1a\nminimal"), "certificate.png")},
            content_type="multipart/form-data",
        )

    assert response.status_code == 500
    result = response.get_json()
    assert set(result) == SUMMARY_RESPONSE_KEYS
    assert result["success"] is False
    assert result["request_id"]
    assert "sensitive internal detail" not in result["message"]
    assert "sensitive internal detail" not in caplog.text


def test_business_document_endpoint_rejects_oversized_multipart_before_processing(client, monkeypatch):
    monkeypatch.setitem(app.config, "DOCUMENT_MAX_CONTENT_LENGTH", 256)
    response = client.post(
        "/api/business-document",
        data={"file": (io.BytesIO(b"x" * 1024), "oversized.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    _assert_pretty_json_response(response)
    result = response.get_json()
    assert set(result) == SUMMARY_RESPONSE_KEYS
    assert result["success"] is False
    assert result["request_id"]
    assert "request size limit" in result["message"]


def test_shared_document_request_cap_applies_to_existing_ocr_endpoints(client, monkeypatch):
    monkeypatch.setitem(app.config, "DOCUMENT_MAX_CONTENT_LENGTH", 256)
    response = client.post(
        "/api/passport",
        data={"file": (io.BytesIO(b"x" * 1024), "legacy.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    assert response.get_json()["success"] is False


def test_pretty_printing_applies_to_existing_ocr_endpoints(client):
    response = client.post("/api/bank-statement")

    assert response.status_code == 400
    _assert_pretty_json_response(response)


def test_business_document_endpoint_rejects_invalid_upload_with_stable_error(client):
    response = client.post(
        "/api/business-document",
        data={"file": (io.BytesIO(b"not a PDF or image"), "registry.txt")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    result = response.get_json()
    assert set(result) == SUMMARY_RESPONSE_KEYS
    assert result["success"] is False
    assert result["document_type"] == "UNKNOWN_BUSINESS_DOCUMENT"
    assert result["raw_text"] == ""
    assert "Unsupported document format" in result["message"]


def test_business_document_endpoint_processes_multipart_hints_and_logs_only_metadata(
    client,
    monkeypatch,
    caplog,
):
    text = _fixture_text("nigeria_cac_certificate.txt")
    calls: dict[str, Any] = {}

    def fake_extract_document_text_pages(file_stream: Any, **kwargs: Any) -> ExtractedDocumentText:
        calls["header"] = file_stream.read(8)
        calls.update(kwargs)
        return ExtractedDocumentText(
            pages=(DocumentTextPage(1, text, "image_ocr", 0.93),),
            total_pages=1,
        )

    monkeypatch.setattr(
        business_processor,
        "extract_document_text_pages",
        fake_extract_document_text_pages,
    )
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="src.api.routes"):
        response = client.post(
            "/api/business-document",
            data={
                "file": (io.BytesIO(b"\x89PNG\r\n\x1a\nminimal"), "certificate.png"),
                "country": "NGA",
                "jurisdiction": "SENSITIVE-COMPANY-NAME",
                "document_type": "certificate_of_incorporation",
            },
            headers={"X-Request-Id": "business-integration-test"},
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    _assert_pretty_json_response(response)
    result = response.get_json()
    assert set(result) == SUMMARY_RESPONSE_KEYS
    assert result["raw_text"] == text.strip()
    assert result["request_id"] == "business-integration-test"
    assert result["jurisdiction"]["country_code"] == "NGA"
    assert result["document_type"] == "CERTIFICATE_OF_INCORPORATION"
    assert result["data"]["legal_company_name"] == "SAMPLE GLOBAL SERVICES LIMITED"
    assert "classification" not in result
    assert "field_confidence" not in result
    assert "evidence" not in result
    assert result["field_details"]["legal_company_name"]["confidence"] > 0
    assert result["field_details"]["legal_company_name"]["evidence"]["text"]
    assert all(len(item["evidence"]) == 1 for item in result["data"]["identifiers"])
    assert "trading_name" not in result["data"]
    assert "directors" not in result["data"]
    assert "shareholders" not in result["data"]
    assert "beneficial_owners" not in result["data"]
    assert "pages" not in result["extraction"]
    assert calls["header"] == b"\x89PNG\r\n\x1a\n"
    assert calls["is_pdf"] is False
    assert calls["max_pages"] >= 1

    log_text = caplog.text
    assert "business_document_processed" in log_text
    assert "business-integration-test" in log_text
    assert text.strip() not in log_text
    assert "SAMPLE GLOBAL SERVICES LIMITED" not in log_text
    assert "RC 1234567" not in log_text
    assert "12345678-0001" not in log_text
    assert "SENSITIVE-COMPANY-NAME" not in log_text


def test_business_document_endpoint_decodes_a_real_png_before_parsing(client, monkeypatch):
    text = _fixture_text("nigeria_cac_certificate.txt")
    image = np.full((40, 60, 3), 255, dtype=np.uint8)
    encoded_ok, encoded = cv2.imencode(".png", image)
    assert encoded_ok
    decoded_shapes: list[tuple[int, ...]] = []

    def fake_read_text(decoded_image: np.ndarray) -> list[OCRBox]:
        decoded_shapes.append(decoded_image.shape)
        return [OCRBox("synthetic business document", 0.93)]

    monkeypatch.setattr(text_extraction.engine, "read_text_from_image", fake_read_text)
    monkeypatch.setattr(text_extraction.engine, "group_boxes_into_lines", lambda boxes: text)

    response = client.post(
        "/api/business-document",
        data={
            "file": (io.BytesIO(encoded.tobytes()), "certificate.png"),
            "response_detail": "full",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    result = response.get_json()
    assert result["data"]["legal_company_name"] == "SAMPLE GLOBAL SERVICES LIMITED"
    assert result["extraction"]["file_type"] == "png"
    assert result["extraction"]["pages"][0]["source"] == "image_ocr"
    assert decoded_shapes == [(40, 60, 3)]


def test_business_document_endpoint_full_detail_retains_audit_fields(client, monkeypatch):
    text = _fixture_text("nigeria_cac_certificate.txt")
    full_result = _parse_fixture("nigeria_cac_certificate.txt")
    monkeypatch.setattr("src.api.routes.extract_business_document_data", lambda *args, **kwargs: full_result)

    response = client.post(
        "/api/business-document",
        data={
            "file": (io.BytesIO(b"\x89PNG\r\n\x1a\nminimal"), "certificate.png"),
            "response_detail": "full",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    result = response.get_json()
    assert EXPECTED_RESPONSE_KEYS.issubset(result)
    assert result["raw_text"] == text.strip()
    assert result["evidence"]["legal_company_name"]
    assert result["field_confidence"]["legal_company_name"]["score"] > 0
    assert result["data"]["identifiers"][0]["evidence"]


def test_business_document_endpoint_rejects_unknown_response_detail(client):
    response = client.post(
        "/api/business-document",
        data={"response_detail": "verbose"},
    )

    assert response.status_code == 400
    result = response.get_json()
    assert set(result) == SUMMARY_RESPONSE_KEYS
    assert result["message"] == "response_detail must be either 'summary' or 'full'."


def test_business_document_endpoint_rejects_non_string_response_detail(client):
    response = client.post(
        "/api/business-document",
        json={
            "url": "https://files.example.com/certificate.pdf",
            "response_detail": {"mode": "full"},
        },
    )

    assert response.status_code == 400
    assert response.get_json()["message"] == "response_detail must be either 'summary' or 'full'."
