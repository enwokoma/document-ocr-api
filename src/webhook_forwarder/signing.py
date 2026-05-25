import hmac
import hashlib
from typing import Tuple


def sign_payload(secret: str, timestamp: int, raw_body: bytes) -> Tuple[str, str]:
    """
    Create signature headers for forwarding.

    Signature format:
      X-Signature = HMAC_SHA256(secret, "{timestamp}." + raw_body).hexdigest()
    """
    msg = f"{timestamp}.".encode("utf-8") + (raw_body or b"")
    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return str(timestamp), sig

