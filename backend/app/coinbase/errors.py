"""Coinbase hataları: her hata kısa bir kod ve sade Türkçe mesaj taşır.

HTTP durumları bilerek 4xx seçilir: sunucu 5xx cevaplarını genel bir metne çevirir
(error_exposure), 401 ise arayüzde oturum bitti gibi görünür. Coinbase kaynaklı sorunlar
bu yüzden 424 / 429 / 422 / 409 ile döner ve mesaj üyeye aynen ulaşır.
"""

from __future__ import annotations

from typing import Any


class CoinbaseError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 424,
        retry_after_seconds: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds
        self.extra = extra or {}

    def body(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"detail": self.message, "code": self.code}
        if self.retry_after_seconds is not None:
            payload["retry_after_seconds"] = self.retry_after_seconds
        for key, value in self.extra.items():
            payload.setdefault(key, value)
        return payload


def clean_remote_message(value: Any, *, limit: int = 160) -> str:
    """Coinbase'in kendi mesajını kısa ve güvenli göster (denetim karakterleri atılır)."""
    if not isinstance(value, str):
        return ""
    text = "".join(ch if ch.isprintable() else " " for ch in value).strip()
    return text[:limit]
