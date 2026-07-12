"""Canonical models and serializers for global business documents."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

UNKNOWN_BUSINESS_DOCUMENT = "UNKNOWN_BUSINESS_DOCUMENT"

BUSINESS_DOCUMENT_TYPES: Dict[str, str] = {
    "CERTIFICATE_OF_INCORPORATION": "Certificate of incorporation",
    "CERTIFICATE_OF_REGISTRATION": "Certificate of entity registration",
    "BUSINESS_REGISTRATION_CERTIFICATE": "Business registration certificate",
    "CAC_CERTIFICATE": "Corporate Affairs Commission certificate of uncertain subtype",
    "CERTIFICATE_OF_FORMATION": "Certificate of formation",
    "CERTIFICATE_OF_GOOD_STANDING": "Certificate of good standing or existence",
    "CERTIFICATE_OF_CHANGE_OF_NAME": "Certificate recording a registered name change",
    "COMPANY_STATUS_REPORT": "Company status, profile, or registry report",
    "MEMORANDUM_AND_ARTICLES_OF_ASSOCIATION": "Memorandum and articles of association",
    "MEMORANDUM_OF_ASSOCIATION": "Memorandum of association",
    "ARTICLES_OF_ASSOCIATION": "Articles of association or company constitution",
    "ARTICLES_OF_INCORPORATION": "Articles of incorporation",
    "ARTICLES_OF_ORGANIZATION": "Articles of organization",
    "TAX_REGISTRATION_CERTIFICATE": "Tax registration certificate",
    "COMPANY_REGISTRY_EXTRACT": "Company or business registry extract",
    "CERTIFIED_REGISTRY_EXTRACT": "Certified company-registry extract",
    UNKNOWN_BUSINESS_DOCUMENT: "Unrecognized business or company document",
}

CANONICAL_BUSINESS_DATA_KEYS = (
    "legal_company_name",
    "trading_name",
    "entity_type",
    "country_of_incorporation",
    "country_code",
    "jurisdiction_of_incorporation",
    "jurisdiction_code",
    "incorporation_date",
    "registration_date",
    "incorporation_or_registration_date",
    "registered_office_address",
    "principal_business_address",
    "identifiers",
    "issuing_authority",
    "document_issue_date",
    "document_reference_number",
    "company_status",
    "directors",
    "shareholders",
    "beneficial_owners",
    "parties",
    "share_capital",
    "business_activities",
    "objects_or_purpose",
    "governing_law",
    "contact_email",
    "contact_phone",
    "document_language",
    "additional_fields",
)

CANONICAL_IDENTIFIER_KEYS = (
    "type",
    "number_type",
    "value",
    "normalized_value",
    "issuing_authority",
    "country_code",
    "jurisdiction",
    "confidence",
    "evidence",
    "source",
    "is_primary",
)

CANONICAL_SHARE_CAPITAL_KEYS = (
    "currency",
    "currency_raw",
    "currency_candidates",
    "amount",
    "amount_text",
    "authorized_amount",
    "issued_amount",
    "paid_up_amount",
    "stated_amount",
    "share_count",
    "issued_share_count",
    "nominal_value_per_share",
    "share_class",
    "share_classes",
)

CANONICAL_PARTY_KEYS = (
    "name",
    "entity_type",
    "roles",
    "status",
    "nationality",
    "address",
    "email",
    "phone",
    "identifiers",
    "shares",
    "share_percentage",
    "confidence",
    "evidence",
)

_DATA_ALIASES: dict[str, tuple[str, ...]] = {
    "legal_company_name": ("legal_company_name", "company_name", "entity_name"),
    "trading_name": ("trading_name", "trade_name", "doing_business_as"),
    "registered_office_address": ("registered_office_address", "registered_address"),
    "principal_business_address": (
        "principal_business_address",
        "head_office_address",
        "business_address",
    ),
    "issuing_authority": ("issuing_authority", "registry_name"),
    "document_issue_date": ("document_issue_date", "document_date"),
    "document_reference_number": ("document_reference_number", "document_number"),
    "objects_or_purpose": ("objects_or_purpose", "business_objects"),
    "business_activities": ("business_activities", "nature_of_business"),
}


def confidence_level(score: float) -> str:
    """Map a zero-to-one score to a stable human-readable band."""
    value = max(0.0, min(float(score), 1.0))
    if value >= 0.85:
        return "HIGH"
    if value >= 0.65:
        return "MEDIUM"
    if value >= 0.40:
        return "LOW"
    return "REJECT"


def canonical_identifier(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize one typed identifier without conflating identifier systems."""
    value = _clean_scalar(raw.get("value"))
    normalized = _clean_scalar(raw.get("normalized_value")) or value
    confidence = _confidence_or_none(raw.get("confidence"))
    raw_evidence = raw.get("evidence")
    evidence: Sequence[Any] = raw_evidence if isinstance(raw_evidence, (list, tuple)) else []
    output = {
        "type": _clean_scalar(raw.get("type") or raw.get("identifier_type") or "OTHER"),
        "number_type": _clean_scalar(raw.get("number_type")),
        "value": value,
        "normalized_value": normalized,
        "issuing_authority": _clean_scalar(raw.get("issuing_authority")),
        "country_code": _clean_scalar(raw.get("country_code")),
        "jurisdiction": _clean_scalar(raw.get("jurisdiction")),
        "confidence": confidence,
        "evidence": [_clean_mapping(item) for item in evidence if isinstance(item, Mapping)],
        "source": _clean_scalar(raw.get("source")),
        "is_primary": bool(raw.get("is_primary", False)),
    }
    return {key: output[key] for key in CANONICAL_IDENTIFIER_KEYS}


def canonical_share_capital(raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Return a predictable capital object while retaining multi-class detail."""
    source = raw or {}
    output: Dict[str, Any] = {}
    for key in CANONICAL_SHARE_CAPITAL_KEYS:
        value = source.get(key)
        if key in {"currency_candidates", "share_classes"}:
            output[key] = (
                [_clean_mapping(item) if isinstance(item, Mapping) else _clean_scalar(item) for item in value]
                if isinstance(value, (list, tuple))
                else []
            )
        else:
            output[key] = _clean_scalar(value)
    return output


def canonical_party(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize one public company party record."""
    output: Dict[str, Any] = {}
    for key in CANONICAL_PARTY_KEYS:
        value = raw.get(key)
        if key == "roles":
            roles = value if isinstance(value, (list, tuple, set)) else [value] if value else []
            output[key] = sorted({str(role).strip().upper() for role in roles if str(role).strip()})
        elif key == "identifiers":
            identifiers = value if isinstance(value, (list, tuple)) else []
            output[key] = [canonical_identifier(item) for item in identifiers if isinstance(item, Mapping)]
        elif key == "evidence":
            entries = value if isinstance(value, (list, tuple)) else []
            output[key] = [_clean_mapping(item) for item in entries if isinstance(item, Mapping)]
        elif key == "confidence":
            output[key] = _confidence_or_none(value)
        else:
            output[key] = _clean_scalar(value)
    legacy_identifier = _clean_scalar(raw.get("identifier"))
    if legacy_identifier and not output["identifiers"]:
        output["identifiers"] = [
            canonical_identifier({"type": "OTHER", "value": legacy_identifier, "normalized_value": legacy_identifier})
        ]
    return output


def canonical_additional_field(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Preserve an unclassified OCR label/value pair with bounded evidence."""
    raw_evidence = raw.get("evidence")
    evidence: Mapping[str, Any] = raw_evidence if isinstance(raw_evidence, Mapping) else {}
    return {
        "label": _clean_scalar(raw.get("label")),
        "value": _clean_scalar(raw.get("value")),
        "confidence": _confidence_or_none(raw.get("confidence")),
        "evidence": _clean_mapping(evidence),
    }


def canonical_business_data(raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Canonicalize a fully merged extraction exactly once."""
    source = dict(raw or {})
    output: Dict[str, Any] = {}
    for key in CANONICAL_BUSINESS_DATA_KEYS:
        value = _aliased_value(source, key)
        if key == "identifiers":
            identifiers = value if isinstance(value, (list, tuple)) else []
            normalized = [canonical_identifier(item) for item in identifiers if isinstance(item, Mapping)]
            output[key] = _append_legacy_identifiers(normalized, source)
        elif key in {"directors", "shareholders", "beneficial_owners", "parties"}:
            parties = value if isinstance(value, (list, tuple)) else []
            output[key] = [canonical_party(item) for item in parties if isinstance(item, Mapping)]
        elif key == "share_capital":
            output[key] = canonical_share_capital(value if isinstance(value, Mapping) else None)
        elif key in {"business_activities", "objects_or_purpose"}:
            output[key] = _clean_string_list(value)
        elif key == "document_language":
            output[key] = _canonical_language(value)
        elif key == "additional_fields":
            entries = value if isinstance(value, (list, tuple)) else []
            output[key] = [canonical_additional_field(item) for item in entries if isinstance(item, Mapping)]
        else:
            output[key] = _clean_scalar(value)

    if not output["country_of_incorporation"]:
        output["country_of_incorporation"] = _clean_scalar(source.get("jurisdiction"))
    if not output["incorporation_or_registration_date"]:
        output["incorporation_or_registration_date"] = output["incorporation_date"] or output["registration_date"]
    _populate_role_lists(output)
    return output


def serialize_document_types() -> list[Dict[str, str]]:
    """Expose recognized categories for documentation or discovery."""
    return [
        {"code": code, "name": name} for code, name in BUSINESS_DOCUMENT_TYPES.items() if code != UNKNOWN_BUSINESS_DOCUMENT
    ]


@dataclass(frozen=True)
class BusinessDocumentRequest:
    """Normalized optional hints accepted by the HTTP endpoint."""

    country_hint: Optional[str] = None
    jurisdiction_hint: Optional[str] = None
    document_type_hint: Optional[str] = None

    @classmethod
    def from_values(
        cls,
        *,
        country_hint: Optional[str] = None,
        jurisdiction_hint: Optional[str] = None,
        document_type_hint: Optional[str] = None,
    ) -> "BusinessDocumentRequest":
        return cls(
            country_hint=_clean_hint(country_hint, upper=True),
            jurisdiction_hint=_clean_hint(jurisdiction_hint),
            document_type_hint=_clean_hint(document_type_hint, upper=True),
        )


@dataclass(frozen=True)
class FieldEvidence:
    """A bounded OCR excerpt explaining one extracted field candidate."""

    field: str
    value: Any
    method: str
    confidence: float
    page: Optional[int] = None
    text: Optional[str] = None
    source: Optional[str] = None
    selected: bool = True

    def as_dict(self) -> Dict[str, Any]:
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
            "source": self.source,
            "selected": self.selected,
        }


@dataclass(frozen=True)
class ClassificationResult:
    """Explainable document categorization result."""

    document_type: str
    confidence: float
    matched_terms: tuple[str, ...] = ()
    alternatives: tuple[Mapping[str, Any], ...] = ()
    ambiguous: bool = False
    source: str = "document_text"

    def as_dict(self) -> Dict[str, Any]:
        score = round(max(0.0, min(float(self.confidence), 1.0)), 3)
        return {
            "document_type": self.document_type,
            "confidence": score,
            "confidence_level": confidence_level(score),
            "matched_terms": list(self.matched_terms),
            "alternatives": [dict(item) for item in self.alternatives],
            "ambiguous": self.ambiguous,
            "source": self.source,
        }


@dataclass(frozen=True)
class JurisdictionResult:
    """Detected/requested incorporation country and registry authority."""

    country_code: Optional[str]
    country_name: Optional[str]
    registry_name: Optional[str]
    source: str
    confidence: float
    requested_country_code: Optional[str] = None
    detected_country_code: Optional[str] = None
    matched_terms: tuple[str, ...] = ()
    conflict: bool = False
    ambiguous: bool = False
    alternatives: tuple[Mapping[str, Any], ...] = ()

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
            "ambiguous": self.ambiguous,
            "alternatives": [dict(item) for item in self.alternatives],
        }


@dataclass(frozen=True)
class FieldConflict:
    """Materially different candidates retained during merge resolution."""

    field: str
    selected_value: Any
    candidate_values: tuple[Any, ...]
    resolution: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "selected_value": self.selected_value,
            "candidate_values": list(self.candidate_values),
            "resolution": self.resolution,
        }


@dataclass
class ParsedBusinessDocument:
    """Internal partial result before candidates are resolved and serialized."""

    data: Dict[str, Any]
    evidence: list[FieldEvidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    conflicts: list[FieldConflict | Mapping[str, Any]] = field(default_factory=list)


@dataclass
class BusinessDocumentResponse:
    """Stable top-level API response for business-document OCR."""

    success: bool
    message: Optional[str]
    document_type: str
    data: Mapping[str, Any]
    classification: Mapping[str, Any]
    jurisdiction: Mapping[str, Any]
    evidence: Iterable[FieldEvidence]
    warnings: Sequence[str]
    conflicts: Sequence[FieldConflict | Mapping[str, Any]]
    raw_text: str
    extraction: Mapping[str, Any] = field(default_factory=dict)
    overall_confidence: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        evidence_items = list(self.evidence)
        score = round(max(0.0, min(float(self.overall_confidence), 1.0)), 3)
        return {
            "success": bool(self.success),
            "message": self.message,
            "document_type": self.document_type,
            "overall_confidence": score,
            "confidence_level": confidence_level(score),
            "classification": dict(self.classification),
            "jurisdiction": dict(self.jurisdiction),
            "data": canonical_business_data(self.data),
            "field_confidence": field_confidence(evidence_items),
            "evidence": evidence_by_field(evidence_items),
            "warnings": list(dict.fromkeys(str(item) for item in self.warnings if str(item))),
            "conflicts": [_serialize_conflict(item) for item in self.conflicts],
            "extraction": dict(self.extraction),
            "raw_text": self.raw_text,
        }


def evidence_by_field(items: Iterable[FieldEvidence]) -> Dict[str, list[Dict[str, Any]]]:
    """Group evidence candidates by canonical field."""
    grouped: Dict[str, list[Dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(item.field, []).append(item.as_dict())
    return grouped


def field_confidence(items: Iterable[FieldEvidence]) -> Dict[str, Dict[str, Any]]:
    """Return the strongest selected evidence score for every field."""
    scores: Dict[str, float] = {}
    for item in items:
        if item.selected:
            scores[item.field] = max(scores.get(item.field, 0.0), float(item.confidence))
    return {
        key: {
            "score": round(max(0.0, min(score, 1.0)), 3),
            "level": confidence_level(score),
        }
        for key, score in sorted(scores.items())
    }


def _append_legacy_identifiers(items: list[Dict[str, Any]], source: Mapping[str, Any]) -> list[Dict[str, Any]]:
    legacy = (
        (
            source.get("registration_number"),
            source.get("registration_number_type") or "COMPANY_REGISTRATION_NUMBER",
            "COMPANY_REGISTRATION_NUMBER",
        ),
        (source.get("tax_identification_number"), "TAX_IDENTIFIER", "TAX_IDENTIFIER"),
    )
    seen = {(str(item.get("type")), str(item.get("normalized_value"))) for item in items}
    for value, number_type, identifier_type in legacy:
        cleaned = _clean_scalar(value)
        if not cleaned:
            continue
        normalized = re.sub(r"\s+", "", str(cleaned).upper())
        key = (identifier_type, normalized)
        compatible_types = (
            {
                "COMPANY_REGISTRATION_NUMBER",
                "BUSINESS_REGISTRATION_NUMBER",
                "STATE_FORMATION_IDENTIFIER",
                "REGISTRY_NUMBER",
            }
            if identifier_type == "COMPANY_REGISTRATION_NUMBER"
            else {identifier_type}
        )
        equivalent = any(
            existing_type in compatible_types
            and (existing_value == normalized or existing_value.endswith(normalized) or normalized.endswith(existing_value))
            for existing_type, existing_value in seen
        )
        if key in seen or equivalent:
            continue
        items.append(
            canonical_identifier(
                {
                    "type": identifier_type,
                    "number_type": number_type,
                    "value": cleaned,
                    "normalized_value": normalized,
                    "country_code": source.get("country_code"),
                    "issuing_authority": source.get("registry_name"),
                    "source": "legacy_field_extractor",
                }
            )
        )
        seen.add(key)
    return items


def _populate_role_lists(output: Dict[str, Any]) -> None:
    role_map = {
        "directors": {"DIRECTOR"},
        "shareholders": {"SHAREHOLDER", "SUBSCRIBER"},
        "beneficial_owners": {"BENEFICIAL_OWNER", "PERSON_WITH_SIGNIFICANT_CONTROL"},
    }
    raw_parties = output.get("parties")
    all_parties: list[Mapping[str, Any]] = (
        [party for party in raw_parties if isinstance(party, Mapping)] if isinstance(raw_parties, list) else []
    )
    for key, roles in role_map.items():
        if output.get(key):
            continue
        output[key] = [party for party in all_parties if roles.intersection(set(party.get("roles") or []))]


def _aliased_value(source: Mapping[str, Any], key: str) -> Any:
    aliases = _DATA_ALIASES.get(key, (key,))
    for alias in aliases:
        value = source.get(alias)
        if value not in (None, "", [], {}):
            return value
    return source.get(key)


def _canonical_language(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, Mapping):
        return {
            "code": _clean_scalar(value.get("code")),
            "name": _clean_scalar(value.get("name")),
            "confidence": _confidence_or_none(value.get("confidence")),
            "source": _clean_scalar(value.get("source")),
        }
    cleaned = _clean_scalar(value)
    return {"code": cleaned, "name": None, "confidence": None, "source": None} if cleaned else None


def _serialize_conflict(item: FieldConflict | Mapping[str, Any]) -> Dict[str, Any]:
    return item.as_dict() if isinstance(item, FieldConflict) else _clean_mapping(item)


def _clean_mapping(value: Mapping[str, Any]) -> Dict[str, Any]:
    return {str(key): _clean_scalar(item) for key, item in value.items()}


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


def _confidence_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(max(0.0, min(float(value), 1.0)), 3)
    except (TypeError, ValueError):
        return None


def _clean_hint(value: Optional[str], *, upper: bool = False) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()[:100]
    return cleaned.upper() if cleaned and upper else cleaned or None
