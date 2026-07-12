"""Stable response types for business and company registration documents.

Business records contain more varied data than an identity card.  This module
defines the canonical JSON vocabulary once so certificates, registry reports,
and constitutional documents can share one API contract even when a field is
not printed by a particular jurisdiction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional


UNKNOWN_BUSINESS_DOCUMENT = "UNKNOWN_BUSINESS_DOCUMENT"

BUSINESS_DOCUMENT_TYPES: Dict[str, str] = {
    "CERTIFICATE_OF_INCORPORATION": "Certificate of incorporation",
    "CERTIFICATE_OF_REGISTRATION": "Certificate of business or entity registration",
    "CERTIFICATE_OF_CHANGE_OF_NAME": "Certificate recording a registered name change",
    "COMPANY_STATUS_REPORT": "Registry company status or profile report",
    "MEMORANDUM_AND_ARTICLES_OF_ASSOCIATION": "Memorandum and articles of association",
    "MEMORANDUM_OF_ASSOCIATION": "Memorandum of association",
    "ARTICLES_OF_ASSOCIATION": "Articles of association or company constitution",
    "CERTIFIED_REGISTRY_EXTRACT": "Certified company-registry extract",
    UNKNOWN_BUSINESS_DOCUMENT: "Unrecognized business registration document",
}

CANONICAL_BUSINESS_DATA_KEYS = (
    "company_name",
    "registration_number",
    "registration_number_type",
    "entity_type",
    "company_status",
    "incorporation_date",
    "registration_date",
    "document_date",
    "registered_address",
    "head_office_address",
    "registry_name",
    "country_code",
    "jurisdiction",
    "governing_law",
    "tax_identification_number",
    "contact_email",
    "contact_phone",
    "nature_of_business",
    "business_objects",
    "share_capital",
    "parties",
)

CANONICAL_SHARE_CAPITAL_KEYS = (
    "currency",
    "amount",
    "amount_text",
    "issued_share_count",
    "nominal_value_per_share",
)

CANONICAL_PARTY_KEYS = (
    "name",
    "roles",
    "status",
    "nationality",
    "address",
    "email",
    "phone",
    "identifier",
    "shares",
    "share_percentage",
)


def confidence_level(score: float) -> str:
    """Map a zero-to-one score to a stable, human-readable confidence band."""
    score = max(0.0, min(float(score), 1.0))
    if score >= 0.85:
        return "HIGH"
    if score >= 0.65:
        return "MEDIUM"
    if score >= 0.40:
        return "LOW"
    return "REJECT"


def canonical_share_capital(raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Return a predictable share-capital object."""
    source = raw or {}
    return {
        key: _clean_scalar(source.get(key))
        for key in CANONICAL_SHARE_CAPITAL_KEYS
    }


def canonical_party(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize one company party without collecting hidden identity details."""
    output: Dict[str, Any] = {}
    for key in CANONICAL_PARTY_KEYS:
        value = raw.get(key)
        if key == "roles":
            roles = value if isinstance(value, (list, tuple, set)) else [value] if value else []
            output[key] = sorted({str(role).strip().upper() for role in roles if str(role).strip()})
        else:
            output[key] = _clean_scalar(value)
    return output


def canonical_business_data(raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Return all canonical company fields in a stable order.

    Lists and nested objects keep their type when empty, which lets callers
    distinguish "not printed" from malformed response data without consulting
    the detected document type.
    """
    source = raw or {}
    output: Dict[str, Any] = {}
    for key in CANONICAL_BUSINESS_DATA_KEYS:
        value = source.get(key)
        if key == "business_objects":
            output[key] = _clean_string_list(value)
        elif key == "parties":
            parties = value if isinstance(value, list) else []
            output[key] = [canonical_party(item) for item in parties if isinstance(item, Mapping)]
        elif key == "share_capital":
            output[key] = canonical_share_capital(value if isinstance(value, Mapping) else None)
        else:
            output[key] = _clean_scalar(value)
    return output


def serialize_document_types() -> list[Dict[str, str]]:
    """Expose supported business-document categories for API discovery."""
    return [
        {"code": code, "name": name}
        for code, name in BUSINESS_DOCUMENT_TYPES.items()
        if code != UNKNOWN_BUSINESS_DOCUMENT
    ]


@dataclass(frozen=True)
class FieldEvidence:
    """A bounded OCR excerpt explaining where one parsed field came from."""

    field: str
    value: Any
    method: str
    confidence: float
    page: Optional[int] = None
    text: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        """Convert evidence to JSON-safe values and limit raw excerpts."""
        excerpt = " ".join(str(self.text or "").split())[:240] or None
        score = round(max(0.0, min(float(self.confidence), 1.0)), 3)
        return {
            "field": self.field,
            "value": self.value,
            "method": self.method,
            "confidence": score,
            "confidence_level": confidence_level(score),
            "page": self.page,
            "text": excerpt,
        }


@dataclass(frozen=True)
class ClassificationResult:
    """Document categorization result with explainable matched terms."""

    document_type: str
    confidence: float
    matched_terms: tuple[str, ...] = ()
    alternatives: tuple[Mapping[str, Any], ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        score = round(max(0.0, min(float(self.confidence), 1.0)), 3)
        return {
            "document_type": self.document_type,
            "confidence": score,
            "confidence_level": confidence_level(score),
            "matched_terms": list(self.matched_terms),
            "alternatives": [dict(item) for item in self.alternatives],
        }


@dataclass(frozen=True)
class JurisdictionResult:
    """Detected/requested incorporation jurisdiction and registry authority."""

    country_code: Optional[str]
    country_name: Optional[str]
    registry_name: Optional[str]
    source: str
    confidence: float
    requested_country_code: Optional[str] = None
    detected_country_code: Optional[str] = None
    matched_terms: tuple[str, ...] = ()
    conflict: bool = False

    def as_dict(self) -> Dict[str, Any]:
        score = round(max(0.0, min(float(self.confidence), 1.0)), 3)
        return {
            "country_code": self.country_code,
            "country_name": self.country_name,
            "registry_name": self.registry_name,
            "source": self.source,
            "confidence": score,
            "confidence_level": confidence_level(score),
            "requested_country_code": self.requested_country_code,
            "detected_country_code": self.detected_country_code,
            "matched_terms": list(self.matched_terms),
            "conflict": self.conflict,
        }


@dataclass
class ParsedBusinessDocument:
    """Internal parser result before the processor creates an API response."""

    data: Dict[str, Any]
    evidence: list[FieldEvidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def evidence_by_field(items: Iterable[FieldEvidence]) -> Dict[str, list[Dict[str, Any]]]:
    """Group evidence entries by canonical field for downstream review."""
    grouped: Dict[str, list[Dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(item.field, []).append(item.as_dict())
    return grouped


def _clean_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.split()).strip() or None
    return value


def _clean_string_list(value: Any) -> list[str]:
    items = value if isinstance(value, (list, tuple, set)) else [value] if value else []
    output = []
    for item in items:
        cleaned = _clean_scalar(item)
        if isinstance(cleaned, str) and cleaned and cleaned not in output:
            output.append(cleaned)
    return output
