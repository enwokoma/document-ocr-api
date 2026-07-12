"""Integration tests for the shared URL input and JSON rendering contract."""

from __future__ import annotations

import io
from typing import Any

import pytest

from app import app
from src.core import document_source

PNG_BYTES = b"\x89PNG\r\n\x1a\nsynthetic-route-image"
PDF_BYTES = b"%PDF-1.7\nsynthetic-route-pdf"


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def _assert_pretty_json(response: Any) -> None:
    assert response.is_json
    body = response.get_data(as_text=True)
    assert body.startswith(("{\n", "[\n"))
    assert '\n  "' in body or body == "[]\n"


@pytest.mark.parametrize(
    ("endpoint", "processor_name", "expected_hint"),
    (
        ("/api/scan-passport", "extract_mrz_from_image", ("country_hint", "GHA")),
        ("/api/passport", "extract_mrz_from_image", ("country_hint", "GHA")),
        ("/api/nin", "extract_nin_from_image", ("country_code", "GHA")),
        ("/api/bank-statement", "extract_bank_statement_data", ("is_pdf", False)),
        ("/api/utility-bill", "extract_utility_bill_data", ("country_code", "GHA")),
        ("/api/voter-id", "extract_voter_id_data", ("country_code", "GHA")),
        ("/api/drivers-license", "extract_drivers_license_data", ("country_code", "GHA")),
    ),
)
def test_every_ocr_route_accepts_a_json_document_url(
    client,
    monkeypatch,
    endpoint,
    processor_name,
    expected_hint,
):
    downloads: list[str] = []
    calls: list[tuple[bytes, dict[str, Any]]] = []

    def fake_download(url: str, **_kwargs: Any):
        downloads.append(url)
        return io.BytesIO(PNG_BYTES), len(PNG_BYTES), "png", "remote.png"

    def fake_processor(file_stream: Any, **kwargs: Any):
        calls.append((file_stream.read(), kwargs))
        return {"success": True, "document_type": "SYNTHETIC", "data": {}}

    monkeypatch.setattr(document_source, "_download_document_url", fake_download)
    monkeypatch.setattr(f"src.api.routes.{processor_name}", fake_processor)

    response = client.post(
        endpoint,
        json={"url": "https://files.example.com/document.png", "country": "GHA"},
    )

    assert response.status_code == 200
    _assert_pretty_json(response)
    assert downloads == ["https://files.example.com/document.png"]
    assert calls[0][0] == PNG_BYTES
    assert calls[0][1][expected_hint[0]] == expected_hint[1]


def test_business_document_route_accepts_json_url_and_hints(client, monkeypatch):
    calls: dict[str, Any] = {}

    monkeypatch.setattr(
        document_source,
        "_download_document_url",
        lambda *args, **kwargs: (io.BytesIO(PDF_BYTES), len(PDF_BYTES), "pdf", "registry.pdf"),
    )

    def fake_processor(file_stream: Any, **kwargs: Any):
        calls["bytes"] = file_stream.read()
        calls.update(kwargs)
        return {
            "success": True,
            "document_type": "CERTIFICATE_OF_INCORPORATION",
            "jurisdiction": {"country_code": "NGA"},
            "extraction": {"file_type": "pdf", "pages_processed": 1},
            "warnings": [],
            "data": {},
        }

    monkeypatch.setattr("src.api.routes.extract_business_document_data", fake_processor)
    response = client.post(
        "/api/business-document",
        json={
            "url": "https://files.example.com/company.pdf",
            "country": "nga",
            "jurisdiction": "Lagos",
            "document_type": "certificate_of_incorporation",
        },
    )

    assert response.status_code == 200
    _assert_pretty_json(response)
    assert calls == {
        "bytes": PDF_BYTES,
        "country_code": "NGA",
        "jurisdiction_hint": "Lagos",
        "document_type_hint": "CERTIFICATE_OF_INCORPORATION",
        "filename": "registry.pdf",
        "is_pdf": True,
    }
    assert response.get_json()["request_id"]


def test_document_url_alias_is_accepted_in_multipart_form(client, monkeypatch):
    calls: list[bytes] = []
    monkeypatch.setattr(
        document_source,
        "_download_document_url",
        lambda *args, **kwargs: (io.BytesIO(PNG_BYTES), len(PNG_BYTES), "png", "remote.png"),
    )
    monkeypatch.setattr(
        "src.api.routes.extract_bank_statement_data",
        lambda file_stream, **kwargs: calls.append(file_stream.read()) or {"success": True},
    )

    response = client.post(
        "/api/bank-statement",
        data={"document_url": "https://files.example.com/statement.png"},
    )

    assert response.status_code == 200
    assert calls == [PNG_BYTES]


@pytest.mark.parametrize("endpoint", ("/api/scan-passport", "/api/passport", "/api/nin"))
def test_image_only_routes_reject_pdf_urls_before_ocr(client, monkeypatch, endpoint):
    monkeypatch.setattr(
        document_source,
        "_download_document_url",
        lambda *args, **kwargs: (io.BytesIO(PDF_BYTES), len(PDF_BYTES), "pdf", "document.pdf"),
    )

    response = client.post(endpoint, json={"url": "https://files.example.com/document.pdf"})

    assert response.status_code == 415
    assert response.get_json()["success"] is False
    assert "image documents only" in response.get_json()["message"]


def test_route_rejects_file_and_url_together_without_fetching(client, monkeypatch):
    def fail_download(*_args: Any, **_kwargs: Any):
        raise AssertionError("URL fetch must not start for an ambiguous request")

    monkeypatch.setattr(document_source, "_download_document_url", fail_download)
    response = client.post(
        "/api/bank-statement",
        data={
            "file": (io.BytesIO(PNG_BYTES), "statement.png"),
            "url": "https://files.example.com/statement.png",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "exactly one" in response.get_json()["message"]


def test_query_string_url_is_rejected_and_never_reflected(client):
    secret_url = "https://files.example.com/document.pdf?token=private-value"
    response = client.post("/api/bank-statement", query_string={"url": secret_url})

    assert response.status_code == 400
    assert "request body" in response.get_json()["message"]
    assert "private-value" not in response.get_data(as_text=True)


@pytest.mark.parametrize("endpoint", ("/api/bank-statement", "/api/business-document"))
def test_unexpected_url_fetch_failures_return_sanitized_json(client, monkeypatch, endpoint):
    def fail_download(*_args: Any, **_kwargs: Any):
        raise RuntimeError("internal transport detail with private-token")

    monkeypatch.setattr(document_source, "_download_document_url", fail_download)
    response = client.post(endpoint, json={"url": "https://files.example.com/document.pdf"})

    assert response.status_code == 502
    _assert_pretty_json(response)
    assert response.get_json()["success"] is False
    assert "Could not retrieve" in response.get_json()["message"]
    assert "private-token" not in response.get_data(as_text=True)


@pytest.mark.parametrize(
    ("method", "endpoint", "kwargs"),
    (
        ("get", "/", {}),
        ("get", "/api/countries", {}),
        ("get", "/api/countries/XYZ", {}),
        ("post", "/api/passport", {}),
        ("post", "/api/nin", {}),
        ("post", "/api/bank-statement", {}),
        ("post", "/api/utility-bill", {}),
        ("post", "/api/voter-id", {}),
        ("post", "/api/drivers-license", {}),
        ("post", "/api/business-document", {}),
        ("post", "/api/webhooks/forward", {"data": b""}),
    ),
)
def test_all_json_endpoint_families_are_pretty_printed(client, method, endpoint, kwargs):
    response = getattr(client, method)(endpoint, **kwargs)

    _assert_pretty_json(response)
