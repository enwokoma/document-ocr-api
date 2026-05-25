"""Bank statement extraction.

The current extractor uses text from PDFs when available and falls back to OCR
for image uploads. It then applies conservative regular expressions for common
statement fields. This is intentionally simple and should be expanded with
bank/country-specific parsers as the project grows.
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

    # Regexes are intentionally broad because banks label the same fields in
    # slightly different ways. Keep them conservative to avoid false positives.
    patterns = {
        "account_number": r"(?:Account\s*(?:No|Number))\s*[:\-]?\s*([0-9xX*]{10,})",
        "account_name": r"(?:Account\s*Name)\s*[:\-]?\s*([A-Z][A-Z\s.'\-]{3,})",
        "bank_name": r"(?:Bank\s*Name)\s*[:\-]?\s*([A-Za-z\s&.\-]+)",
        "opening_balance": r"(?:Opening\s*Balance)\s*[:\-]?\s*(?:NGN|₦)?\s*(-?[\d,]+\.\d{2})",
        "closing_balance": r"(?:Closing\s*Balance)\s*[:\-]?\s*(?:NGN|₦)?\s*(-?[\d,]+\.\d{2})",
        "period": r"(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})\s*(?:to|-|→)\s*(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})"
    }

    results = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            if key == "period":
                results["start_date"] = match.group(1)
                results["end_date"] = match.group(2)
            else:
                results[key] = clean_text(match.group(1))

    return {
        "success": True,
        "document_type": "BANK_STATEMENT",
        "data": results,
        "raw_text": text if not results else None
    }
