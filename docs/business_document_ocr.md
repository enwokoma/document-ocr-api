# Business-document OCR

The business-document endpoint extracts a reviewable, jurisdiction-aware company record from registration certificates, formation filings, registry reports, constitutional documents, tax certificates, and related business records. It uses deterministic OCR and parsing rules; it does not query or validate against a government registry.

## Endpoint

`POST /api/business-document` accepts either a multipart upload or a document URL in a form/JSON request body. Provide exactly one source.

| Field | Required | Meaning |
| --- | --- | --- |
| `file` | conditional | PDF or supported image upload; mutually exclusive with `url` |
| `url` / `document_url` | conditional | Public HTTPS PDF/image URL; mutually exclusive with `file` |
| `country` | no | ISO alpha-3 code, registered alpha-2 alias, or registered country-name alias |
| `jurisdiction` | no | State, province, or other subnational incorporation-jurisdiction hint |
| `document_type` | no | A code from the business-document taxonomy, such as `CERTIFICATE_OF_INCORPORATION` |

Hints are advisory. Strong document evidence wins over a conflicting hint, and the response records a warning. An unsupported hint is ignored with a warning. A valid document-type hint is used at moderate confidence only when text classification is otherwise unknown.

The shared route authentication convention applies. In deployments where HMAC verification is enabled, send the configured signature and timestamp headers.

Sanitized request example:

```bash
curl -X POST http://localhost:5005/api/business-document \
  -H "X-Signature: <computed-signature>" \
  -H "X-Timestamp: <unix-timestamp>" \
  -F "file=@sample-company-certificate.pdf" \
  -F "country=NGA" \
  -F "document_type=CERTIFICATE_OF_INCORPORATION"
```

The equivalent URL request is:

```bash
curl -X POST http://localhost:5005/api/business-document \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://files.example.com/sample-company-certificate.pdf",
    "country": "NGA",
    "document_type": "CERTIFICATE_OF_INCORPORATION"
  }'
```

The URL must be in the request body, not the query string. The [README shared document-input section](../README.md#shared-document-input) documents remote URL policy, limits, aliases, security controls, and configuration.

PowerShell example for the local Nigerian samples (adjust the port if the server was started on `5000` instead of the application's default `5005`):

```powershell
curl.exe -X POST "http://localhost:5005/api/business-document" `
  -F "file=@C:\path\to\document-ocr-api\CAC.pdf" `
  -F "country=NGA"
```

Use the same endpoint for all three samples. The document-type hint is optional; classification is performed from the extracted text.

| Local sample | Expected type |
| --- | --- |
| `CAC.pdf` | `CERTIFICATE_OF_INCORPORATION` |
| `MEMART.pdf` | `MEMORANDUM_AND_ARTICLES_OF_ASSOCIATION` |
| `StatusReport.pdf` | `COMPANY_STATUS_REPORT` |

The current `MEMART.pdf` has 28 pages, while the safe default processes 20. To extract all of it, set `BUSINESS_DOCUMENT_MAX_PAGES=40` before starting the server; the configured decoded-pixel and request-size limits still apply.

The shared source resolver accepts PDF, JPEG, PNG, TIFF, BMP, and WebP signatures and normalizes the filename extension from the content signature. Invalid, empty, or unreadable uploads return HTTP 400. Unsupported remote content returns HTTP 415, unsafe destinations return HTTP 403, upstream retrieval failures return HTTP 502/504, and size-limit failures return HTTP 413. A parsed document returns HTTP 200 even when its type or jurisdiction is unknown; callers must inspect confidence and warnings. An unhandled server error returns HTTP 500.

The HTTP route adds a `request_id` to the processor response. JSON indentation is configured application-wide, so business-document, legacy OCR, metadata, health, webhook, and JSON error responses are all human-readable.

## Architecture and processing flow

```text
Flask route
  -> shared bounded upload-or-URL resolution and content-signature inspection
  -> business upload inspection
  -> page-aware embedded-PDF text / rendered OCR selection
  -> document classification
  -> country and subdivision detection
  -> language detection
  -> core + generic + structured-section parsers
  -> confidence-aware candidate merge
  -> typed identifier extraction
  -> normalization and validation warnings
  -> one canonical response serialization
```

The main modules have deliberately narrow responsibilities:

- `src/core/document_source.py` handles the universal upload/URL contract, content signatures, URL policy, bounded streaming, and temporary-resource cleanup.
- `src/api/routes.py` applies the shared source resolver, optional hints, status codes, request IDs, and metadata-only logging.
- `config.py` and `upload.py` enforce bounded processing and verify file signatures.
- `text_extraction.py` is the shared page-aware PDF/image OCR layer.
- `classification.py` provides an explainable business-document taxonomy result.
- `profiles.py` and `jurisdictions.py` detect countries, registries, and configured subdivisions.
- `language.py` provides best-effort language hints.
- `fields.py`, `generic.py`, and `sections.py` produce partial field candidates and bounded evidence.
- `identifiers.py` extracts multiple jurisdiction-neutral identifier objects while retaining local number types.
- `processor.py` reconciles hints, merges partial candidates before canonicalization, records conflicts, and computes overall confidence.
- `schema.py` emits the stable response shape.

No database, object store, or queue is used by this pipeline. Processing is synchronous within the request.

## Canonical response

After shared authentication succeeds, extraction, validation, size-limit, and unhandled-error responses use the same top-level business-document shape. Authentication middleware can return the repository's shorter shared 401 response. The shortened example below is sanitized; omitted canonical data fields are still emitted as `null`, empty lists, or an empty nested object as appropriate.

```json
{
  "success": true,
  "message": null,
  "document_type": "CERTIFICATE_OF_INCORPORATION",
  "overall_confidence": 0.91,
  "confidence_level": "HIGH",
  "classification": {
    "document_type": "CERTIFICATE_OF_INCORPORATION",
    "confidence": 0.94,
    "confidence_level": "HIGH",
    "matched_terms": ["certificate of incorporation"],
    "alternatives": [],
    "ambiguous": false,
    "source": "document_text"
  },
  "jurisdiction": {
    "country_code": "NGA",
    "country_name": "Nigeria",
    "registry_name": "Corporate Affairs Commission",
    "source": "country_hint_and_document",
    "confidence": 1.0,
    "confidence_level": "HIGH",
    "requested_country_code": "NGA",
    "detected_country_code": "NGA",
    "matched_terms": ["Corporate Affairs Commission"],
    "conflict": false,
    "ambiguous": false,
    "alternatives": [],
    "subdivision": null
  },
  "data": {
    "legal_company_name": "EXAMPLE HOLDINGS LIMITED",
    "trading_name": null,
    "entity_type": "LIMITED_COMPANY",
    "country_of_incorporation": "Nigeria",
    "country_code": "NGA",
    "jurisdiction_of_incorporation": null,
    "jurisdiction_code": null,
    "incorporation_date": "2024-01-15",
    "registration_date": null,
    "incorporation_or_registration_date": "2024-01-15",
    "registered_office_address": "1 Example Road, Sample City",
    "principal_business_address": null,
    "identifiers": [
      {
        "type": "COMPANY_REGISTRATION_NUMBER",
        "number_type": "CAC_RC",
        "value": "RC 1234567",
        "normalized_value": "RC1234567",
        "issuing_authority": "Corporate Affairs Commission",
        "country_code": "NGA",
        "jurisdiction": null,
        "confidence": 0.97,
        "evidence": [
          {
            "text": "RC 1234567",
            "start": 120,
            "end": 130,
            "page": 1,
            "method": "jurisdiction_identifier_pattern",
            "pattern_label": "CAC registered company number"
          }
        ],
        "source": "jurisdiction_profile",
        "is_primary": true
      }
    ],
    "issuing_authority": "Corporate Affairs Commission",
    "document_issue_date": "2024-01-15",
    "document_reference_number": null,
    "company_status": null,
    "directors": [],
    "shareholders": [],
    "beneficial_owners": [],
    "parties": [],
    "share_capital": {
      "currency": null,
      "currency_raw": null,
      "currency_candidates": [],
      "amount": null,
      "amount_text": null,
      "authorized_amount": null,
      "issued_amount": null,
      "paid_up_amount": null,
      "stated_amount": null,
      "share_count": null,
      "issued_share_count": null,
      "nominal_value_per_share": null,
      "share_class": null,
      "share_classes": []
    },
    "business_activities": [],
    "objects_or_purpose": [],
    "governing_law": "Companies and Allied Matters Act 2020",
    "contact_email": null,
    "contact_phone": null,
    "document_language": {
      "code": "en",
      "name": "English",
      "confidence": 0.87,
      "source": "document_text"
    },
    "additional_fields": []
  },
  "field_confidence": {
    "legal_company_name": {"score": 0.98, "level": "HIGH"}
  },
  "evidence": {
    "legal_company_name": [
      {
        "field": "legal_company_name",
        "value": "EXAMPLE HOLDINGS LIMITED",
        "method": "company_name_label",
        "confidence": 0.98,
        "confidence_level": "HIGH",
        "page": 1,
        "text": "Company Name: EXAMPLE HOLDINGS LIMITED",
        "source": null,
        "selected": true
      }
    ]
  },
  "warnings": [],
  "conflicts": [],
  "extraction": {
    "file_type": "pdf",
    "size_bytes": 20480,
    "pages_processed": 1,
    "total_pages": 1,
    "truncated": false,
    "pages": [
      {"page": 1, "source": "embedded_pdf_text", "ocr_confidence": null, "text_length": 840}
    ]
  },
  "raw_text": "<complete extracted OCR text>",
  "request_id": "<route-generated-id>"
}
```

The canonical `data` object covers legal and trading names, entity type, country and subnational jurisdiction, incorporation/registration dates, addresses, identifiers, issuing authority, document dates and references, status, company parties, capital, activities and objects, contact details, language, and unclassified fields.

## Confidence, evidence, conflicts, and warnings

Confidence is extraction confidence, not proof that a registry record is authentic or current.

- `overall_confidence` combines classification, jurisdiction, key-field, and identifier signals.
- `classification.confidence` and `jurisdiction.confidence` describe their respective inference stages.
- `field_confidence` contains the strongest selected evidence score for each extracted field.
- `evidence` groups all bounded field candidates. `selected` distinguishes the value used in `data` from retained alternatives.
- Identifier objects carry their own confidence and evidence because several identifier systems may coexist in one document.
- `conflicts` retains materially different candidates and describes how one was selected. Identifier conflicts use the identifier type/local number type and candidate values.
- `warnings` are human-readable review signals for missing, uncertain, ambiguous, truncated, future-dated, or conflicting results. They do not necessarily mean the request failed.

Confidence levels are `HIGH` at 0.85 or above, `MEDIUM` at 0.65, `LOW` at 0.40, and `REJECT` below 0.40.

The processor canonicalizes only after partial results have been merged. Empty or lower-confidence candidates do not blindly overwrite stronger values; list fields are combined and nested capital data is merged separately.

## Typed identifiers

Do not assume that every jurisdiction has one interchangeable registration number. Each `data.identifiers` entry uses one canonical type and may also provide a jurisdiction-specific `number_type`.

Canonical identifier types are:

- `COMPANY_REGISTRATION_NUMBER`
- `BUSINESS_REGISTRATION_NUMBER`
- `TAX_IDENTIFIER`
- `EMPLOYER_IDENTIFIER`
- `REGISTRY_NUMBER`
- `STATE_FORMATION_IDENTIFIER`
- `DOCUMENT_REFERENCE_NUMBER`
- `OTHER`

`value` preserves a readable representation; `normalized_value` supports comparison and deduplication. `issuing_authority`, `country_code`, `jurisdiction`, `source`, confidence, and evidence explain the designation. Distinct values with the same local designation are retained as a conflict rather than silently collapsed.

## Generic fallback and unclassified fields

Unknown document types and unprofiled jurisdictions remain parseable. The generic layer extracts conservative label/value pairs, portable fields, generic typed identifiers, and an inferred issuing authority where possible. The response remains successful when text was extracted, but uses `UNKNOWN_BUSINESS_DOCUMENT` and adds a warning when classification is unreliable.

Unrecognized label/value pairs are retained in `data.additional_fields` as:

```json
{
  "label": "Local Registry Category",
  "value": "Example Value",
  "confidence": 0.82,
  "evidence": {
    "method": "generic_label_value",
    "page": 1,
    "text": "Local Registry Category: Example Value"
  }
}
```

Additional fields are bounded to 50 entries. Reaching the limit produces a warning.

## Extending jurisdiction profiles

Profiles contain trusted detection markers, identifier patterns, aliases, and optional subdivisions. They do not contain arbitrary parsing code. Register application profiles during startup, before serving concurrent requests. Duplicate country codes fail unless `replace=True` is explicitly requested.

The following sanitized example is structurally valid but uses placeholder registry wording. Replace markers and identifier formats with authoritative, tested rules for the target jurisdiction.

```python
from src.document_ocr.business_document.identifiers import (
    IdentifierPattern,
    IdentifierType,
    RegistrationPattern,
)
from src.document_ocr.business_document.profiles import (
    AuthorityMarker,
    BusinessJurisdictionProfile,
    register_business_profile,
)


profile = BusinessJurisdictionProfile(
    code="IRL",
    name="Ireland",
    registry_name="Example Registry Authority",
    aliases=("IE", "IRELAND"),
    authority_markers=(
        AuthorityMarker(
            "Example registry authority",
            r"\bEXAMPLE\s+REGISTRY\s+AUTHORITY\b",
            6.0,
        ),
    ),
    registration_patterns=(
        RegistrationPattern(
            pattern=r"\bCOMPANY\s+NUMBER\s*[:#-]?\s*(?P<number>\d{6,8})\b",
            number_type="EXAMPLE_COMPANY_NUMBER",
            identifier_type=IdentifierType.COMPANY_REGISTRATION_NUMBER,
            confidence=0.94,
            label="example company number",
        ),
    ),
    identifier_patterns=(
        IdentifierPattern(
            pattern=r"\bTAX\s+REFERENCE\s*[:#-]?\s*(?P<value>[A-Z0-9-]{6,16})\b",
            identifier_type=IdentifierType.TAX_IDENTIFIER,
            number_type="EXAMPLE_TAX_REFERENCE",
            confidence=0.90,
            label="example tax reference",
        ),
    ),
)

register_business_profile(profile)
```

Constructors validate confidence ranges and compile regular expressions immediately. Keep patterns bounded and specific, require meaningful alphanumeric structure, and add positive, negative, ambiguity, and performance tests before registration.

## Built-in Nigeria behavior

The Nigeria profile recognizes `NGA`, `NG`, country-name aliases, Corporate Affairs Commission wording, CAMA/Companies and Allied Matters Act wording, and common CAC prefixes.

- `RC` maps to `COMPANY_REGISTRATION_NUMBER` with local type `CAC_RC`.
- `BN` maps to `BUSINESS_REGISTRATION_NUMBER` with local type `CAC_BN`.
- `IT` maps to `REGISTRY_NUMBER` with local type `CAC_IT`.
- `LLP` and `LP` use distinct CAC local types.
- Nigerian TIN and CAC document-reference patterns are separate identifier entries.
- The default issuing authority is the Corporate Affairs Commission; a Nigerian tax identifier can name the Federal Inland Revenue Service.

Prefixes are retained in the readable and normalized identifier values. Detection is evidence-based and does not confirm that a CAC or tax number exists in an external registry.

## Built-in United States behavior

The United States profile recognizes `USA`, `US`, country-name aliases, Secretary/Department of State wording, named states, and common corporation-law wording. It includes subdivision profiles for all states and the District of Columbia.

- Entity, file, and charter numbers map to `STATE_FORMATION_IDENTIFIER` with distinct local types.
- EIN maps to `EMPLOYER_IDENTIFIER`; a separately labelled federal tax ID maps to `TAX_IDENTIFIER`.
- Filing/document numbers map to `DOCUMENT_REFERENCE_NUMBER`.
- A reliably detected state supplies `jurisdiction_code`, `jurisdiction_of_incorporation`, and its registry name.
- A bare `$` capital symbol is interpreted as USD only when US context is available; without country context it remains an unresolved raw currency symbol.

State registries vary substantially. The built-in profile supplies common evidence and identifier labels, not exhaustive rules for every filing system or historical layout.

## Configuration and limits

| Environment variable | Default | Accepted bound/meaning |
| --- | ---: | --- |
| `DOCUMENT_MAX_UPLOAD_BYTES` | `20971520` | Universal upload/URL byte cap; clamped to 1 KiB-100 MiB |
| `DOCUMENT_INPUT_SPOOL_MEMORY_BYTES` | `1048576` | Remote bytes retained in memory before spooling to a temporary file |
| `DOCUMENT_URL_ENABLED` | `1` | Enables URL retrieval |
| `DOCUMENT_URL_ALLOW_HTTP` | `0` | Opt-in for plain HTTP; also configure port 80 if needed |
| `DOCUMENT_URL_ALLOWED_PORTS` | `443` | Comma-separated remote-port allowlist |
| `DOCUMENT_URL_ALLOWED_HOSTS` | empty | Optional exact/wildcard host allowlist |
| `DOCUMENT_URL_CONNECT_TIMEOUT_SECONDS` | `3` | Per-connection timeout, clamped to 0.5-30 seconds |
| `DOCUMENT_URL_READ_TIMEOUT_SECONDS` | `8` | Per-read timeout, clamped to 0.5-60 seconds |
| `DOCUMENT_URL_TOTAL_TIMEOUT_SECONDS` | `20` | Whole remote retrieval deadline, clamped to 1-120 seconds |
| `DOCUMENT_URL_MAX_REDIRECTS` | `3` | Revalidated redirect limit, clamped to 0-10 |
| `DOCUMENT_URL_MAX_LENGTH` | `2048` | URL length limit, clamped to 256-8192 characters |
| `BUSINESS_DOCUMENT_MAX_PAGES` | `20` | Clamped to 1-100 pages |
| `BUSINESS_DOCUMENT_MAX_UPLOAD_BYTES` | `20971520` | Clamped to 1 KiB-100 MiB |
| `BUSINESS_DOCUMENT_MAX_IMAGE_PIXELS` | `25000000` | Decoded image/PDF-page pixel cap; clamped to 1-50 million |
| `BUSINESS_DOCUMENT_MAX_PAGE_TEXT_CHARS` | `100000` | Retained characters per page; clamped to 10,000-500,000 |
| `BUSINESS_DOCUMENT_COMPARE_RENDERED_PDF_TEXT` | `1` | Enables rendered comparison when embedded page text is weak |

The request-scoped body cap applies to every OCR route and is initialized to `DOCUMENT_MAX_UPLOAD_BYTES` plus 1 MiB for multipart framing, preventing oversized bodies from being materialized before route validation. The webhook route is not affected. The shared resolver enforces the exact upload/remote byte cap; the business processor separately applies `BUSINESS_DOCUMENT_MAX_UPLOAD_BYTES`. The lower applicable limit wins. Images are inspected for decoded dimensions before OpenCV decoding; PDF render scale is reduced to remain within the same per-page pixel budget. Strong selectable PDF text skips rendered OCR, and confidently readable upright OCR skips rotation retries.

When the page limit is exceeded, processing continues for the retained pages, `extraction.truncated` is true, and a warning is returned. Text over the per-page character limit is truncated with a warning. Comparing rendered OCR with weak embedded text improves scanned/malformed PDF handling but increases CPU and latency.

Structured extraction is also bounded: objects, parties, evidence excerpts, role blocks, and additional fields have defensive limits. A limit may cause partial output and should be treated as a review condition; not every internal section cap currently emits a dedicated warning.

## Safe logging and observability

The route logs one structured completion record containing request ID, input source (`upload` or `url`), source size, content length, detected file type, pages processed, booleans indicating which hints were supplied, selected document type/country, success, warning count, and duration. Exception logs retain similarly bounded request metadata. It does not log the remote URL.

Do not add raw OCR text, evidence excerpts, company names, addresses, identifier values, uploaded bytes, filenames, email addresses, phone numbers, or party details to logs. These can be sensitive business or personal data. Use `request_id`, counts, classifications, timing, and non-content error categories for correlation. Apply the same rule to traces, metrics labels, and error-reporting services.

The API intentionally returns `raw_text` and evidence to the authorized caller for auditability. Treat the response as sensitive, limit retention, and apply transport encryption and access controls.

## Known limitations

- Extraction and confidence are heuristic; no result is authoritative registry verification, legal advice, or proof of authenticity.
- Seals, signatures, stamps, tampering, handwriting, and document validity are not verified.
- OCR quality, unusual fonts, scans, multi-column tables, and complex constitutional-document layouts can reduce accuracy.
- The taxonomy and field rules cover common registry wording but cannot enumerate every jurisdiction, language, historical form, or local identifier.
- Language detection supports several common European-language markers, while much field extraction remains strongest on English labels.
- Ambiguous numeric dates, jurisdictions, currencies, identifiers, and competing field values require review; consult `warnings`, `conflicts`, and evidence.
- Page and section limits can omit information from very long status reports or constitutional documents.
- OCR executes synchronously; production deployments still need gateway timeouts, concurrency limits, and rate limiting appropriate to available CPU and memory.
- URL retrieval supports directly accessible documents only; authenticated portals, cookies, caller-supplied remote credentials, and interactive login flows are intentionally unsupported.
- Party extraction is conservative and may omit rows rather than risk interpreting addresses or table metadata as people.
- Ownership relationships are extracted as public party records; the pipeline does not build or validate a complete ownership graph.
- `success: true` means usable text reached the parsing pipeline, not that all requested fields were found. Gate automated decisions on required fields, confidence, warnings, and application-specific validation.
