"""HTTP routes for document extraction endpoints.

Each route handles Flask-specific concerns, such as reading uploaded files and
building HTTP responses. The actual OCR logic stays in processor modules so it
can be tested and extended without running a web server.
"""

import json
import logging
import time
import uuid
from functools import wraps
from typing import Any, Mapping

from flask import Blueprint, jsonify, request

from src.core.auth import verify_hmac
from src.core.document_source import DocumentSource, DocumentSourceError, resolve_document_source
from src.countries.registry import get_country_profile, list_country_profiles, serialize_country_profile
from src.document_ocr.bank_statement.processor import extract_bank_statement_data
from src.document_ocr.business_document.processor import business_document_error, extract_business_document_data
from src.document_ocr.drivers_license.processor import extract_drivers_license_data
from src.document_ocr.nin.processor import extract_nin_from_image, nin_extraction_error
from src.document_ocr.passport.processor import extract_mrz_from_image
from src.document_ocr.utility_bill.processor import extract_utility_bill_data
from src.document_ocr.voter_id.processor import extract_voter_id_data

# The API currently groups all document routes into one blueprint. The name is
# historical; new document routes can be added here without changing app.py.
passport_bp = Blueprint("passport", __name__)
logger = logging.getLogger(__name__)
_gunicorn_logger = logging.getLogger("gunicorn.error")
if _gunicorn_logger.handlers:
    logger.handlers = _gunicorn_logger.handlers
    logger.setLevel(_gunicorn_logger.level)
    logger.propagate = False


def require_document_source(*, allow_pdf: bool = True):
    """Inject one validated upload/URL source and close owned remote streams."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            try:
                source = resolve_document_source(request, allow_pdf=allow_pdf)
            except DocumentSourceError as exc:
                return jsonify({"success": False, "message": exc.message}), _source_error_status(exc)
            with source:
                return view(source, *args, **kwargs)

        return wrapped

    return decorator


@passport_bp.route("/scan-passport", methods=["POST"])
@verify_hmac
@require_document_source(allow_pdf=False)
def scan_passport(source: DocumentSource):
    """
    Scan Passport (Legacy)
    ---
    tags: [Passport]
    consumes: [multipart/form-data, application/json]
    parameters:
      - name: X-Signature
        in: header
        type: string
        required: true
      - name: X-Timestamp
        in: header
        type: string
        required: true
      - name: file
        in: formData
        type: file
        required: false
        description: Passport image; provide exactly one of file or url
      - name: url
        in: formData
        type: string
        required: false
        description: Public HTTPS URL for a passport image
    responses:
      200:
        description: Extracted Data
    """
    return process_raw_passport(source)


@passport_bp.route("/passport", methods=["POST"])
@verify_hmac
@require_document_source(allow_pdf=False)
def extract_passport(source: DocumentSource):
    """
    Extract Passport Data
    ---
    tags: [Passport]
    consumes: [multipart/form-data, application/json]
    parameters:
      - name: X-Signature
        in: header
        type: string
        required: true
      - name: X-Timestamp
        in: header
        type: string
        required: true
      - name: file
        in: formData
        type: file
        required: false
        description: Passport image; provide exactly one of file or url
      - name: url
        in: formData
        type: string
        required: false
        description: Public HTTPS URL for a passport image
    responses:
      200:
        description: Extracted Data
    """
    return process_raw_passport(source)


def process_raw_passport(source: DocumentSource):
    """Validate the upload and pass the image stream into the passport parser."""
    try:
        result = extract_mrz_from_image(source.file, country_hint=get_country_hint())
        return jsonify(result), 200 if result.get("success") else 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@passport_bp.route("/nin", methods=["POST"])
@verify_hmac
@require_document_source(allow_pdf=False)
def extract_nin(source: DocumentSource):
    """
    Extract NIN Slip Data
    ---
    tags: [NIN]
    consumes: [multipart/form-data, application/json]
    parameters:
      - name: X-Signature
        in: header
        type: string
        required: true
      - name: X-Timestamp
        in: header
        type: string
        required: true
      - name: file
        in: formData
        type: file
        required: false
        description: NIN image; provide exactly one of file or url
      - name: url
        in: formData
        type: string
        required: false
        description: Public HTTPS URL for a NIN image
    responses:
      200:
        description: Extracted Data
    """
    try:
        result = extract_nin_from_image(source.file, country_code=get_country_hint(default="NGA"))
        return jsonify(result), 200 if result.get("success") else 400
    except Exception as e:
        return jsonify(nin_extraction_error(str(e))), 500


@passport_bp.route("/bank-statement", methods=["POST"])
@verify_hmac
@require_document_source()
def extract_statement(source: DocumentSource):
    """
    Extract Bank Statement Summary
    ---
    tags: [Bank Statement]
    consumes: [multipart/form-data, application/json]
    parameters:
      - name: X-Signature
        in: header
        type: string
        required: true
      - name: X-Timestamp
        in: header
        type: string
        required: true
      - name: file
        in: formData
        type: file
        required: false
        description: Bank statement PDF/image; provide exactly one of file or url
      - name: url
        in: formData
        type: string
        required: false
        description: Public HTTPS URL for a bank statement PDF/image
    responses:
      200:
        description: Extracted Data
    """
    try:
        result = extract_bank_statement_data(source.file, is_pdf=source.is_pdf)
        return jsonify(result), 200 if result.get("success") else 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@passport_bp.route("/utility-bill", methods=["POST"])
@verify_hmac
@require_document_source()
def extract_utility_bill(source: DocumentSource):
    """
    Extract Utility Bill / Receipt Data
    ---
    tags: [Utility Bill]
    consumes: [multipart/form-data, application/json]
    parameters:
      - name: country
        in: formData
        type: string
        required: false
      - name: file
        in: formData
        type: file
        required: false
        description: Utility document PDF/image; provide exactly one of file or url
      - name: url
        in: formData
        type: string
        required: false
        description: Public HTTPS URL for a utility document PDF/image
    responses:
      200:
        description: Extracted Data
    """
    try:
        result = extract_utility_bill_data(
            source.file,
            country_code=get_country_hint(default="NGA"),
            is_pdf=source.is_pdf,
        )
        return jsonify(result), 200 if result.get("success") else 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e), "document_type": "UTILITY_BILL"}), 500


@passport_bp.route("/voter-id", methods=["POST"])
@verify_hmac
@require_document_source()
def extract_voter_id(source: DocumentSource):
    """
    Extract Voter ID / Voter Card Data
    ---
    tags: [Voter ID]
    consumes: [multipart/form-data, application/json]
    parameters:
      - name: country
        in: formData
        type: string
        required: false
      - name: file
        in: formData
        type: file
        required: false
        description: Voter document PDF/image; provide exactly one of file or url
      - name: url
        in: formData
        type: string
        required: false
        description: Public HTTPS URL for a voter document PDF/image
    responses:
      200:
        description: Extracted Data
    """
    try:
        result = extract_voter_id_data(
            source.file,
            country_code=get_country_hint(default="NGA"),
            is_pdf=source.is_pdf,
        )
        return jsonify(result), 200 if result.get("success") else 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e), "document_type": "VOTER_ID"}), 500


@passport_bp.route("/drivers-license", methods=["POST"])
@verify_hmac
@require_document_source()
def extract_drivers_license(source: DocumentSource):
    """
    Extract Driver's License Data
    ---
    tags: [Driver's License]
    consumes: [multipart/form-data, application/json]
    parameters:
      - name: country
        in: formData
        type: string
        required: false
      - name: file
        in: formData
        type: file
        required: false
        description: Driver's license PDF/image; provide exactly one of file or url
      - name: url
        in: formData
        type: string
        required: false
        description: Public HTTPS URL for a driver's license PDF/image
    responses:
      200:
        description: Extracted Data
    """
    try:
        result = extract_drivers_license_data(
            source.file,
            country_code=get_country_hint(default="NGA"),
            is_pdf=source.is_pdf,
        )
        return jsonify(result), 200 if result.get("success") else 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e), "document_type": "DRIVERS_LICENSE"}), 500


@passport_bp.route("/business-document", methods=["POST"])
@verify_hmac
def extract_business_document():
    """
    Extract Business Registration Document Data
    ---
    tags: [Business Documents]
    consumes: [multipart/form-data, application/json]
    parameters:
      - name: country
        in: formData
        type: string
        required: false
        description: Optional ISO country code or registered country alias
      - name: jurisdiction
        in: formData
        type: string
        required: false
        description: Optional state, province, or subnational jurisdiction hint
      - name: document_type
        in: formData
        type: string
        required: false
        description: Optional business-document taxonomy hint
      - name: file
        in: formData
        type: file
        required: false
        description: Business document PDF/image; provide exactly one of file or url
      - name: url
        in: formData
        type: string
        required: false
        description: Public HTTPS URL for a business document PDF/image
    responses:
      200:
        description: Business document extracted, including review warnings
      400:
        description: Invalid upload or unreadable document
      500:
        description: Internal extraction error
    """
    started = time.perf_counter()
    request_id = _request_id()
    try:
        source = resolve_document_source(request)
    except DocumentSourceError as exc:
        return _business_document_route_error(exc.message, request_id, started, status_code=_source_error_status(exc))

    country_hint = get_optional_hint("country", uppercase=True)
    jurisdiction_hint = get_optional_hint("jurisdiction")
    document_type_hint = get_optional_hint("document_type", uppercase=True)
    try:
        with source:
            result = extract_business_document_data(
                source.file,
                country_code=country_hint,
                jurisdiction_hint=jurisdiction_hint,
                document_type_hint=document_type_hint,
                filename=source.file.filename,
                is_pdf=source.is_pdf,
            )
        result["request_id"] = request_id
        status_code = 200 if result.get("success") else 400
        logger.info(
            "business_document_processed %s",
            json.dumps(
                {
                    "request_id": request_id,
                    "content_length": request.content_length,
                    "input_source": source.origin,
                    "source_size_bytes": source.size_bytes,
                    "file_type": result.get("extraction", {}).get("file_type"),
                    "pages_processed": result.get("extraction", {}).get("pages_processed"),
                    "country_hint_present": country_hint is not None,
                    "jurisdiction_hint_present": jurisdiction_hint is not None,
                    "document_type_hint_present": document_type_hint is not None,
                    "document_type": result.get("document_type"),
                    "country_code": result.get("jurisdiction", {}).get("country_code"),
                    "success": result.get("success"),
                    "warning_count": len(result.get("warnings", [])),
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
                ensure_ascii=False,
            ),
        )
        return jsonify(result), status_code
    except Exception:
        logger.error(
            "business_document_unhandled_exception %s",
            json.dumps(
                {
                    "request_id": request_id,
                    "content_length": request.content_length,
                    "country_hint_present": country_hint is not None,
                    "jurisdiction_hint_present": jurisdiction_hint is not None,
                    "document_type_hint_present": document_type_hint is not None,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
                ensure_ascii=False,
            ),
        )
        result = business_document_error("Unhandled exception while processing the business document.")
        result["request_id"] = request_id
        return jsonify(result), 500


@passport_bp.route("/countries", methods=["GET"])
def list_countries():
    """Return the countries and local ID types currently registered by the API."""
    profiles = list_country_profiles()
    return jsonify(
        {
            "success": True,
            "countries": [serialize_country_profile(profile) for profile in profiles.values()],
        }
    ), 200


@passport_bp.route("/countries/<country_code>", methods=["GET"])
def get_country(country_code):
    """Return one registered country profile by ISO-3166 alpha-3 code."""
    profile = get_country_profile(country_code)
    if profile is None:
        return jsonify(
            {
                "success": False,
                "message": f"Unsupported country code: {country_code.upper()}",
            }
        ), 404
    return jsonify({"success": True, "country": serialize_country_profile(profile)}), 200


def get_country_hint(default=None):
    """Read an optional country hint from form data or query parameters.

    The API accepts ISO-3166 alpha-3 codes such as `NGA`. Passport processing can
    usually infer the country from MRZ text, but ID documents often need a hint
    because local IDs do not share one universal layout.
    """
    value = get_request_value("country") or default
    return value.upper() if isinstance(value, str) and value.strip() else default


def get_optional_hint(name, *, uppercase=False):
    """Read a bounded optional body/query hint without treating it as trusted evidence."""
    value = get_request_value(name)
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = " ".join(value.split())[:100]
    return cleaned.upper() if uppercase else cleaned


def get_request_value(name: str) -> Any:
    """Read a scalar hint from form, JSON, then query parameters."""
    value = request.form.get(name)
    if value is not None:
        return value
    if request.is_json:
        payload = request.get_json(silent=True)
        if isinstance(payload, Mapping) and name in payload:
            return payload.get(name)
    return request.args.get(name)


def _request_id():
    """Return a bounded caller correlation ID or generate one for business OCR logs."""
    supplied = request.headers.get("X-Request-Id") or request.headers.get("X-Correlation-Id")
    return supplied.strip()[:128] if isinstance(supplied, str) and supplied.strip() else uuid.uuid4().hex


def _business_document_route_error(message, request_id, started, *, status_code=400):
    """Return and log a canonical business-document request error."""
    result = business_document_error(message)
    result["request_id"] = request_id
    logger.info(
        "business_document_rejected %s",
        json.dumps(
            {
                "request_id": request_id,
                "content_length": request.content_length,
                "success": False,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        ),
    )
    return jsonify(result), status_code


def _source_error_status(error: DocumentSourceError) -> int:
    """Preserve the upload error convention while using precise URL statuses."""
    if request.files.get("file") is not None and error.status_code in {415, 422}:
        return 400
    return error.status_code
