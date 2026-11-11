"""Akıllı başlama: DCA yalnız Kais analizi istenen yönle uyumlu ve yeterince güçlüyse başlar (opsiyonel; sunucu bayrağı KAISTRADE_DCA_SMART_ENTRY).

Bu kapı yalnız GİRİŞİ geciktirir/engeller; emir, stop, hedef ve risk kurallarına dokunmaz. Güvenli yönde hata verir: analiz okunamıyorsa, coin analiz listesinde
yoksa ya da güven eşiğin altındaysa sonuç "uygun değil"dir. Kais analizi (`/api/analysis-universe`, 15 dakikalık, en hacimli 100 coin) 60 sn önbelleklidir."""
from __future__ import annotations

import os
from typing import Any, Awaitable, Callable

ENABLE_ENV = "KAISTRADE_DCA_SMART_ENTRY"
MIN_CONFIDENCE_ENV = "KAISTRADE_DCA_ENTRY_MIN_CONFIDENCE"
MAX_WAIT_ENV = "KAISTRADE_DCA_ENTRY_MAX_WAIT_HOURS"
DEFAULT_MIN_CONFIDENCE = 80.0          # arayüzdeki "güçlü sinyal" eşiğiyle aynı
DEFAULT_MAX_WAIT_HOURS = 6.0           # tur bitince uygun an en çok bu kadar beklenir; gelmezse bot durur


class EntryNotReady(Exception):
    """Kais analizi şu an giriş için uygun değil (açıklama kullanıcıya gösterilir)."""


def enabled() -> bool:
    return str(os.getenv(ENABLE_ENV, "1")).strip().lower() in {"1", "true", "yes", "on"}


def min_confidence() -> float:
    try:
        value = float(os.getenv(MIN_CONFIDENCE_ENV, DEFAULT_MIN_CONFIDENCE))
    except (TypeError, ValueError):
        return DEFAULT_MIN_CONFIDENCE
    return min(max(value, 50.0), 100.0)


def max_wait_seconds() -> float:
    try:
        value = float(os.getenv(MAX_WAIT_ENV, DEFAULT_MAX_WAIT_HOURS))
    except (TypeError, ValueError):
        value = DEFAULT_MAX_WAIT_HOURS
    return min(max(value, 0.25), 48.0) * 3600.0


def _norm(symbol: Any) -> str:
    return str(symbol or "").upper().replace("/", "").replace("-", "").replace("_", "")


def evaluate(row: dict[str, Any] | None, symbol: str, direction: str, threshold: float | None = None) -> tuple[bool, str]:
    """Saf karar: (uygun mu, açıklama). `row` analiz listesindeki satırdır (direction, confidence)."""
    threshold = min_confidence() if threshold is None else threshold
    wanted = str(direction).upper()
    if row is None:
        return False, f"{symbol} Kais analiz listesinde yok (en hacimli 100 coin taranır)"
    lean = str(row.get("direction") or "").upper()
    try:
        confidence = float(row.get("confidence"))
    except (TypeError, ValueError):
        return False, f"{symbol} için Kais güveni okunamadı"
    if confidence != confidence:
        return False, f"{symbol} için Kais güveni okunamadı"
    if lean not in ("LONG", "SHORT"):
        return False, f"Kais analizi {symbol} için şu an net bir yön vermiyor"
    if lean != wanted:
        return False, f"Kais analizi {symbol} için şu an {lean} diyor; {wanted} için uygun değil"
    if confidence < threshold:
        return False, f"Kais güveni %{confidence:.0f}; en az %{threshold:.0f} gerekir"
    return True, f"Kais analizi uygun: {wanted} %{confidence:.0f}"


async def _fetch_rows() -> list[dict[str, Any]]:
    from ..main import analysis_universe           # gecikmeli içe aktarma: yalnız çalışma anında (döngüsel bağımlılık yok)
    data = await analysis_universe("15m", 100)
    rows = data.get("results") if isinstance(data, dict) else None
    return [r for r in rows or [] if isinstance(r, dict)]


Fetcher = Callable[[], Awaitable[list[dict[str, Any]]]]


async def check(symbol: str, direction: str, *, fetch: Fetcher | None = None, threshold: float | None = None) -> tuple[bool, str]:
    """Kais analizine bak: (uygun mu, açıklama). Hata/okunamama = uygun DEĞİL (fail-closed)."""
    try:
        rows = await (fetch or _fetch_rows)()
    except Exception:  # noqa: BLE001 - belirsizlikte yeni risk açılmaz
        return False, "Kais analizi şu an okunamadı"
    key = _norm(symbol)
    row = next((r for r in rows if _norm(r.get("symbol")) == key), None)
    return evaluate(row, symbol, direction, threshold)
