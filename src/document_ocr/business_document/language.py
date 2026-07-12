"""Dependency-free, explainable language hints for registry documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.document_ocr.business_document.schema import confidence_level


@dataclass(frozen=True)
class LanguageResult:
    """Best-effort document language result."""

    code: str | None
    name: str | None
    confidence: float
    matched_terms: tuple[str, ...] = ()
    ambiguous: bool = False

    def as_dict(self) -> dict[str, Any]:
        score = round(max(0.0, min(float(self.confidence), 1.0)), 3)
        return {
            "code": self.code,
            "name": self.name,
            "confidence": score,
            "confidence_level": confidence_level(score),
            "matched_terms": list(self.matched_terms),
            "ambiguous": self.ambiguous,
            "source": "document_text" if self.code else "undetermined",
        }


_LANGUAGES: dict[str, tuple[str, tuple[tuple[str, float], ...]]] = {
    "en": (
        "English",
        (
            (r"\bCERTIFICATE\s+OF\b", 3.0),
            (r"\b(?:BUSINESS|TAX)\s+REGISTRATION\s+CERTIFICATE\b", 3.0),
            (r"\bTHIS\s+IS\s+TO\s+CERTIFY\b", 3.0),
            (r"\bREGISTERED\s+OFFICE\b", 2.0),
            (r"\bCOMPANY\s+(?:NUMBER|STATUS|NAME)\b", 1.5),
            (r"\bARTICLES\s+OF\b", 2.0),
        ),
    ),
    "fr": (
        "French",
        (
            (r"\bCERTIFICAT\s+(?:DE|D['’])", 3.0),
            (r"\bSOCI[ÉE]T[ÉE]\b", 2.0),
            (r"\bSI[ÈE]GE\s+SOCIAL\b", 2.5),
            (r"\bREGISTRE\s+(?:DU\s+COMMERCE|DES\s+SOCI[ÉE]T[ÉE]S)\b", 3.0),
            (r"\bDATE\s+DE\s+(?:CR[ÉE]ATION|CONSTITUTION)\b", 2.0),
        ),
    ),
    "pt": (
        "Portuguese",
        (
            (r"\bCERTID[ÃA]O\s+DE\b", 3.0),
            (r"\bSOCIEDADE\b", 2.0),
            (r"\bSEDE\s+SOCIAL\b", 2.5),
            (r"\bREGISTO\s+COMERCIAL\b|\bREGISTRO\s+COMERCIAL\b", 3.0),
            (r"\bDATA\s+DE\s+CONSTITUI[ÇC][ÃA]O\b", 2.0),
        ),
    ),
    "es": (
        "Spanish",
        (
            (r"\bCERTIFICADO\s+DE\b", 3.0),
            (r"\bSOCIEDAD\b", 2.0),
            (r"\bDOMICILIO\s+SOCIAL\b", 2.5),
            (r"\bREGISTRO\s+MERCANTIL\b", 3.0),
            (r"\bFECHA\s+DE\s+CONSTITUCI[ÓO]N\b", 2.0),
        ),
    ),
    "de": (
        "German",
        (
            (r"\bGR[ÜU]NDUNGSURKUNDE\b|\bURKUNDE\b", 3.0),
            (r"\bGESELLSCHAFT\b", 2.0),
            (r"\bHANDELSREGISTER\b", 3.0),
            (r"\bSITZ\s+DER\s+GESELLSCHAFT\b", 2.5),
        ),
    ),
    "nl": (
        "Dutch",
        (
            (r"\bOPRICHTINGSAKTE\b|\bAKTE\s+VAN\s+OPRICHTING\b", 3.0),
            (r"\bVENNOOTSCHAP\b", 2.0),
            (r"\bHANDELSREGISTER\b", 3.0),
            (r"\bSTATUTAIRE\s+ZETEL\b", 2.5),
        ),
    ),
}


def detect_document_language(text: str) -> LanguageResult:
    """Infer language only when registry wording supplies enough evidence."""
    value = re.sub(r"\s+", " ", str(text or "").upper()).strip()
    if not value:
        return LanguageResult(None, None, 0.0)

    scored: list[tuple[float, str, str, tuple[str, ...]]] = []
    for code, (name, patterns) in _LANGUAGES.items():
        score = 0.0
        matched: list[str] = []
        for pattern, weight in patterns:
            match = re.search(pattern, value, flags=re.IGNORECASE)
            if match:
                score += weight
                matched.append(" ".join(match.group(0).split()))
        scored.append((score, code, name, tuple(matched)))
    scored.sort(reverse=True)
    best_score, code, name, best_matches = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if best_score < 2.0:
        return LanguageResult(None, None, min(best_score / 5.0, 0.39), best_matches)
    ambiguous = second_score > 0 and best_score - second_score < 1.0
    confidence = min(0.98, 0.45 + best_score / 12.0)
    if ambiguous:
        confidence = min(confidence, 0.59)
    return LanguageResult(code, name, confidence, best_matches, ambiguous)
