"""Hesap geneli günlük kâr/zarar defteri (UTC gün başından beri) — tek doğruluk kaynağı: Binance gelir geçmişi.

Manuel, Grid ve KAIS (Original V2) işlemlerinin hepsi aynı Binance hesabında olduğu için gelir geçmişi hepsini birlikte
içerir: gerçekleşen PnL (stop, acil kapanış, hedef, elle kapatma), komisyon ve fonlama. Transferler (para yatırma/çekme,
hesaplar arası) kâr/zarar değildir ve sayılmaz. KAIS'in günlük kayıp kapısı (original_v2_live.daily_guard) zaten bu
kuralla çalışıyordu; bu modül aynı hesabı manuel ve grid kapılarına da verir.

Defter durum sözlüğünde geçici alanda (`_daily_ledger`) tutulur; kalıcı kayda yazılmaz (sanitized_state yalnız izinli
alanları saklar). Çok üyeli modda her üyenin durumu ayrı olduğu için defter de üye başınadır.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

KEY = "_daily_ledger"
TRANSFER_TYPES = frozenset({"TRANSFER", "INTERNAL_TRANSFER"})
MAX_PAGES = 8
PAGE_LIMIT = 1000
FRESH_SECONDS = 30.0        # giriş kapıları bu yaşa kadar önbelleği kullanır, daha eskiyse yeniden okur
STALE_OK_SECONDS = 300.0    # okuma başarısızsa bu yaştaki son defter kabul edilir; daha eskiyse kapı kapanır
BUCKETS = ("realized_pnl", "commission", "funding", "other")
_BUCKET_OF = {"REALIZED_PNL": "realized_pnl", "COMMISSION": "commission", "FUNDING_FEE": "funding"}


class LedgerUnavailable(RuntimeError):
    """Günlük gelir geçmişi okunamadı ya da doğrulanamadı."""


def day_start_ms(now: datetime | None = None) -> int:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return int(current.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def _day_of(start_ms: int) -> str:
    return datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).date().isoformat()


async def fetch(client: Any, now: datetime | None = None) -> tuple[list[dict[str, Any]], int, int]:
    """Bugünün gelir satırları (sayfalı). Eksik/bozuk/çakışan veri → LedgerUnavailable (tahmin yapılmaz)."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start_ms, end_ms = day_start_ms(current), int(current.timestamp() * 1000)
    rows: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        try:
            batch = await client.signed("GET", "/fapi/v1/income", {
                "startTime": start_ms, "endTime": end_ms, "limit": PAGE_LIMIT, "page": page,
            })
        except Exception as exc:  # noqa: BLE001 - istemci hatası sınıfı çağırana göre değişir; hepsi "okunamadı"
            raise LedgerUnavailable("Binance gelir geçmişi okunamadı.") from exc
        if not isinstance(batch, list):
            raise LedgerUnavailable("Binance gelir geçmişi yanıtı geçersiz.")
        rows.extend(batch)
        if len(batch) < PAGE_LIMIT:
            return rows, start_ms, end_ms
    raise LedgerUnavailable("Günlük gelir geçmişi sayfa sınırını aştı; eksik hesap yapılmaz.")


def build(rows: list[Any], start_ms: int, end_ms: int, *, fetched_at: float | None = None) -> dict[str, Any]:
    """Satırları doğrular ve özetler. Kural original_v2_live.daily_guard ile aynı: yalnız USDT, tekrar eden ya da gün dışı
    satır yok; transferler hariç her gelir türü kâr/zarardır."""
    seen: set[tuple[str, str]] = set()
    totals = {name: Decimal(0) for name in BUCKETS}
    compact: list[tuple[str, str, int, float]] = []
    for row in rows:
        if (not isinstance(row, dict) or row.get("asset") != "USDT"
                or not {"incomeType", "tranId", "time", "income"}.issubset(row)):
            raise LedgerUnavailable("USDT günlük gelir satırı doğrulanamadı.")
        key = (str(row["incomeType"]), str(row["tranId"]))
        try:
            stamp = int(row["time"])
            amount = Decimal(str(row["income"]))
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise LedgerUnavailable("Günlük gelir satırı geçersiz.") from exc
        if key in seen or not start_ms <= stamp <= end_ms or not amount.is_finite():
            raise LedgerUnavailable("Günlük gelir satırlarında çakışma ya da gün dışı kayıt var.")
        seen.add(key)
        kind = str(row["incomeType"])
        if kind in TRANSFER_TYPES:
            continue
        totals[_BUCKET_OF.get(kind, "other")] += amount
        compact.append((str(row.get("symbol") or "").upper(), kind, stamp, float(amount)))
    summary = {name: round(float(value), 8) for name, value in totals.items()}
    summary["net"] = round(float(sum(totals.values())), 8)
    return {"day": _day_of(start_ms), "start_ms": start_ms, "end_ms": end_ms,
            "fetched_at": time.time() if fetched_at is None else fetched_at, "totals": summary, "rows": compact,
            "source": "BINANCE_INCOME"}


def store(state: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    if isinstance(state, dict):
        state[KEY] = ledger
    return ledger


def cached(state: dict[str, Any], *, max_age: float = STALE_OK_SECONDS, now: float | None = None) -> dict[str, Any] | None:
    """Aynı UTC güne ait ve en fazla max_age saniyelik defter; yoksa None. Gün dönünce eski defter kullanılmaz."""
    ledger = state.get(KEY) if isinstance(state, dict) else None
    if not isinstance(ledger, dict):
        return None
    stamp = time.time() if now is None else now
    if ledger.get("day") != _day_of(day_start_ms(datetime.fromtimestamp(stamp, tz=timezone.utc))):
        return None
    if stamp - float(ledger.get("fetched_at") or 0) > max_age:
        return None
    return ledger


async def refresh(client: Any, state: dict[str, Any], *, max_age: float = FRESH_SECONDS,
                  stale_ok: float = STALE_OK_SECONDS) -> dict[str, Any]:
    """Yeterince taze defter varsa onu, yoksa yeniden okur. Okuma başarısızsa stale_ok yaşına kadar son defteri döndürür
    (stale=True), daha eskiyse LedgerUnavailable — çağıran kapı yeni girişi kapatır."""
    fresh = cached(state, max_age=max_age)
    if fresh is not None:
        return fresh
    try:
        rows, start_ms, end_ms = await fetch(client)
        return store(state, build(rows, start_ms, end_ms))
    except LedgerUnavailable:
        fallback = cached(state, max_age=stale_ok)
        if fallback is not None:
            return {**fallback, "stale": True}
        raise


def window_totals(ledger: dict[str, Any], symbol: str, start_ms: int, end_ms: int | None = None) -> dict[str, float]:
    """Tek coin ve zaman aralığı (ör. bir grid'in yaşam süresi) için özet. Grid çalışırken o coine başka emir girmediği için
    aralıktaki satırlar o grid'e aittir: stop, acil kapanış, hedef, komisyon ve fonlama dahil."""
    symbol = str(symbol or "").upper()
    totals = {name: 0.0 for name in BUCKETS}
    for row_symbol, kind, stamp, amount in ledger.get("rows") or ():
        if row_symbol != symbol or stamp < start_ms or (end_ms is not None and stamp > end_ms):
            continue
        totals[_BUCKET_OF.get(kind, "other")] += amount
    out = {name: round(value, 8) for name, value in totals.items()}
    out["net"] = round(sum(totals.values()), 8)
    return out


def public(ledger: dict[str, Any] | None) -> dict[str, Any] | None:
    """Durum yanıtı için küçük özet (satırlar hariç)."""
    if not isinstance(ledger, dict):
        return None
    return {"day": ledger.get("day"), "fetched_at": ledger.get("fetched_at"), "source": ledger.get("source"),
            "stale": bool(ledger.get("stale")), **(ledger.get("totals") or {})}
