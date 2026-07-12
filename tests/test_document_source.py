"""Security and normalization tests for universal file-or-URL document input."""

from __future__ import annotations

import io
import socket
from typing import Any

import pytest
from flask import Flask, request
from urllib3.exceptions import ProtocolError, ReadTimeoutError

from src.core import document_source
from src.core.document_source import (
    DocumentSourceError,
    DocumentSourceSettings,
    _download_document_url,
    _request_validated_url,
    _validate_document_url,
    resolve_document_source,
)

PUBLIC_IPV4 = "93.184.216.34"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"synthetic-image"
PDF_BYTES = b"%PDF-1.7\nsynthetic-pdf"


def _resolver_for(*addresses: str):
    def resolve(host: str, port: int, family: int, sock_type: int, protocol: int):
        assert host
        assert port
        assert family == socket.AF_UNSPEC
        assert sock_type == socket.SOCK_STREAM
        assert protocol == socket.IPPROTO_TCP
        output = []
        for address in addresses:
            address_family = socket.AF_INET6 if ":" in address else socket.AF_INET
            socket_address: tuple[Any, ...] = (address, port, 0, 0) if address_family == socket.AF_INET6 else (address, port)
            output.append((address_family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", socket_address))
        return output

    return resolve


class FakeResponse:
    def __init__(self, *, status: int = 200, headers: dict[str, str] | None = None, chunks: tuple[bytes, ...] = ()):
        self.status = status
        self.headers = headers or {}
        self._chunks = chunks
        self.released = False

    def stream(self, _size: int, *, decode_content: bool = False):
        assert decode_content is False
        yield from self._chunks

    def release_conn(self):
        self.released = True


class FailingResponse(FakeResponse):
    def __init__(self, error: Exception):
        super().__init__(status=200)
        self.error = error

    def stream(self, _size: int, *, decode_content: bool = False):
        assert decode_content is False
        raise self.error
        yield b""  # pragma: no cover - makes this a generator for the response contract


@pytest.fixture
def flask_app():
    app = Flask(__name__)
    app.config.update(TESTING=True)
    return app


def test_uploaded_document_is_magic_typed_rewound_and_filename_corrected(flask_app):
    with flask_app.test_request_context(
        "/api/bank-statement",
        method="POST",
        data={"file": (io.BytesIO(PDF_BYTES), "statement.bin")},
    ):
        source = resolve_document_source(request)
        assert source.origin == "upload"
        assert source.is_pdf is True
        assert source.file_type == "pdf"
        assert source.file.filename == "statement.pdf"
        assert source.file.read(4) == b"%PDF"


def test_document_input_requires_exactly_one_file_or_body_url(flask_app):
    with flask_app.test_request_context("/api/passport", method="POST"):
        with pytest.raises(DocumentSourceError, match="No file provided"):
            resolve_document_source(request, allow_pdf=False)

    with flask_app.test_request_context(
        "/api/passport",
        method="POST",
        data={
            "file": (io.BytesIO(PNG_BYTES), "passport.png"),
            "url": "https://files.example.com/passport.png",
        },
    ):
        with pytest.raises(DocumentSourceError, match="exactly one"):
            resolve_document_source(request, allow_pdf=False)


def test_document_url_is_rejected_in_query_string(flask_app):
    with flask_app.test_request_context(
        "/api/passport?url=https://files.example.com/passport.png",
        method="POST",
    ):
        with pytest.raises(DocumentSourceError, match="request body"):
            resolve_document_source(request, allow_pdf=False)


@pytest.mark.parametrize(
    ("url", "status_code"),
    (
        ("http://files.example.com/document.pdf", 403),
        ("ftp://files.example.com/document.pdf", 403),
        ("https://user:secret@files.example.com/document.pdf", 403),
        ("https://files.example.com:8443/document.pdf", 403),
        ("https://files.example.com/document.pdf#fragment", 403),
    ),
)
def test_url_policy_rejects_unsafe_syntax(url, status_code):
    with pytest.raises(DocumentSourceError) as captured:
        _validate_document_url(
            url,
            settings=DocumentSourceSettings(),
            resolver=_resolver_for(PUBLIC_IPV4),
        )

    assert captured.value.status_code == status_code


@pytest.mark.parametrize(
    "address",
    (
        "127.0.0.1",
        "10.0.0.8",
        "169.254.169.254",
        "100.64.0.1",
        "192.0.2.10",
        "::1",
        "fe80::1",
        "::ffff:127.0.0.1",
    ),
)
def test_url_policy_rejects_every_non_global_resolved_address(address):
    with pytest.raises(DocumentSourceError, match="prohibited") as captured:
        _validate_document_url(
            "https://files.example.com/document.pdf",
            settings=DocumentSourceSettings(),
            resolver=_resolver_for(address),
        )

    assert captured.value.status_code == 403


def test_url_policy_rejects_mixed_public_and_private_dns_answers():
    with pytest.raises(DocumentSourceError, match="prohibited"):
        _validate_document_url(
            "https://files.example.com/document.pdf",
            settings=DocumentSourceSettings(),
            resolver=_resolver_for(PUBLIC_IPV4, "10.0.0.5"),
        )


def test_pinned_https_transport_connects_to_validated_ip_with_original_tls_hostname(monkeypatch):
    captured: dict[str, Any] = {}
    fake_response = FakeResponse(status=200, chunks=(PDF_BYTES,))

    class FakePool:
        def __init__(self, host: str, port: int, **kwargs: Any):
            captured.update({"host": host, "port": port, **kwargs})

        def urlopen(self, method: str, target: str, **kwargs: Any):
            captured.update({"method": method, "target": target, "headers": kwargs["headers"]})
            return fake_response

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(document_source, "HTTPSConnectionPool", FakePool)
    target = _validate_document_url(
        "https://files.example.com/folder/document.pdf?signature=redacted",
        settings=DocumentSourceSettings(),
        resolver=_resolver_for(PUBLIC_IPV4),
    )

    response = _request_validated_url(
        target,
        settings=DocumentSourceSettings(),
        deadline=document_source.time.monotonic() + 10,
    )
    response.release_conn()

    assert captured["host"] == PUBLIC_IPV4
    assert captured["port"] == 443
    assert captured["assert_hostname"] == "files.example.com"
    assert captured["server_hostname"] == "files.example.com"
    assert captured["headers"]["Host"] == "files.example.com"
    assert captured["target"] == "/folder/document.pdf?signature=redacted"
    assert captured["closed"] is True


def test_remote_document_streams_with_bounds_and_safe_filename(monkeypatch):
    response = FakeResponse(
        status=200,
        headers={
            "Content-Length": str(len(PDF_BYTES)),
            "Content-Disposition": 'attachment; filename="../../quarterly statement.exe"',
        },
        chunks=(PDF_BYTES[:8], PDF_BYTES[8:]),
    )
    monkeypatch.setattr(document_source, "_request_validated_url", lambda *args, **kwargs: response)

    stream, size, file_type, filename = _download_document_url(
        "https://files.example.com/download?secret=not-logged",
        settings=DocumentSourceSettings(),
        resolver=_resolver_for(PUBLIC_IPV4),
    )

    assert size == len(PDF_BYTES)
    assert file_type == "pdf"
    assert filename == "quarterly_statement.pdf"
    assert stream.read() == PDF_BYTES
    stream.close()
    assert response.released is True


def test_redirect_target_is_revalidated_and_private_destination_is_blocked(monkeypatch):
    redirect = FakeResponse(status=302, headers={"Location": "https://internal.example.com/document.pdf"})
    monkeypatch.setattr(document_source, "_request_validated_url", lambda *args, **kwargs: redirect)

    def resolver(host: str, *args: Any):
        address = PUBLIC_IPV4 if host == "files.example.com" else "10.0.0.2"
        return _resolver_for(address)(host, *args)

    with pytest.raises(DocumentSourceError, match="prohibited"):
        _download_document_url(
            "https://files.example.com/document.pdf",
            settings=DocumentSourceSettings(),
            resolver=resolver,
        )
    assert redirect.released is True


@pytest.mark.parametrize(
    ("response", "status_code", "message"),
    (
        (
            FakeResponse(status=200, headers={"Content-Length": "999"}, chunks=(PDF_BYTES,)),
            413,
            "size limit",
        ),
        (
            FakeResponse(status=200, chunks=(b"not-a-document",)),
            415,
            "supported PDF or image",
        ),
        (
            FakeResponse(status=200, headers={"Content-Encoding": "gzip"}, chunks=(PDF_BYTES,)),
            502,
            "Compressed",
        ),
        (
            FakeResponse(status=404),
            502,
            "unsuccessful",
        ),
        (
            FakeResponse(status=200, chunks=()),
            422,
            "empty",
        ),
    ),
)
def test_remote_failures_are_sanitized_and_bounded(monkeypatch, response, status_code, message):
    monkeypatch.setattr(document_source, "_request_validated_url", lambda *args, **kwargs: response)

    with pytest.raises(DocumentSourceError, match=message) as captured:
        _download_document_url(
            "https://files.example.com/document.pdf?private-token=never-returned",
            settings=DocumentSourceSettings(max_bytes=64),
            resolver=_resolver_for(PUBLIC_IPV4),
        )

    assert captured.value.status_code == status_code
    assert "private-token" not in captured.value.message
    assert response.released is True


@pytest.mark.parametrize(
    ("error", "status_code", "message"),
    (
        (ReadTimeoutError(None, "https://redacted", "timed out"), 504, "Timed out"),
        (ProtocolError("connection failed"), 502, "Could not read"),
    ),
)
def test_remote_stream_transport_errors_are_sanitized(monkeypatch, error, status_code, message):
    response = FailingResponse(error)
    monkeypatch.setattr(document_source, "_request_validated_url", lambda *args, **kwargs: response)

    with pytest.raises(DocumentSourceError, match=message) as captured:
        _download_document_url(
            "https://files.example.com/document.pdf?token=not-returned",
            settings=DocumentSourceSettings(),
            resolver=_resolver_for(PUBLIC_IPV4),
        )

    assert captured.value.status_code == status_code
    assert "not-returned" not in captured.value.message
    assert response.released is True


def test_json_url_resolves_to_rewound_image_source(flask_app, monkeypatch):
    monkeypatch.setattr(
        document_source,
        "_download_document_url",
        lambda *args, **kwargs: (io.BytesIO(PNG_BYTES), len(PNG_BYTES), "png", "remote.png"),
    )
    with flask_app.test_request_context(
        "/api/passport",
        method="POST",
        json={"url": "https://files.example.com/passport.png"},
    ):
        with resolve_document_source(request, allow_pdf=False) as source:
            assert source.origin == "url"
            assert source.file.filename == "remote.png"
            assert source.file.read(8) == b"\x89PNG\r\n\x1a\n"
