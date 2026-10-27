"""Original V2 canlı giriş modeli: trend + EMA20 geri çekilmesi + yeniden güçlenme ("pullback_v1").

Giriş kararı ve bu modele özgü STOP SEVİYESİ. Risk, pozisyon büyüklüğü, kaldıraç, TP oranları ve emir yürütme bu modülde YOKTUR:
* Stop geri çekilmenin ucuna bağlanır: LONG son 6 mumun en düşüğünün, SHORT en yükseğinin 0,15 ATR ötesi; fiyata en az 1 ATR uzak.
  Hedefler aynı R oranlarıyla (1R/2R/3R) `analysis.entry_levels` ile hesaplanır; eski `analyze` aynı fonksiyonu varsayılan
  1,5 ATR alt sınırıyla kullanır (eski sistemin sayıları değişmez).
* Karar mevcut `StrategyResult` biçiminde döner; sonrasındaki tüm canlı kapılar (stop %6, minimum tutar, marjin,
  pozisyon/yön sınırları, spread, günlük kilitler, sahiplik/arm) aynen çalışır.
* Model ortam değişkeniyle seçilir; varsayılan "legacy" (eski sistem). Geçersiz değer girişi reddeder (fail-closed).

Kurallar (d = +1 LONG, -1 SHORT; t = son kapanmış 15 dakikalık mum; değerler başlangıç değerleridir, doğrulanmamıştır):
  K1 Yön      : 1h ve 4h yönü d (mevcut `shared_mtf_decision`, güven eşiği yok)
  K2 Yapı     : d*(EMA20-EMA50) >= 0,15*ATR
  K4 Geri çek.: son 6 mumda (t dahil) fiyat EMA20 bölgesine değdi: LONG low <= EMA20+0,25*ATR, SHORT high >= EMA20-0,25*ATR
  K5 Tetik    : t mumunda (1) kapanış EMA20'nin trend tarafında, (2) kapanış önceki mumun ucunu geçti,
                (3) kapanış mumun doğru %40'lık ucunda, (4) MACD histogramı trend yönünde döndü
  K6 Uzama    : d*(kapanış-EMA20) <= 1,25*ATR
  K7 Yer      : önceki 80 mumun (t HARİÇ) karşı ucuna en az 0,5*ATR; kapanış bu uçu geçmişse reddedilir
Emir öncesi: canlı fiyat sinyal kapanışından işlem aleyhine 0,4*ATR'den fazla uzaksa giriş yapılmaz (karar katmanı).
"""

from __future__ import annotations

import hmac
import math
import os
import secrets
from copy import deepcopy
from typing import Any

from ..analysis import analyze, atr as atr_value_of, ema, entry_levels
from . import original_v2_demo as demo
from .contracts import QualityCheck, StrategyInput, StrategyProvenance, StrategyResult, StrategySignal

MODEL_ENV = "PROTREBOT_ORIGINAL_V2_ENTRY_MODEL"
MODEL_LEGACY = "legacy"
MODEL_PULLBACK = "pullback_v1"
MODEL_VERSION = "pullback-v1"

# Başlangıç değerleri (doğrulanmamış). Tek yerde tutulur ve her kayda yazılır.
PARAMETERS = {
    "structure_min_gap_atr": 0.15,
    "pullback_window": 6,
    "pullback_touch_atr": 0.25,
    "close_position": 0.6,
    "extension_max_atr": 1.25,
    "room_min_atr": 0.5,
    "room_window": 80,
    "preferred_extension_min_atr": 0.25,
    "preferred_extension_max_atr": 1.0,
    "fill_deviation_max_atr": 0.4,
    "stop_min_atr": 1.0,  # stop fiyata en az bu kadar ATR uzak (eski sistemde 1,5)
}
CHECK_KEYS = ("pb_trend", "pb_structure", "pb_pullback", "pb_trigger", "pb_extension", "pb_room")
INTERVAL_SECONDS = {"15m": 900, "1h": 3600, "4h": 14400}
MIN_CLOSED = {"15m": 220, "1h": 50, "4h": 50}

# Süreç içi işaret: yalnız bu modülün ürettiği sonuçlar taşır; dışarıdan gelen hiçbir veriyle taklit edilemez.
_TOKEN = secrets.token_hex(16)


def configured_model() -> str:
    value = os.getenv(MODEL_ENV, MODEL_LEGACY).strip().lower()
    if value in {"", MODEL_LEGACY}:
        return MODEL_LEGACY
    if value == MODEL_PULLBACK:
        return MODEL_PULLBACK
    demo._fail("INVALID_ENTRY_MODEL")


def pullback_enabled() -> bool:
    return configured_model() == MODEL_PULLBACK


def is_pullback_result(result: Any) -> bool:
    """Sonuç, bu modülün girişe uygun bulduğu bir karar mı? (Güven/tuzak karşılaştırmasını atlamanın tek koşulu.)"""
    marker = (getattr(result, "legacy", None) or {}).get("entry_model")
    signal = getattr(result, "signal", None)
    if not isinstance(marker, dict) or signal is None or not signal.strategy_eligible:
        return False
    token = marker.get("token")
    return (
        marker.get("name") == MODEL_PULLBACK and isinstance(token, str)
        and hmac.compare_digest(token, _TOKEN)
    )


def gate_signal(result: Any) -> dict[str, Any]:
    """`evaluate_entry_gates` için sinyal. Yalnız işaretli sonuçta eski puan karşılaştırmaları (güven, tuzak) nötrleşir;
    diğer tüm kapılar (arm, sembol, yön izni, spread, pozisyon, yinelenen, maruziyet, günlük kilitler, yön sınırları) aynen çalışır."""
    analysis = deepcopy(result.legacy["analysis"])
    if is_pullback_result(result):
        analysis["confidence"] = 100
        analysis["radar"] = {**(analysis.get("radar") or {}), "trap_score": 0}
    return analysis


def rank_key(result: Any, opportunity_score: float) -> tuple[int, float, float]:
    """Sıralama: makul uzaklıkta (0,25–1,0 ATR) yeniden güçlenenler önde; sıfıra yakınlık tek başına kalite değildir.
    Aynı grupta hacim oranı, sonra mevcut fırsat puanı."""
    analysis = result.legacy["analysis"]
    metrics = analysis.get("pullback") or {}
    extension = metrics.get("extension_atr")
    low, high = PARAMETERS["preferred_extension_min_atr"], PARAMETERS["preferred_extension_max_atr"]
    preferred = int(isinstance(extension, (int, float)) and low <= extension <= high)
    return (preferred, float(analysis.get("volume_ratio") or 0.0), float(opportunity_score))


def fill_deviation_check(result: Any, live_price: Any) -> tuple[bool, dict[str, Any]]:
    """Emir öncesi karar katmanı kontrolü: canlı fiyat sinyal kapanışından işlem aleyhine çok uzaklaştı mı? Emir koduna dokunmaz."""
    analysis = result.legacy["analysis"]
    direction = 1 if analysis.get("direction") == "LONG" else -1 if analysis.get("direction") == "SHORT" else 0
    try:
        price, signal_price, atr = float(live_price), float(analysis["entry"]), float(analysis["atr"])
    except (KeyError, TypeError, ValueError):
        return False, {"reason": "FILL_DEVIATION_DATA_UNAVAILABLE"}
    if direction == 0 or not all(math.isfinite(value) for value in (price, signal_price, atr)) or atr <= 0 or price <= 0:
        return False, {"reason": "FILL_DEVIATION_DATA_UNAVAILABLE"}
    adverse_atr = direction * (price - signal_price) / atr
    limit = PARAMETERS["fill_deviation_max_atr"]
    passed = adverse_atr <= limit
    return passed, {"adverse_atr": adverse_atr, "limit_atr": limit, "signal_close": signal_price, "live_price": price,
                    "reason": None if passed else "PRICE_MOVED_FROM_SIGNAL"}


def _closed_windows(request: StrategyInput) -> tuple[dict[str, list[dict[str, float]]] | None, dict[str, int], str | None, list[str]]:
    closed: dict[str, list[dict[str, float]]] = {}
    latest: dict[str, int] = {}
    for interval in demo.INTERVALS:
        rows = request.candles_by_timeframe.get(interval, [])
        if not isinstance(rows, list):
            return None, latest, "INVALID_CANDLE_DATA", [f"{interval}: invalid candles"]
        valid: list[dict[str, float]] = []
        for row in rows:
            try:
                item = {key: float(row[key]) for key in ("open", "high", "low", "close", "volume")}
                timestamp = int(row["time"])
            except (KeyError, TypeError, ValueError, OverflowError):
                return None, latest, "INVALID_CANDLE_DATA", [f"{interval}: invalid OHLCV"]
            if not all(math.isfinite(value) for value in item.values()) or item["volume"] < 0 or item["high"] < item["low"] \
                    or item["high"] < max(item["open"], item["close"]) or item["low"] > min(item["open"], item["close"]):
                return None, latest, "INVALID_CANDLE_DATA", [f"{interval}: invalid OHLCV"]
            if timestamp + INTERVAL_SECONDS[interval] > request.decision_time:
                continue
            if valid and timestamp <= valid[-1]["time"]:
                return None, latest, "NON_CHRONOLOGICAL_CANDLES", [f"{interval}: timestamps not strictly increasing"]
            valid.append({"time": timestamp, **item})
        if len(valid) < MIN_CLOSED[interval]:
            return None, latest, "INSUFFICIENT_CLOSED_CANDLES", [f"{interval}: {len(valid)}/{MIN_CLOSED[interval]} closed candles"]
        closed[interval] = valid
        latest[interval] = int(valid[-1]["time"])
    return closed, latest, None, []


def evaluate_rules(closed_15m: list[dict[str, float]], direction_1h: str, direction_4h: str,
                   *, allow_either_timeframe: bool = False) -> dict[str, Any]:
    """Saf kural hesabı. Yön, K2 yapısından gelir (EMA20 > EMA50 ise LONG, < ise SHORT); belirsizse direction=None."""
    closes = [c["close"] for c in closed_15m]
    highs = [c["high"] for c in closed_15m]
    lows = [c["low"] for c in closed_15m]
    volumes = [c["volume"] for c in closed_15m]
    e20, e50 = ema(closes, 20), ema(closes, 50)
    atr = atr_value_of(highs, lows, closes)
    metrics: dict[str, Any] = {"atr": atr, "direction": None}
    checks: dict[str, bool] = {key: False for key in CHECK_KEYS}
    if not math.isfinite(atr) or atr <= 0:
        return {"direction": None, "checks": checks, "metrics": metrics, "eligible": False, "reason": "ATR_UNAVAILABLE"}
    price = closes[-1]
    gap_atr = (e20[-1] - e50[-1]) / atr
    minimum_gap = PARAMETERS["structure_min_gap_atr"]
    d = 1 if gap_atr >= minimum_gap else -1 if gap_atr <= -minimum_gap else 0
    metrics["ema_gap_atr"] = gap_atr
    if d == 0:
        return {"direction": None, "checks": checks, "metrics": metrics, "eligible": False, "reason": "PB_STRUCTURE"}
    direction = "LONG" if d > 0 else "SHORT"
    metrics["direction"] = direction
    checks["pb_structure"] = True

    # K1: 1h ve 4h yönü (mevcut MTF kapısı; güven eşiği yok, SHORT için eski alignment filtresi kapalı).
    from ..main import shared_mtf_decision
    mtf = shared_mtf_decision(
        symbol="", entry_direction=direction, confidence_15m=0.0,
        timeframe_results={"1h": {"direction": direction_1h, "confidence": 0.0}, "4h": {"direction": direction_4h, "confidence": 0.0}},
        short_filter=False, allow_either_timeframe=allow_either_timeframe,
    )
    checks["pb_trend"] = bool(mtf["entry_permission"])
    metrics["direction_1h"], metrics["direction_4h"] = direction_1h, direction_4h

    # K4: son 6 mumda (t dahil) EMA20 bölgesine geri çekilme.
    window, touch = PARAMETERS["pullback_window"], PARAMETERS["pullback_touch_atr"] * atr
    start = len(closes) - window
    if d > 0:
        distances = [(lows[i] - e20[i]) / atr for i in range(start, len(closes))]
        checks["pb_pullback"] = any(lows[i] <= e20[i] + touch for i in range(start, len(closes)))
    else:
        distances = [(e20[i] - highs[i]) / atr for i in range(start, len(closes))]
        checks["pb_pullback"] = any(highs[i] >= e20[i] - touch for i in range(start, len(closes)))
    metrics["pullback_depth_atr"] = min(distances)

    # K5: yeniden başlama mumu.
    fast, slow = ema(closes, 12), ema(closes, 26)
    macd_line = [a - b for a, b in zip(fast, slow)]
    signal_line = ema(macd_line, 9)
    hist_now, hist_before = macd_line[-1] - signal_line[-1], macd_line[-2] - signal_line[-2]
    candle_range = highs[-1] - lows[-1]
    close_position = (price - lows[-1]) / candle_range if candle_range > 0 else None
    side_ok = d * (price - e20[-1]) > 0
    broke_previous = price > highs[-2] if d > 0 else price < lows[-2]
    limit = PARAMETERS["close_position"]
    position_ok = close_position is not None and (close_position >= limit if d > 0 else close_position <= 1 - limit)
    momentum_turn = d * (hist_now - hist_before) > 0
    checks["pb_trigger"] = bool(side_ok and broke_previous and position_ok and momentum_turn)
    metrics.update(close_position=close_position, hist=hist_now, hist_change=hist_now - hist_before,
                   side_ok=side_ok, broke_previous=broke_previous, position_ok=position_ok, momentum_turn=momentum_turn)

    # K6: aşırı EMA20 uzaması (iki yönde de).
    extension_atr = d * (price - e20[-1]) / atr
    checks["pb_extension"] = extension_atr <= PARAMETERS["extension_max_atr"]
    metrics["extension_atr"] = extension_atr

    # K7: önceki 80 mumun karşı ucuna yer; sinyal mumu (t) pencereye dahil DEĞİL.
    span = PARAMETERS["room_window"]
    prior_highs, prior_lows = highs[-span - 1:-1], lows[-span - 1:-1]
    if len(prior_highs) < span:
        room_atr = None
    else:
        room_atr = (max(prior_highs) - price) / atr if d > 0 else (price - min(prior_lows)) / atr
    checks["pb_room"] = room_atr is not None and room_atr >= PARAMETERS["room_min_atr"]
    metrics["room_atr"] = room_atr
    metrics["volume_ratio_20"] = volumes[-1] / (sum(volumes[-21:-1]) / 20) if len(volumes) > 21 and sum(volumes[-21:-1]) > 0 else None

    eligible = all(checks.values())
    reason = "READY" if eligible else next(key.upper() for key in CHECK_KEYS if not checks[key])
    return {"direction": direction, "checks": checks, "metrics": metrics, "eligible": eligible, "reason": reason}


def evaluate_live_pullback(request: StrategyInput, *, allowed_symbols: list[str], enabled: bool | None = None) -> StrategyResult:
    """Canlı Original V2 için yeni giriş kararı. Geçerlilik, sembol evreni ve profil denetimleri eski canlı yolla aynıdır."""
    demo._require_enabled(enabled)
    profile = demo.fixed_profile()
    if (
        not isinstance(allowed_symbols, list) or not 1 <= len(allowed_symbols) <= 100
        or any(not isinstance(symbol, str) for symbol in allowed_symbols)
        or len(set(allowed_symbols)) != len(allowed_symbols)
        or any(not symbol.isascii() or not symbol.isalnum() or not symbol.isupper() or not symbol.endswith("USDT") for symbol in allowed_symbols)
    ):
        demo._fail("INVALID_LIVE_SYMBOL_UNIVERSE")
    if request.symbol not in allowed_symbols:
        demo._fail("SYMBOL_OUTSIDE_LIVE_UNIVERSE")
    if request.historical_policy_override is not None:
        demo._fail("CALLER_POLICY_OVERRIDE_DENIED")
    if request.short_filter_rules is not None:
        demo._fail("CALLER_SHORT_FILTER_OVERRIDE_DENIED")
    for interval in demo.INTERVALS:
        rows = request.candles_by_timeframe.get(interval, [])
        if isinstance(rows, list) and len(rows) > profile["closed_window"]:
            demo._fail("CLOSED_WINDOW_EXCEEDED")

    provenance = StrategyProvenance(strategy_id=demo.STRATEGY_ID, strategy_version=MODEL_VERSION, parameter_hash=demo.DOCUMENT_SHA256)
    base_signal = dict(
        symbol=request.symbol, decision_time=request.decision_time, market_type="BINANCE_DEMO_MODEL_ONLY",
        exit_policy_id=profile["model"]["exit_policy_id"], provenance=provenance, invalidation=None,
    )
    closed, latest, invalid_reason, invalid_details = _closed_windows(request)
    if closed is None:
        legacy = {"decision": "WAIT", "symbol": request.symbol, "signal_timestamp": None, "decision_time": request.decision_time,
                  "entry_eligible": False, "reason": invalid_reason, "reasons": invalid_details}
        signal = StrategySignal(
            decision="WAIT", strategy_eligible=False, signal_open_time=None, latest_closed_timestamps=latest,
            direction=None, entry=None, stop=None, tp1=None, tp2=None, tp3=None, atr=None,
            reason=str(invalid_reason), reasons=tuple(invalid_details), quality_checks=(), diagnostics={}, overlay=None, **base_signal,
        )
        return StrategyResult(signal=signal, legacy=legacy)

    from ..execution_core import configured_mtf_allow_either_timeframe
    candles = closed["15m"]
    analysis = analyze(candles)  # tüm gösterge alanları (bilgi amaçlı); yön/stop/hedef aşağıda bu modelin yönüne göre yeniden kurulur
    directions = {interval: analyze(closed[interval])["direction"] for interval in ("1h", "4h")}
    decision = evaluate_rules(candles, directions["1h"], directions["4h"], allow_either_timeframe=bool(configured_mtf_allow_either_timeframe()))
    direction = decision["direction"]
    eligible = bool(decision["eligible"])
    legacy_direction = analysis["direction"]
    if direction in {"LONG", "SHORT"}:
        # Stop: geri çekilmenin ucu (son `pullback_window` mum, sinyal mumu dahil) ± 0,15 ATR, en az `stop_min_atr` ATR uzak.
        recent = candles[-PARAMETERS["pullback_window"]:]
        pullback_low, pullback_high = min(c["low"] for c in recent), max(c["high"] for c in recent)
        stop, _risk, targets = entry_levels(direction, analysis["entry"], analysis["atr"], pullback_low, pullback_high,
                                            atr_multiple=PARAMETERS["stop_min_atr"])
        analysis.update(direction=direction, stop_loss=stop, tp1=targets[0], tp2=targets[1], tp3=targets[2])
        decision["metrics"]["stop_model"] = "pullback_extreme"
        decision["metrics"]["pullback_extreme"] = pullback_low if direction == "LONG" else pullback_high
    analysis["entry_model"] = MODEL_VERSION
    analysis["legacy_direction"] = legacy_direction
    analysis["pullback"] = {**decision["metrics"], "checks": dict(decision["checks"]), "parameters": dict(PARAMETERS)}
    mtf = {
        "direction": direction or "BEKLE", "entry_permission": bool(decision["checks"]["pb_trend"]), "verdict": MODEL_VERSION,
        "alignment": None, "higher_timeframe_confirmation": bool(decision["checks"]["pb_trend"]),
        "timeframes": {"15m": {"direction": direction or "BEKLE"}, "1h": {"direction": directions["1h"]}, "4h": {"direction": directions["4h"]}},
    }
    checks = tuple(QualityCheck(key, bool(decision["checks"][key]), decision["metrics"].get({
        "pb_trend": "direction_1h", "pb_structure": "ema_gap_atr", "pb_pullback": "pullback_depth_atr",
        "pb_trigger": "close_position", "pb_extension": "extension_atr", "pb_room": "room_atr"}[key]), PARAMETERS_FOR[key])
        for key in CHECK_KEYS)
    reason = decision["reason"]
    legacy = {
        "decision": "BUY" if eligible and direction == "LONG" else "SELL" if eligible and direction == "SHORT" else "WAIT",
        "symbol": request.symbol, "signal_timestamp": int(candles[-1]["time"]), "decision_time": request.decision_time,
        "latest_closed_timestamps": latest, "analysis": analysis, "regime": analysis.get("trend"), "mtf": mtf,
        "entry_eligible": eligible, "reason": reason, "reasons": [] if eligible else [reason], "risk": None, "gate": None,
    }
    if eligible:
        legacy["entry_model"] = {"name": MODEL_PULLBACK, "token": _TOKEN}
    signal = StrategySignal(
        decision=legacy["decision"], strategy_eligible=eligible, signal_open_time=legacy["signal_timestamp"],
        latest_closed_timestamps=latest, direction=analysis.get("direction"), entry=analysis.get("entry"),
        stop=analysis.get("stop_loss"), tp1=analysis.get("tp1"), tp2=analysis.get("tp2"), tp3=analysis.get("tp3"),
        atr=analysis.get("atr"), reason=reason, reasons=tuple(legacy["reasons"]), quality_checks=checks,
        diagnostics={"analysis": deepcopy(analysis), "mtf": deepcopy(mtf), "regime": analysis.get("trend")},
        overlay=deepcopy(analysis.get("series")), **base_signal,
    )
    return StrategyResult(signal=signal, legacy=legacy)


PARAMETERS_FOR = {
    "pb_trend": "1h+4h = yön",
    "pb_structure": PARAMETERS["structure_min_gap_atr"],
    "pb_pullback": PARAMETERS["pullback_touch_atr"],
    "pb_trigger": PARAMETERS["close_position"],
    "pb_extension": PARAMETERS["extension_max_atr"],
    "pb_room": PARAMETERS["room_min_atr"],
}
