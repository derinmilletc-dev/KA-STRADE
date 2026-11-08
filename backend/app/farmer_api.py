"""KAIS FARMER — ince, EKLEMELİ HTTP katmanı (C10). Mevcut sözleşmelere dokunmaz; yeni bir `/api/v25/farmer/*` yolu ekler.

Çalışma zamanı `app.state.live_farmer_runner` altında kuruluysa (canlı kapılar açıkken) onu kullanır; kurulu değilse `available=false` döner ve
komutlar 409 ile reddedilir. Bu katman HİÇBİR güvenlik kapısını (ARM, canlı kilit, hesap-geneli günlük kayıp, doluluk, rezervasyon) atlamaz:
komutlar doğrudan orkestratöre gider, giriş kararları orkestratörde kalır. ARM yenileme mevcut `/api/v25/arm` ucunu kullanır.

Çalışma zamanı sözleşmesi (bu katmanın okuduğu alanlar): `.orchestrator` (zorunlu), `.apply_settings(FarmerSettings)` (isteğe bağlı; yoksa ayar yazma 409),
`.configured` (isteğe bağlı bool; yoksa True — kullanıcı daha önce ayar kaydetti mi)."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from . import farmer_view
from .farmer_engine.capability import live_entry_blockers
from .farmer_engine.orchestrator import INTERVENTION, OFF

router = APIRouter(prefix="/api/v25/farmer", tags=["KAIS FARMER"])


def _owner(request: Request) -> dict[str, Any]:
    from .v25_execution import execution_owner
    return execution_owner(request)


def _runtime(request: Request) -> Any | None:
    return getattr(request.app.state, "live_farmer_runner", None)


def _authorized(runtime: Any, owner: dict[str, Any]) -> bool:
    """Hesap yalıtımı (C10.1): çalışma zamanı kendi hesabına bağlıdır; başka hesap onu göremez ve yönetemez. `authorize` yoksa (eski sözleşme) serbest."""
    check = getattr(runtime, "authorize", None)
    return True if check is None else bool(check(str(owner.get("id") or "")))


def _require_runtime(request: Request, owner: dict[str, Any] | None = None) -> Any:
    runtime = _runtime(request)
    if runtime is None:
        raise HTTPException(409, {"code": "FARMER_UNAVAILABLE"})
    if owner is not None and not _authorized(runtime, owner):
        raise HTTPException(403, {"code": "FARMER_NOT_YOUR_ACCOUNT"})
    return runtime


def _arm(request: Request) -> dict[str, Any]:
    state = getattr(request.app.state, "v25_execution", None) or {}
    until = float(state.get("armed_until") or 0)
    return {"active": until > time.time(), "expires_at": until if until > 0 else None}


def _daily_loss(runtime: Any) -> tuple[str, ...]:
    try:
        fn = runtime.orchestrator.guard.daily_loss
        return tuple(fn()) if fn else ()
    except Exception:  # noqa: BLE001 — okunamayan günlük kayıp zaten orkestratörde giriş engelidir; burada yalnız gösterim
        return ("DAILY_LEDGER_UNAVAILABLE",)


@router.get("/status")
async def farmer_status(request: Request) -> dict[str, Any]:
    owner = _owner(request)
    runtime = _runtime(request)
    if runtime is not None and not _authorized(runtime, owner):
        runtime = None                                              # başka hesabın durumu sızmaz
    return farmer_view.build_status(runtime, arm=_arm(request), now=time.time(), daily_loss=_daily_loss(runtime) if runtime is not None else ())


@router.post("/preview")
async def farmer_preview(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    _owner(request)
    try:
        settings = farmer_view.parse_settings(body)
    except farmer_view.SettingsError as exc:
        raise HTTPException(422, {"code": exc.code, "field": exc.field}) from exc
    return {"ok": True, "preview": farmer_view.preview(settings)}


@router.post("/settings")
async def farmer_settings(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    runtime = _require_runtime(request, _owner(request))
    orch = runtime.orchestrator
    if orch.base != OFF or orch.engines:
        raise HTTPException(409, {"code": "SETTINGS_LOCKED_WHILE_ACTIVE"})
    try:
        settings = farmer_view.parse_settings(body, orch.settings.as_dict())
    except farmer_view.SettingsError as exc:
        raise HTTPException(422, {"code": exc.code, "field": exc.field}) from exc
    apply = getattr(runtime, "apply_settings", None)
    if apply is None:
        raise HTTPException(409, {"code": "SETTINGS_NOT_SUPPORTED"})
    await asyncio.to_thread(apply, settings)
    return {"ok": True, "preview": farmer_view.preview(settings)}


@router.post("/start")
async def farmer_start(request: Request) -> dict[str, Any]:
    runtime = _require_runtime(request, _owner(request))
    blockers = live_entry_blockers()
    if blockers:
        raise HTTPException(423, {"code": "FARMER_GATES_CLOSED", "blockers": farmer_view.entry_blockers_public()})
    if getattr(runtime, "configured", True) is not True:
        raise HTTPException(409, {"code": "FARMER_NOT_CONFIGURED"})
    prepare = getattr(runtime, "prepare_start", None)
    if prepare is not None:                                         # kapalıyken açılışta borsa okunmaz; kurtarma kullanıcı başlatınca yapılır
        await asyncio.to_thread(prepare)
    state = await asyncio.to_thread(runtime.orchestrator.start)
    if state != "RUNNING":
        raise HTTPException(409, {"code": "FARMER_NOT_STARTED", "state": state})
    return {"ok": True, "state": state}


@router.post("/stop-bot")
async def farmer_stop_bot(request: Request) -> dict[str, Any]:
    """Yalnız YENİ girişleri durdurur; açık pozisyonlar ve korumalar aynen kalır."""
    runtime = _require_runtime(request, _owner(request))
    return {"ok": True, "state": await asyncio.to_thread(runtime.orchestrator.stop_bot)}


@router.post("/emergency-close")
async def farmer_emergency_close(request: Request) -> dict[str, Any]:
    """Yalnız Farmer'a ait pozisyonları reduce-only kapatır; sonuç doğrulanmış/başarısız ayrımıyla döner."""
    runtime = _require_runtime(request, _owner(request))
    report = await asyncio.to_thread(runtime.orchestrator.emergency_close)
    failed, unowned = list(report.get("failed") or []), list(report.get("unowned") or [])
    return {"ok": True, "state": runtime.orchestrator.state, "closed": len(report.get("closed") or []), "failed": len(failed), "unowned": len(unowned),
            "complete": not failed and not unowned and runtime.orchestrator.state != INTERVENTION}


@router.post("/acknowledge")
async def farmer_acknowledge(request: Request) -> dict[str, Any]:
    """Müdahale sonrası temiz uzlaştırma → OFF (kendiliğinden RUNNING'e dönmez)."""
    runtime = _require_runtime(request, _owner(request))
    return {"ok": True, "state": await asyncio.to_thread(runtime.orchestrator.acknowledge_intervention)}
