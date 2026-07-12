"""End-to-end, jurisdiction-aware business-document OCR processor."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from typing import Any, Iterable, Mapping, Optional, Sequence

from src.document_ocr.business_document.classification import (
    classification_keywords,
    classify_business_document,
)
from src.document_ocr.business_document.config import (
    BusinessDocumentSettings,
    get_business_document_settings,
)
from src.document_ocr.business_document.fields import parse_core_business_fields
from src.document_ocr.business_document.generic import parse_generic_business_fields
from src.document_ocr.business_document.identifiers import extract_business_identifiers
from src.document_ocr.business_document.jurisdictions import (
    SubdivisionResult,
    detect_business_jurisdiction,
    detect_business_subdivision,
    get_business_jurisdiction,
    jurisdiction_keywords,
    jurisdiction_warnings,
    normalize_country_code,
)
from src.document_ocr.business_document.language import detect_document_language
from src.document_ocr.business_document.schema import (
    BUSINESS_DOCUMENT_TYPES,
    UNKNOWN_BUSINESS_DOCUMENT,
    BusinessDocumentRequest,
    BusinessDocumentResponse,
    ClassificationResult,
    FieldConflict,
    FieldEvidence,
    JurisdictionResult,
    ParsedBusinessDocument,
)
from src.document_ocr.business_document.sections import parse_business_sections
from src.document_ocr.business_document.upload import inspect_business_upload
from src.document_ocr.text_extraction import ExtractedDocumentText, extract_document_text_pages

_FIELD_ALIASES = {
    "company_name": "legal_company_name",
    "registered_address": "registered_office_address",
    "head_office_address": "principal_business_address",
    "registry_name": "issuing_authority",
    "document_date": "document_issue_date",
    "nature_of_business": "business_activities",
    "business_objects": "objects_or_purpose",
    "registration_number": "identifiers",
    "registration_number_type": "identifiers",
    "tax_identification_number": "identifiers",
}

_LIST_FIELDS = {
    "additional_fields",
    "business_activities",
    "objects_or_purpose",
    "parties",
}

_PRIMARY_IDENTIFIER_TYPES = {
    "COMPANY_REGISTRATION_NUMBER",
    "BUSINESS_REGISTRATION_NUMBER",
    "STATE_FORMATION_IDENTIFIER",
    "REGISTRY_NUMBER",
}


def extract_business_document_data(
    file_stream: Any,
    *,
    country_code: Optional[str] = None,
    jurisdiction_hint: Optional[str] = None,
    document_type_hint: Optional[str] = None,
    filename: Optional[str] = None,
    is_pdf: Optional[bool] = None,
    settings: Optional[BusinessDocumentSettings] = None,
) -> dict[str, Any]:
    """Validate, OCR, classify, and parse an uploaded business document."""
    active_settings = settings or get_business_document_settings()
    upload = inspect_business_upload(
        file_stream,
        filename=filename or getattr(file_stream, "filename", None),
        max_upload_bytes=active_settings.max_upload_bytes,
    )
    if not upload.valid:
        return business_document_error(
            upload.message or "Invalid business document upload.",
            warnings=(upload.warning,) if upload.warning else (),
            extraction={"size_bytes": upload.size_bytes, "file_type": upload.file_type},
        )

    try:
        file_stream.seek(0)
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    detected_pdf = upload.is_pdf
    extraction_warnings = []
    if is_pdf is not None and bool(is_pdf) != detected_pdf:
        extraction_warnings.append("The supplied PDF hint did not match the uploaded file signature.")
    if upload.warning:
        extraction_warnings.append(upload.warning)

    extracted = extract_document_text_pages(
        file_stream,
        is_pdf=detected_pdf,
        max_pages=active_settings.max_pages,
        max_image_pixels=active_settings.max_image_pixels,
        max_page_text_chars=active_settings.max_page_text_chars,
        page_scorer=business_document_text_score,
        compare_rendered_pdf_text=active_settings.compare_rendered_pdf_text,
    )
    extraction_warnings.extend(_readable_extraction_warnings(extracted.warnings))
    extraction_metadata = _extraction_metadata(extracted, upload.size_bytes, upload.file_type)
    if not extracted.text:
        return business_document_error(
            "Could not extract text from the business document.",
            warnings=extraction_warnings,
            extraction=extraction_metadata,
        )

    return parse_business_document_text(
        extracted.text,
        country_hint=country_code,
        jurisdiction_hint=jurisdiction_hint,
        document_type_hint=document_type_hint,
        page_texts=extracted.page_texts,
        extraction=extraction_metadata,
        initial_warnings=extraction_warnings,
    )


def parse_business_document_text(
    text: str,
    *,
    country_hint: Optional[str] = None,
    jurisdiction_hint: Optional[str] = None,
    document_type_hint: Optional[str] = None,
    page_texts: Optional[Sequence[str]] = None,
    extraction: Optional[Mapping[str, Any]] = None,
    initial_warnings: Iterable[str] = (),
) -> dict[str, Any]:
    """Parse already-extracted OCR text for deterministic tests and reuse."""
    raw_text = str(text or "").strip()
    if not raw_text:
        return business_document_error("No OCR text was available for business-document parsing.")

    request_hints = BusinessDocumentRequest.from_values(
        country_hint=country_hint,
        jurisdiction_hint=jurisdiction_hint,
        document_type_hint=document_type_hint,
    )
    warnings = list(initial_warnings)
    normalized_country_hint = _validated_country_hint(request_hints.country_hint, warnings)

    detected_classification = classify_business_document(raw_text)
    classification = _reconcile_document_type_hint(
        detected_classification,
        request_hints.document_type_hint,
        warnings,
    )
    if classification.document_type == UNKNOWN_BUSINESS_DOCUMENT:
        warnings.append("The business document type could not be reliably determined; generic extraction was used.")
    elif classification.ambiguous:
        warnings.append("The business document type is ambiguous; review the classification alternatives.")

    jurisdiction = detect_business_jurisdiction(raw_text, normalized_country_hint)
    subdivision = _reconcile_subdivision_hint(
        raw_text,
        jurisdiction,
        request_hints.jurisdiction_hint,
        warnings,
    )
    warnings.extend(jurisdiction_warnings(jurisdiction, subdivision))

    language = detect_document_language(raw_text)
    if language.code is None:
        warnings.append("The document language could not be reliably determined.")
    elif language.ambiguous:
        warnings.append("The document language is ambiguous.")

    pages = tuple(page_texts) if page_texts is not None else (raw_text,)
    partials = (
        parse_core_business_fields(
            raw_text,
            jurisdiction=jurisdiction,
            document_type=classification.document_type,
            page_texts=pages,
        ),
        parse_generic_business_fields(
            raw_text,
            country_code=jurisdiction.country_code,
            page_texts=pages,
        ),
        parse_business_sections(
            raw_text,
            document_type=classification.document_type,
            page_texts=pages,
            country_code=jurisdiction.country_code,
        ),
    )
    merged, evidence, merge_warnings, conflicts = _merge_partial_results(partials)
    warnings.extend(merge_warnings)

    identifier_result = extract_business_identifiers(raw_text, jurisdiction=jurisdiction)
    identifier_dicts, identifier_evidence = _serialize_identifiers(identifier_result.identifiers, pages, raw_text)
    merged["identifiers"] = identifier_dicts
    evidence.extend(identifier_evidence)
    warnings.extend(identifier_result.warnings)
    conflicts.extend(conflict.as_dict() for conflict in identifier_result.conflicts)

    if jurisdiction.country_code:
        merged["country_code"] = jurisdiction.country_code
        merged["country_of_incorporation"] = jurisdiction.country_name or jurisdiction.country_code
    if subdivision:
        merged["jurisdiction_code"] = subdivision.code
        merged["jurisdiction_of_incorporation"] = subdivision.name
    merged["document_language"] = language.as_dict()
    if not merged.get("issuing_authority"):
        merged["issuing_authority"] = (
            subdivision.registry_name if subdivision and not subdivision.ambiguous else jurisdiction.registry_name
        )
    if not merged.get("document_reference_number"):
        merged["document_reference_number"] = _first_identifier_value(
            identifier_dicts,
            "DOCUMENT_REFERENCE_NUMBER",
        )

    evidence = _mark_selected_evidence(evidence, merged)
    warnings.extend(_validation_warnings(merged, evidence, jurisdiction.country_code))
    overall_confidence = _overall_confidence(
        classification=classification,
        jurisdiction=jurisdiction,
        data=merged,
        evidence=evidence,
        identifiers=identifier_dicts,
    )
    response = BusinessDocumentResponse(
        success=True,
        message=None,
        document_type=classification.document_type,
        data=merged,
        classification=classification.as_dict(),
        jurisdiction=_jurisdiction_payload(jurisdiction, subdivision),
        evidence=evidence,
        warnings=warnings,
        conflicts=conflicts,
        raw_text=raw_text,
        extraction=dict(extraction or _text_only_extraction(pages)),
        overall_confidence=overall_confidence,
    )
    return response.as_dict()


def business_document_error(
    message: str,
    *,
    warnings: Iterable[str] = (),
    raw_text: str = "",
    extraction: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build a complete error response using the same public schema."""
    classification = ClassificationResult(UNKNOWN_BUSINESS_DOCUMENT, 0.0, source="undetermined")
    jurisdiction = JurisdictionResult(None, None, None, "undetermined", 0.0)
    return BusinessDocumentResponse(
        success=False,
        message=message,
        document_type=UNKNOWN_BUSINESS_DOCUMENT,
        data={},
        classification=classification.as_dict(),
        jurisdiction=jurisdiction.as_dict(),
        evidence=(),
        warnings=tuple(warnings),
        conflicts=(),
        raw_text=raw_text,
        extraction=dict(extraction or {}),
        overall_confidence=0.0,
    ).as_dict()


def business_document_text_score(text: str) -> int:
    """Score page text for choosing embedded PDF text versus rendered OCR."""
    upper = str(text or "").upper()
    score = 0
    for keyword in (*classification_keywords(), *jurisdiction_keywords()):
        if keyword in upper:
            score += 4
    for pattern in (
        r"\b(?:COMPANY|ENTITY|BUSINESS)\s+(?:NAME|NUMBER|STATUS|TYPE)\b",
        r"\b(?:REGISTERED\s+OFFICE|PRINCIPAL\s+PLACE\s+OF\s+BUSINESS)\b",
        r"\b(?:DIRECTORS?|SHAREHOLDERS?|BENEFICIAL\s+OWNERS?|SHARE\s+CAPITAL)\b",
        r"\b(?:REGISTRATION|INCORPORATION|FORMATION)\s+DATE\b",
    ):
        if re.search(pattern, upper):
            score += 3
    score += min(len(re.findall(r"[A-Z0-9]{2,}", upper)) // 20, 10)
    return score


def _merge_partial_results(
    partials: Sequence[ParsedBusinessDocument],
) -> tuple[dict[str, Any], list[FieldEvidence], list[str], list[FieldConflict | Mapping[str, Any]]]:
    merged: dict[str, Any] = {}
    scores: dict[str, float] = {}
    evidence: list[FieldEvidence] = []
    warnings: list[str] = []
    conflicts: list[FieldConflict | Mapping[str, Any]] = []
    for partial in partials:
        warnings.extend(_readable_parser_warnings(partial.warnings))
        conflicts.extend(partial.conflicts)
        mapped_evidence = [replace(item, field=_canonical_field(item.field)) for item in partial.evidence]
        evidence.extend(mapped_evidence)
        evidence_scores = _evidence_scores(mapped_evidence)
        for raw_key, value in partial.data.items():
            if raw_key in {"registration_number", "registration_number_type", "tax_identification_number"}:
                continue
            key = _canonical_field(raw_key)
            if _empty(value):
                continue
            if key not in merged or _empty(merged[key]):
                merged[key] = value
                scores[key] = evidence_scores.get(key, 0.5)
                continue
            if key in _LIST_FIELDS:
                merged[key] = _merge_list_field(key, merged[key], value)
                scores[key] = max(scores.get(key, 0.0), evidence_scores.get(key, 0.0))
                continue
            if key == "share_capital" and isinstance(merged[key], Mapping) and isinstance(value, Mapping):
                merged[key], capital_conflicts = _deep_merge_mapping(key, merged[key], value)
                conflicts.extend(capital_conflicts)
                continue
            if _equivalent(merged[key], value):
                continue
            candidate_score = evidence_scores.get(key, 0.5)
            previous_value = merged[key]
            selected_value = previous_value
            if key == "issuing_authority" and _authorities_compatible(previous_value, value):
                if candidate_score > scores.get(key, 0.5):
                    merged[key] = value
                    scores[key] = candidate_score
                continue
            if candidate_score > scores.get(key, 0.5) + 0.02:
                selected_value = value
                merged[key] = value
                scores[key] = candidate_score
            conflicts.append(
                FieldConflict(
                    field=key,
                    selected_value=selected_value,
                    candidate_values=(previous_value, value),
                    resolution="highest_confidence_evidence",
                )
            )
            warnings.append(f"Conflicting values were extracted for {key}; the strongest evidence was selected.")
    return merged, evidence, warnings, conflicts


def _serialize_identifiers(
    identifiers: Sequence[Any],
    page_texts: Sequence[str],
    raw_text: str,
) -> tuple[list[dict[str, Any]], list[FieldEvidence]]:
    output: list[dict[str, Any]] = []
    field_evidence: list[FieldEvidence] = []
    primary_assigned = False
    for identifier in identifiers:
        item = identifier.as_dict()
        item["is_primary"] = False
        if not primary_assigned and item.get("type") in _PRIMARY_IDENTIFIER_TYPES:
            item["is_primary"] = True
            primary_assigned = True
        raw_identifier_evidence = item.get("evidence")
        identifier_evidence = raw_identifier_evidence if isinstance(raw_identifier_evidence, list) else []
        for entry in identifier_evidence:
            if isinstance(entry, dict):
                entry["page"] = _page_for_offsets(
                    entry.get("start"),
                    entry.get("end"),
                    raw_text,
                    page_texts,
                ) or _page_for_excerpt(item.get("value"), page_texts)
        output.append(item)
        first_evidence: Mapping[str, Any] = next(
            (entry for entry in identifier_evidence if isinstance(entry, Mapping)),
            {},
        )
        field_evidence.append(
            FieldEvidence(
                field="identifiers",
                value=item.get("normalized_value"),
                method=str(first_evidence.get("method") or item.get("source") or "identifier_pattern"),
                confidence=float(item.get("confidence") or 0.0),
                page=first_evidence.get("page"),
                text=first_evidence.get("text"),
                source=item.get("source"),
            )
        )
    return output, field_evidence


def _validated_country_hint(value: Optional[str], warnings: list[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = normalize_country_code(value)
    if normalized is None:
        warnings.append("The country hint is invalid; use an ISO country code or a registered profile alias.")
    return normalized


def _reconcile_document_type_hint(
    detected: ClassificationResult,
    hint: Optional[str],
    warnings: list[str],
) -> ClassificationResult:
    if not hint:
        return detected
    normalized = re.sub(r"[^A-Z0-9]+", "_", hint.upper()).strip("_")
    if normalized not in BUSINESS_DOCUMENT_TYPES or normalized == UNKNOWN_BUSINESS_DOCUMENT:
        warnings.append("The document type hint is unsupported and was ignored.")
        return detected
    if detected.document_type == UNKNOWN_BUSINESS_DOCUMENT:
        warnings.append("The document type is based only on the caller hint and was not confirmed by OCR text.")
        return ClassificationResult(normalized, 0.55, alternatives=detected.alternatives, source="request_hint")
    if detected.document_type != normalized:
        warnings.append("The document type hint conflicts with the type detected from OCR text.")
    return detected


def _reconcile_subdivision_hint(
    text: str,
    jurisdiction: JurisdictionResult,
    hint: Optional[str],
    warnings: list[str],
) -> Optional[SubdivisionResult]:
    detected = detect_business_subdivision(text, jurisdiction.country_code)
    if not hint:
        return detected
    profile = get_business_jurisdiction(jurisdiction.country_code)
    normalized = re.sub(r"[^A-Z0-9]+", " ", hint.upper()).strip()
    requested = None
    if profile:
        for item in profile.subdivisions:
            aliases = {item.code.upper(), item.name.upper(), *(alias.upper() for alias in item.aliases)}
            if normalized in {re.sub(r"[^A-Z0-9]+", " ", alias).strip() for alias in aliases}:
                requested = item
                break
    if requested is None:
        warnings.append("The jurisdiction hint is invalid for the selected country and was ignored.")
        return detected
    if detected and detected.code != requested.code:
        warnings.append("The jurisdiction hint conflicts with the state or province detected from OCR text.")
        return detected
    if detected:
        return detected
    warnings.append("The incorporation jurisdiction is based only on the caller hint.")
    assert profile is not None
    return SubdivisionResult(
        country_code=jurisdiction.country_code or profile.code,
        code=requested.code,
        name=requested.name,
        registry_name=requested.registry_name,
        confidence=0.60,
        source="request_hint",
    )


def _jurisdiction_payload(
    country: JurisdictionResult,
    subdivision: Optional[SubdivisionResult],
) -> dict[str, Any]:
    payload = country.as_dict()
    payload["subdivision"] = subdivision.as_dict() if subdivision else None
    return payload


def _validation_warnings(
    data: Mapping[str, Any],
    evidence: Sequence[FieldEvidence],
    country_code: Optional[str],
) -> list[str]:
    warnings = []
    if not data.get("legal_company_name"):
        warnings.append("The legal company name could not be reliably extracted.")
    if not data.get("identifiers"):
        warnings.append("No reliable registration, registry, tax, employer, or document identifier was extracted.")
    if not data.get("issuing_authority"):
        warnings.append("The issuing authority could not be reliably determined.")
    incorporation = _iso_date(data.get("incorporation_date") or data.get("registration_date"))
    issue = _iso_date(data.get("document_issue_date"))
    if incorporation and incorporation > date.today():
        warnings.append("The extracted incorporation or registration date is in the future.")
    if issue and issue > date.today():
        warnings.append("The extracted document issue date is in the future.")
    if incorporation and issue and incorporation > issue:
        warnings.append("The incorporation or registration date is later than the document issue date.")
    if country_code != "USA":
        for item in evidence:
            if item.field not in {"incorporation_date", "registration_date", "document_issue_date"}:
                continue
            match = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-]\d{2,4}\b", item.text or "")
            if match and int(match.group(1)) <= 12 and int(match.group(2)) <= 12:
                warnings.append("A numeric date is day/month ambiguous; review its field evidence.")
                break
    return warnings


def _overall_confidence(
    *,
    classification: ClassificationResult,
    jurisdiction: JurisdictionResult,
    data: Mapping[str, Any],
    evidence: Sequence[FieldEvidence],
    identifiers: Sequence[Mapping[str, Any]],
) -> float:
    scores = _evidence_scores(evidence)
    name_score = scores.get("legal_company_name", 0.0)
    identifier_score = max((float(item.get("confidence") or 0.0) for item in identifiers), default=0.0)
    coverage_fields = (
        "legal_company_name",
        "entity_type",
        "incorporation_date",
        "registration_date",
        "registered_office_address",
        "issuing_authority",
        "company_status",
        "identifiers",
    )
    coverage = sum(not _empty(data.get(key)) for key in coverage_fields) / len(coverage_fields)
    value = (
        0.25 * classification.confidence
        + 0.15 * jurisdiction.confidence
        + 0.20 * name_score
        + 0.20 * identifier_score
        + 0.20 * coverage
    )
    return round(max(0.0, min(value, 1.0)), 3)


def _mark_selected_evidence(
    evidence: Sequence[FieldEvidence],
    data: Mapping[str, Any],
) -> list[FieldEvidence]:
    output = []
    for item in evidence:
        selected_value = data.get(item.field)
        selected = (
            True if item.field in _LIST_FIELDS or item.field == "identifiers" else _equivalent(item.value, selected_value)
        )
        output.append(replace(item, selected=selected))
    return output


def _evidence_scores(items: Sequence[FieldEvidence]) -> dict[str, float]:
    output: dict[str, float] = {}
    for item in items:
        output[item.field] = max(output.get(item.field, 0.0), float(item.confidence))
    return output


def _merge_list_field(key: str, existing: Any, incoming: Any) -> list[Any]:
    left = list(existing) if isinstance(existing, (list, tuple)) else [existing]
    right = list(incoming) if isinstance(incoming, (list, tuple)) else [incoming]
    if key == "parties":
        return _merge_parties([item for item in (*left, *right) if isinstance(item, Mapping)])
    output = []
    seen = set()
    for item in (*left, *right):
        marker = repr(sorted(item.items())) if isinstance(item, Mapping) else str(item).casefold()
        if _empty(item) or marker in seen:
            continue
        seen.add(marker)
        output.append(item)
    return output


def _merge_parties(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in items:
        name = " ".join(str(item.get("name") or "").split())
        key = re.sub(r"[^A-Z0-9]", "", name.upper())
        if not key:
            continue
        if key not in merged:
            merged[key] = dict(item)
            order.append(key)
            continue
        current = merged[key]
        current["roles"] = sorted(set(current.get("roles") or []) | set(item.get("roles") or []))
        for field in ("status", "nationality", "address", "email", "phone", "shares", "share_percentage"):
            if _empty(current.get(field)) and not _empty(item.get(field)):
                current[field] = item[field]
    return [merged[key] for key in order]


def _deep_merge_mapping(
    field_name: str,
    existing: Mapping[str, Any],
    incoming: Mapping[str, Any],
) -> tuple[dict[str, Any], list[FieldConflict]]:
    output = dict(existing)
    conflicts = []
    for key, value in incoming.items():
        if _empty(value):
            continue
        if _empty(output.get(key)):
            output[key] = value
        elif not _equivalent(output[key], value):
            conflicts.append(
                FieldConflict(
                    field=f"{field_name}.{key}",
                    selected_value=output[key],
                    candidate_values=(output[key], value),
                    resolution="first_non_empty_value",
                )
            )
    return output, conflicts


def _first_identifier_value(items: Sequence[Mapping[str, Any]], identifier_type: str) -> Optional[str]:
    for item in items:
        if item.get("type") == identifier_type:
            value = item.get("value")
            return str(value) if value else None
    return None


def _canonical_field(value: str) -> str:
    return _FIELD_ALIASES.get(value, value)


def _page_for_excerpt(excerpt: Any, page_texts: Sequence[str]) -> Optional[int]:
    needle = re.sub(r"\s+", " ", str(excerpt or "")).strip().casefold()
    if not needle:
        return None
    for page_number, page_text in enumerate(page_texts, start=1):
        haystack = re.sub(r"\s+", " ", page_text or "").casefold()
        if needle in haystack:
            return page_number
    return None


def _page_for_offsets(
    start: Any,
    end: Any,
    raw_text: str,
    page_texts: Sequence[str],
) -> Optional[int]:
    """Resolve raw-text character offsets against the retained page boundaries."""
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
        return None
    joined = "\n".join(page_texts)
    leading_trim = len(joined) - len(joined.lstrip())
    if joined.strip() != raw_text:
        return None
    joined_start = start + leading_trim
    joined_end = end + leading_trim
    cursor = 0
    for page_number, page_text in enumerate(page_texts, start=1):
        page_end = cursor + len(page_text)
        if joined_start < page_end and joined_end > cursor:
            return page_number
        cursor = page_end + 1
    return None


def _iso_date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _equivalent(left: Any, right: Any) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return re.sub(r"[^A-Z0-9]", "", left.upper()) == re.sub(r"[^A-Z0-9]", "", right.upper())
    return left == right


def _authorities_compatible(left: Any, right: Any) -> bool:
    left_words = set(re.findall(r"[A-Z]{4,}", str(left or "").upper()))
    right_words = set(re.findall(r"[A-Z]{4,}", str(right or "").upper()))
    authority_words = {"STATE", "SECRETARY", "DIVISION", "CORPORATIONS", "REGISTRY", "REGISTRAR", "COMMISSION"}
    shared_specific = (left_words & right_words) - authority_words
    return bool(shared_specific) and bool(left_words & authority_words) and bool(right_words & authority_words)


def _empty(value: Any) -> bool:
    return value in (None, "", [], {}, ())


def _extraction_metadata(
    extracted: ExtractedDocumentText,
    size_bytes: Optional[int],
    file_type: Optional[str],
) -> dict[str, Any]:
    return {
        "file_type": file_type,
        "size_bytes": size_bytes,
        "pages_processed": len(extracted.pages),
        "total_pages": extracted.total_pages,
        "truncated": extracted.truncated,
        "pages": [
            {
                "page": page.page_number,
                "source": page.source,
                "ocr_confidence": page.ocr_confidence,
                "text_length": len(page.text),
            }
            for page in extracted.pages
        ],
    }


def _text_only_extraction(page_texts: Sequence[str]) -> dict[str, Any]:
    return {
        "file_type": "text_fixture",
        "pages_processed": len(page_texts),
        "total_pages": len(page_texts),
        "truncated": False,
    }


def _readable_extraction_warnings(items: Iterable[str]) -> list[str]:
    mapping = {
        "document_stream_unreadable": "The uploaded document stream could not be read.",
        "invalid_image_format": "The uploaded image could not be decoded.",
        "image_pixel_limit_exceeded": "The uploaded image exceeds the configured decoded-pixel limit.",
        "empty_pdf": "The uploaded PDF is empty.",
        "invalid_or_unreadable_pdf": "The uploaded PDF is invalid or unreadable.",
        "page_limit_reached": "The document exceeded the configured page limit and was truncated.",
        "page_text_limit_reached": "Extracted page text exceeded the configured character limit and was truncated.",
        "no_text_extracted": "No readable text was extracted from the document.",
    }
    return [mapping.get(item, item.replace("_", " ").capitalize()) for item in items]


def _readable_parser_warnings(items: Iterable[str]) -> list[str]:
    mapping = {
        "country_hint_conflicts_with_document": "The country hint conflicts with the document text.",
        "business_objects_not_found": "No objects or purpose clauses were found in the memorandum.",
        "company_parties_not_found": "No company parties were found in the status report.",
        "additional_fields_truncated": "Additional unclassified fields were truncated at the configured limit.",
    }
    ignored = {"company_name_not_found", "registration_number_not_found"}
    return [mapping.get(item, item.replace("_", " ").capitalize()) for item in items if item not in ignored]
