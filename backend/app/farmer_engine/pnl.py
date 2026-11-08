"""KAIS FARMER PnL + funding doğrulaması (C8). SAF çekirdek: ağ/FastAPI yok; borsa okumaları `ExecutionSource` portundan gelir.

Sözleşme (yeni bir attribution sistemi YOK):
  * gross + commission: mevcut `v25_execution.verified_plan_pnl` (resmî userTrades; kimlik = clientOrderId/clientAlgoId→orderId).
    Farmer ledger'ındaki `kf-` kimlikleri ondan beklenen plan alanlarına çevrilir (`farmer_plan`). Yalnız sembol eşleşmesi KABUL EDİLMEZ
    (`symbol_fallback` kapalı; attribution != "ORDER_IDS" ise UNVERIFIED).
  * funding: `/fapi/v1/income` FUNDING_FEE. İşaret Binance semantiğidir: income > 0 hesaba GELEN, < 0 ÖDENEN (repo: original_profit_guard
    `paid = -total`). `farmer_result.funding` bu İŞARETLİ hesap etkisidir.
        verified_net_pnl = gross − commission + funding
  * İkinci kaynak çapraz kontrolü (income REALIZED_PNL / COMMISSION): ikisi uyuşmazsa UNVERIFIED (kopya/eksik satırı yakalar).
  * Aynı (incomeType, tranId) iki kez toplanmaz (daily_ledger kuralı); aynı anahtar farklı tutarla gelirse çelişki → UNVERIFIED.
  * Hesaplama her denemede BAŞTAN yapılır (birikimli değil) → tekrar denemeler sonucu değiştirmez.
Eksik / belirsiz / çelişkili veri → UNVERIFIED (0 ya da tahmin YAZILMAZ). Hedef/maks. zarar/tahmini maliyet hiçbir zaman PnL yerine kullanılmaz."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Protocol, Sequence

from ..grid_engine.ports import LONG
from .orchestrator import PnlOutcome  # yeniden deneme standardı: orchestrator.PNL_RETRY_BACKOFF_SECONDS
from .store import FarmerStore

VERIFIED, UNVERIFIED = "VERIFIED", "UNVERIFIED"
FUNDING, REALIZED, COMMISSION = "FUNDING_FEE", "REALIZED_PNL", "COMMISSION"
INCOME_PAGE_LIMIT = 1000
TOLERANCE = Decimal("0.00000001")          # Binance değerleri 8 ondalık; kaynaklar arası fark bundan büyükse çelişki
END_SKEW_MS = 5000                         # yalnız BİTİŞ tarafı: yerel saat ↔ borsa saati (kapanıştan sonra pozisyon düz → funding doğamaz)


class PnlUnavailable(Exception):
    """Borsa verisi okunamadı / kanıtlanamadı. `args[0]` güvenli, kısa bir KOD'dur (sır/istek içeriği yok)."""


class ExecutionSource(Protocol):
    def plan_pnl(self, plan: dict[str, Any]) -> dict[str, Any]: ...                                   # verified_plan_pnl çıktısı
    def income_rows(self, symbol: str, income_type: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]: ...
    def foreign_overlaps(self, symbol: str, start_ms: int, end_ms: int) -> list[str]: ...             # başka strateji penceresi (varsa neden)


# ------------------------------------------------------------------ kimlik: ledger → plan alanları
def farmer_plan(store: FarmerStore, result: Mapping[str, Any]) -> dict[str, Any]:
    """Farmer ledger'ındaki emir kimliklerini `verified_plan_pnl`'in okuduğu alanlara çevirir.
    entry_client_order_id=E; close_client_order_ids=C (piyasa kapanışları); stop_client_id=en yeni S; diğer S/T koşullu emir kimlikleri
    `smart_guard.client_ids` (verified_plan_pnl'in 'bize ait ek koruma emri' alanı) — algo emir geçmişinden actualOrderId'ye bu kimliklerle ulaşılır."""
    rows = store.orders(result["grid_id"])
    entries = [r["client_id"] for r in rows if r["role"] == "E"]
    if len(entries) != 1:
        raise PnlUnavailable("ENTRY_ID_AMBIGUOUS")
    stops = [r["client_id"] for r in sorted((r for r in rows if r["role"] == "S"), key=lambda r: r["rev"])]
    targets = [r["client_id"] for r in rows if r["role"] == "T"]
    closes = [r["client_id"] for r in rows if r["role"] == "C"]
    created = datetime.fromtimestamp(float(result["opened_at"]), timezone.utc).isoformat()
    return {"symbol": result["symbol"], "direction": result["side"], "created_at": created, "intent_id": "",
            "entry_client_order_id": entries[0], "close_client_order_ids": closes, "stop_client_id": stops[-1] if stops else None,
            "smart_guard": {"client_ids": [*stops[:-1], *targets]}, "exchange_order_ids": [], "protection_ids": []}


# ------------------------------------------------------------------ yardımcılar
def _dec(value: Any) -> Decimal | None:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _ms(iso: Any) -> int | None:
    try:
        parsed = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return int((parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def sum_income(rows: Sequence[Mapping[str, Any]], income_type: str, symbol: str, start_ms: int, end_ms: int) -> Decimal:
    """Bir gelir türünün TEKİLLEŞTİRİLMİŞ toplamı (işaretli). Bozuk satır, USDT dışı varlık ya da çelişen tekrar → PnlUnavailable."""
    seen: dict[tuple[str, str], tuple[Decimal, int]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not {"incomeType", "tranId", "time", "income"}.issubset(row):
            raise PnlUnavailable("INCOME_ROW_INVALID")
        if str(row["incomeType"]) != income_type or str(row.get("symbol") or "").upper() != symbol:
            continue                                                # başka tür / başka sembol: bu pozisyona yazılmaz
        if str(row.get("asset") or "") != "USDT":
            raise PnlUnavailable("INCOME_ASSET_NOT_USDT")
        amount, stamp = _dec(row["income"]), row["time"]
        if amount is None or not str(stamp).isdigit():
            raise PnlUnavailable("INCOME_ROW_INVALID")
        key, value = (str(row["incomeType"]), str(row["tranId"])), (amount, int(stamp))
        if key in seen:
            if seen[key] != value:
                raise PnlUnavailable("INCOME_DUPLICATE_CONFLICT")
            continue                                                # aynı kayıt tekrar geldi: bir kez sayılır
        seen[key] = value
    return sum((amt for amt, ts in seen.values() if start_ms <= ts <= end_ms), Decimal(0))


class FarmerPnlVerifier:
    """`orchestrator.PnlVerifier` uygulaması. Slot bırakmadan BAĞIMSIZDIR (orkestratör flat doğrulanınca slotu zaten bırakmıştır)."""

    def __init__(self, store: FarmerStore, source: ExecutionSource, *, clock=time.time) -> None:
        self.store, self.source, self.clock = store, source, clock

    def verify(self, result: Mapping[str, Any]) -> PnlOutcome:
        try:
            return self._verify(result)
        except PnlUnavailable as exc:
            return PnlOutcome(UNVERIFIED, error=_safe(exc.args[0] if exc.args else "UNAVAILABLE"))
        except Exception as exc:                                    # noqa: BLE001 — hata metni (sır/istek) KAYDEDİLMEZ, yalnız sınıf adı
            return PnlOutcome(UNVERIFIED, error=_safe(f"VERIFY_ERROR:{exc.__class__.__name__}"))

    def _verify(self, result: Mapping[str, Any]) -> PnlOutcome:
        if not float(result.get("closed_at") or 0) > 0 or result.get("close_reason") in (None, "OPEN"):
            raise PnlUnavailable("NOT_CLOSED")
        plan = farmer_plan(self.store, result)
        out = self.source.plan_pnl(plan)
        gross, commission = _dec(out.get("gross_realized_pnl")), _dec(out.get("commission_usdt"))
        if gross is None:
            raise PnlUnavailable("GROSS_MISSING")
        if commission is None:
            raise PnlUnavailable("COMMISSION_MISSING")
        if out.get("attribution") != "ORDER_IDS":
            raise PnlUnavailable("ATTRIBUTION_NOT_BY_ORDER_ID")
        if out.get("non_usdt_commission_assets"):
            raise PnlUnavailable("COMMISSION_ASSET_NOT_USDT")
        if out.get("funding_included") is not False:
            raise PnlUnavailable("FUNDING_CONTRACT_MISMATCH")       # çifte sayımı önlemek için sözleşme değişirse dur
        entry_ms = _ms(out.get("opened_at"))
        if entry_ms is None or out.get("exit_price") is None or not int(out.get("trade_count") or 0) >= 2:
            raise PnlUnavailable("ENTRY_OR_EXIT_NOT_PROVEN")
        end_ms = int(float(result["closed_at"]) * 1000) + END_SKEW_MS
        symbol = str(result["symbol"]).upper()

        # ikinci kaynak: gerçekleşen PnL ve komisyon gelir kayıtları (pozisyon penceresi)
        realized = sum_income(self.source.income_rows(symbol, REALIZED, entry_ms, end_ms), REALIZED, symbol, entry_ms, end_ms)
        paid = -sum_income(self.source.income_rows(symbol, COMMISSION, entry_ms, end_ms), COMMISSION, symbol, entry_ms, end_ms)
        if abs(realized - gross) > TOLERANCE:
            raise PnlUnavailable("GROSS_SOURCES_DISAGREE")
        if abs(paid - commission) > TOLERANCE:
            raise PnlUnavailable("COMMISSION_SOURCES_DISAGREE")

        # funding atfı: yalnız bu sembol + bu pozisyonun penceresi; pencere başka Farmer/strateji ile çakışıyorsa belirsiz
        for other in self.store.results(result["account_key"]):
            if other["grid_id"] == result["grid_id"] or other["symbol"] != result["symbol"]:
                continue
            o_start, o_end = float(other["opened_at"]) * 1000, (float(other["closed_at"]) * 1000 if float(other["closed_at"] or 0) > 0 else float("inf"))
            if o_start <= end_ms and entry_ms <= o_end:
                raise PnlUnavailable("FUNDING_WINDOW_OVERLAPS_FARMER")
        if self.source.foreign_overlaps(symbol, entry_ms, end_ms):
            raise PnlUnavailable("FUNDING_WINDOW_OVERLAPS_OTHER_BOT")
        funding = sum_income(self.source.income_rows(symbol, FUNDING, entry_ms, end_ms), FUNDING, symbol, entry_ms, end_ms)

        net = gross - commission + funding
        return PnlOutcome(VERIFIED, gross=float(gross), commission=float(commission), funding=float(funding), net=float(net))


def _safe(code: str) -> str:
    return "".join(ch for ch in str(code) if ch.isalnum() or ch in "_:-")[:80] or "UNAVAILABLE"
