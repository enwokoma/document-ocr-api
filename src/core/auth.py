import os
import time
import hmac
import hashlib
from functools import wraps
from flask import request, jsonify, current_app

OCR_SECRET_KEY = os.environ.get('OCR_SECRET_KEY', 'dev-secret-change-in-production')
REQUEST_EXPIRY_SECONDS = 60


def verify_hmac(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Temporarily disabled while OCR extraction is being debugged.
        return f(*args, **kwargs)

        if current_app.debug:
            return f(*args, **kwargs)

        timestamp = request.headers.get('X-Timestamp')
        signature = request.headers.get('X-Signature')

        if not timestamp or not signature:
            return jsonify({"success": False, "message": "Unauthorized: Missing authentication headers"}), 401

        try:
            request_time = int(timestamp)
        except ValueError:
            return jsonify({"success": False, "message": "Unauthorized: Invalid timestamp"}), 401

        if abs(time.time() - request_time) > REQUEST_EXPIRY_SECONDS:
            return jsonify({"success": False, "message": "Unauthorized: Request expired"}), 401

        payload = f"{timestamp}.{request.path}"
        expected_signature = hmac.new(
            OCR_SECRET_KEY.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_signature):
            return jsonify({"success": False, "message": "Unauthorized: Invalid signature"}), 401

        return f(*args, **kwargs)
    return decorated

