from flask import Blueprint, request, jsonify
from src.document_ocr.passport.processor import extract_mrz_from_image
from src.document_ocr.nin.processor import extract_nin_from_image, nin_extraction_error
from src.document_ocr.bank_statement.processor import extract_bank_statement_data
from src.core.auth import verify_hmac

passport_bp = Blueprint('passport', __name__)


@passport_bp.route('/scan-passport', methods=['POST'])
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


@passport_bp.route('/passport', methods=['POST'])
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
    file = get_uploaded_file()
    if isinstance(file, tuple):
        return file
    try:
        result = extract_mrz_from_image(file)
        return jsonify(result), 200 if result.get("success") else 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@passport_bp.route('/nin', methods=['POST'])
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
        result = extract_nin_from_image(file)
        return jsonify(result), 200 if result.get("success") else 400
    except Exception as e:
        return jsonify(nin_extraction_error(str(e))), 500


@passport_bp.route('/bank-statement', methods=['POST'])
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
    is_pdf = file.filename.lower().endswith('.pdf')
    try:
        result = extract_bank_statement_data(file, is_pdf=is_pdf)
        return jsonify(result), 200 if result.get("success") else 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


def get_uploaded_file():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "No file provided"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "Invalid filename"}), 400
    return file

