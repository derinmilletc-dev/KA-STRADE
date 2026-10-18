"""Keep server-side error details away from customers.

Members only see a short, safe message plus a reference code.  The real detail
goes to the admin error centre (and the log) under the same reference code.
"""
from __future__ import annotations

import json
from typing import Any

SAFE_MESSAGE = "Şu anda işlem tamamlanamadı. Lütfen birkaç dakika sonra tekrar deneyin. Sorun sürerse destek ekibine bu hata kodunu iletin:"
# The member's own Binance key problems are actionable for the member, so those
# routes keep their short message; every other server-side field is dropped.
KEEP_MESSAGE_PREFIXES = ("/api/exchange-connections/",)
SAFE_KEYS = ("detail", "message", "code", "retry_after_seconds")


def original_detail(body: bytes) -> str:
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return body[:500].decode("utf-8", "replace")
    detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
    return (detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False, default=str))[:800]


def safe_body(status: int, body: bytes, path: str, request_id: str) -> dict[str, Any] | None:
    """Replacement body for a member, or None when the response is left alone."""
    if status < 500:
        return None
    if path.startswith(KEEP_MESSAGE_PREFIXES):
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            payload = None
        detail = payload.get("detail") if isinstance(payload, dict) else None
        source = detail if isinstance(detail, dict) else payload if isinstance(payload, dict) else None
        if isinstance(source, dict):
            kept = {key: source[key] for key in SAFE_KEYS if isinstance(source.get(key), (str, int)) and not isinstance(source.get(key), bool)}
            if kept.get("message") or kept.get("detail"):
                kept["request_id"] = request_id
                return kept
        if isinstance(detail, str) and detail.strip():
            return {"detail": detail[:400], "request_id": request_id}
    return {"detail": f"{SAFE_MESSAGE} {request_id}", "code": "SERVER_ERROR", "request_id": request_id}
