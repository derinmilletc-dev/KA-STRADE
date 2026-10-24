"""Manual one-directional grid planner (pure math, no exchange calls, no orders).

Splits a user-chosen total risk over up to 5 limit levels sharing one stop, and checks
the result against the account execution policy. Placing live grid orders is a separate step.
"""
from __future__ import annotations

from typing import Any

MAX_LEVELS = 5
MIN_LEVELS = 2
DEFAULT_COST_BPS = 8.0  # round-trip fee/slippage allowance per level


class GridError(ValueError):
    pass


def plan_grid(
    *, direction: str, start_price: float, end_price: float, levels: int, stop_loss: float,
    total_capital_usdt: float, total_risk_usdt: float, leverage: int,
    policy: dict[str, Any], cost_bps: float = DEFAULT_COST_BPS,
) -> dict[str, Any]:
    direction = str(direction).upper()
    if direction not in {"LONG", "SHORT"}:
        raise GridError("Yön LONG veya SHORT olmalı")
    if not MIN_LEVELS <= int(levels) <= MAX_LEVELS:
        raise GridError(f"Kademe sayısı {MIN_LEVELS}-{MAX_LEVELS} arasında olmalı")
    values = (start_price, end_price, stop_loss, total_capital_usdt, total_risk_usdt, leverage)
    if not all(isinstance(v, (int, float)) and v == v and v > 0 and v != float("inf") for v in values):
        raise GridError("Fiyat, sermaye, risk ve kaldıraç sıfırdan büyük olmalı")
    if not 0 <= cost_bps <= 100:
        raise GridError("Maliyet payı geçersiz")
    levels = int(levels)
    if direction == "LONG" and not (stop_loss < end_price < start_price):
        raise GridError("LONG için sıra: Stop < son kademe < ilk kademe olmalı")
    if direction == "SHORT" and not (start_price < end_price < stop_loss):
        raise GridError("SHORT için sıra: ilk kademe < son kademe < Stop olmalı")
    if total_risk_usdt > total_capital_usdt:
        raise GridError("Toplam risk, grid sermayesinden büyük olamaz")

    step = (end_price - start_price) / (levels - 1)
    risk_each = total_risk_usdt / levels
    rows = []
    for i in range(levels):
        price = start_price + step * i
        take_profit = price - step
        distance = abs(price - stop_loss) + price * cost_bps / 10000
        quantity = risk_each / distance
        notional = quantity * price
        rows.append({
            "level": i + 1, "entry_price": round(price, 10), "take_profit": round(take_profit, 10),
            "quantity": quantity, "notional_usdt": round(notional, 6),
            "margin_usdt": round(notional / leverage, 6), "risk_usdt": round(risk_each, 6),
        })
    total_margin = sum(r["margin_usdt"] for r in rows)
    total_notional = sum(r["notional_usdt"] for r in rows)
    farthest_stop_pct = max(abs(r["entry_price"] - stop_loss) / r["entry_price"] * 100 for r in rows)

    blocked: list[str] = []
    if total_margin > total_capital_usdt + 1e-9:
        blocked.append(f"Gereken teminat {total_margin:.2f} USDT, grid sermayesi {total_capital_usdt:.2f} USDT")
    if leverage > float(policy.get("max_leverage", 30)):
        blocked.append(f"Kaldıraç hesap sınırı {policy.get('max_leverage')} üstünde")
    if total_risk_usdt > float(policy.get("daily_loss_limit", 10)):
        blocked.append(f"Toplam risk günlük kayıp limiti {policy.get('daily_loss_limit')} USDT üstünde")
    if total_notional > float(policy.get("max_total_exposure_usdt", 350)):
        blocked.append(f"Toplam pozisyon büyüklüğü {total_notional:.2f}, hesap sınırı {policy.get('max_total_exposure_usdt')} USDT")
    if farthest_stop_pct > float(policy.get("max_stop_distance_pct", 5)):
        blocked.append(f"En uzak kademenin stop mesafesi %{farthest_stop_pct:.2f}, sınır %{policy.get('max_stop_distance_pct')}")
    return {
        "direction": direction, "levels": rows, "stop_loss": stop_loss,
        "total_risk_usdt": round(total_risk_usdt, 6), "total_margin_usdt": round(total_margin, 6),
        "total_notional_usdt": round(total_notional, 6), "farthest_stop_pct": round(farthest_stop_pct, 4),
        "blocked_by": blocked, "within_account_limits": not blocked,
    }
