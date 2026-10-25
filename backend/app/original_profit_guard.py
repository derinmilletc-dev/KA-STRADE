"""Original V2 LIVE · Akıllı Kâr Koruma (Smart Profit Protection).

Yalnızca Original V2 Live'ın açtığı pozisyonlara dokunur. Strateji, TP1/TP3/SL, risk ve Demo/Backtest
kodu değişmez. Güven puanı aynı strateji fonksiyonuyla (kapanmış mumlardan) yeniden hesaplanır; bu
yüzden puan yalnızca 15 dakikalık mum kapanışında değişir ve karar da yalnızca kapanışlara bağlıdır.

Durum makinesi (pozisyon başına, plan["smart_guard"] içinde kalıcı):
  NORMAL            puan >= eşik
  KAR_KORUMA        puan ilk kez eşiğin altına düştü; sayaç başladı
  CIKISA_HAZIR      sayaç doldu VE sonraki yeni kapanmış mumda puan hâlâ eşiğin altında
Zarardaki pozisyon asla bu özellikle kapatılmaz; net kâra geçince (puan hâlâ düşükse) kapatılır.
"""
from __future__ import annotations

import logging
import math
import time
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, Awaitable, Callable

logger = logging.getLogger("kaistrade.profit_guard")

GUARD_KEY = "smart_guard"
SETTINGS_KEY = "smart_profit"
CLIENT_KIND = "SGP"
CLOSE_INTENT_PREFIX = "smartexit-"
CLOSE_REASON = "SMART_EXIT"
STOP_CLOSE_REASON = "SMART_STOP"
CLOSE_REASON_LABEL = "Akıllı Güven Puanı Çıkışı"
STOP_REASON_LABEL = "Akıllı Kâr Koruma Stopu"

MODE_NORMAL = "NORMAL"
MODE_PROTECT = "KAR_KORUMA"
MODE_EXIT_READY = "CIKISA_HAZIR"
MODES = {MODE_NORMAL, MODE_PROTECT, MODE_EXIT_READY}

CANDLE_SECONDS = 900
FUNDING_CACHE_SECONDS = 300
MIN_THRESHOLD, MAX_THRESHOLD = 50, 95
MIN_WAIT_MINUTES, MAX_WAIT_MINUTES = 1, 120
MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS = 30, 300

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,             # ANA SİSTEM varsayılan KAPALI: sen açana kadar gerçek emir gitmez
    "threshold": 70,              # puan eşiği
    "wait_minutes": 10,           # bekleme süresi
    "check_interval_seconds": 120,  # net kâr / emir kontrol aralığı
    "protect_stop_enabled": True,   # geçici kâr koruma stopu
    "smart_exit_enabled": True,     # akıllı çıkış (gerçek emir)
    "emergency_off": False,         # bağımsız ACİL DURDUR
    "direction_mismatch_action": "record_only",  # ters yön sinyali: "record_only" (yalnız kaydet) | "count_as_low"
}
MISMATCH_ACTIONS = ("record_only", "count_as_low")


def _d(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    return number if number.is_finite() else None


def clean_settings(raw: Any) -> dict[str, Any]:
    """Kayıtlı ayarı güvenli aralıklara çeker; eksik/bozuksa varsayılan."""
    base = dict(DEFAULT_SETTINGS)
    if not isinstance(raw, dict):
        return base
    for key in ("enabled", "protect_stop_enabled", "smart_exit_enabled", "emergency_off"):
        if type(raw.get(key)) is bool:
            base[key] = raw[key]
    for key, low, high in (("threshold", MIN_THRESHOLD, MAX_THRESHOLD),
                           ("wait_minutes", MIN_WAIT_MINUTES, MAX_WAIT_MINUTES),
                           ("check_interval_seconds", MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS)):
        value = raw.get(key)
        if type(value) is int and low <= value <= high:
            base[key] = value
    if raw.get("direction_mismatch_action") in MISMATCH_ACTIONS:
        base["direction_mismatch_action"] = raw["direction_mismatch_action"]
    return base


def get_settings(config: dict[str, Any]) -> dict[str, Any]:
    return clean_settings(config.get(SETTINGS_KEY))


def candle_open(now: float) -> int:
    """Son KAPANMIŞ 15 dakikalık mumun sınırı (UTC). Strateji `at` değeriyle aynı."""
    return int(now) // CANDLE_SECONDS * CANDLE_SECONDS


def new_guard() -> dict[str, Any]:
    return {
        "mode": MODE_NORMAL, "last_score": None, "last_score_candle": None, "last_score_note": None,
        "first_low_at": None, "first_low_candle": None, "timer_end": None, "exit_ready": False,
        "exit_ready_at": None, "temp_stop": None, "last_net_pnl": None, "last_check_at": None,
        "close_reason": None, "close_order_id": None, "observations": [], "atr": None,
        "funding_paid": None, "funding_checked_at": None, "last_error": None,
        "gen": 0, "client_ids": [], "ledger": [], "last_low": False,
        "direction_state": None, "opposite_confidence": None, "mismatch_since": None,
    }


def guard_of(plan: dict[str, Any]) -> dict[str, Any]:
    guard = plan.get(GUARD_KEY)
    if not isinstance(guard, dict) or guard.get("mode") not in MODES:
        guard = new_guard()
        plan[GUARD_KEY] = guard
    for key, value in new_guard().items():
        guard.setdefault(key, value)
    return guard


# ---------------------------------------------------------------- puan
def classify_observation(analysis: dict[str, Any] | None, direction: str, threshold: int) -> dict[str, Any]:
    """Aynı stratejinin `analysis` çıktısını pozisyon açısından sınıflandırır. Puan yapay değiştirilmez.

    - Yön pozisyonla aynı: puan = strateji güveni.
    - BEKLE: stratejinin kendi (69'u geçemeyen) puanı; gerçek düşük puandır.
    - Ters yön: puan YOK; ayrı `direction_mismatch` durumu (karşı yönün güveni ayrıca kaydedilir)."""
    out = {"score": None, "note": "puan_okunamadi", "mismatch": False, "opposite_confidence": None}
    try:
        confidence = int(analysis["confidence"])  # type: ignore[index]
        signal_direction = str(analysis["direction"]).upper()  # type: ignore[index]
    except (KeyError, TypeError, ValueError):
        return out
    if direction not in {"LONG", "SHORT"} or not 0 <= confidence <= 100:
        return {**out, "note": "puan_gecersiz"}
    if signal_direction == direction:
        return {**out, "score": confidence, "note": "yon_ayni"}
    if signal_direction == "BEKLE":
        return {**out, "score": confidence, "note": "bekle"}
    if signal_direction in {"LONG", "SHORT"}:
        return {**out, "note": "direction_mismatch", "mismatch": True, "opposite_confidence": confidence}
    return {**out, "note": "yon_taninmadi"}


def position_score(analysis: dict[str, Any], direction: str, threshold: int) -> tuple[int | None, str]:
    """Geriye dönük kısa yol: ters yönde puan None ve not `direction_mismatch`."""
    result = classify_observation(analysis, direction, threshold)
    return result["score"], result["note"]


# ---------------------------------------------------------------- net kâr
def view_of(row: Any) -> dict[str, Any] | None:
    """Anlık görüntüdeki/positionRisk satırından tutarlı pozisyon görünümü; eksikse None."""
    if not isinstance(row, dict):
        return None
    amount = _d(row.get("positionAmt"))
    quantity = abs(amount) if amount is not None else _d(row.get("quantity"))
    if quantity is None or quantity <= 0:
        return None
    direction = str(row.get("direction") or "").upper()
    if amount is not None and amount != 0:
        direction = "LONG" if amount > 0 else "SHORT"
    entry = _d(row.get("entry_price", row.get("entryPrice")))
    mark = _d(row.get("mark_price", row.get("markPrice")))
    if direction not in {"LONG", "SHORT"} or entry is None or mark is None or entry <= 0 or mark <= 0:
        return None
    return {"symbol": str(row.get("symbol") or "").upper(), "direction": direction,
            "quantity": quantity, "entry": entry, "mark": mark}


def net_pnl(view: dict[str, Any], *, fee_bps: Any, slippage_bps: Any, funding_paid: Any) -> dict[str, Decimal] | None:
    """Net PnL = gerçekleşmemiş − açılış komisyonu − tahmini kapanış komisyonu − funding − tahmini kayma.

    Funding ödenmişse maliyet, alınmışsa sıfır sayılır (muhafazakâr). Veri eksikse None (otomatik işlem yok)."""
    fee, slip, funding = _d(fee_bps), _d(slippage_bps), _d(funding_paid)
    if None in (fee, slip, funding) or fee < 0 or slip < 0:
        return None
    sign = Decimal(1) if view["direction"] == "LONG" else Decimal(-1)
    qty, entry, mark = view["quantity"], view["entry"], view["mark"]
    unrealized = (mark - entry) * qty * sign
    open_fee = entry * qty * fee / Decimal(10000)
    close_fee = mark * qty * fee / Decimal(10000)
    slippage = mark * qty * slip / Decimal(10000)
    funding_cost = max(funding, Decimal(0))
    total_cost = open_fee + close_fee + slippage + funding_cost
    return {"unrealized": unrealized, "open_fee": open_fee, "close_fee": close_fee, "slippage": slippage,
            "funding": funding_cost, "cost": total_cost, "net": unrealized - total_cost}


# ---------------------------------------------------------------- geçici stop
def temp_stop_level(view: dict[str, Any], pnl: dict[str, Decimal], *, atr: Any, tick: Any,
                    original_stop: Any, targets: Any = None) -> tuple[Decimal | None, str]:
    """Net kârı koruyan geçici stop fiyatı. Güvenilir hesaplanamazsa (None, sebep): seviye uydurulmaz.

    Alt sınır: giriş ± birim başı toplam maliyet + küçük tampon (stop tetiklense bile net kâr pozitif).
    Üst sınır: işaret fiyatından en az max(3 tick, 0.1 ATR) uzak (hemen tetiklenmesin).
    İdeal seviye: işaret fiyatından 1 ATR geride, alt sınırın daha kârlı tarafında kalacak şekilde."""
    atr_v, tick_v, original = _d(atr), _d(tick), _d(original_stop)
    if atr_v is None or atr_v <= 0:
        return None, "atr_yok"
    if tick_v is None or tick_v <= 0:
        return None, "tick_yok"
    if original is None or original <= 0:
        return None, "orijinal_sl_yok"
    if pnl["net"] <= 0:
        return None, "net_kar_yok"
    qty, entry, mark = view["quantity"], view["entry"], view["mark"]
    cost_unit = pnl["cost"] / qty
    buffer = max(2 * tick_v, cost_unit / 4)
    gap = max(3 * tick_v, atr_v / 10)
    if view["direction"] == "LONG":
        floor = entry + cost_unit + buffer
        wanted = max(floor, mark - atr_v)
        price = (wanted / tick_v).to_integral_value(rounding=ROUND_CEILING) * tick_v
        if price > mark - gap:
            return None, "kar_payi_yetersiz"
        if price <= original:
            return None, "orijinal_sl_ile_cakisiyor"
    else:
        floor = entry - cost_unit - buffer
        wanted = min(floor, mark + atr_v)
        price = (wanted / tick_v).to_integral_value(rounding=ROUND_FLOOR) * tick_v
        if price < mark + gap:
            return None, "kar_payi_yetersiz"
        if price >= original:
            return None, "orijinal_sl_ile_cakisiyor"
    if price <= 0:
        return None, "fiyat_gecersiz"
    # TP çakışması: stop, hiçbir hedefin (TP1/TP2/TP3) kâr tarafına geçmemeli
    for target in targets if isinstance(targets, (list, tuple)) else []:
        value = _d(target)
        if value is None:
            return None, "tp_okunamadi"
        if (view["direction"] == "LONG" and price >= value) or (view["direction"] == "SHORT" and price <= value):
            return None, "tp_ile_cakisiyor"
    return price, "ok"


# ---------------------------------------------------------------- durum makinesi
def _reset_normal(guard: dict[str, Any]) -> None:
    guard.update(mode=MODE_NORMAL, first_low_at=None, first_low_candle=None, timer_end=None,
                 exit_ready=False, exit_ready_at=None)


def observe(guard: dict[str, Any], *, score: int | None, candle: int, now: float,
            threshold: int, wait_seconds: int, note: str | None = None,
            mismatch: bool = False, mismatch_action: str = "record_only",
            opposite_confidence: int | None = None) -> list[str]:
    """Kapanmış mum gözlemi. Aynı mum tekrar okunursa YENİ gözlem sayılmaz. Olay kodları döner.

    Olaylar: SCORE_UNAVAILABLE, DUPLICATE_OBSERVATION, SCORE_LOW_STARTED, SCORE_LOW_CONTINUES, EXIT_READY,
    RECOVERED, SCORE_OK, DIRECTION_MISMATCH, DIRECTION_MISMATCH_STARTED, DIRECTION_MATCH_RESTORED.

    Ters yön (`mismatch`): puan değildir. `record_only`: yalnız kaydedilir, sayaç/mod değişmez ve çıkış beklemeye
    alınır. `count_as_low`: düşük puan gibi sayılır (kayıtta nedeni `direction_mismatch` yazar)."""
    events: list[str] = []
    if score is None and not mismatch:
        guard["last_score_note"] = note or "puan_yok"
        return ["SCORE_UNAVAILABLE"]
    if guard.get("last_score_candle") == candle:
        return ["DUPLICATE_OBSERVATION"]
    log = guard.setdefault("observations", [])
    if mismatch:
        started = guard.get("direction_state") != "MISMATCH"
        guard.update(last_score=None, last_score_candle=candle, last_score_note="direction_mismatch",
                     direction_state="MISMATCH", opposite_confidence=opposite_confidence)
        if started:
            guard["mismatch_since"] = now
        log.append({"candle": candle, "score": None, "direction_mismatch": True, "opposite_confidence": opposite_confidence,
                    "action": mismatch_action, "at": now, "source": "15m_kapanis"})
        del log[:-20]
        events.append("DIRECTION_MISMATCH_STARTED" if started else "DIRECTION_MISMATCH")
        if mismatch_action != "count_as_low":
            guard["last_low"] = False  # yalnız kayıt: bu gözlem çıkışı tetiklemez/doğrulamaz
            return events
        low = True
    else:
        if guard.get("direction_state") == "MISMATCH":
            events.append("DIRECTION_MATCH_RESTORED")
        guard.update(last_score=score, last_score_candle=candle, last_score_note=note,
                     direction_state="MATCH", opposite_confidence=None, mismatch_since=None)
        log.append({"candle": candle, "score": score, "at": now, "source": "15m_kapanis"})
        del log[:-20]
        low = score < threshold  # type: ignore[operator]
    guard["last_low"] = low
    if low:
        guard["low_reason"] = "direction_mismatch" if mismatch else "puan"  # çıkışa yol açan son gözlemin nedeni
    if not low:
        if guard["mode"] != MODE_NORMAL or guard.get("first_low_at") is not None:
            events.append("RECOVERED")
        events.append("SCORE_OK")
        _reset_normal(guard)
        return events
    if guard.get("first_low_at") is None:
        guard.update(mode=MODE_PROTECT, first_low_at=now, first_low_candle=candle,
                     timer_end=now + wait_seconds, exit_ready=False, exit_ready_at=None,
                     low_reason="direction_mismatch" if mismatch else "puan")
        return [*events, "SCORE_LOW_STARTED"]
    # sayaç doldu mu VE ilk düşük mumdan SONRA yeni kapanmış mumda puan hâlâ düşük mü?
    if (not guard.get("exit_ready") and now >= float(guard["timer_end"])
            and candle > int(guard["first_low_candle"])):
        guard.update(mode=MODE_EXIT_READY, exit_ready=True, exit_ready_at=now)
        return [*events, "EXIT_READY"]
    return [*events, "SCORE_LOW_CONTINUES"]


def exit_decision(guard: dict[str, Any], net: Decimal | None, *, smart_exit: bool) -> str:
    """CLOSE | WAIT_PROFIT | NONE. Zarar veya bilinmeyen net kârda asla CLOSE."""
    if not smart_exit or not guard.get("exit_ready") or guard.get("mode") != MODE_EXIT_READY:
        return "NONE"
    if net is None:
        return "NONE"
    return "CLOSE" if net > 0 else "WAIT_PROFIT"


def checks_due(guard: dict[str, Any], now: float, interval: int) -> bool:
    last = guard.get("last_check_at")
    return last is None or now - float(last) >= interval


def temp_stop_matches(row: dict[str, Any], *, symbol: str, direction: str, price: Decimal, quantity: Decimal) -> bool:
    """Borsadaki açık algo satırı geçici stop kaydıyla birebir aynı mı?"""
    side = "SELL" if direction == "LONG" else "BUY"
    try:
        return (str(row.get("symbol") or "").upper() == symbol and str(row.get("side") or "").upper() == side
                and str(row.get("type") or "").upper() == "STOP_MARKET"
                and str(row.get("status") or "").upper() in {"NEW", "WORKING", "PENDING_NEW"}
                and Decimal(str(row.get("trigger_price", row.get("triggerPrice")))) == price
                and Decimal(str(row.get("quantity", row.get("origQty")))) == quantity)
    except Exception:
        return False


def is_guard_client_id(value: Any, intent_id: str, client_id_for: Callable[[str, str], str]) -> bool:
    return bool(value) and str(value) == client_id_for(CLIENT_KIND, intent_id)


# =============================================================== borsa katmanı (Original V2 Live)
_RULES_CACHE: dict[str, tuple[float, dict[str, Decimal]]] = {}
RULES_TTL_SECONDS = 3600
ORDER_RETRY_SECONDS = 60
PENDING_VISIBILITY_SECONDS = 45
SCORE_RETRY_SECONDS = 30
_ACTIVE_BLOCKED_STATUSES = ("KAPAT", "GÜVENLİK", "ACİL", "STOP BAŞARISIZ", "DOLUM BEKLİYOR")


def _fee_slippage(state: dict[str, Any]) -> tuple[Decimal | None, Decimal | None]:
    from .strategies import original_v2_demo as strategy
    fixed = strategy.fixed_profile()["execution_policy"]
    current = state.get("policy") or {}
    try:
        fee = max(Decimal(str(current.get("fee_bps_per_side", 0))), Decimal(str(fixed["fee_bps_per_side"])))
        slip = max(Decimal(str(current.get("slippage_bps_per_side", 0))), Decimal(str(fixed["slippage_bps_per_side"])))
    except Exception:
        return None, None
    return fee, slip


async def fetch_analysis(client: Any, symbol: str, at: int,
                         kline_fetch: Callable[[str, str, int], Awaitable[Any]] | None) -> tuple[dict[str, Any] | None, str]:
    """Aynı strateji fonksiyonu, aynı kapanmış mum penceresi. Veri eksik/bayatsa (None, sebep)."""
    from .backtest_data import normalize_candles
    from .strategies import original_v2_demo as strategy
    from .strategies.contracts import StrategyInput
    frames: dict[str, Any] = {}
    try:
        for interval in strategy.INTERVALS:
            if kline_fetch is None:
                raw = await client.public_get("/fapi/v1/klines", {"symbol": symbol, "interval": interval,
                                                                  "limit": strategy.CANDLE_FETCH_LIMIT})
            else:
                raw = await kline_fetch(symbol, interval, strategy.CANDLE_FETCH_LIMIT)
            if not isinstance(raw, list) or any(not isinstance(row, list) or len(row) < 8 for row in raw):
                return None, "mum_verisi_gecersiz"
            rows = [{"open_time": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4],
                     "volume": r[5], "close_time": r[6], "quote_volume": r[7]} for r in raw]
            series, duplicates = normalize_candles(rows, interval)
            closed = series.closed(at)
            if duplicates or len(closed) != strategy.CANDLE_FETCH_LIMIT - 1 or not series.history_complete(at):
                return None, "mum_verisi_eksik_veya_bayat"
            frames[interval] = closed
        result = strategy.evaluate_live_universe(StrategyInput(symbol, at, frames), allowed_symbols=[symbol], enabled=True)
    except Exception as exc:  # veri/oran sınırı/strateji hatası: otomatik işlem yok
        logger.warning("SMART_GUARD_SCORE_FAILED symbol=%s error=%s", symbol, type(exc).__name__)
        return None, "puan_hesaplanamadi"
    analysis = (result.legacy or {}).get("analysis")
    return (analysis, "ok") if isinstance(analysis, dict) else (None, "analiz_yok")


async def _rules(client: Any, symbol: str) -> dict[str, Decimal] | None:
    from . import v25_execution as live
    cached = _RULES_CACHE.get(symbol)
    if cached and time.time() - cached[0] < RULES_TTL_SECONDS:
        return cached[1]
    try:
        rules = await live.live_symbol_rules(client, symbol, "MARKET")
    except Exception:
        return None
    _RULES_CACHE[symbol] = (time.time(), rules)
    return rules


async def _funding_paid(client: Any, plan: dict[str, Any], guard: dict[str, Any], now: float, *, force: bool = False) -> Decimal | None:
    """Girişten beri ödenen funding (USDT, ödenen=pozitif). Alınamazsa eski değer; hiç yoksa None."""
    cached = _d(guard.get("funding_paid"))
    if not force and cached is not None and guard.get("funding_checked_at") and now - float(guard["funding_checked_at"]) < FUNDING_CACHE_SECONDS:
        return cached
    from datetime import datetime
    try:
        start_ms = int(datetime.fromisoformat(str(plan["created_at"]).replace("Z", "+00:00")).timestamp() * 1000)
        rows = await client.signed("GET", "/fapi/v1/income", {"symbol": plan["symbol"], "incomeType": "FUNDING_FEE",
                                                              "startTime": start_ms, "limit": 1000})
        if not isinstance(rows, list):
            raise ValueError("income")
        total = Decimal(0)
        for row in rows:
            if isinstance(row, dict) and str(row.get("asset") or "USDT") == "USDT" and str(row.get("symbol") or plan["symbol"]) == plan["symbol"]:
                amount = _d(row.get("income"))
                if amount is None:
                    raise ValueError("income_value")
                total += amount
    except Exception:
        return cached if (cached is not None and not force) else None
    paid = -total
    guard["funding_paid"] = str(paid)
    guard["funding_checked_at"] = now
    return paid


def _alert(state: dict[str, Any], config: dict[str, Any], plan: dict[str, Any], kind: str, severity: str,
           title: str, message: str) -> None:
    from . import v25_execution as live
    from .original_v2_live import SOURCE
    event = live.add_event(state, kind, message, symbol=plan.get("symbol"), plan_id=plan.get("id"),
                           recipient_user_id=config.get("owner_id"), source=SOURCE)
    config.setdefault("alerts", []).insert(0, {
        "id": "original-live:" + event["id"], "type": kind, "severity": severity, "title": title,
        "message": event["message"], "timestamp": event["created_at"], "target": "master-trade", "read": False})
    del config["alerts"][100:]


def _note_once(guard: dict[str, Any], key: str, value: str) -> bool:
    if guard.get(key) == value:
        return False
    guard[key] = value
    return True


async def _open_algos(client: Any, symbol: str) -> list[dict[str, Any]] | None:
    from . import v25_execution as live
    try:
        payload = await client.signed("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol})
        if live.classify_algo_snapshot_payload(payload) == "UNKNOWN":
            return None
        return live.normalize_open_algo_rows(payload)
    except Exception:
        return None


# ---------------------------------------------------------------- bağımsız emir defteri (idempotency)
LEDGER_ACTIVE = {"INTENT", "SENT", "UNKNOWN", "CONFIRMED"}


def ledger_active(guard: dict[str, Any], kind: str) -> dict[str, Any] | None:
    """Bu pozisyon için `kind` (TEMP_STOP | CLOSE) türünde yaşayan kayıt varsa onu döndürür."""
    for entry in reversed(guard.get("ledger") or []):
        if entry.get("kind") == kind and entry.get("status") in LEDGER_ACTIVE:
            return entry
    return None


def ledger_add(guard: dict[str, Any], kind: str, client_id: str, **info: Any) -> dict[str, Any]:
    """Emri göndermeden ÖNCE kaydedilir. Aynı türde yaşayan kayıt varken ikinci kayıt açılmaz."""
    existing = ledger_active(guard, kind)
    if existing is not None:
        return existing
    entry = {"kind": kind, "client_id": client_id, "status": "INTENT", "at": time.time(), **info}
    ledger = guard.setdefault("ledger", [])
    ledger.append(entry)
    del ledger[:-30]
    return entry


def ledger_status(guard: dict[str, Any], client_id: str, status: str) -> None:
    for entry in guard.get("ledger") or []:
        if entry.get("client_id") == client_id:
            entry["status"] = status
            entry["updated_at"] = time.time()


def _guard_rows(rows: list[dict[str, Any]], plan: dict[str, Any], guard: dict[str, Any]) -> list[dict[str, Any]]:
    """Borsadaki, bu pozisyonun TÜM kuşaklardaki geçici stop satırları."""
    ids = {str(value) for value in guard.get("client_ids") or [] if value}
    symbol = str(plan.get("symbol") or "").upper()
    return [row for row in rows if str(row.get("client_algo_id") or "") in ids and str(row.get("symbol") or "").upper() == symbol]


async def cancel_temp_stop(client: Any, state: dict[str, Any], config: dict[str, Any], plan: dict[str, Any],
                           guard: dict[str, Any], *, reason: str) -> bool:
    """Yalnızca bu özelliğin geçici stop(lar)ını iptal eder ve borsadan doğrular. Orijinal SL/TP'ye dokunmaz."""
    stop = guard.get("temp_stop")
    if not isinstance(stop, dict) and not (guard.get("client_ids") and ledger_active(guard, "TEMP_STOP")):
        return True
    symbol = plan["symbol"]
    stop = stop if isinstance(stop, dict) else guard.setdefault("temp_stop", {})
    rows = await _open_algos(client, symbol)
    if rows is None:
        stop["cancel_pending"] = True
        guard["last_error"] = "geçici stop durumu borsadan doğrulanamadı"
        return False
    mine = _guard_rows(rows, plan, guard)
    for row in mine:
        try:
            await client.signed("DELETE", "/fapi/v1/algoOrder", {"symbol": symbol, "algoId": row["algo_id"]})
        except Exception as exc:
            if getattr(exc, "exchange_code", None) not in (-2011, -2013):
                stop["cancel_pending"] = True
                guard["last_error"] = "geçici stop iptal edilemedi"
                if _note_once(guard, "cancel_fail_note", reason):
                    _alert(state, config, plan, "SMART_GUARD_ORDER_FAILED", "WARNING", "Akıllı Kâr Koruma",
                           f"{symbol} geçici koruma stopu iptal edilemedi; orijinal Stop/TP korunuyor, tekrar denenecek.")
                return False
    if mine:
        check = await _open_algos(client, symbol)
        if check is None or _guard_rows(check, plan, guard):
            stop["cancel_pending"] = True
            guard["last_error"] = "geçici stop iptali doğrulanamadı"
            return False
    for entry in guard.get("ledger") or []:
        if entry.get("kind") == "TEMP_STOP" and entry.get("status") in LEDGER_ACTIVE:
            entry["status"] = "CANCELLED"
            entry["updated_at"] = time.time()
    guard["temp_stop"] = None
    guard["cancel_fail_note"] = None
    if mine:
        _alert(state, config, plan, "SMART_GUARD_TEMP_STOP_REMOVED", "INFO", "Akıllı Kâr Koruma",
               f"{symbol} geçici kâr koruma stopu kaldırıldı ({reason}). Orijinal Stop ve TP aynen duruyor.")
    return True


async def ensure_temp_stop(client: Any, state: dict[str, Any], config: dict[str, Any], plan: dict[str, Any],
                           guard: dict[str, Any], view: dict[str, Any], pnl: dict[str, Decimal]) -> None:
    from . import v25_execution as live
    stop = guard.get("temp_stop")
    symbol = plan["symbol"]
    if isinstance(stop, dict) and stop.get("cancel_pending"):
        await cancel_temp_stop(client, state, config, plan, guard, reason="bekleyen iptal")
        return
    rows = await _open_algos(client, symbol)
    if rows is None:
        return
    mine = _guard_rows(rows, plan, guard)
    entry = ledger_active(guard, "TEMP_STOP")
    if len(mine) > 1 or (mine and entry is None):
        await cancel_temp_stop(client, state, config, plan, guard, reason="kayıt dışı/çift geçici stop")
        return
    if mine and entry is not None:
        row = mine[0]
        price, quantity = _d(entry.get("price")), _d(entry.get("quantity"))
        if row.get("client_algo_id") == entry["client_id"] and price is not None and quantity is not None \
                and temp_stop_matches(row, symbol=symbol, direction=view["direction"], price=price, quantity=quantity):
            if quantity == view["quantity"]:
                entry["status"] = "CONFIRMED"
                if isinstance(stop, dict):
                    stop.pop("pending", None)
                    stop["algo_id"] = int(row["algo_id"])
                return
            await cancel_temp_stop(client, state, config, plan, guard, reason="miktar değişti")
            guard["last_check_at"] = None  # yeni miktarla hemen yeniden kur
            return
        await cancel_temp_stop(client, state, config, plan, guard, reason="parametre uyuşmadı")
        return
    if entry is not None:  # kayıt var, borsada görünmüyor
        if entry["status"] in {"INTENT", "SENT", "UNKNOWN"} and time.time() - float(entry.get("at") or 0) < PENDING_VISIBILITY_SECONDS:
            return  # emir yeni gönderildi; borsa listesinde görünmesi beklenir, tekrar gönderilmez
        entry["status"] = "LOST" if entry["status"] != "CONFIRMED" else "GONE"  # yeni kuşak kimlikle kurulabilir
        guard["temp_stop"] = None
    if not config_protect_enabled(config):
        return
    rules = await _rules(client, symbol)
    if rules is None:
        if _note_once(guard, "temp_stop_note", "kural_yok"):
            _alert(state, config, plan, "SMART_GUARD_TEMP_STOP_UNAVAILABLE", "WARNING", "Akıllı Kâr Koruma",
                   f"{symbol} için geçici stop kurulamadı: borsa fiyat kuralları okunamadı. Orijinal Stop/TP çalışıyor.")
        return
    price, why = temp_stop_level(view, pnl, atr=guard.get("atr"), tick=rules["tick"],
                                 original_stop=plan.get("stop_loss"), targets=plan.get("targets"))
    if price is None:
        if _note_once(guard, "temp_stop_note", why):
            _alert(state, config, plan, "SMART_GUARD_TEMP_STOP_UNAVAILABLE", "WARNING", "Akıllı Kâr Koruma",
                   f"{symbol} için geçici kâr koruma stopu güvenli hesaplanamadı ({why}); seviye uydurulmadı. "
                   "Orijinal Stop/TP çalışıyor, çıkış kararı mum kapanışına göre sürüyor.")
        return
    if view["quantity"] * price < rules["min_notional"]:
        if _note_once(guard, "temp_stop_note", "min_notional"):
            _alert(state, config, plan, "SMART_GUARD_TEMP_STOP_UNAVAILABLE", "WARNING", "Akıllı Kâr Koruma",
                   f"{symbol} kalan miktar borsa minimum tutarının altında; geçici stop kurulamadı.")
        return
    guard["gen"] = int(guard.get("gen") or 0) + 1
    client_id = live.client_id_for(CLIENT_KIND, f"{plan['intent_id']}:{guard['gen']}")
    ids = guard.setdefault("client_ids", [])
    if client_id not in ids:
        ids.append(client_id)  # tanınabilsin diye emirden ÖNCE kayda girer
    entry = ledger_add(guard, "TEMP_STOP", client_id, price=live.decimal_text(price), quantity=live.decimal_text(view["quantity"]))
    params = {"algoType": "CONDITIONAL", "symbol": symbol, "side": "SELL" if view["direction"] == "LONG" else "BUY",
              "type": "STOP_MARKET", "workingType": "MARK_PRICE", "priceProtect": "TRUE",
              "triggerPrice": live.decimal_text(price), "quantity": live.decimal_text(view["quantity"]),
              "reduceOnly": "true", "clientAlgoId": client_id}
    guard["temp_stop"] = {"pending": True, "price": entry["price"], "quantity": entry["quantity"], "client_id": client_id}
    await live.persist_state_awaited(state)  # emri göndermeden önce kaydet: yeniden başlatmada çift emir olmaz
    try:
        await live.post_entry_protection_algo(client, params, rows)
        entry["status"] = "SENT"
    except Exception as exc:
        guard["last_error"] = "geçici stop gönderilemedi"
        if getattr(exc, "unknown_execution", False):
            entry["status"] = "UNKNOWN"
            return  # sonuç belirsiz: bir sonraki turda borsadan okunur, aynı kayıt varken tekrar GÖNDERİLMEZ
        entry["status"] = "REJECTED"
        guard["temp_stop"] = None
        if _note_once(guard, "temp_stop_note", "emir_hatasi"):
            _alert(state, config, plan, "SMART_GUARD_ORDER_FAILED", "WARNING", "Akıllı Kâr Koruma",
                   f"{symbol} geçici kâr koruma stopu gönderilemedi; orijinal Stop/TP çalışıyor.")
        return
    verify = await _open_algos(client, symbol)
    found = [r for r in (_guard_rows(verify, plan, guard) if verify is not None else []) if r.get("client_algo_id") == client_id]
    if found and temp_stop_matches(found[0], symbol=symbol, direction=view["direction"], price=price, quantity=view["quantity"]):
        entry["status"] = "CONFIRMED"
        guard["temp_stop"].pop("pending", None)
        guard["temp_stop"]["algo_id"] = int(found[0]["algo_id"])
        guard["temp_stop"]["verified_at"] = time.time()
        guard["temp_stop_note"] = None
        _alert(state, config, plan, "SMART_GUARD_TEMP_STOP_CREATED", "INFO", "Akıllı Kâr Koruma",
               f"{symbol} için net kârı koruyan geçici stop kuruldu: {live.decimal_text(price)}. "
               "Bu stop yalnızca bu pozisyonda çalışır; orijinal Stop ve TP aynen duruyor.")


def config_protect_enabled(config: dict[str, Any]) -> bool:
    return get_settings(config)["protect_stop_enabled"]


async def _observe_now(client: Any, state: dict[str, Any], plan: dict[str, Any], guard: dict[str, Any], view: dict[str, Any],
                       cfg: dict[str, Any], now: float, kline_fetch: Any) -> tuple[list[str], dict[str, Any], str]:
    """Son kapanmış mumdan puanı hesapla ve durum makinesine işle (tek yer; hem periyodik hem çıkış öncesi)."""
    at = candle_open(now)
    analysis, why = await fetch_analysis(client, plan["symbol"], at, kline_fetch)
    obs = classify_observation(analysis, view["direction"], cfg["threshold"]) if analysis else \
        {"score": None, "note": why, "mismatch": False, "opposite_confidence": None}
    if analysis and analysis.get("atr") is not None:
        guard["atr"] = analysis["atr"]
    events = observe(guard, score=obs["score"], candle=at, now=now, threshold=cfg["threshold"],
                     wait_seconds=cfg["wait_minutes"] * 60, note=obs["note"], mismatch=obs["mismatch"],
                     mismatch_action=cfg["direction_mismatch_action"], opposite_confidence=obs["opposite_confidence"])
    return events, obs, why


async def _exit(client: Any, state: dict[str, Any], config: dict[str, Any], plan: dict[str, Any], guard: dict[str, Any],
                cfg: dict[str, Any], now: float, kline_fetch: Any) -> None:
    """Çıkış emri: önce HER ŞEY yeniden doğrulanır (pozisyon, miktar, puan, net kâr)."""
    from . import v25_execution as live
    symbol = plan["symbol"]
    entry = ledger_active(guard, "CLOSE")
    if entry is not None and entry["status"] in {"SENT", "CONFIRMED"}:
        return
    if now < float(guard.get("exit_retry_at") or 0):
        return
    try:
        fresh = await live.live_plan_position(client, symbol)
    except Exception:
        guard["last_error"] = "pozisyon doğrulanamadı"
        return
    view = view_of(fresh) if fresh else None
    if view is None or view["direction"] != str(plan.get("direction") or "").upper() or not live.confirm_live_plan_provenance(plan, fresh):
        guard["last_error"] = "pozisyon/sahiplik doğrulanamadı; kapatılmadı"
        return
    at = candle_open(now)
    if guard.get("last_score_candle") != at:  # yeni kapanmış mum varsa puanı yeniden doğrula
        events, obs, why = await _observe_now(client, state, plan, guard, view, cfg, now, kline_fetch)
        if obs["score"] is None and not obs["mismatch"]:
            guard["last_error"] = "puan doğrulanamadı; kapatılmadı"
            return
        if "RECOVERED" in events:
            await _on_recovered(client, state, config, plan, guard, obs["score"])
            return
        if "DIRECTION_MISMATCH_STARTED" in events:
            _alert_mismatch(state, config, plan, guard, cfg)
    if not guard.get("exit_ready") or not guard.get("last_low"):
        if guard.get("direction_state") == "MISMATCH":
            guard["last_error"] = "ters yön sinyali (yalnız kayıt modu): çıkış beklemede"
        return
    fee, slip = _fee_slippage(state)
    funding = await _funding_paid(client, plan, guard, now, force=True)
    pnl = net_pnl(view, fee_bps=fee, slippage_bps=slip, funding_paid=funding) if funding is not None else None
    if pnl is None or exit_decision(guard, pnl["net"], smart_exit=True) != "CLOSE":
        guard["last_error"] = "net kâr doğrulanamadı veya pozitif değil; kapatılmadı"
        return
    close_intent = f"{CLOSE_INTENT_PREFIX}{plan['id']}"
    close_client_id = live.client_id_for("CLOSE", close_intent)  # STOP kimliğinden (SGP) bağımsız, tek ve sabit kimlik
    entry = ledger_add(guard, "CLOSE", close_client_id, reason=guard.get("low_reason") or "puan", net_pnl=str(pnl["net"]))
    ids = plan.setdefault("close_client_order_ids", [])
    if close_client_id not in ids:
        ids.append(close_client_id)
    guard["close_net_pnl"] = str(pnl["net"])
    await live.persist_state_awaited(state)  # emirden önce kaydet: yeniden başlatmada çift kapatma olmaz
    try:
        result = await live.close_tracked_symbol(client, symbol, close_intent)  # kendi içinde clientOrderId ile de tekrarsız
    except Exception as exc:
        guard["exit_retry_at"] = now + ORDER_RETRY_SECONDS
        unknown = bool(getattr(exc, "unknown_execution", False))
        entry["status"] = "UNKNOWN" if unknown else "REJECTED"
        guard["last_error"] = "kapatma emri sonucu doğrulanamadı" if unknown else "kapatma emri gönderilemedi"
        _alert(state, config, plan, "SMART_GUARD_ORDER_FAILED", "WARNING", "Akıllı Kâr Koruma",
               f"{symbol} akıllı çıkış emri tamamlanamadı; pozisyon ve orijinal Stop/TP korunuyor. Aynı kimlikle tekrar denenecek.")
        return
    entry["status"] = "SENT"
    if result and result.get("orderId"):
        order_id = int(result["orderId"])
        known = plan.setdefault("exchange_order_ids", [])
        if order_id not in known:
            known.append(order_id)
        guard["close_order_id"] = order_id
    guard["close_reason"] = CLOSE_REASON
    plan["close_reason"] = CLOSE_REASON
    plan["status"] = "KAPATMA EMRİ GÖNDERİLDİ" if result else "KAPANDI"
    _alert(state, config, plan, "SMART_GUARD_CLOSED", "INFO", "Akıllı Kâr Koruma",
           f"{symbol} {CLOSE_REASON_LABEL} ile kapatıldı: "
           + ("ters yön sinyali (düşük puan gibi sayıldı) " if guard.get("low_reason") == "direction_mismatch"
              else f"güven puanı {guard.get('last_score')} (eşik {cfg['threshold']}) altında kaldı ")
           + f"ve net kâr {pnl['net']:.4f} USDT idi.")
    await cancel_temp_stop(client, state, config, plan, guard, reason="pozisyon kapatıldı")


async def _on_recovered(client: Any, state: dict[str, Any], config: dict[str, Any], plan: dict[str, Any],
                        guard: dict[str, Any], score: int | None) -> None:
    _alert(state, config, plan, "SMART_GUARD_RECOVERED", "INFO", "Akıllı Kâr Koruma",
           f"{plan['symbol']} güven puanı {score if score is not None else 'normal'} ile toparlandı. "
           "Sayaç sıfırlandı; geçici kâr koruma stopu varsa kaldırılıyor.")
    await cancel_temp_stop(client, state, config, plan, guard, reason="puan toparlandı")


def _alert_mismatch(state: dict[str, Any], config: dict[str, Any], plan: dict[str, Any], guard: dict[str, Any], cfg: dict[str, Any]) -> None:
    action = ("düşük puan gibi sayılıyor (sayaç işliyor)" if cfg["direction_mismatch_action"] == "count_as_low"
              else "yalnız kaydediliyor; sayaç ve çıkış kararı değişmiyor")
    _alert(state, config, plan, "SMART_GUARD_DIRECTION_MISMATCH", "WARNING", "Akıllı Kâr Koruma",
           f"{plan['symbol']} için strateji şimdi pozisyonun TERS yönünü gösteriyor (karşı yön güveni {guard.get('opposite_confidence')}). "
           f"Bu bir güven puanı değildir; {action}. Orijinal Stop/TP aynen çalışıyor.")


def _describe(events: list[str], guard: dict[str, Any], plan: dict[str, Any], cfg: dict[str, Any], candle: int) -> str:
    from datetime import datetime, timezone
    when = datetime.fromtimestamp(candle, timezone.utc).strftime("%H:%M")
    shown = ("ters yön (puan yok, karşı yön güveni %s)" % guard.get("opposite_confidence")
             if guard.get("direction_state") == "MISMATCH" else f"puan {guard.get('last_score')}")
    return (f"{plan['symbol']} 15 dk mum kapanışı gözlemi (mum {when} UTC): {shown}, eşik {cfg['threshold']}, "
            f"durum {guard.get('mode')}. Bu karar 15 dakikalık kapanmış mumlara bağlıdır; mum içinde değişmez.")


async def tick(application: Any, client: Any, state: dict[str, Any], snapshot: dict[str, Any],
               kline_fetch: Callable[[str, str, int], Awaitable[Any]] | None = None, *, now: float | None = None) -> None:
    """Ana döngüden çağrılır. Hata vermez: tek pozisyondaki sorun diğerlerini ve ana döngüyü durdurmaz.

    Ana sistem KAPALIYKEN (varsayılan) borsaya hiçbir istek ve emir gitmez; yalnız önceden kalmış geçici stop temizlenir."""
    from . import v25_execution as live
    from . import original_v2_live as original
    now = time.time() if now is None else now
    config = original.settings(state)
    if not config:
        return
    cfg = get_settings(config)
    plans = [p for p in (state.get("plans") or {}).values() if isinstance(p, dict) and original.plan_is_original(p)]
    off = (not cfg["enabled"]) or cfg["emergency_off"]
    positions = {}
    for row in snapshot.get("positions", []) or []:
        item = view_of(row)
        if item is not None:
            positions[item["symbol"]] = item
    for plan in plans:
        guard = plan.get(GUARD_KEY)
        try:
            active = live.live_plan_is_active(plan) and live.live_plan_can_mutate(plan)
            if not active:
                if isinstance(guard, dict) and guard.get("ledger") and not guard.get("finalized") and ledger_active(guard, "TEMP_STOP"):
                    await _finalize(client, state, config, plan, guard)
                continue
            if off and not isinstance(guard, dict):
                continue  # hiç açılmamış kayıt: kapalıyken hiçbir şey yapma
            guard = guard_of(plan)
            view = positions.get(str(plan.get("symbol") or "").upper())
            if off:
                if guard.get("temp_stop") or ledger_active(guard, "TEMP_STOP") or guard["mode"] != MODE_NORMAL:
                    if await cancel_temp_stop(client, state, config, plan, guard, reason="sistem kapalı"):
                        _reset_normal(guard)
                continue
            if view is None or snapshot.get("hedge_mode") is True \
                    or view["direction"] != str(plan.get("direction") or "").upper():
                continue
            status = str(plan.get("status") or "").upper()
            close_entry = ledger_active(guard, "CLOSE")
            if any(word in status for word in _ACTIVE_BLOCKED_STATUSES) or plan.get("protection_state") != "MATCHED" \
                    or not plan.get("original_tp_attempted") or (close_entry is not None and close_entry["status"] in {"SENT", "CONFIRMED"}):
                continue
            await _tick_plan(client, state, config, plan, guard, view, cfg, snapshot, kline_fetch, now)
        except Exception as exc:  # asla ana döngüyü düşürme
            logger.warning("SMART_GUARD_TICK_FAILED symbol=%s error=%s", plan.get("symbol"), type(exc).__name__)
            if isinstance(plan.get(GUARD_KEY), dict):
                plan[GUARD_KEY]["last_error"] = f"beklenmeyen hata: {type(exc).__name__}"
    live.persist_state(state)


async def _finalize(client: Any, state: dict[str, Any], config: dict[str, Any], plan: dict[str, Any], guard: dict[str, Any]) -> None:
    """Pozisyon kapandı: kalan geçici stop kaydını temizle ve sebebi yaz."""
    reason = str(plan.get("close_reason") or "")
    if await cancel_temp_stop(client, state, config, plan, guard, reason="pozisyon kapandı"):
        guard["finalized"] = True
        if reason == STOP_CLOSE_REASON:
            _alert(state, config, plan, "SMART_GUARD_TEMP_STOP_TRIGGERED", "INFO", "Akıllı Kâr Koruma",
                   f"{plan['symbol']} geçici kâr koruma stopu tetiklendi; pozisyon net kârda kapandı.")


async def _tick_plan(client: Any, state: dict[str, Any], config: dict[str, Any], plan: dict[str, Any], guard: dict[str, Any],
                     view: dict[str, Any], cfg: dict[str, Any], snapshot: dict[str, Any], kline_fetch: Any, now: float) -> None:
    from . import v25_execution as live
    symbol = plan["symbol"]
    at = candle_open(now)
    # 1) Yeni kapanmış mum varsa puanı yeniden hesapla (aynı mum tekrar okunmaz)
    if guard.get("last_score_candle") != at and now >= float(guard.get("score_retry_at") or 0):
        events, obs, why = await _observe_now(client, state, plan, guard, view, cfg, now, kline_fetch)
        score = obs["score"]
        if "SCORE_UNAVAILABLE" in events:
            guard["score_retry_at"] = now + SCORE_RETRY_SECONDS
            if _note_once(guard, "score_note_event", why):
                live.add_event(state, "SMART_GUARD_SCORE_UNAVAILABLE",
                               f"{symbol} güven puanı hesaplanamadı ({why}); otomatik çıkış yapılmaz.", symbol=symbol, plan_id=plan.get("id"))
        else:
            guard["score_note_event"] = None
            logger.info("SMART_GUARD_OBSERVATION symbol=%s candle=%s score=%s note=%s events=%s basis=15m_candle_close",
                        symbol, at, score, obs["note"], ",".join(events))
            if guard["mode"] != MODE_NORMAL or "RECOVERED" in events or obs["mismatch"] or "DIRECTION_MATCH_RESTORED" in events:
                live.add_event(state, "SMART_GUARD_OBSERVATION", _describe(events, guard, plan, cfg, at),
                               symbol=symbol, plan_id=plan.get("id"))
            if "DIRECTION_MISMATCH_STARTED" in events:
                _alert_mismatch(state, config, plan, guard, cfg)
            if "SCORE_LOW_STARTED" in events:
                what = ("strateji ters yönü gösteriyor (düşük puan gibi sayılıyor)" if obs["mismatch"]
                        else f"güven puanı {score}'e düştü (eşik {cfg['threshold']})")
                _alert(state, config, plan, "SMART_GUARD_LOW_SCORE", "WARNING", "Akıllı Kâr Koruma",
                       f"{symbol} {what}. Kâr koruma modu başladı; "
                       f"{cfg['wait_minutes']} dakikalık sayaç çalışıyor. Karar yalnızca 15 dakikalık mum kapanışına göre verilir; "
                       "zarardaysa pozisyon kapatılmaz.")
            if "EXIT_READY" in events:
                _alert(state, config, plan, "SMART_GUARD_EXIT_READY", "WARNING", "Akıllı Kâr Koruma",
                       f"{symbol} sayaç doldu ve sonraki kapanmış mumda da puan eşiğin altında. "
                       "Çıkışa hazır: net kâra geçince kapatılacak; zarardaysa beklenir.")
            if "RECOVERED" in events:
                await _on_recovered(client, state, config, plan, guard, score)
        await live.persist_state_awaited(state)
    # 1b) Miktar senkronu HER turda (borsa isteği yok): TP1/dış kısmi kapanıştan sonra eski miktarlı stop kalmasın
    stop = guard.get("temp_stop")
    if isinstance(stop, dict) and not stop.get("pending") and not stop.get("cancel_pending"):
        quantity = _d(stop.get("quantity"))
        if quantity is not None and quantity != view["quantity"]:
            if await cancel_temp_stop(client, state, config, plan, guard, reason="miktar değişti"):
                guard["last_check_at"] = None  # aşağıda yeni miktarla hemen yeniden kurulur
    # 2) Kontrol aralığında net kâr, geçici stop ve çıkış
    if guard["mode"] == MODE_NORMAL and not guard.get("temp_stop"):
        return
    if not checks_due(guard, now, cfg["check_interval_seconds"]):
        return
    guard["last_check_at"] = now
    if guard["mode"] == MODE_NORMAL:  # temizlenmemiş geçici stop kaldıysa temizle
        await cancel_temp_stop(client, state, config, plan, guard, reason="puan normal")
        return
    fee, slip = _fee_slippage(state)
    funding = await _funding_paid(client, plan, guard, now)
    pnl = net_pnl(view, fee_bps=fee, slippage_bps=slip, funding_paid=funding) if funding is not None else None
    guard["last_net_pnl"] = str(pnl["net"]) if pnl else None
    if pnl is None:
        guard["last_error"] = "net kâr hesaplanamadı (funding/fiyat verisi eksik); otomatik işlem yok"
        return
    if pnl["net"] > 0 and cfg["protect_stop_enabled"]:
        await ensure_temp_stop(client, state, config, plan, guard, view, pnl)
    if guard["mode"] == MODE_EXIT_READY:
        decision = exit_decision(guard, pnl["net"], smart_exit=cfg["smart_exit_enabled"])
        if decision == "CLOSE":
            if guard.get("waiting_profit"):
                guard["waiting_profit"] = False
                _alert(state, config, plan, "SMART_GUARD_NET_PROFIT", "INFO", "Akıllı Kâr Koruma",
                       f"{symbol} net kâra geçti ({pnl['net']:.4f} USDT) ve puan hâlâ eşiğin altında; kapatma doğrulanıyor.")
            await _exit(client, state, config, plan, guard, cfg, now, kline_fetch)
        elif decision == "WAIT_PROFIT":
            guard["waiting_profit"] = True
