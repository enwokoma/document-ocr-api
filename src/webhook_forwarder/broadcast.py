"""Send one received webhook payload to multiple configured targets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import requests


@dataclass(frozen=True)
class BroadcastResult:
    """Result for one attempted forwarded webhook request."""

    name: str
    url: str
    ok: bool
    status_code: Optional[int]
    error: Optional[str]
    response_preview: Optional[str]


def broadcast_raw_webhook(
    *,
    raw_body: bytes,
    headers: Dict[str, str],
    target_1_url: Optional[str],
    target_2_url: Optional[str],
    target_3_url: Optional[str] = None,
    timeout_seconds: float = 5.0,
) -> List[BroadcastResult]:
    """
    Forward a webhook payload to configured target URLs.

    Args:
        raw_body: The raw webhook body to forward
        headers: Headers to include in the forwarded request
        target_1_url: First target endpoint URL
        target_2_url: Second target endpoint URL
        target_3_url: Optional third target endpoint URL
        timeout_seconds: Request timeout in seconds

    Returns:
        List of BroadcastResult for each target
    """
    targets = [
        ("target_1", target_1_url),
        ("target_2", target_2_url),
        ("target_3", target_3_url),
    ]

    results: List[BroadcastResult] = []
    for name, url in targets:
        if not url:
            # Missing targets are reported instead of raising so the response can
            # show exactly which destinations were configured.
            results.append(
                BroadcastResult(
                    name=name,
                    url="",
                    ok=False,
                    status_code=None,
                    error="Missing target URL",
                    response_preview=None,
                )
            )
            continue

        try:
            # Forward the original raw body. Re-serializing JSON could change
            # whitespace or ordering and break downstream signature checks.
            resp = requests.post(
                url,
                data=raw_body,
                headers=headers,
                timeout=timeout_seconds,
            )
            preview = (resp.text or "")[:1000] if resp is not None else ""
            results.append(
                BroadcastResult(
                    name=name,
                    url=url,
                    ok=resp.ok,
                    status_code=resp.status_code,
                    error=None if resp.ok else (resp.text[:500] if resp.text else "Non-2xx"),
                    response_preview=preview if preview else None,
                )
            )
        except Exception as e:
            results.append(
                BroadcastResult(
                    name=name,
                    url=url,
                    ok=False,
                    status_code=None,
                    error=str(e),
                    response_preview=None,
                )
            )

    return results

