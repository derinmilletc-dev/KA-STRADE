"""Akıllı çıkış GÖLGE MODU: yalnız kayıt tutar, borsaya HİÇBİR emir göndermez.

Her açık Original V2 pozisyonu için "yeni çıkış kuralları olsaydı ne yapardık?" sorusunu cevaplar ve
plan kaydına (`plan["exit_shadow"]`) yazar. Gerçek stop/TP/akıllı koruma davranışı değişmez.

Aşamalar (stop yalnız sıkılaşır, asla genişlemez):
  START      → giriş
  WATCH      → fiyat +0,5R gitti (izleme)
  BREAKEVEN  → TP1 alındı: önerilen stop = giriş + masraf
  TRAIL      → TP1 alındı ve fiyat +1,5R'ı gördü: önerilen stop = en iyi fiyatın 1 ATR arkası (yalnız kâr yönünde kayar)
Sanal çıkışlar (ilk gerçekleşen kaydedilir, sonra gölge donar):
  SHADOW_STOP → fiyat önerilen stopa değdi
  STRUCTURE   → (yalnız yeni giriş modeli) işlemi açtıran yapı 2 kapanmış mumda üst üste bozuldu ve net kârdayız
  TIME        → pozisyon uzun süredir açık, hedefe gitmedi ve net kârdayız

Bayrak: PROTREBOT_EXIT_SHADOW=1 (varsayılan kapalı). Rakamlar doğrulanmamış başlangıç değerleridir."""
from __future__ import annotations

import logging
import math
import os
import time
from typing import Any

from .analysis import atr as atr_of, ema

logger = logging.getLogger(__name__)

ENV = "PROTREBOT_EXIT_SHADOW"
SHADOW_KEY = "exit_shadow"
VERSION = "exit-shadow-v1"
CANDLE_SECONDS = 900
CANDLE_LIMIT = 120
MAX_EVENTS = 30
PULLBACK_MODEL = "pullback-v1"

PARAMETERS: dict[str, float] = {
    "watch_r": 0.5,           # bu kadar R kâr görülünce izleme aşaması
    "trail_start_r": 1.5,     # TP1 sonrası bu kadar R görülünce iz süren stop başlar
    "trail_atr": 1.0,         # iz süren stop en iyi fiyatın bu kadar ATR arkasında
    "tp1_quantity_ratio": 0.9,  # kalan miktar başlangıcın bu oranının altına inince TP1 alınmış sayılır
    "structure_candles": 2,   # yapı bozulması kaç kapanmış mum üst üste görülmeli
    "time_limit_hours": 6.0,  # bu süreden sonra hedefe gitmeyen kârdaki pozisyon için sanal çıkış
    "time_limit_max_r": 1.0,  # süre çıkışı yalnız en iyi kâr bu R'ın altında kaldıysa
}


def enabled() -> bool:
    return str(os.environ.get(ENV, "")).strip().lower() in {"1", "true", "on", "yes"}


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def macd_hist(closes: list[float]) -> list[float]:
    fast, slow = ema(closes, 12), ema(closes, 26)
    line = [a - b for a, b in zip(fast, slow)]
    signal = ema(line, 9)
    return [a - b for a, b in zip(line, signal)]


def structure_broken(candles: list[dict[str, float]], direction: str, count: int) -> tuple[bool, str | None]:
    """Son `count` kapanmış mumun HER BİRİNDE işlemi açtıran yapıdan biri bozuk mu? (EMA20'nin ters tarafında kapanış,
    EMA20/EMA50 sırası ters, MACD histogramı ters). Veri yetersizse bozuk SAYILMAZ."""
    d = 1 if direction == "LONG" else -1
    if len(candles) < 60 or count < 1:
        return False, None
    closes = [c["close"] for c in candles]
    e20, e50, hist = ema(closes, 20), ema(closes, 50), macd_hist(closes)
    reasons: list[str] = []
    for index in range(len(candles) - count, len(candles)):
        if d * (closes[index] - e20[index]) < 0:
            reasons.append("ema20")
        elif d * (e20[index] - e50[index]) < 0:
            reasons.append("ema_yapi")
        elif d * hist[index] < 0:
            reasons.append("macd")
        else:
            return False, None
    return True, reasons[-1]


def new_shadow(now: float, model: str) -> dict[str, Any]:
    return {"version": VERSION, "model": model, "started_at": now, "phase": "START", "proposed_stop": None,
            "max_r": 0.0, "best_price": None, "atr": None, "last_candle": None, "virtual_exit": None,
            "events": [], "parameters": dict(PARAMETERS), "orders_sent": 0}


def _event(shadow: dict[str, Any], now: float, kind: str, **info: Any) -> None:
    shadow["events"] = (shadow.get("events") or [])[-(MAX_EVENTS - 1):] + [{"at": now, "kind": kind, **info}]


def evaluate(shadow: dict[str, Any], *, direction: str, entry: float, initial_stop: float, mark: float,
             quantity: float, initial_quantity: float | None, cost_bps: float, now: float,
             candles: list[dict[str, float]] | None = None) -> dict[str, Any]:
    """Saf fonksiyon: gölge durumunu günceller ve döndürür. Emir göndermez, dış istek yapmaz."""
    if shadow.get("virtual_exit"):
        return shadow  # sanal çıkış kaydedildi: gölge donar
    d = 1 if direction == "LONG" else -1
    risk = abs(entry - initial_stop)
    if direction not in {"LONG", "SHORT"} or risk <= 0 or entry <= 0 or mark <= 0:
        shadow["last_error"] = "veri_gecersiz"
        return shadow
    shadow["last_error"] = None
    r_now = d * (mark - entry) / risk
    best = shadow.get("best_price")
    shadow["best_price"] = mark if best is None else (max(best, mark) if d == 1 else min(best, mark))
    if candles:
        last = candles[-1]
        if int(last.get("time") or 0) >= float(shadow.get("started_at") or 0):  # girişten önceki mumun ucu sayılmaz
            extreme = last["high"] if d == 1 else last["low"]
            shadow["best_price"] = max(shadow["best_price"], extreme) if d == 1 else min(shadow["best_price"], extreme)
        if len(candles) >= 15:
            shadow["atr"] = atr_of([c["high"] for c in candles], [c["low"] for c in candles], [c["close"] for c in candles])
    shadow["max_r"] = max(float(shadow.get("max_r") or 0.0), d * (shadow["best_price"] - entry) / risk)
    shadow["r_now"] = r_now
    cost = entry * cost_bps / 10000
    net_positive = d * (mark - entry) - cost > 0
    tp1_done = initial_quantity is not None and initial_quantity > 0 and quantity < initial_quantity * PARAMETERS["tp1_quantity_ratio"]

    def propose(level: float, phase: str) -> None:
        current = shadow.get("proposed_stop")
        floor = initial_stop if current is None else current
        if d * (level - floor) > 0 and d * (mark - level) > 0:  # yalnız sıkılaşır ve fiyatın doğru tarafında kalır
            shadow["proposed_stop"] = level
        if shadow.get("phase") != phase:
            shadow["phase"] = phase
            _event(shadow, now, "PHASE", phase=phase, proposed_stop=shadow.get("proposed_stop"), r=round(r_now, 3))

    # 1) Önce mevcut önerilen stop değdi mi? (sanal stop)
    proposed = shadow.get("proposed_stop")
    if proposed is not None and d * (mark - proposed) <= 0:
        _virtual_exit(shadow, now, "SHADOW_STOP", price=proposed, entry=entry, risk=risk, d=d)
        return shadow
    # 2) Aşamalar
    if shadow.get("phase") == "START" and shadow["max_r"] >= PARAMETERS["watch_r"]:
        shadow["phase"] = "WATCH"
        _event(shadow, now, "PHASE", phase="WATCH", r=round(r_now, 3))
    if tp1_done:
        propose(entry + d * cost, "BREAKEVEN" if shadow.get("phase") not in {"TRAIL"} else "TRAIL")
        atr_value = _num(shadow.get("atr"))
        if shadow["max_r"] >= PARAMETERS["trail_start_r"] and atr_value and atr_value > 0:
            propose(shadow["best_price"] - d * PARAMETERS["trail_atr"] * atr_value, "TRAIL")
    # 3) Yapı bozulması (yalnız yeni giriş modeli, yalnız yeni kapanmış mumda)
    if candles and shadow.get("model") == PULLBACK_MODEL:
        broken, why = structure_broken(candles, direction, int(PARAMETERS["structure_candles"]))
        if broken and net_positive:
            _virtual_exit(shadow, now, "STRUCTURE", price=mark, entry=entry, risk=risk, d=d, detail=why)
            return shadow
    # 4) Süre sınırı
    age_hours = (now - float(shadow.get("started_at") or now)) / 3600
    if age_hours >= PARAMETERS["time_limit_hours"] and shadow["max_r"] < PARAMETERS["time_limit_max_r"] and net_positive:
        _virtual_exit(shadow, now, "TIME", price=mark, entry=entry, risk=risk, d=d)
    return shadow


def _virtual_exit(shadow: dict[str, Any], now: float, reason: str, *, price: float, entry: float, risk: float, d: int,
                  detail: str | None = None) -> None:
    shadow["virtual_exit"] = {"reason": reason, "price": price, "r": round(d * (price - entry) / risk, 3), "at": now,
                              "detail": detail, "phase": shadow.get("phase")}
    _event(shadow, now, "VIRTUAL_EXIT", reason=reason, price=price, r=shadow["virtual_exit"]["r"], detail=detail)


def plan_model(plan: dict[str, Any]) -> str:
    entry = ((plan.get("original_record") or {}).get("entry") or {}) if isinstance(plan.get("original_record"), dict) else {}
    model = entry.get("entry_model") if isinstance(entry, dict) else None
    return str(model or "legacy")


async def tick(state: dict[str, Any], snapshot: dict[str, Any], client: Any, *, now: float | None = None,
               candle_fetch: Any = None) -> None:
    """Ana döngüden çağrılır. Hiçbir koşulda hata fırlatmaz ve emir göndermez."""
    if not enabled():
        return
    from . import original_profit_guard as guard
    from . import original_v2_live as original
    from . import v25_execution as live
    moment = time.time() if now is None else now
    positions: dict[tuple[str, str], dict[str, Any]] = {}
    for row in (snapshot or {}).get("positions", []) or []:
        view = guard.view_of(row)
        if view is not None:
            positions[(view["symbol"], view["direction"])] = view
    changed = False
    for plan in list((state.get("plans") or {}).values()):
        try:
            if not isinstance(plan, dict) or not original.plan_is_original(plan):
                continue
            shadow = plan.get(SHADOW_KEY)
            if not live.live_plan_is_active(plan):
                if isinstance(shadow, dict) and not shadow.get("finalized"):
                    shadow.update(finalized=True, real_status=plan.get("status"), real_close_reason=plan.get("close_reason"))
                    changed = True
                continue
            symbol, direction = str(plan.get("symbol") or "").upper(), str(plan.get("direction") or "").upper()
            view = positions.get((symbol, direction))
            entry, initial_stop = _num(plan.get("entry_price")), _num(plan.get("initial_stop_loss") or plan.get("stop_loss"))
            if view is None or entry is None or initial_stop is None:
                continue
            if not isinstance(shadow, dict):
                shadow = plan[SHADOW_KEY] = new_shadow(moment, plan_model(plan))
            candles = None
            candle = int(moment // CANDLE_SECONDS) * CANDLE_SECONDS
            if not shadow.get("virtual_exit") and shadow.get("last_candle") != candle:
                candles = await _closed_candles(client, symbol, moment, candle_fetch)
                if candles is not None:
                    shadow["last_candle"] = candle
            evaluate(shadow, direction=direction, entry=float(view["entry"]), initial_stop=initial_stop,
                     mark=float(view["mark"]), quantity=float(view["quantity"]),
                     initial_quantity=_num(plan.get("initial_quantity")),
                     cost_bps=_num(plan.get("original_cost_bps")) or 16.0, now=moment, candles=candles)
            changed = True
        except Exception as exc:  # noqa: BLE001 - gölge mod ana akışı asla bozmaz
            logger.warning("EXIT_SHADOW_FAILED symbol=%s error=%s", plan.get("symbol") if isinstance(plan, dict) else None,
                           type(exc).__name__)
    if changed:
        live.persist_state(state)


async def _closed_candles(client: Any, symbol: str, now: float, candle_fetch: Any = None) -> list[dict[str, float]] | None:
    try:
        if candle_fetch is not None:
            rows = await candle_fetch(symbol)
        else:
            from . import v25_execution as live
            rows, _ = await live.live_candles(client, symbol, "15m", CANDLE_LIMIT)
        closed = [row for row in rows if int(row["time"]) + CANDLE_SECONDS <= now]
        return closed if len(closed) >= 60 else None
    except Exception:  # noqa: BLE001 - veri yoksa bu tur yapı kontrolü yapılmaz
        return None
