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
            "max_margin_per_trade": settings.get("max_margin_per_trade"),
            "max_leverage": settings.get("max_leverage", 50),
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
    """Manuel demo pozisyonda stop taşıma (canlıdaki "stop/hedefleri güncelle"nin stop kısmı). Hedef değişimi demo motorunda yok."""
    from . import binance_demo as demo

    data = body.model_dump() if body else {}
    if any(data.get(key) is not None for key in ("tp1", "tp2", "tp3")):
        raise HTTPException(409, "Demo'da hedefler sonradan değiştirilemez; yalnız stop taşınabilir.")
    if data.get("stop_loss") is None:
        raise HTTPException(422, "Değiştirilecek stop yok.")
    symbol = _plan_symbol(request, data.get("plan_id"))
    await demo.demo_move_stop(request, demo.MoveStopRequest(symbol=symbol, stop_loss=float(data["stop_loss"]), confirmation="DEMO STOP GÜNCELLE"))
    return await build_status(request)


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


# ---------------------------------------------------------------------------------------------
# Manuel emir: canlıdaki sihirbazın (ön kontrol → dry-run → gönder) demo karşılıkları.
# Aynı istek/cevap biçimi; yalnız demo-fapi.binance.com'a gider.
# ---------------------------------------------------------------------------------------------
_DEMO_ORDER_FIELDS = {"symbol", "direction", "order_type", "margin_usdt", "leverage", "limit_price", "stop_loss", "tp1", "tp2", "tp3",
                      "tp_split", "limit_expiry_minutes"}


def _demo_order(data: dict[str, Any]):
    from . import binance_demo as demo

    return demo.DemoOrderRequest(**{key: value for key, value in data.items() if key in _DEMO_ORDER_FIELDS and value is not None})


def _demo_policy(request: Request) -> dict[str, Any]:
    from . import v21_demo
    from .execution_core import sanitize_execution_policy

    settings = v21_demo.state_for(request).get("settings", {})
    keys = ("max_margin_per_trade", "max_loss_per_trade", "daily_loss_limit", "max_leverage", "max_positions")
    return sanitize_execution_policy({key: settings[key] for key in keys if settings.get(key) is not None})


@router.post("/order/test")
async def order_test(request: Request, body: Loose) -> dict[str, Any]:
    from . import binance_demo as demo

    order = _demo_order(body.model_dump())
    result = await demo.demo_order_test(request, order)
    preview = result.get("preview") or {}
    return {"ok": True, "exchange_response": {}, "created_order": False, "message": result.get("message"),
            "notional_usdt": preview.get("notional_usdt"), "estimated_stop_loss_usdt": preview.get("risk_per_trade"),
            "tp2_on_exchange": True}


@router.post("/order")
async def order_send(request: Request, body: Loose) -> dict[str, Any]:
    from . import binance_demo as demo

    data = body.model_dump()
    if str(data.get("confirmation") or "").strip().upper() not in {"CANLI EMİR GÖNDER", "DEMO EMİR GÖNDER"}:
        raise HTTPException(422, "Emri göndermek için onay gerekir.")
    return await demo.execute_demo_order(request.app, _demo_order(data), source="MANUAL", request=request)


@router.post("/order/preflight")
async def order_preflight(request: Request, body: Loose) -> dict[str, Any]:
    """Canlıdaki ön kontrolün demo karşılığı: emir göndermez, aynı kapıları demo hesabında okur."""
    from . import binance_demo as demo
    from . import manual_preflight
    from .execution_core import position_notional_usdt, risk_sized_order  # noqa: F401 - risk_sized_order aşağıda kullanılır
    from .liquidation_risk import isolated_liquidation_risk  # noqa: F401
    from . import v21_demo

    data = body.model_dump()
    try:
        symbol = demo.normalize_symbol(str(data.get("symbol") or ""))
        direction = str(data.get("direction") or "").upper()
        margin = float(data.get("margin_usdt"))
        leverage = int(data.get("leverage"))
        entry = float(data["entry"]) if data.get("entry") else None
        stop = float(data["stop_loss"]) if data.get("stop_loss") else None
        client = demo.client_for(request)
        snapshot = await demo.account_snapshot(client)
        policy = _demo_policy(request)
        estimated_loss = None
        sized_notional = None
        if entry and stop and entry != stop:
            try:
                sized = risk_sized_order(entry, stop, {**policy, "max_margin_per_trade": margin, "max_leverage": leverage}, atr=None, manual=True)
                estimated_loss, sized_notional = float(sized["estimated_stop_loss_usdt"]), float(sized["notional_usdt"])
            except (ValueError, KeyError):
                estimated_loss = None
        liquidation = None
        if sized_notional and entry and stop:
            try:
                brackets = await client.signed("GET", "/fapi/v1/leverageBracket", {"symbol": symbol})
                liquidation = manual_preflight.liquidation_check(
                    symbol=symbol, direction=direction, entry=entry, stop=stop, margin_usdt=margin, leverage=leverage,
                    notional_usdt=sized_notional, policy=policy, brackets=brackets)
            except Exception:  # noqa: BLE001 - okunamazsa atlanır (engel sayılmaz), canlıdaki gibi
                liquidation = None
        book = await client.public_get("/fapi/v1/ticker/bookTicker", {"symbol": symbol})
        bid, ask = float(book.get("bidPrice", 0)), float(book.get("askPrice", 0))
        spread = (ask - bid) / ((ask + bid) / 2) * 10000 if bid > 0 and ask >= bid else 0.0
        state = demo.state_for(request)
        occupied = any(str(item.get("symbol")).upper() == symbol for item in snapshot.get("positions", [])) or any(
            str(item.get("symbol")).upper() == symbol for item in snapshot.get("open_orders", []))
        v21 = v21_demo.state_for(request)
        realized = demo.verified_realized_pnl(v21) if isinstance(v21, dict) else 0.0
        report = manual_preflight.build_report(
            symbol=symbol, direction=direction, margin_usdt=margin, leverage=leverage, estimated_loss_usdt=estimated_loss,
            snapshot=snapshot, policy=policy, daily={"realized_pnl": realized}, spread_bps=spread,
            active_plans=list(state.get("plans", {}).values()), readiness_gates=[], budget_left=None, committed=0.0,
            symbol_occupied=bool(occupied), symbol_occupancy_detail="", grid_busy=demo.demo_grid_busy(request.app, state, symbol),
            isolated_error=None, liquidation=liquidation)
        return {**report, "created_order": False}
    except Exception:  # noqa: BLE001 - canlıdaki gibi: okunamazsa gönderimi engellemeyen "kontrol edilemedi" cevabı
        return {"ok": None, "checks": [], "first_blocker": None, "created_order": False,
                "message": "Ön kontrol şu an yapılamadı; emir gönderiminde kontroller yine çalışır."}


@router.put("/policy")
async def policy_update(request: Request, body: Loose) -> dict[str, Any]:
    """Risk limitleri (marjin, işlem başı kayıp, günlük limit, kaldıraç, pozisyon sayısı): demo ayarlarına yazılır;
    hem manuel hem otomatik demo işlemler aynı değerleri kullanır."""
    from . import v21_demo

    data = body.model_dump()
    bounds = {"max_margin_per_trade": (5, 300), "max_loss_per_trade": (0.5, 25), "daily_loss_limit": (5, 1000), "max_leverage": (1, 50), "max_positions": (1, 5)}
    settings = v21_demo.state_for(request).setdefault("settings", {})
    for key, (low, high) in bounds.items():
        if data.get(key) is None:
            continue
        value = _num(data.get(key), float("nan"))
        if value != value or not low <= value <= high:
            raise HTTPException(422, f"{key}: {low} – {high} arasında olmalı.")
        settings[key] = int(value) if key in {"max_leverage", "max_positions"} else value
    persist = getattr(v21_demo, "persist_state", None)
    if callable(persist):
        persist(v21_demo.state_for(request))
    return await build_status(request)



@router.post("/risk/preview")
async def risk_preview(request: Request, body: Loose) -> dict[str, Any]:
    """Canlıdaki risk önizlemesinin demo karşılığı (emir yok; yalnız hesap)."""
    from .execution_core import risk_sized_order

    data = body.model_dump()
    try:
        policy = {**_demo_policy(request), "max_margin_per_trade": float(data["margin_usdt"]), "max_leverage": int(data["leverage"])}
        from .execution_core import sanitize_execution_policy

        risk = risk_sized_order(float(data["entry"]), float(data["stop_loss"]), sanitize_execution_policy(policy),
                                atr=float(data["atr"]) if data.get("atr") else None, manual=bool(data.get("manual")))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"Risk preview reddedildi: {exc}") from exc
    return {"ok": True, "orders_created": False, **{key: risk[key] for key in (
        "entry", "stop", "stop_distance_pct", "max_stop_distance_pct", "leverage", "notional_usdt", "margin_usdt",
        "estimated_stop_loss_usdt", "capped", "atr")}}
