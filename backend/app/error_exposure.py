"""Keep server-side error details away from customers.

Members only see a short, safe message plus a reference code.  The real detail
goes to the admin error centre (and the log) under the same reference code.
"""
from __future__ import annotations

import json
import re
from typing import Any

SAFE_MESSAGE = "Şu anda işlem tamamlanamadı. Lütfen birkaç dakika sonra tekrar deneyin. Sorun sürerse destek ekibine bu hata kodunu iletin:"
# The member's own Binance key problems are actionable for the member, so those
# routes keep their short message; every other server-side field is dropped.
KEEP_MESSAGE_PREFIXES = ("/api/exchange-connections/",)
SAFE_KEYS = ("detail", "message", "code", "retry_after_seconds")
# Kais sohbet yanıtları zaten üyeye gösterilmek üzere üretilir (sabit metin + üyenin kendi araç özetleri). 5xx'te de
# yalnızca bu alanlar, tür ve biçim kontrolüyle korunur; başka her alan (iç hata ayrıntısı dahil) düşürülür.
ASSISTANT_REPLY_PATHS = frozenset({"/api/assistant/chat"})
SAFE_TOKEN = re.compile(r"[a-z][a-z0-9_]{0,39}")


def assistant_reply(body: bytes, request_id: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("reply"), str) or not payload["reply"].strip():
        return None
    sources = payload.get("sources")
    kept: dict[str, Any] = {
        "reply": payload["reply"][:4000],
        "language": payload.get("language") if payload.get("language") in ("tr", "en") else "tr",
        "sources": [item for item in sources if isinstance(item, str) and SAFE_TOKEN.fullmatch(item)] if isinstance(sources, list) else [],
        "request_id": request_id,
    }
    if isinstance(payload.get("error_code"), str) and SAFE_TOKEN.fullmatch(payload["error_code"]):
        kept["error_code"] = payload["error_code"]
    return kept


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
    if path in ASSISTANT_REPLY_PATHS:
        kept = assistant_reply(body, request_id)
        if kept is not None:
            return kept
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
