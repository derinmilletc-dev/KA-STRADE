"""Manuel GRID — canlı çalıştırma katmanı (sunucuda varsayılan KAPALI).

- Açılması için sunucuda KAISTRADE_GRID_LIVE=1 olmalı (kullanıcı kendisi açar). Kapalıyken başlatma reddedilir.
- Tavanlar: en fazla 20 kademe, toplam marjin en fazla 300 USDT (sistem tavanı), kaldıraç en fazla 20x. Asıl koruma hesabın
  kendi risk ayarlarıdır: en kötü zarar, tek işlem ve günlük zarar sınırını, toplam büyüklük de maruziyet sınırını aşarsa grid HİÇ başlamaz.
- Plan, ekrandaki önizlemeyle AYNI hesaptan geçer (gridMath.ts): kademe/miktar borsa adımlarına yuvarlanır, en kötü zarar
  tek işlem sınırını ve günlük zarar hakkını aşarsa grid HİÇ başlamaz.
- Emirler mevcut canlı bağlantıdan (BinanceLiveClient) gider; bu dosya anahtar istemez/okumaz.
- Her dolan miktar için borsada koruyucu stop vardır (motor: önce stop, sonra hedef).
- ACİL DURDUR: girişleri iptal eder, piyasadan kapatır, kapandığını borsadan doğrular; doğrulayamazsa stoplara DOKUNMAZ.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import HTTPException

from .execution_core import HARD_MAX_MARGIN_USDT
from .grid_engine.engine import (BLOCKED, COMPLETED, EMERGENCY_CLOSED, INTERVENTION_REQUIRED, PAUSED, RUNNING, STOPPED, GridEngine,
                                 GridLevelSpec, GridSpec, grid_stats)
from .grid_engine.live_adapter import BinanceLiveGridAdapter
from .grid_engine.neutral import NEUTRAL, NeutralGridEngine
from .grid_engine.ports import EPS, LONG, SHORT
from .grid_engine.store import TERMINAL, GridStore
from .web_security import env_flag

logger = logging.getLogger(__name__)

ENV_FLAG = "KAISTRADE_GRID_LIVE"
LIVE_MAX_LEVELS = 20
LIVE_MAX_MARGIN_USDT = HARD_MAX_MARGIN_USDT   # sistem genel tavanı (300)
LIVE_MAX_LEVERAGE = 20
CONFIRM_START = "CANLI GRID BAŞLAT"
CONFIRM_EMERGENCY = "GRID ACİL DURDUR"
MAKER_FEE, TAKER_FEE = 0.0002, 0.0005          # gridMath.ts ile aynı
TICK_SECONDS = 5.0
MAX_LOOP_ERRORS = 6
FINAL_STATUSES = {STOPPED, COMPLETED, EMERGENCY_CLOSED}
MAX_GRIDS_PER_USER = 5                         # kullanıcı başına aynı anda çalışan grid (coin başına bir)
NEEDS_USER = {BLOCKED, INTERVENTION_REQUIRED}


class LiveGridError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def live_enabled() -> bool:
    return env_flag(ENV_FLAG)


def limits_payload() -> dict[str, Any]:
    return {"enabled": live_enabled(), "max_levels": LIVE_MAX_LEVELS, "max_margin_usdt": LIVE_MAX_MARGIN_USDT, "max_leverage": LIVE_MAX_LEVERAGE}


# ---------------------------------------------------------------- saf hesap (ağ yok)
def _dec(value: Any) -> Decimal:
    return Decimal(repr(float(value))) if not isinstance(value, Decimal) else value


def _snap(value: Any, tick: Decimal, mode: str = ROUND_HALF_UP) -> Decimal:
    return (_dec(value) / tick).to_integral_value(rounding=mode) * tick


def prepare_live_grid(*, direction: str, lower: float, upper: float, count: int, margin_usdt: float, leverage: int, stop: float,
                      price: float, rules: dict[str, Any], policy: dict[str, Any], realized_pnl_today: float,
                      existing_exposure: float, open_positions: int, recycle: bool = False, reserved_worst_loss: float = 0.0,
                      reserved_pending_notional: float = 0.0, reserved_symbols: int = 0) -> dict[str, Any]:
    """Önizlemeyle aynı hesap + canlı sert sınırlar. Herhangi bir kontrol başarısızsa LiveGridError (grid başlamaz)."""
    direction = str(direction).upper()
    if direction not in (LONG, SHORT):
        raise LiveGridError("Yön LONG veya SHORT olmalı.")
    values = (lower, upper, margin_usdt, leverage, stop, price)
    if not all(isinstance(v, (int, float)) and v == v and v > 0 and v != float("inf") for v in values):
        raise LiveGridError("Fiyat, marjin ve kaldıraç sıfırdan büyük sayı olmalı.")
    count = int(count)
    problems: list[str] = []
    if not 2 <= count <= LIVE_MAX_LEVELS:
        raise LiveGridError(f"Kademe sayısı 2 ile {LIVE_MAX_LEVELS} arasında olmalı.")
    if margin_usdt > LIVE_MAX_MARGIN_USDT + 1e-9:
        raise LiveGridError(f"Toplam marjin en fazla {LIVE_MAX_MARGIN_USDT:g} USDT olabilir.")
    max_lev = min(LIVE_MAX_LEVERAGE, int(float(policy.get("max_leverage", LIVE_MAX_LEVERAGE))))
    if leverage > max_lev:
        raise LiveGridError(f"Kaldıraç en fazla {max_lev}x olabilir.")
    if lower >= upper:
        raise LiveGridError("Alt sınır, üst sınırdan küçük olmalı.")
    if direction == LONG and stop >= lower:
        raise LiveGridError("LONG grid için stop, alt sınırın altında olmalı.")
    if direction == SHORT and stop <= upper:
        raise LiveGridError("SHORT grid için stop, üst sınırın üstünde olmalı.")
    try:
        tick, step = _dec(rules["tick"]), _dec(rules["step"])
        min_qty, min_notional = _dec(rules.get("min_qty", 0)), _dec(rules.get("min_notional", 0))
        max_qty = _dec(rules["max_qty"]) if rules.get("max_qty") is not None else None
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise LiveGridError("Borsa fiyat/miktar kuralları okunamadı; grid başlatılmadı.", 503) from None
    if not (tick > 0 and step > 0):
        raise LiveGridError("Borsa fiyat/miktar kuralları geçersiz; grid başlatılmadı.", 503)

    spacing = (upper - lower) / (count - 1)
    per_margin = margin_usdt / count
    rows: list[dict[str, Any]] = []
    for i in range(count):
        entry_raw = upper - spacing * i if direction == LONG else lower + spacing * i
        target_raw = entry_raw + spacing if direction == LONG else entry_raw - spacing
        entry, target = _snap(entry_raw, tick), _snap(target_raw, tick)
        qty = _snap(per_margin * leverage / float(entry), step, ROUND_FLOOR)
        rows.append({"level": i + 1, "entry": float(entry), "target": float(target), "qty": float(qty)})
    stop_price = float(_snap(stop, tick, ROUND_FLOOR if direction == LONG else ROUND_CEILING))

    if len({r["entry"] for r in rows}) != len(rows):
        problems.append("Yuvarlamadan sonra iki kademe aynı fiyata düşüyor.")
    for r in rows:
        notional = r["qty"] * r["entry"]
        if r["qty"] <= 0 or Decimal(repr(r["qty"])) < min_qty or notional + 1e-9 < max(float(min_notional), 5.0):
            problems.append(f"Kademe {r['level']} borsanın en düşük emir değerinin altında.")
        if max_qty is not None and Decimal(repr(r["qty"])) > max_qty:
            problems.append(f"Kademe {r['level']} borsanın en büyük miktarını aşıyor.")
        better = r["target"] > r["entry"] if direction == LONG else r["target"] < r["entry"]
        if not better:
            problems.append(f"Kademe {r['level']} hedefi girişten ileride değil.")
        elif abs(r["target"] - r["entry"]) * r["qty"] <= (r["entry"] + r["target"]) * r["qty"] * MAKER_FEE + 1e-12:
            problems.append(f"Kademe {r['level']} kârı işlem ücretini karşılamıyor; aralığı genişlet ya da kademe sayısını azalt.")
        wrong_side = r["entry"] >= price if direction == LONG else r["entry"] <= price
        if wrong_side:
            problems.append(f"Kademe {r['level']} güncel fiyatın yanlış tarafında; limit emir hemen dolar.")
        beyond = r["entry"] <= stop_price if direction == LONG else r["entry"] >= stop_price
        if beyond:
            problems.append(f"Kademe {r['level']} stopun ötesinde.")

    total_notional = sum(r["qty"] * r["entry"] for r in rows)
    total_margin = total_notional / leverage
    raw_loss = sum(r["qty"] * abs(r["entry"] - stop_price) for r in rows)
    worst_loss = raw_loss + total_notional * MAKER_FEE + sum(r["qty"] * stop_price for r in rows) * TAKER_FEE
    far_entry = min(r["entry"] for r in rows) if direction == LONG else max(r["entry"] for r in rows)
    stop_distance_pct = abs(far_entry - stop_price) / far_entry * 100

    if total_margin > LIVE_MAX_MARGIN_USDT + 1e-9 or total_margin > margin_usdt + 1e-6:
        problems.append(f"Gereken marjin {total_margin:.2f} USDT, sınırı aşıyor.")
    loss_limit = float(policy.get("max_loss_per_trade", 3))
    if worst_loss > loss_limit + 1e-9:
        problems.append(f"En kötü zarar {worst_loss:.2f} USDT, tek işlem sınırı {loss_limit:g} USDT.")
    daily_left = float(policy.get("daily_loss_limit", 10)) + min(0.0, float(realized_pnl_today)) - max(0.0, float(reserved_worst_loss))
    if daily_left <= 0 or worst_loss > daily_left + 1e-9:
        reserved_note = f" (çalışan grid'ler {float(reserved_worst_loss):.2f} USDT ayırdı)" if reserved_worst_loss > 0 else ""
        problems.append(f"En kötü zarar {worst_loss:.2f} USDT, bugün kalan zarar hakkı {max(daily_left, 0):.2f} USDT{reserved_note}.")
    exposure_limit = float(policy.get("max_total_exposure_usdt", 350))
    exposure_now = existing_exposure + max(0.0, float(reserved_pending_notional))
    if exposure_now + total_notional > exposure_limit + 1e-9:
        problems.append(f"Toplam maruziyet {exposure_now + total_notional:.2f}, sınır {exposure_limit:g} USDT.")
    if open_positions + max(0, int(reserved_symbols)) >= int(policy.get("max_positions", 5)):
        problems.append("Açık pozisyon / grid sınırı dolu.")
    max_stop_pct = float(policy.get("max_stop_distance_pct", 5))
    if stop_distance_pct > max_stop_pct + 1e-9:
        problems.append(f"Stop mesafesi %{stop_distance_pct:.2f}, sınır %{max_stop_pct:g}.")
    if stop_distance_pct * leverage > 70:
        problems.append("Stop, tahmini likidasyona çok yakın; kaldıracı düşür ya da stopu yakınlaştır.")
    if problems:
        raise LiveGridError(" ".join(dict.fromkeys(problems)))
    gross_per_cycle = sum(abs(r["target"] - r["entry"]) * r["qty"] for r in rows)
    fees_per_cycle = sum((r["entry"] + r["target"]) * r["qty"] * MAKER_FEE for r in rows)
    return {"direction": direction, "stop_price": stop_price, "levels": rows, "total_margin_usdt": round(total_margin, 6),
            "total_notional_usdt": round(total_notional, 6), "worst_loss_usdt": round(worst_loss, 6),
            "stop_distance_pct": round(stop_distance_pct, 4), "leverage": int(leverage), "recycle": bool(recycle),
            "lower": float(lower), "upper": float(upper), "count": count,
            "profit_per_cycle_usdt": round((gross_per_cycle - fees_per_cycle) / count, 8), "spacing_pct": round(spacing / price * 100, 4)}


def _parse_rules(rules: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal | None]:
    try:
        tick, step = _dec(rules["tick"]), _dec(rules["step"])
        min_qty, min_notional = _dec(rules.get("min_qty", 0)), _dec(rules.get("min_notional", 0))
        max_qty = _dec(rules["max_qty"]) if rules.get("max_qty") is not None else None
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise LiveGridError("Borsa fiyat/miktar kuralları okunamadı; grid başlatılmadı.", 503) from None
    if not (tick > 0 and step > 0):
        raise LiveGridError("Borsa fiyat/miktar kuralları geçersiz; grid başlatılmadı.", 503)
    return tick, step, min_qty, min_notional, max_qty


def prepare_live_neutral_grid(*, lower: float, upper: float, count: int, margin_usdt: float, leverage: int, stop_lower: float,
                              stop_upper: float, price: float, rules: dict[str, Any], policy: dict[str, Any], realized_pnl_today: float,
                              existing_exposure: float, open_positions: int, reserved_worst_loss: float = 0.0,
                              reserved_pending_notional: float = 0.0, reserved_symbols: int = 0) -> dict[str, Any]:
    """Nötr grid: alt-üst aralığa count+1 çizgi; fiyata en yakın çizgi boş, altında ALIŞ, üstünde SATIŞ (her biri bir çizgi ötede hedef).
    İki tarafta stop. En kötü zarar, fiyatın tek yöne gidip ilgili stopa ulaştığı senaryodur. Kontrol başarısızsa grid başlamaz."""
    values = (lower, upper, margin_usdt, leverage, stop_lower, stop_upper, price)
    if not all(isinstance(v, (int, float)) and v == v and v > 0 and v != float("inf") for v in values):
        raise LiveGridError("Fiyat, marjin ve kaldıraç sıfırdan büyük sayı olmalı.")
    count = int(count)
    if not 2 <= count <= LIVE_MAX_LEVELS:
        raise LiveGridError(f"Kademe sayısı 2 ile {LIVE_MAX_LEVELS} arasında olmalı.")
    if margin_usdt > LIVE_MAX_MARGIN_USDT + 1e-9:
        raise LiveGridError(f"Toplam marjin en fazla {LIVE_MAX_MARGIN_USDT:g} USDT olabilir.")
    max_lev = min(LIVE_MAX_LEVERAGE, int(float(policy.get("max_leverage", LIVE_MAX_LEVERAGE))))
    if leverage > max_lev:
        raise LiveGridError(f"Kaldıraç en fazla {max_lev}x olabilir.")
    if not lower < price < upper:
        raise LiveGridError("Nötr grid için güncel fiyat alt ve üst sınırın arasında olmalı.")
    if not stop_lower < lower:
        raise LiveGridError("Alt stop, alt sınırın altında olmalı.")
    if not stop_upper > upper:
        raise LiveGridError("Üst stop, üst sınırın üstünde olmalı.")
    tick, step, min_qty, min_notional, max_qty = _parse_rules(rules)

    spacing = (upper - lower) / count
    lines = [float(_snap(lower + spacing * k, tick)) for k in range(count + 1)]
    problems: list[str] = []
    if len(set(lines)) != len(lines):
        problems.append("Yuvarlamadan sonra iki çizgi aynı fiyata düşüyor; aralığı genişlet ya da kademe sayısını azalt.")
    skip = min(range(len(lines)), key=lambda k: abs(lines[k] - price))
    per_margin = margin_usdt / count
    rows: list[dict[str, Any]] = []
    for k, line in enumerate(lines):
        if k == skip:
            continue
        side = LONG if k < skip else SHORT
        target = lines[k + 1] if side == LONG else lines[k - 1]
        qty = _snap(per_margin * leverage / line, step, ROUND_FLOOR)
        rows.append({"level": len(rows) + 1, "entry": line, "target": target, "qty": float(qty), "side": side})
    stop_lo = float(_snap(stop_lower, tick, ROUND_FLOOR))
    stop_hi = float(_snap(stop_upper, tick, ROUND_CEILING))
    for r in rows:
        notional = r["qty"] * r["entry"]
        if r["qty"] <= 0 or Decimal(repr(r["qty"])) < min_qty or notional + 1e-9 < max(float(min_notional), 5.0):
            problems.append(f"Kademe {r['level']} borsanın en düşük emir değerinin altında.")
        if max_qty is not None and Decimal(repr(r["qty"])) > max_qty:
            problems.append(f"Kademe {r['level']} borsanın en büyük miktarını aşıyor.")
        if abs(r["target"] - r["entry"]) * r["qty"] <= (r["entry"] + r["target"]) * r["qty"] * MAKER_FEE + 1e-12:
            problems.append(f"Kademe {r['level']} kârı işlem ücretini karşılamıyor; aralığı genişlet ya da kademe sayısını azalt.")
        wrong_side = r["entry"] >= price if r["side"] == LONG else r["entry"] <= price
        if wrong_side:
            problems.append(f"Kademe {r['level']} güncel fiyatın yanlış tarafında; limit emir hemen dolar.")
    longs = [r for r in rows if r["side"] == LONG]
    shorts = [r for r in rows if r["side"] == SHORT]
    if not longs or not shorts:
        problems.append("Fiyat aralığın bir ucuna çok yakın; nötr grid'in iki tarafında da kademe olmalı.")

    def side_loss(items: list[dict[str, Any]], stop: float) -> float:
        raw = sum(r["qty"] * abs(r["entry"] - stop) for r in items)
        return raw + sum(r["qty"] * r["entry"] for r in items) * MAKER_FEE + sum(r["qty"] * stop for r in items) * TAKER_FEE

    loss_down, loss_up = side_loss(longs, stop_lo), side_loss(shorts, stop_hi)
    worst_loss = max(loss_down, loss_up)
    long_notional = sum(r["qty"] * r["entry"] for r in longs)
    short_notional = sum(r["qty"] * r["entry"] for r in shorts)
    total_notional = long_notional + short_notional
    total_margin = total_notional / leverage
    near_long = max((r["entry"] for r in longs), default=price)
    near_short = min((r["entry"] for r in shorts), default=price)
    far_long = min((r["entry"] for r in longs), default=price)
    far_short = max((r["entry"] for r in shorts), default=price)
    stop_distance_pct = max(abs(far_long - stop_lo) / far_long * 100, abs(stop_hi - far_short) / far_short * 100)
    worst_stretch_pct = max(abs(near_long - stop_lo) / near_long * 100, abs(stop_hi - near_short) / near_short * 100)

    if total_margin > LIVE_MAX_MARGIN_USDT + 1e-9 or total_margin > margin_usdt + 1e-6:
        problems.append(f"Gereken marjin {total_margin:.2f} USDT, sınırı aşıyor.")
    loss_limit = float(policy.get("max_loss_per_trade", 3))
    if worst_loss > loss_limit + 1e-9:
        problems.append(f"En kötü zarar {worst_loss:.2f} USDT, tek işlem sınırı {loss_limit:g} USDT.")
    daily_left = float(policy.get("daily_loss_limit", 10)) + min(0.0, float(realized_pnl_today)) - max(0.0, float(reserved_worst_loss))
    if daily_left <= 0 or worst_loss > daily_left + 1e-9:
        reserved_note = f" (çalışan grid'ler {float(reserved_worst_loss):.2f} USDT ayırdı)" if reserved_worst_loss > 0 else ""
        problems.append(f"En kötü zarar {worst_loss:.2f} USDT, bugün kalan zarar hakkı {max(daily_left, 0):.2f} USDT{reserved_note}.")
    exposure_limit = float(policy.get("max_total_exposure_usdt", 350))
    exposure_now = existing_exposure + max(0.0, float(reserved_pending_notional))
    side_max = max(long_notional, short_notional)          # aynı anda yalnız bir taraf açık pozisyona dönüşür
    if exposure_now + side_max > exposure_limit + 1e-9:
        problems.append(f"Toplam maruziyet {exposure_now + side_max:.2f}, sınır {exposure_limit:g} USDT.")
    if open_positions + max(0, int(reserved_symbols)) >= int(policy.get("max_positions", 5)):
        problems.append("Açık pozisyon / grid sınırı dolu.")
    max_stop_pct = float(policy.get("max_stop_distance_pct", 5))
    if stop_distance_pct > max_stop_pct + 1e-9:
        problems.append(f"Stop mesafesi %{stop_distance_pct:.2f}, sınır %{max_stop_pct:g}.")
    if worst_stretch_pct * leverage > 70:
        problems.append("Stop, tahmini likidasyona çok yakın; kaldıracı düşür ya da stopları yakınlaştır.")
    if problems:
        raise LiveGridError(" ".join(dict.fromkeys(problems)))
    gross = sum(abs(r["target"] - r["entry"]) * r["qty"] for r in rows)
    fees = sum((r["entry"] + r["target"]) * r["qty"] * MAKER_FEE for r in rows)
    return {"direction": NEUTRAL, "stop_price": stop_lo, "stop_price_upper": stop_hi, "levels": rows,
            "total_margin_usdt": round(total_margin, 6), "total_notional_usdt": round(total_notional, 6),
            "worst_loss_usdt": round(worst_loss, 6), "worst_loss_down_usdt": round(loss_down, 6), "worst_loss_up_usdt": round(loss_up, 6),
            "stop_distance_pct": round(stop_distance_pct, 4), "leverage": int(leverage), "recycle": True,
            "lower": float(lower), "upper": float(upper), "count": count,
            "profit_per_cycle_usdt": round((gross - fees) / len(rows), 8), "spacing_pct": round(spacing / price * 100, 4)}


def grid_spec_from_plan(grid_id: str, symbol: str, plan: dict[str, Any]) -> GridSpec:
    meta = {key: plan[key] for key in ("total_margin_usdt", "total_notional_usdt", "worst_loss_usdt", "leverage", "stop_distance_pct",
                                       "lower", "upper", "count", "profit_per_cycle_usdt", "spacing_pct") if key in plan}
    return GridSpec(grid_id, symbol, plan["direction"],
                    [GridLevelSpec(r["entry"], r["target"], r["qty"], str(r.get("side") or "")) for r in plan["levels"]],
                    plan["stop_price"], recycle=bool(plan.get("recycle", False)), meta=meta,
                    stop_price_upper=float(plan.get("stop_price_upper") or 0.0))


def grid_spec_from_row(row: Any) -> GridSpec:
    spec = json.loads(row["spec"])
    return GridSpec(row["grid_id"], row["symbol"], row["side"], [GridLevelSpec(**lv) for lv in spec["levels"]], float(spec["stop_price"]),
                    recycle=bool(spec.get("recycle", False)), meta=dict(spec.get("meta") or {}),
                    stop_price_upper=float(spec.get("stop_price_upper") or 0.0))


def _owner_matches(row: Any, user_id: str, *, owner: bool) -> bool:
    """Grid'in sahibi bu kullanıcı mı? Sahipsiz ('') eski kayıtlar yalnız yöneticiye aittir."""
    row_user = str(row["user_id"] or "")
    return row_user == str(user_id or "") if row_user else bool(owner)


# ---------------------------------------------------------------- çalıştırıcı
class LiveGridRunner:
    """Kullanıcı başına birden fazla canlı grid (coin başına bir). Her grid kendi döngüsünde koşar; motor çağrıları
    ayrı iş parçacığında yapılır ve borsa çağrıları olay döngüsüne köprülenir. Bir kullanıcı yalnız kendi grid'lerini görür/yönetir."""

    def __init__(self, store: GridStore) -> None:
        self.store = store
        self._engines: dict[str, GridEngine] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._paused_for_block: set[str] = set()
        self._errors: dict[str, int] = {}
        self._adapters: dict[str, BinanceLiveGridAdapter] = {}
        self._lock: asyncio.Lock | None = None

    # ---- geriye uyum (tek grid dönemi alanları)
    @property
    def _engine(self) -> GridEngine | None:
        row = self.store.latest_grid()
        return self._engines.get(row["grid_id"]) if row is not None else None

    @property
    def loop_errors(self) -> int:
        return max(self._errors.values(), default=0)

    @property
    def lock(self) -> asyncio.Lock:
        """Başlatma (yer kontrolü + kayıt) için tek kilit: aynı anda iki istek aynı coinde iki grid açamaz."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def grid_lock(self, grid_id: str) -> asyncio.Lock:
        lock = self._locks.get(grid_id)
        if lock is None:
            lock = self._locks[grid_id] = asyncio.Lock()
        return lock

    # ---- defter okuma
    def current_row(self, user_id: str | None = None, *, owner: bool = True) -> Any:
        if user_id is None:
            return self.store.latest_grid()
        rows = self.user_rows(user_id, owner=owner)
        return rows[0] if rows else None

    def user_rows(self, user_id: str, *, owner: bool = False, active_only: bool = False) -> list[Any]:
        rows = self.store.grids(user_id=str(user_id or ""), include_legacy=owner,
                                exclude_statuses=FINAL_STATUSES if active_only else None)
        return [r for r in rows if _owner_matches(r, user_id, owner=owner)]

    def occupying_row(self, user_id: str | None = None, symbol: str | None = None, *, owner: bool = True) -> Any:
        """Yer tutan (bitmemiş) grid. user_id yoksa tüm defter (eski davranış)."""
        if user_id is None:
            rows = self.store.grids(exclude_statuses=FINAL_STATUSES)
        else:
            rows = self.user_rows(user_id, owner=owner, active_only=True)
        if symbol is not None:
            rows = [r for r in rows if r["symbol"] == symbol]
        return rows[0] if rows else None

    def occupying_rows(self, user_id: str, *, owner: bool = False) -> list[Any]:
        return self.user_rows(user_id, owner=owner, active_only=True)

    def symbol_busy(self, symbol: str, user_id: str = "", *, owner: bool = False) -> bool:
        """Bu kullanıcının bu coinde bitmemiş grid'i var mı? (Manuel/otomatik emir bu coine girmemeli.)"""
        return self.occupying_row(str(user_id or ""), symbol, owner=owner) is not None

    def loop_running(self, grid_id: str | None = None) -> bool:
        if grid_id is None:
            return any(not task.done() for task in self._tasks.values())
        task = self._tasks.get(grid_id)
        return task is not None and not task.done()

    def reserved_risk(self, user_id: str, *, owner: bool = False) -> dict[str, Any]:
        """Kullanıcının çalışan grid'lerinin ayırdığı risk: en kötü zarar toplamı ve henüz dolmamış giriş emirlerinin değeri
        (dolmuş kısım zaten borsa pozisyonu olarak sayılır; çift sayılmaz)."""
        worst, pending, symbols = 0.0, 0.0, []
        for row in self.occupying_rows(user_id, owner=owner):
            spec = json.loads(row["spec"])
            worst += float((spec.get("meta") or {}).get("worst_loss_usdt") or 0.0)
            for o in self.store.orders(row["grid_id"], "E"):
                if o["status"] not in TERMINAL:
                    pending += max(0.0, o["qty"] - o["filled"]) * o["price"]
            symbols.append(row["symbol"])
        return {"worst_loss_usdt": round(worst, 8), "pending_entry_notional_usdt": round(pending, 8), "symbols": symbols,
                "count": len(symbols)}

    def grid_payload(self, row: Any) -> dict[str, Any]:
        gid = row["grid_id"]
        orders = self.store.orders(gid)
        net = 0.0
        for o in orders:
            net += o["filled"] if o["role"] == "E" else -o["filled"] if o["role"] in ("T", "S", "C") else 0.0
        spec_row = json.loads(row["spec"])
        try:
            stats = grid_stats(grid_spec_from_row(row), orders)
        except (KeyError, TypeError, ValueError):
            stats = None
        return {
            "grid_id": gid, "symbol": row["symbol"], "side": row["side"], "status": row["status"], "reason": row["halt_reason"],
            "position_qty": round(net, 8), "spec": spec_row, "needs_user": row["status"] in NEEDS_USER, "loop_running": self.loop_running(gid),
            "recycle": bool(spec_row.get("recycle", False)), "created_at": row["created_at"], "updated_at": row["updated_at"], "stats": stats,
            "orders": [{"client_id": o["client_id"], "role": o["role"], "level": o["level"], "cycle": o["cycle"], "side": o["side"], "type": o["type"],
                        "price": o["price"], "stop_price": o["stop_price"], "qty": o["qty"], "filled": o["filled"], "status": o["status"]}
                       for o in orders if o["status"] not in ("CANCELED", "REJECTED") or o["filled"] > EPS],
            "alerts": [{"level": level, "text": text} for level, text in self.store.alerts(gid)[-12:]],
        }

    def status_payload(self, user_id: str | None = None, *, owner: bool = True, history: int = 6) -> dict[str, Any]:
        """Bu kullanıcının grid'leri: çalışanlar + son bitenler. 'grid' alanı geriye uyum için en yenisidir."""
        if user_id is None:
            rows = self.store.grids(limit=history + 20)
        else:
            rows = self.user_rows(user_id, owner=owner)
        active = [r for r in rows if r["status"] not in FINAL_STATUSES]
        finished = [r for r in rows if r["status"] in FINAL_STATUSES][:history]
        grids = [self.grid_payload(r) for r in active + finished]
        out: dict[str, Any] = {**limits_payload(), "loop_running": any(g["loop_running"] for g in grids), "grids": grids,
                               "active_count": len(active), "grid": grids[0] if grids else None}
        if grids:
            latest = rows[0]["grid_id"]
            out["grid"] = next(g for g in grids if g["grid_id"] == latest) if any(g["grid_id"] == latest for g in grids) else grids[0]
        return out

    def find_row(self, user_id: str | None, grid_id: str | None, *, owner: bool = True, active_only: bool = True) -> Any:
        """İstenen grid (yoksa tek aktif grid). Başkasının grid'i 'bulunamadı' sayılır."""
        if user_id is None:
            rows = self.store.grids(exclude_statuses=FINAL_STATUSES if active_only else None)
        else:
            rows = self.user_rows(user_id, owner=owner, active_only=active_only)
        if grid_id:
            return next((r for r in rows if r["grid_id"] == grid_id), None)
        return rows[0] if len(rows) == 1 else None

    # ---- motor kurma
    def build_engine(self, row: Any, adapter: BinanceLiveGridAdapter, state: dict[str, Any]) -> GridEngine:
        return self._new_engine(grid_spec_from_row(row), adapter, state, user_id=str(row["user_id"] or ""))

    @staticmethod
    def blocked_check(state: dict[str, Any]) -> bool:
        """Canlı yürütme kilitli mi (acil durum, belirsiz emir, hız sınırı)? Testlerde değiştirilebilir."""
        from . import v25_execution as v25
        return bool(v25.live_execution_blocked(state))

    def _new_engine(self, spec: GridSpec, adapter: BinanceLiveGridAdapter, state: dict[str, Any], *, user_id: str = "") -> GridEngine:
        def risk_gate() -> tuple[bool, str]:
            if self.blocked_check(state):
                return False, "canlı yürütme kilitli"
            if state.get("real_trading_locked") is not False:
                return False, "gerçek işlem kilidi kapalı"
            return True, ""

        engine_cls = NeutralGridEngine if spec.side == NEUTRAL else GridEngine
        return engine_cls(adapter, self.store, spec, risk_gate=risk_gate, sleep=time.sleep, user_id=user_id)

    def _record(self, state: dict[str, Any], kind: str, message: str, **extra: Any) -> None:
        from . import v25_execution as v25
        try:
            v25.add_event(state, kind, message, **extra)
            v25.persist_state(state)
        except Exception:  # günlük yazılamasa da grid korunmalı
            logger.warning("LIVE_GRID_EVENT_FAILED kind=%s", kind)

    # ---- yaşam döngüsü
    async def start(self, app: Any, state: dict[str, Any], adapter: BinanceLiveGridAdapter, plan: dict[str, Any], symbol: str, *,
                    user_id: str = "", owner: bool = True) -> dict[str, Any]:
        async with self.lock:
            if self.occupying_row(user_id, symbol, owner=owner) is not None:
                raise LiveGridError(f"{symbol} için zaten bir grid var (çalışıyor ya da müdahale bekliyor). Önce onu durdur.", 409)
            if len(self.occupying_rows(user_id, owner=owner)) >= MAX_GRIDS_PER_USER:
                raise LiveGridError(f"Aynı anda en fazla {MAX_GRIDS_PER_USER} grid çalışabilir. Önce birini durdur.", 409)
            grid_id = "g" + uuid.uuid4().hex[:12]
            engine = self._new_engine(grid_spec_from_plan(grid_id, symbol, plan), adapter, state, user_id=user_id)
            self._engines[grid_id] = engine
            self._paused_for_block.discard(grid_id)
            self._errors[grid_id] = 0
            self._adapters[grid_id] = adapter
            self._record(state, "LIVE_GRID_START", f"{symbol} canlı grid başlatılıyor ({plan['direction']}, {len(plan['levels'])} kademe, "
                         f"marjin {plan['total_margin_usdt']} USDT{', tur açık' if plan.get('recycle') else ''}).", symbol=symbol, grid_id=grid_id)
        async with self.grid_lock(grid_id):
            status = await asyncio.to_thread(engine.start)
        if status == BLOCKED:
            self._record(state, "LIVE_GRID_BLOCKED", f"{symbol} grid başlamadı: {self.store.grid(grid_id)['halt_reason']}", symbol=symbol, grid_id=grid_id)
        else:
            self._ensure_loop(app, state, grid_id)
        return {**self.status_payload(user_id, owner=owner), "started_grid_id": grid_id}

    def _ensure_loop(self, app: Any, state: dict[str, Any], grid_id: str | None = None) -> None:
        gid = grid_id or next(iter(self._engines), None)
        if gid is None or self.loop_running(gid):
            return
        self._tasks[gid] = asyncio.get_running_loop().create_task(self._run(app, state, gid), name=f"live-grid-{gid}")

    async def resume(self, app: Any, state: dict[str, Any], adapter: BinanceLiveGridAdapter, *, user_id: str | None = None,
                     grid_id: str | None = None, owner: bool = True) -> dict[str, Any]:
        """Döngüsü durmuş (ör. sunucu yeniden başladı) grid'leri devam ettirir. grid_id yoksa kullanıcının tüm uygun grid'leri."""
        rows = [self.find_row(user_id, grid_id, owner=owner)] if grid_id else (
            self.occupying_rows(user_id, owner=owner) if user_id is not None else [r for r in [self.occupying_row()] if r is not None])
        rows = [r for r in rows if r is not None]
        if not rows:
            raise LiveGridError("Devam ettirilecek canlı grid yok.", 404)
        resumable = [r for r in rows if r["status"] not in NEEDS_USER]
        if not resumable:
            raise LiveGridError("Grid müdahale bekliyor; önce ACİL DURDUR ile kapat ya da borsayı elle kontrol et.", 409)
        for row in resumable:
            gid = row["grid_id"]
            async with self.grid_lock(gid):
                if self.loop_running(gid):
                    continue
                self._engines[gid] = self.build_engine(row, adapter, state)
                self._adapters[gid] = adapter
                self._paused_for_block.discard(gid)
                self._errors[gid] = 0
                self._ensure_loop(app, state, gid)
        return self.status_payload(user_id, owner=owner)

    async def _run(self, app: Any, state: dict[str, Any], grid_id: str | None = None) -> None:
        gid = grid_id or next(iter(self._engines), "")
        last_status = ""
        while True:
            await asyncio.sleep(TICK_SECONDS)
            engine = self._engines.get(gid)
            if engine is None:
                return
            try:
                async with self.grid_lock(gid):
                    if engine is not self._engines.get(gid):
                        return
                    if gid not in self._paused_for_block and self.blocked_check(state) and engine.status == RUNNING:
                        self._paused_for_block.add(gid)        # kilit/acil durum: yeni giriş yok; pozisyon korunur
                        await asyncio.to_thread(engine.pause)
                        self._record(state, "LIVE_GRID_PAUSED", f"{engine.sym} canlı yürütme kilitlendi; grid duraklatıldı, korumalar sürüyor.", grid_id=gid)
                    status = await asyncio.to_thread(self._tick_once, engine)
                self._errors[gid] = 0
            except Exception as exc:  # ağ/ayrıştırma: sonraki tur tekrar dener; sürekli ise duraklat
                self._errors[gid] = self._errors.get(gid, 0) + 1
                logger.warning("LIVE_GRID_TICK_FAILED grid=%s type=%s count=%s", gid, type(exc).__name__, self._errors[gid])
                if self._errors[gid] >= MAX_LOOP_ERRORS:
                    try:
                        async with self.grid_lock(gid):
                            await asyncio.to_thread(engine.pause)
                    except Exception:
                        pass
                    self._record(state, "LIVE_GRID_LOOP_FAILED", f"{engine.sym} grid döngüsü art arda hata verdi; yeni giriş durduruldu. Borsayı kontrol edin.", grid_id=gid)
                    return
                continue
            if status != last_status:
                last_status = status
                self._record(state, "LIVE_GRID_STATUS", f"{engine.sym} grid durumu: {status}", grid_id=gid, status=status)
            if status in FINAL_STATUSES or status in NEEDS_USER:
                return

    def _tick_once(self, engine: GridEngine) -> str:
        engine.tick()
        status = engine.status
        if status == BLOCKED:               # tutarsızlık: bekleyen girişler iptal edilir, açık pozisyon stop/hedefle kalır
            engine.pause()
            if engine.status == PAUSED:     # kullanıcı görsün: durum BLOCKED kalır, döngü biter, müdahale beklenir
                self.store.set_grid(engine.gid, status=BLOCKED)
            status = engine.status
        if status == PAUSED and self._flat_and_entries_done(engine):
            self.store.set_grid(engine.gid, status=STOPPED, halt_reason="Duraklatıldı; açık pozisyon ve bekleyen giriş yok")
            status = STOPPED
        return status

    @staticmethod
    def _flat_and_entries_done(engine: GridEngine) -> bool:
        entries = engine.store.orders(engine.gid, "E")
        return abs(engine.grid_net()) <= EPS and bool(entries) and all(e["status"] in TERMINAL for e in entries)

    def _engine_for(self, row: Any, adapter: BinanceLiveGridAdapter, state: dict[str, Any]) -> GridEngine:
        engine = self._engines.get(row["grid_id"])
        if engine is None:
            engine = self.build_engine(row, adapter, state)
            self._engines[row["grid_id"]] = engine
        return engine

    async def pause(self, state: dict[str, Any], adapter: BinanceLiveGridAdapter, *, user_id: str | None = None, grid_id: str | None = None,
                    owner: bool = True) -> dict[str, Any]:
        row = self.find_row(user_id, grid_id, owner=owner)
        if row is None:
            raise LiveGridError("Durdurulacak canlı grid yok." if not grid_id else "Bu grid bulunamadı ya da zaten bitti.", 404)
        async with self.grid_lock(row["grid_id"]):
            engine = self._engine_for(row, adapter, state)
            status = await asyncio.to_thread(engine.pause)
            if status == PAUSED and self._flat_and_entries_done(engine):
                self.store.set_grid(engine.gid, status=STOPPED, halt_reason="Kullanıcı durdurdu; açık pozisyon yok")
            self._record(state, "LIVE_GRID_PAUSED", f"{engine.sym} grid durduruldu (bekleyen girişler iptal; açık pozisyon stop/hedefle kalır).", grid_id=engine.gid)
        return self.status_payload(user_id, owner=owner)

    async def emergency(self, app: Any, state: dict[str, Any], adapter: BinanceLiveGridAdapter, *, user_id: str | None = None,
                        grid_id: str | None = None, owner: bool = True) -> dict[str, Any]:
        row = self.find_row(user_id, grid_id, owner=owner)
        if row is None:
            raise LiveGridError("Acil durdurulacak canlı grid yok." if not grid_id else "Bu grid bulunamadı ya da zaten bitti.", 404)
        async with self.grid_lock(row["grid_id"]):
            engine = self._engine_for(row, adapter, state)
            status = await asyncio.to_thread(engine.emergency_stop)
            self._record(state, "LIVE_GRID_EMERGENCY", f"{engine.sym} grid ACİL DURDUR sonucu: {status}", grid_id=engine.gid, status=status)
        return self.status_payload(user_id, owner=owner)


def runner_for(app: Any) -> LiveGridRunner:
    existing = getattr(app.state, "live_grid_runner", None)
    if existing is not None:
        return existing
    from .local_storage import DATA_DIR
    runner = LiveGridRunner(GridStore(str(DATA_DIR / "grid_live.sqlite3"), check_same_thread=False))
    app.state.live_grid_runner = runner
    return runner


# ---------------------------------------------------------------- uç noktaların iş mantığı (v25_execution.py çağırır)
def build_adapter(app: Any, request: Any) -> BinanceLiveGridAdapter:
    from . import v25_execution as v25
    api_key, secret_key = v25.live_credentials_status(request)[:2]
    client = v25.BinanceLiveClient(app.state.http, api_key, secret_key, diagnostic_state=getattr(app.state, "v25_execution", None),
                                   application=app, background_session=False)
    return BinanceLiveGridAdapter(client, asyncio.get_running_loop())


def require_enabled() -> None:
    if not live_enabled():
        raise HTTPException(403, f"Canlı grid kapalı. Sunucuda {ENV_FLAG}=1 ile açılır.")


def http_error(exc: LiveGridError) -> HTTPException:
    return HTTPException(exc.status, str(exc))
