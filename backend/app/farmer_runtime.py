"""KAIS FARMER canlı çalışma zamanı (C10.1). `app.state.live_farmer_runner` BURADA kurulur; HTTP katmanı (`farmer_api`) yalnız bunu okur.

Bu modül yeni güvenlik/durum makinesi EKLEMEZ: portlar mevcut sistemin kapılarına bağlanır (`farmer_engine.live`), emirler yalnız `FarmerEngine` → mevcut
`BinanceLiveGridAdapter` üzerinden `kf-` kimlikleriyle gider. Kurulum açılışta BİR kez yapılır (`install`), kapanışta `shutdown` ile kaldırılır.

Fail-closed: kurulum hata verirse `live_farmer_runner` HİÇ ayarlanmaz (sahte çalıştırıcı yok, RUNNING yok); API "kullanılamıyor" döner. Çok üyeli kayıt
(live_registry) modunda kurulmaz. Normal kapanış acil kapanış DEĞİLDİR: pozisyon kapatılmaz, borsadaki STOP/TP korunur, yeni giriş durur.
Test çerçevesi/harness kodu burada İMPORT EDİLMEZ."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable

from .farmer_engine.engine import FarmerEngine, farmer_spec
from .farmer_engine.feed import FarmerFeed
from .farmer_engine.live import (FarmerRunnerView, LiveGates, LivePnlSource, LiveReservations, LiveSnapshotSource, LiveUniverse,
                                 LoopBridge, daily_loss_blockers)
from .farmer_engine.orchestrator import OFF, FarmerSettings, Orchestrator
from .farmer_engine.pnl import FarmerPnlVerifier
from .farmer_engine.store import FarmerStore
from .grid_engine.ports import ExchangeUnreachable

logger = logging.getLogger(__name__)

ACCOUNT_KEY = "LIVE"
STATE_ATTR = "live_farmer_runner"
TICK_SECONDS = 5.0
RECOVERY_RETRY_SECONDS = 10.0
STORE_FILE = "farmer_live.sqlite3"


class FarmerPrepareError(RuntimeError):
    """Giriş hazırlığı (filtre / isolated marjin / kaldıraç) doğrulanamadı: emir GÖNDERİLMEZ."""


# ------------------------------------------------------------------ borsa adaptörü (her çağrıda taze kimlik bilgisi)
class LazyLiveExchange:
    """`ExchangePort`: her çağrıda mevcut oturum yetkisinden kimlik bilgisini çözer ve MEVCUT `BinanceLiveGridAdapter` kurar.
    Kimlik çözülemezse `ExchangeUnreachable` (motor 'bilinmiyor' sayar; emir gönderilmez)."""

    def __init__(self, build: Callable[[], Any]) -> None:
        self._build = build

    def _adapter(self) -> Any:
        try:
            return self._build()
        except ExchangeUnreachable:
            raise
        except Exception as exc:                                      # noqa: BLE001 — sır sızmasın: yalnız sınıf adı
            raise ExchangeUnreachable(f"FARMER_CREDENTIALS_UNAVAILABLE:{exc.__class__.__name__}") from exc

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._adapter(), name)


# ------------------------------------------------------------------ canlı motor fabrikası
class LiveEngineFactory:
    """`orchestrator.EngineFactory`: hazırlık (filtre + isolated marjin + doğrulanmış kaldıraç) → `FarmerEngine`. Emir göndermez."""

    def __init__(self, application: Any, bridge: LoopBridge, store: FarmerStore, client_factory: Callable[[], Any], live: Any) -> None:
        self.application, self.bridge, self.store, self.client_factory, self.live = application, bridge, store, client_factory, live

    def create(self, grid_id: str, candidate: Any, params: Any, exchange: Any) -> FarmerEngine:
        from .grid_engine.filters import GridFilterError, extract_symbol_filters
        symbol = str(candidate.symbol).upper()
        client = self.client_factory()
        try:
            info = self.bridge(client.public_get("/fapi/v1/exchangeInfo"))
            rules = extract_symbol_filters(info, symbol)
        except GridFilterError as exc:
            raise FarmerPrepareError(f"FILTERS:{exc.__class__.__name__}") from exc
        margin, leverage = float(params.margin_usdt), int(params.leverage)
        if leverage != float(params.leverage) or leverage < 1:
            raise FarmerPrepareError("LEVERAGE_NOT_INTEGER")
        spec = farmer_spec(grid_id, symbol, candidate.direction, float(candidate.entry_price), margin=margin, leverage=leverage,
                           target_usdt=float(params.target_usdt), max_loss_usdt=float(params.max_loss_usdt),
                           tick=float(rules["tickSize"]), step=float(rules["stepSize"]))
        level = spec.levels[0]
        if level.qty < float(rules["minQty"]) or level.qty * float(candidate.entry_price) < float(rules["minNotional"]):
            raise FarmerPrepareError("BELOW_EXCHANGE_MINIMUM")
        margin_type = self.bridge(self.live.set_live_isolated_margin(client, symbol))
        if margin_type != "ISOLATED":
            raise FarmerPrepareError("MARGIN_NOT_ISOLATED")
        audit = self.bridge(self.live.apply_live_verified_leverage(client, symbol, leverage, "ISOLATED"))
        if str((audit or {}).get("margin_type") or "").upper() != "ISOLATED":
            raise FarmerPrepareError("LEVERAGE_OR_MARGIN_UNVERIFIED")
        return FarmerEngine(exchange, self.store, spec)

    def rebuild(self, row: Any, exchange: Any) -> FarmerEngine:
        from .grid_live import grid_spec_from_row
        return FarmerEngine(exchange, self.store, grid_spec_from_row(row))


# ------------------------------------------------------------------ çalışma zamanı
class FarmerRuntime(FarmerRunnerView):
    """`app.state.live_farmer_runner`. Sözleşme: `.orchestrator`, `.apply_settings`, `.configured`, `.authorize`, `.prepare_start`; ayrıca
    `live_bot_runners` arayüzü (`symbol_busy` / `reserved_risk`) — Farmer KAPALI/boşken bu ikisi Grid/DCA kararlarını DEĞİŞTİRMEZ."""

    def __init__(self, application: Any, orchestrator: Orchestrator, store: FarmerStore, *, tick_seconds: float = TICK_SECONDS,
                 retry_seconds: float = RECOVERY_RETRY_SECONDS, configured: bool = False) -> None:
        super().__init__(lambda: self.orchestrator)
        self.application, self.orchestrator, self.store = application, orchestrator, store
        self.tick_seconds, self.retry_seconds, self.configured = tick_seconds, retry_seconds, configured
        self.owner_id: str | None = None
        self.task: asyncio.Task | None = None
        self.stopping = False
        self.last_error = ""

    # ---------- hesap sahipliği ----------
    def authorize(self, owner_id: str) -> bool:
        """İlk yetkili denetleyen bağlanır (mevcut `guard_execution_ownership` ile aynı 'talep' mantığı); başka hesap reddedilir."""
        owner_id = str(owner_id or "")
        if not owner_id:
            return False
        if self.owner_id is None:
            self.owner_id = owner_id
        return owner_id == self.owner_id

    # ---------- ayar ----------
    def apply_settings(self, settings: FarmerSettings) -> None:
        """Yalnız KAPALI ve pozisyonsuzken (API kapısı da denetler). Ayar GERÇEK FarmerStore'a yazılır."""
        orch = self.orchestrator
        if orch.base != OFF or orch.engines:
            raise RuntimeError("SETTINGS_LOCKED_WHILE_ACTIVE")
        from .farmer_engine.margin import MarginGuard
        orch.settings = settings
        orch.guard = MarginGuard(orch.store, orch.account_key, max_positions=settings.max_positions, total_margin_cap=settings.total_margin_cap,
                                 position_margin=settings.confirm.margin_usdt, daily_loss=orch.guard.daily_loss)
        orch._persist()
        self.configured = True

    # ---------- başlatma öncesi kurtarma (kullanıcı eylemiyle; KAPALIyken açılışta borsa okunmaz) ----------
    def prepare_start(self) -> dict[str, Any]:
        orch = self.orchestrator
        if orch.recovered:
            return {"recovered": True}
        report = orch.recover()
        return {"recovered": orch.recovered, "problems": list(report.get("problems") or [])}

    def needs_recovery(self) -> bool:
        """Açılışta borsaya dokunmak gerekli mi? Yalnız kayıtlı/yarım iş varsa (tutulan slot ya da KAPALI olmayan kayıtlı durum)."""
        if self.orchestrator.recovered:
            return False
        saved = self.store.bot(self.orchestrator.account_key)
        return bool(self.store.holds(self.orchestrator.account_key)) or bool(saved and saved["state"] != OFF)

    # ---------- döngü ----------
    async def run(self) -> None:
        orch = self.orchestrator
        while not self.stopping:
            try:
                if self.needs_recovery():
                    await asyncio.to_thread(orch.recover)
                    if not orch.recovered:
                        await asyncio.sleep(self.retry_seconds)
                        continue
                if orch.engines or orch.base != OFF:
                    await self._warm_up()
                await asyncio.to_thread(orch.tick)
                self.last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:                                  # noqa: BLE001 — döngü ölmez; korumalar borsada
                self.last_error = exc.__class__.__name__
                logger.warning("KAIS_FARMER_TICK_FAILED type=%s", exc.__class__.__name__)
            await asyncio.sleep(self.tick_seconds)

    async def _warm_up(self) -> None:
        try:
            from . import v25_execution as live
            client = live.public_client_for(self.application)

            async def rest(params: dict[str, Any]) -> Any:
                return await client.public_get("/fapi/v1/klines", params)
            await self.orchestrator.feed.warm_up(rest)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                      # noqa: BLE001 — ısınma başarısız: veri yok nedeni açıkça raporlanır, giriş olmaz
            logger.warning("KAIS_FARMER_WARMUP_FAILED type=%s", exc.__class__.__name__)

    def start_loop(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run(), name="kais-farmer-loop")

    async def shutdown(self) -> None:
        """Yeni giriş etkinliğini durdur, durumu kaydet, kaynakları bırak. Pozisyon KAPATILMAZ; borsadaki STOP/TP yerinde kalır."""
        self.stopping = True
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        try:
            await asyncio.to_thread(self.orchestrator._persist)
        except Exception as exc:                                      # noqa: BLE001
            logger.warning("KAIS_FARMER_PERSIST_FAILED type=%s", exc.__class__.__name__)
        try:
            from .original_v2_live import apply_farmer_stream_demand
            await apply_farmer_stream_demand(self.application, [])    # Farmer sembol talebini bırak (Original V2 tabanı aynen kalır)
        except Exception as exc:                                      # noqa: BLE001
            logger.warning("KAIS_FARMER_DEMAND_RELEASE_FAILED type=%s", exc.__class__.__name__)
        try:
            self.store.db.close()
        except Exception:                                             # noqa: BLE001
            pass


def settings_from_store(store: FarmerStore, account_key: str) -> tuple[FarmerSettings | None, bool]:
    """Kayıtlı ayarı geri yükler (gerçek store). Yoksa önerilen değerler UYGULANMAZ: (None, False) — kullanıcı ayarlayana dek yapılandırılmamış."""
    from . import farmer_view
    saved = store.bot(account_key)
    if not saved or not saved.get("settings"):
        return None, False
    return farmer_view.parse_settings(saved["settings"]), True


def build_runtime(application: Any, *, store: FarmerStore | None = None, live: Any = None, bridge: LoopBridge | None = None,
                  client_factory: Callable[[], Any] | None = None, exchange: Any = None, feed: FarmerFeed | None = None,
                  data_dir: Path | None = None, account_key: str = ACCOUNT_KEY, snapshot_source: Any = None, universe: Any = None,
                  factory: Any = None, clock: Callable[[], float] | None = None, env: Any = None) -> FarmerRuntime:
    """Mevcut hesap bağlamından Orkestratörü ve çalışma zamanını kurar. Test enjeksiyon noktaları parametrelerdir (üretimde hepsi mevcut sistemden)."""
    from . import account_reservations, daily_ledger, farmer_view, market_universe, original_v2_live
    if live is None:
        from . import v25_execution as live
    loop = asyncio.get_running_loop() if (bridge is None or exchange is None) else None
    bridge = bridge or LoopBridge(loop)
    if store is None:
        from .local_storage import DATA_DIR
        store = FarmerStore(str(Path(data_dir or DATA_DIR) / STORE_FILE), check_same_thread=False)
    state_of = lambda: application.state.v25_execution                                   # noqa: E731

    def client_factory_default() -> Any:
        credentials = bridge(live.auto_session_credentials(application, state_of()))
        if not credentials or len(credentials[0]) < 10 or len(credentials[1]) < 10:
            raise ExchangeUnreachable("FARMER_CREDENTIALS_UNAVAILABLE")
        return live.client_for_with_credentials(application, credentials)

    client_factory = client_factory or client_factory_default
    if exchange is None:
        from .grid_engine.live_adapter import BinanceLiveGridAdapter
        exchange = LazyLiveExchange(lambda: BinanceLiveGridAdapter(client_factory(), loop))

    owner_of = lambda: live.live_reservation_owner(state_of(), None)                    # noqa: E731

    def reconcile() -> None:
        """MEVCUT `reconcile_account_reservations` (tür kapsamı: yalnız FARMER). Taze hesap görüntüsüyle çağrılır; okunamazsa istisna → orkestratör fail-closed."""
        client = client_factory()

        async def run() -> None:
            snapshot = await live.account_snapshot(client, None, None, cached=False, source="farmer")
            await live.reconcile_account_reservations(client, state_of(), owner_of(), snapshot, kinds=frozenset({"FARMER"}))
        bridge(run())

    saved_settings, configured = settings_from_store(store, account_key)
    settings = saved_settings or farmer_view.parse_settings({})
    gates = LiveGates(application, live, env=env)                                    # env=None → os.environ (üretim)
    snapshot_source = snapshot_source or LiveSnapshotSource(bridge, client_factory, live)
    reservations = LiveReservations(account_reservations, owner_of, reconcile=reconcile)
    universe = universe or LiveUniverse(application, bridge, lambda: live.public_client_for(application), original_v2_live, market_universe)
    pnl = FarmerPnlVerifier(store, LivePnlSource(bridge, client_factory, live, application))

    async def apply_demand(symbols: list[str]) -> list[str]:
        return await original_v2_live.apply_farmer_stream_demand(application, symbols)

    stream = getattr(application.state, "original_kline_stream", None)
    feed = feed or FarmerFeed(stream, apply_demand=apply_demand)
    factory = factory or LiveEngineFactory(application, bridge, store, client_factory, live)
    daily_loss = lambda: daily_loss_blockers(state_of(), daily_ledger)                  # noqa: E731
    orchestrator = Orchestrator(account_key=account_key, store=store, settings=settings, exchange=exchange, snapshot_source=snapshot_source,
                                feed=feed, gates=gates, reservations=reservations, universe=universe, factory=factory,
                                set_demand=lambda symbols: bridge(feed.set_demand(symbols)), pnl=pnl, daily_loss=daily_loss,
                                **({"clock": clock} if clock else {}))
    return FarmerRuntime(application, orchestrator, store, configured=configured)


def install(application: Any, **kw: Any) -> FarmerRuntime | None:
    """Açılışta BİR kez. Başarısızlık → None ve `live_farmer_runner` ayarlanmaz (fail-closed). Zaten kuruluysa AYNI örnek döner."""
    existing = getattr(application.state, STATE_ATTR, None)
    if existing is not None:
        return existing
    try:
        from . import live_registry
        if live_registry.enabled():
            logger.warning("KAIS_FARMER_NOT_INSTALLED reason=MULTI_MEMBER_MODE")
            return None
        runtime = build_runtime(application, **kw)
    except Exception as exc:                                          # noqa: BLE001 — kurulum hatası canlı başka hiçbir şeyi etkilemez
        logger.warning("KAIS_FARMER_INSTALL_FAILED type=%s", exc.__class__.__name__)
        return None
    setattr(application.state, STATE_ATTR, runtime)
    runtime.start_loop()
    return runtime


async def uninstall(application: Any) -> None:
    runtime = getattr(application.state, STATE_ATTR, None)
    if runtime is None:
        return
    await runtime.shutdown()
    setattr(application.state, STATE_ATTR, None)
