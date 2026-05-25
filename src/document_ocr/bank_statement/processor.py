import re
import pdfplumber
from src.core.ocr_engine import get_document_engine, get_image_from_stream, improve_image_quality, clean_text

engine = get_document_engine()

def extract_bank_statement_data(file_path_or_stream, is_pdf=True):
    text = ""
    if is_pdf:
        try:
            with pdfplumber.open(file_path_or_stream) as pdf:
                for page in pdf.pages[:3]:
                    text += (page.extract_text() or "") + "\n"
        except Exception:
            pass
    else:
        image = get_image_from_stream(file_path_or_stream)
        if image is not None:
            boxes = engine.read_text_from_image(improve_image_quality(image))
            text = engine.group_boxes_into_lines(boxes)

    if not text:
        return {"success": False, "message": "Could not extract text from document."}

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
