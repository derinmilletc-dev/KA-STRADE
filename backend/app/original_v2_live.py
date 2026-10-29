"""OWNER-only Original V2 integration; signal policy remains the Demo policy."""
from __future__ import annotations

import copy
import asyncio
import logging
import math
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import original_v2_demo_execution as original
from .execution_core import configured_min_confidence, system_ceilings
from .market_universe import (
    ORIGINAL_DEFAULT_SYMBOL_COUNT,
    ORIGINAL_MAX_SYMBOL_COUNT,
    ORIGINAL_MIN_SYMBOL_COUNT,
    ORIGINAL_MIN_SYMBOL_AGE_DAYS,
    ranked_original_live_symbols,
)
from .strategies import original_v2_demo as strategy
from .strategies.contracts import StrategyResult
from .strategies import original_v2_pullback
from . import original_live_risk as risk_settings
from . import original_risk_capacity as risk_capacity
from . import scan_rejections
from . import live_access
from .maintenance import MAINTENANCE_SKIP_REASON, is_maintenance_rejection, maintenance_blocks_automation
from .v22_commercial import authenticated_user

router = APIRouter(prefix="/api/v25/original-v2", tags=["Original V2 LIVE"])
FLAG = "PROTREBOT_LIVE_KAIS_ORIGINAL_V2_ENABLED"
SOURCE = "ORIGINAL_V2_LIVE"
SCANNER_HISTORY_LIMIT = 128
SESSION_WARNING_SECONDS = 60 * 60  # müşteriye "1 saat kaldı" uyarısı + push
# Çıkış politikası (yalnız canlı Original V2): TP1, sinyalin TP1 mesafesinin %80'ine kurulur ve orada pozisyonun %70'i
# kapanır; kalan %30 değişmeyen ilk stop ve TP3 ile devam eder. Demo ve eski planlar %60 / 1R'de kalır.
TP1_PRICE_RATIO = Decimal("0.80")
TP1_FRACTION = Decimal("0.70")
# 70/30'un tam bölünmesi için miktar sembolün adımının 10 katına yuvarlanır (60/40'ta 5 idi).
SPLIT_STEPS = 10
# Strateji kimliği: kaldıraç 3x isolated. Aşağıdaki iki oran bakiyeye göre yüzdedir (sabit USDT değil).
ORIGINAL_LEVERAGE = 3
DEFAULT_MAX_MARGIN_PER_TRADE_PCT = 30.0   # tek işlemin teminatı güncel bakiyenin en fazla %30'u
DEFAULT_MAX_TOTAL_MARGIN_PCT = 80.0       # açık tüm pozisyonların toplam teminatı bakiyenin en fazla %80'i
MARGIN_PER_TRADE_ENV = "ORIGINAL_V2_MAX_MARGIN_PER_TRADE_PCT"
TOTAL_MARGIN_ENV = "ORIGINAL_V2_MAX_TOTAL_MARGIN_PCT"
# Stop kurulamazsa: ilk turdan sonra 90 sn arayla en fazla 3 yeniden deneme turu; hepsi başarısızsa pozisyon
# reduce-only kapatılır. Her tur kendi içinde install_verified_stop'un kısa (1-2 sn) denemelerini içerir.
STOP_REPAIR_RETRY_SECONDS = 90
STOP_REPAIR_MAX_RETRIES = 3


def _percent_setting(name: str, default: float) -> float:
    """Sunucu yapılandırması (ortam değişkeni); yoksa varsayılan. Geçersiz değer güvenli varsayılana döner."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.error("ORIGINAL_MARGIN_PERCENT_INVALID name=%s", name)
        return default
    if not math.isfinite(value) or not 0 < value <= 100:
        logger.error("ORIGINAL_MARGIN_PERCENT_OUT_OF_RANGE name=%s", name)
        return default
    return value


MARGIN_MODES = ("guvenli", "orta", "agresif")
MARGIN_MODE_LABELS = {"guvenli": "Güvenli", "orta": "Orta", "agresif": "Agresif"}
MARGIN_MODE_FIXED_PERCENT = {"orta": 50.0, "agresif": 65.0}
AGGRESSIVE_MARGIN_CONFIRMATION = "AGRESİF MODU KABUL EDİYORUM"


def margin_percent_limits(mode: str = "guvenli") -> tuple[float, float]:
    """(işlem başı %, toplam %). İşlem başı oran toplam orandan büyük olamaz.

    Güvenli mod bugünkü davranıştır (ortam değişkeni / varsayılan %30); Orta %50, Agresif %65.
    Toplam teminat sınırı (varsayılan %80) her modda aynen geçerlidir."""
    total = _percent_setting(TOTAL_MARGIN_ENV, DEFAULT_MAX_TOTAL_MARGIN_PCT)
    if mode not in MARGIN_MODES:
        raise ValueError("Bilinmeyen teminat modu")
    base = MARGIN_MODE_FIXED_PERCENT.get(mode)
    if base is None:
        base = _percent_setting(MARGIN_PER_TRADE_ENV, DEFAULT_MAX_MARGIN_PER_TRADE_PCT)
    return min(base, total), total


def margin_mode_percents() -> dict[str, float]:
    return {mode: margin_percent_limits(mode)[0] for mode in MARGIN_MODES}


def selected_margin_mode(config: dict[str, Any]) -> str:
    """Hesabın seçili modu. Kayıt yoksa Güvenli; bozuk/onaysız kayıt hata verir (sessizce düşürmez)."""
    mode = config.get("margin_mode", "guvenli")
    if mode not in MARGIN_MODES:
        raise HTTPException(423, "Teminat modu geçersiz; yeni giriş kapalı.")
    if mode == "agresif" and config.get("margin_mode_confirmed") is not True:
        raise HTTPException(423, "Agresif mod onayı kayıtlı değil; yeni giriş kapalı.")
    if mode in {"orta", "agresif"} and config.get("margin_mode_acknowledged") is not True:
        raise HTTPException(423, "Teminat modu onayı kayıtlı değil; yeni giriş kapalı.")
    return mode


def margin_mode_status() -> list[dict[str, Any]]:
    percents = margin_mode_percents()
    return [{"mode": mode, "label": MARGIN_MODE_LABELS[mode], "per_trade_percent": percents[mode],
             "total_percent": margin_percent_limits(mode)[1]} for mode in MARGIN_MODES]


def percent_text(fraction: Decimal) -> str:
    return f"%{int((fraction * 100).to_integral_value())}"


def scaled_targets(spec: dict[str, Any], tick: Decimal) -> list[str]:
    """Sinyalin TP1'ini girişe doğru %80'e çeker; TP2/TP3 ve stop aynen kalır.

    LONG: entry + (tp1 - entry) * 0.80 · SHORT: entry - (entry - tp1) * 0.80. Sinyal TP1'i girişin doğru
    tarafında değilse ya da yuvarlama sonrası seviye girişe/TP3'e çarparsa giriş açılmaz.
    """
    from . import v25_execution as live
    try:
        entry = Decimal(str(spec["entry_price"]))
        targets = [Decimal(str(value)) for value in spec["targets"]]
        direction = str(spec.get("direction") or "").upper()
        tick = Decimal(str(tick))
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise HTTPException(423, "Original V2 TP1 seviyesi hesaplanamadı; giriş açılmadı.") from exc
    if len(targets) != 3 or not all(value.is_finite() and value > 0 for value in (entry, tick, *targets)):
        raise HTTPException(423, "Original V2 TP1 seviyesi hesaplanamadı; giriş açılmadı.")
    signal_tp1 = targets[0]
    if direction == "LONG":
        valid = entry < signal_tp1 <= targets[2]
    elif direction == "SHORT":
        valid = targets[2] <= signal_tp1 < entry
    else:
        valid = False
    if not valid:
        raise HTTPException(423, "Original V2 sinyal TP1 seviyesi girişin doğru tarafında değil; giriş açılmadı.")
    tp1 = live.round_tick(entry + (signal_tp1 - entry) * TP1_PRICE_RATIO, tick)
    inside = entry < tp1 <= targets[2] if direction == "LONG" else targets[2] <= tp1 < entry
    if not inside:
        raise HTTPException(423, "Original V2 TP1 seviyesi fiyat adımına yuvarlanınca geçersiz kaldı; giriş açılmadı.")
    return [live.decimal_text(tp1), spec["targets"][1], spec["targets"][2]]
logger = logging.getLogger(__name__)


class Start(risk_settings.RiskChoice):
    model_config = ConfigDict(extra="forbid")
    confirmation: str = "ANLADIM"
    totp_code: str | None = Field(default=None, min_length=6, max_length=32, repr=False)
    symbol_count: int = Field(default=ORIGINAL_DEFAULT_SYMBOL_COUNT, ge=ORIGINAL_MIN_SYMBOL_COUNT, le=ORIGINAL_MAX_SYMBOL_COUNT, strict=True)
    session_hours: int = Field(default=1, strict=True)

    @field_validator("session_hours")
    @classmethod
    def validate_session_hours(cls, value: int) -> int:
        if value not in {1, 4, 12, 24}:
            raise ValueError("Original V2 oturumu 1, 4, 12 veya 24 saat olabilir.")
        return value

class ExtendSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_hours: int = Field(strict=True)
    understood: bool = Field(default=False, strict=True)

    @field_validator("session_hours")
    @classmethod
    def validate_session_hours(cls, value: int) -> int:
        if value not in {1, 4, 12, 24}:
            raise ValueError("Original V2 oturumu 1, 4, 12 veya 24 saat olabilir.")
        return value


class MarginModeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(max_length=16)
    acknowledged: bool = Field(default=False, strict=True)
    confirmation: str = Field(default="", max_length=64, repr=False)
    understood: bool = Field(default=False, strict=True)


class SmartProfitUpdate(BaseModel):
    """Akıllı Kâr Koruma ayarları; yalnız gönderilen alanlar değişir. Orijinal Stop/TP hiçbir ayarla kapanmaz."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = Field(default=None, strict=True)
    threshold: int | None = Field(default=None, strict=True, ge=50, le=95)
    wait_minutes: int | None = Field(default=None, strict=True, ge=1, le=120)
    check_interval_seconds: int | None = Field(default=None, strict=True, ge=30, le=300)
    protect_stop_enabled: bool | None = Field(default=None, strict=True)
    smart_exit_enabled: bool | None = Field(default=None, strict=True)
    emergency_off: bool | None = Field(default=None, strict=True)
    direction_mismatch_action: Literal["record_only", "count_as_low"] | None = None
    understood: bool | None = Field(default=None, strict=True)  # AÇMAK için zorunlu: gerçek emir gönderebileceğini anladığını onaylar


class FillLockApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lock_id: str = Field(min_length=1, max_length=64)
    confirmation: str
    totp_code: str | None = Field(default=None, max_length=32, repr=False)  # artık gerekmiyor; eski istemciler için kabul edilir


def fill_locked(state: dict[str, Any]) -> bool:
    return (settings(state).get("fill_lock") or {}).get("active") is True


def require_fill_unlocked(state: dict[str, Any]) -> None:
    if fill_locked(state):
        raise HTTPException(423, "Original V2 dolum güvenlik kilidi aktif; OWNER onayı olmadan yeni LIVE giriş açılamaz.")


def available() -> bool:
    value = os.getenv(FLAG, "false").strip().lower()
    if value not in {"true", "false", "1", "0"}:
        raise HTTPException(503, "Original V2 LIVE ayarı geçersiz.")
    return value in {"true", "1"}


def settings(state: dict[str, Any]) -> dict[str, Any]:
    return state.get("original_v2_live", {})


def running(state: dict[str, Any]) -> bool:
    return settings(state).get("enabled") is True


def _valid_symbol_count(config: dict[str, Any]) -> int:
    count = config.get("symbol_count", ORIGINAL_DEFAULT_SYMBOL_COUNT)
    if type(count) is not int or not ORIGINAL_MIN_SYMBOL_COUNT <= count <= ORIGINAL_MAX_SYMBOL_COUNT:
        raise HTTPException(503, "Original V2 sembol sayısı geçersiz; yeni taramalar kapalı.")
    return count


def _today_universe(config: dict[str, Any]) -> dict[str, Any] | None:
    universe = config.get("universe")
    if not isinstance(universe, dict) or universe.get("updated_day") != datetime.now(timezone.utc).date().isoformat():
        return None
    if universe.get("minimum_age_days") != ORIGINAL_MIN_SYMBOL_AGE_DAYS:
        return None
    ranked = universe.get("ranked_symbols")
    if (
        not isinstance(ranked, list)
        or len(ranked) > ORIGINAL_MAX_SYMBOL_COUNT
        or any(not isinstance(symbol, str) or not symbol.isascii() or not symbol.isalnum() or not symbol.isupper() or not symbol.endswith("USDT") for symbol in ranked)
        or len(set(ranked)) != len(ranked)
    ):
        return None
    eligible_count = universe.get("eligible_count")
    if type(eligible_count) is not int or eligible_count < len(ranked):
        return None
    return universe


def _scanner_history_summary(scanner: dict[str, Any], now: float) -> dict[str, int]:
    history = scanner.get("history")
    if not isinstance(history, list):
        return {"scans": 0, "candidates": 0, "trades": 0}
    recent = []
    for item in history:
        if not isinstance(item, dict):
            continue
        at = item.get("at")
        candidates = item.get("candidates")
        trades = item.get("trades")
        if not isinstance(at, (int, float)) or isinstance(at, bool):
            continue
        try:
            at_value = float(at)
        except (OverflowError, ValueError):
            continue
        if (
            math.isfinite(at_value) and now - 86400 <= at_value <= now
            and type(candidates) is int and candidates >= 0
            and type(trades) is int and trades >= 0
        ):
            recent.append({**item, "at": at_value})
    return {
        "scans": len(recent),
        "candidates": sum(item["candidates"] for item in recent),
        "trades": sum(item["trades"] for item in recent),
    }


def _record_scanner_scan(scanner: dict[str, Any], at: float, candidates: int,
                         rejections: Any = None, checks: Any = None) -> dict[str, Any]:
    history = scanner.get("history")
    if not isinstance(history, list):
        history = []
    item: dict[str, Any] = {"at": at, "candidates": candidates, "trades": 0}
    if rejections is not None:
        try:
            # Yalnızca bilgi amaçlı döküm: hata olursa tarama ve emirler etkilenmez.
            item.update(scan_rejections.history_fields(rejections, checks))
        except Exception:
            logger.warning("SCAN_REJECTION_RECORD_FAILED")
    history.append(item)
    scanner["history"] = history[-SCANNER_HISTORY_LIMIT:]
    return scanner["history"][-1]


def selected_symbols(config: dict[str, Any]) -> list[str]:
    universe = _today_universe(config)
    if universe is None:
        return []
    return universe["ranked_symbols"][:_valid_symbol_count(config)]


async def resolve_live_universe(application: Any, client: Any, config: dict[str, Any]) -> list[str]:
    from . import v25_execution as live
    universe = _today_universe(config)
    if universe is None:
        info = await client.public_get("/fapi/v1/exchangeInfo")
        tickers = await client.public_get("/fapi/v1/ticker/24hr")
        book_tickers = await client.public_get("/fapi/v1/ticker/bookTicker")
        filter_counts: dict[str, Any] = {}
        try:
            ranked, eligible_count = ranked_original_live_symbols(
                info, tickers, book_tickers, now_ms=int(time.time() * 1000),
                diagnostics=filter_counts,
            )
        except ValueError:
            config["universe_filter_failure"] = filter_counts
            raise
        universe = {
            "updated_day": datetime.now(timezone.utc).date().isoformat(),
            "updated_at": live.now_iso(),
            "ranked_symbols": ranked,
            "eligible_count": eligible_count,
            "minimum_age_days": ORIGINAL_MIN_SYMBOL_AGE_DAYS,
            "filter_counts": filter_counts,
        }
        config.pop("universe_filter_failure", None)
        config["universe"] = universe
    symbols = universe["ranked_symbols"][:_valid_symbol_count(config)]
    service = getattr(application.state, "public_market_data", None)
    if service is not None:
        service.set_original_symbols(shared_original_symbols(application, symbols))
    await configure_original_kline_stream(application, symbols)
    universe["selected_symbols"] = symbols
    return symbols


def shared_original_symbols(application: Any, symbols: list[str]) -> list[str]:
    """The market-data stream is shared by all members. With multi-user LIVE it must carry the
    union of every member's symbols, so one member can never switch off another member's feed."""
    from . import live_registry
    if not live_registry.enabled():
        return symbols
    demands = getattr(application.state, "original_symbol_demands", None)
    if not isinstance(demands, dict):
        demands = {}
        application.state.original_symbol_demands = demands
    demands[live_registry.CURRENT_USER.get() or ""] = list(symbols)
    union: list[str] = []
    for wanted in demands.values():
        for symbol in wanted:
            if symbol not in union:
                union.append(symbol)
    return union[:100]


async def configure_original_kline_stream(application: Any, symbols: list[str]) -> None:
    stream = getattr(application.state, "original_kline_stream", None)
    if stream is None:
        return
    from .public_market_data import ORIGINAL_INTERVALS
    from . import live_registry
    if not live_registry.enabled():
        await stream.configure(symbols, ORIGINAL_INTERVALS)
        return
    lock = getattr(application.state, "original_demand_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        application.state.original_demand_lock = lock
    async with lock:  # compute the union and apply it without another member interleaving
        await stream.configure(shared_original_symbols(application, symbols), ORIGINAL_INTERVALS)


def plan_is_original(plan: dict[str, Any]) -> bool:
    return plan.get("source") == SOURCE


def preserve(payload: dict[str, Any]) -> dict[str, Any] | None:
    value = payload.get("original_v2_live")
    if not isinstance(value, dict):
        return None
    result = copy.deepcopy(value)
    result["enabled"] = False
    result["paused"] = True
    result["pause_reason"] = "Yeniden başlatma sonrası risk onayı ve ARM gerekli."
    return result


def public_config(state: dict[str, Any]) -> dict[str, Any]:
    config = settings(state)
    symbols = selected_symbols(config)
    cached_universe = _today_universe(config)
    universe = {
        "requested_count": _valid_symbol_count(config),
        "selected_count": len(symbols),
        "eligible_count": int(cached_universe.get("eligible_count", 0)) if cached_universe is not None else 0,
        "minimum_age_days": ORIGINAL_MIN_SYMBOL_AGE_DAYS,
        "updated_at": cached_universe.get("updated_at") if cached_universe is not None else None,
        "filter_counts": (
            copy.deepcopy(cached_universe.get("filter_counts", {}))
            if cached_universe is not None
            else copy.deepcopy(config.get("universe_filter_failure", {}))
        ),
    }
    scanner = copy.deepcopy(config.get("scanner"))
    if isinstance(scanner, dict):
        scanner["last_24h"] = _scanner_history_summary(scanner, time.time())
        scanner["rejections_24h"] = scan_rejections.summarize(scanner.get("history"), time.time())
    expires = float(config.get("session_expires_at_epoch") or ((state.get("auto") or {}).get("session_until") if running(state) else 0) or 0)
    return {"available": available(), "enabled": running(state),
            "paused": config.get("paused", False), "pause_reason": config.get("pause_reason"),
            "risk_percent": config.get("risk_percent", 0.5), "daily_loss_percent": config.get("daily_loss_percent", 5),
            "risk_revision": config.get("risk_revision", 0),
            "risk_limits": risk_settings.limits(state),
            "margin_mode": settings(state).get("margin_mode", "guvenli"),
            "margin_modes": margin_mode_status(),
            "smart_profit": smart_profit_public(state),
            "risk_projection": copy.deepcopy(config.get("risk_projection")),
            "last_risk_adjustment": copy.deepcopy(config.get("last_risk_adjustment")),
            "daily": copy.deepcopy(config.get("daily")),
            "fill_lock": copy.deepcopy(config.get("fill_lock")),
            "scanner": scanner,
            "universe": universe,
            "session": {"hours": config.get("session_hours", 1),
                        "started_at": config.get("session_started_at"),
                        "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat() if expires else None,
                        "remaining_seconds": max(0, int(expires - time.time())) if running(state) else 0,
                        "authority_limited": config.get("session_authority_limited", False),
                        "warning_sent": bool(config.get("session_warning_sent"))},
            "symbols": symbols}


def smart_profit_public(state: dict[str, Any]) -> dict[str, Any]:
    """Ayarlar + açık pozisyonların koruma durumu (yalnız okuma)."""
    from . import original_profit_guard as guard_module
    config = settings(state)
    positions = []
    for plan in (state.get("plans") or {}).values():
        if not isinstance(plan, dict) or not plan_is_original(plan):
            continue
        guard = plan.get(guard_module.GUARD_KEY)
        if not isinstance(guard, dict) or str(plan.get("status") or "").upper() in _TERMINAL_PLAN_STATUSES:
            continue
        stop = guard.get("temp_stop") if isinstance(guard.get("temp_stop"), dict) else None
        positions.append({
            "plan_id": plan.get("id"), "symbol": plan.get("symbol"), "direction": plan.get("direction"),
            "mode": guard.get("mode"), "last_score": guard.get("last_score"),
            "last_score_candle": guard.get("last_score_candle"), "first_low_at": guard.get("first_low_at"),
            "timer_end": guard.get("timer_end"), "exit_ready": bool(guard.get("exit_ready")),
            "waiting_profit": bool(guard.get("waiting_profit")),
            "direction_state": guard.get("direction_state"), "opposite_confidence": guard.get("opposite_confidence"),
            "low_reason": guard.get("low_reason"),
            "temp_stop_price": stop.get("price") if stop else None,
            "last_net_pnl": guard.get("last_net_pnl"), "last_check_at": guard.get("last_check_at"),
            "last_error": guard.get("last_error"), "basis": "15m_candle_close",
        })
    return {"settings": guard_module.get_settings(config), "defaults": dict(guard_module.DEFAULT_SETTINGS),
            "basis": "15m_candle_close", "positions": positions}


def guard_kline_fetch(application: Any, client: Any) -> Any:
    """Akıllı Kâr Koruma için mum kaynağı: varsa akış, yoksa/abone değilse REST. Strateji verisi aynı."""
    stream = getattr(getattr(application, "state", None), "original_kline_stream", None)
    if stream is None:
        return None

    async def fetch(symbol: str, interval: str, limit: int) -> Any:
        async def rest(params: dict[str, Any]) -> Any:
            return await client.public_get("/fapi/v1/klines", params)
        if not stream.is_subscribed(symbol, interval):
            return await rest({"symbol": symbol, "interval": interval, "limit": limit})
        return await stream.get_candles(symbol, interval, limit, rest)
    return fetch


TRANSIENT_PAUSE_REASON_PREFIX = "Canlı salt-okunur veri geçici olarak alınamadı"
TRANSIENT_RESUME_WINDOW_SECONDS = 600.0


def pause(state: dict[str, Any], reason: str, *, note: str | None = None) -> None:
    from . import v25_execution as live
    config = settings(state)
    if not config:
        return
    already = config.get("paused") and config.get("pause_reason") == reason
    config.update(enabled=False, paused=True, pause_reason=reason)
    if not already:
        config["paused_at_epoch"] = time.time()
    state["auto"].update(enabled=False, session_until=0.0)
    state.update(live_auto_trade=False, real_trading_locked=True, armed_until=0.0)
    if not already:
        # Varsayılan cümle aynen korunur; yalnız stop-kurulamadı akışı kendi (doğru) notunu verir.
        suffix = note if note is not None else "Açık pozisyon otomatik kapatılmadı."
        event = live.add_event(state, "ORIGINAL_LIVE_PAUSED",
                               f"Original V2 LIVE durduruldu: {reason}. {suffix}",
                               recipient_user_id=config.get("owner_id"), source=SOURCE)
        config.setdefault("alerts", []).insert(0, {
            "id": "original-live:" + event["id"], "type": "ORIGINAL_LIVE_PAUSED", "severity": "CRITICAL",
            "title": "Original V2 LIVE güvenlik kilidi", "message": event["message"],
            "timestamp": event["created_at"], "target": "master-trade", "read": False,
        })
        del config["alerts"][100:]
    live.persist_state(state)


def stop_repair_wait(plan: dict[str, Any]) -> bool:
    """Önceki başarısız stop turundan beri 90 sn dolmadıysa True: bu turda stop yeniden denenmez.

    Alan bozuk ya da sayı değilse bekleme yok sayılır (koruma kurmayı geciktirmek yerine denemeye devam)."""
    retry_at = plan.get("original_stop_retry_at")
    if isinstance(retry_at, bool) or not isinstance(retry_at, (int, float)) or not math.isfinite(retry_at):
        return False
    return time.time() < float(retry_at)


def clear_stop_failures(plan: dict[str, Any]) -> None:
    """Stop doğrulandı: sayaç ve bekleme sıfırlanır (stop sonradan silinirse sayım baştan başlar)."""
    for key in ("original_stop_failures", "original_stop_retry_at", "original_stop_close_requested"):
        plan.pop(key, None)


def record_stop_failure(state: dict[str, Any], plan: dict[str, Any], symbol: str, message: str) -> bool:
    """Original planın stop turu başarısız oldu.

    False: 90 sn sonra yeni tur denenecek (yeni girişler kilitli, pozisyon açık kalır).
    True: ilk tur + 3 yeniden deneme tükendi; çağıran pozisyonu reduce-only kapatmalıdır.
    """
    previous = plan.get("original_stop_failures")
    failures = (previous if isinstance(previous, int) and not isinstance(previous, bool) and previous > 0 else 0) + 1
    plan["original_stop_failures"] = failures
    plan.update(stop_install_failed=True, protection_state="MISSING", last_error=message)
    if failures > STOP_REPAIR_MAX_RETRIES:
        plan.pop("original_stop_retry_at", None)
        plan["original_stop_close_requested"] = True
        plan["status"] = "STOP BAŞARISIZ · KAPATILIYOR"
        pause(state, f"{symbol} Stop {STOP_REPAIR_MAX_RETRIES} yeniden denemeden sonra da kurulamadı: {message}",
              note="Korumasız kalmasın diye pozisyon reduce-only güvenlik kapatmasıyla kapatılıyor.")
        return True
    plan["original_stop_retry_at"] = time.time() + STOP_REPAIR_RETRY_SECONDS
    plan["status"] = "STOP BAŞARISIZ · YENİ GİRİŞLER KİLİTLİ"
    pause(state, f"{symbol} Stop kurulamadı: {message}",
          note=f"Açık pozisyon şimdilik kapatılmadı; stop {STOP_REPAIR_RETRY_SECONDS} saniye arayla en fazla "
               f"{STOP_REPAIR_MAX_RETRIES} kez yeniden denenecek, kurulamazsa pozisyon kapatılacak.")
    return False


def resume_after_transient_pause(state: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    """Kısa bir salt-okunur veri/ağ kesintisi yüzünden duraklayan oturumu, hesap temiz ve
    korumalı doğrulandığında kendiliğinden sürdürür. Başka hiçbir duraklatma nedeni için çalışmaz."""
    from . import v25_execution as live
    config = settings(state)
    if not config or config.get("paused") is not True or config.get("enabled") is True:
        return False
    if not str(config.get("pause_reason") or "").startswith(TRANSIENT_PAUSE_REASON_PREFIX):
        return False
    now = time.time()
    paused_at = float(config.get("paused_at_epoch") or 0)
    expires = float(config.get("session_expires_at_epoch") or 0)
    if paused_at <= 0 or now - paused_at > TRANSIENT_RESUME_WINDOW_SECONDS or expires <= now:
        return False
    emergency = state.get("emergency") if isinstance(state.get("emergency"), dict) else {}
    if emergency.get("active") is True or state.get("reconciliation_required") or state.get("execution_state") != "LOCKED":
        return False
    if fill_locked(state) or live.account_rate_limit_active(state):
        return False
    if not isinstance(snapshot, dict) or snapshot.get("open_algo_orders_available") is not True \
            or snapshot.get("algo_orders_quality") not in {"VALID_EMPTY", "VALID_ORDERS"}:
        return False
    positions = snapshot.get("positions") if isinstance(snapshot.get("positions"), list) else None
    if positions is None:
        return False
    accounted = live.positions_fully_accounted_by_confirmed_plans(state, snapshot)
    if positions and not accounted:
        return False
    if live.unresolved_execution_evidence(state, clean_reconciliation_snapshot=True, allow_confirmed_active_plans=accounted):
        return False
    if not (state.get("auto_authorization") or {}).get("user_id"):
        return False
    config.update(enabled=True, paused=False, pause_reason=None)
    state["auto"].update(enabled=True, session_until=expires, last_skip_reason=None, last_error=None)
    state["auto_authorization"]["expires_at_epoch"] = expires
    state.update(live_auto_trade=True, real_trading_locked=False, armed_until=0.0)
    live.add_event(state, "ORIGINAL_LIVE_RESUMED",
                   "Kısa veri kesintisi düzeldi; hesap ve korumalar doğrulandı, Original V2 oturumu kaldığı yerden devam ediyor.",
                   recipient_user_id=config.get("owner_id"), source=SOURCE)
    return True


def check_session_time(state: dict[str, Any]) -> None:
    from . import v25_execution as live
    if not running(state):
        return
    config = settings(state)
    expires = float(config.get("session_expires_at_epoch") or (state.get("auto") or {}).get("session_until") or 0)
    remaining = expires - time.time()
    if remaining <= 0:
        pause(state, "Original V2 oturum süresi doldu; yeni oturum için ARM ve güncel 2FA gerekli.")
    elif remaining <= SESSION_WARNING_SECONDS and not config.get("session_warning_sent"):
        config["session_warning_sent"] = True
        event = live.add_event(state, "ORIGINAL_SESSION_EXPIRING", "Canlı oturum 1 saat içinde bitecek; otomatik yenilenmez. Süreyi Canlı İşlem ekranındaki \"Oturumu uzat\" ile uzatabilirsin; yoksa bittiğinde yeniden başlat.", recipient_user_id=config.get("owner_id"), source=SOURCE)
        config.setdefault("alerts", []).insert(0, {
            "id": "original-live:" + event["id"], "type": "ORIGINAL_SESSION_EXPIRING", "severity": "WARNING",
            "title": "Original V2 oturumu bitmek üzere", "message": event["message"],
            "timestamp": event["created_at"], "target": "master-trade", "read": False,
        })
        del config["alerts"][100:]
        live.persist_state(state)


def merge_bell(request: Request, items: list[dict[str, Any]], *, read_id: str | None = None,
               read_all: bool = False, limit: int = 100) -> list[dict[str, Any]]:
    from . import v25_execution as live
    from .live_notifications import merge_bell as merge_live_bell
    if read_id and read_id.startswith("live:"):
        items = merge_live_bell(request, items, read_id=read_id, limit=limit)
        read_id = None
    else:
        items = merge_live_bell(request, items, read_all=read_all, limit=limit)
    application = getattr(request, "app", None)
    state = getattr(getattr(application, "state", None), "v25_execution", {})
    config = settings(state)
    if not config:
        if read_id:
            raise HTTPException(404, "Bildirim bulunamadı.")
        return items
    user = authenticated_user(request)
    if not live_access.role_ok(user.get("role")) or user["id"] != config.get("owner_id"):
        if read_id:
            raise HTTPException(404, "Bildirim bulunamadı.")
        return items
    alerts = config.get("alerts", [])
    if read_id and not any(item["id"] == read_id for item in alerts):
        raise HTTPException(404, "Bildirim bulunamadı.")
    for alert in alerts:
        if read_all or alert["id"] == read_id:
            alert["read"] = True
    if read_id or read_all:
        live.persist_state(request.app.state.v25_execution)
    return sorted([*items, *copy.deepcopy(alerts)], key=lambda item: str(item.get("timestamp") or ""), reverse=True)[:limit]


async def require_owner_authority(application: Any, state: dict[str, Any]) -> None:
    from . import v25_execution as live
    from .v22_commercial import validate_authoritative_session
    authorization = state.get("auto_authorization") or {}
    if authorization.get("user_id") != settings(state).get("owner_id"):
        raise HTTPException(403, "Original V2 OWNER oturumu değişti.")
    if not await live.background_authorization_active(application, state):
        raise HTTPException(403, "Original V2 OWNER oturumu doğrulanamadı.")
    pool = getattr(application.state, "db_pool", None)
    if pool is None:
        raise HTTPException(503, "Original V2 OWNER yetkisi doğrulanamadı.")
    user = await validate_authoritative_session(
        application, settings(state)["owner_id"], authorization.get("auth_version"),
    )
    if not live_access.role_ok(user["role"]):
        raise HTTPException(403, "Original V2 yalnız LIVE yetkili hesap tarafından çalıştırılabilir.")
    if authorization.get("fingerprint") != settings(state).get("fingerprint"):
        raise HTTPException(423, "Original V2 LIVE hesap bağlantısı değişti.")


async def daily_guard(client: Any, state: dict[str, Any], snapshot: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    start_ms = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    config = settings(state)
    wallet = float(snapshot["wallet_balance"])
    unrealized = float(snapshot["unrealized_pnl"])
    if not all(math.isfinite(value) for value in (wallet, unrealized)) or wallet <= 0:
        raise HTTPException(423, "Günlük bakiye doğrulanamadı.")
    rows = []
    for page in range(1, 9):
        batch = await client.signed("GET", "/fapi/v1/income", {
            "startTime": start_ms, "endTime": end_ms, "limit": 1000, "page": page,
        })
        if not isinstance(batch, list):
            raise HTTPException(423, "Günlük hesap hareketleri doğrulanamadı.")
        rows.extend(batch)
        if len(batch) < 1000:
            break
    else:
        raise HTTPException(423, "Günlük hesap hareketleri eksik; yeni girişler kapalı.")
    seen, total, pnl = set(), Decimal(0), Decimal(0)
    for row in rows:
        if (not isinstance(row, dict) or row.get("asset") != "USDT"
                or not {"incomeType", "tranId", "time", "income"}.issubset(row)):
            raise HTTPException(423, "USDT günlük kayıp hesabı doğrulanamadı.")
        key = (str(row["incomeType"]), str(row["tranId"]))
        if key in seen or not start_ms <= int(row["time"]) <= end_ms:
            raise HTTPException(423, "Günlük hesap hareketlerinde çakışma var.")
        seen.add(key)
        amount = Decimal(str(row["income"]))
        if not amount.is_finite():
            raise HTTPException(423, "Günlük hesap hareketi geçersiz.")
        total += amount
        if row["incomeType"] not in {"TRANSFER", "INTERNAL_TRANSFER"}:
            pnl += amount
    day = now.date().isoformat()
    day_state = config.get("daily", {})
    if day_state.get("day") != day:
        opening = float(Decimal(str(wallet)) - total)
        if not math.isfinite(opening) or opening <= 0:
            raise HTTPException(423, "Gün başlangıç bakiyesi doğrulanamadı.")
        day_state = {"day": day, "opening_wallet": opening, "locked": False}
        config["daily"] = day_state
    limit = day_state["opening_wallet"] * config["daily_loss_percent"] / 100
    loss = max(0, -float(pnl) - min(0, unrealized))
    # Gerçekleşmiş net zarar: gerçekleşen PnL + komisyon + fonlama (transferler hariç, yukarıda). Kapıda ayrıca kullanılır.
    day_state.update(loss=loss, limit=limit, realized_loss=max(0.0, -float(pnl)))
    if loss >= limit:
        day_state["locked"] = True
    if day_state["locked"]:
        raise HTTPException(423, "Günlük kayıp sınırı aşıldı; bugün yeni işlem açılmaz.")


def execution_policy(state: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    from pydantic import ValidationError
    config = settings(state)
    try:
        risk_settings.RiskChoice(
            risk_percent=config.get("risk_percent", 0.5),
            daily_loss_percent=config.get("daily_loss_percent", 5),
            risk_acknowledged=config.get("risk_acknowledged", False),
            high_risk_confirmation=risk_settings.HIGH_RISK_CONFIRMATION if config.get("high_risk_confirmed") is True else "",
        )
    except ValidationError as exc:
        logger.error("ORIGINAL_RISK_CONSENT_INVALID")
        raise HTTPException(423, "Original V2 risk sınırları veya yüksek risk onayı geçersiz; yeni giriş kapalı.") from exc
    fixed = strategy.fixed_profile()["execution_policy"]
    symbols = selected_symbols(settings(state))
    if not symbols:
        raise HTTPException(423, "Original V2 günlük sembol listesi hazır değil; yeni girişler kapalı.")
    current = state["policy"]
    result = {**current, **fixed, "max_stop_distance_pct": 6}
    result["allowed_symbols"] = symbols
    for key in ("max_positions", "max_same_direction_positions", "max_spread_bps"):
        result[key] = min(current[key], fixed[key])
    # Bakiye ve seçilen yüzdelerden dinamik: sabit USDT (3 / 25 / 350 / 10) bu hesabı kısıtlamaz.
    # Core sert üst sınırları sanitize_execution_policy ile ayrıca uygulanmaya devam eder.
    wallet = float(snapshot["wallet_balance"])
    if not math.isfinite(wallet) or wallet <= 0:
        raise HTTPException(423, "Güncel Binance bakiyesi doğrulanamadı; yeni giriş kapalı.")
    per_trade_pct, total_pct = margin_percent_limits(selected_margin_mode(config))
    ceilings = system_ceilings()
    # Dinamik değerler sistem tavanlarıyla sınırlanır; kırpma olursa sebebi plan/ekran bilgisinde görünür (bkz. risk_adjustment).
    result["max_margin_per_trade"] = min(wallet * per_trade_pct / 100, ceilings["max_margin_per_trade_usdt"])
    result["max_total_exposure_usdt"] = min(wallet * total_pct / 100 * ORIGINAL_LEVERAGE, ceilings["max_total_exposure_usdt"])
    result["max_total_margin_usdt"] = result["max_total_exposure_usdt"] / ORIGINAL_LEVERAGE
    # Sahip kararı: canlı Original V2'de günlük işlem sayısı ve ardışık kayıp sınırı yok (0 = sınırsız).
    # Pozisyon, yön, maruziyet, günlük kayıp ve açık zarar kilitleri ile her işlemin stop-loss'u aynen çalışır.
    result["daily_trade_limit"] = 0
    result["consecutive_loss_limit"] = 0
    result["max_direction_exposure_usdt"] = current.get("max_direction_exposure_usdt")
    for key in ("allow_long", "allow_short"):
        result[key] = current[key] and fixed[key]
    result["max_trap_score"] = min(current["max_trap_score"], fixed["max_trap_score"])
    day = config.get("daily") if isinstance(config.get("daily"), dict) else {}
    opening = day.get("opening_wallet") if day.get("day") == datetime.now(timezone.utc).date().isoformat() else None
    reference = float(opening) if isinstance(opening, (int, float)) and not isinstance(opening, bool) and opening > 0 else wallet
    result["daily_loss_limit"] = min(reference * float(config.get("daily_loss_percent", 5)) / 100,
                                     system_ceilings()["max_daily_loss_usdt"])
    for key in ("min_confidence", "fee_bps_per_side", "slippage_bps_per_side", "minimum_net_reward_usdt"):
        result[key] = max(current[key], fixed[key])
    # Sunucu ayarı (PROTREBOT_MIN_CONFIDENCE) canlı girişte de alt sınırdır; yalnız yükseltebilir, %80'in altına indiremez.
    result["min_confidence"] = max(int(result["min_confidence"]), configured_min_confidence())
    result["max_loss_per_trade"] = min(wallet * float(config["risk_percent"]) / 100,
                                      system_ceilings()["max_loss_per_trade_usdt"])
    return result


async def build_spec(client: Any, result: StrategyResult, state: dict[str, Any],
                     snapshot: dict[str, Any], *,
                     check_results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    from . import v25_execution as live
    symbols = selected_symbols(settings(state))
    try:
        original.validate_live_result(result, symbols)
    except HTTPException as exc:
        detail = str(exc.detail)
        live.record_live_entry_check(check_results, "strategy_signal", False, detail, symbol=result.signal.symbol)
        raise live.LiveExchangeError(detail, http_status=exc.status_code, check_key="strategy_signal") from exc
    if not result.signal.strategy_eligible:
        detail = "Original V2 sinyali girişe uygun değil."
        live.record_live_entry_check(check_results, "strategy_signal", False, detail, symbol=result.signal.symbol)
        raise live.LiveExchangeError(detail, http_status=423, check_key="strategy_signal")
    live.record_live_entry_check(check_results, "strategy_signal", True, "Original V2 sinyali güncel ve girişe uygun.", symbol=result.signal.symbol)
    try:
        spec = await original.build_live_spec(client, result, symbols)
    except HTTPException as exc:
        detail = str(exc.detail)
        live.record_live_entry_check(check_results, "strategy_sizing", False, detail, symbol=result.signal.symbol)
        raise live.LiveExchangeError(detail, http_status=exc.status_code, check_key="strategy_sizing") from exc
    policy = execution_policy(state, snapshot)
    entry, stop = Decimal(spec["entry_price"]), Decimal(spec["stop_loss"])
    budget = Decimal(str(policy["max_loss_per_trade"]))
    rules = await live.live_symbol_rules(client, spec["symbol"], "MARKET")
    # TP1 girişe doğru %80'e çekilir ve plan kendi %70 oranını taşır; TP2/TP3 ve stop değişmez.
    spec["targets"] = scaled_targets(spec, rules["tick"])
    spec["tp1_fraction"] = str(TP1_FRACTION)
    # The percentage budget includes estimated commission and slippage, not only stop distance.
    cost_per_unit = entry * Decimal(str(policy["fee_bps_per_side"] + policy["slippage_bps_per_side"])) * 2 / 10000
    # Tamamen dinamik: miktar yalnız seçilen risk bütçesi (bakiye x risk %) ve bakiye yüzdesi teminat tavanından gelir.
    risk_quantity = budget / (abs(entry - stop) + cost_per_unit)
    margin_quantity = Decimal(str(policy["max_margin_per_trade"])) * ORIGINAL_LEVERAGE / entry
    unrounded_quantity = min(risk_quantity, margin_quantity)
    quantity = live.floor_step(unrounded_quantity, rules["step"] * SPLIT_STEPS)
    size_metrics = original.minimum_order_parts(
        quantity, entry, rules["step"], rules["min_qty"], rules["min_notional"], TP1_FRACTION,
    )
    if not size_metrics["passes"]:
        live.record_live_entry_check(
            check_results, "minimum_quantity", False,
            f"Toplam miktar, TP1 {percent_text(TP1_FRACTION)} veya kalan miktar Binance minimum miktar/notional şartını karşılamıyor.",
            symbol=spec["symbol"],
        )
        raise live.MinimumTradeSizeError(spec["symbol"], {
            key: size_metrics[key] for key in (
                "total_quantity", "total_notional_usdt", "tp1_quantity", "tp1_notional_usdt",
                "remaining_quantity", "remaining_notional_usdt", "min_qty", "min_notional_usdt",
            )
        })
    live.record_live_entry_check(
        check_results, "minimum_quantity", True,
        f"Toplam miktar, TP1 {percent_text(TP1_FRACTION)} ve kalan miktar Binance minimum miktar/notional şartını karşılıyor.",
        symbol=spec["symbol"],
    )
    margin = float(quantity * entry / ORIGINAL_LEVERAGE)
    if margin < 5:
        live.record_live_entry_check(
            check_results, "available_collateral", False,
            "Original V2 risk bütçesi minimum 5 USDT marjini karşılamıyor.",
            symbol=spec["symbol"],
        )
        raise HTTPException(423, "Original V2 risk bütçesi minimum marjini karşılamıyor.")
    live.record_live_entry_check(
        check_results, "available_collateral", True,
        "Hesaplanan Original V2 marjini minimum 5 USDT şartını karşılıyor.",
        symbol=spec["symbol"],
    )
    notional = float(quantity * entry)
    net_reward = float(quantity * abs(Decimal(spec["targets"][0]) - entry) - quantity * cost_per_unit)
    if net_reward < policy["minimum_net_reward_usdt"]:
        live.record_live_entry_check(
            check_results, "risk_reward", False,
            "TP1, ücret ve kayma sonrası minimum net getiri eşiğini karşılamıyor.",
            symbol=spec["symbol"],
        )
        raise HTTPException(423, "Original V2 maliyet veya borsa miktar kapısı geçilmedi.")
    live.record_live_entry_check(
        check_results, "risk_reward", True,
        "TP1 maliyetlerden sonra minimum net getiri eşiğini karşılıyor.",
        symbol=spec["symbol"],
    )
    if quantity > rules["max_qty"]:
        live.record_live_entry_check(
            check_results, "maximum_quantity", False,
            "Hesaplanan miktar Binance sembol üst sınırını aşıyor.",
            symbol=spec["symbol"],
        )
        raise HTTPException(423, "Original V2 maliyet veya borsa miktar kapısı geçilmedi.")
    live.record_live_entry_check(
        check_results, "maximum_quantity", True,
        "Hesaplanan miktar Binance sembol üst sınırını aşmıyor.",
        symbol=spec["symbol"],
    )
    spec.update(quantity=live.decimal_text(quantity), margin_usdt=margin, notional_usdt=float(quantity * entry),
                step=rules["step"], min_qty=rules["min_qty"], min_notional=rules["min_notional"],
                estimated_stop_loss_usdt=float(quantity * (abs(entry - stop) + cost_per_unit)),
                original_risk_budget=float(budget), initial_stop_loss=spec["stop_loss"],
                original_cost_bps=policy["fee_bps_per_side"] * 2 + policy["slippage_bps_per_side"] * 2,
                initial_quantity=live.decimal_text(quantity))
    wallet = risk_settings.verified_wallet(snapshot)
    selected_percent = settings(state)["risk_percent"]
    requested_loss = wallet * selected_percent / 100
    effective_loss = float(spec["estimated_stop_loss_usdt"])
    reasons = []
    requested_quantity = Decimal(str(requested_loss)) / (abs(entry - stop) + cost_per_unit)
    if margin_quantity == unrounded_quantity and margin_quantity < requested_quantity:
        reasons.append(f"İşlem başı teminat tavanı ({policy['max_margin_per_trade']:.2f} USDT)")
    if float(budget) < requested_loss - 1e-8:
        reasons.append(f"Sistem güvenlik tavanı: işlem başı en fazla {system_ceilings()['max_loss_per_trade_usdt']:g} USDT zarar")
    if quantity < unrounded_quantity:
        reasons.append("Binance miktar adımı ve TP miktar bölüşümü için aşağı yuvarlama")
    adjustment = {
        "symbol": spec["symbol"], "selected_percent": selected_percent,
        "effective_percent": effective_loss / wallet * 100,
        "requested_loss_usdt": requested_loss, "effective_loss_usdt": effective_loss,
        "reasons": reasons,
    }
    # Salt okunur: hedef / uygulanabilir maksimum / gerçekleşecek risk ayrımı. Miktarı ve kapıları değiştirmez.
    try:
        open_margin_now, _ = open_position_totals(state)
        adjustment["breakdown"] = risk_capacity.trade_risk_breakdown(
            wallet=wallet, selected_percent=selected_percent, budget_loss=float(budget),
            entry=entry, stop=stop, cost_per_unit=cost_per_unit, leverage=ORIGINAL_LEVERAGE,
            margin_cap_usdt=float(policy["max_margin_per_trade"]),
            total_margin_room_usdt=float(policy["max_total_margin_usdt"]) - open_margin_now,
            available_balance=snapshot.get("available_balance"),
            fee_rate=float(policy["fee_bps_per_side"]) / 10000,
            max_quantity=rules.get("max_qty"), quantity_step=rules["step"] * SPLIT_STEPS,
            effective_loss=effective_loss,
        )
    except Exception:  # noqa: BLE001 - yalnız bilgi; hiçbir koşulda giriş kararını bozmaz
        logger.exception("ORIGINAL_RISK_BREAKDOWN_FAILED")
        adjustment["breakdown"] = {"status": "unavailable", "reason": "Risk ayrıntısı hesaplanamadı."}
    spec["risk_adjustment"] = adjustment
    settings(state)["last_risk_adjustment"] = adjustment
    loss_budget_passed = float(spec["estimated_stop_loss_usdt"]) <= float(budget) + 1e-8
    live.record_live_entry_check(
        check_results, "risk_budget", loss_budget_passed,
        "Tahmini Original V2 stop kaybı işlem başı risk bütçesi içinde." if loss_budget_passed else "Tahmini Original V2 stop kaybı işlem başı risk bütçesini aşıyor.",
        symbol=spec["symbol"],
    )
    if not loss_budget_passed:
        raise live.LiveExchangeError("Original V2 stop kaybı işlem başı risk bütçesini aşıyor.", http_status=423, check_key="risk_budget")
    if notional > policy["max_margin_per_trade"] * ORIGINAL_LEVERAGE + 1e-8:
        live.record_live_entry_check(
            check_results, "risk_budget", False,
            "Original V2 pozisyon büyüklüğü işlem başı marjin sınırını aşıyor.",
            symbol=spec["symbol"],
        )
        raise HTTPException(423, "Original V2 marjin limiti aşıldı.")
    live.record_live_entry_check(
        check_results, "risk_budget", True,
        "Original V2 pozisyon büyüklüğü işlem başı marjin sınırında.",
        symbol=spec["symbol"],
    )
    open_margin, open_risk = open_position_totals(state)
    total_margin_limit = float(policy["max_total_margin_usdt"])
    if open_margin + margin > total_margin_limit + 1e-8:
        live.record_live_entry_check(
            check_results, "total_margin", False,
            f"Açık pozisyonların toplam teminatı izin verilen sınırı aşardı "
            f"({open_margin + margin:.2f} / {total_margin_limit:.2f} USDT).",
            symbol=spec["symbol"],
        )
        raise live.LiveExchangeError("Toplam teminat kullanımı sınırı aşılırdı.", http_status=423, check_key="total_margin")
    live.record_live_entry_check(check_results, "total_margin", True,
                                 "Toplam teminat kullanımı bakiye yüzdesi sınırında.", symbol=spec["symbol"])
    # Günlük zarar bütçesi: bugün gerçekleşen net zarar (komisyon ve fonlama dahil) + açık pozisyonların
    # başlangıç stop riski (komisyon ve kayma tahmini dahil) + bu girişin riski, günlük sınırı aşamaz.
    daily_limit = float(policy["daily_loss_limit"])
    day = settings(state).get("daily") if isinstance(settings(state).get("daily"), dict) else {}
    today = datetime.now(timezone.utc).date().isoformat()
    realized_loss = float(day.get("realized_loss") or 0) if day.get("day") == today else 0.0
    if not math.isfinite(realized_loss) or realized_loss < 0:
        raise live.LiveExchangeError("Günlük gerçekleşen zarar doğrulanamadı; yeni giriş kapalı.", http_status=423, check_key="daily_loss")
    committed = realized_loss + open_risk + effective_loss
    if committed > daily_limit + 1e-8:
        live.record_live_entry_check(
            check_results, "total_open_risk", False,
            f"Günlük zarar bütçesi aşılırdı: gerçekleşen {realized_loss:.2f} + açık stop riski {open_risk:.2f} "
            f"+ bu işlem {effective_loss:.2f} = {committed:.2f} / {daily_limit:.2f} USDT.",
            symbol=spec["symbol"],
        )
        raise live.LiveExchangeError("Günlük zarar bütçesi bu girişle aşılırdı; yeni pozisyon açılmadı.", http_status=423, check_key="total_open_risk")
    live.record_live_entry_check(check_results, "total_open_risk", True,
                                 f"Günlük zarar bütçesi içinde ({committed:.2f} / {daily_limit:.2f} USDT).", symbol=spec["symbol"])
    return spec


_TERMINAL_PLAN_STATUSES = {"KAPANDI", "İPTAL", "CLOSED", "CANCELLED"}


def _plan_number(plan: dict[str, Any], key: str) -> float:
    try:
        value = float(plan.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


def _live_position_quantity(state: dict[str, Any], plan: dict[str, Any]) -> float | None:
    rows = (state.get("snapshot") or {}).get("positions")
    if not isinstance(rows, list):
        return None
    total = 0.0
    for row in rows:
        if not isinstance(row, dict) or str(row.get("symbol") or "").upper() != str(plan.get("symbol") or "").upper():
            continue
        try:
            quantity = abs(float(row.get("quantity", row.get("positionAmt")) or 0))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(quantity):
            return None
        total += quantity
    return total


def plan_open_exposure(state: dict[str, Any], plan: dict[str, Any]) -> tuple[float, float]:
    """Bir planın şu an gerçekten açıkta olan (teminat, stop riski) değeri.

    TP1 doğrulanmadıysa ya da veri eksikse planın tam başlangıç değeri korunur: risk asla olduğundan
    düşük hesaplanmaz. TP1 doğrulandıysa kalan miktar, güncel stop ve maliyet üzerinden yeniden hesaplanır.
    Her çağrıda yeniden hesaplandığı için stop değişikliği ve kısmi kapanış hemen yansır.
    """
    full_margin = _plan_number(plan, "margin_usdt")
    full_risk = _plan_number(plan, "initial_risk_usdt")
    if not (plan.get("original_tp1_complete") or plan.get("breakeven_done")):
        return full_margin, full_risk
    try:
        initial_quantity = Decimal(str(plan.get("initial_quantity") or plan.get("quantity")))
        entry = Decimal(str(plan["entry_price"]))
        step = Decimal(str(plan["step"]))
        fraction = live_plan_fraction(plan)
        if not all(v.is_finite() for v in (initial_quantity, entry, step)) or min(initial_quantity, entry, step) <= 0:
            raise ValueError
        closed = (initial_quantity * fraction // step) * step
        remaining = initial_quantity - closed
        live_quantity = _live_position_quantity(state, plan)
        if live_quantity is not None:
            remaining = max(remaining, min(Decimal(str(live_quantity)), initial_quantity))
        # Güncel stop; stop durumu doğrulanamıyorsa ilk (daha uzak) stop kullanılır.
        stop_value = plan.get("stop_loss")
        if str(plan.get("protection_state") or "").upper() in {"UNKNOWN", "BROKEN"} or stop_value in (None, ""):
            stop_value = plan.get("initial_stop_loss")
        stop = Decimal(str(stop_value))
        cost_bps = Decimal(str(plan["original_cost_bps"])) if plan.get("original_cost_bps") is not None else Decimal("0")
        if not stop.is_finite() or stop <= 0 or not cost_bps.is_finite() or cost_bps < 0 or remaining < 0:
            raise ValueError
        direction = str(plan.get("direction") or "").upper()
        if direction == "LONG":
            adverse = max(Decimal("0"), entry - stop)
        elif direction == "SHORT":
            adverse = max(Decimal("0"), stop - entry)
        else:
            raise ValueError
        risk = float(remaining * (adverse + entry * cost_bps / 10000))
        leverage = _plan_number(plan, "leverage") or float(ORIGINAL_LEVERAGE)
        margin = float(remaining * entry) / leverage
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return full_margin, full_risk
    # Kalan değer hiçbir zaman ilk değeri aşmaz ama eksik hesaplanmasın diye ilk değer üst sınırdır.
    if full_risk > 0:
        risk = min(risk, full_risk)
    if full_margin > 0:
        margin = min(margin, full_margin)
    return margin, risk


def live_plan_fraction(plan: dict[str, Any]) -> Decimal:
    from . import v25_execution as live
    return live.plan_tp1_fraction(plan)


def open_position_totals(state: dict[str, Any]) -> tuple[float, float]:
    """Açık/bekleyen tüm planların (kaynağı fark etmez) şu an açıkta olan teminat ve stop riski toplamı."""
    margin = risk = 0.0
    for plan in (state.get("plans") or {}).values():
        if not isinstance(plan, dict) or str(plan.get("status") or "").upper() in _TERMINAL_PLAN_STATUSES:
            continue
        plan_margin, plan_risk = plan_open_exposure(state, plan)
        margin += plan_margin
        risk += plan_risk
    return margin, risk


async def check_entry(application: Any, client: Any, state: dict[str, Any],
                      snapshot: dict[str, Any]) -> None:
    from . import v25_execution as live
    check_session_time(state)
    require_fill_unlocked(state)
    if not available() or not running(state) or settings(state).get("paused"):
        raise HTTPException(423, "Original V2 LIVE kapalı veya güvenlik kilidi aktif.")
    await require_owner_authority(application, state)
    if state.get("real_trading_locked") is not False:
        raise HTTPException(423, "Original V2 gerçek işlem kilidi kapalı.")
    if not live.auto_session_active(state) or live.live_execution_blocked(state):
        raise HTTPException(423, "Original V2 LIVE yürütme izni sona erdi.")
    await daily_guard(client, state, snapshot)


async def persist_required(state: dict[str, Any]) -> None:
    from . import v25_execution as live
    try:
        result = await live.persist_state_awaited(state)
    except (OSError, RuntimeError) as exc:
        raise live.LiveExchangeError("Original V2 kalıcı işlem kaydı doğrulanamadı.", http_status=503) from exc
    if result not in {"INSERTED", "UPDATED"}:
        raise live.LiveExchangeError("Original V2 kalıcı işlem kaydı doğrulanamadı.", http_status=503)

async def audit_fill(state: dict[str, Any], plan: dict[str, Any], position: dict[str, Any]) -> None:
    """Check the settled entry once, not the smaller remainder after a TP exit."""
    from . import v25_execution as live
    if plan.get("original_fill_audit"):
        return
    violations = []
    evidence: dict[str, Any] = {}
    try:
        if plan.get("recovered_after_restart"):
            raise ValueError("Recovered intent has no durable settled-fill audit")
        entry = Decimal(str(position.get("entry_price")))
        quantity = Decimal(str(plan["initial_quantity"] if plan.get("original_tp_attempted") else position["quantity"]))
        stop = Decimal(str(plan["initial_stop_loss"]))
        budget = Decimal(str(plan["original_risk_budget"]))
        cost_bps = Decimal(str(plan["original_cost_bps"]))
        leverage = Decimal(str(plan["leverage"]))
        if not all(value.is_finite() for value in (entry, quantity, stop, budget, cost_bps, leverage)) \
                or min(entry, quantity, stop, budget) <= 0 or leverage != 3 or cost_bps < 0:
            raise ValueError("Invalid fill evidence")
        stop_percent = abs(entry - stop) / entry * 100
        risk = quantity * (abs(entry - stop) + entry * cost_bps / 10000)
        margin = quantity * entry / leverage
        evidence = {
            "entry_price": live.decimal_text(entry), "quantity": live.decimal_text(quantity),
            "stop_percent": live.decimal_text(stop_percent), "risk_usdt": live.decimal_text(risk),
            "risk_budget_usdt": live.decimal_text(budget), "margin_usdt": live.decimal_text(margin),
        }
        if stop_percent > 6:
            violations.append("Stop mesafesi %6 sınırını aştı")
        if risk > budget:
            violations.append("Dolum riski işlem risk bütçesini aştı")
        if margin < 5:
            violations.append("Dolum teminatı 5 USDT altına düştü")
        if (plan["direction"] == "LONG" and stop >= entry) or (plan["direction"] == "SHORT" and stop <= entry):
            violations.append("İlk stop gerçekleşen giriş yönüyle uyuşmuyor")
    except (KeyError, TypeError, ValueError, ArithmeticError):
        violations.append("Gerçek dolum risk kanıtı doğrulanamadı")
    audit = {"id": uuid.uuid4().hex, "checked_at": live.now_iso(), "violations": violations, **evidence}
    plan["original_fill_audit"] = audit
    try:
        adjustment = plan.get("risk_adjustment")
        breakdown = adjustment.get("breakdown") if isinstance(adjustment, dict) else None
        if isinstance(breakdown, dict) and breakdown.get("reachable") is False:
            live.add_event(
                state, "ORIGINAL_RISK_TARGET_UNREACHED",
                f"{plan['symbol']}: seçilen risk %{breakdown.get('target_percent'):.2f} bu işlemde uygulanamadı; "
                f"gerçek risk %{breakdown.get('effective_percent'):.2f}. Sebep: {breakdown.get('unreached_reason')}.",
                plan_id=plan.get("id"), symbol=plan["symbol"],
            )
    except Exception:  # noqa: BLE001 - yalnız bildirim; dolum denetimini bozmaz
        logger.exception("ORIGINAL_RISK_SHORTFALL_EVENT_FAILED")
    if violations:
        config = settings(state)
        previous = config.get("fill_lock") or {}
        config["fill_lock"] = {
            "id": audit["id"],
            "active": True, "created_at": previous.get("created_at") if previous.get("active") else audit["checked_at"],
            "plan_id": plan["id"], "symbol": plan["symbol"], "violations": violations, "evidence": evidence,
        }
        pause(state, f"{plan['symbol']} dolum güvenlik ihlali: {'; '.join(violations)}. Kilit için OWNER onayı gerekli")
    persisted = False
    try:
        await persist_required(state)
        persisted = True
    finally:
        if not persisted:
            plan.pop("original_fill_audit", None)
            pause(state, f"{plan['symbol']} dolum risk kontrolünün kalıcı kaydı doğrulanamadı")


async def install_targets(client: Any, state: dict[str, Any], plan: dict[str, Any],
                          position: dict[str, Any], existing_algos: list[dict[str, Any]] | None) -> None:
    from . import v25_execution as live
    quantity = Decimal(position["quantity"])
    if not plan.get("original_tp_attempted"):
        plan["initial_quantity"] = live.decimal_text(quantity)
    # Oran planın kendisinden okunur: alanı olmayan eski planlar %60'ta kalır, yeni planlar %70 taşır.
    fraction = live.plan_tp1_fraction(plan)
    pct = percent_text(fraction)
    partial = live.floor_step(Decimal(plan["initial_quantity"]) * fraction, Decimal(plan["step"]))
    if not plan.get("original_tp_attempted") and partial != Decimal(plan["initial_quantity"]) * fraction:
        pause(state, f"{plan['symbol']} doğrulanan dolum tam {pct} TP1 bölünmesine uygun değil")
        raise live.LiveExchangeError(f"Original V2 TP1 tam {pct} miktarı kurulamadı; pozisyon kapatılmadı.", http_status=423)
    if not plan.get("original_tp_attempted") and (
        partial < Decimal(plan["min_qty"]) or partial * Decimal(str(position["mark_price"])) < Decimal(plan["min_notional"])
    ):
        pause(state, f"{plan['symbol']} TP1 {pct} borsa minimumunu karşılamıyor")
        raise live.LiveExchangeError("Original V2 TP1 miktarı kurulamadı; pozisyon kapatılmadı.", http_status=423)
    common = {"algoType": "CONDITIONAL", "symbol": plan["symbol"],
              "side": "SELL" if plan["direction"] == "LONG" else "BUY",
              "type": "TAKE_PROFIT_MARKET", "workingType": "MARK_PRICE", "priceProtect": "TRUE"}
    ids = [plan["stop_algo_id"]]
    try:
        # Never reallocate 60% of the remainder on a protection repair or restart.
        if not plan.get("original_tp_attempted"):
            plan["original_tp_attempted"] = True
            await persist_required(state)
            result = await live.post_entry_protection_algo(client, {
                **common, "triggerPrice": plan["targets"][0], "quantity": live.decimal_text(partial),
                "reduceOnly": "true", "clientAlgoId": live.client_id_for("TP12", plan["intent_id"]),
            }, existing_algos)
            if not result.get("algoId"):
                raise live.LiveExchangeError("Original V2 TP1 kimliği doğrulanamadı.", unknown_execution=True)
            plan["tp12_algo_id"] = int(result["algoId"])
            plan["tp12_client_id"] = live.client_id_for("TP12", plan["intent_id"])
            await persist_required(state)
        if not plan.get("tp12_algo_id"):
            raise live.LiveExchangeError("Original V2 önceki TP1 kurulumu belirsiz.", unknown_execution=True)
        ids.append(plan["tp12_algo_id"])
        result = await live.post_entry_protection_algo(client, {
            **common, "triggerPrice": plan["targets"][2], "closePosition": "true",
            "clientAlgoId": live.client_id_for("TP3", plan["intent_id"]),
        }, existing_algos)
        if not result.get("algoId"):
            raise live.LiveExchangeError("Original V2 TP3 kimliği doğrulanamadı.", unknown_execution=True)
        plan["original_tp3_algo_id"] = int(result["algoId"])
        ids.append(plan["original_tp3_algo_id"])
    except Exception as exc:
        plan.update(protection_state="MISSING", tp_protection_state="UNPROTECTED",
                    status="STOP AKTİF · ORIGINAL TP DOĞRULANAMADI", last_error=live.sanitized_exception_message(exc))
        pause(state, f"{plan['symbol']} TP kurulamadı: {live.sanitized_exception_message(exc)}")
        raise
    plan.update(protection_ids=ids, monitoring_targets=[], tp_protection_state="EXCHANGE_BACKED",
                protection_state="MATCHED", status="KORUMA AKTİF", protected_at=live.now_iso(),
                entry_remainder_pending=False)
    await persist_required(state)


async def verify_targets(client: Any, state: dict[str, Any], plan: dict[str, Any],
                         rows: list[dict[str, Any]]) -> None:
    from . import v25_execution as live
    if plan.get("stop_loss") != plan.get("initial_stop_loss"):
        pause(state, f"{plan['symbol']} ilk stop değişti")
        raise live.LiveExchangeError("Original V2 ilk stop değiştirilemez.", unknown_execution=True)
    if not plan.get("original_tp_attempted"):
        return
    normalized = [{
        **row,
        "algo_id": row.get("algo_id", row.get("algoId")),
        "client_algo_id": row.get("client_algo_id", row.get("clientAlgoId")),
    } for row in rows]
    for target, identity, quantity in [
        (plan["targets"][2], plan.get("original_tp3_algo_id"), None),
        (plan["targets"][0], plan.get("tp12_algo_id"),
         live.decimal_text(live.floor_step(Decimal(plan["initial_quantity"]) * live.plan_tp1_fraction(plan), Decimal(plan["step"])))),
    ]:
        if not any(row.get("algo_id") == identity for row in normalized):
            continue
        params = {
            "symbol": plan["symbol"], "side": "SELL" if plan["direction"] == "LONG" else "BUY",
            "type": "TAKE_PROFIT_MARKET", "triggerPrice": target, "workingType": "MARK_PRICE",
            "clientAlgoId": live.client_id_for("TP3" if quantity is None else "TP12", plan["intent_id"]),
            **({"closePosition": "true"} if quantity is None else {"quantity": quantity, "reduceOnly": "true"}),
        }
        try:
            match = live.existing_entry_protection_algo(params, normalized)
            if not match or int(match["algoId"]) != identity:
                raise live.LiveExchangeError("Original V2 TP kimliği eşleşmiyor.", unknown_execution=True)
        except live.LiveExchangeError:
            plan["tp_protection_state"] = "UNPROTECTED"
            pause(state, f"{plan['symbol']} TP fiyatı, miktarı veya kimliği değişti")
            raise
    ids = {int(row.get("algo_id", row.get("algoId", 0)) or 0) for row in rows}
    if plan.get("original_tp3_algo_id") not in ids:
        plan["tp_protection_state"] = "UNPROTECTED"
        pause(state, f"{plan['symbol']} TP3 borsada yok")
    if plan.get("tp12_algo_id") in ids or plan.get("original_tp1_complete"):
        return
    if not plan.get("tp12_algo_id"):
        pause(state, f"{plan['symbol']} TP1 kimliği eksik")
        return
    native = await client.signed("GET", "/fapi/v1/algoOrder", {"algoId": plan["tp12_algo_id"]})
    if not isinstance(native, dict) or int(native.get("algoId") or 0) != plan["tp12_algo_id"]:
        raise live.LiveExchangeError("Original V2 TP1 geçmişi doğrulanamadı.", unknown_execution=True)
    actual = int(native.get("actualOrderId") or 0)
    if actual <= 0:
        pause(state, f"{plan['symbol']} TP1 yürütme kanıtı yok")
        return
    order = await client.signed("GET", "/fapi/v1/order", {"symbol": plan["symbol"], "orderId": actual})
    allocation = live.floor_step(Decimal(plan["initial_quantity"]) * live.plan_tp1_fraction(plan), Decimal(plan["step"]))
    if (not isinstance(order, dict) or int(order.get("orderId") or 0) != actual
            or order.get("symbol") != plan["symbol"] or order.get("side") != ("SELL" if plan["direction"] == "LONG" else "BUY")
            or not live._truthy(order.get("reduceOnly")) or order.get("status") != "FILLED"
            or Decimal(str(order.get("executedQty", 0))) != allocation):
        pause(state, f"{plan['symbol']} TP1 {percent_text(live.plan_tp1_fraction(plan))} dolumu doğrulanamadı")
        return
    plan["original_tp1_complete"] = True
    try:
        # Yalnız kayıt: doğrulamada zaten okunan borsa emri; hata olursa sessizce atlanır.
        from . import original_v2_record
        original_v2_record.attach_tp1(plan, order, native, str(allocation), live.now_iso())
    except Exception:  # noqa: BLE001
        pass
    live.persist_state(state)


def _record_maintenance_skip(config: dict[str, Any], scanner: dict[str, Any]) -> None:
    """Website maintenance blocks only new entries; the session stays running (not paused)."""
    config["last_skip_reason"] = MAINTENANCE_SKIP_REASON
    scanner.update(stage="MAINTENANCE", last_skip_reason=MAINTENANCE_SKIP_REASON)


# Saf limit reddi: emir gönderilmeden önce "bu giriş limiti aşardı" demektir. Bu bir borsa/uzlaşma hatası değildir;
# kilit, acil durdurma ve oturum kapatma gerektirmez. Aday atlanır, tur sessizce biter.
_LIMIT_REJECTION_ACCOUNT_KEYS = frozenset({"total_margin", "total_open_risk"})  # hesap seviyesi: sıradaki adaylar da aşar
_LIMIT_REJECTION_PER_TRADE_KEYS = frozenset({"risk_budget"})  # işlem seviyesi: başka aday sığabilir


def limit_rejection_kind(error: BaseException) -> str | None:
    """'account' | 'trade' yalnız emirsiz, açık bir limit reddi için; aksi halde None (eski davranış: hata akışı)."""
    if not isinstance(error, live.LiveExchangeError):
        return None
    if getattr(error, "unknown_execution", False) or getattr(error, "timed_out", False):
        return None
    key = str(getattr(error, "check_key", "") or "")
    if key in _LIMIT_REJECTION_ACCOUNT_KEYS:
        return "account"
    if key in _LIMIT_REJECTION_PER_TRADE_KEYS:
        return "trade"
    return None


def _entry_rejection_category(error: Exception) -> str:
    key = str(getattr(error, "check_key", "") or "").lower()
    detail = str(getattr(error, "detail", error)).lower()
    combined = f"{key} {detail}"
    if any(token in combined for token in ("daily", "günlük", "kayip", "kayıp", "loss limit", "trade limit")):
        return "daily_limit"
    if any(token in combined for token in ("position", "pozisyon", "occupancy", "duplicate", "same_direction")):
        return "position_limit"
    if "spread" in combined:
        return "spread_wide"
    if any(token in combined for token in ("minimum_margin", "margin floor")):
        return "minimum_margin"
    if "minimum_trade_size" in combined or "minimum_quantity" in combined or "notional" in combined:
        return "minimum_trade_size"
    return "risk_or_account_limit"


def _record_order_rejection(
    scanner: dict[str, Any], result: StrategyResult, category: str, reason: str,
    size_metrics: dict[str, str] | None = None,
) -> None:
    counts = scanner.setdefault("rejection_counts", {})
    if not isinstance(counts, dict):
        counts = scanner["rejection_counts"] = {}
    counts[category] = int(counts.get(category, 0)) + 1
    near_misses = scanner.get("near_misses")
    if not isinstance(near_misses, list):
        near_misses = []
    analysis = result.legacy.get("analysis") or {}
    updated = {
        "symbol": result.signal.symbol,
        "reason_code": category,
        "reason": reason,
        "confidence": analysis.get("confidence"),
        "confidence_threshold": next(
            (check.threshold for check in result.signal.quality_checks if check.key == "confidence"),
            None,
        ),
        "failed_checks": [],
        "progress": 5,
        **(size_metrics or {}),
    }
    near_misses = [item for item in near_misses if not isinstance(item, dict) or item.get("symbol") != updated["symbol"]]
    scanner["near_misses"] = [updated, *near_misses][:3]


async def cycle(application: Any, credentials: tuple[str, str] | None) -> None:
    from . import v25_execution as live
    state = application.state.v25_execution
    config = settings(state)
    scanner = config.setdefault("scanner", {})
    scanner["last_cycle_at"] = live.now_iso()
    check_session_time(state)
    if not running(state):
        scanner.update(stage="BLOCKED")
        return
    stored_universe = config.get("universe")
    ranked_symbols = stored_universe.get("ranked_symbols") if isinstance(stored_universe, dict) else None
    if isinstance(ranked_symbols, list):
        cached_symbols = ranked_symbols[:_valid_symbol_count(config)]
        if cached_symbols and all(
            isinstance(symbol, str) and symbol.isascii() and symbol.isalnum()
            and symbol.isupper() and symbol.endswith("USDT")
            for symbol in cached_symbols
        ) and len(set(cached_symbols)) == len(cached_symbols):
            service = getattr(application.state, "public_market_data", None)
            if service is not None:
                service.set_original_symbols(shared_original_symbols(application, cached_symbols))
            await configure_original_kline_stream(application, cached_symbols)
    if live.account_rate_limit_active(state):
        scanner.update(stage="RATE_LIMIT_WAIT", last_failure={
            "stage": "cooldown", "reason_code": "active_cooldown",
            "message": "Binance istek sınırı bekleniyor; bu döngüde tarama yapılmadı.",
        })
        scanner["last_24h"] = _scanner_history_summary(scanner, time.time())
        return
    global_ban_seconds = live.BINANCE_GLOBAL_BAN.remaining_seconds()
    # Engel yalnız paylaşımlı Render IP'sindeyse ve korumalı yol açıksa tarama sürer (hesap/emir zaten sabit IP'den gider).
    from .public_market_data import protected_lane_available
    render_only = global_ban_seconds > 0 and not live.BINANCE_GLOBAL_BAN.applies_to(live.account_egress_route())
    if global_ban_seconds > 0 and not (render_only and protected_lane_available()):
        scanner.update(stage="RATE_LIMIT_WAIT", last_failure={
            "stage": "cooldown", "reason_code": "binance_global_ban",
            "message": f"Binance geçici engeli sürüyor; {global_ban_seconds} saniye bekleniyor.",
        })
        scanner["last_24h"] = _scanner_history_summary(scanner, time.time())
        return
    at = int(time.time()) // 900 * 900
    if config.get("last_decision_time", -1) >= at:
        scanner.update(stage="WAITING_NEXT_CANDLE")
        return
    maintenance_active = maintenance_blocks_automation(getattr(application.state, "maintenance", None))
    if maintenance_active and scanner.get("maintenance_checked_at") == at:
        # check_entry already ran for this candle during maintenance; keep its normal cadence.
        _record_maintenance_skip(config, scanner)
        return
    scan_diagnostics: dict[str, Any] = {}
    try:
        if credentials is None:
            credentials = await live.auto_session_credentials(application, state)
        if not live.usable_live_credentials(credentials):
            raise HTTPException(423, "Original V2 LIVE bağlantısı kesildi.")
        if not live.readiness_for(application, state, credentials=credentials)["ready"]:
            raise HTTPException(423, "Original V2 LIVE yayın kapıları kapalı.")
        client = live.client_for_with_credentials(application, credentials)
        snapshot = await live.account_snapshot(client, cached=True, source="original_strategy")
        await check_entry(application, client, state, snapshot)
        if maintenance_active:
            # Authority, lock and daily_guard checks above still ran (and still pause on
            # failure); only the scan and new entries are skipped. The candle is not consumed.
            scanner["maintenance_checked_at"] = at
            _record_maintenance_skip(config, scanner)
            live.persist_state(state)
            return
        occupied = {
            item["symbol"]
            for item in (
                snapshot.get("positions", [])
                + snapshot.get("open_orders", [])
                + snapshot.get("open_algo_orders", [])
            )
        }
        scanner.update(stage="UNIVERSE_REFRESH")
        symbols = await resolve_live_universe(application, client, config)
        if not symbols:
            config["last_decision_time"] = at
            scanner["rejection_counts"] = {}
            scanner["near_misses"] = []
            _record_scanner_scan(scanner, time.time(), 0)
            scanner.update(stage="NO_ELIGIBLE_SYMBOLS", scanned_symbol_count=0,
                           scanned_symbols=[], candidate_count=0, last_scan_at=live.now_iso(),
                           last_failure=None, last_24h=_scanner_history_summary(scanner, time.time()))
            live.persist_state(state)
            return
        scanner.update(stage="SCANNING")
        sizing_policy = execution_policy(state, snapshot)
        sizing_context = {
            "max_loss_per_trade": sizing_policy["max_loss_per_trade"],
            "max_margin_per_trade": sizing_policy["max_margin_per_trade"],
            "fee_bps_per_side": sizing_policy["fee_bps_per_side"],
            "slippage_bps_per_side": sizing_policy["slippage_bps_per_side"],
            "tp1_fraction": str(TP1_FRACTION),
            "split_steps": SPLIT_STEPS,
        }
        stream = getattr(application.state, "original_kline_stream", None)

        async def fetch_streamed_klines(symbol: str, interval: str, limit: int) -> Any:
            from .binance_kline_stream import KlineStreamUnavailable

            if stream is None or not stream.is_subscribed(symbol, interval):
                raise RuntimeError("Original V2 kline stream is not configured for the active universe")
            try:
                return await stream.get_candles(
                    symbol,
                    interval,
                    limit,
                    lambda params: client.public_get("/fapi/v1/klines", params),
                )
            except KlineStreamUnavailable as exc:
                raise live.LiveRateLimitError(
                    "Binance geçici engeli sürüyor; eksik mum verisi nedeniyle tarama bekletildi.",
                    retry_after=exc.retry_after,
                    exchange_code=-1003,
                ) from exc

        eligible, _results = await original.live_candidates(
            client, at, occupied, symbols, require_complete=True, diagnostics=scan_diagnostics,
            sizing_context=sizing_context,
            kline_fetch=fetch_streamed_klines if stream is not None else None,
        )
        config["last_decision_time"] = at
        history_item = _record_scanner_scan(
            scanner, time.time(), len(eligible),
            scan_diagnostics.get("rejection_counts", {}), scan_diagnostics.get("check_failure_counts", {}),
        )
        scanner.update(stage="SCAN_COMPLETE", last_scan_at=live.now_iso(),
                       scanned_symbol_count=len(symbols), scanned_symbols=symbols,
                       data_complete_count=scan_diagnostics.get("data_complete_count", 0),
                       candidate_count=len(eligible),
                       rejection_counts=scan_diagnostics.get("rejection_counts", {}),
                       near_misses=scan_diagnostics.get("near_misses", []),
                       last_failure=None,
                       last_24h=_scanner_history_summary(scanner, time.time()))
        for candidate, result in eligible[:3]:
            if not running(state):
                break
            signal = result.legacy["analysis"]
            try:
                preview = await build_spec(client, result, state, snapshot)
                if original_v2_pullback.is_pullback_result(result):
                    # Karar katmanı: canlı fiyat sinyal kapanışından işlem aleyhine çok uzaklaştıysa bu aday atlanır (emir kodu değişmez).
                    deviation_ok, deviation = original_v2_pullback.fill_deviation_check(result, preview.get("current_price"))
                    if not deviation_ok:
                        _record_order_rejection(scanner, result, "price_moved", f"PRICE_MOVED_FROM_SIGNAL {deviation}")
                        logger.warning("ORIGINAL_V2_PULLBACK_PRICE_MOVED symbol=%s detail=%s", candidate["symbol"], deviation)
                        scanner["candidate_count"] = max(0, int(scanner.get("candidate_count") or 0) - 1)
                        history_item["candidates"] = scanner["candidate_count"]
                        continue
                body = live.LiveOrderRequest(
                    symbol=candidate["symbol"], direction=signal["direction"], margin_usdt=preview["margin_usdt"],
                    leverage=3, stop_loss=signal["stop_loss"], tp1=signal["tp1"], tp2=signal["tp2"], tp3=signal["tp3"],
                    intent_id=f"original-live-{config['owner_id']}-{at}-{candidate['symbol']}",
                )
                await live.execute_live_order(
                    application,
                    body,
                    source=SOURCE,
                    credentials=credentials,
                    strategy_result=result,
                )
                history_item["trades"] += 1
            except live.MinimumTradeSizeError as exc:
                category = "minimum_trade_size"
                _record_order_rejection(scanner, result, category, live.sanitized_exception_message(exc),
                                         exc.sizing_metrics)
                logger.warning(
                    "ORIGINAL_LIVE_MINIMUM_TRADE_SIZE_SKIPPED symbol=%s total_quantity=%s total_notional_usdt=%s "
                    "tp1_quantity=%s tp1_notional_usdt=%s remaining_quantity=%s remaining_notional_usdt=%s "
                    "min_qty=%s min_notional_usdt=%s",
                    exc.symbol,
                    *(exc.sizing_metrics[key] for key in (
                        "total_quantity", "total_notional_usdt", "tp1_quantity", "tp1_notional_usdt",
                        "remaining_quantity", "remaining_notional_usdt", "min_qty", "min_notional_usdt",
                    )),
                )
                scanner["candidate_count"] = max(0, int(scanner.get("candidate_count") or 0) - 1)
                history_item["candidates"] = scanner["candidate_count"]
                continue
            except Exception as exc:
                if is_maintenance_rejection(exc):
                    # Expected skip: no pause, no rejection record, no further candidates this round.
                    _record_maintenance_skip(config, scanner)
                    break
                category = _entry_rejection_category(exc)
                _record_order_rejection(
                    scanner, result, category, live.sanitized_exception_message(exc),
                )
                if getattr(exc, "check_key", None) == "symbol_occupancy":
                    config["last_skip_reason"] = "symbol_occupied"
                    scanner.update(stage="SYMBOL_OCCUPIED", last_skip_reason="symbol_occupied")
                    continue
                limit_kind = limit_rejection_kind(exc)
                if limit_kind is not None:
                    # Limit doldu: yeni giriş açılmadı, sistem kilitlenmez. Gerçek hatalar aşağıdaki raise ile eskisi gibi kilitler.
                    config["last_skip_reason"] = f"limit_{category}"
                    scanner.update(stage="LIMIT_REACHED", last_skip_reason=f"limit_{category}")
                    scanner["candidate_count"] = max(0, int(scanner.get("candidate_count") or 0) - 1)
                    history_item["candidates"] = scanner["candidate_count"]
                    logger.warning("ORIGINAL_LIVE_LIMIT_REACHED symbol=%s check=%s", candidate["symbol"], getattr(exc, "check_key", ""))
                    if limit_kind == "account":
                        break
                    continue
                raise
        scanner["last_24h"] = _scanner_history_summary(scanner, time.time())
        live.persist_state(state)
    except live.LiveRateLimitError as exc:
        failure = scan_diagnostics.get("failure") or {}
        scanner.update(stage="RATE_LIMIT_WAIT", last_failure={
            **failure,
            "stage": "cooldown",
            "reason_code": "binance_rate_limit",
            "message": live.sanitized_exception_message(exc),
        })
        scanner["last_24h"] = _scanner_history_summary(scanner, time.time())
        live.persist_state(state)
        if exc.request_method not in {None, "GET"}:
            pause(state, live.sanitized_exception_message(exc))
        raise
    except Exception as exc:
        if (isinstance(exc, live.LiveExchangeError) and exc.transient_read_failure
                and not exc.unknown_execution and exc.request_path == "/fapi/v1/income"):
            scanner.update(stage="WAITING_RISK_DATA", last_failure={
                "stage": "risk_data",
                "reason_code": "risk_data_unavailable",
                "message": live.sanitized_exception_message(exc),
            })
            scanner["last_24h"] = _scanner_history_summary(scanner, time.time())
            live.persist_state(state)
            raise
        scanner.update(stage="BLOCKED")
        failure = scan_diagnostics.get("failure") or {}
        scanner["last_failure"] = {
            **failure,
            "stage": failure.get("stage", "cycle"),
            "reason_code": failure.get("reason_code") or (
                _entry_rejection_category(exc)
                if isinstance(exc, (HTTPException, live.LiveExchangeError))
                else "cycle_error"
            ),
            "message": live.sanitized_exception_message(exc),
        }
        scanner["last_24h"] = _scanner_history_summary(scanner, time.time())
        pause(state, live.sanitized_exception_message(exc))
        raise


@router.get("")
async def status(request: Request) -> dict[str, Any]:
    live_access.require_live_member(request)
    from . import v25_execution as live
    live.guard_execution_ownership(request, claim=False)
    return public_config(request.app.state.v25_execution)


@router.get("/persistence-diagnostics")
async def persistence_diagnostics(request: Request, response: Response = None) -> dict[str, Any]:
    live_access.require_live_member(request)
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
    from . import v25_execution as live
    live.guard_execution_ownership(request, claim=False)
    state = request.app.state.v25_execution
    pool = getattr(request.app.state, "db_pool", None)
    result = {
        "read_only": True, "state_key": live.snapshot_key(state),
        "build_commit": os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT") or None,
        "database_status": "UNAVAILABLE", "record_exists": None,
        "database_revision": None, "original_record_exists": None,
        "local_revision": state.get("persistence_revision", 0),
        "recovery_loaded": bool(state.get("recovery_loaded")),
        "last_write": getattr(request.app.state, "v25_last_persistence_write", None),
        "last_awaited_write": getattr(request.app.state, "v25_last_awaited_persistence_write", None),
        "diagnosis": "DATABASE_UNAVAILABLE",
    }
    if pool is None:
        return result
    try:
        row = await asyncio.wait_for(pool.fetchrow(
            """SELECT payload->>'persistence_revision' AS revision,
                      jsonb_typeof(payload->'original_v2_live') = 'object' AS original_record_exists
               FROM application_state_snapshots WHERE state_key = $1""", live.snapshot_key(state)), timeout=3)
    except Exception as exc:
        result["database_status"] = "TABLE_MISSING" if getattr(exc, "sqlstate", None) == "42P01" else "READ_FAILED"
        result["diagnosis"] = result["database_status"]
        return result
    result.update(database_status="CONNECTED", record_exists=row is not None, local_revision=state.get("persistence_revision", 0))
    if row is None:
        result.update(original_record_exists=False, diagnosis="FIRST_RECORD_MISSING")
        return result
    revision = row["revision"]
    try:
        revision = 0 if revision is None or revision == "" else int(revision)
        if revision < 0 or revision > 9_223_372_036_854_775_807:
            raise ValueError("Invalid revision")
    except (ValueError, TypeError, OverflowError):
        result["diagnosis"] = "INVALID_DATABASE_REVISION"
        return result
    result.update(database_revision=revision, original_record_exists=bool(row["original_record_exists"]))
    if revision > result["local_revision"]:
        result["diagnosis"] = "DATABASE_AHEAD"
    elif (result["last_awaited_write"] or {}).get("result") == "SKIPPED":
        result["diagnosis"] = "WRITE_SKIPPED_REQUIRES_REVIEW"
    elif (result["last_awaited_write"] or {}).get("result") in {"TABLE_MISSING", "DATABASE_ERROR"}:
        result["diagnosis"] = "PREVIOUS_WRITE_FAILED"
    else:
        result["diagnosis"] = "NO_REVISION_CONFLICT_OBSERVED"
    return result


@router.post("/start")
async def start(request: Request, body: Start) -> dict[str, Any]:
    from . import v25_execution as live
    user = live_access.require_live_member(request)
    live_access.forbid_smart_trade(request, user)
    if not available():
        raise HTTPException(423, "Original V2 LIVE sunucu ayarı kapalı.")
    await live.require_live_understanding(request, user, body.understood)
    state = request.app.state.v25_execution
    async with state["lock"]:
        require_fill_unlocked(state)
        check_session_time(state)
        if live.auto_session_is_active(state) or running(state):
            raise HTTPException(409, "Önce mevcut LIVE otomasyonunu durdurun.")
        allowed, reason = live.live_auto_start_gate(request.app, state, request, unlimited_daily_trades=True,
                                                    tolerate_healthy_plans=True)
        if not allowed:
            raise HTTPException(423, reason)
        previous = settings(state)
        _, _, fingerprint = live.live_credentials_status(request)
        if not fingerprint:
            raise HTTPException(423, "Original V2 için doğrulanmış LIVE bağlantısı gerekli.")
        if previous and previous.get("owner_id") == user["id"] and previous.get("fingerprint") != fingerprint:
            state["original_v2_live"] = previous = risk_settings.rebound_config(state, previous, fingerprint)
        if previous and (previous.get("owner_id") != user["id"] or previous.get("fingerprint") != fingerprint):
            raise HTTPException(423, "Original V2 hesap bağlantısı değişti; günlük limit kaydı korunuyor.")
        client = live.client_for(request.app, request)
        try:
            snapshot = await live.account_snapshot(client)
        except (live.LiveExchangeError, live.BinanceDemoError) as exc:
            raise live.safe_exchange_error(exc) from exc
        previous = await risk_settings.save_choice(request, user, state, body, snapshot, fingerprint)
        config = {**previous, "owner_id": user["id"], "risk_percent": body.risk_percent,
                  "fingerprint": fingerprint, "symbol_count": body.symbol_count,
                  "daily_loss_percent": body.daily_loss_percent, "enabled": False, "paused": False}
        scanner = config.setdefault("scanner", {})
        scanner.update(stage="UNIVERSE_PENDING", last_scan_at=None, scanned_symbol_count=0,
                       scanned_symbols=[], candidate_count=0)
        state["original_v2_live"] = config
        try:
            await daily_guard(client, state, snapshot)
            if config.get("paused"):
                raise HTTPException(423, "Original V2 başlatma sırasında durduruldu.")
            await live._start_live_auto_session(
                request, live.Confirmation(understood=True),
                state_lock_held=True, unlimited_daily_trades=True,
            )
            started = time.time()
            consent = live.consent_status(state, request)
            if not consent.get("active") or not consent.get("expires_at"):
                raise HTTPException(423, "Original V2 canlı risk izni doğrulanamadı.")
            consent_expiry = datetime.fromisoformat(consent["expires_at"]).timestamp()
            requested_expiry = started + body.session_hours * 3600
            expires = min(requested_expiry, consent_expiry)
            if expires <= started:
                raise HTTPException(423, "Original V2 canlı risk izni süresi doldu.")
            state["auto"].update(session_until=expires, last_decision=f"Original V2 {body.session_hours} saatlik sunucu gözetimli oturumu açıldı; bitiş canlı risk izniyle sınırlıdır.")
            state["auto_authorization"]["expires_at_epoch"] = expires
            config.update(session_hours=body.session_hours, session_id=uuid.uuid4().hex,
                          session_started_at=live.now_iso(), session_expires_at_epoch=expires,
                          session_authority_limited=expires < requested_expiry, session_warning_sent=False)
            config.update(enabled=True, paused=False, pause_reason=None)
            if await live.persist_state_awaited(state) not in {"INSERTED", "UPDATED"}:
                raise HTTPException(503, "Original V2 kalıcı durum kaydı doğrulanamadı.")
        except Exception as exc:
            pause(state, live.sanitized_exception_message(exc))
            if isinstance(exc, (live.LiveExchangeError, live.BinanceDemoError)):
                raise live.safe_exchange_error(exc) from exc
            raise
    return await status(request)

@router.post("/risk-settings")
async def update_risk(request: Request, body: risk_settings.RiskUpdate) -> dict[str, Any]:
    from . import v25_execution as live
    user = live_access.require_live_member(request)
    if not available():
        raise HTTPException(423, "Original V2 LIVE sunucu ayarı kapalı.")
    live.execution_owner(request)
    state = request.app.state.v25_execution
    async with state["lock"]:
        if not state.get("recovery_ready"):
            raise HTTPException(503, "Canlı durum kurtarma tamamlanmadı; risk ayarı değiştirilmedi.")
        await live.require_live_understanding(request, user, body.understood)
        _, _, fingerprint = live.live_credentials_status(request)
        if not fingerprint:
            raise HTTPException(423, "Risk ayarı için doğrulanmış LIVE bağlantısı gerekli.")
        client = live.client_for(request.app, request)
        try:
            snapshot = await live.account_snapshot(client)
        except (live.LiveExchangeError, live.BinanceDemoError) as exc:
            raise live.safe_exchange_error(exc) from exc
        await risk_settings.save_choice(request, user, state, body, snapshot, fingerprint)
    return await status(request)

@router.post("/extend-session")
async def extend_session(request: Request, body: ExtendSession) -> dict[str, Any]:
    """Çalışan oturumun bitişini ileri alır. Yeni giriş izni, risk ayarı, ARM veya açık pozisyon değişmez.

    Yeni bitiş = şimdi + seçilen süre, canlı risk izninin bitişiyle sınırlı. Süre kısalmaz; kazanç yoksa reddedilir."""
    from . import v25_execution as live
    user = live_access.require_live_member(request)
    if not available():
        raise HTTPException(423, "Original V2 LIVE sunucu ayarı kapalı.")
    live.execution_owner(request)
    state = request.app.state.v25_execution
    async with state["lock"]:
        if not state.get("recovery_ready"):
            raise HTTPException(503, "Canlı durum kurtarma tamamlanmadı; oturum uzatılmadı.")
        await live.require_live_understanding(request, user, body.understood)
        config = settings(state)
        if config.get("owner_id") != user["id"]:
            raise HTTPException(403, "Oturum yalnız hesabın sahibi tarafından uzatılabilir.")
        if not running(state) or config.get("paused"):
            raise HTTPException(409, "Uzatılacak çalışan bir oturum yok; yeni oturum başlatın.")
        now = time.time()
        current = float(config.get("session_expires_at_epoch") or (state.get("auto") or {}).get("session_until") or 0)
        if current <= now:
            raise HTTPException(409, "Oturum süresi doldu; yeni oturum başlatın.")
        consent = live.consent_status(state, request)
        if not consent.get("active") or not consent.get("expires_at"):
            raise HTTPException(423, "Original V2 canlı risk izni doğrulanamadı; oturum uzatılmadı.")
        consent_expiry = datetime.fromisoformat(consent["expires_at"]).timestamp()
        requested = now + body.session_hours * 3600
        new_expiry = min(requested, consent_expiry)
        if new_expiry <= current + 60:
            raise HTTPException(409, "Oturum zaten bu süreye ya da canlı risk izninin bitişine kadar açık; daha fazla uzatılamaz.")
        keys = ("session_expires_at_epoch", "session_hours", "session_authority_limited", "session_warning_sent")
        previous = {key: config.get(key) for key in keys}
        had = {key: key in config for key in keys}
        previous_auto = (state["auto"].get("session_until"), state["auto_authorization"].get("expires_at_epoch"))
        config.update(session_expires_at_epoch=new_expiry, session_hours=body.session_hours,
                      session_authority_limited=new_expiry < requested, session_warning_sent=False)
        state["auto"]["session_until"] = new_expiry
        state["auto_authorization"]["expires_at_epoch"] = new_expiry
        persisted = False
        try:
            await persist_required(state)
            persisted = True
        except live.LiveExchangeError:
            raise HTTPException(503, "Oturum uzatması kalıcı kaydedilemedi; eski süre korundu.") from None
        finally:
            if not persisted:
                for key in keys:
                    if had[key]:
                        config[key] = previous[key]
                    else:
                        config.pop(key, None)
                state["auto"]["session_until"], state["auto_authorization"]["expires_at_epoch"] = previous_auto
                live.persist_state(state)
        live.add_event(
            state, "ORIGINAL_SESSION_EXTENDED",
            f"Canlı oturum uzatıldı: {body.session_hours} saat (yeni bitiş " + datetime.fromtimestamp(new_expiry, timezone.utc).strftime("%H:%M UTC") + ")."
            + (" Bitiş canlı risk iziniyle sınırlı." if new_expiry < requested else ""),
            actor=user["id"], previous_expiry=current, new_expiry=new_expiry,
        )
        live.persist_state(state)
    return await status(request)


@router.post("/margin-mode")
async def update_margin_mode(request: Request, body: MarginModeUpdate) -> dict[str, Any]:
    """Yalnız bu hesabın teminat modunu değiştirir; yalnız YENİ girişlere uygulanır, açık pozisyona dokunmaz."""
    from . import v25_execution as live
    user = live_access.require_live_member(request)
    if not available():
        raise HTTPException(423, "Original V2 LIVE sunucu ayarı kapalı.")
    live.execution_owner(request)
    if body.mode not in MARGIN_MODES:
        raise HTTPException(422, "Teminat modu Güvenli, Orta veya Agresif olmalı.")
    if body.mode in {"orta", "agresif"} and not body.acknowledged:
        raise HTTPException(422, "Bu mod için yüksek teminat riskini onaylamalısınız.")
    if body.mode == "agresif" and body.confirmation.strip() != AGGRESSIVE_MARGIN_CONFIRMATION:
        raise HTTPException(422, f"Agresif mod için {AGGRESSIVE_MARGIN_CONFIRMATION} yazın.")
    state = request.app.state.v25_execution
    async with state["lock"]:
        if not state.get("recovery_ready"):
            raise HTTPException(503, "Canlı durum kurtarma tamamlanmadı; teminat modu değiştirilmedi.")
        await live.require_live_understanding(request, user, body.understood)
        config = settings(state)
        if config.get("owner_id") not in {None, user["id"]}:
            raise HTTPException(403, "Teminat modu yalnız hesabın sahibi tarafından değiştirilebilir.")
        keys = ("margin_mode", "margin_mode_acknowledged", "margin_mode_confirmed", "margin_mode_changed_at")
        previous_values = {key: config.get(key) for key in keys}
        had = {key: key in config for key in keys}
        old_mode = config.get("margin_mode", "guvenli")
        config["margin_mode"] = body.mode
        config["margin_mode_acknowledged"] = body.mode in {"orta", "agresif"}
        config["margin_mode_confirmed"] = body.mode == "agresif"
        config["margin_mode_changed_at"] = live.now_iso()
        persisted = False
        try:
            await persist_required(state)
            persisted = True
        except live.LiveExchangeError:
            raise HTTPException(503, "Teminat modu kalıcı kaydedilemedi; eski mod korundu.") from None
        finally:
            if not persisted:
                for key in keys:
                    if had[key]:
                        config[key] = previous_values[key]
                    else:
                        config.pop(key, None)
                live.persist_state(state)
        live.add_event(
            state, "ORIGINAL_MARGIN_MODE_CHANGED",
            f"Teminat modu {MARGIN_MODE_LABELS.get(old_mode, old_mode)} → {MARGIN_MODE_LABELS[body.mode]} (yalnız yeni girişler).",
            actor=user["id"], previous=old_mode, mode=body.mode,
        )
        live.persist_state(state)
    return await status(request)


@router.get("/smart-profit")
async def smart_profit_status(request: Request) -> dict[str, Any]:
    live_access.require_live_member(request)
    from . import v25_execution as live
    live.guard_execution_ownership(request, claim=False)
    return smart_profit_public(request.app.state.v25_execution)


@router.post("/smart-profit")
async def update_smart_profit(request: Request, body: SmartProfitUpdate) -> dict[str, Any]:
    """Akıllı Kâr Koruma ayarı. Kapatmak/ACİL DURDUR orijinal Stop/TP'yi silmez; yalnız geçici stop temizlenir."""
    from . import v25_execution as live
    from . import original_profit_guard as guard_module
    user = live_access.require_live_member(request)
    if not available():
        raise HTTPException(423, "Original V2 LIVE sunucu ayarı kapalı.")
    live.execution_owner(request)
    changes = body.model_dump(exclude_none=True)
    understood = changes.pop("understood", None) is True
    if not changes:
        raise HTTPException(422, "Değiştirilecek bir ayar gönderilmedi.")
    state = request.app.state.v25_execution
    async with state["lock"]:
        config = settings(state)
        if config.get("owner_id") not in {None, user["id"]}:
            raise HTTPException(403, "Bu ayar yalnız hesabın sahibi tarafından değiştirilebilir.")
        previous = copy.deepcopy(config.get(guard_module.SETTINGS_KEY))
        updated = guard_module.get_settings(config)
        if changes.get("enabled") is True and updated.get("enabled") is not True and not understood:
            raise HTTPException(422, "Akıllı Kâr Koruma gerçek emir gönderebilir; açmak için açık onay (understood) gerekir.")
        updated.update(changes)
        config[guard_module.SETTINGS_KEY] = guard_module.clean_settings(updated)
        try:
            await persist_required(state)
        except live.LiveExchangeError:
            if previous is None:
                config.pop(guard_module.SETTINGS_KEY, None)
            else:
                config[guard_module.SETTINGS_KEY] = previous
            live.persist_state(state)
            raise HTTPException(503, "Akıllı Kâr Koruma ayarı kalıcı kaydedilemedi; eski ayar korundu.") from None
        live.add_event(state, "ORIGINAL_SMART_PROFIT_SETTINGS",
                       "Akıllı Kâr Koruma ayarı güncellendi: " + ", ".join(sorted(changes)) + ". Orijinal Stop/TP değişmedi.",
                       actor=user["id"], changes=changes)
        live.persist_state(state)
    return smart_profit_public(state)


@router.post("/fill-lock/approve")
async def approve_fill_lock(request: Request, body: FillLockApproval) -> dict[str, Any]:
    from . import v25_execution as live
    user = live_access.require_live_member(request)
    live.guard_execution_ownership(request, claim=False)
    state = request.app.state.v25_execution
    if body.confirmation != "DOLUM RİSKİNİ İNCELEDİM":
        raise HTTPException(422, "Kilidi kaldırmak için DOLUM RİSKİNİ İNCELEDİM yazın.")
    async with state["lock"]:
        config = settings(state)
        lock = config.get("fill_lock") or {}
        if config.get("owner_id") != user["id"]:
            raise HTTPException(403, "Dolum kilidi yalnız ilgili OWNER tarafından kaldırılabilir.")
        if not fill_locked(state) or lock.get("id") != body.lock_id:
            raise HTTPException(409, "Dolum kilidi değişti; güncel durumu inceleyin.")
        config["fill_lock"] = {**lock, "active": False, "approved_by": user["id"], "approved_at": live.now_iso()}
        persisted = False
        try:
            await persist_required(state)
            persisted = True
        except live.LiveExchangeError:
            raise HTTPException(503, "Dolum kilidi onayı kalıcı kaydedilemedi; kilit aktif kaldı.") from None
        finally:
            if not persisted and config.get("fill_lock", {}).get("id") == body.lock_id:
                config["fill_lock"] = lock
                live.persist_state(state)
        if config.get("fill_lock", {}).get("id") != body.lock_id or fill_locked(state):
            raise HTTPException(409, "Dolum kilidi kalıcı kayıt sırasında değişti; güncel durumu inceleyin.")
        live.add_event(state, "ORIGINAL_FILL_LOCK_APPROVED", "OWNER dolum riskini inceleyip kilidi kaldırdı; otomasyon başlamadı.",
                       actor=user["id"], lock_id=body.lock_id)
        live.persist_state(state)
    return await status(request)


@router.post("/stop")
async def stop(request: Request) -> dict[str, Any]:
    from . import v25_execution as live
    user = live_access.require_live_member(request)
    live.execution_owner(request)
    state = request.app.state.v25_execution
    async with state["lock"]:
        if settings(state).get("owner_id") != user["id"]:
            raise HTTPException(409, "Bu hesap için Original V2 LIVE oturumu yok.")
        pause(state, "OWNER acil durdurma")
        if await live.persist_state_awaited(state) not in {"INSERTED", "UPDATED"}:
            raise HTTPException(503, "Durdurma kalıcı kaydı doğrulanamadı; LIVE durumunu kontrol edin.")
    await configure_original_kline_stream(request.app, [])
    return await status(request)
