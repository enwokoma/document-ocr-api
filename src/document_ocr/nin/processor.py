"""Nigerian NIN card and slip extraction.

The parser reads text from an uploaded image, tries likely rotations, extracts
fields by label and pattern, and returns a stable JSON shape. Country selection
is handled through `src.countries` so future ID processors can share the route.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from src.core.flash_glance import flash_glance_hint
from src.core.ocr_engine import (
    get_document_engine,
    get_image_from_stream,
    improve_image_quality,
)
from src.countries.registry import country_validation_summary, get_country_profile


engine = get_document_engine()

CANONICAL_NIN_DATA_KEYS: Tuple[str, ...] = (
    "nin",
    "tracking_id",
    "surname",
    "first_name",
    "middle_name",
    "other_names",
    "full_name",
    "gender",
    "date_of_birth",
    "date_issued",
    "address",
)

_NIN_SCANLIKE_BRIGHT_LOW_SAT_PCT = 35.0
_NIN_SCANLIKE_SATURATION_MEAN = 8.0
_NIN_SCANLIKE_VALUE_MEAN = 185.0
_NIN_FLASH_VERY_BRIGHT_PCT = 12.0
_NIN_GLARE_MAX_COMPONENT_PCT = 2.5
_NIN_GLARE_MAX_COMPONENT_BBOX_PCT = 15.0


def _canonical_nin_data(raw: Optional[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    """
    Same JSON shape for NIN slip and NIN card: every key present; unknown fields are null.
    """
    src = raw or {}
    full_name = _build_full_name({k: v for k, v in src.items() if isinstance(v, str)}) or src.get("full_name")
    out: Dict[str, Optional[str]] = {}
    for key in CANONICAL_NIN_DATA_KEYS:
        if key == "full_name":
            v = full_name if isinstance(full_name, str) and full_name.strip() else None
        else:
            val = src.get(key)
            if val is None or (isinstance(val, str) and not val.strip()):
                v = None
            else:
                v = str(val).strip()
        out[key] = v
    return out


def nin_extraction_error(message: str, raw_text: str = "") -> Dict[str, Any]:
    """Build the standard error response for the NIN endpoint."""
    return {
        "success": False,
        "message": message,
        "document_type": "UNKNOWN",
        "data": _canonical_nin_data(None),
        "raw_text": raw_text,
    }


def _nin_quality_error(message: str, quality: Dict[str, Any], flash_glance: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build a standard NIN error response with image-quality diagnostics."""
    payload = nin_extraction_error(message)
    payload["quality"] = quality
    if flash_glance is not None:
        payload["flash_glance"] = flash_glance
    return payload


def _bright_component_stats(mask: np.ndarray) -> Dict[str, float | bool]:
    """Measure how much of a binary mask is occupied by bright components."""
    total = int(mask.size)
    if total <= 0:
        return {
            "pct": 0.0,
            "max_component_pct": 0.0,
            "max_component_bbox_pct": 0.0,
            "localized": False,
        }

    pct = 100.0 * float(np.count_nonzero(mask)) / float(total)
    component_pct = 0.0
    component_bbox_pct = 0.0
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype("uint8") * 255, 8)
    if count > 1:
        max_idx = int(stats[1:, cv2.CC_STAT_AREA].argmax()) + 1
        max_area = int(stats[max_idx, cv2.CC_STAT_AREA])
        component_pct = 100.0 * float(max_area) / float(total)
        bbox_area = int(stats[max_idx, cv2.CC_STAT_WIDTH]) * int(stats[max_idx, cv2.CC_STAT_HEIGHT])
        component_bbox_pct = 100.0 * float(bbox_area) / float(total)

    localized = (
        component_pct >= _NIN_GLARE_MAX_COMPONENT_PCT
        and component_bbox_pct <= _NIN_GLARE_MAX_COMPONENT_BBOX_PCT
    )
    return {
        "pct": round(pct, 2),
        "max_component_pct": round(component_pct, 2),
        "max_component_bbox_pct": round(component_bbox_pct, 2),
        "localized": bool(localized),
    }


def _nin_image_quality_issue(image) -> Optional[Dict[str, Any]]:
    """Reject images that look like scans/screenshots or contain strong glare."""
    if image is None or image.size == 0:
        return None

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    bright_low_saturation = (value >= 235) & (saturation <= 45)
    very_bright = value >= 245

    total = int(value.size)
    if total <= 0:
        return None

    scan_like = {
        "bright_low_saturation_pct": round(100.0 * float(np.count_nonzero(bright_low_saturation)) / float(total), 2),
        "saturation_mean": round(float(np.mean(saturation)), 2),
        "value_mean": round(float(np.mean(value)), 2),
    }
    scan_like["scan_like"] = bool(
        scan_like["bright_low_saturation_pct"] >= _NIN_SCANLIKE_BRIGHT_LOW_SAT_PCT
        and scan_like["saturation_mean"] <= _NIN_SCANLIKE_SATURATION_MEAN
        and scan_like["value_mean"] >= _NIN_SCANLIKE_VALUE_MEAN
    )
    if scan_like["scan_like"]:
        return _nin_quality_error(
            (
                "NIN image rejected. The upload looks like a scanned, copied, or screenshot NIN document "
                "instead of a live photo. Retake a live photo of the original NIN slip or card in frame."
            ),
            {"scan_like": scan_like},
            flash_glance_hint(image),
        )

    bright_stats = _bright_component_stats(very_bright & (saturation <= 35))
    flash_like = bool(bright_stats["pct"] >= _NIN_FLASH_VERY_BRIGHT_PCT or bright_stats["localized"])
    if flash_like:
        return _nin_quality_error(
            (
                "NIN image rejected. Flash, glare, or overexposure is covering part of the NIN document. "
                "Retake the photo in even light without reflection."
            ),
            {"flash_glare": bright_stats},
            flash_glance_hint(image),
        )

    return None


_FIELD_LABELS_RE = (
    r"Tracking\s*ID|"
    r"National\s+Identification\s+Number(?!\s+Slip)|"
    r"NIN|"
    r"Surname|"
    r"Last\s*Name|"
    r"Given\s*Name(?:s)?|"
    r"First\s*Name|"
    r"Middle\s*Name|"
    r"Other\s*Name(?:s)?|"
    r"Gender|"
    r"Sex|"
    r"Date\s*of\s*Birth|"
    r"DOB|"
    r"Date\s*(?:of\s+)?Issued?|"
    r"Issue\s*Date|"
    r"Address|"
    r"Note"
)

_LABEL_PATTERN = re.compile(
    rf"\b({_FIELD_LABELS_RE})\s*[:\-]",
    re.IGNORECASE,
)


def _normalize_label(label: str) -> Optional[str]:
    """Map different printed field labels to API response keys."""
    l = re.sub(r"\s+", " ", label.strip().lower())
    if l == "tracking id":
        return "tracking_id"
    if l in ("nin", "national identification number"):
        return "nin"
    if l in ("surname", "last name"):
        return "surname"
    if l in ("first name", "given name", "given names"):
        return "first_name"
    if l == "middle name":
        return "middle_name"
    if l in ("other name", "other names"):
        return "other_names"
    if l in ("gender", "sex"):
        return "gender"
    if l in ("date of birth", "dob"):
        return "date_of_birth"
    if l in (
        "date issued",
        "date of issued",
        "date of issue",
        "issue date",
        "issued",
    ):
        return "date_issued"
    if l == "address":
        return "address"
    return None


_RESERVED_NAME_WORDS = {
    # Slip headers / footers / orgs
    "FEDERAL", "REPUBLIC", "NIGERIA", "NATIONAL", "IDENTITY", "IDENTIFICATION",
    "MANAGEMENT", "MANAGEIMENT", "SYSTEM", "COMMISSION", "SLIP", "SLIPS",
    "NIMC", "NIMNC", "NINC", "NIN", "NINS",
    # Field labels (in case they leak into values)
    "TRACKING", "ID", "SURNAME", "GIVEN", "MIDDLE", "FIRST", "LAST", "OTHER",
    "NAME", "NAMES", "GENDER", "SEX", "ADDRESS", "DATE", "BIRTH", "ISSUED",
    "ISSUE", "DOB", "NOTE",
    # Address parts
    "STREET", "ROAD", "AVENUE", "WAY", "EXPRESS", "BUS", "STOP", "AREA",
    "NO", "ROUNDABOUT", "BLOCK", "FLAT", "APARTMENT", "ESTATE", "COURT",
    "DRIVE", "LANE", "BOULEVARD", "TERRACE", "RD", "ST", "AVE", "BLV",
    "BLVD", "PLAZA", "JUNCTION", "CRESCENT", "ZONE", "WUSE", "ABUJA",
    "OFF", "NORTH", "SOUTH", "EAST", "WEST",
    # Footer words
    "YOU", "WILL", "BE", "NOTIFIED", "CARD", "READY", "CONTACT", "PLEASE",
    "FOR", "ANY", "AND", "THE", "WHEN", "YOUR", "IS", "AT", "OR", "OF",
    "CALL", "GOV", "NG", "COM", "WWW", "HELPDESK", "SOKODE", "DALABA",
}


def _clean_value(value: str) -> str:
    """Normalize spacing and punctuation around an OCR field value."""
    v = value.replace("\r", " ").replace("\n", " ")
    v = re.sub(r"\s+", " ", v).strip(" :;-,.|")
    return v


def _validate_name(token: str) -> bool:
    """Return whether a token looks like a real name rather than a label."""
    if not token:
        return False
    if not re.fullmatch(r"[A-Za-z][A-Za-z'\-]{2,29}", token):
        return False
    return token.upper() not in _RESERVED_NAME_WORDS


_NAME_TOKEN_RE = re.compile(r"\b([A-Za-z][A-Za-z'\-]{2,29})\b")
_NIN_RE = re.compile(r"\b(\d{11})\b")
_TRACKING_RE = re.compile(r"\b([A-Za-z0-9]{8,30})\b")
_GENDER_RE = re.compile(r"(?<![A-Za-z])(MALE|FEMALE|M|F)(?![A-Za-z])", re.IGNORECASE)
_DATE_RES = [
    re.compile(r"\b(\d{1,2}[\-/\.]\d{1,2}[\-/\.]\d{2,4})\b"),
    re.compile(r"\b(\d{4}[\-/\.]\d{1,2}[\-/\.]\d{1,2})\b"),
    re.compile(r"\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})\b"),
    re.compile(r"\b([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4})\b"),
]


def _is_addressy(chunk: str) -> bool:
    """Detect whether text contains common address words."""
    return any(kw in chunk.upper() for kw in (
        "STREET", "ROAD", "AVENUE", " WAY", "EXPRESS", "BUS STOP",
        "AREA", "NO ", "NO.", "BLOCK ", "FLAT ", "ESTATE", "COURT",
        "DRIVE", "LANE", "BOULEVARD", "TERRACE", "PLAZA", "JUNCTION",
    ))


def _extract_first_name_token(chunk: str, *, max_tokens: int = 6, max_chars: int = 80) -> Optional[str]:
    """Pick the first plausible name token from a short OCR chunk."""
    if not chunk or len(chunk) > max_chars:
        return None
    cleaned = _clean_value(chunk)
    if not cleaned:
        return None
    tokens = cleaned.split()
    if len(tokens) > max_tokens:
        return None
    if _is_addressy(cleaned):
        return None
    for m in _NAME_TOKEN_RE.finditer(cleaned):
        v = m.group(1)
        if _validate_name(v):
            return v.upper()
    return None


def _extract_last_name_token(chunk: str, *, max_tokens: int = 6, max_chars: int = 80) -> Optional[str]:
    """Pick the last plausible name token from a short OCR chunk."""
    if not chunk or len(chunk) > max_chars:
        return None
    cleaned = _clean_value(chunk)
    if not cleaned:
        return None
    tokens = cleaned.split()
    if len(tokens) > max_tokens:
        return None
    if _is_addressy(cleaned):
        return None
    last = None
    for m in _NAME_TOKEN_RE.finditer(cleaned):
        v = m.group(1)
        if _validate_name(v):
            last = v.upper()
    return last


def _extract_nin(chunk: str, *, prefer: str = "first") -> Optional[str]:
    """Extract an 11-digit Nigerian NIN from a text chunk."""
    matches = list(_NIN_RE.finditer(chunk or ""))
    if not matches:
        digits = re.sub(r"\D", "", chunk or "")
        if len(digits) >= 11:
            return digits[:11]
        return None
    return matches[0].group(1) if prefer == "first" else matches[-1].group(1)


def _extract_tracking_id(chunk: str, *, prefer: str = "first") -> Optional[str]:
    """Extract a likely alphanumeric tracking ID from a text chunk."""
    candidates: List[Tuple[int, str]] = []
    for m in _TRACKING_RE.finditer(chunk or ""):
        v = m.group(1)
        if not (any(c.isalpha() for c in v) and any(c.isdigit() for c in v)):
            continue
        if re.fullmatch(r"\d{11}", v):
            continue
        candidates.append((m.start(), v.upper()))
    if not candidates:
        return None
    return candidates[0][1] if prefer == "first" else candidates[-1][1]


def _extract_gender(chunk: str, *, prefer: str = "first") -> Optional[str]:
    """Normalize gender text to `M` or `F` when it is present."""
    matches = list(_GENDER_RE.finditer(chunk or ""))
    if not matches:
        return None
    m = matches[0] if prefer == "first" else matches[-1]
    v = m.group(1).upper()
    return "M" if v in ("M", "MALE") else "F"


def _extract_date(chunk: str) -> Optional[str]:
    """Extract the first date-like value from a text chunk."""
    for p in _DATE_RES:
        m = p.search(chunk or "")
        if m:
            return m.group(1)
    return None


def _extract_for_field(key: str, chunk: str, *, prefer: str) -> Optional[str]:
    """Dispatch field extraction to the correct helper for one response key."""
    if not chunk or not chunk.strip():
        return None
    if key == "nin":
        return _extract_nin(chunk, prefer=prefer)
    if key == "tracking_id":
        return _extract_tracking_id(chunk, prefer=prefer)
    if key == "gender":
        return _extract_gender(chunk, prefer=prefer)
    if key in ("date_of_birth", "date_issued"):
        return _extract_date(chunk)
    if key in ("surname", "first_name", "middle_name"):
        return _extract_first_name_token(chunk) if prefer == "first" else _extract_last_name_token(chunk)
    if key == "other_names":
        cleaned = _clean_value(chunk)
        return cleaned.upper() if cleaned else None
    if key == "address":
        cleaned = _clean_value(chunk)
        if not cleaned:
            return None
        if _is_addressy(cleaned) or len(cleaned) > 25:
            return cleaned
        return None
    return _clean_value(chunk) or None


def _parse_fields(text: str) -> Dict[str, str]:
    """Parse fields with both forward (label: value) and reverse (value label:) patterns."""
    matches = list(_LABEL_PATTERN.finditer(text))
    if not matches:
        return {}

    extracted: Dict[str, str] = {}
    for i, m in enumerate(matches):
        key = _normalize_label(m.group(1))
        if not key or key in extracted:
            continue

        after_start = m.end()
        after_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        after_chunk = text[after_start:after_end]

        before_end = m.start()
        before_start = matches[i - 1].end() if i > 0 else 0
        before_chunk = text[before_start:before_end]

        forward_value = _extract_for_field(key, after_chunk, prefer="first")
        reverse_value = _extract_for_field(key, before_chunk, prefer="last")

        value = forward_value or reverse_value
        if value:
            extracted[key] = value

    return extracted


def _scan_address(text: str) -> Optional[str]:
    """Scan all label-delimited chunks and return the one most likely to be an address."""
    matches = list(_LABEL_PATTERN.finditer(text))
    if not matches:
        return _clean_value(text) if _is_addressy(text) else None

    boundaries = [0] + [p for m in matches for p in (m.start(), m.end())] + [len(text)]
    chunks: List[str] = []
    for i in range(0, len(boundaries) - 1, 2):
        start = boundaries[i]
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(text)
        chunks.append(text[start:end])

    best: Optional[Tuple[int, int, str]] = None
    for chunk in chunks:
        cleaned = _clean_value(chunk)
        if not cleaned or len(cleaned) < 15:
            continue
        score = sum(1 for kw in (
            "STREET", "ROAD", "AVENUE", "WAY", "EXPRESS", "BUS", "STOP",
            "AREA", "JUNCTION", "ESTATE", "COURT", "DRIVE", "LANE", "PLAZA",
        ) if kw in cleaned.upper())
        if score == 0:
            continue
        if best is None or (score, len(cleaned)) > (best[0], best[1]):
            best = (score, len(cleaned), cleaned)
    return best[2] if best else None


def _detect_document_type(text: str) -> str:
    """Classify the OCR text as a NIN slip, NIN card, or unknown document."""
    upper = text.upper()
    card_indicators = (
        "SURNAME/NOM",
        "GIVEN NAMES/PRENOMS",
        "GIVEN NAMES /PRENOMS",
        "SEX/SEXE",
        "DIGITAL NIN",
    )
    if any(k in upper for k in card_indicators):
        return "NIN_CARD"
    slip_keywords = (
        "TRACKING ID",
        "NATIONAL IDENTIFICATION NUMBER SLIP",
        "NIN SLIP",
        "NIMC",
        "NIMNC",
        "NINC",
        "NATIONAL IDENTITY MANAGEMENT",
        "IDENTITY MANAGEIMENT",
    )
    if any(k in upper for k in slip_keywords):
        return "NIN_SLIP"
    if "NATIONAL IDENTIFICATION NUMBER" in upper or "NATIONAL IDENTITY" in upper:
        return "NIN_SLIP"
    return "UNKNOWN"


_CARD_LABEL_LINE_RE = [
    (re.compile(r"^\s*SURNAME(?:\s*/\s*NOM)?\s*:?\s*$", re.IGNORECASE), "surname"),
    (
        re.compile(
            r"^\s*(?:GIVEN\s+NAMES?(?:\s*/\s*PR[EÉ]?NOMS?)?|FIRST\s+NAME(?:S)?)\s*:?\s*$",
            re.IGNORECASE,
        ),
        "first_name",
    ),
    (re.compile(r"^\s*MIDDLE\s+NAME(?:\s*/.*)?\s*:?\s*$", re.IGNORECASE), "middle_name"),
]


def _normalize_date_string(value: str) -> str:
    """Add missing spaces around OCR-merged date parts."""
    if not value:
        return value
    v = value.strip()
    v = re.sub(r"(\d)([A-Za-z])", r"\1 \2", v)
    v = re.sub(r"([A-Za-z])(\d)", r"\1 \2", v)
    v = re.sub(r"\s+", " ", v).strip()
    return v


def _parse_card(text: str) -> Dict[str, str]:
    """Extract fields from the NIN card layout."""
    result: Dict[str, str] = {}
    if not text:
        return result

    lines = text.split("\n")

    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        for pat, key in _CARD_LABEL_LINE_RE:
            if pat.match(line):
                for j in range(i + 1, min(i + 3, len(lines))):
                    nxt = lines[j].strip()
                    if not nxt:
                        continue
                    name = _extract_first_name_token(nxt)
                    if name:
                        result.setdefault(key, name)
                    break
                break

    date_pat = re.compile(
        r"(\d{1,2}\s*[A-Za-z]{3,9}\s*\d{2,4}|"
        r"\d{1,2}[\-/\.]\d{1,2}[\-/\.]\d{2,4}|"
        r"\d{4}[\-/\.]\d{1,2}[\-/\.]\d{1,2})",
        re.IGNORECASE,
    )
    dates = [m.group(1) for m in date_pat.finditer(text)]
    if dates:
        result.setdefault("date_of_birth", _normalize_date_string(dates[0]))
        if len(dates) >= 2:
            result.setdefault("date_issued", _normalize_date_string(dates[-1]))

    sex_match = re.search(
        r"SEX(?:\s*/\s*SEXE)?\s*[:\-]?\s*(?:\n|\s)\s*([MF])(?![A-Za-z])",
        text,
        re.IGNORECASE,
    )
    if sex_match:
        v = sex_match.group(1).upper()
        result.setdefault("gender", "M" if v == "M" else "F")
    else:
        for line in lines:
            stripped = line.strip()
            if re.fullmatch(r"[MF]", stripped, re.IGNORECASE):
                result.setdefault("gender", stripped.upper())
                break

    nin_label = re.search(
        r"NATIONAL\s+IDENTIFICATION\s+NUMBER(?:\s*\(NIN\))?\s*[:\-]?\s*\n?\s*(\d[\d\s\.]{9,20}\d)",
        text,
        re.IGNORECASE,
    )
    if nin_label:
        digits = re.sub(r"\D", "", nin_label.group(1))
        if len(digits) >= 11:
            result.setdefault("nin", digits[:11])
    if not result.get("nin"):
        m = _NIN_RE.search(text)
        if m:
            result["nin"] = m.group(1)
        else:
            digits_only = re.sub(r"\D", "", text)
            if len(digits_only) >= 11:
                for i in range(0, len(digits_only) - 10):
                    candidate = digits_only[i : i + 11]
                    if candidate[0] != "0":
                        result.setdefault("nin", candidate)
                        break

    return result


def _build_full_name(data: Dict[str, str]) -> Optional[str]:
    """Join available name fields into one display-friendly full name."""
    parts: List[str] = []
    for key in ("first_name", "middle_name", "other_names", "surname"):
        v = data.get(key)
        if v:
            parts.append(v)
    return " ".join(parts) if parts else None


def _rotate_image(image, angle: int):
    """Rotate an image by one of the angles used during OCR fallback."""
    if angle == 0 or image is None:
        return image
    if angle == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if angle == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    if angle == 270:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return image


def _ocr_text_for_image(image, enhance: bool = False) -> str:
    """Read text from an image, optionally enhancing contrast first."""
    if image is None:
        return ""
    target = improve_image_quality(image) if enhance else image
    boxes = engine.read_text_from_image(target)
    if not boxes:
        return ""
    return engine.group_boxes_into_lines(boxes)


def _score_extraction(extracted: Dict[str, str]) -> int:
    """Score an extraction so the best image rotation can be selected."""
    weights = {
        "nin": 5,
        "tracking_id": 3,
        "surname": 3,
        "first_name": 3,
        "middle_name": 2,
        "gender": 2,
        "address": 2,
        "date_of_birth": 1,
        "date_issued": 1,
    }
    return sum(w for k, w in weights.items() if extracted.get(k))


def _post_process(extracted: Dict[str, str]) -> Dict[str, str]:
    """Remove obvious OCR label words that slipped into name fields."""
    for key in ("surname", "first_name", "middle_name"):
        v = extracted.get(key)
        if v and v.upper() in _RESERVED_NAME_WORDS:
            extracted.pop(key, None)
    return extracted


def _extraction_good_enough(merged: Dict[str, str], score: int) -> bool:
    """
    Skip extra rotation passes when the first OCR orientation is already usable.
    Full rotation sweep is only needed when the image is crooked or text is sparse.
    """
    if score >= 18:
        return True
    if not merged.get("nin"):
        return False
    if score >= 14:
        return True
    if score >= 10 and (merged.get("surname") or merged.get("first_name")):
        return True
    return False


def extract_nin_from_image(file_stream, country_code: str = "NGA"):
    """Extract local ID data from an uploaded image stream.

    The current parser understands Nigerian NIN cards and slips. The country
    argument is already part of the function signature so future country-specific
    parsers can be selected without changing the route contract.
    """
    country_profile = get_country_profile(country_code)
    if country_profile is None:
        return {
            "success": False,
            "message": f"Unsupported country code for NIN extraction: {country_code}",
            "document_type": "UNKNOWN",
            "country": {
                "country_code": country_code,
                "country_name": None,
                "supported": False,
                "checks": {},
            },
            "data": _canonical_nin_data(None),
            "raw_text": "",
        }

    image = get_image_from_stream(file_stream)
    if image is None:
        return {
            "success": False,
            "message": "Invalid image format.",
            "document_type": "UNKNOWN",
            "data": _canonical_nin_data(None),
            "raw_text": "",
        }

    quality_issue = _nin_image_quality_issue(image)
    if quality_issue:
        return quality_issue

    best_text = ""
    best_extracted: Dict[str, str] = {}
    best_score = -1

    for idx, angle in enumerate((0, 90, 270, 180)):
        rotated = _rotate_image(image, angle)
        text = _ocr_text_for_image(rotated, enhance=(idx > 0))
        if not text:
            continue
        slip_extracted = _parse_fields(text)
        card_extracted = _parse_card(text)
        merged = {**card_extracted, **slip_extracted}
        for k, v in card_extracted.items():
            merged.setdefault(k, v)
        score = _score_extraction(merged)
        if score > best_score:
            best_score = score
            best_text = text
            best_extracted = merged
        if _extraction_good_enough(merged, score):
            break

    if not best_text:
        return {
            "success": False,
            "message": "Could not read any text from the image. Try a clearer photo.",
            "document_type": "UNKNOWN",
            "data": _canonical_nin_data(None),
            "raw_text": "",
        }

    extracted = _post_process(best_extracted)

    if not extracted.get("nin"):
        m = _NIN_RE.search(best_text)
        if m:
            extracted["nin"] = m.group(1)

    addr_scan = _scan_address(best_text)
    if addr_scan:
        extracted["address"] = addr_scan

    full_name = _build_full_name(extracted)
    if full_name:
        extracted["full_name"] = full_name

    document_type = _detect_document_type(best_text)
    success = bool(extracted.get("nin"))

    payload: Dict[str, Any] = {
        "success": success,
        "message": None,
        "document_type": document_type,
        "country": country_validation_summary(
            country_code=country_profile.code,
            document_type=document_type,
            extracted_data=extracted,
        ),
        "data": _canonical_nin_data(extracted),
        "raw_text": best_text,
    }
    if not success:
        payload["message"] = "Could not extract NIN from image."
    return payload
