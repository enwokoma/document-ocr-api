# Document OCR API

A Flask API for extracting structured data from identity documents and financial records. The project currently supports passport MRZ extraction, Nigerian NIN card/slip parsing, bank statement parsing, and an optional generic webhook forwarder.

The codebase is organized so new document types and country-specific rules can be added without reshaping the whole service.

## Features

- Passport MRZ extraction with TD3 validation and image-quality checks.
- Nigerian NIN card and slip parsing with normalized response fields.
- Bank statement summary extraction from PDFs and images.
- Optional webhook forwarding to up to three configured targets.
- Swagger UI at `/api-docs`.
- HMAC request-signing utilities for production authentication.
- OCR backend abstraction with RapidOCR first and optional EasyOCR fallback.

## Project Structure

```text
document-ocr-api/
  app.py
  requirements.txt
  src/
    api/
      routes.py
    countries/
      profile.py
      registry.py
      ghana/
      nigeria/
    core/
      auth.py
      flash_glance.py
      ocr_engine.py
    document_ocr/
      bank_statement/
      nin/
      passport/
    webhook_forwarder/
      broadcast.py
      routes.py
      signing.py
  tests/
```

## Requirements

- Python 3.11 or 3.12
- pip
- RapidOCR dependencies from `requirements.txt`

RapidOCR is the preferred OCR backend. EasyOCR can be enabled as a fallback with `ENABLE_EASYOCR_FALLBACK=1`, but it is slower.

## Setup

```bash
git clone https://github.com/YOUR_USERNAME/document-ocr-api.git
cd document-ocr-api
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

On Linux or macOS, activate the environment with:

```bash
source venv/bin/activate
```

## Configuration

Create a `.env` file from `.env.example`.

```env
OCR_SECRET_KEY=change-this-in-production

FORWARDER_SECRET=change-this-if-webhook-forwarding-is-enabled
FORWARDER_TARGET_1_URL=https://endpoint1.example.com/webhook
FORWARDER_TARGET_2_URL=https://endpoint2.example.com/webhook
FORWARDER_TARGET_3_URL=https://endpoint3.example.com/webhook

ENABLE_EASYOCR_FALLBACK=0
```

`FORWARDER_*` settings are only required if `/api/webhooks/forward` is used.

## Running

```bash
python app.py
```

The API runs on `http://localhost:5005`.

Swagger UI is available at:

```text
http://localhost:5005/api-docs
```

For production-style serving:

```bash
gunicorn --bind 0.0.0.0:5005 --workers 4 --timeout 120 app:app
```

## Endpoints

### Health Check

```http
GET /
```

Returns:

```json
{
  "status": "healthy",
  "message": "Document OCR API is live"
}
```

### Passport Extraction

```http
POST /api/passport
POST /api/scan-passport
```

Form data:

- `file`: passport image
- `country` (optional): ISO-3166 alpha-3 country hint, for example `NGA`

Example:

```bash
curl -X POST http://localhost:5005/api/passport ^
  -F "file=@passport.jpg"
```

### NIN Extraction

```http
POST /api/nin
```

Form data:

- `file`: NIN card or slip image
- `country` (optional): ISO-3166 alpha-3 country code. Defaults to `NGA`.

### Bank Statement Extraction

```http
POST /api/bank-statement
```

Form data:

- `file`: PDF or image bank statement

### Country Metadata

```http
GET /api/countries
GET /api/countries/{country_code}
```

Returns registered countries and their local identity document metadata. This is
metadata only; a listed ID does not automatically mean an OCR parser exists for
that exact ID yet.

Example:

```bash
curl http://localhost:5005/api/countries/NGA
```

Example response fragment:

```json
{
  "success": true,
  "country": {
    "country_code": "NGA",
    "country_name": "Nigeria",
    "supported_identity_documents": [
      {"code": "NIN_CARD", "name": "National Identification Number card"},
      {"code": "VOTER_CARD", "name": "Permanent voter card"},
      {"code": "DRIVERS_LICENSE", "name": "Driver's license"}
    ]
  }
}
```

### Webhook Forwarding

```http
POST /api/webhooks/forward
```

Receives a raw request body, signs it with `FORWARDER_SECRET`, and forwards it to configured targets.

Forwarded requests include:

- `X-Timestamp`
- `X-Signature`
- `X-Source: webhook-forwarder`
- `Content-Type`, when provided by the original request
- `X-Request-Id` or `X-Correlation-Id`, when provided

The forwarder keeps a short in-memory dedupe cache for repeated payloads.

## HMAC Authentication

The request auth decorator is present in `src/core/auth.py`. It expects:

```text
X-Timestamp: current Unix timestamp
X-Signature: HMAC_SHA256(OCR_SECRET_KEY, "{timestamp}.{path}")
```

Authentication is currently bypassed in code while OCR behavior is being developed. Re-enable it before exposing the API publicly.

## Extending The API

For a new document type:

1. Add a processor under `src/document_ocr/<document_type>/processor.py`.
2. Keep the processor response shape consistent: `success`, `message`, `document_type`, `data`, and optional diagnostics.
3. Add the route in `src/api/routes.py`.
4. Add focused tests for missing files, invalid inputs, and a known-good sample.

For country-specific logic:

1. Create a country package under `src/countries/<country>/`, for example `src/countries/nigeria/`.
2. Put country-specific aliases, supported document types, and validation helpers in that package.
3. Register the country's `CountryProfile` in `src/countries/registry.py`.
4. Keep shared OCR/parsing in the document processor.
5. Return country codes and validation details explicitly in the response.

Current country-specific support:

- `NGA` / Nigeria
  - Passport MRZ country-code alias correction, such as `N6A` or `NG4` to `NGA`.
  - Nigerian NIN card/slip metadata and parser support.
  - Additional local ID metadata: voter card, driver's license, BVN, and Tax Identification Number.
  - Basic NIN format validation for exactly 11 digits.
- `GHA` / Ghana
  - Passport MRZ country-code alias correction, such as `6HA` to `GHA`.
  - Starter local ID metadata: Ghana Card, voter ID, driver's license, Tax Identification Number, and SSNIT number.
  - No Ghana-specific OCR parser has been implemented yet; this profile is a template for adding one.

Example response fragment for country-aware endpoints:

```json
{
  "country": {
    "country_code": "NGA",
    "country_name": "Nigeria",
    "supported": true,
    "checks": {
      "document_type_supported": true,
      "nin_format_valid": true
    }
  }
}
```

When adding another country, keep the shape similar to `src/countries/ghana/rules.py`:

```python
from src.countries.profile import CountryProfile

COUNTRY_PROFILE = CountryProfile(
    code="ABC",
    name="Example Country",
    mrz_code_aliases={"ABC"},
    supported_identity_documents={
        "NATIONAL_ID": "National identity card",
        "VOTER_ID": "Voter identity card",
    },
)
```

## Tests

```bash
python -m pytest tests -v
```

The included tests are smoke tests for route availability and basic error behavior. Full OCR accuracy tests should use controlled sample documents.

## Security Notes

- The API processes uploads in memory and does not persist documents by default.
- Use a strong `OCR_SECRET_KEY` before production deployment.
- Re-enable HMAC verification before public exposure.
- Put rate limiting and upload-size limits at the reverse proxy or gateway layer.
- Webhook logs redact common sensitive headers.

## License

MIT
