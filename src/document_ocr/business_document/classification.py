"""Explainable classification for company and business-registry documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from src.document_ocr.business_document.schema import (
    ClassificationResult,
    UNKNOWN_BUSINESS_DOCUMENT,
    confidence_level,
)


@dataclass(frozen=True)
class MatchRule:
    """One weighted phrase or regular expression used during classification."""

    label: str
    pattern: str
    weight: float
    anchor: bool = False


@dataclass(frozen=True)
class DocumentSignature:
    """Weighted textual signature for one business-document category."""

    document_type: str
    rules: tuple[MatchRule, ...]
    high_score: float
    minimum_score: float = 3.0
    negative_patterns: tuple[str, ...] = ()


_SIGNATURES = (
    DocumentSignature(
        document_type="CERTIFICATE_OF_CHANGE_OF_NAME",
        high_score=10.0,
        rules=(
            MatchRule("certificate of change of name", r"\bCERTIFICATE\s+OF\s+(?:CHANGE|CHANGE\s+OF)\s+NAME\b", 9.0, True),
            MatchRule("change of name", r"\bCHANGE\s+OF\s+(?:COMPANY\s+)?NAME\b", 4.0, True),
            MatchRule("formerly known as", r"\bFORMERLY\s+KNOWN\s+AS\b", 3.0),
            MatchRule("new company name", r"\bNEW\s+(?:COMPANY\s+)?NAME\b", 2.0),
            MatchRule("registrar certification", r"\b(?:REGISTRAR|REGISTRAR[ -]GENERAL)\b", 1.0),
        ),
    ),
    DocumentSignature(
        document_type="CERTIFICATE_OF_INCORPORATION",
        high_score=11.0,
        rules=(
            MatchRule("certificate of incorporation", r"\bCERTIFICATE\s+OF\s+INCORPORATION\b", 8.0, True),
            MatchRule("incorporated under law", r"\bINCORPORATED\s+(?:UNDER|PURSUANT\s+TO)\b", 3.0, True),
            MatchRule("this is to certify", r"\b(?:THIS\s+IS\s+TO|I\s+HEREBY)\s+CERTIF(?:Y|IES)\b", 2.0),
            MatchRule("date of incorporation", r"\bDATE\s+OF\s+INCORPORATION\b", 2.0),
            MatchRule("company registration number", r"\b(?:COMPANY|REGISTRATION)\s+(?:NO|NUMBER)\b", 1.5),
            MatchRule("registrar seal", r"\b(?:REGISTRAR|OFFICIAL\s+SEAL)\b", 1.0),
        ),
        negative_patterns=(r"\bSTATUS\s+REPORT\b", r"\bMEMORANDUM\s+AND\s+ARTICLES\b"),
    ),
    DocumentSignature(
        document_type="CERTIFICATE_OF_REGISTRATION",
        high_score=10.0,
        rules=(
            MatchRule("certificate of registration", r"\bCERTIFICATE\s+OF\s+REGISTRATION\b", 8.0, True),
            MatchRule("business name registration", r"\bREGISTRATION\s+OF\s+(?:A\s+)?BUSINESS\s+NAME\b", 4.0, True),
            MatchRule("registered as", r"\b(?:HAS\s+BEEN|IS\s+HEREBY)\s+REGISTERED\b", 2.5),
            MatchRule("business registration number", r"\b(?:BN|BUSINESS\s+NUMBER|REGISTRATION\s+NUMBER)\b", 1.5),
            MatchRule("registrar certification", r"\b(?:REGISTRAR|REGISTRAR[ -]GENERAL)\b", 1.0),
        ),
        negative_patterns=(r"\bCERTIFICATE\s+OF\s+INCORPORATION\b",),
    ),
    DocumentSignature(
        document_type="COMPANY_STATUS_REPORT",
        high_score=12.0,
        rules=(
            MatchRule("company status report", r"\b(?:COMPANY|ENTITY|BUSINESS)?\s*STATUS\s+REPORT\b", 8.0, True),
            MatchRule("company profile", r"\b(?:COMPANY|ENTITY)\s+PROFILE\b", 4.0, True),
            MatchRule("general details section", r"\b(?:COMPANY|GENERAL|REGISTRATION)\s+DETAILS\b", 2.0),
            MatchRule("company status", r"\b(?:COMPANY|REGISTRATION)\s+STATUS\b", 2.0),
            MatchRule("registered address", r"\bREGISTERED\s+(?:OFFICE\s+)?ADDRESS\b", 1.5),
            MatchRule("directors section", r"\b(?:PARTICULARS\s+OF\s+)?DIRECTORS\b", 1.5),
            MatchRule("shareholders section", r"\b(?:SHAREHOLDERS|SHARE\s+CAPITAL|RETURN\s+OF\s+ALLOTMENT)\b", 1.5),
            MatchRule("current record language", r"\b(?:CURRENT\s+STATUS|AS\s+AT|ANNUAL\s+RETURNS)\b", 1.0),
        ),
    ),
    DocumentSignature(
        document_type="MEMORANDUM_AND_ARTICLES_OF_ASSOCIATION",
        high_score=12.0,
        rules=(
            MatchRule(
                "memorandum and articles of association",
                r"\bMEMORANDUM\s+(?:AND|&)\s+ARTICLES\s+OF\s+ASSOCIATION\b",
                9.0,
                True,
            ),
            MatchRule("MEMART", r"\bMEMART\b", 7.0, True),
            MatchRule("memorandum of association", r"\bMEMORANDUM\s+OF\s+ASSOCIATION\b", 3.5),
            MatchRule("articles of association", r"\bARTICLES\s+OF\s+ASSOCIATION\b", 3.5),
            MatchRule("company objects", r"\bOBJECTS?\s+(?:FOR\s+WHICH|OF)\s+THE\s+COMPANY\b", 1.5),
            MatchRule("subscriber statement", r"\b(?:SUBSCRIBERS?|SUBSCRIBED)\s+(?:TO|BY|HERETO)\b", 1.0),
            MatchRule("share capital clause", r"\bSHARE\s+CAPITAL\s+OF\s+THE\s+COMPANY\b", 1.0),
        ),
    ),
    DocumentSignature(
        document_type="MEMORANDUM_OF_ASSOCIATION",
        high_score=9.0,
        rules=(
            MatchRule("memorandum of association", r"\bMEMORANDUM\s+OF\s+ASSOCIATION\b", 7.0, True),
            MatchRule("company objects", r"\bOBJECTS?\s+(?:FOR\s+WHICH|OF)\s+THE\s+COMPANY\b", 2.0),
            MatchRule("registered office clause", r"\bREGISTERED\s+OFFICE\s+OF\s+THE\s+COMPANY\b", 1.0),
            MatchRule("subscriber statement", r"\b(?:SUBSCRIBERS?|SUBSCRIBED)\s+(?:TO|BY|HERETO)\b", 1.0),
        ),
        negative_patterns=(r"\bARTICLES\s+OF\s+ASSOCIATION\b",),
    ),
    DocumentSignature(
        document_type="ARTICLES_OF_ASSOCIATION",
        high_score=9.0,
        rules=(
            MatchRule("articles of association", r"\bARTICLES\s+OF\s+ASSOCIATION\b", 7.0, True),
            MatchRule("company constitution", r"\b(?:COMPANY\s+)?CONSTITUTION\b", 5.0, True),
            MatchRule("model articles", r"\bMODEL\s+ARTICLES\b", 2.0),
            MatchRule("interpretation article", r"\bINTERPRETATION\b.{0,80}\bARTICLES\b", 1.0),
            MatchRule("directors powers", r"\bPOWERS?\s+OF\s+(?:THE\s+)?DIRECTORS\b", 1.0),
        ),
        negative_patterns=(r"\bMEMORANDUM\s+OF\s+ASSOCIATION\b",),
    ),
    DocumentSignature(
        document_type="CERTIFIED_REGISTRY_EXTRACT",
        high_score=10.0,
        rules=(
            MatchRule("certified registry extract", r"\bCERTIFIED\s+(?:TRUE\s+COPY|EXTRACT)\b", 6.0, True),
            MatchRule("registry extract", r"\b(?:REGISTRY|REGISTER)\s+EXTRACT\b", 5.0, True),
            MatchRule("company search report", r"\b(?:COMPANY|OFFICIAL)\s+SEARCH\s+(?:REPORT|RESULT)\b", 4.0, True),
            MatchRule("extracted from register", r"\bEXTRACT(?:ED)?\s+FROM\s+THE\s+REGISTER\b", 3.0),
            MatchRule("registered particulars", r"\bREGISTERED\s+PARTICULARS\b", 2.0),
        ),
        negative_patterns=(r"\bSTATUS\s+REPORT\b",),
    ),
)


def normalize_classification_text(text: str) -> str:
    """Normalize OCR spacing and a few frequent joined business-document words."""
    value = str(text or "").upper().replace("\u2013", "-").replace("\u2014", "-")
    replacements = {
        "CERTIFICATEOFINCORPORATION": "CERTIFICATE OF INCORPORATION",
        "CERTIFICATEOFREGISTRATION": "CERTIFICATE OF REGISTRATION",
        "STATUSREPORT": "STATUS REPORT",
        "MEMORANDUMANDARTICLES": "MEMORANDUM AND ARTICLES",
        "MEMORANDUM&ARTICLES": "MEMORANDUM & ARTICLES",
        "ARTICLESOFASSOCIATION": "ARTICLES OF ASSOCIATION",
        "MEMORANDUMOFASSOCIATION": "MEMORANDUM OF ASSOCIATION",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return re.sub(r"\s+", " ", value).strip()


def classify_business_document(text: str) -> ClassificationResult:
    """Classify extracted text and retain the terms that drove the decision."""
    normalized = normalize_classification_text(text)
    if not normalized:
        return ClassificationResult(document_type=UNKNOWN_BUSINESS_DOCUMENT, confidence=0.0)

    scored = [_score_signature(normalized, signature) for signature in _SIGNATURES]
    scored.sort(key=lambda item: (item["confidence"], item["raw_score"]), reverse=True)
    best = scored[0]

    if best["raw_score"] < best["minimum_score"] or best["confidence"] < 0.40:
        return ClassificationResult(
            document_type=UNKNOWN_BUSINESS_DOCUMENT,
            confidence=min(0.39, best["confidence"]),
            matched_terms=tuple(best["matched_terms"]),
            alternatives=tuple(_serialize_alternatives(scored[:3])),
        )

    return ClassificationResult(
        document_type=str(best["document_type"]),
        confidence=float(best["confidence"]),
        matched_terms=tuple(best["matched_terms"]),
        alternatives=tuple(_serialize_alternatives(scored[1:4])),
    )


def looks_like_business_document(text: str, *, minimum_confidence: float = 0.40) -> bool:
    """Return whether OCR text has a recognizable business-document signature."""
    result = classify_business_document(text)
    return result.document_type != UNKNOWN_BUSINESS_DOCUMENT and result.confidence >= minimum_confidence


def classification_keywords() -> tuple[str, ...]:
    """Return human-readable signature terms for OCR page-quality scoring."""
    return tuple(dict.fromkeys(rule.label.upper() for signature in _SIGNATURES for rule in signature.rules))


def _score_signature(normalized: str, signature: DocumentSignature) -> dict[str, object]:
    matched_terms = []
    raw_score = 0.0
    anchor_found = False
    for rule in signature.rules:
        if re.search(rule.pattern, normalized, flags=re.IGNORECASE | re.DOTALL):
            matched_terms.append(rule.label)
            raw_score += rule.weight
            anchor_found = anchor_found or rule.anchor

    for pattern in signature.negative_patterns:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            raw_score -= 2.0

    raw_score = max(0.0, raw_score)
    confidence = min(raw_score / signature.high_score, 1.0)
    if not anchor_found:
        confidence = min(confidence, 0.58)

    # A company/legal-registry context reduces accidental classification of an
    # unrelated document containing one generic heading.
    context = bool(
        re.search(
            r"\b(?:COMPANY|BUSINESS|CORPORATION|INCORPORATED|LIMITED|LTD|PLC|LLC|REGISTRAR|REGISTRY|ASSOCIATION)\b",
            normalized,
        )
    )
    if not context:
        confidence *= 0.65

    return {
        "document_type": signature.document_type,
        "confidence": round(confidence, 4),
        "raw_score": round(raw_score, 3),
        "minimum_score": signature.minimum_score,
        "matched_terms": matched_terms,
    }


def _serialize_alternatives(items: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    alternatives = []
    for item in items:
        score = float(item["confidence"])
        if score <= 0:
            continue
        alternatives.append(
            {
                "document_type": item["document_type"],
                "confidence": round(score, 3),
                "confidence_level": confidence_level(score),
                "matched_terms": list(item["matched_terms"]),
            }
        )
    return alternatives
