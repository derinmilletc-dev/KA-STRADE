"""DCA planı: taban emir + güvenlik (ek) emirleri + ortalamaya göre hedef + zorunlu stop. SAF hesap (ağ yok).

Plan başlamadan en kötü zararı hesaplar: bütün ek emirler dolar, sonra stop çalışır. Sınırlara (tek işlem zararı, günlük zarar hakkı,
maruziyet, pozisyon sayısı, stop mesafesi, likidasyon tamponu) sığmayan plan reddedilir (DcaPlanError). Grid'in `prepare_live_grid` kuralıyla aynı çizgi.
En kötü zarar: ideal dolum fiyatlarına EK olarak piyasa girişi/stop kayma payı ve funding payı içerir (değerler `risk_config.py`'de, hepsi tahmin/güvenlik payıdır,
kesin değer değildir). Likidasyon fiyatı da TAHMİNDİR: bakım teminatı oranı varsayımdır (`MAINTENANCE_RATE_ESTIMATE`), borsanın gerçek likidasyon fiyatı değildir."""
from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

from ..execution_core import HARD_MAX_MARGIN_USDT
from .risk_config import (FUNDING_ALLOWANCE_PCT, LIQ_BUFFER_PCT, MAINTENANCE_RATE_ESTIMATE, MAKER_FEE, MIN_NET_PROFIT_PCT,  # noqa: F401
                          MIN_NET_PROFIT_USDT, SLIPPAGE_LIMIT_ENTRY_PCT, SLIPPAGE_MARKET_PCT, SLIPPAGE_TP_EXIT_PCT, TAKER_FEE,
                          TRAIL_MIN_LOCKED_PCT, TRAIL_MIN_PCT)

LONG, SHORT = "LONG", "SHORT"
MAX_SAFETY_ORDERS = 5            # grid'deki kademe sınırıyla aynı
MAX_VOLUME_MULTIPLIER = 2.0
MAX_LEVERAGE = 5                 # DCA için sistem tavanından (50x) çok daha düşük
MIN_STEP_PCT, MAX_STEP_PCT = 0.3, 10.0
MIN_TP_PCT, MAX_TP_PCT = 0.2, 20.0
MIN_STOP_GAP_PCT, MAX_STOP_GAP_PCT = 0.3, 10.0
MAX_SPAN_PCT = 20.0              # taban fiyattan stopa en çok bu kadar uzak
MAINTENANCE_RATE = MAINTENANCE_RATE_ESTIMATE   # TAHMİN (risk_config.py): gerçek bakım teminatı tablosu okunmuyor
DEFAULT_POLICY_LEVERAGE = 20
MIN_ORDER_USDT = 5.0


class DcaPlanError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def _dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(repr(float(value)))


def _snap(value: Any, unit: Decimal, mode: str = ROUND_HALF_UP) -> Decimal:
    return (_dec(value) / unit).to_integral_value(rounding=mode) * unit


def _positive(*values: Any) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and 0 < v < float("inf") for v in values)


def limits_payload() -> dict[str, Any]:
    return {"max_safety_orders": MAX_SAFETY_ORDERS, "max_volume_multiplier": MAX_VOLUME_MULTIPLIER, "max_leverage": MAX_LEVERAGE,
            "step_pct": [MIN_STEP_PCT, MAX_STEP_PCT], "take_profit_pct": [MIN_TP_PCT, MAX_TP_PCT], "stop_gap_pct": [MIN_STOP_GAP_PCT, MAX_STOP_GAP_PCT],
            "max_span_pct": MAX_SPAN_PCT, "max_margin_usdt": HARD_MAX_MARGIN_USDT,
            "costs": {"taker_fee": TAKER_FEE, "slippage_market_pct": SLIPPAGE_MARKET_PCT, "slippage_limit_entry_pct": SLIPPAGE_LIMIT_ENTRY_PCT,
                      "slippage_tp_exit_pct": SLIPPAGE_TP_EXIT_PCT, "funding_allowance_pct": FUNDING_ALLOWANCE_PCT,
                      "min_net_profit_usdt": MIN_NET_PROFIT_USDT, "min_net_profit_pct": MIN_NET_PROFIT_PCT,
                      "maintenance_rate_estimate": MAINTENANCE_RATE_ESTIMATE, "liq_buffer_pct": LIQ_BUFFER_PCT}}


def prepare_dca_plan(*, direction: str, price: float, base_margin_usdt: float, safety_orders: int, step_pct: float, volume_multiplier: float,
                     take_profit_pct: float, stop_gap_pct: float, leverage: int, rules: dict[str, Any], policy: dict[str, Any],
                     realized_pnl_today: float = 0.0, existing_exposure: float = 0.0, open_positions: int = 0,
                     reserved_worst_loss: float = 0.0, reserved_pending_notional: float = 0.0, reserved_symbols: int = 0,
                     recycle: bool = False, trailing_pct: float = 0.0) -> dict[str, Any]:
    direction = str(direction).upper()
    if direction not in (LONG, SHORT):
        raise DcaPlanError("Yön LONG veya SHORT olmalı.")
    if not _positive(price, base_margin_usdt, step_pct, volume_multiplier, take_profit_pct, stop_gap_pct, leverage):
        raise DcaPlanError("Fiyat, marjin, aralık, çarpan, hedef, stop ve kaldıraç sıfırdan büyük sayı olmalı.")
    if isinstance(safety_orders, bool) or int(safety_orders) != safety_orders:
        raise DcaPlanError("Ek emir sayısı tam sayı olmalı.")
    count = int(safety_orders)
    if not 1 <= count <= MAX_SAFETY_ORDERS:
        raise DcaPlanError(f"Ek emir sayısı 1 ile {MAX_SAFETY_ORDERS} arasında olmalı.")
    if not 1.0 <= volume_multiplier <= MAX_VOLUME_MULTIPLIER:
        raise DcaPlanError(f"Hacim çarpanı 1 ile {MAX_VOLUME_MULTIPLIER:g} arasında olmalı.")
    if not MIN_STEP_PCT <= step_pct <= MAX_STEP_PCT:
        raise DcaPlanError(f"Ek emirler arası aralık %{MIN_STEP_PCT:g} ile %{MAX_STEP_PCT:g} arasında olmalı.")
    if not MIN_TP_PCT <= take_profit_pct <= MAX_TP_PCT:
        raise DcaPlanError(f"Kâr hedefi %{MIN_TP_PCT:g} ile %{MAX_TP_PCT:g} arasında olmalı.")
    if not MIN_STOP_GAP_PCT <= stop_gap_pct <= MAX_STOP_GAP_PCT:
        raise DcaPlanError(f"Stop mesafesi (son ek emrin ötesi) %{MIN_STOP_GAP_PCT:g} ile %{MAX_STOP_GAP_PCT:g} arasında olmalı.")
    if int(leverage) != leverage:
        raise DcaPlanError("Kaldıraç tam sayı olmalı.")
    trailing_pct = float(trailing_pct or 0.0)
    if trailing_pct:
        if not (trailing_pct == trailing_pct and trailing_pct > 0):
            raise DcaPlanError("Takip mesafesi sıfırdan büyük bir sayı olmalı.")
        if trailing_pct < TRAIL_MIN_PCT - 1e-9:
            raise DcaPlanError(f"Takip mesafesi en az %{TRAIL_MIN_PCT:g} olmalı.")
        if take_profit_pct - trailing_pct < TRAIL_MIN_LOCKED_PCT - 1e-9:
            raise DcaPlanError(f"Takip eden hedefte kâr hedefi, takip mesafesinden en az %{TRAIL_MIN_LOCKED_PCT:g} büyük olmalı "
                               "(aksi halde çekilen stop ücreti karşılamaz).")
    max_lev = min(MAX_LEVERAGE, int(float(policy.get("max_leverage", DEFAULT_POLICY_LEVERAGE))))
    if leverage > max_lev:
        raise DcaPlanError(f"Kaldıraç en fazla {max_lev}x olabilir.")
    try:
        tick, step = _dec(rules["tick"]), _dec(rules["step"])
        min_qty, min_notional = _dec(rules.get("min_qty", 0)), _dec(rules.get("min_notional", 0))
        max_qty = _dec(rules["max_qty"]) if rules.get("max_qty") is not None else None
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise DcaPlanError("Borsa fiyat/miktar kuralları okunamadı; DCA başlatılmadı.", 503) from None
    if not (tick > 0 and step > 0):
        raise DcaPlanError("Borsa fiyat/miktar kuralları geçersiz; DCA başlatılmadı.", 503)

    sign = 1 if direction == LONG else -1            # LONG: fiyat düştükçe ek alım; SHORT: yükseldikçe ek satış
    problems: list[str] = []
    entry_mode = ROUND_FLOOR if direction == LONG else ROUND_CEILING   # ek emir fiyatı başlangıç fiyatına daha uzak yana yuvarlanır
    rows: list[dict[str, Any]] = []
    cum_qty = cum_cost = 0.0
    base_qty = float(_snap(_dec(base_margin_usdt) * _dec(leverage) / _dec(price), step, ROUND_FLOOR))   # Decimal: 0,3x1,5 float'ta 0,44999… çıkıp 0,449'a düşmesin
    for i in range(count + 1):
        entry = float(_snap(price, tick)) if i == 0 else float(_snap(price * (1 - sign * step_pct / 100 * i), tick, entry_mode))
        qty = base_qty if i == 0 else float(_snap(_dec(base_qty) * _dec(volume_multiplier) ** i, step, ROUND_FLOOR))
        cum_qty += qty
        cum_cost += qty * entry
        avg = cum_cost / cum_qty if cum_qty > 0 else entry
        tp = float(_snap(avg * (1 + sign * take_profit_pct / 100), tick, ROUND_CEILING if direction == LONG else ROUND_FLOOR))
        rows.append({"index": i, "kind": "BASE" if i == 0 else "SAFETY", "entry": entry, "qty": qty, "notional": qty * entry,
                     "margin": qty * entry / leverage, "cum_qty": cum_qty, "avg_entry": avg, "tp_price": tp})
    last_entry = rows[-1]["entry"]
    stop_price = float(_snap(last_entry * (1 - sign * stop_gap_pct / 100), tick, ROUND_FLOOR if direction == LONG else ROUND_CEILING))

    if len({r["entry"] for r in rows}) != len(rows):
        problems.append("Yuvarlamadan sonra iki emir aynı fiyata düşüyor; aralığı genişlet.")
    for r in rows:
        label = "Taban emir" if r["index"] == 0 else f"Ek emir {r['index']}"
        if r["qty"] <= 0 or Decimal(repr(r["qty"])) < min_qty or r["notional"] + 1e-9 < max(float(min_notional), MIN_ORDER_USDT):
            problems.append(f"{label} borsanın en düşük emir değerinin altında; marjini artır.")
        if max_qty is not None and Decimal(repr(r["qty"])) > max_qty:
            problems.append(f"{label} borsanın en büyük miktarını aşıyor.")
        beyond = r["entry"] <= stop_price if direction == LONG else r["entry"] >= stop_price
        if beyond:
            problems.append(f"{label} stopun ötesinde.")
    slip_market, slip_limit, slip_tp = SLIPPAGE_MARKET_PCT / 100, SLIPPAGE_LIMIT_ENTRY_PCT / 100, SLIPPAGE_TP_EXIT_PCT / 100
    cum_notional = entry_slippage = 0.0
    for r in rows:
        cum_notional += r["notional"]
        entry_slippage += r["notional"] * (slip_market if r["index"] == 0 else slip_limit)    # taban piyasadan girer, ek emirler limit
        net = (abs(r["tp_price"] - r["avg_entry"]) * r["cum_qty"]) - (r["avg_entry"] + r["tp_price"]) * r["cum_qty"] * TAKER_FEE
        r["tp_net_profit_usdt"] = round(net, 8)                       # yalnız ücret düşülmüş (eski alan, anlamı değişmedi)
        realistic = net - entry_slippage - r["tp_price"] * r["cum_qty"] * slip_tp - cum_notional * FUNDING_ALLOWANCE_PCT / 100
        needed = max(MIN_NET_PROFIT_USDT, cum_notional * MIN_NET_PROFIT_PCT / 100)
        r["tp_net_profit_after_costs_usdt"] = round(realistic, 8)      # ücret + kayma + funding payı düşülmüş (tahmin)
        if net <= 1e-12:
            problems.append("Kâr hedefi işlem ücretini karşılamıyor; hedefi artır.")
            break
        if realistic < needed - 1e-12:
            problems.append(f"Kâr hedefi, ücret + kayma + funding payı düşülünce yeterli net kâr bırakmıyor (en az {needed:.4f} USDT gerekir, "
                            f"kalan {realistic:.4f} USDT); hedefi artır.")
            break

    total_qty = rows[-1]["cum_qty"]
    total_notional = sum(r["notional"] for r in rows)
    total_margin = total_notional / leverage
    avg_full = rows[-1]["avg_entry"]
    # En kötü senaryo: taban + TÜM ek emirler dolar, sonra stop çalışır. Her aşama tam bir kez sayılır:
    # fiyat farkı zararı (raw) + giriş/çıkış komisyonu (fees) + kayma payı (taban piyasa girişi + piyasa stop + limit girişler) + funding payı.
    raw_loss = sum(r["qty"] * sign * (r["entry"] - stop_price) for r in rows)
    fees = total_notional * TAKER_FEE + total_qty * stop_price * TAKER_FEE
    slippage = (sum(r["notional"] * (slip_market if r["index"] == 0 else slip_limit) for r in rows) + total_qty * stop_price * slip_market)
    funding = total_notional * FUNDING_ALLOWANCE_PCT / 100
    worst_loss = raw_loss + fees + slippage + funding
    span_pct = abs(price - stop_price) / price * 100
    stop_distance_pct = abs(last_entry - stop_price) / last_entry * 100
    liq_price = avg_full * (1 - sign / leverage + sign * MAINTENANCE_RATE)    # TAHMİN (sabit bakım teminatı varsayımı), borsa fiyatı değil
    liq_gap_pct = sign * (stop_price - liq_price) / avg_full * 100   # + : stop (tahmini) likidasyondan önce çalışır

    if span_pct > MAX_SPAN_PCT + 1e-9:
        problems.append(f"Taban fiyattan stopa uzaklık %{span_pct:.2f}, sınır %{MAX_SPAN_PCT:g}; aralığı ya da ek emir sayısını azalt.")
    if liq_gap_pct < LIQ_BUFFER_PCT:
        problems.append("Stop, tahmini likidasyona çok yakın ya da ötesinde; kaldıracı düşür, aralığı kısalt ya da stopu yakınlaştır.")
    if total_margin > HARD_MAX_MARGIN_USDT + 1e-9:
        problems.append(f"Gereken toplam marjin {total_margin:.2f} USDT, sınırı {HARD_MAX_MARGIN_USDT:g} USDT.")
    loss_limit = float(policy.get("max_loss_per_trade", 3))
    if worst_loss > loss_limit + 1e-9:
        problems.append(f"En kötü zarar {worst_loss:.2f} USDT, tek işlem sınırı {loss_limit:g} USDT.")
    daily_left = float(policy.get("daily_loss_limit", 10)) + min(0.0, float(realized_pnl_today)) - max(0.0, float(reserved_worst_loss))
    if daily_left <= 0 or worst_loss > daily_left + 1e-9:
        note = f" (çalışan botlar {float(reserved_worst_loss):.2f} USDT ayırdı)" if reserved_worst_loss > 0 else ""
        problems.append(f"En kötü zarar {worst_loss:.2f} USDT, bugün kalan zarar hakkı {max(daily_left, 0):.2f} USDT{note}.")
    exposure_limit = float(policy.get("max_total_exposure_usdt", 350))
    exposure_now = existing_exposure + max(0.0, float(reserved_pending_notional))
    if exposure_now + total_notional > exposure_limit + 1e-9:
        problems.append(f"Toplam maruziyet {exposure_now + total_notional:.2f}, sınır {exposure_limit:g} USDT.")
    if open_positions + max(0, int(reserved_symbols)) >= int(policy.get("max_positions", 5)):
        problems.append("Açık pozisyon / bot sınırı dolu.")
    max_stop_pct = float(policy.get("max_stop_distance_pct", 5))
    if stop_distance_pct > max_stop_pct + 1e-9:
        problems.append(f"Son ek emirden stopa mesafe %{stop_distance_pct:.2f}, sınır %{max_stop_pct:g}.")
    if problems:
        raise DcaPlanError(" ".join(dict.fromkeys(problems)))
    return {"direction": direction, "price": float(price), "leverage": int(leverage), "recycle": bool(recycle), "stages": rows,
            "stop_price": stop_price, "stop_distance_pct": round(stop_distance_pct, 4), "span_pct": round(span_pct, 4),
            "total_margin_usdt": round(total_margin, 6), "total_notional_usdt": round(total_notional, 6),
            "worst_loss_usdt": round(worst_loss, 6), "avg_entry_full": round(avg_full, 8),
            "worst_loss_breakdown": {"price_loss_usdt": round(raw_loss, 6), "fees_usdt": round(fees, 6),
                                     "slippage_allowance_usdt": round(slippage, 6), "funding_allowance_usdt": round(funding, 6)},
            "liquidation_estimate": round(liq_price, 8), "liquidation_is_estimate": True, "liquidation_gap_pct": round(liq_gap_pct, 4),
            "safety_orders": count, "step_pct": float(step_pct), "volume_multiplier": float(volume_multiplier),
            "take_profit_pct": float(take_profit_pct), "stop_gap_pct": float(stop_gap_pct), "trailing_pct": trailing_pct}
