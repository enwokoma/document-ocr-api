"""Nigeria-specific document rules.

This module is the home for behavior that should not be shared globally. When
another country is added, create a sibling package such as
`src/document_ocr/ghana/` or `src/document_ocr/kenya/` instead of modifying the
generic passport, NIN, or bank-statement processors.
"""

from __future__ import annotations

import re
from typing import Optional

from src.document_ocr.country_profile import CountryProfile


NIGERIA_PROFILE = CountryProfile(
    code="NGA",
    name="Nigeria",
    mrz_code_aliases={"NGA", "NGE", "NG4", "N6A", "N64", "NGR"},
    supported_identity_documents={"NIN_CARD", "NIN_SLIP"},
    passport_personal_number_label="nin",
)


def validate_nin(value: Optional[str]) -> bool:
    """Validate the public Nigerian NIN shape.

    The local processor only knows the visible format: exactly 11 digits. It
    does not validate the number against any government or identity database.
    """
    return bool(re.fullmatch(r"\d{11}", value or ""))
