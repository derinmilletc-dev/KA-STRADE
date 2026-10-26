"""Original V2 canlı işlem kayıtları: giriş özeti, TP1 gerçekleşmesi ve çıkış bacakları.

Kurallar (kesin kapsam):
- YALNIZ Original V2 planları için çalışır; çağıranlar `plan_is_original` ile ayırır.
- Hiçbir ağ çağrısı yapmaz; yalnız bellekte zaten bulunan veriyi okur.
- Hiçbir hata dışarı çıkmaz: kayıt başarısızlığı işlem hatasına dönüşmez.
- Planlanan ve gerçekleşen değerler ayrı alanlardadır. Bilinmeyen bilgi tahmin edilmez (None / "UNKNOWN").
- Funding henüz alınmaz: "NOT_COMPUTED" olarak işaretlenir, sıfır yazılmaz.
Kalıcı depo: mevcut sinyal günlüğü dosyası (trades tablosu, işlem kimliği altında). Plan kaydındaki küçük kopya
(plan["original_record"]) kapanışta tekrar yayımlanır; günlükteki yazımlar tekrar çalıştırılınca mükerrer üretmez.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)
SCHEMA = 1
NOT_RECORDED = ["quote_volume_24h", "price_change_pct_24h", "funding_rate", "btc_market_trend"]


def clean(value: Any, depth: int = 0) -> Any:
    """JSON'a güvenli kopya: sonlu olmayan sayılar ve bilinmeyen tipler None/metin olur."""
    if depth > 6:
        return None
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, (float, Decimal)):
        try:
            number = float(value)
        except (ValueError, ArithmeticError, OverflowError):
            return None
        return number if math.isfinite(number) else None
    if isinstance(value, dict):
        return {str(key): clean(item, depth + 1) for key, item in list(value.items())[:60]}
    if isinstance(value, (list, tuple)):
        return [clean(item, depth + 1) for item in list(value)[:60]]
    return str(value)[:120]


def num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return result if math.isfinite(result) else None


def _iso(milliseconds: Any) -> str | None:
    value = num(milliseconds)
    if value is None or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def publish(intent_id: Any, section: str, data: dict[str, Any]) -> None:
    """Sınırlı, bloklamayan kuyruğa ekleme. Kuyruk dolu/devre dışıysa sessizce atlanır."""
    try:
        identifier = str(intent_id or "")
        if not identifier:
            return
        from . import signal_journal
        signal_journal.emit("original_v2", intent_id=identifier, section=section, data=clean(data))
    except Exception:  # noqa: BLE001 - kayıt hiçbir koşulda işlemi etkilemez
        return


def _record(plan: dict[str, Any]) -> dict[str, Any]:
    record = plan.get("original_record")
    if not isinstance(record, dict):
        record = plan["original_record"] = {"schema": SCHEMA}
    return record


def build_entry(strategy_result: Any, spec: dict[str, Any], plan: dict[str, Any], *, spread_bp: Any,
                snapshot: dict[str, Any] | None, policy: dict[str, Any] | None, config: dict[str, Any] | None,
                recorded_at: str) -> dict[str, Any]:
    legacy = getattr(strategy_result, "legacy", None) or {}
    analysis = legacy.get("analysis") or {}
    mtf = legacy.get("mtf") or {}
    ema, bollinger, radar = (analysis.get(key) or {} for key in ("ema", "bollinger", "radar"))
    frames = mtf.get("timeframes") or {}
    price = num(analysis.get("entry"))
    atr = num(analysis.get("atr"))
    upper, middle, lower = (num(bollinger.get(key)) for key in ("upper", "middle", "lower"))
    checks = []
    for check in getattr(getattr(strategy_result, "signal", None), "quality_checks", ()) or ():
        checks.append({"key": getattr(check, "key", None), "passed": getattr(check, "passed", None),
                       "value": getattr(check, "value", None), "threshold": getattr(check, "threshold", None)})
    positions = (snapshot or {}).get("positions") or []
    directions = [str(item.get("direction") or "").upper() for item in positions if isinstance(item, dict)]
    universe = (config or {}).get("universe") if isinstance((config or {}).get("universe"), dict) else {}
    ranked = universe.get("ranked_symbols") if isinstance(universe.get("ranked_symbols"), list) else []
    symbol = str(spec.get("symbol") or plan.get("symbol") or "")
    now = datetime.now(timezone.utc)
    return {
        "recorded_at": recorded_at,
        "signal": {
            "symbol": symbol, "direction": analysis.get("direction"), "confidence": analysis.get("confidence"),
            "trap_score": radar.get("trap_score"), "breakout_quality": radar.get("breakout_quality"),
            "momentum": analysis.get("momentum"), "trend": analysis.get("trend"), "rsi": analysis.get("rsi"),
            "macd_hist": analysis.get("macd"), "adx": analysis.get("adx"), "atr": analysis.get("atr"),
            "atr_pct": atr / price * 100 if atr is not None and price else None,
            "volume_ratio": analysis.get("volume_ratio"),
            "ema20": ema.get("ema20"), "ema50": ema.get("ema50"), "ema200": ema.get("ema200"),
            "bb_upper": upper, "bb_middle": middle, "bb_lower": lower,
            "bb_width": (upper - lower) / middle if None not in (upper, lower, middle) and middle else None,
            "support": analysis.get("support"), "resistance": analysis.get("resistance"),
            "signal_entry": analysis.get("entry"), "signal_stop": analysis.get("stop_loss"),
            "signal_tp1": analysis.get("tp1"), "signal_tp2": analysis.get("tp2"), "signal_tp3": analysis.get("tp3"),
            "signal_timestamp": legacy.get("signal_timestamp"), "decision_time": legacy.get("decision_time"),
        },
        "mtf": {
            "alignment": mtf.get("alignment"), "verdict": mtf.get("verdict"),
            "higher_timeframe_confirmation": mtf.get("higher_timeframe_confirmation"),
            "directions": {key: (frames.get(key) or {}).get("direction") for key in ("15m", "1h", "4h")},
            "confidence": {key: (frames.get(key) or {}).get("confidence") for key in ("15m", "1h", "4h")},
            "weights": {key: (frames.get(key) or {}).get("weight") for key in ("15m", "1h", "4h")},
            "short_filter_v2": mtf.get("short_filter_v2"),
        },
        "quality_checks": checks,
        "planned": {
            "entry_price": spec.get("entry_price"), "initial_stop": plan.get("initial_stop_loss") or spec.get("stop_loss"),
            "targets": spec.get("targets"), "tp1_fraction": plan.get("tp1_fraction") or spec.get("tp1_fraction"),
            "quantity": spec.get("quantity"), "margin_usdt": spec.get("margin_usdt"),
            "notional_usdt": spec.get("notional_usdt"), "leverage": spec.get("leverage"),
            "initial_risk_usdt": plan.get("initial_risk_usdt"), "risk_budget": plan.get("original_risk_budget"),
            "cost_bps": plan.get("original_cost_bps"),
        },
        "market": {
            "spread_bp": spread_bp, "hour_utc": now.hour, "weekday_utc": now.weekday(),
            "universe_rank": ranked.index(symbol) + 1 if symbol in ranked else None,
            "universe_ranked_count": len(ranked) or None, "universe_eligible_count": universe.get("eligible_count"),
        },
        "account": {
            "wallet_balance": (snapshot or {}).get("wallet_balance"),
            "available_balance": (snapshot or {}).get("available_balance"),
            "unrealized_pnl": (snapshot or {}).get("unrealized_pnl"),
            "open_positions": len(positions), "open_long": directions.count("LONG"), "open_short": directions.count("SHORT"),
        },
        "policy": {key: (policy or {}).get(key) for key in (
            "min_confidence", "max_stop_distance_pct", "fee_bps_per_side", "slippage_bps_per_side",
            "max_loss_per_trade", "max_margin_per_trade", "max_positions", "max_same_direction_positions")},
        "not_recorded": NOT_RECORDED,
    }


def attach_entry(plan: dict[str, Any], strategy_result: Any, spec: dict[str, Any], *, spread_bp: Any,
                 snapshot: dict[str, Any] | None, policy: dict[str, Any] | None, config: dict[str, Any] | None,
                 recorded_at: str) -> None:
    """Plan kaydına küçük kopya koyar ve günlüğe yayımlar. Hata olursa plan olduğu gibi kalır."""
    try:
        entry = clean(build_entry(strategy_result, spec, plan, spread_bp=spread_bp, snapshot=snapshot,
                                  policy=policy, config=config, recorded_at=recorded_at))
        _record(plan).setdefault("entry", entry)
        publish(plan.get("intent_id"), "entry", plan["original_record"]["entry"])
    except Exception:  # noqa: BLE001
        logger.warning("ORIGINAL_RECORD_ENTRY_FAILED", exc_info=False)


def build_tp1(plan: dict[str, Any], order: dict[str, Any], native: dict[str, Any], allocation: Any,
              recorded_at: str) -> dict[str, Any]:
    return {
        "recorded_at": recorded_at,
        "planned": {"fraction": plan.get("tp1_fraction"), "quantity": allocation,
                    "initial_quantity": plan.get("initial_quantity"),
                    "target_price": (plan.get("targets") or [None])[0] if isinstance(plan.get("targets"), list) else None},
        "actual": {"executed_qty": order.get("executedQty"), "avg_price": order.get("avgPrice"),
                   "status": order.get("status"), "order_id": order.get("orderId"),
                   "update_time_ms": order.get("updateTime"), "update_time": _iso(order.get("updateTime")),
                   "algo_id": native.get("algoId"), "client_algo_id": native.get("clientAlgoId"),
                   "actual_order_id": native.get("actualOrderId")},
    }


def attach_tp1(plan: dict[str, Any], order: dict[str, Any], native: dict[str, Any], allocation: Any,
               recorded_at: str) -> None:
    try:
        tp1 = clean(build_tp1(plan, order, native, allocation, recorded_at))
        _record(plan).setdefault("tp1", tp1)
        publish(plan.get("intent_id"), "tp1", plan["original_record"]["tp1"])
    except Exception:  # noqa: BLE001
        logger.warning("ORIGINAL_RECORD_TP1_FAILED", exc_info=False)


def _role_map(plan: dict[str, Any], algo_history: list[dict[str, Any]], client_id_for: Any) -> dict[int, str]:
    """Çıkış emri kimliği -> rol. Yalnız borsa kayıtlarındaki istemci kimliğinden; eşleşmeyen UNKNOWN kalır."""
    intent = str(plan.get("intent_id") or "")
    named: dict[str, str] = {}
    if plan.get("stop_client_id"):
        named[str(plan["stop_client_id"])] = "STOP"
    named[client_id_for("TP12", intent)] = "TP1"
    named[client_id_for("TP3", intent)] = "TP3"
    for value in (plan.get("smart_guard") or {}).get("client_ids") or []:
        named[str(value)] = "SMART_STOP"
    for value in plan.get("stop_bridge_client_ids") or []:
        named[str(value)] = "STOP_BRIDGE"
    roles: dict[int, str] = {}
    for item in algo_history:
        role = named.get(str(item.get("clientAlgoId") or ""))
        actual = str(item.get("actualOrderId") or "")
        if role and actual.isdigit():
            roles[int(actual)] = role
    tp1 = ((plan.get("original_record") or {}).get("tp1") or {}).get("actual") or {}
    if str(tp1.get("order_id") or "").isdigit() and int(tp1["order_id"]) not in roles:
        roles[int(tp1["order_id"])] = "TP1"
    return roles


def build_legs(plan: dict[str, Any], entry_rows: list[dict[str, Any]], close_rows: list[dict[str, Any]],
               selected_rows: list[dict[str, Any]], algo_history: list[dict[str, Any]], client_id_for: Any) -> dict[str, Any]:
    roles = _role_map(plan, algo_history, client_id_for)
    symbol = str(plan.get("symbol") or "")
    legs: dict[str, Any] = {}

    def add(kind: str, row: dict[str, Any]) -> None:
        fill_id = str(row.get("id") or "")
        order_id = row.get("orderId")
        key = f"{symbol}:{fill_id}" if fill_id else f"{symbol}:o{order_id}:{row.get('time')}:{row.get('qty')}:{row.get('price')}"
        order_number = int(order_id) if str(order_id or "").isdigit() else None
        legs.setdefault(key, {
            "kind": kind, "fill_id": fill_id or None, "order_id": order_id,
            "time_ms": row.get("time"), "time": _iso(row.get("time")),
            "price": row.get("price"), "qty": row.get("qty"), "quote_qty": row.get("quoteQty"),
            "realized_pnl": row.get("realizedPnl"), "commission": row.get("commission"),
            "commission_asset": row.get("commissionAsset"), "maker": row.get("maker"),
            "role": "ENTRY" if kind == "ENTRY" else roles.get(order_number or -1, "UNKNOWN"),
        })

    for row in entry_rows:
        add("ENTRY", row)
    for row in close_rows:
        add("EXIT", row)
    non_usdt = sorted({str(row.get("commissionAsset") or "").upper() for row in selected_rows
                       if row.get("commissionAsset") and str(row.get("commissionAsset")).upper() != "USDT"})
    usdt_fee = sum(num(row.get("commission")) or 0.0 for row in selected_rows
                   if str(row.get("commissionAsset") or "").upper() == "USDT")
    gross_values = [num(row.get("realizedPnl")) for row in close_rows]
    gross = sum(gross_values) if gross_values and all(value is not None for value in gross_values) else None
    commission_complete = bool(selected_rows) and not non_usdt
    return {"legs": legs, "summary": {
        "plan_close_reason": plan.get("close_reason") or "UNKNOWN",
        "close_reason_note": "Plan alanından aynen alındı; tahmin edilmedi.",
        "entry_qty": sum(num(row.get("qty")) or 0.0 for row in entry_rows),
        "exit_qty": sum(num(row.get("qty")) or 0.0 for row in close_rows),
        "gross_realized_pnl": gross, "commission_usdt": usdt_fee,
        "commission_complete": commission_complete, "non_usdt_commission_assets": non_usdt,
        "funding": {"status": "NOT_COMPUTED", "usdt": None},
        "net_excl_funding_usdt": gross - usdt_fee if gross is not None and commission_complete else None,
        "leg_count": len(legs),
    }}


def publish_close(plan: dict[str, Any], entry_rows: list[dict[str, Any]], close_rows: list[dict[str, Any]],
                  selected_rows: list[dict[str, Any]], algo_history: list[dict[str, Any]], client_id_for: Any) -> None:
    """Kapanış doğrulanınca: giriş özeti ve TP1 kopyasını tekrar yayımlar (eksik kalmışsa tamamlar), bacakları yazar."""
    try:
        record = plan.get("original_record") if isinstance(plan.get("original_record"), dict) else {}
        for section in ("entry", "tp1"):
            if isinstance(record.get(section), dict):
                publish(plan.get("intent_id"), section, record[section])
        publish(plan.get("intent_id"), "legs",
                build_legs(plan, entry_rows, close_rows, selected_rows, algo_history, client_id_for))
    except Exception:  # noqa: BLE001
        logger.warning("ORIGINAL_RECORD_CLOSE_FAILED", exc_info=False)
