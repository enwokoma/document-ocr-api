"""Universal, bounded file-or-URL document input handling."""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import ssl
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from tempfile import SpooledTemporaryFile
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import urllib3
from urllib3 import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import HTTPError, ReadTimeoutError
from werkzeug.datastructures import FileStorage
from werkzeug.http import parse_options_header
from werkzeug.utils import secure_filename

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_TYPE_EXTENSIONS = {
    "pdf": ".pdf",
    "jpeg": ".jpg",
    "png": ".png",
    "tiff": ".tif",
    "bmp": ".bmp",
    "webp": ".webp",
}
_TYPE_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "tiff": "image/tiff",
    "bmp": "image/bmp",
    "webp": "image/webp",
}


@dataclass(frozen=True)
class DocumentSourceSettings:
    """Environment-backed limits for uploads and remote URL retrieval."""

    max_bytes: int = 20 * 1024 * 1024
    spool_memory_bytes: int = 1024 * 1024
    url_enabled: bool = True
    allow_http: bool = False
    allowed_ports: tuple[int, ...] = (443,)
    allowed_hosts: tuple[str, ...] = ()
    connect_timeout_seconds: float = 3.0
    read_timeout_seconds: float = 8.0
    total_timeout_seconds: float = 20.0
    max_redirects: int = 3
    max_url_length: int = 2048


@dataclass
class DocumentSource:
    """One normalized document stream passed to an existing OCR processor."""

    file: FileStorage
    file_type: str
    is_pdf: bool
    origin: str
    size_bytes: int
    _owns_stream: bool = False

    def __enter__(self) -> "DocumentSource":
        self.file.stream.seek(0)
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        if self._owns_stream:
            self.file.close()


class DocumentSourceError(ValueError):
    """A sanitized request-source failure with an HTTP status code."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class _ValidatedURL:
    url: str
    scheme: str
    hostname: str
    port: int
    request_target: str
    host_header: str
    addresses: tuple[str, ...]


def get_document_source_settings() -> DocumentSourceSettings:
    """Load bounded document-source settings from environment variables."""
    allow_http = _boolean_env("DOCUMENT_URL_ALLOW_HTTP", default=False)
    default_ports = (80, 443) if allow_http else (443,)
    return DocumentSourceSettings(
        max_bytes=_bounded_int(
            "DOCUMENT_MAX_UPLOAD_BYTES",
            default=20 * 1024 * 1024,
            minimum=1024,
            maximum=100 * 1024 * 1024,
        ),
        spool_memory_bytes=_bounded_int(
            "DOCUMENT_INPUT_SPOOL_MEMORY_BYTES",
            default=1024 * 1024,
            minimum=64 * 1024,
            maximum=10 * 1024 * 1024,
        ),
        url_enabled=_boolean_env("DOCUMENT_URL_ENABLED", default=True),
        allow_http=allow_http,
        allowed_ports=_port_list_env("DOCUMENT_URL_ALLOWED_PORTS", default=default_ports),
        allowed_hosts=_host_list_env("DOCUMENT_URL_ALLOWED_HOSTS"),
        connect_timeout_seconds=_bounded_float(
            "DOCUMENT_URL_CONNECT_TIMEOUT_SECONDS",
            default=3.0,
            minimum=0.5,
            maximum=30.0,
        ),
        read_timeout_seconds=_bounded_float(
            "DOCUMENT_URL_READ_TIMEOUT_SECONDS",
            default=8.0,
            minimum=0.5,
            maximum=60.0,
        ),
        total_timeout_seconds=_bounded_float(
            "DOCUMENT_URL_TOTAL_TIMEOUT_SECONDS",
            default=20.0,
            minimum=1.0,
            maximum=120.0,
        ),
        max_redirects=_bounded_int("DOCUMENT_URL_MAX_REDIRECTS", default=3, minimum=0, maximum=10),
        max_url_length=_bounded_int("DOCUMENT_URL_MAX_LENGTH", default=2048, minimum=256, maximum=8192),
    )


def resolve_document_source(
    flask_request: Any,
    *,
    allow_pdf: bool = True,
    settings: Optional[DocumentSourceSettings] = None,
) -> DocumentSource:
    """Resolve exactly one multipart upload or body URL into a seekable file."""
    active_settings = settings or get_document_source_settings()
    uploaded = flask_request.files.get("file")
    document_url = _document_url_from_body(flask_request)

    if uploaded is not None and document_url:
        raise DocumentSourceError("Provide exactly one document source: either file or url, not both.")
    if uploaded is not None:
        return _source_from_upload(uploaded, allow_pdf=allow_pdf, settings=active_settings)
    if document_url:
        if not active_settings.url_enabled:
            raise DocumentSourceError("Document URL retrieval is disabled.", status_code=403)
        return _source_from_url(document_url, allow_pdf=allow_pdf, settings=active_settings)
    raise DocumentSourceError("No file provided and no document URL provided.")


def detect_document_file_type(header: bytes) -> Optional[str]:
    """Return the supported PDF/image type identified by magic bytes."""
    if header.startswith(b"%PDF-"):
        return "pdf"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    if header.startswith(b"BM"):
        return "bmp"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "webp"
    return None


def _source_from_upload(
    uploaded: FileStorage,
    *,
    allow_pdf: bool,
    settings: DocumentSourceSettings,
) -> DocumentSource:
    if not uploaded.filename:
        raise DocumentSourceError("Invalid filename.")
    size = _stream_size(uploaded.stream)
    if size <= 0:
        raise DocumentSourceError("The document is empty.", status_code=422)
    if size > settings.max_bytes:
        raise DocumentSourceError("The document exceeds the configured size limit.", status_code=413)
    header = _read_header(uploaded.stream)
    file_type = detect_document_file_type(header)
    if file_type is None:
        raise DocumentSourceError("Unsupported document format. Upload a PDF or supported image.", status_code=415)
    if file_type == "pdf" and not allow_pdf:
        raise DocumentSourceError("This endpoint accepts image documents only.", status_code=415)
    filename = _canonical_filename(uploaded.filename, file_type)
    normalized = FileStorage(
        stream=uploaded.stream,
        filename=filename,
        name=uploaded.name,
        content_type=_TYPE_CONTENT_TYPES[file_type],
        headers=uploaded.headers,
    )
    normalized.stream.seek(0)
    return DocumentSource(normalized, file_type, file_type == "pdf", "upload", size)


def _source_from_url(
    document_url: str,
    *,
    allow_pdf: bool,
    settings: DocumentSourceSettings,
) -> DocumentSource:
    try:
        stream, size, file_type, filename = _download_document_url(document_url, settings=settings)
    except DocumentSourceError:
        raise
    except Exception as exc:
        raise DocumentSourceError("Could not retrieve the document URL.", status_code=502) from exc
    if file_type == "pdf" and not allow_pdf:
        stream.close()
        raise DocumentSourceError("This endpoint accepts image documents only.", status_code=415)
    storage = FileStorage(
        stream=stream,
        filename=filename,
        name="file",
        content_type=_TYPE_CONTENT_TYPES[file_type],
    )
    return DocumentSource(storage, file_type, file_type == "pdf", "url", size, _owns_stream=True)


def _download_document_url(
    document_url: str,
    *,
    settings: DocumentSourceSettings,
    resolver: Optional[Callable[..., Sequence[Any]]] = None,
) -> tuple[Any, int, str, str]:
    active_resolver = resolver or socket.getaddrinfo
    deadline = time.monotonic() + settings.total_timeout_seconds
    current_url = document_url
    previous_scheme: Optional[str] = None
    visited: set[str] = set()

    for redirect_count in range(settings.max_redirects + 1):
        if time.monotonic() >= deadline:
            raise DocumentSourceError("Timed out while retrieving the document URL.", status_code=504)
        target = _validate_document_url(current_url, settings=settings, resolver=active_resolver)
        if previous_scheme == "https" and target.scheme != "https":
            raise DocumentSourceError("HTTPS document URLs cannot redirect to HTTP.", status_code=403)
        if target.url in visited:
            raise DocumentSourceError("The document URL contains a redirect loop.", status_code=502)
        visited.add(target.url)

        response = _request_validated_url(target, settings=settings, deadline=deadline)
        status = int(response.status)
        if status in _REDIRECT_STATUSES:
            location = response.headers.get("Location")
            response.release_conn()
            if not location:
                raise DocumentSourceError("The document URL returned an invalid redirect.", status_code=502)
            if redirect_count >= settings.max_redirects:
                raise DocumentSourceError("The document URL exceeded the redirect limit.", status_code=502)
            previous_scheme = target.scheme
            current_url = urljoin(target.url, location)
            continue
        if not 200 <= status < 300:
            response.release_conn()
            raise DocumentSourceError("The document URL returned an unsuccessful response.", status_code=502)

        try:
            try:
                stream, size = _read_remote_response(response, settings=settings, deadline=deadline)
            except DocumentSourceError:
                raise
            except ReadTimeoutError as exc:
                raise DocumentSourceError("Timed out while retrieving the document URL.", status_code=504) from exc
            except (HTTPError, OSError, ssl.SSLError) as exc:
                raise DocumentSourceError("Could not read the document URL response.", status_code=502) from exc
        finally:
            response.release_conn()
        header = _read_header(stream)
        file_type = detect_document_file_type(header)
        if file_type is None:
            stream.close()
            raise DocumentSourceError("The document URL did not return a supported PDF or image.", status_code=415)
        filename = _remote_filename(target.url, response.headers, file_type)
        stream.seek(0)
        return stream, size, file_type, filename

    raise DocumentSourceError("The document URL exceeded the redirect limit.", status_code=502)


def _validate_document_url(
    raw_url: str,
    *,
    settings: DocumentSourceSettings,
    resolver: Callable[..., Sequence[Any]],
) -> _ValidatedURL:
    value = str(raw_url or "").strip()
    if not value or len(value) > settings.max_url_length or re.search(r"[\x00-\x20\x7f]", value):
        raise DocumentSourceError("The document URL is invalid.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise DocumentSourceError("The document URL is invalid.") from exc
    scheme = parsed.scheme.lower()
    if scheme not in ({"https", "http"} if settings.allow_http else {"https"}):
        raise DocumentSourceError("The document URL scheme is not allowed.", status_code=403)
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise DocumentSourceError("The document URL contains prohibited credentials or fragments.", status_code=403)
    if not parsed.hostname or "%" in parsed.hostname:
        raise DocumentSourceError("The document URL hostname is invalid.")
    try:
        hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise DocumentSourceError("The document URL hostname is invalid.") from exc
    if not hostname:
        raise DocumentSourceError("The document URL hostname is invalid.")
    port = port or (443 if scheme == "https" else 80)
    if port not in settings.allowed_ports:
        raise DocumentSourceError("The document URL port is not allowed.", status_code=403)
    if settings.allowed_hosts and not _host_is_allowed(hostname, settings.allowed_hosts):
        raise DocumentSourceError("The document URL hostname is not allowed.", status_code=403)

    addresses = _resolve_public_addresses(hostname, port, resolver=resolver)
    host_for_url = f"[{hostname}]" if ":" in hostname else hostname
    netloc = host_for_url if port == (443 if scheme == "https" else 80) else f"{host_for_url}:{port}"
    path = parsed.path or "/"
    normalized_url = urlunsplit((scheme, netloc, path, parsed.query, ""))
    host_header = host_for_url if port == (443 if scheme == "https" else 80) else f"{host_for_url}:{port}"
    request_target = urlunsplit(("", "", path, parsed.query, ""))
    return _ValidatedURL(normalized_url, scheme, hostname, port, request_target, host_header, addresses)


def _resolve_public_addresses(
    hostname: str,
    port: int,
    *,
    resolver: Callable[..., Sequence[Any]],
) -> tuple[str, ...]:
    try:
        records = resolver(hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    except OSError as exc:
        raise DocumentSourceError("The document URL hostname could not be resolved.", status_code=502) from exc
    addresses: list[str] = []
    for record in records:
        try:
            raw_address = str(record[4][0]).split("%", 1)[0]
            address = ipaddress.ip_address(raw_address)
        except (IndexError, TypeError, ValueError) as exc:
            raise DocumentSourceError("The document URL resolved to an invalid address.", status_code=502) from exc
        comparable = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address
        if not comparable.is_global:
            raise DocumentSourceError("The document URL destination is prohibited.", status_code=403)
        rendered = str(address)
        if rendered not in addresses:
            addresses.append(rendered)
    if not addresses:
        raise DocumentSourceError("The document URL hostname could not be resolved.", status_code=502)
    return tuple(addresses)


def _request_validated_url(
    target: _ValidatedURL,
    *,
    settings: DocumentSourceSettings,
    deadline: float,
) -> Any:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DocumentSourceError("Timed out while retrieving the document URL.", status_code=504)
    timeout = urllib3.Timeout(
        connect=min(settings.connect_timeout_seconds, remaining),
        read=min(settings.read_timeout_seconds, remaining),
    )
    last_error: Optional[BaseException] = None
    for address in target.addresses:
        pool: Any
        if target.scheme == "https":
            pool = HTTPSConnectionPool(
                address,
                target.port,
                timeout=timeout,
                retries=False,
                maxsize=1,
                block=True,
                ssl_context=ssl.create_default_context(),
                assert_hostname=target.hostname,
                server_hostname=target.hostname,
            )
        else:
            pool = HTTPConnectionPool(
                address,
                target.port,
                timeout=timeout,
                retries=False,
                maxsize=1,
                block=True,
            )
        try:
            response = pool.urlopen(
                "GET",
                target.request_target,
                headers={
                    "Accept": "application/pdf,image/*;q=0.9,application/octet-stream;q=0.5",
                    "Host": target.host_header,
                    "User-Agent": "document-ocr-api/1.0",
                },
                redirect=False,
                preload_content=False,
                decode_content=False,
                retries=False,
                timeout=timeout,
            )
            response.release_conn = _release_and_close_pool(response.release_conn, pool)
            return response
        except ReadTimeoutError as exc:
            last_error = exc
            pool.close()
        except (HTTPError, OSError, ssl.SSLError) as exc:
            last_error = exc
            pool.close()
    if isinstance(last_error, ReadTimeoutError):
        raise DocumentSourceError("Timed out while retrieving the document URL.", status_code=504) from last_error
    raise DocumentSourceError("Could not retrieve the document URL.", status_code=502) from last_error


def _release_and_close_pool(original_release: Callable[[], None], pool: Any) -> Callable[[], None]:
    """Bind one response release callback to the pool that owns its connection."""

    def release_and_close() -> None:
        try:
            original_release()
        finally:
            pool.close()

    return release_and_close


def _read_remote_response(
    response: Any,
    *,
    settings: DocumentSourceSettings,
    deadline: float,
) -> tuple[Any, int]:
    encoding = str(response.headers.get("Content-Encoding") or "").strip().lower()
    if encoding not in {"", "identity"}:
        raise DocumentSourceError("Compressed document URL responses are not supported.", status_code=502)
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError):
            declared_length = 0
        if declared_length > settings.max_bytes:
            raise DocumentSourceError("The remote document exceeds the configured size limit.", status_code=413)

    stream = SpooledTemporaryFile(max_size=settings.spool_memory_bytes, mode="w+b")
    size = 0
    try:
        for chunk in response.stream(64 * 1024, decode_content=False):
            if time.monotonic() >= deadline:
                raise DocumentSourceError("Timed out while retrieving the document URL.", status_code=504)
            if not chunk:
                continue
            size += len(chunk)
            if size > settings.max_bytes:
                raise DocumentSourceError("The remote document exceeds the configured size limit.", status_code=413)
            stream.write(chunk)
        if size == 0:
            raise DocumentSourceError("The document URL returned an empty document.", status_code=422)
        stream.seek(0)
        return stream, size
    except Exception:
        stream.close()
        raise


def _document_url_from_body(flask_request: Any) -> Optional[str]:
    if any(flask_request.args.get(name) for name in ("url", "document_url")):
        raise DocumentSourceError("Document URLs must be provided in the request body, not the query string.")
    values: list[str] = []
    for name in ("url", "document_url"):
        value = flask_request.form.get(name)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    if flask_request.is_json:
        payload = flask_request.get_json(silent=True)
        if isinstance(payload, Mapping):
            for name in ("url", "document_url"):
                value = payload.get(name)
                if isinstance(value, str) and value.strip():
                    values.append(value.strip())
    unique = list(dict.fromkeys(values))
    if len(unique) > 1:
        raise DocumentSourceError("Provide only one document URL value.")
    return unique[0] if unique else None


def _remote_filename(url: str, headers: Mapping[str, Any], file_type: str) -> str:
    candidate = ""
    disposition = str(headers.get("Content-Disposition") or "")
    if disposition:
        _, parameters = parse_options_header(disposition)
        candidate = str(parameters.get("filename") or parameters.get("filename*") or "")
    if not candidate:
        candidate = PurePosixPath(unquote(urlsplit(url).path)).name
    return _canonical_filename(candidate or "document", file_type)


def _canonical_filename(value: str, file_type: str) -> str:
    cleaned = secure_filename(str(value or ""))
    stem = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", cleaned).strip("._-") or "document"
    return f"{stem[:100]}{_TYPE_EXTENSIONS[file_type]}"


def _stream_size(stream: Any) -> int:
    try:
        original = stream.tell()
        stream.seek(0, 2)
        size = int(stream.tell())
        stream.seek(original)
        return size
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise DocumentSourceError("The document stream could not be read.") from exc


def _read_header(stream: Any, length: int = 16) -> bytes:
    try:
        original = stream.tell()
        stream.seek(0)
        header = bytes(stream.read(length) or b"")
        stream.seek(original)
        return header
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise DocumentSourceError("The document stream could not be read.") from exc


def _host_is_allowed(hostname: str, patterns: Sequence[str]) -> bool:
    for pattern in patterns:
        if pattern.startswith("*.") and hostname.endswith(pattern[1:]) and hostname != pattern[2:]:
            return True
        if hostname == pattern:
            return True
    return False


def _bounded_int(name: str, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _bounded_float(name: str, *, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _boolean_env(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _port_list_env(name: str, *, default: tuple[int, ...]) -> tuple[int, ...]:
    raw = os.getenv(name)
    if not raw:
        return default
    ports = []
    for item in raw.split(","):
        try:
            port = int(item.strip())
        except ValueError:
            return default
        if not 1 <= port <= 65535:
            return default
        ports.append(port)
    return tuple(dict.fromkeys(ports)) or default


def _host_list_env(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    hosts = []
    for item in raw.split(","):
        value = item.strip().lower().rstrip(".")
        if value and re.fullmatch(r"(?:\*\.)?[a-z0-9.-]+", value):
            hosts.append(value)
    return tuple(dict.fromkeys(hosts))
