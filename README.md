# Document OCR API

A Flask API for extracting structured data from identity documents, business-registration documents, financial records, and utility proof-of-address documents. The project supports passport MRZ extraction, Nigerian NIN card/slip parsing, jurisdiction-aware business-document extraction, bank statement parsing, utility bill/receipt parsing, and an optional generic webhook forwarder.

The codebase is organized so new document types and country-specific rules can be added without reshaping the whole service.

## Features

- Passport MRZ extraction with TD3 validation and image-quality checks.
- Nigerian NIN card and slip parsing with normalized response fields.
- Bank statement summary extraction from PDFs and images.
- Utility bill and payment receipt extraction with address, receipt date, and month-age calculation.
- Optional webhook forwarding to up to three configured targets.
- Swagger UI at `/api-docs`.
- HMAC request-signing utilities for production authentication.
- OCR backend abstraction with RapidOCR first and optional EasyOCR fallback.
- Business-document classification, jurisdiction detection, typed identifiers, field-level confidence/evidence, conflict reporting, and generic fallback extraction.

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
      business_document/
      drivers_license/
      nin/
      passport/
      utility_bill/
      voter_id/
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

BUSINESS_DOCUMENT_MAX_PAGES=20
BUSINESS_DOCUMENT_MAX_UPLOAD_BYTES=20971520
BUSINESS_DOCUMENT_MAX_IMAGE_PIXELS=25000000
BUSINESS_DOCUMENT_MAX_PAGE_TEXT_CHARS=100000
BUSINESS_DOCUMENT_COMPARE_RENDERED_PDF_TEXT=1
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

### Utility Bill / Receipt Extraction

```http
POST /api/utility-bill
```

Form data:

- `file`: utility bill or utility payment receipt image/PDF
- `country` (optional): ISO-3166 alpha-3 country code. Defaults to `NGA`.

The utility bill response focuses on proof-of-address checks. It returns the
service address, receipt/bill date, `days_old`, `months_old`, and an `is_recent`
flag based on a 90-day freshness window. Older receipts can still return
`success: true` when the address and date are readable; consumers can decide how
to enforce freshness from `is_recent`.

Example:

```bash
curl -X POST http://localhost:5005/api/utility-bill ^
  -F "file=@utility_receipt.jpg" ^
  -F "country=NGA"
```

### Voter ID / Voter Card Extraction

```http
POST /api/voter-id
```

Form data:

- `file`: voter document image, or PDF with embedded text
- `country` (optional): ISO-3166 alpha-3 country code. Defaults to `NGA`.

`voter_id` is the canonical processor name. Country metadata keeps local naming
clear: Nigeria exposes `VOTER_CARD`, while Ghana exposes `VOTER_ID`.

### Driver's License Extraction

```http
POST /api/drivers-license
```

Form data:

- `file`: driver's license image, or PDF with embedded text
- `country` (optional): ISO-3166 alpha-3 country code. Defaults to `NGA`.

For these newer identity processors, the runtime flow is:

```text
Flask route -> shared document processor -> text_extraction.py -> country parser
```

For example, `/api/voter-id` calls `src/document_ocr/voter_id/processor.py`.
That processor calls `src/document_ocr/text_extraction.py` to convert the upload
into text, then dispatches to `src/countries/nigeria/voter_id.py` or
`src/countries/ghana/voter_id.py`.

### Business-document extraction

```http
POST /api/business-document
```

Form data:

- `file`: PDF, JPEG, PNG, TIFF, BMP, or WebP business document
- `country` (optional): country code or registered country alias, for example `NGA` or `USA`
- `jurisdiction` (optional): state, province, or other subnational jurisdiction hint
- `document_type` (optional): taxonomy code, for example `CERTIFICATE_OF_INCORPORATION`

The response uses one global schema for certificates, registry extracts, status reports, constitutional documents, tax certificates, and unknown business records. It includes typed registration/tax identifiers, confidence and evidence by field, warnings and retained conflicts, page-extraction diagnostics, raw OCR text, and unclassified label/value fields. Nigeria and United States profiles are built in; unknown jurisdictions use the generic fallback.

```bash
curl -X POST http://localhost:5005/api/business-document \
  -F "file=@certificate.pdf" \
  -F "country=NGA"
```

See [Business-document OCR](docs/business_document_ocr.md) for the complete response contract, supported taxonomy, profile-extension example, limits, privacy guidance, and known limitations.

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
  - Voter card parser support.
  - Driver's license parser support.
  - Additional local ID metadata: BVN and Tax Identification Number.
  - Basic NIN format validation for exactly 11 digits.
- `GHA` / Ghana
  - Passport MRZ country-code alias correction, such as `6HA` to `GHA`.
  - Voter ID parser support.
  - Driver's license parser support.
  - Starter local ID metadata: Ghana Card, Tax Identification Number, and SSNIT number.

Processor naming rule:

- Use one canonical folder for the shared document family, such as `document_ocr/voter_id`.
- Put local country names in `src/countries/<country>/rules.py`.
- Put country-specific parsing differences in `src/countries/<country>/<document>.py`.

Example:

```text
src/
  document_ocr/
    voter_id/
      processor.py
  countries/
    nigeria/
      voter_id.py      # Parses Nigeria Voter Card
      rules.py         # Exposes local code VOTER_CARD
    ghana/
      voter_id.py      # Parses Ghana Voter ID
      rules.py         # Exposes local code VOTER_ID
```

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

For local development, install the test dependencies with:

```bash
pip install -r requirements-dev.txt
```

Run the complete local quality gate with:

```bash
ruff format --check app.py src/api/routes.py src/document_ocr/business_document src/document_ocr/text_extraction.py tests/test_business_document_*.py
ruff check app.py src/api/routes.py src/document_ocr/business_document src/document_ocr/text_extraction.py tests/test_business_document_*.py
mypy
python -m pytest tests -v
```

Tests cover route behavior, parser rules, country profiles, page-aware text extraction, and legacy endpoint regressions using sanitized synthetic fixtures. OCR accuracy against real-world layouts still requires a controlled, legally usable document corpus.

## Security Notes

- The API processes uploads in memory and does not persist documents by default.
- Business-document responses intentionally contain raw OCR text and evidence; treat them as sensitive and do not log them.
- Use a strong `OCR_SECRET_KEY` before production deployment.
- Re-enable HMAC verification before public exposure.
- Put rate limiting and upload-size limits at the reverse proxy or gateway layer.
- Webhook logs redact common sensitive headers.

## License

MIT
