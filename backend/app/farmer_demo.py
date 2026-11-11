"""KAIS FARMER (Demo): canlı Farmer motorunun AYNISI; yalnız Binance Futures Demo hesabına bağlanır, gerçek para yok.

Canlı Farmer koduna dokunmaz: `farmer_engine` (orkestratör, motor, kapılar) aynen kullanılır; yalnız hesap/kapı/PnL kaynakları
demo karşılıklarıyla değiştirilir. Her üyenin kendi çalışma zamanı, kendi sqlite kaydı ve kendi hesap anahtarı vardır
("DEMO:<üye>"); canlı Farmer kaydıyla karışmaz. Demo'da ARM ve canlı yetenek bayrakları (KAISTRADE_FARMER_LIVE/TPM) gerekmez.
Adresler: /api/binance-demo/cockpit/farmer/* (canlıdaki /api/v25/farmer/* ile aynı gövdeler)."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from . import binance_demo as demo
from . import farmer_view
from .farmer_engine.live import (FarmerRunnerView, LiveGates, LivePnlSource, LiveReservations, LiveSnapshotSource, LiveUniverse, LoopBridge)
from .farmer_engine.orchestrator import INTERVENTION, OFF, GateState, Orchestrator
from .farmer_engine.feed import FarmerFeed
from .farmer_engine.pnl import FarmerPnlVerifier
from .farmer_engine.store import FarmerStore
from .farmer_runtime import FarmerRuntime, LiveEngineFactory, LazyLiveExchange, settings_from_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/binance-demo/cockpit/farmer", tags=["KAIS FARMER Demo"])

REGISTRY_ATTR = "demo_farmer_registry"
MEMBER_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
STORE_PREFIX = "farmer_demo_"
ACCOUNT_PREFIX = "DEMO"
# Demo'da canlı yetenek kapısı yok (gerçek para yok): durum cevabında "kapı açık" görünsün.
DEMO_ENV = {"KAISTRADE_FARMER_LIVE": "1", "KAISTRADE_FARMER_TPM_VERIFIED": "1"}


def _live_registry_key(uid: str) -> str:
    """Akış sembol talebi üye başına bir anahtarda tutulur; demo anahtarı canlı Farmer talebiyle çakışmaz."""
    return f"demo:{uid}"


# ------------------------------------------------------------------ canlı modül yüzeyinin demo karşılığı
class DemoLive:
    """Farmer adaptörlerinin `live` (v25_execution) üzerinden çağırdığı işlevlerin demo karşılığı."""
    LiveExchangeError = demo.BinanceDemoError

    def __init__(self, application: Any, uid: str, maintenance_of: Any) -> None:
        self.application, self.uid, self._maintenance_of = application, uid, maintenance_of

    # --- kapılar
    def is_armed(self, state: Any) -> bool:
        return True

    def live_execution_blocked(self, state: Any) -> bool:
        return False

    def guard_new_entry(self, maintenance: Any) -> None:
        from .maintenance import guard_new_entry
        guard_new_entry(maintenance)

    def live_reservation_owner(self, state: Any, request: Any) -> str:
        return f"{ACCOUNT_PREFIX}:{self.uid}"

    def live_plan_is_active(self, plan: dict[str, Any]) -> bool:
        return demo._plan_is_active(plan)

    def live_bot_runners(self, application: Any) -> list[Any]:
        return []

    # --- hesap okuma
    async def account_snapshot(self, client: Any, request_id: Any = None, evidence_state: Any = None, *, cached: bool = False, source: str | None = None) -> dict[str, Any]:
        return await demo.account_snapshot(client)

    def public_client_for(self, application: Any) -> Any:
        return demo.BinanceDemoClient(self.application.state.http, "", "", public_only=True)

    async def set_live_isolated_margin(self, client: Any, symbol: str) -> str:
        await demo.set_isolated_margin(client, symbol)
        return "ISOLATED"

    async def apply_live_verified_leverage(self, client: Any, symbol: str, leverage: int, margin_type: str = "ISOLATED") -> dict[str, Any]:
        return await demo.apply_verified_leverage(client, symbol, leverage)

    # --- PnL ve rezervasyon mutabakatı: canlıdaki, borsa istemcisini yalnız `signed` ile kullanan okuma işlevlerinin aynısı
    async def verified_plan_pnl(self, client: Any, plan: dict[str, Any]) -> dict[str, Any]:
        from . import v25_execution as engine
        return await engine.verified_plan_pnl(client, plan)

    async def reconcile_account_reservations(self, client: Any, state: Any, owner: str, snapshot: Any = None, kinds: Any = None) -> None:
        from . import v25_execution as engine
        await engine.reconcile_account_reservations(client, state, owner, snapshot, kinds=kinds)

    def _iso_epoch_ms(self, value: Any) -> int:
        from . import v25_execution as engine
        return engine._iso_epoch_ms(value)


class DemoGates(LiveGates):
    """Demo kapıları: ARM yok, canlı yetenek bayrağı yok; bakım kapısı ve doluluk (grid/DCA/manuel planlar) aynen."""

    def __init__(self, application: Any, live: DemoLive, demo_state: dict[str, Any], uid: str, registry: "DemoFarmerRegistry") -> None:
        super().__init__(application, live, env=DEMO_ENV)
        self.demo_state, self.uid, self.registry = demo_state, uid, registry

    def entry_gates(self) -> GateState:
        safety: list[str] = []
        try:
            self.live.guard_new_entry(getattr(self.application.state, "maintenance", None))
        except Exception:                                            # noqa: BLE001 — bakım kapısı HTTPException fırlatır
            safety.append("MAINTENANCE")
        return GateState((), tuple(safety))

    def foreign_occupied(self, symbol: str) -> tuple[str, ...]:
        out: list[str] = []
        try:
            if demo.demo_grid_busy(self.application, self.demo_state, symbol):
                out.append("OTHER_BOT")
        except Exception:                                            # noqa: BLE001 — okunamayan doluluk = dolu say
            out.append("OCCUPANCY_UNREADABLE")
        for plan in (self.demo_state.get("plans") or {}).values():
            if isinstance(plan, dict) and str(plan.get("symbol") or "").upper() == symbol and demo._plan_is_active(plan):
                out.append("ACTIVE_PLAN")
        return tuple(out)


class DemoPnlSource(LivePnlSource):
    def __init__(self, bridge: LoopBridge, client_factory: Any, live: DemoLive, demo_state: dict[str, Any]) -> None:
        super().__init__(bridge, client_factory, live, None)
        self.demo_state = demo_state

    def foreign_overlaps(self, symbol: str, start_ms: int, end_ms: int) -> list[str]:
        out: list[str] = []
        for plan in (self.demo_state.get("plans") or {}).values():
            if not isinstance(plan, dict) or str(plan.get("symbol") or "").upper() != symbol:
                continue
            start = self.live._iso_epoch_ms(plan.get("created_at"))
            closed = plan.get("closed_at")
            end = self.live._iso_epoch_ms(closed) if closed else float("inf")
            if start <= end_ms and start_ms <= end:
                out.append("DEMO_PLAN")
        return out


class _NoSharedUniverse:
    """Canlı Original V2 evrenini paylaşma: demo Farmer her gün kendi evrenini üretir (aynı sıralama işlevi)."""
    @staticmethod
    def settings(state: Any) -> Any:
        raise RuntimeError("DEMO_OWN_UNIVERSE")

    @staticmethod
    def selected_symbols(config: Any) -> list[str]:
        return []


def demo_daily_loss_blockers(v21_state: dict[str, Any]) -> tuple[str, ...]:
    """Demo günlük zarar kapısı: demo risk ayarındaki günlük limit (manuel + otomatik + Farmer aynı sayı) ve doğrulanmış gerçekleşen PnL."""
    try:
        limit = float((v21_state.get("settings") or {}).get("daily_loss_limit"))
    except (TypeError, ValueError):
        return ()
    if limit <= 0:
        return ()
    try:
        realized = float(demo.verified_realized_pnl(v21_state))
    except Exception:                                                # noqa: BLE001 — okunamayan defter: yeni giriş kapalı
        return ("DAILY_LEDGER_UNAVAILABLE",)
    return ("DAILY_LOSS_LIMIT",) if realized <= -limit else ()


class DemoFarmerRuntime(FarmerRuntime):
    """Canlı çalışma zamanının aynısı; yalnız mum ısınması demo (herkese açık) istemciyle yapılır."""

    def __init__(self, *args: Any, live: DemoLive, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self._demo_live = live

    async def _warm_up(self) -> None:
        try:
            client = self._demo_live.public_client_for(self.application)

            async def rest(params: dict[str, Any]) -> Any:
                return await client.public_get("/fapi/v1/klines", params)
            await self.orchestrator.feed.warm_up(rest)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                      # noqa: BLE001 — ısınma başarısız: neden raporlanır, giriş olmaz
            logger.warning("KAIS_FARMER_DEMO_WARMUP_FAILED type=%s", exc.__class__.__name__)


def build_demo_runtime(application: Any, request: Request, uid: str, registry: "DemoFarmerRegistry", *, store: FarmerStore | None = None,
                       data_dir: Path | None = None) -> DemoFarmerRuntime:
    from . import market_universe, original_v2_live
    from .v21_demo import state_for as v21_state_for
    demo_state = demo.state_for(request)
    v21_state = v21_state_for(request)
    loop = asyncio.get_running_loop()
    bridge = LoopBridge(loop)
    if store is None:
        from .local_storage import DATA_DIR
        store = FarmerStore(str(Path(data_dir or DATA_DIR) / f"{STORE_PREFIX}{uid}.sqlite3"), check_same_thread=False)
    live = DemoLive(application, uid, None)
    facade = SimpleNamespace(state=SimpleNamespace(
        http=application.state.http, maintenance=getattr(application.state, "maintenance", None),
        v25_execution=demo_state))

    def client_factory() -> Any:
        return demo.client_for_state(application, demo_state)

    account_key = f"{ACCOUNT_PREFIX}:{uid}"
    from .grid_engine.live_adapter import BinanceLiveGridAdapter
    exchange = LazyLiveExchange(lambda: BinanceLiveGridAdapter(client_factory(), loop))
    owner_of = lambda: live.live_reservation_owner(demo_state, None)                     # noqa: E731

    def reconcile() -> None:
        client = client_factory()

        async def run() -> None:
            snapshot = await live.account_snapshot(client, None, None, cached=False, source="farmer")
            await live.reconcile_account_reservations(client, demo_state, owner_of(), snapshot, kinds=frozenset({"FARMER"}))
        bridge(run())

    saved_settings, configured = settings_from_store(store, account_key)
    settings = saved_settings or farmer_view.parse_settings({})
    from . import account_reservations
    gates = DemoGates(facade, live, demo_state, uid, registry)
    snapshot_source = LiveSnapshotSource(bridge, client_factory, live)
    reservations = LiveReservations(account_reservations, owner_of, reconcile=reconcile)
    universe = LiveUniverse(facade, bridge, lambda: live.public_client_for(application), _NoSharedUniverse, market_universe)
    pnl = FarmerPnlVerifier(store, DemoPnlSource(bridge, client_factory, live, demo_state))

    async def apply_demand(symbols: list[str]) -> list[str]:
        # Akış talebi bu üyenin DEMO anahtarında tutulur; canlı Farmer talebi (üye kimliği anahtarı) ile çakışmaz, akışa birleşimi gider.
        from . import live_registry
        token = live_registry.bind(_live_registry_key(uid))
        try:
            return await original_v2_live.apply_farmer_stream_demand(application, symbols)
        finally:
            live_registry.CURRENT_USER.reset(token)

    stream = getattr(application.state, "original_kline_stream", None)
    feed = FarmerFeed(stream, apply_demand=apply_demand)
    factory = LiveEngineFactory(facade, bridge, store, client_factory, live)
    orchestrator = Orchestrator(account_key=account_key, store=store, settings=settings, exchange=exchange, snapshot_source=snapshot_source,
                                feed=feed, gates=gates, reservations=reservations, universe=universe, factory=factory,
                                set_demand=lambda symbols: bridge(feed.set_demand(symbols)), pnl=pnl,
                                daily_loss=lambda: demo_daily_loss_blockers(v21_state))
    return DemoFarmerRuntime(application, orchestrator, store, configured=configured, live=live)


class DemoFarmerRegistry:
    """Üye başına ayrı demo Farmer çalışma zamanı. Diğer demo botların (grid/DCA/manuel) coin doluluğu görünümü için `symbol_busy` sunar."""
    is_farmer_view = True

    def __init__(self, application: Any, *, data_dir: Path | None = None) -> None:
        self.application, self.data_dir = application, data_dir
        self.runtimes: dict[str, DemoFarmerRuntime] = {}

    @staticmethod
    def uid_of(user_id: str) -> str:
        uid = str(user_id or "").strip() or "owner"
        if not MEMBER_ID.match(uid):
            raise ValueError("FARMER_MEMBER_INVALID")
        return uid

    def get(self, uid: str) -> DemoFarmerRuntime | None:
        return self.runtimes.get(uid)

    def for_request(self, request: Request, user_id: str) -> DemoFarmerRuntime:
        uid = self.uid_of(user_id)
        runtime = self.runtimes.get(uid)
        if runtime is None:
            from . import live_registry
            token = live_registry.bind(_live_registry_key(uid))
            try:
                runtime = build_demo_runtime(self.application, request, uid, self, data_dir=self.data_dir)
                runtime.owner_id = uid
                runtime.start_loop()
            finally:
                live_registry.CURRENT_USER.reset(token)
            self.runtimes[uid] = runtime
        return runtime

    def symbol_busy(self, symbol: str, user_id: str = "", *, owner: bool = False) -> bool:
        runtime = self.runtimes.get(str(user_id or "").strip() or "owner")
        return bool(runtime and any(e.sym == symbol for e in runtime.orchestrator.engines.values()))

    def reserved_risk(self, user_id: str, *, owner: bool = False, exclude: str | None = None) -> dict[str, Any]:
        runtime = self.runtimes.get(str(user_id or "").strip() or "owner")
        return FarmerRunnerView(lambda: runtime.orchestrator if runtime else None).reserved_risk(user_id, owner=owner, exclude=exclude)

    async def shutdown(self) -> None:
        from . import live_registry
        for uid, runtime in list(self.runtimes.items()):
            token = live_registry.bind(_live_registry_key(uid))
            try:
                await runtime.shutdown()
            except Exception as exc:                                  # noqa: BLE001
                logger.warning("KAIS_FARMER_DEMO_SHUTDOWN_FAILED type=%s", exc.__class__.__name__)
            finally:
                live_registry.CURRENT_USER.reset(token)
        self.runtimes.clear()


async def uninstall(application: Any) -> None:
    registry = getattr(application.state, REGISTRY_ATTR, None)
    if registry is not None:
        await registry.shutdown()
        setattr(application.state, REGISTRY_ATTR, None)


def registry_for(application: Any) -> DemoFarmerRegistry:
    existing = getattr(application.state, REGISTRY_ATTR, None)
    if existing is None:
        existing = DemoFarmerRegistry(application)
        setattr(application.state, REGISTRY_ATTR, existing)
    return existing


# ------------------------------------------------------------------ HTTP: canlıdaki /farmer/* ile aynı gövdeler
def _actor(request: Request) -> str:
    return demo._grid_actor(request)[0]


def _runtime(request: Request) -> DemoFarmerRuntime:
    try:
        return registry_for(request.app).for_request(request, _actor(request))
    except ValueError as exc:
        raise HTTPException(409, {"code": "FARMER_UNAVAILABLE"}) from exc


def _arm() -> dict[str, Any]:
    return {"active": True, "expires_at": None}                     # Demo'da ARM yok


def _daily_loss(request: Request) -> tuple[str, ...]:
    from .v21_demo import state_for as v21_state_for
    return demo_daily_loss_blockers(v21_state_for(request))


@router.get("/status")
async def farmer_status(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    return {**farmer_view.build_status(runtime, arm=_arm(), env=DEMO_ENV, now=time.time(), daily_loss=_daily_loss(request)), "account": "DEMO"}


@router.post("/preview")
async def farmer_preview(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    try:
        settings = farmer_view.parse_settings(body, require_user=True)
    except farmer_view.SettingsError as exc:
        raise HTTPException(422, {"code": exc.code, "field": exc.field}) from exc
    return {"ok": True, "preview": farmer_view.preview(settings)}


@router.post("/settings")
async def farmer_settings(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime(request)
    orch = runtime.orchestrator
    if orch.base != OFF or orch.engines:
        raise HTTPException(409, {"code": "SETTINGS_LOCKED_WHILE_ACTIVE"})
    try:
        settings = farmer_view.parse_settings(body, orch.settings.as_dict(), require_user=True)
    except farmer_view.SettingsError as exc:
        raise HTTPException(422, {"code": exc.code, "field": exc.field}) from exc
    await asyncio.to_thread(runtime.apply_settings, settings)
    return {"ok": True, "preview": farmer_view.preview(settings)}


@router.post("/start")
async def farmer_start(request: Request) -> dict[str, Any]:
    from . import binance_demo
    runtime = _runtime(request)
    binance_demo.guard_new_entry(getattr(request.app.state, "maintenance", None))
    from . import live_registry
    live_single = getattr(request.app.state, "live_farmer_runner", None)
    if not live_registry.enabled() and live_single is not None and (live_single.orchestrator.base != OFF or live_single.orchestrator.engines):
        # Tek hesap modunda akış talebi tek listedir; canlı Farmer çalışırken demo Farmer onun talebini ezmesin.
        raise HTTPException(409, {"code": "LIVE_FARMER_ACTIVE"})
    if runtime.configured is not True:
        raise HTTPException(409, {"code": "FARMER_NOT_CONFIGURED"})
    await asyncio.to_thread(runtime.prepare_start)
    state = await asyncio.to_thread(runtime.orchestrator.start)
    if state != "RUNNING":
        raise HTTPException(409, {"code": "FARMER_NOT_STARTED", "state": state})
    return {"ok": True, "state": state}


@router.post("/stop-bot")
async def farmer_stop_bot(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    return {"ok": True, "state": await asyncio.to_thread(runtime.orchestrator.stop_bot)}


@router.post("/emergency-close")
async def farmer_emergency_close(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    report = await asyncio.to_thread(runtime.orchestrator.emergency_close)
    failed, unowned = list(report.get("failed") or []), list(report.get("unowned") or [])
    return {"ok": True, "state": runtime.orchestrator.state, "closed": len(report.get("closed") or []), "failed": len(failed), "unowned": len(unowned),
            "complete": not failed and not unowned and runtime.orchestrator.state != INTERVENTION}


@router.post("/acknowledge")
async def farmer_acknowledge(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    return {"ok": True, "state": await asyncio.to_thread(runtime.orchestrator.acknowledge_intervention)}
