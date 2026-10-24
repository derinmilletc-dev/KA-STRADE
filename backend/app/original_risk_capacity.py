"""Original V2 LIVE: bir işlem için hedef / uygulanabilir maksimum / gerçekleşecek riskin AYRI hesabı.

Bu modül yalnız ölçer ve açıklar; giriş boyutunu, kapıları, stop'u veya hedefleri DEĞİŞTİRMEZ.
Girdi/çıktı saf sayıdır (I/O yok), Demo ve backtest katmanları bunu çağırmaz.

Üç ayrı değer:
- hedef risk: kullanıcının seçtiği yüzde x bakiye (sistem tavanından önce).
- uygulanabilir maksimum risk: bu işlemin stop mesafesi, işlem başı teminat sınırı, toplam teminatın kalan payı,
  kullanılabilir bakiye ve Binance üst miktarı ile ulaşılabilecek EN BÜYÜK stop kaybı (ücret + kayma tahmini dahil).
- gerçekleşecek risk: botun gerçekten hesapladığı miktarın stop kaybı (adım yuvarlaması ve tavanlar sonrası).

Hesaba katılmayanlar `excludes` içinde açıkça yazılır: stop'un kaymayla/boşlukla aşılması ve fonlama.
Likidasyon payı (bakım teminatı kademeleri) bu hesaba girmez; ayrı bir kapıdır ve girişi yine reddedebilir.
"""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_FLOOR
from typing import Any

EXCLUDES = ("stop_kayma_boşluk", "fonlama")
TOLERANCE = 1e-8


def _floor_to_step(value: Decimal, step: Decimal | None) -> Decimal:
    if value <= 0:
        return Decimal(0)
    if step is None or step <= 0:
        return value
    return (value / step).to_integral_value(ROUND_FLOOR) * step


def _positive(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except Exception:  # noqa: BLE001 - doğrulanamayan değer "bilinmiyor" sayılır
        return None
    return number if number.is_finite() and number > 0 else None


def trade_risk_breakdown(
    *,
    wallet: float,
    selected_percent: float,
    budget_loss: float,
    entry: Decimal,
    stop: Decimal,
    cost_per_unit: Decimal,
    leverage: int,
    margin_cap_usdt: float,
    total_margin_room_usdt: float,
    available_balance: Any,
    fee_rate: float,
    max_quantity: Any,
    quantity_step: Decimal | None,
    effective_loss: float,
) -> dict[str, Any]:
    """Salt okunur ayrıntı. Hiçbir koşulda istisna fırlatmaz sonuç ya da {"status": "unavailable"} döner."""
    try:
        per_unit = abs(entry - stop) + cost_per_unit
        if not (entry > 0 and per_unit > 0 and wallet > 0 and leverage >= 1):
            return {"status": "unavailable", "reason": "Giriş fiyatı, stop veya bakiye doğrulanamadı."}
        target_loss = wallet * selected_percent / 100
        lev = Decimal(leverage)
        factors: list[tuple[str, Decimal]] = [
            ("işlem başı teminat sınırı", Decimal(str(max(margin_cap_usdt, 0))) * lev / entry),
            ("toplam teminatın kalan payı", Decimal(str(max(total_margin_room_usdt, 0))) * lev / entry),
        ]
        unverified: list[str] = []
        available = _positive(available_balance)
        if available is None:
            unverified.append("kullanılabilir bakiye")
        else:
            # Teminat + giriş ücreti, kullanılabilir bakiyeyi aşamaz.
            factors.append(("kullanılabilir bakiye", available / (entry * (Decimal(1) / lev + Decimal(str(fee_rate))))))
        exchange_max = _positive(max_quantity)
        if exchange_max is not None:
            factors.append(("Binance üst miktar sınırı", exchange_max))
        limiting_name, limiting_qty = min(factors, key=lambda item: item[1])
        max_qty = _floor_to_step(limiting_qty, quantity_step)
        max_loss = float(max_qty * per_unit)
        reachable_loss = min(max_loss, budget_loss)
        reachable = reachable_loss + TOLERANCE >= target_loss
        sizing_binding = "risk bütçesi"
        if not reachable:
            sizing_binding = limiting_name if max_loss <= budget_loss else "sistem güvenlik tavanı"
        return {
            "status": "partial" if unverified else "verified",
            "unverified": unverified,
            "target_loss_usdt": target_loss,
            "target_percent": selected_percent,
            "budget_loss_usdt": budget_loss,
            "max_applicable_loss_usdt": max_loss,
            "max_applicable_percent": max_loss / wallet * 100,
            "effective_loss_usdt": effective_loss,
            "effective_percent": effective_loss / wallet * 100,
            "reachable": reachable,
            "limiting_factor": limiting_name,
            "unreached_reason": None if reachable else sizing_binding,
            "stop_distance_pct": float(abs(entry - stop) / entry * 100),
            "excludes": list(EXCLUDES),
            "liquidation_checked_separately": True,
        }
    except Exception as exc:  # noqa: BLE001 - bilgi amaçlı; giriş kararını asla etkilemez
        return {"status": "unavailable", "reason": f"Risk ayrıntısı hesaplanamadı ({type(exc).__name__})."}


def capacity_preview(*, wallet: float, max_margin_usdt: float, leverage: int, cost_pct: float,
                     max_stop_pct: float = 6.0) -> dict[str, Any] | None:
    """Aday yokken ekranda gösterilen teorik üst sınır (boş hesap: açık pozisyon ve anlık bakiye payı yok)."""
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
               for v in (wallet, max_margin_usdt, leverage, cost_pct, max_stop_pct)):
        return None
    if wallet <= 0 or max_margin_usdt <= 0 or leverage < 1:
        return None
    max_notional = max_margin_usdt * leverage
    rows = []
    for stop_pct in (1.0, 2.0, 3.0, 4.0, 5.0, max_stop_pct):
        loss = max_notional * (stop_pct + cost_pct) / 100
        rows.append({"stop_pct": stop_pct, "max_loss_usdt": loss, "max_percent": loss / wallet * 100})
    return {"max_notional_usdt": max_notional, "cost_pct": cost_pct, "by_stop_distance": rows}
