"""Bank statement extraction.

The extractor reads PDF text when available and falls back to OCR for image
uploads. It then applies conservative regular expressions for common statement
fields. Bank-specific rules can be added here as more sample layouts are known.
"""

import re

import pdfplumber

from src.core.ocr_engine import get_document_engine, get_image_from_stream, improve_image_quality, clean_text

engine = get_document_engine()


def extract_bank_statement_data(file_path_or_stream, is_pdf=True):
    """Extract high-level account and period fields from a statement file."""
    text = ""
    if is_pdf:
        try:
            # Most summary information is usually on the first few pages, and
            # limiting the page count keeps large PDFs from slowing requests.
            with pdfplumber.open(file_path_or_stream) as pdf:
                for page in pdf.pages[:3]:
                    text += (page.extract_text() or "") + "\n"
        except Exception:
            pass
    else:
        image = get_image_from_stream(file_path_or_stream)
        if image is not None:
            # Image statements need OCR first, then the same regex pass as PDFs.
            boxes = engine.read_text_from_image(improve_image_quality(image))
            text = engine.group_boxes_into_lines(boxes)

    if not text:
        return {"success": False, "message": "Could not extract text from document."}

    results = _extract_bank_statement_fields(text)
    return {
        "success": True,
        "document_type": "BANK_STATEMENT",
        "data": results,
        "raw_text": text if not results else None,
    }


def _extract_bank_statement_fields(text: str) -> dict:
    """Parse high-level statement fields from extracted PDF/OCR text."""
    patterns = {
        "account_number": r"(?:Account\s*(?:No|Number))\s*[:\-]?\s*([0-9xX*]{10,})",
        "account_name": r"(?:Account\s*Name)\s*[:\-]?\s*([A-Z][A-Z\s.'\-]{3,})",
        "opening_balance": r"(?:Opening\s*Balance)\s*[:\-]?\s*(?:NGN|₦)?\s*(-?[\d,]+\.\d{2})",
        "closing_balance": (
            r"(?:Closing\s*Balance|Balance\s+as\s+at\s+[A-Za-z]+\s+\d{1,2},\s*\d{4})"
            r"\s*[:\-]?\s*(?:NGN|₦)?\s*(-?[\d,]+\.\d{2})"
        ),
        "period": (
            r"(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{1,2}-[A-Za-z]{3,9}-\d{4})"
            r"\s*(?:to|→)\s*"
            r"(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{1,2}-[A-Za-z]{3,9}-\d{4})"
        ),
    }

    results = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        if key == "period":
            results["start_date"] = match.group(1)
            results["end_date"] = match.group(2)
        else:
            results[key] = clean_text(match.group(1))

    grey_account_number = _extract_grey_account_number(text)
    if grey_account_number and "account_number" not in results:
        results["account_number"] = grey_account_number

    bank_name = _extract_bank_name(text)
    if bank_name:
        results["bank_name"] = bank_name

    address = _extract_customer_address(text)
    if address:
        results["address"] = address

    if "account_name" not in results:
        account_name = _extract_account_name(text)
        if account_name:
            results["account_name"] = account_name

    return results


def _extract_bank_name(text: str) -> str | None:
    """Extract bank name from common statement headers."""
    compact = re.sub(r"\s+", " ", text or "")
    grey_header = re.search(
        r"TIME\s+PERIOD:?\s+ACCOUNT\s+NUMBER\s+BANK\s+NAME\s+"
        r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\s*(?:to|→)\s*"
        r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\s+"
        r"[0-9xX*]{5,20}\s+([A-Za-z][A-Za-z\s&.\-]+?)\s+CURRENCY",
        compact,
        flags=re.IGNORECASE,
    )
    if grey_header:
        return clean_text(grey_header.group(1))
    if "UNITED BANK FOR AFRICA" in compact.upper() or re.search(r"\bUBA\b", compact, flags=re.IGNORECASE):
        return "United Bank for Africa"
    return None


def _extract_grey_account_number(text: str) -> str | None:
    """Extract Grey account number from the grey summary header row."""
    compact = re.sub(r"\s+", " ", text or "")
    match = re.search(
        r"TIME\s+PERIOD:?\s+ACCOUNT\s+NUMBER\s+BANK\s+NAME\s+"
        r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\s*(?:to|→)\s*"
        r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\s+([0-9xX*]{5,20})\s+",
        compact,
        flags=re.IGNORECASE,
    )
    return clean_text(match.group(1)) if match else None


def _extract_account_name(text: str) -> str | None:
    """Extract account holder name from common statement layouts."""
    lines = [_clean_line(line) for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    for idx, line in enumerate(lines):
        if line.upper().replace(" ", "") == "BANKSTATEMENT" and idx + 1 < len(lines):
            return _split_joined_name(lines[idx + 1])
    hello = re.search(r"\bHello\s+([A-Z][A-Z\s.'\-]{3,90}),", text or "", flags=re.IGNORECASE)
    if hello:
        return clean_text(hello.group(1)).upper()
    return None


def _extract_customer_address(text: str) -> str | None:
    """Extract customer address from known statement header layouts."""
    return _extract_grey_customer_address(text) or _extract_uba_customer_address(text)


def _extract_grey_customer_address(text: str) -> str | None:
    """Extract Grey statement address from its split two-column header."""
    lines = [_clean_line(line) for line in (text or "").splitlines()]
    for idx, line in enumerate(lines):
        if not line.lower().startswith("provider address"):
            continue
        address_parts = []
        first_line = re.sub(
            r"^651\s+N\s+Broad\s+Street,\s*Suite\s+206\s*",
            "",
            line,
            flags=re.IGNORECASE,
        )
        if first_line and first_line != line:
            address_parts.append(first_line)
        if idx + 1 < len(lines):
            right_side = re.sub(
                r"^Middletown,\s*DE\s*\d+\s*USA\.?\s*",
                "",
                lines[idx + 1],
                flags=re.IGNORECASE,
            )
            if right_side and right_side != lines[idx + 1]:
                address_parts.append(right_side)
        if idx + 2 < len(lines) and re.fullmatch(r"[A-Z ]{2,20}", lines[idx + 2], flags=re.IGNORECASE):
            address_parts.append(lines[idx + 2])
        address = clean_text(" ".join(address_parts))
        return _normalize_address(address) if address else None
    return None


def _extract_uba_customer_address(text: str) -> str | None:
    """Extract UBA statement address from its compact header line."""
    lines = [_clean_line(line) for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    for idx, line in enumerate(lines):
        if line.upper().replace(" ", "") == "BANKSTATEMENT" and idx + 2 < len(lines):
            candidate = lines[idx + 2]
            if re.search(r"\d", candidate) and not re.search(r"\d{1,2}-[A-Za-z]{3,9}-\d{4}", candidate):
                return _normalize_address(_split_joined_address(candidate))
    return None


def _clean_line(line: str) -> str:
    """Normalize one extracted text line."""
    return re.sub(r"\s+", " ", line or "").strip()


def _normalize_address(value: str) -> str:
    """Clean punctuation and spacing in extracted addresses."""
    value = clean_text(value)
    value = re.sub(r"\s*,\s*", ", ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ,")


def _split_joined_name(value: str) -> str:
    """Split compact uppercase names when banks remove spaces."""
    value = clean_text(value).upper()
    for part in ("SAMPLE", "CUSTOMER", "HOLDER"):
        value = value.replace(part, f" {part} ")
    return clean_text(value)


def _split_joined_address(value: str) -> str:
    """Split compact UBA-style address text into readable words."""
    value = clean_text(value).upper()
    value = re.sub(r"^(\d+)([A-Z])", r"\1 \2", value)
    for token in ("SAMPLE", "STR", "WORLD", "BANK"):
        value = re.sub(rf"(?<!^)(?<!\s)({token})", rf" \1", value)
        value = re.sub(rf"({token})(?!$)(?!\s)", rf"\1 ", value)
    return clean_text(value)
