"""HTTP routes for document extraction endpoints.

Each route handles Flask-specific concerns, such as reading uploaded files and
building HTTP responses. The actual OCR logic stays in processor modules so it
can be tested and extended without running a web server.
"""

import json
import logging
import time
import uuid

from flask import Blueprint, jsonify, request

from src.core.auth import verify_hmac
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


@passport_bp.route("/scan-passport", methods=["POST"])
@verify_hmac
def scan_passport():
    """
    Scan Passport (Legacy)
    ---
    tags: [Passport]
    consumes: [multipart/form-data]
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
        required: true
    responses:
      200:
        description: Extracted Data
    """
    return process_raw_passport()


@passport_bp.route("/passport", methods=["POST"])
@verify_hmac
def extract_passport():
    """
    Extract Passport Data
    ---
    tags: [Passport]
    consumes: [multipart/form-data]
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
        required: true
    responses:
      200:
        description: Extracted Data
    """
    return process_raw_passport()


def process_raw_passport():
    """Validate the upload and pass the image stream into the passport parser."""
    file = get_uploaded_file()
    if isinstance(file, tuple):
        return file
    try:
        result = extract_mrz_from_image(file, country_hint=get_country_hint())
        return jsonify(result), 200 if result.get("success") else 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@passport_bp.route("/nin", methods=["POST"])
@verify_hmac
def extract_nin():
    """
    Extract NIN Slip Data
    ---
    tags: [NIN]
    consumes: [multipart/form-data]
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
        required: true
    responses:
      200:
        description: Extracted Data
    """
    file = get_uploaded_file()
    if isinstance(file, tuple):
        return file
    try:
        result = extract_nin_from_image(file, country_code=get_country_hint(default="NGA"))
        return jsonify(result), 200 if result.get("success") else 400
    except Exception as e:
        return jsonify(nin_extraction_error(str(e))), 500


@passport_bp.route("/bank-statement", methods=["POST"])
@verify_hmac
def extract_statement():
    """
    Extract Bank Statement Summary
    ---
    tags: [Bank Statement]
    consumes: [multipart/form-data]
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
        required: true
    responses:
      200:
        description: Extracted Data
    """
    file = get_uploaded_file()
    if isinstance(file, tuple):
        return file
    is_pdf = file.filename.lower().endswith(".pdf")
    try:
        result = extract_bank_statement_data(file, is_pdf=is_pdf)
        return jsonify(result), 200 if result.get("success") else 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@passport_bp.route("/utility-bill", methods=["POST"])
@verify_hmac
def extract_utility_bill():
    """
    Extract Utility Bill / Receipt Data
    ---
    tags: [Utility Bill]
    consumes: [multipart/form-data]
    parameters:
      - name: country
        in: formData
        type: string
        required: false
      - name: file
        in: formData
        type: file
        required: true
    responses:
      200:
        description: Extracted Data
    """
    file = get_uploaded_file()
    if isinstance(file, tuple):
        return file
    try:
        result = extract_utility_bill_data(
            file,
            country_code=get_country_hint(default="NGA"),
            is_pdf=is_pdf_upload(file),
        )
        return jsonify(result), 200 if result.get("success") else 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e), "document_type": "UTILITY_BILL"}), 500


@passport_bp.route("/voter-id", methods=["POST"])
@verify_hmac
def extract_voter_id():
    """
    Extract Voter ID / Voter Card Data
    ---
    tags: [Voter ID]
    consumes: [multipart/form-data]
    parameters:
      - name: country
        in: formData
        type: string
        required: false
      - name: file
        in: formData
        type: file
        required: true
    responses:
      200:
        description: Extracted Data
    """
    file = get_uploaded_file()
    if isinstance(file, tuple):
        return file
    try:
        result = extract_voter_id_data(
            file,
            country_code=get_country_hint(default="NGA"),
            is_pdf=is_pdf_upload(file),
        )
        return jsonify(result), 200 if result.get("success") else 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e), "document_type": "VOTER_ID"}), 500


@passport_bp.route("/drivers-license", methods=["POST"])
@verify_hmac
def extract_drivers_license():
    """
    Extract Driver's License Data
    ---
    tags: [Driver's License]
    consumes: [multipart/form-data]
    parameters:
      - name: country
        in: formData
        type: string
        required: false
      - name: file
        in: formData
        type: file
        required: true
    responses:
      200:
        description: Extracted Data
    """
    file = get_uploaded_file()
    if isinstance(file, tuple):
        return file
    try:
        result = extract_drivers_license_data(
            file,
            country_code=get_country_hint(default="NGA"),
            is_pdf=is_pdf_upload(file),
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
    consumes: [multipart/form-data]
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
        required: true
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
    if "file" not in request.files:
        return _business_document_route_error("No file provided", request_id, started)
    file = request.files["file"]
    if file.filename == "":
        return _business_document_route_error("Invalid filename", request_id, started)

    country_hint = get_optional_hint("country", uppercase=True)
    jurisdiction_hint = get_optional_hint("jurisdiction")
    document_type_hint = get_optional_hint("document_type", uppercase=True)
    try:
        result = extract_business_document_data(
            file,
            country_code=country_hint,
            jurisdiction_hint=jurisdiction_hint,
            document_type_hint=document_type_hint,
            filename=file.filename,
        )
        result["request_id"] = request_id
        status_code = 200 if result.get("success") else 400
        logger.info(
            "business_document_processed %s",
            json.dumps(
                {
                    "request_id": request_id,
                    "content_length": request.content_length,
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


def get_uploaded_file():
    """Return the uploaded file or a ready-to-send Flask error response.

    Keeping this in one helper gives every endpoint the same missing-file and
    empty-filename behavior.
    """
    if "file" not in request.files:
        return jsonify({"success": False, "message": "No file provided"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"success": False, "message": "Invalid filename"}), 400
    return file


def is_pdf_upload(file) -> bool:
    """Return True when an upload filename looks like a PDF."""
    return file.filename.lower().endswith(".pdf")


def get_country_hint(default=None):
    """Read an optional country hint from form data or query parameters.

    The API accepts ISO-3166 alpha-3 codes such as `NGA`. Passport processing can
    usually infer the country from MRZ text, but ID documents often need a hint
    because local IDs do not share one universal layout.
    """
    value = request.form.get("country") or request.args.get("country") or default
    return value.upper() if isinstance(value, str) and value.strip() else default


def get_optional_hint(name, *, uppercase=False):
    """Read a bounded optional form/query hint without treating it as trusted evidence."""
    value = request.form.get(name) or request.args.get(name)
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = " ".join(value.split())[:100]
    return cleaned.upper() if uppercase else cleaned


def _request_id():
    """Return a bounded caller correlation ID or generate one for business OCR logs."""
    supplied = request.headers.get("X-Request-Id") or request.headers.get("X-Correlation-Id")
    return supplied.strip()[:128] if isinstance(supplied, str) and supplied.strip() else uuid.uuid4().hex


def _business_document_route_error(message, request_id, started):
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
    return jsonify(result), 400
