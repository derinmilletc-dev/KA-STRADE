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

import copy
import logging
import math
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)
SCHEMA = 1
OBSERVATION_VERSION = 1
KILL_SWITCH_ENV = "PROTREBOT_ORIGINAL_OBSERVATION_ENABLED"
BREAKER_LIMIT = 5
CONTEXT_STORE_LIMIT = 64
PUBLISH_EVERY_SECONDS = 300
CHECKPOINT_MINUTES = (15, 30, 60, 120)
R_THRESHOLDS = (("first_profit_s", 0.0), ("first_r025_s", 0.25), ("first_r05_s", 0.5), ("first_r10_s", 1.0))
# Kapanış yalnız plan durumu bunlardan biri olunca (doğrulanmış kapanış) kabul edilir; ACİL DURDURULDU ek olarak pozisyonun da görünmemesini ister.
TERMINAL_STATUSES = {"KAPANDI", "İPTAL", "CLOSED", "CANCELLED"}
SOFT_TERMINAL_STATUSES = {"ACİL DURDURULDU"}
SOFT_MISSING_SNAPSHOTS = 2
BOOT_ID = uuid.uuid4().hex[:12]  # süreç kimliği: önceki süreçte "kuyruğa alındı" denen kayıt bu süreçte doğrulanmış sayılmaz
STALE_QUEUED_SECONDS = 60.0
TIME_FIELDS = ("fill_time_s", "first_profit_s", "first_r025_s", "first_r05_s", "first_r10_s", "tp1_touch_s",
               "mfe.t_s", "mae.t_s", "checkpoints.*.sample_age_s")
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


def publish(intent_id: Any, section: str, data: dict[str, Any]) -> bool | None:
    """Sınırlı, bloklamayan kuyruğa ekleme. True: alındı, False: atıldı (gözlem katmanı yeniden dener), None: kapalı/kimliksiz."""
    try:
        identifier = str(intent_id or "")
        if not identifier:
            return None
        from . import signal_journal
        return signal_journal.emit("original_v2", intent_id=identifier, section=section, data=clean(data))
    except Exception:  # noqa: BLE001 - kayıt hiçbir koşulda işlemi etkilemez
        return False


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
                 recorded_at: str, order_result: Any = None) -> None:
    """Plan kaydına küçük kopya koyar ve günlüğe yayımlar. Hata olursa plan olduğu gibi kalır."""
    try:
        entry = clean(build_entry(strategy_result, spec, plan, spread_bp=spread_bp, snapshot=snapshot,
                                  policy=policy, config=config, recorded_at=recorded_at))
        _record(plan).setdefault("entry", entry)
        publish(plan.get("intent_id"), "entry", plan["original_record"]["entry"])
    except Exception:  # noqa: BLE001
        logger.warning("ORIGINAL_RECORD_ENTRY_FAILED", exc_info=False)
    attach_observation(plan, strategy_result, order_result)


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
    _guarded("publish_close_exact", _publish_exact, plan, entry_rows)


def _apply_exact_timing(record: dict[str, Any], exact: dict[str, Any]) -> None:
    """Kapanışta gerçek dolum zamanı gelince: tahmini zamana dayanan süre ölçümlerini mümkün olanlar için yeniden hesaplar.
    Olay zamanları (ilk kâr, 0,25R/0,5R/1R, TP1, MFE/MAE) mutlak anlardır, bu yüzden kesin kaydırılabilir. Kontrol noktaları
    'dolumdan N dk sonraki ilk örnek' olarak TAHMİNİ zamana göre seçilmişti; yaşları kaydırılır ama seçim yaklaşık kalır.
    Tahmini değerler `timing_estimated` altında korunur (kesin ve tahmini veri ayrılabilsin)."""
    exact_time = num(exact.get("first_time_s"))
    fill = record.get("fill") if isinstance(record.get("fill"), dict) else None
    if exact_time is None or fill is None:
        return
    fill.setdefault("time_exact_s", exact_time)
    context = record.get("context") if isinstance(record.get("context"), dict) else {}
    decision = num(fill.get("decision_time") if fill.get("decision_time") is not None else context.get("decision_time"))
    if decision is not None:
        fill.setdefault("latency_exact_s", exact_time - decision)
    path = record.get("path")
    if not isinstance(path, dict) or not isinstance(path.get("time_basis"), dict) or num(path.get("fill_time_s")) is None:
        return
    basis = path["time_basis"]
    if basis.get("recomputed_from_exact") or basis.get("source") in ("order_result", "exact_legs"):
        if basis.get("source") == "order_result":
            basis.setdefault("exact_check_s", num(path["fill_time_s"]) - exact_time)  # çapraz kontrol: fark (sn)
        return
    delta = num(path["fill_time_s"]) - exact_time  # + : gerçek dolum tahminden önceydi
    if delta < -1.0:
        basis["exact_rejected"] = "exact_after_estimate"  # tutarsız veri: tahmin açıkça tahmin olarak kalır
        return
    delta = max(delta, 0.0)
    path["timing_estimated"] = {
        "fill_time_s": path["fill_time_s"], "partial": path.get("partial"), "partial_known": path.get("partial_known"),
        **{key: path.get(key) for key in ("first_profit_s", "first_r025_s", "first_r05_s", "first_r10_s", "tp1_touch_s")},
        "mfe_t_s": (path.get("mfe") or {}).get("t_s"), "mae_t_s": (path.get("mae") or {}).get("t_s"),
        "checkpoint_ages_s": {key: (value or {}).get("sample_age_s") for key, value in (path.get("checkpoints") or {}).items()},
    }
    for key in ("first_profit_s", "first_r025_s", "first_r05_s", "first_r10_s", "tp1_touch_s"):
        if num(path.get(key)) is not None:
            path[key] = path[key] + delta
    for key in ("mfe", "mae"):
        if isinstance(path.get(key), dict) and num(path[key].get("t_s")) is not None:
            path[key]["t_s"] += delta
    for value in (path.get("checkpoints") or {}).values():
        if isinstance(value, dict) and num(value.get("sample_age_s")) is not None:
            value["sample_age_s"] += delta
    path["fill_time_s"] = exact_time
    first = num(path.get("first_sample_s"))
    if first is not None:
        path["partial"], path["partial_known"] = (first - exact_time) > 60, True
    basis.update(source="exact_legs", estimated=False, recomputed_from_exact=True, delta_s=delta,
                 note="Olay zamanları gerçek dolum zamanına göre yeniden hesaplandı; kontrol noktaları yaklaşık "
                      "(seçim tahmini dolum zamanına göre yapılmıştı).")
    path["time_sources"] = {key: "exact_legs" for key in TIME_FIELDS}


def _publish_exact(plan: dict[str, Any], entry_rows: list[dict[str, Any]]) -> None:
    """Kapanışta: kesin dolumu ekler, süre ölçümlerini yeniden hesaplar ve context + fill + son fiyat izini makbuzla yayımlar.
    Günlük tarafı idempotent olduğu için tekrar yayım mükerrer kayıt üretmez."""
    record = plan.get("original_record") if isinstance(plan.get("original_record"), dict) else {}
    if not isinstance(record.get("context"), dict):
        return
    now = time.time()
    exact = exact_fill_from_rows(entry_rows)
    if exact is not None:
        record.setdefault("fill", {}).setdefault("exact", clean(exact))
        _apply_exact_timing(record, exact)
        record.setdefault("journal_sent", {})["fill"] = {"state": "dirty"}
    _sync_journal(plan, now=now)
    if isinstance(record.get("path"), dict):
        if exact is not None:
            record["path"]["journal"] = {"state": "dirty"}
        if record["path"].get("ended_reason") or exact is not None:
            _publish_path(plan, record["path"], None)



# ---------------------------------------------------------------------------
# Giriş kalitesi gözlem katmanı (yalnız ölçüm; emir/strateji akışına dokunmaz)
# Kurallar: eşzamansız çağrı yok, ağ yok, dosya yok; yalnız plan["original_record"] içine yazar.
# ---------------------------------------------------------------------------
_CONTEXTS: dict[tuple[str, int], dict[str, Any]] = {}
_BREAKER: dict[str, Any] = {"errors": 0, "open": False}
OBSERVATION_FUNCTIONS = (
    "observation_enabled", "_guarded", "_ema_series", "_run_age", "compute_context", "note_candidate",
    "recall_context", "_slip", "build_fill", "publish_tracked", "_needs_publish", "_sync_journal", "attach_observation",
    "_new_path", "update_path", "_publish_path", "_end_path", "_time_basis", "_complete_fill", "_retry_pending",
    "observe_positions", "exact_fill_from_rows", "_apply_exact_timing", "_publish_exact",
)


def observation_enabled() -> bool:
    if str(os.environ.get(KILL_SWITCH_ENV, "1")).strip().lower() in {"0", "false", "off", "no"}:
        return False
    return not _BREAKER["open"]


def reset_breaker() -> None:
    _BREAKER.update(errors=0, open=False)


def _guarded(name: str, func: Any, *args: Any, **kwargs: Any) -> Any:
    """Kapalıysa hiçbir şey yapmaz. Hata yutulur; üst üste BREAKER_LIMIT hatada kayıt kendini kapatır."""
    if not observation_enabled():
        return None
    try:
        result = func(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - gözlem hiçbir koşulda işlemi etkilemez
        _BREAKER["errors"] += 1
        if _BREAKER["errors"] >= BREAKER_LIMIT and not _BREAKER["open"]:
            _BREAKER["open"] = True
            logger.warning("ORIGINAL_OBSERVATION_DISABLED after=%d last=%s", _BREAKER["errors"], name)
        elif _BREAKER["errors"] <= 2:
            logger.warning("ORIGINAL_OBSERVATION_FAILED stage=%s error=%s", name, type(exc).__name__)
        return None
    _BREAKER["errors"] = 0
    return result


def _ema_series(values: list[float], period: int) -> list[float]:
    """analysis.ema ile aynı: pencere başından tohumlanır."""
    if not values:
        return []
    alpha = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(value * alpha + result[-1] * (1 - alpha))
    return result


def _run_age(flags: list[bool]) -> dict[str, Any]:
    age = 0
    for flag in reversed(flags):
        if not flag:
            break
        age += 1
    # EMA'lar pencere başından tohumlandığı için ilk mum hep eşittir; yaş ilk mumdan sonrasına dayanıyorsa "en az bu kadar".
    capped = bool(flags) and (age == len(flags) or (len(flags) > 1 and age == len(flags) - 1 and not flags[0]))
    return {"candles": age, "minutes": age * 15, "capped": capped}


def compute_context(candles: list[dict[str, Any]], direction: str, *, atr: Any, signal_entry: Any,
                    signal_stop: Any, signal_timestamp: Any = None, decision_time: Any = None) -> dict[str, Any] | None:
    """Karar anı bağlamı: kapanmış 15 dk mumlardan, işlem yönünde + olacak şekilde. Saf hesap."""
    sign = 1 if direction == "LONG" else -1 if direction == "SHORT" else 0
    rows = [row for row in candles or [] if isinstance(row, dict)][-259:]
    if not sign or len(rows) < 2:
        return None
    closes = [num(row.get("close")) for row in rows]
    highs = [num(row.get("high")) for row in rows]
    lows = [num(row.get("low")) for row in rows]
    if any(value is None for value in closes + highs + lows):
        return None
    e20, e50, e200 = (_ema_series(closes, period) for period in (20, 50, 200))
    price = closes[-1]
    atr_value = num(atr)
    entry, stop = num(signal_entry), num(signal_stop)
    risk = abs(entry - stop) if entry is not None and stop is not None else None
    atr_ok = atr_value is not None and atr_value > 0
    risk_ok = risk is not None and risk > 0

    def distance(ema_value: float) -> dict[str, Any]:
        gap = sign * (price - ema_value)
        return {"atr": gap / atr_value if atr_ok else None, "pct": gap / price * 100 if price else None}

    ages = {
        "price_ema20": _run_age([sign * (c - a) > 0 for c, a in zip(closes, e20)]),
        "ema20_ema50": _run_age([sign * (a - b) > 0 for a, b in zip(e20, e50)]),
        "ema50_ema200": _run_age([sign * (b - c) > 0 for b, c in zip(e50, e200)]),
        "all_aligned": _run_age([sign * (c - a) > 0 and sign * (a - b) > 0 and sign * (b - d) > 0
                                 for c, a, b, d in zip(closes, e20, e50, e200)]),
    }
    moves: dict[str, Any] = {}
    for hours, count in ((8, 32), (12, 48), (24, 96)):
        if len(closes) > count and closes[-1 - count]:
            previous = closes[-1 - count]
            move = sign * (price - previous)
            moves[f"{hours}h"] = {"pct": move / previous * 100, "atr": move / atr_value if atr_ok else None,
                                  "r": move / risk if risk_ok else None}
        else:
            moves[f"{hours}h"] = None
    lookback = min(80, len(rows))
    low, high = min(lows[-lookback:]), max(highs[-lookback:])
    span = high - low
    position = ((price - low) if sign == 1 else (high - price)) / span if span > 0 else None
    extreme_gap = (high - price) if sign == 1 else (price - low)
    return {
        "version": OBSERVATION_VERSION, "direction": direction, "window_candles": len(rows),
        "signal_timestamp": signal_timestamp, "decision_time": decision_time,
        "ema_distance": {"ema20": distance(e20[-1]), "ema50": distance(e50[-1]), "ema200": distance(e200[-1])},
        "alignment_age": ages, "prior_move": moves,
        "range_20h": {"position": position, "extreme_distance_atr": extreme_gap / atr_value if atr_ok else None,
                      "candles": lookback},
        "risk_basis": {"signal_entry": entry, "signal_stop": stop, "r": risk},
    }


def note_candidate(symbol: Any, result: Any, candles: Any) -> None:
    """_candidates içinden: uygun aday için bağlamı hesaplayıp bellek deposuna koyar."""
    def work() -> None:
        legacy = getattr(result, "legacy", None) or {}
        analysis = legacy.get("analysis") or {}
        decision_time = legacy.get("decision_time")
        context = compute_context(
            list(candles or []), str(analysis.get("direction") or ""), atr=analysis.get("atr"),
            signal_entry=analysis.get("entry"), signal_stop=analysis.get("stop_loss"),
            signal_timestamp=legacy.get("signal_timestamp"), decision_time=decision_time)
        if context is None or not isinstance(decision_time, int):
            return
        _CONTEXTS[(str(symbol), decision_time)] = clean(context)
        while len(_CONTEXTS) > CONTEXT_STORE_LIMIT:
            _CONTEXTS.pop(next(iter(_CONTEXTS)))
    _guarded("note_candidate", work)


def recall_context(symbol: Any, decision_time: Any) -> dict[str, Any] | None:
    return _CONTEXTS.get((str(symbol), decision_time)) if isinstance(decision_time, int) else None


def _slip(sign: int, average: float | None, reference: Any, risk: Any) -> dict[str, Any]:
    """Giriş kayması (+ = giriş kötüleşti). Gerçek ortalama fiyat yoksa bilinmiyor (None)."""
    reference, risk = num(reference), num(risk)
    if average is None or not reference:
        return {"bp": None, "r": None}
    adverse = sign * (average - reference)
    return {"bp": adverse / reference * 1e4, "r": adverse / risk if risk and risk > 0 else None}


def build_fill(plan: dict[str, Any], order: Any, context: dict[str, Any], signal_entry: Any,
               decision_time: Any) -> dict[str, Any]:
    """Emir RESULT cevabından gerçek dolum. Eksik bilgi boş bırakılır; sonradan snapshot ile tamamlanır."""
    order = order if isinstance(order, dict) else {}
    sign = 1 if context.get("direction") == "LONG" else -1
    average = num(order.get("avgPrice"))
    average = average if average and average > 0 else None
    update_ms = num(order.get("updateTime"))
    fill_time = update_ms / 1000 if update_ms and update_ms > 0 else None
    risk = (context.get("risk_basis") or {}).get("r")
    decision = num(decision_time)
    return {
        "avg_price": average, "price_source": "order_result" if average else None,
        "executed_qty": num(order.get("executedQty")), "planned_qty": num(plan.get("quantity")),
        "status": order.get("status"), "time_s": fill_time, "time_source": "order_result" if fill_time else None,
        "time_estimated": False if fill_time is not None else None,
        "slippage_vs_signal": _slip(sign, average, signal_entry, risk),
        "slippage_vs_planned": _slip(sign, average, plan.get("entry_price"), risk),
        "latency_s": fill_time - decision if fill_time is not None and decision is not None else None,
        "latency_source": "order_result" if fill_time is not None else None,
        "latency_estimated": False if fill_time is not None else None,
        "decision_time": decision,
    }


def attach_observation(plan: dict[str, Any], strategy_result: Any, order_result: Any) -> None:
    def work() -> None:
        legacy = getattr(strategy_result, "legacy", None) or {}
        analysis = legacy.get("analysis") or {}
        decision_time = legacy.get("decision_time")
        context = recall_context(plan.get("symbol"), decision_time)
        if context is None:
            return
        record = _record(plan)
        record.setdefault("context", copy.deepcopy(context))  # bağımsız kopya: bellek deposu ve plan paylaşmaz
        record.setdefault("fill", clean(build_fill(plan, order_result, context, analysis.get("entry"), decision_time)))
        _sync_journal(plan)
    _guarded("attach_observation", work)


def publish_tracked(intent_id: Any, section: str, data: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """Makbuzlu yayım. Dönen durum YALNIZ kuyruğa kabulü gösterir ('queued'); 'persisted' ancak yazıcı iş parçacığı
    SQLite'a yazıp geri okuyarak doğruladığında (signal_journal.receipt_state) verilir."""
    moment = time.time() if now is None else now
    state = {"state": "dropped", "token": None, "boot": BOOT_ID, "at": moment}
    try:
        identifier = str(intent_id or "")
        if not identifier:
            state["state"] = "disabled"
            return state
        from . import signal_journal
        token = signal_journal.new_receipt()
        state["token"] = token
        accepted = signal_journal.emit("original_v2", intent_id=identifier, section=section, data=clean(data), receipt=token)
        if accepted is True:
            state["state"] = "queued"
        elif accepted is None:
            state["state"] = "disabled"  # günlük kapalı: yeniden denenmez
        else:
            signal_journal.set_receipt(token, "dropped")
    except Exception:  # noqa: BLE001
        state["state"] = "dropped"
    return state


def _needs_publish(state: Any, now: float) -> bool:
    """Önceki yayımın durumuna bakar: doğrulanmışsa/devre dışıysa False; kayıp, başarısız, bayat ya da başka süreçten kalmışsa True."""
    if not isinstance(state, dict):
        return True
    current = state.get("state")
    if current in ("persisted", "disabled"):
        return False
    if current in ("queued", "retrying"):
        if state.get("boot") != BOOT_ID:
            return True
        try:
            from . import signal_journal
            actual = signal_journal.receipt_state(state.get("token"))
        except Exception:  # noqa: BLE001
            return True
        if actual == "persisted":
            state["state"] = "persisted"
            return False
        if actual in ("queued", "retrying"):
            state["state"] = actual
            return now - (num(state.get("at")) or 0.0) > STALE_QUEUED_SECONDS
        return True  # failed / abandoned / dropped / unknown
    return True  # dirty, dropped, failed


def _sync_journal(plan: dict[str, Any], force: bool = False, now: float | None = None) -> None:
    """context ve fill'i günlüğe yayımlar; her biri için durum plana yazılır. Kayıp/başarısız/bayat olanlar yeniden yayımlanır.
    Günlük tarafı idempotent: context ilk yazan kalır, fill yalnız boş alanları tamamlar -> tekrar yayım mükerrer üretmez."""
    record = plan.get("original_record")
    if not isinstance(record, dict):
        return
    moment = time.time() if now is None else now
    sent = record.get("journal_sent")
    if not isinstance(sent, dict):
        sent = record["journal_sent"] = {}
    for section in ("context", "fill"):
        data = record.get(section)
        if isinstance(data, dict) and (force or _needs_publish(sent.get(section), moment)):
            sent[section] = publish_tracked(plan.get("intent_id"), section, data, moment)


def _time_basis(source: Any, price_source: Any = None) -> dict[str, Any]:
    """Süre ölçümlerinin dayandığı dolum zamanı kaynağı. 'order_result' = borsa emir cevabı; 'exact_legs' = kapanışta gerçek
    dolum kayıtları; diğer her kaynak (ör. 'first_snapshot') TAHMİNİDİR."""
    estimated = source not in ("order_result", "exact_legs")
    return {"source": source or "unknown", "estimated": estimated, "price_source": price_source,
            "recomputed_from_exact": False, "applies_to": list(TIME_FIELDS),
            "note": "Tahmini: gerçek dolum zamanı bilinmiyor." if estimated else None}


def _new_path(plan: dict[str, Any], fill_price: float, risk: float, fill_time: float, now: float,
              time_source: Any = None, price_source: Any = None) -> dict[str, Any]:
    return {
        "version": OBSERVATION_VERSION, "fill_price": fill_price, "r": risk,
        "fill_time_s": fill_time, "time_basis": _time_basis(time_source, price_source),
        "samples": 0, "first_sample_s": None, "last_sample_s": None,
        "max_gap_s": 0.0, "mfe": None, "mae": None, "checkpoints": {}, "tp1_touch_s": None,
        "first_profit_s": None, "first_r025_s": None, "first_r05_s": None, "first_r10_s": None,
        "missing_snapshots": 0, "ended_reason": None, "partial": None, "partial_known": False,
        "last_published_s": None, "journal": None,
        "time_sources": {key: (time_source or "unknown") for key in TIME_FIELDS},
    }


def update_path(path: dict[str, Any], sign: int, mark: float, tp1: float | None, now: float) -> bool:
    """Tek örnek ekler. Dönüş: journal'a hemen yayımlanmalı mı (yeni kontrol noktası / eşik)."""
    risk = path["r"]
    elapsed = max(0.0, now - path["fill_time_s"])
    r_now = sign * (mark - path["fill_price"]) / risk
    urgent = False
    if path["samples"] == 0:
        path["first_sample_s"] = now
        if path.get("time_basis", {}).get("estimated", True):
            path["partial"], path["partial_known"] = None, False  # gerçek dolum zamanı bilinmiyor: kesin sonuç verilmez
        else:
            path["partial"], path["partial_known"] = elapsed > 60, True
    else:
        path["max_gap_s"] = max(path["max_gap_s"], now - path["last_sample_s"])
    path["samples"] += 1
    path["last_sample_s"] = now
    if path["mfe"] is None or r_now > path["mfe"]["r"]:
        path["mfe"] = {"r": r_now, "price": mark, "t_s": elapsed}
    if path["mae"] is None or r_now < path["mae"]["r"]:
        path["mae"] = {"r": r_now, "price": mark, "t_s": elapsed}
    for key, level in R_THRESHOLDS:
        reached = r_now > level if level == 0.0 else r_now >= level
        if path[key] is None and reached:
            path[key] = elapsed
            urgent = True
    if path["tp1_touch_s"] is None and tp1 is not None and sign * (mark - tp1) >= 0:
        path["tp1_touch_s"] = elapsed
        urgent = True
    for minutes in CHECKPOINT_MINUTES:
        label = str(minutes)
        if label not in path["checkpoints"] and elapsed >= minutes * 60:
            path["checkpoints"][label] = {"price": mark, "r": r_now, "sample_age_s": elapsed}
            urgent = True
    return urgent


def _publish_path(plan: dict[str, Any], path: dict[str, Any], moment: float | None = None) -> None:
    """Fiyat izini makbuzla yayımlar; context/fill durumu da denetlenir (kayıp bir yayım kendiliğinden onarılır)."""
    now = time.time() if moment is None else moment
    body = {key: value for key, value in path.items() if key != "journal"}
    path["journal"] = publish_tracked(plan.get("intent_id"), "path", body, now)
    if moment is not None and path["journal"]["state"] in ("queued", "disabled"):
        path["last_published_s"] = moment
    _sync_journal(plan, now=now)


def _end_path(plan: dict[str, Any], path: dict[str, Any], reason: str, moment: float | None = None) -> None:
    if path.get("ended_reason") is None:
        path["ended_reason"] = reason
        _publish_path(plan, path, moment)


def _complete_fill(plan: dict[str, Any], record: dict[str, Any], position: dict[str, Any], moment: float) -> bool:
    """Eksik dolum alanlarını snapshot'taki GERÇEK pozisyon verisiyle tamamlar; dolu alan asla ezilmez. Değişti mi?
    Zaman snapshot'tan geliyorsa TAHMİNİDİR ve öyle işaretlenir (time/latency *_source ve *_estimated)."""
    fill = record.setdefault("fill", {})
    changed = False
    price = num(fill.get("avg_price"))
    if not price or price <= 0:
        actual = num(position.get("entry_price"))
        if actual and actual > 0:
            fill["avg_price"], fill["price_source"] = actual, "snapshot"
            changed = True
    if num(fill.get("time_s")) is None:
        fill["time_s"], fill["time_source"], fill["time_estimated"] = moment, "first_snapshot", True
        changed = True
    context = record.get("context") if isinstance(record.get("context"), dict) else {}
    basis = context.get("risk_basis") or {}
    sign = 1 if context.get("direction") == "LONG" else -1
    average = num(fill.get("avg_price"))
    for key, reference in (("slippage_vs_signal", basis.get("signal_entry")), ("slippage_vs_planned", plan.get("entry_price"))):
        current = fill.get(key) if isinstance(fill.get(key), dict) else {}
        if current.get("bp") is None and average:
            computed = _slip(sign, average, reference, basis.get("r"))
            if computed["bp"] is not None:
                fill[key] = computed
                changed = True
    decision = num(fill.get("decision_time") if fill.get("decision_time") is not None else context.get("decision_time"))
    if fill.get("latency_s") is None and decision is not None and num(fill.get("time_s")) is not None:
        fill["latency_s"] = num(fill["time_s"]) - decision
        fill["latency_source"] = fill.get("time_source") or "unknown"
        fill["latency_estimated"] = fill.get("time_source") not in ("order_result", "exact_legs")
        changed = True
    if changed:
        record.setdefault("journal_sent", {})["fill"] = {"state": "dirty"}
    return changed


def _retry_pending(plan: dict[str, Any], record: dict[str, Any], path: Any, now: float) -> None:
    if isinstance(path, dict) and isinstance(path.get("journal"), dict) and _needs_publish(path["journal"], now):
        _publish_path(plan, path, now)
    else:
        _sync_journal(plan, now=now)


def observe_positions(state: dict[str, Any], snapshot: dict[str, Any] | None, now: float | None = None) -> None:
    """reconcile sonrası: snapshot'taki işaret fiyatından, bağlam taşıyan planların fiyat izini günceller.

    İz yalnız plan DOĞRULANMIŞ kapanışa (KAPANDI/İPTAL...) geçince biter. Pozisyonun snapshot'ta görünmemesi tek başına
    kapanış sayılmaz (snapshot hatası olabilir): örnekleme durur, pozisyon dönerse sürer. Geçersiz snapshot hiç sayılmaz."""
    if not observation_enabled():
        return
    moment = time.time() if now is None else now

    def work() -> None:
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("positions"), list):
            return  # snapshot alınamadı/bozuk: bu tur yok sayılır, "kayıp pozisyon" sayılmaz
        positions = [row for row in snapshot["positions"] if isinstance(row, dict)]
        for plan in list((state.get("plans") or {}).values()):
            if not isinstance(plan, dict):
                continue
            record = plan.get("original_record")
            if not isinstance(record, dict) or not isinstance(record.get("context"), dict):
                continue  # bağlamsız (eski) planlar atlanır: yanıltıcı kısmi iz yazılmaz
            path = record.get("path")
            if isinstance(path, dict) and path.get("ended_reason"):
                _retry_pending(plan, record, path, moment)
                continue
            status = str(plan.get("status") or "").upper()
            symbol, direction = str(plan.get("symbol") or ""), str(plan.get("direction") or "").upper()
            position = next((row for row in positions if str(row.get("symbol")) == symbol
                             and str(row.get("direction") or "").upper() == direction), None)
            if status in TERMINAL_STATUSES:
                if not isinstance(path, dict):
                    path = record["path"] = {"version": OBSERVATION_VERSION, "samples": 0, "journal": None,
                                             "time_basis": _time_basis(None), "partial": None, "partial_known": False}
                _end_path(plan, path, "plan_closed" if path.get("samples") else "plan_closed_no_samples", moment)
                continue
            if position is None:
                if isinstance(path, dict):
                    path["missing_snapshots"] = int(path.get("missing_snapshots") or 0) + 1
                    if status in SOFT_TERMINAL_STATUSES and path["missing_snapshots"] >= SOFT_MISSING_SNAPSHOTS:
                        _end_path(plan, path, "plan_closed", moment)
                _retry_pending(plan, record, path, moment)
                continue
            _complete_fill(plan, record, position, moment)
            fill = record["fill"]
            mark = num(position.get("mark_price"))
            price = num(fill.get("avg_price"))
            stop = num(plan.get("initial_stop_loss") or plan.get("stop_loss"))
            if not mark or mark <= 0 or not price or price <= 0 or stop is None or abs(price - stop) <= 0:
                _retry_pending(plan, record, path, moment)
                continue
            if not isinstance(path, dict):
                path = record["path"] = _new_path(plan, price, abs(price - stop), float(fill["time_s"]), moment,
                                                  fill.get("time_source"), fill.get("price_source"))
            path["missing_snapshots"] = 0
            targets = plan.get("targets")
            tp1 = num(targets[0]) if isinstance(targets, list) and targets else None
            sign = 1 if direction == "LONG" else -1
            urgent = update_path(path, sign, mark, tp1, moment)
            last = path.get("last_published_s")
            retry = isinstance(path.get("journal"), dict) and _needs_publish(path["journal"], moment)
            if urgent or last is None or retry or moment - last >= PUBLISH_EVERY_SECONDS:
                _publish_path(plan, path, moment)
            else:
                _sync_journal(plan, now=moment)
    _guarded("observe_positions", work)


def exact_fill_from_rows(entry_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Kapanışta giriş bacaklarından kesin dolum: VWAP, ilk/son zaman, toplam miktar."""
    quantity = value = 0.0
    times: list[float] = []
    for row in entry_rows or []:
        qty, price, when = num(row.get("qty")), num(row.get("price")), num(row.get("time"))
        if not qty or qty <= 0 or not price or price <= 0:
            continue
        quantity += qty
        value += qty * price
        if when:
            times.append(when / 1000)
    if quantity <= 0:
        return None
    return {"vwap": value / quantity, "qty": quantity, "first_time_s": min(times) if times else None,
            "last_time_s": max(times) if times else None, "legs": len(entry_rows)}
