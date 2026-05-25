# Document OCR API

A comprehensive, scalable Python Flask API for extracting structured data from identity documents and financial records using advanced OCR, machine learning, and image processing techniques.

## 🎯 Overview

Document OCR API provides a robust, production-ready solution for automated document processing. It combines state-of-the-art OCR engines, intelligent validation algorithms, and quality assessment to deliver accurate, reliable data extraction from:

- **Passports**: Machine Readable Zone (MRZ) extraction with validation
- **National ID Cards**: Nigerian NIN slips and cards (extensible to other countries)
- **Financial Documents**: Bank statements and transaction records
- **Webhook Integration**: Optional webhook forwarding for event-driven workflows

## ✨ Features

### Document Processing
- **Passport MRZ Extraction**: Extract and validate Machine Readable Zone data from TD3 passport pages
- **NIN Card & Slip Processing**: Extract data from Nigerian National Identification Number documents with support for both card and slip formats
- **Bank Statement Analysis**: Extract key financial data from PDF and image-based bank statements
- **Multi-Language Support**: OCR processing optimized for multiple document standards and languages

### Quality & Validation
- **Image Quality Detection**: Automatic detection of glare, blur, and other image quality issues
- **Document Type Classification**: Automatic classification of document types
- **Data Validation**: Check-digit validation for MRZ and NIN data
- **Liveness Detection**: Detect scanned vs. live-photography documents

### API Features
- **REST API**: Clean, documented RESTful endpoints
- **HMAC Request Signing**: Optional request authentication with HMAC-SHA256
- **Webhook Management**: Generic webhook forwarder for event distribution
- **Extensible Architecture**: Designed to support additional document types and countries
- **Swagger/OpenAPI Documentation**: Interactive API documentation at `/api-docs`

## 📋 Requirements

- Python 3.11 or higher
- pip or conda
- 500MB+ disk space (for OCR models)

### OCR Backends
- **RapidOCR (Recommended)**: `rapidocr-onnxruntime` - Fast, accurate, low-latency
- **EasyOCR (Fallback)**: Slower but works without additional dependencies

## 🚀 Installation

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/document-ocr-api.git
cd document-ocr-api
```

### 2. Create Virtual Environment

```bash
python -m venv venv

# On Linux/macOS:
source venv/bin/activate

# On Windows:
venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment

Create a `.env` file in the project root:

```env
# OCR API Configuration
OCR_SECRET_KEY=your-secret-key-here

# Webhook Forwarder Configuration (optional)
FORWARDER_SECRET=your-webhook-forwarder-secret
FORWARDER_TARGET_1_URL=https://endpoint1.example.com/webhook
FORWARDER_TARGET_2_URL=https://endpoint2.example.com/webhook
FORWARDER_TARGET_3_URL=https://endpoint3.example.com/webhook

# Optional: Enable EasyOCR fallback
ENABLE_EASYOCR_FALLBACK=0
```

## ▶️ Running the API

### Development Mode

```bash
python app.py
```

The API will start on `http://localhost:5005` with hot-reload enabled.

### Production Deployment

```bash
gunicorn --bind 0.0.0.0:5005 --workers 4 --timeout 120 app:app
```

### API Documentation

Interactive API docs available at: `http://localhost:5005/api-docs`

## 📚 API Endpoints

### Health Check
```
GET /
```
Check API health status.

**Response:**
```json
{
  "status": "healthy",
  "message": "Document OCR API is live"
}
```

### Passport Extraction

#### Extract Passport MRZ
```
POST /api/passport
```

Extract Machine Readable Zone and visual field data from a passport photograph.

**Parameters:**
- `file` (form-data, required): Passport image file (JPEG, PNG)
- `X-Signature` (header, optional): HMAC signature for request authentication
- `X-Timestamp` (header, optional): Unix timestamp for request authentication

**Response (Success):**
```json
{
  "success": true,
  "verification": {
    "is_valid_format": true,
    "is_nigerian_passport": true,
    "document_type": "P",
    "issuing_country": "NGA"
  },
  "data": {
    "surname": "DOE",
    "given_names": "JOHN",
    "passport_number": "A12345678",
    "nationality": "NGA",
    "date_of_birth": "1990-01-15",
    "gender": "Male",
    "date_of_expiry": "2030-01-14",
    "date_of_issue": "2020-01-15",
    "nin": "12345678901"
  },
  "mrz_raw": ["P<NGADOE<<JOHN<....", "A123456781NGA900115M3001141234567890112<<2"],
  "flash_glance": {
    "bright_pct": 5.2,
    "flashy": false
  }
}
```

**Response (Error):**
```json
{
  "success": false,
  "message": "Passport image rejected. The upload looks like a scanned, copied, or screenshot passport page...",
  "quality": {
    "scan_like": {...},
    "live_capture_context": {...}
  }
}
```

#### Legacy Passport Endpoint
```
POST /api/scan-passport
```
Same as `/api/passport` - maintained for backwards compatibility.

### NIN Extraction

```
POST /api/nin
```

Extract data from Nigerian National Identification Number (NIN) slips or cards.

**Parameters:**
- `file` (form-data, required): NIN document image (JPEG, PNG)
- `X-Signature` (header, optional): HMAC signature
- `X-Timestamp` (header, optional): Unix timestamp

**Response (Success):**
```json
{
  "success": true,
  "document_type": "NIN_SLIP",
  "data": {
    "nin": "12345678901",
    "tracking_id": "ABC123XYZ456",
    "surname": "DOE",
    "first_name": "JOHN",
    "middle_name": "MICHAEL",
    "other_names": null,
    "full_name": "JOHN MICHAEL DOE",
    "gender": "M",
    "date_of_birth": "1990-01-15",
    "date_issued": "2021-06-20",
    "address": "123 Main Street, Lagos, Nigeria"
  },
  "raw_text": "..."
}
```

### Bank Statement Extraction

```
POST /api/bank-statement
```

Extract financial summary data from bank statements (PDF or image).

**Parameters:**
- `file` (form-data, required): Bank statement file (PDF, JPEG, PNG)
- `X-Signature` (header, optional): HMAC signature
- `X-Timestamp` (header, optional): Unix timestamp

**Response:**
```json
{
  "success": true,
  "document_type": "BANK_STATEMENT",
  "data": {
    "account_number": "1234567890",
    "account_name": "JOHN DOE",
    "bank_name": "Example Bank",
    "opening_balance": "10000.00",
    "closing_balance": "15500.50",
    "start_date": "01/01/2024",
    "end_date": "31/01/2024"
  }
}
```

### Webhook Forwarding (Optional)

```
POST /api/webhooks/forward
```

Receive a webhook payload and forward it to configured target endpoints with cryptographic signing.

**Parameters:**
- Raw JSON/form body
- `X-Request-Id` (header, optional): Correlation ID
- `X-Correlation-Id` (header, optional): Correlation ID

**Response:**
```json
{
  "success": true,
  "deduped": false,
  "forwarded": [
    {
      "name": "target_1",
      "url": "https://endpoint1.example.com/webhook",
      "ok": true,
      "status_code": 200,
      "error": null,
      "response_preview": "{...}"
    },
    {
      "name": "target_2",
      "url": "https://endpoint2.example.com/webhook",
      "ok": false,
      "status_code": 500,
      "error": "Server error",
      "response_preview": null
    },
    {
      "name": "target_3",
      "url": "",
      "ok": false,
      "status_code": null,
      "error": "Missing target URL",
      "response_preview": null
    }
  ]
}
```

## 🔧 Configuration Reference

### Environment Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `OCR_SECRET_KEY` | string | `dev-secret-change-in-production` | HMAC secret for request signing verification |
| `FORWARDER_SECRET` | string | (empty) | Secret for signing forwarded webhooks |
| `FORWARDER_TARGET_1_URL` | URL | (empty) | Primary webhook target endpoint |
| `FORWARDER_TARGET_2_URL` | URL | (empty) | Secondary webhook target endpoint |
| `FORWARDER_TARGET_3_URL` | URL | (empty) | Tertiary webhook target endpoint |
| `ENABLE_EASYOCR_FALLBACK` | bool | `0` | Enable EasyOCR if RapidOCR unavailable |

### Request Authentication (HMAC)

When enabled, requests require HMAC-SHA256 signatures:

```
Payload = "{timestamp}.{path}"
X-Signature = HMAC_SHA256(OCR_SECRET_KEY, Payload)
X-Timestamp = current_unix_timestamp
```

**Note:** Currently disabled by default (see `src/core/auth.py`).

## 🏗️ Architecture

### Module Structure

```
document-ocr-api/
├── src/
│   ├── api/                      # REST API endpoints
│   │   └── routes.py
│   ├── core/                     # Core utilities
│   │   ├── auth.py              # HMAC authentication
│   │   ├── ocr_engine.py        # OCR abstraction layer
│   │   └── flash_glance.py      # Flash/glare detection
│   ├── document_ocr/            # Document processors
│   │   ├── passport/            # Passport MRZ extraction
│   │   ├── nin/                 # NIN card/slip processing
│   │   └── bank_statement/      # Bank statement analysis
│   └── webhook_forwarder/       # Webhook routing (optional)
│       ├── routes.py
│       ├── signing.py           # Payload signing
│       └── broadcast.py         # Multi-target forwarding
├── app.py                       # Flask application entry point
├── requirements.txt             # Python dependencies
└── README.md
```

### OCR Engine

The OCR engine automatically selects the best available backend:

1. **RapidOCR (ONNX Runtime)** - Recommended (fastest, most accurate)
2. **RapidOCR (Standard)** - Fallback
3. **EasyOCR** - Slower alternative (requires `ENABLE_EASYOCR_FALLBACK=1`)

## 🌍 Extensibility & Multi-Country Support

The architecture is designed to support expansion to additional document types and countries:

### Adding Support for New Document Types

1. Create `src/document_ocr/{document_type}/processor.py`
2. Implement extraction and validation logic
3. Add endpoint to `src/api/routes.py`
4. Register blueprint in `app.py`

### Adding Country-Specific Validations

1. Create country-specific validation module: `src/document_ocr/{document_type}/{country}.py`
2. Implement country-specific check-digit, format, and field validation
3. Integrate with the main processor

### Planned Enhancements

- [ ] Support for international passports (all countries)
- [ ] African ID cards (Ghana, Kenya, South Africa, etc.)
- [ ] Driver's licenses
- [ ] Visa documents
- [ ] Business registration documents
- [ ] Machine learning-based field classification
- [ ] Real-time validation against government databases (OAuth integration)

## 🔐 Security Considerations

- **No Data Storage**: The API processes documents in-memory only; no persistence by default
- **HMAC Signing**: Optional request authentication prevents replay attacks
- **Sensitive Header Redaction**: Logs redact auth headers and API keys
- **Webhook Deduplication**: Prevents duplicate webhook processing (60-second TTL)
- **CORS**: Configure as needed for production deployments
- **Rate Limiting**: Implement at reverse proxy or API gateway level

## 🧪 Testing

Run tests for current document processors:

```bash
python -m pytest tests/ -v
```

Interactive manual testing:

```bash
curl -X POST http://localhost:5005/api/passport \
  -F "file=@passport.jpg" \
  -H "X-Signature: <signature>" \
  -H "X-Timestamp: $(date +%s)"
```

## 📊 Performance Notes

- **Passport Extraction**: ~2-5 seconds per image (depending on OCR backend and image quality)
- **NIN Extraction**: ~2-4 seconds per image
- **Bank Statement Analysis**: ~3-8 seconds per PDF (3-page average)
- **Memory Usage**: ~1-2 GB with RapidOCR loaded
- **Concurrency**: Suitable for 10-20 concurrent requests per worker

## 📝 License

This project is licensed under the MIT License.

See the `LICENSE` file for full details.

## 🤝 Contributing

Contributions are welcome! Please follow these guidelines:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Commit changes: `git commit -am 'Add your feature'`
4. Push to branch: `git push origin feature/your-feature`
5. Submit a Pull Request

## 📧 Support & Contact

For questions, issues, or feature requests, please open a GitHub issue or contact the maintainers.

---

**Last Updated:** May 2026
**Maintained by:** The Document OCR Team


