"""Demo ikizi: Master Trade "Canlı İşlem" ekranının (Rehber / Kais Koç) demo karşılığı.

Bu modül CANLI motora (v25_execution / original_v2_live) hiç dokunmaz ve onu içe aktarmaz.
Ekranın konuştuğu adreslerin aynısını `/api/binance-demo/cockpit/*` altında sunar ve her şeyi
mevcut Binance Futures Demo motoruna (binance_demo, v21_demo) yönlendirir.
Tüm emirler yalnızca demo-fapi.binance.com'a gider; gerçek hesaba emir gitmesi mümkün değildir.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

router = APIRouter(prefix="/api/binance-demo/cockpit", tags=["Demo Cockpit Twin"])

SESSION_HOURS = {1, 4, 12, 24}
ARM_RENEW_BELOW_SECONDS = 120
UNSUPPORTED = "Bu ayar Demo motorunda yok; Demo'da kullanılamaz."


class Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


def _iso(epoch: float | None) -> str | None:
    return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat() if epoch else None


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result and abs(result) != float("inf") else default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


async def _account(request: Request) -> tuple[dict[str, Any], str | None]:
    from . import binance_demo as demo

    try:
        return await demo.demo_account(request), None
    except HTTPException as exc:
        return {}, str(exc.detail)[:240]


def _session_until(auto: dict[str, Any]) -> float:
    return _num(auto.get("twin_session_until"))


def server_flags() -> dict[str, bool]:
    """Kais Original Demo stratejisinin sunucu ayarları (okunur, değiştirilmez)."""
    from . import original_v2_demo_execution as execution
    from .strategies import original_v2_demo as strategy

    try:
        enabled = bool(strategy.feature_enabled())
    except Exception:  # geçersiz ayar değeri: kapalı say
        enabled = False
    try:
        send = bool(execution.send_orders_enabled())
    except Exception:
        send = False
    return {"enabled": enabled, "send_orders": send}


def _original_enabled(auto: dict[str, Any]) -> bool:
    from .strategies.original_v2_demo import STRATEGY_ID

    return bool(auto.get("enabled")) and auto.get("strategy_id") == STRATEGY_ID


def _original_status(v21: dict[str, Any], wallet: float) -> dict[str, Any]:
    settings = v21.get("settings", {})
    auto = v21.get("auto", {})
    scanner = v21.get("scanner", {})
    enabled = _original_enabled(auto)
    until = _session_until(auto)
    max_loss = _num(settings.get("max_loss_per_trade"), 1.0)
    daily_limit = _num(settings.get("daily_loss_limit"), 20.0)
    daily = v21.get("daily") or {}
    realized = _num(daily.get("realized_pnl"))
    return {
        "available": all(server_flags().values()),
        "server_flags": server_flags(),
        "enabled": enabled,
        "paused": False,
        "pause_reason": None,
        "risk_percent": auto.get("twin_risk_percent", round(max_loss / wallet * 100, 2) if wallet > 0 else 0.5),
        "daily_loss_percent": auto.get("twin_daily_percent", round(daily_limit / wallet * 100, 2) if wallet > 0 else 5),
        "risk_revision": int(auto.get("twin_risk_revision") or 0),
        "risk_limits": {
            "max_loss_per_trade_usdt": max_loss,
            "daily_loss_limit_usdt": daily_limit,
            "max_margin_usdt": _num(settings.get("max_margin_per_trade"), 100.0),
            "min_margin_usdt": 5,
            "leverage": 1,
        },
        "margin_mode": "guvenli",
        "margin_modes": [{"mode": "guvenli", "label": "Güvenli", "per_trade_percent": 100, "total_percent": 100}],
        "smart_profit": None,
        "demo": True,
        "daily": {
            "day": daily.get("date"),
            "limit": daily_limit,
            "loss": max(0.0, -realized),
            "realized_loss": max(0.0, -realized),
            "locked": realized <= -daily_limit,
        },
        "scanner": {
            "stage": "ACTIVE" if enabled else "OFF",
            "last_scan_at": scanner.get("last_scan_at") or scanner.get("last_scan"),
            "scanned_symbol_count": scanner.get("scanned_count") or scanner.get("scanned_symbol_count") or 0,
            "candidate_count": len(scanner.get("all_candidates") or []),
        },
        "symbols": list(settings.get("allowed_symbols") or []),
        "session": {
            "hours": int(auto.get("twin_session_hours") or 1),
            "started_at": auto.get("started_at"),
            "expires_at": _iso(until) if enabled and until else None,
            "remaining_seconds": max(0, int(until - time.time())) if enabled and until else 0,
            "authority_limited": False,
            "warning_sent": False,
        },
    }


async def build_status(request: Request) -> dict[str, Any]:
    from . import binance_demo as demo
    from . import v21_demo

    account, error = await _account(request)
    demo_state = demo.state_for(request)
    demo_public = demo.public_status(demo_state)
    v21 = v21_demo.summary_payload(v21_demo.state_for(request))
    auto = v21.get("auto", {})
    armed = bool(demo_public.get("armed"))
    # public_status() kullanıcıya özel anahtarı bilmez; bağlı mı sorusu isteğe (oturuma) göre cevaplanır.
    configured = bool(demo.credentials_configured(request))
    connected = configured and error is None and bool(account) and account.get("connected") is not False
    wallet = _num(account.get("wallet_balance"))
    until = _session_until(auto)
    farfuture = _iso(max(until, time.time() + 3600)) if connected else None
    settings = v21.get("settings", {})
    plans = account.get("plans") or []
    return {
        "version": demo_public.get("version"),
        "mode": "DEMO_COCKPIT",
        "demo": True,
        "host": demo.DEMO_REST_BASE,
        "websocket_host": demo.DEMO_WS_BASE,
        "credentials": {"configured": configured, "fingerprint": None, "storage": "DEMO"},
        "consent": {"active": connected, "expires_at": farfuture, "reason": "NONE" if connected else "EXPIRED", "scope_match": True},
        "authorization": {"valid": connected, "expires_at": farfuture, "reason": "NONE" if connected else "EXPIRED", "scope_match": True},
        "connected": connected,
        "account_rate_limit": None,
        "connection": {"last_checked": demo_public.get("last_checked"), "last_error": error or demo_public.get("last_error"), "clock_offset_ms": None},
        "stream": v21.get("stream"),
        "armed": armed,
        "armed_until": demo_public.get("armed_until"),
        "live_lock_reason": None,
        "real_trading_locked": True,
        "live_auto_trade": _original_enabled(auto),
        "reconciliation_required": False,
        "execution_state": "ARMED" if armed else "LOCKED",
        "recovery_ready": True,
        "recovery_error": None,
        "auto": auto,
        "scanner": {
            "last_scan_at": (v21.get("scanner") or {}).get("last_scan_at"),
            "scanned_symbol_count": (v21.get("scanner") or {}).get("scanned_count", 0),
            "candidate_symbols": [],
            "candidate_count": len((v21.get("scanner") or {}).get("all_candidates") or []),
        },
        "auto_session_until": _iso(until) if _original_enabled(auto) and until else None,
        "policy": {
            "max_positions": settings.get("max_positions"),
            "daily_loss_limit": settings.get("daily_loss_limit"),
            "max_loss_per_trade": settings.get("max_loss_per_trade"),
        },
        "policy_acknowledged": True,
        "readiness": {"ready": connected, "gates": []},
        "account": {
            "wallet_balance": account.get("wallet_balance"),
            "available_balance": account.get("available_balance"),
            "margin_balance": account.get("margin_balance"),
            "unrealized_pnl": account.get("unrealized_pnl"),
            "positions": account.get("positions") or [],
            "open_orders": account.get("open_orders") or [],
        },
        "daily": v21.get("daily"),
        "journal": v21.get("journal"),
        "plans": plans,
        "events": (demo_public.get("events") or [])[:80],
        "emergency": {"active": False, "reason": None, "triggered_at": None},
        "original_v2_live": _original_status(v21, wallet),
        "withdrawals_supported": False,
        "secret_inputs_in_browser": False,
        "profit_guaranteed": False,
    }


async def _renew_arm(request: Request) -> None:
    """Oturum sürerken 10 dakikalık DEMO emir kilidini yenile (yalnız demo)."""
    from . import binance_demo as demo
    from . import v21_demo

    auto = v21_demo.state_for(request).get("auto", {})
    until = _session_until(auto)
    if not (_original_enabled(auto) and until > time.time()):
        return
    demo_state = demo.state_for(request)
    remaining = _num(demo_state.get("armed_until")) - time.time()
    if remaining < ARM_RENEW_BELOW_SECONDS:
        try:
            await demo.demo_arm(request, demo.ArmRequest(confirmation="DEMO"))
        except HTTPException:
            pass


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    await _renew_arm(request)
    return await build_status(request)


@router.get("/original-v2")
async def original_status(request: Request) -> dict[str, Any]:
    return (await build_status(request))["original_v2_live"]


def _ack(name: str):
    async def handler(request: Request, body: Loose | None = None) -> dict[str, Any]:
        return await build_status(request)

    handler.__name__ = name
    return handler


router.add_api_route("/consent", _ack("consent"), methods=["POST"])
router.add_api_route("/policy/acknowledge", _ack("policy_ack"), methods=["POST"])
router.add_api_route("/recovery/check", _ack("recovery"), methods=["POST"])
router.add_api_route("/emergency/clear", _ack("emergency_clear"), methods=["POST"])


@router.post("/arm")
async def arm(request: Request, body: Loose | None = None) -> dict[str, Any]:
    from . import binance_demo as demo

    await demo.demo_arm(request, demo.ArmRequest(confirmation="DEMO"))
    return await build_status(request)


@router.post("/disarm")
async def disarm(request: Request) -> dict[str, Any]:
    from . import binance_demo as demo

    await demo.demo_disarm(request)
    return await build_status(request)


async def _apply_risk(request: Request, body: Loose, wallet: float) -> None:
    from . import v21_demo

    data = body.model_dump()
    updates: dict[str, Any] = {}
    auto = v21_demo.state_for(request)["auto"]
    if data.get("risk_percent") is not None:
        auto["twin_risk_percent"] = _num(data["risk_percent"])
    if data.get("daily_loss_percent") is not None:
        auto["twin_daily_percent"] = _num(data["daily_loss_percent"])
    if data.get("risk_percent") is not None or data.get("daily_loss_percent") is not None:
        auto["twin_risk_revision"] = int(auto.get("twin_risk_revision") or 0) + 1
    if data.get("risk_percent") is not None and wallet > 0:
        updates["max_loss_per_trade"] = round(_clamp(wallet * _num(data["risk_percent"]) / 100, 0.5, 25), 2)
    if data.get("daily_loss_percent") is not None and wallet > 0:
        updates["daily_loss_limit"] = round(_clamp(wallet * _num(data["daily_loss_percent"]) / 100, 5, 250), 2)
    if updates:
        await v21_demo.v21_settings(request, v21_demo.SettingsUpdate(**updates))


async def _wallet(request: Request) -> float:
    account, _ = await _account(request)
    return _num(account.get("wallet_balance"))


@router.post("/original-v2/risk-settings")
async def risk_settings(request: Request, body: Loose) -> dict[str, Any]:
    if body.model_dump().get("understood") is not True:
        raise HTTPException(422, "Önce riski anladığınızı onaylayın.")
    await _apply_risk(request, body, await _wallet(request))
    return await build_status(request)


@router.post("/original-v2/start")
async def start(request: Request, body: Loose) -> dict[str, Any]:
    from . import binance_demo as demo
    from . import v21_demo
    from .strategies.original_v2_demo import STRATEGY_ID

    data = body.model_dump()
    if data.get("understood") is not True:
        raise HTTPException(422, "Önce riski anladığınızı onaylayın.")
    hours = int(data.get("session_hours") or 1)
    if hours not in SESSION_HOURS:
        raise HTTPException(422, "Oturum 1, 4, 12 veya 24 saat olabilir.")
    flags = server_flags()
    if not (flags["enabled"] and flags["send_orders"]):
        raise HTTPException(423, "Kais Original Demo emirleri sunucu ayarında kapalı; sunucu yöneticisi açmadan otomasyon başlatılmadı.")
    wallet = await _wallet(request)
    if wallet <= 0:
        raise HTTPException(412, "Önce Demo hesabını bağlayın.")
    await _apply_risk(request, body, wallet)
    await demo.demo_arm(request, demo.ArmRequest(confirmation="DEMO"))
    await v21_demo.v21_auto_start(
        request, v21_demo.AutoStartRequest(confirmation="DEMO OTOMATİK", strategy_id=STRATEGY_ID),
    )
    state = v21_demo.state_for(request)
    state["auto"]["twin_session_hours"] = hours
    state["auto"]["twin_session_until"] = time.time() + hours * 3600
    v21_demo.persist_state(state)
    return await build_status(request)


async def _stop(request: Request) -> dict[str, Any]:
    from . import v21_demo

    await v21_demo.v21_auto_stop(request)
    state = v21_demo.state_for(request)
    state["auto"]["twin_session_until"] = 0
    v21_demo.persist_state(state)
    return await build_status(request)


@router.post("/original-v2/stop")
async def stop(request: Request, body: Loose | None = None) -> dict[str, Any]:
    return await _stop(request)


@router.post("/auto/stop")
async def auto_stop(request: Request, body: Loose | None = None) -> dict[str, Any]:
    return await _stop(request)


@router.post("/original-v2/extend-session")
async def extend_session(request: Request, body: Loose) -> dict[str, Any]:
    from . import v21_demo

    hours = int(body.model_dump().get("session_hours") or 0)
    if hours not in SESSION_HOURS:
        raise HTTPException(422, "Oturum 1, 4, 12 veya 24 saat olabilir.")
    state = v21_demo.state_for(request)
    if not _original_enabled(state["auto"]):
        raise HTTPException(409, "Önce otomasyonu başlatın.")
    state["auto"]["twin_session_hours"] = hours
    state["auto"]["twin_session_until"] = time.time() + hours * 3600
    v21_demo.persist_state(state)
    return await build_status(request)


@router.post("/original-v2/margin-mode")
async def margin_mode(request: Request, body: Loose | None = None) -> dict[str, Any]:
    raise HTTPException(409, UNSUPPORTED)


@router.post("/original-v2/smart-profit")
async def smart_profit_save(request: Request, body: Loose | None = None) -> dict[str, Any]:
    raise HTTPException(409, UNSUPPORTED)


@router.get("/original-v2/smart-profit")
async def smart_profit_read(request: Request) -> dict[str, Any]:
    raise HTTPException(409, UNSUPPORTED)


@router.post("/original-v2/fill-lock/approve")
async def fill_lock(request: Request, body: Loose | None = None) -> dict[str, Any]:
    raise HTTPException(409, UNSUPPORTED)


@router.post("/position/protection")
async def protection(request: Request, body: Loose | None = None) -> dict[str, Any]:
    raise HTTPException(409, UNSUPPORTED)


def _plan_symbol(request: Request, plan_id: Any) -> str:
    from . import binance_demo as demo

    for plan in demo.state_for(request).get("plans", {}).values():
        if isinstance(plan, dict) and str(plan.get("id")) == str(plan_id):
            return str(plan.get("symbol") or "").upper()
    raise HTTPException(404, "Bu pozisyon bulunamadı.")


@router.post("/position/close")
async def close_position(request: Request, body: Loose) -> dict[str, Any]:
    from . import binance_demo as demo

    data = body.model_dump()
    symbol = _plan_symbol(request, data.get("plan_id")) if data.get("plan_id") else str(data.get("symbol") or "").upper()
    await demo.demo_close_position(
        request, demo.ClosePositionRequest(symbol=symbol, confirmation="DEMO KAPAT"),
    )
    return await build_status(request)


@router.post("/position/reduce")
async def reduce_position(request: Request, body: Loose) -> dict[str, Any]:
    from . import binance_demo as demo

    data = body.model_dump()
    symbol = _plan_symbol(request, data.get("plan_id")) if data.get("plan_id") else str(data.get("symbol") or "").upper()
    percent = _clamp(_num(data.get("percent")), 1, 99)
    account, _ = await _account(request)
    position = next((p for p in account.get("positions") or [] if str(p.get("symbol")).upper() == symbol), None)
    if not position:
        raise HTTPException(404, "Bu paritede açık Demo pozisyonu yok.")
    quantity = _num(position.get("quantity")) * percent / 100
    if quantity <= 0:
        raise HTTPException(422, "Azaltılacak miktar bulunamadı.")
    await demo.demo_reduce_position(
        request, demo.ReducePositionRequest(symbol=symbol, quantity=quantity, confirmation="DEMO AZALT"),
    )
    return await build_status(request)


@router.post("/emergency")
async def emergency(request: Request, body: Loose | None = None) -> dict[str, Any]:
    from . import binance_demo as demo
    from . import v21_demo

    await demo.demo_emergency(
        request, demo.EmergencyRequest(confirmation="DEMO ACİL DURDUR", close_positions=True),
    )
    await v21_demo.v21_auto_stop(request)
    return await build_status(request)
