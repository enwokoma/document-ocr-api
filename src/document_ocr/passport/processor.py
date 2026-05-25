import re
import cv2
import numpy as np
from src.core.flash_glance import flash_glance_hint
from src.core.ocr_engine import get_document_engine, get_image_from_stream, improve_image_quality

engine = get_document_engine()

_TD3_ALLOWED_RE = re.compile(r"^[A-Z0-9<]+$")
_TD3_DATE_RE = re.compile(r"^[0-9]{6}$")
_TD3_COUNTRY_RE = re.compile(r"^[A-Z]{3}$")
_NIGERIA_COUNTRY_OCR_ALIASES = {"NGA", "NGE", "NG4", "N6A", "N64", "NGR"}
_MRZ_GLARE_MAX_COMPONENT_PCT = 0.8
_DATA_GLARE_MAX_COMPONENT_PCT = 1.5
_LOCAL_GLARE_MAX_COMPONENT_PCT = 25.0
_SCANLIKE_BRIGHT_LOW_SAT_PCT = 55.0
_SCANLIKE_VERY_BRIGHT_PCT = 30.0
_SCANLIKE_SATURATION_MEAN = 25.0
_LIVE_CONTEXT_BORDER_SATURATION_MEAN = 55.0
_LIVE_CONTEXT_BORDER_INNER_COLOR_DIFF = 70.0
_LIVE_CONTEXT_BORDER_EDGE_PCT = 6.0
_OCR_MAX_WIDTH = 1800
_FIELD_LABEL_WORDS = {
    "AUTHORITY",
    "AUTORITE",
    "CODE",
    "COUNTRY",
    "DATE",
    "DATEOF",
    "DE",
    "DELIVRANCE",
    "DU",
    "EXPIRATION",
    "EXPIRY",
    "FEDERAL",
    "GIVEN",
    "GIVENNAMES",
    "GLVEN",
    "HOLDER",
    "ISSUE",
    "LIEU",
    "NAME",
    "NAMES",
    "NATIONALITY",
    "NATIONALITE",
    "NATIONALTY",
    "NATIONALTE",
    "NAISSANCE",
    "NOM",
    "NOMS",
    "PASSPORT",
    "PASSEPORT",
    "PAYS",
    "PLACE",
    "PRENOM",
    "PRENOMS",
    "PREVIOUS",
    "PRECEDENT",
    "REPUBLIC",
    "SEXE",
    "SIGNATURE",
    "SURNAME",
    "TITULAIRE",
    "TYPE",
}

_FIELD_LABEL_RE = re.compile(
    r"\b("
    r"SUR\s*NAME|SURNAME|SURNAM[E]?|SUMAME|NOM|"
    r"GIVEN\s*NAMES?|GIVENNAMES|GLVEN|GIV[EF]N|PRENOMS?|"
    r"NATIONALITY|NATIONALITE|NATIONALTY|NATIONALTE|"
    r"DATE\s*OF|DATEOF|BIRTH|ISSUE|EXPIRY|EXPIRATION|"
    r"SEX|SEXE|PLACE\s*OF|PASSPORT|PASSEPORT|NIN|"
    r"AUTHORITY|AUTORITE|HOLDER|SIGNATURE|PREVIOUS|PRECEDENT"
    r")\b",
    re.IGNORECASE,
)


def _looks_like_td3_line(line: str) -> bool:
    return isinstance(line, str) and len(line) == 44 and bool(_TD3_ALLOWED_RE.match(line))


def _looks_like_td3_line1(line1: str) -> bool:
    if not _looks_like_td3_line(line1):
        return False
    if not re.fullmatch(r"[A-Z<]{2}", line1[0:2] or ""):
        return False
    if not _TD3_COUNTRY_RE.match(line1[2:5] or ""):
        return False
    if "<<" not in line1[5:44]:
        return False
    return True


def _looks_like_td3_line2(line2: str) -> bool:
    if not _looks_like_td3_line(line2):
        return False
    if not re.fullmatch(r"[A-Z0-9<]{9}", line2[0:9] or ""):
        return False
    if not line2[9].isdigit():
        return False
    if not re.fullmatch(r"[A-Z]{3}", line2[10:13] or ""):
        return False
    if not _TD3_DATE_RE.match(line2[13:19] or ""):
        return False
    if not line2[19].isdigit():
        return False
    if line2[20] not in ("M", "F", "<"):
        return False
    if not _TD3_DATE_RE.match(line2[21:27] or ""):
        return False
    if not line2[27].isdigit():
        return False
    if not re.fullmatch(r"[A-Z0-9<]{14}", line2[28:42] or ""):
        return False
    if not line2[42].isdigit():
        return False
    if not line2[43].isdigit():
        return False
    return True


def _clean_mrz_line(line: str) -> str:
    clean_line = (line or "").upper().replace(" ", "").strip()
    clean_line = re.sub(r"[^A-Z0-9<]", "", clean_line)
    return clean_line


def _normalize_td3_line1(line1: str) -> str:
    if not line1 or len(line1) != 44:
        return line1
    chars = list(line1)
    for idx in range(5, 44):
        if chars[idx] == "0":
            chars[idx] = "O"
        elif chars[idx] == "1":
            chars[idx] = "I"
        elif chars[idx] == "5":
            chars[idx] = "S"
    return "".join(chars)


def _mrz_check_digit(value: str) -> str:
    weights = [7, 3, 1]
    total = 0
    for idx, char in enumerate(value):
        if "0" <= char <= "9":
            val = int(char)
        elif "A" <= char <= "Z":
            val = ord(char) - 55
        elif char == "<":
            val = 0
        else:
            val = 0
        total += val * weights[idx % 3]
    return str(total % 10)


def _td3_check_digits_valid(line2: str) -> bool:
    if not _looks_like_td3_line2(line2):
        return False
    return (
        _mrz_check_digit(line2[0:9]) == line2[9]
        and _mrz_check_digit(line2[13:19]) == line2[19]
        and _mrz_check_digit(line2[21:27]) == line2[27]
        and _mrz_check_digit(line2[28:42]) == line2[42]
        and _mrz_check_digit(line2[0:10] + line2[13:20] + line2[21:43]) == line2[43]
    )


def _glare_stats(bgr) -> dict:
    if bgr is None or bgr.size == 0:
        return {"pct": 0.0, "max_component_pct": 0.0, "localized": False}
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    mask = ((value >= 245) & (saturation <= 35)).astype("uint8")
    total = int(mask.size)
    pct = 100.0 * float(np.count_nonzero(mask)) / float(total) if total else 0.0
    component_pct = 0.0
    component_bbox_pct = 0.0
    if total:
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask * 255, 8)
        if count > 1:
            max_idx = int(stats[1:, cv2.CC_STAT_AREA].argmax()) + 1
            max_area = int(stats[max_idx, cv2.CC_STAT_AREA])
            component_pct = 100.0 * float(max_area) / float(total)
            bbox_area = int(stats[max_idx, cv2.CC_STAT_WIDTH]) * int(stats[max_idx, cv2.CC_STAT_HEIGHT])
            component_bbox_pct = 100.0 * float(bbox_area) / float(total)
    localized = (
        component_pct > 0.0
        and component_pct <= _LOCAL_GLARE_MAX_COMPONENT_PCT
        and component_bbox_pct <= _LOCAL_GLARE_MAX_COMPONENT_PCT
    )
    return {
        "pct": round(pct, 2),
        "max_component_pct": round(component_pct, 2),
        "max_component_bbox_pct": round(component_bbox_pct, 2),
        "localized": bool(localized),
    }


def _scan_like_stats(bgr) -> dict:
    if bgr is None or bgr.size == 0:
        return {
            "bright_low_saturation_pct": 0.0,
            "very_bright_pct": 0.0,
            "saturation_mean": 0.0,
            "scan_like": False,
        }

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    total = int(value.size)
    if total <= 0:
        return {
            "bright_low_saturation_pct": 0.0,
            "very_bright_pct": 0.0,
            "saturation_mean": 0.0,
            "scan_like": False,
        }

    bright_low_saturation_pct = 100.0 * float(np.count_nonzero((value >= 235) & (saturation <= 45))) / float(total)
    very_bright_pct = 100.0 * float(np.count_nonzero(value >= 245)) / float(total)
    saturation_mean = float(np.mean(saturation))
    scan_like = (
        bright_low_saturation_pct >= _SCANLIKE_BRIGHT_LOW_SAT_PCT
        and very_bright_pct >= _SCANLIKE_VERY_BRIGHT_PCT
        and saturation_mean <= _SCANLIKE_SATURATION_MEAN
    )

    return {
        "bright_low_saturation_pct": round(bright_low_saturation_pct, 2),
        "very_bright_pct": round(very_bright_pct, 2),
        "saturation_mean": round(saturation_mean, 2),
        "scan_like": bool(scan_like),
    }


def _live_capture_context_stats(bgr) -> dict:
    if bgr is None or bgr.size == 0:
        return {
            "border_saturation_mean": 0.0,
            "border_inner_color_diff": 0.0,
            "border_edge_pct": 0.0,
            "live_context": False,
        }

    h, w = bgr.shape[:2]
    margin = max(6, int(min(h, w) * 0.04))
    if h <= margin * 2 or w <= margin * 2:
        return {
            "border_saturation_mean": 0.0,
            "border_inner_color_diff": 0.0,
            "border_edge_pct": 0.0,
            "live_context": False,
        }

    border_pixels = np.concatenate(
        [
            bgr[:margin].reshape(-1, 3),
            bgr[-margin:].reshape(-1, 3),
            bgr[:, :margin].reshape(-1, 3),
            bgr[:, -margin:].reshape(-1, 3),
        ]
    )
    inner = bgr[margin : h - margin, margin : w - margin]
    border_hsv = cv2.cvtColor(border_pixels.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 160)
    border_mask = np.zeros_like(edges)
    border_mask[:margin, :] = 255
    border_mask[-margin:, :] = 255
    border_mask[:, :margin] = 255
    border_mask[:, -margin:] = 255
    border_area = int(np.count_nonzero(border_mask))
    border_edge_pct = 100.0 * float(np.count_nonzero(edges & border_mask)) / float(border_area) if border_area else 0.0
    border_saturation_mean = float(np.mean(border_hsv[:, 1]))
    border_inner_color_diff = float(np.linalg.norm(np.mean(border_pixels, axis=0) - np.mean(inner, axis=(0, 1))))
    live_context = (
        border_saturation_mean >= _LIVE_CONTEXT_BORDER_SATURATION_MEAN
        and border_inner_color_diff >= _LIVE_CONTEXT_BORDER_INNER_COLOR_DIFF
        and border_edge_pct >= _LIVE_CONTEXT_BORDER_EDGE_PCT
    )

    return {
        "border_saturation_mean": round(border_saturation_mean, 2),
        "border_inner_color_diff": round(border_inner_color_diff, 2),
        "border_edge_pct": round(border_edge_pct, 2),
        "live_context": bool(live_context),
    }


def _document_quality_issue(image) -> dict | None:
    h, w = image.shape[:2]
    mrz_zone = image[int(h * 0.75):h, 0:w]
    data_zone = image[int(h * 0.35):int(h * 0.95), 0:w]
    mrz_glare = _glare_stats(mrz_zone)
    data_glare = _glare_stats(data_zone)
    scan_like = _scan_like_stats(image)
    live_context = _live_capture_context_stats(image)
    if scan_like["scan_like"] and not live_context["live_context"]:
        return {
            "success": False,
            "message": (
                "Passport image rejected. The upload looks like a scanned, copied, or screenshot passport page "
                "instead of a live photo. Retake a live photo of the original passport with the full data page in frame."
            ),
            "quality": {
                "scan_like": scan_like,
                "live_capture_context": live_context,
            },
        }
    if (
        (mrz_glare["localized"] and mrz_glare["max_component_pct"] >= _MRZ_GLARE_MAX_COMPONENT_PCT)
        or (data_glare["localized"] and data_glare["max_component_pct"] >= _DATA_GLARE_MAX_COMPONENT_PCT)
    ):
        return {
            "success": False,
            "message": (
                "Passport image rejected. Glare or overexposure is covering part of the data page or MRZ. "
                "Retake the photo with the full data page flat, in frame, and without reflection."
            ),
            "quality": {
                "mrz_glare": mrz_glare,
                "data_page_glare": data_glare,
            },
        }
    return None


def _is_probably_nigerian(country: str, nationality: str = "") -> bool:
    return country in _NIGERIA_COUNTRY_OCR_ALIASES or nationality == "NGA"


def _correct_line1_country(line1: str, line2: str) -> str:
    if not _looks_like_td3_line(line1) or len(line2) < 13:
        return line1

    country = line1[2:5]
    nationality = line2[10:13]
    if country != "NGA" and _is_probably_nigerian(country, nationality):
        return line1[:2] + "NGA" + line1[5:]
    return line1


def _clean_visual_line(line: str) -> str:
    return re.sub(r"[^A-Z0-9 /-]", " ", (line or "").upper()).strip()


def _strip_field_words(line: str) -> str:
    words = [
        word
        for word in _clean_visual_line(line).split()
        if re.fullmatch(r"[A-Z0-9]+", word) and word not in _FIELD_LABEL_WORDS
    ]
    return " ".join(words).strip()


def _line_has_field_label(line: str) -> bool:
    words = set(_clean_visual_line(line).split())
    return bool(words & _FIELD_LABEL_WORDS) or bool(_FIELD_LABEL_RE.search(_clean_visual_line(line)))


def _is_likely_field_value(line: str, *, letters_only: bool = True) -> bool:
    cleaned = _strip_field_words(line)
    if not cleaned:
        return False
    words = [word for word in cleaned.split() if word]
    if not words:
        return False
    if letters_only:
        return bool(re.fullmatch(r"[A-Z][A-Z -]{1,39}", cleaned))
    return True


def _next_visual_value(lines, label_pattern, *, letters_only=True):
    candidates = _visual_value_candidates(lines, label_pattern, letters_only=letters_only)
    return _best_visual_value(candidates, letters_only=letters_only)


def _visual_value_candidates(lines, label_pattern, *, letters_only=True):
    label_re = re.compile(label_pattern, re.IGNORECASE)
    candidates = []
    for idx, line in enumerate(lines):
        if not label_re.search(line):
            continue

        label_end = _strip_field_words(line)
        if _is_likely_field_value(label_end, letters_only=letters_only):
            candidates.append(label_end)

        for nxt in lines[idx + 1: idx + 5]:
            if _line_has_field_label(nxt):
                break
            cleaned_next = _strip_field_words(nxt)
            if _is_likely_field_value(cleaned_next, letters_only=letters_only):
                candidates.append(cleaned_next)
                break
    return candidates


def _visual_value_score(value: str, *, letters_only: bool = True) -> int:
    cleaned = _strip_field_words(value)
    if not cleaned:
        return -100

    words = cleaned.split()
    compact = "".join(words)
    score = len(compact)
    if letters_only:
        if not re.fullmatch(r"[A-Z ]+", cleaned):
            score -= 20
        if len(compact) <= 2:
            score -= 8
        if len(words) > 2:
            score -= 5 * (len(words) - 2)
    return score


def _best_visual_value(candidates, *, letters_only: bool = True):
    if not candidates:
        return ""
    return max(candidates, key=lambda item: _visual_value_score(item, letters_only=letters_only))


def _parse_visual_date(value: str):
    value = _clean_visual_line(value.replace("[", "1"))
    match = re.search(
        r"\b([0-9]{1,2})\s*"
        r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s*"
        r"(?:(?:/)?\s*(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s*)?"
        r"([0-9]{2,4})\b",
        value,
    )
    if not match:
        return None

    months = {
        "JAN": "01",
        "FEB": "02",
        "MAR": "03",
        "APR": "04",
        "MAY": "05",
        "JUN": "06",
        "JUL": "07",
        "AUG": "08",
        "SEP": "09",
        "OCT": "10",
        "NOV": "11",
        "DEC": "12",
    }
    day, month, year = match.groups()
    if len(year) == 2:
        year = ("19" if int(year) > 40 else "20") + year
    return f"{year}-{months[month]}-{int(day):02d}"


def _visual_date_score(value: str) -> int:
    value = _clean_visual_line(value.replace("[", "1"))
    match = re.search(r"\b([0-9]{1,2})\s*(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)", value)
    if not match:
        return -1
    score = 0
    if len(match.group(1)) == 2:
        score += 10
    if "/" in value:
        score += 3
    return score


def _name_to_mrz(value: str) -> str:
    return re.sub(r"[^A-Z<]", "", re.sub(r"[\s-]+", "<", (value or "").upper()))


def _build_line1(document_type: str, country: str, surname: str, given_names: str) -> str:
    doc_code = _name_to_mrz(document_type)[:2]
    if len(doc_code) < 2:
        doc_code = doc_code.ljust(2, "<")
    country_code = _name_to_mrz(country)[:3].ljust(3, "<")
    name_part = f"{_name_to_mrz(surname)}<<{_name_to_mrz(given_names)}".rstrip("<")
    return (doc_code + country_code + name_part).ljust(44, "<")[:44]


def _parse_names_from_line1(line1: str):
    names_section = line1[5:].split("<<")
    surname = names_section[0].replace("<", " ").strip() if len(names_section) > 0 else ""
    given_names = names_section[1].replace("<", " ").strip() if len(names_section) > 1 else ""
    return surname, given_names


def _should_prefer_visual_name(mrz_name: str, visual_name: str) -> bool:
    visual_clean = _strip_field_words(visual_name)
    mrz_compact = re.sub(r"[^A-Z]", "", (mrz_name or "").upper())
    visual_compact = re.sub(r"[^A-Z]", "", visual_clean.upper())
    if not visual_compact or len(visual_compact) < 3:
        return False
    if not re.fullmatch(r"[A-Z][A-Z -]{2,39}", visual_clean):
        return False
    if not mrz_compact:
        return True
    if mrz_compact == visual_compact:
        return False
    if len(mrz_compact) <= 4 and len(visual_compact) >= len(mrz_compact) + 2:
        return True
    return False


def _mrz_line1_score(line1: str, line2: str) -> int:
    line1 = _correct_line1_country(_normalize_td3_line1(line1), line2)
    if not _looks_like_td3_line1(line1):
        return -100

    surname, given_names = _parse_names_from_line1(line1)
    compact_surname = surname.replace(" ", "")
    compact_given = given_names.replace(" ", "")
    score = 0
    score += 20 if line1[0] == "P" else 0
    score += 20 if line1[2:5] == "NGA" else 0
    score += min(len(compact_surname), 12)
    score += min(len(compact_given), 12)
    if " " in given_names:
        score += 8
    if len(compact_surname) < 2:
        score -= 12
    if len(compact_given) < 3:
        score -= 12
    if any(char.isdigit() for char in compact_surname + compact_given):
        score -= 20
    return score


def _select_valid_mrz(mrz_candidates):
    best_pair = []
    best_score = -999
    for i in range(len(mrz_candidates) - 1):
        line1 = _normalize_td3_line1(mrz_candidates[i])
        line2 = mrz_candidates[i + 1]
        if not (line1.startswith("P") or "NGA" in line1[:10] or line2[10:13] == "NGA"):
            continue
        if not _looks_like_td3_line2(line2):
            continue

        score = _mrz_line1_score(line1, line2)
        if score > best_score:
            best_pair = [_correct_line1_country(line1, line2), line2]
            best_score = score

    if best_pair:
        return best_pair
    if len(mrz_candidates) >= 2:
        return mrz_candidates[-2:]
    return []


def _mrz_candidates_from_text(text: str) -> list[str]:
    mrz_candidates = []
    for line in text.split("\n"):
        clean_line = _clean_mrz_line(line)
        if len(clean_line) >= 25 and "<" in clean_line:
            mrz_candidates.append(clean_line.ljust(44, "<")[:44])
    return mrz_candidates


def _image_variants(image, *, mode="default"):
    image = _resize_for_ocr(image)
    if mode == "single":
        return [image]
    variants = [image, improve_image_quality(image)]
    if mode == "fast":
        return variants

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray)
    variants.append(cv2.cvtColor(clahe, cv2.COLOR_GRAY2BGR))

    if image.shape[1] < 1200:
        scaled = cv2.resize(image, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
        variants.append(improve_image_quality(scaled))
    return variants


def _resize_for_ocr(image):
    h, w = image.shape[:2]
    if w <= _OCR_MAX_WIDTH:
        return image
    scale = _OCR_MAX_WIDTH / float(w)
    return cv2.resize(image, (_OCR_MAX_WIDTH, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)


def _read_text_with_variants(image, *, mode="default"):
    seen = set()
    lines = []
    for variant in _image_variants(image, mode=mode):
        boxes = engine.read_text_from_image(variant)
        text = engine.group_boxes_into_lines(boxes)
        for line in text.split("\n"):
            cleaned = line.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                lines.append(cleaned)
    return "\n".join(lines)


def _extract_issue_date_from_lines(lines):
    label_re = re.compile(r"\bDATE\s*OF\s*ISSUE\b|\bDELIVRANCE\b", re.IGNORECASE)
    parsed_candidates = []
    for idx, line in enumerate(lines):
        if not label_re.search(line):
            continue
        for candidate in [line] + lines[idx + 1: idx + 4]:
            parsed = _parse_visual_date(candidate)
            if parsed:
                parsed_candidates.append((parsed, _visual_date_score(candidate)))
    if not parsed_candidates:
        return None
    return max(parsed_candidates, key=lambda item: item[1])[0]


def _extract_visual_fields(image):
    height, width = image.shape[:2]
    data_page_crop = image[int(height * 0.40):int(height * 0.88), 0:width]
    text = _read_text_with_variants(data_page_crop, mode="single")
    lines = [line for line in text.split("\n") if line.strip()]

    fields = {
        "raw_text": text,
        "surname": _next_visual_value(lines, r"\bSUR\s*NAME\b|\bSURNAM[E]?\b|\bSUMAME\b|\bNOM\b"),
        "given_names": _next_visual_value(lines, r"\bGIVEN(?:\s+NAMES?)?\b|\bGIV[EF]N\b|\bPRENOMS?\b"),
        "date_of_issue": None,
    }

    issue_value = _next_visual_value(lines, r"\bDATE\s+OF\s+ISSUE\b", letters_only=False)
    fields["date_of_issue"] = _extract_issue_date_from_lines(lines) or _parse_visual_date(issue_value)
    return fields


def extract_mrz_from_image(file_stream):
    image = get_image_from_stream(file_stream)
    if image is None:
        return {"success": False, "message": "Invalid image format."}

    glance = flash_glance_hint(image)

    def with_glance(payload: dict) -> dict:
        out = dict(payload)
        if glance is not None:
            out["flash_glance"] = glance
        return out

    quality_issue = _document_quality_issue(image)
    if quality_issue:
        return with_glance(quality_issue)

    height, width = image.shape[:2]
    mrz_crop = image[int(height * 0.72):height, 0:width]
    if not engine.is_available():
        return with_glance(
            {
                "success": False,
                "message": (
                    "OCR backend unavailable. Install Python 3.12 with rapidocr-onnxruntime, "
                    "or set ENABLE_EASYOCR_FALLBACK=1 to use the slower EasyOCR fallback."
                ),
                "raw_text_detected": "",
            }
        )

    text = _read_text_with_variants(mrz_crop, mode="single")
    mrz_candidates = _mrz_candidates_from_text(text)
    valid_mrz = _select_valid_mrz(mrz_candidates)
    if len(valid_mrz) < 2:
        fallback_text = _read_text_with_variants(mrz_crop, mode="fast")
        fallback_candidates = _mrz_candidates_from_text(fallback_text)
        fallback_mrz = _select_valid_mrz(fallback_candidates)
        if len(fallback_mrz) >= 2:
            text = "\n".join(line for line in [text, fallback_text] if line)
            mrz_candidates = fallback_candidates
            valid_mrz = fallback_mrz

    if len(valid_mrz) < 2:
        return with_glance(
            {
                "success": False,
                "message": "Invalid passport",
                "raw_text_detected": text,
            }
        )

    line1 = valid_mrz[0]
    line2 = valid_mrz[1]
    line1 = _correct_line1_country(line1, line2)

    if not _looks_like_td3_line1(line1) or not _looks_like_td3_line2(line2):
        return with_glance(
            {
                "success": False,
                "message": "MRZ looks invalid or incomplete. Retake a clear photo with the full MRZ strip in frame.",
                "raw_text_detected": text,
                "mrz_raw": [line1, line2],
            }
        )

    prefix = line2[0]
    if prefix == "6" or prefix == "8":
        line2 = "B" + line2[1:]

    document_type = line1[0:2].replace("<", "")
    country = line1[2:5].replace("<", "")
    surname, given_names = _parse_names_from_line1(line1)
    if surname and given_names:
        line1 = _build_line1(document_type, country, surname, given_names)

    passport_num_raw = line2[0:9]
    passport_check_digit = line2[9]

    if _mrz_check_digit(passport_num_raw) != passport_check_digit:
        confusions = {
            "2": ["5"],
            "5": ["2", "S"],
            "8": ["B", "3"],
            "B": ["8", "6"],
            "0": ["O", "D"],
            "O": ["0"],
            "6": ["B", "8"],
            "S": ["5"],
        }
        chars = list(passport_num_raw)
        fixed = False
        for idx in range(len(chars)):
            orig = chars[idx]
            if orig in confusions:
                for alt in confusions[orig]:
                    chars[idx] = alt
                    test_str = "".join(chars)
                    if _mrz_check_digit(test_str) == passport_check_digit:
                        if test_str[0].isalpha():
                            passport_num_raw = test_str
                            fixed = True
                            break
                if fixed:
                    break
                chars[idx] = orig

    line2 = passport_num_raw + passport_check_digit + line2[10:]
    if not _looks_like_td3_line2(line2) or not _td3_check_digits_valid(line2):
        return with_glance(
            {
                "success": False,
                "message": "MRZ is incomplete or failed check-digit validation. Retake a clear photo with the full MRZ strip in frame.",
                "raw_text_detected": text,
                "mrz_raw": [line1, line2],
            }
        )

    passport_number = passport_num_raw.replace("<", "")
    nationality = line2[10:13].replace("<", "")
    date_of_birth = line2[13:19]
    gender = line2[20]
    date_of_expiry = line2[21:27]
    personal_number = line2[28:42].replace("<", "")
    visual_fields = {}

    if _is_probably_nigerian(country, nationality) and country != "NGA":
        country = "NGA"
        line1 = line1[:2] + "NGA" + line1[5:]

    if _is_probably_nigerian(country, nationality) and (
        len(surname.replace(" ", "")) < 2
        or len(given_names.replace(" ", "")) < 3
        or any(char.isdigit() for char in surname + given_names)
    ):
        visual_fields = _extract_visual_fields(image)
        surname = visual_fields.get("surname") or surname
        given_names = visual_fields.get("given_names") or given_names
        if surname and given_names:
            line1 = _build_line1(document_type, country, surname, given_names)

    if country == "NGA":
        nin_candidate = personal_number.replace("<", "")
        if nin_candidate and not nin_candidate.isdigit():
            return with_glance(
                {
                    "success": False,
                    "message": "MRZ looks invalid or incomplete. Retake a clear photo with the full MRZ strip in frame.",
                    "raw_text_detected": text,
                    "mrz_raw": [line1, line2],
                }
            )

    if not visual_fields:
        visual_fields = _extract_visual_fields(image)

    visual_surname = visual_fields.get("surname") or ""
    visual_given_names = visual_fields.get("given_names") or ""
    if _should_prefer_visual_name(surname, visual_surname):
        surname = visual_surname
    if _should_prefer_visual_name(given_names, visual_given_names):
        given_names = visual_given_names
    if surname and given_names:
        line1 = _build_line1(document_type, country, surname, given_names)

    return with_glance(
        {
            "success": True,
            "verification": {
                "is_valid_format": True,
                "is_nigerian_passport": country == "NGA" and "P" in document_type,
                "document_type": document_type,
                "issuing_country": country,
            },
            "data": {
                "surname": surname,
                "given_names": given_names,
                "passport_number": passport_number,
                "nationality": nationality,
                "date_of_birth": format_date(date_of_birth),
                "gender": "Male" if gender == "M" else "Female" if gender == "F" else gender,
                "date_of_expiry": format_date(date_of_expiry),
                "date_of_issue": visual_fields.get("date_of_issue"),
                "nin": personal_number,
            },
            "mrz_raw": [line1, line2],
        }
    )


def format_date(date_string):
    if len(date_string) != 6:
        return date_string
    prefix = "19" if int(date_string[0:2]) > 40 else "20"
    return f"{prefix}{date_string[0:2]}-{date_string[2:4]}-{date_string[4:6]}"

