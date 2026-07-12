"""Flask application entrypoint.

This file creates the web server, wires route blueprints into Flask, and enables
Swagger UI. The heavy OCR work lives under `src/document_ocr`; this file should
stay small so startup behavior is easy to understand.
"""

from dotenv import load_dotenv

load_dotenv()

import uuid

from flask import Flask, Request, current_app, jsonify, request
from flasgger import Swagger
from werkzeug.exceptions import RequestEntityTooLarge

from src.api.routes import passport_bp
from src.document_ocr.business_document.config import get_business_document_settings
from src.document_ocr.business_document.processor import business_document_error
from src.webhook_forwarder.routes import forwarder_bp

business_document_settings = get_business_document_settings()


class DocumentOCRRequest(Request):
    """Apply the business upload cap without changing legacy endpoint limits."""

    @property
    def max_content_length(self) -> int | None:
        if self.path == "/api/business-document":
            return current_app.config["BUSINESS_DOCUMENT_MAX_CONTENT_LENGTH"]
        return super().max_content_length


app = Flask(__name__)
app.request_class = DocumentOCRRequest
# Leave room for multipart boundaries while enforcing the request-scoped cap
# before Flask materializes a business-document upload.
app.config["BUSINESS_DOCUMENT_MAX_CONTENT_LENGTH"] = business_document_settings.max_upload_bytes + 1024 * 1024

swagger_config = {
    "headers": [],
    "specs": [
        {
            "endpoint": "apispec_1",
            "route": "/apispec_1.json",
            "rule_filter": lambda rule: True,
            "model_filter": lambda tag: True,
        }
    ],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/api-docs",
}
app.config["SWAGGER"] = {
    "title": "Document OCR API",
    "uiversion": 3,
    "description": (
        "A RESTful API for extracting structured data from identity, financial, utility, and business documents. "
        "Supports passport MRZ extraction, local identity documents, bank statements, and jurisdiction-aware company records. "
        "HMAC route decorators are present, but enforcement must be enabled in src/core/auth.py before public deployment."
    ),
}
Swagger(app, config=swagger_config)

app.register_blueprint(passport_bp, url_prefix="/api")
app.register_blueprint(forwarder_bp, url_prefix="/api")


@app.errorhandler(RequestEntityTooLarge)
def request_entity_too_large(_error):
    """Return JSON rather than Flask's HTML page for oversized request bodies."""
    if request.path == "/api/business-document":
        result = business_document_error("Uploaded document exceeds the configured request size limit.")
        result["request_id"] = uuid.uuid4().hex
        return jsonify(result), 413
    return jsonify({"success": False, "message": "Request body is too large"}), 413


@app.route("/")
def health_check():
    """Return a small JSON response that load balancers can use as a health check."""
    return {"status": "healthy", "message": "Document OCR API is live"}, 200


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5005)
