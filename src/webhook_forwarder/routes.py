"""Webhook-forwarder HTTP route.

This module receives a webhook once, logs a safe preview, signs the exact raw
payload, and sends it to configured downstream URLs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Dict

from flask import Blueprint, jsonify, request

from src.webhook_forwarder.broadcast import broadcast_raw_webhook
from src.webhook_forwarder.signing import sign_payload


forwarder_bp = Blueprint("webhook_forwarder", __name__)
logger = logging.getLogger(__name__)
_gunicorn_logger = logging.getLogger("gunicorn.error")
if _gunicorn_logger.handlers:
    logger.handlers = _gunicorn_logger.handlers
    logger.setLevel(_gunicorn_logger.level)
    logger.propagate = False

FORWARDER_SECRET = os.environ.get("FORWARDER_SECRET", "")
FORWARDER_TARGET_1_URL = os.environ.get("FORWARDER_TARGET_1_URL", "")
FORWARDER_TARGET_2_URL = os.environ.get("FORWARDER_TARGET_2_URL", "")
FORWARDER_TARGET_3_URL = os.environ.get("FORWARDER_TARGET_3_URL", "")

_SEEN: Dict[str, float] = {}
_DEDUPE_TTL_SECONDS = 60


def _prune_seen(now: float) -> None:
    """Remove old dedupe entries so the in-memory cache cannot grow forever."""
    expired = [k for k, ts in _SEEN.items() if (now - ts) > _DEDUPE_TTL_SECONDS]
    for k in expired:
        _SEEN.pop(k, None)


def _dedupe_key(raw_body: bytes) -> str:
    """Hash a request body so duplicate payloads can be detected cheaply."""
    return hashlib.sha256(raw_body or b"").hexdigest()


_SENSITIVE_HEADER_KEYS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-signature",
}


def _safe_headers_for_log() -> Dict[str, str]:
    """
    Best-effort: capture exactly what was sent, while masking known sensitive headers.
    """
    out: Dict[str, str] = {}
    for k, v in request.headers.items():
        if k.lower() in _SENSITIVE_HEADER_KEYS:
            out[k] = "***redacted***"
        else:
            out[k] = v
    return out


def _safe_body_preview(raw_body: bytes, limit: int = 8192) -> str:
    """Return a bounded text preview for logs without assuming valid UTF-8."""
    if not raw_body:
        return ""
    b = raw_body[: max(0, limit)]
    return b.decode("utf-8", errors="replace")


def _forward_headers(timestamp: int, signature: str) -> Dict[str, str]:
    """Build the headers sent to each downstream webhook target."""
    headers: Dict[str, str] = {
        "X-Timestamp": str(timestamp),
        "X-Signature": signature,
        "X-Source": "webhook-forwarder",
    }

    content_type = request.headers.get("Content-Type")
    if content_type:
        headers["Content-Type"] = content_type

    for h in ("X-Request-Id", "X-Correlation-Id"):
        v = request.headers.get(h)
        if v:
            headers[h] = v

    return headers


@forwarder_bp.route("/webhooks/forward", methods=["POST"])
def webhook_forward():
    """
    Generic Webhook Forwarder
    ---
    tags: [Webhooks]
    consumes: [application/json]
    parameters:
      - name: X-Signature
        in: header
        type: string
        description: Optional signature header for webhook verification
      - name: X-Timestamp
        in: header
        type: string
        description: Optional timestamp header for webhook verification
      - name: X-Request-Id
        in: header
        type: string
        description: Optional request ID for tracing
    responses:
      200:
        description: Webhook processed and forwarded to targets
      500:
        description: Internal server error
    """
    raw_body: bytes = request.get_data(cache=False) or b""
    now = time.time()
    _prune_seen(now)

    request_id = (
        request.headers.get("X-Request-Id")
        or request.headers.get("X-Correlation-Id")
        or request.headers.get("X-Amzn-Trace-Id")
    )
    body_hash = _dedupe_key(raw_body)

    logger.info(
        "webhook_received %s",
        json.dumps(
            {
                "path": request.path,
                "method": request.method,
                "remote_addr": request.remote_addr,
                "content_type": request.headers.get("Content-Type"),
                "content_length": request.content_length,
                "request_id": request_id,
                "body_sha256": body_hash,
                "headers": _safe_headers_for_log(),
                "body_preview": _safe_body_preview(raw_body),
            },
            ensure_ascii=False,
        ),
    )

    try:
        if not FORWARDER_SECRET:
            logger.error("webhook_missing_secret %s", json.dumps({"request_id": request_id}, ensure_ascii=False))
            return jsonify({"success": False, "message": "FORWARDER_SECRET is not configured"}), 500

        if body_hash in _SEEN:
            logger.info(
                "webhook_deduped %s",
                json.dumps({"request_id": request_id, "body_sha256": body_hash}, ensure_ascii=False),
            )
            return jsonify({"success": True, "deduped": True}), 200
        _SEEN[body_hash] = now

        ts = int(now)
        x_timestamp, x_signature = sign_payload(FORWARDER_SECRET, ts, raw_body)
        headers = _forward_headers(int(x_timestamp), x_signature)

        results = broadcast_raw_webhook(
            raw_body=raw_body,
            headers=headers,
            target_1_url=FORWARDER_TARGET_1_URL,
            target_2_url=FORWARDER_TARGET_2_URL,
            target_3_url=FORWARDER_TARGET_3_URL,
        )

        ok_all = all(r.ok for r in results if r.url)
        payload = {
            "success": ok_all,
            "deduped": False,
            "forwarded": [
                {
                    "name": r.name,
                    "url": r.url,
                    "ok": r.ok,
                    "status_code": r.status_code,
                    "error": r.error,
                    "response_preview": r.response_preview,
                }
                for r in results
            ],
        }

        logger.info(
            "webhook_forwarded %s",
            json.dumps(
                {
                    "request_id": request_id,
                    "body_sha256": body_hash,
                    "ok_all": ok_all,
                    "forwarded": [
                        {
                            "name": r.name,
                            "url": r.url,
                            "ok": r.ok,
                            "status_code": r.status_code,
                            "response_preview": r.response_preview,
                        }
                        for r in results
                    ],
                },
                ensure_ascii=False,
            ),
        )

        # We received the webhook; forwarding failures are reported in body.
        return jsonify(payload), 200
    except Exception:
        logger.exception(
            "webhook_unhandled_exception %s",
            json.dumps({"request_id": request_id, "body_sha256": body_hash}, ensure_ascii=False),
        )
        return jsonify({"success": False, "message": "Unhandled exception in webhook forwarder"}), 500

