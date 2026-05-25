"""Flask application entrypoint.

This file creates the web server, wires route blueprints into Flask, and enables
Swagger UI. The heavy OCR work lives under `src/document_ocr`; this file should
stay small so startup behavior is easy to understand.
"""

import os
from dotenv import load_dotenv
load_dotenv()

from flask import Flask
from flasgger import Swagger
from src.api.routes import passport_bp
from src.webhook_forwarder.routes import forwarder_bp

app = Flask(__name__)

swagger_config = {
    "headers": [],
    "specs": [
        {
            "endpoint": 'apispec_1',
            "route": '/apispec_1.json',
            "rule_filter": lambda rule: True,
            "model_filter": lambda tag: True,
        }
    ],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/api-docs"
}
app.config['SWAGGER'] = {
    'title': 'Document OCR API',
    'uiversion': 3,
    'description': (
        'A RESTful API for extracting structured data from identity documents and financial records. '
        'Supports passport MRZ extraction, NIN slip/card processing, and bank statement analysis. '
        'X-Signature and X-Timestamp headers are required unless the Flask app is running in debug mode.'
    ),
}
Swagger(app, config=swagger_config)

app.register_blueprint(passport_bp, url_prefix='/api')
app.register_blueprint(forwarder_bp, url_prefix='/api')

@app.route('/')
def health_check():
    """Return a small JSON response that load balancers can use as a health check."""
    return {"status": "healthy", "message": "Document OCR API is live"}, 200

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5005)



