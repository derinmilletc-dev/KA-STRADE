"""KAIS FARMER C10.1 — çalışma zamanı kablolaması, rezervasyon mutabakatı, hesap yalıtımı, Farmer KAPALI.

GERÇEK (bu dosyada çalışan gerçek kod): farmer_runtime (kurulum/kapanış/fail-closed), Orchestrator, FarmerStore (sqlite), LiveGates / LiveReservations /
LiveSnapshot eşlemesi, account_reservations (sqlite), v25_execution'ın GERÇEK kapıları (is_armed, live_execution_blocked) ve GERÇEK
`reconcile_account_reservations` (tür kapsamlı), farmer_api işleyicileri.
SAHTE (STUB): Binance (FakeExchange / FakeClient — ağ yok), kline akışı, strateji değerlendirici, fastapi (yoksa kabuk). Gerçek Binance davranışı BURADA kanıtlanmaz."""
from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import account_reservations as ar  # noqa: E402
from app import farmer_runtime as fr  # noqa: E402
from app import v25_execution as real_live  # noqa: E402
from app.farmer_engine import orchestrator as o  # noqa: E402
from app.farmer_engine.ids import farmer_client_order_id  # noqa: E402
from app.farmer_engine.live import FarmerRunnerView  # noqa: E402
from app.farmer_engine.store import FarmerStore  # noqa: E402
from app.grid_engine.ports import NEW, PARTIAL  # noqa: E402
from tests.test_farmer_orchestrator import ACC, Rig  # noqa: E402

try:
    import fastapi as _fa
    HAS_FASTAPI = isinstance(getattr(_fa, "APIRouter", None), type)
except ImportError:
    HAS_FASTAPI = False
if HAS_FASTAPI:
    from fastapi import HTTPException  # noqa: E402
    from app import farmer_api as api  # noqa: E402

ONE = {"BBBUSDT": {"confidence": 50}, "CCCUSDT": {"confidence": 50}}       # yalnız AAAUSDT onaylanır
OWNER = "u1"


class SyncBridge:
    """Üretimdeki LoopBridge yerine (test): coroutine'i senkron çalıştırır."""
    def __call__(self, coro):
        return asyncio.run(coro)


class FakeClient:                                                         # STUB: Binance imzalı istemci
    def __init__(self):
        self.calls, self.orders, self.fail = [], {}, False

    async def signed(self, method, path, params=None):
        self.calls.append((method, path, dict(params or {})))
        if self.fail:
            raise RuntimeError("down")
        if (method, path) == ("GET", "/fapi/v1/order"):
            cid = (params or {}).get("origClientOrderId")
            if cid in self.orders:
                return [dict(self.orders[cid], orderId=1)]
            raise real_live.LiveExchangeError("Unknown order", exchange_code=-2013)
        return []

    async def public_get(self, path, params=None):
        return {}


class LiveProxy:
    """GERÇEK v25_execution; yalnız hesap görüntüsü okuması (ağ) sahte."""
    def __init__(self, client):
        self._client, self.snapshot = client, {"positions": [], "open_orders": []}

    def __getattr__(self, name):
        return getattr(real_live, name)

    async def account_snapshot(self, client, *a, **kw):
        if self._client.fail:
            raise RuntimeError("down")
        return self.snapshot


def new_state(owner=OWNER):
    return {"armed_until": time.time() + 3600, "real_trading_locked": False, "recovery_ready": True, "policy": {}, "plans": {},
            "auto": {"enabled": False, "session_until": 0.0}, "emergency": {"active": False},
            "live_session_authorization": {"user_id": owner}}


OPEN_ENV = {"KAISTRADE_FARMER_LIVE": "1", "KAISTRADE_FARMER_TPM_VERIFIED": "1"}      # yalnız test ortamı eşlemesi; süreç ortamı DEĞİŞMEZ


def new_app(state=None, **runners):
    from app.maintenance import default_maintenance_state
    return SimpleNamespace(state=SimpleNamespace(v25_execution=state or new_state(), maintenance=default_maintenance_state(), **runners))


class Kit:
    """Gerçek çalışma zamanı + gerçek rezervasyon defteri + sahte borsa."""
    def __init__(self, rig=None, app=None, env=OPEN_ENV, **rig_kw):
        self.env = env
        self.rig = rig or Rig(table=dict(ONE), store=FarmerStore(":memory:", check_same_thread=False), **rig_kw)
        self.app = app or new_app()
        self.client = FakeClient()
        self.live = LiveProxy(self.client)
        self.runtime = self.build()

    def build(self):
        r = self.rig
        runtime = fr.build_runtime(self.app, store=r.store, live=self.live, bridge=SyncBridge(), client_factory=lambda: self.client, exchange=r.ex,
                                   feed=r.feed, snapshot_source=r.source, universe=r.uni, factory=r.factory, account_key=ACC, clock=r.clock, env=self.env)
        self.app.state.live_farmer_runner = runtime
        return runtime

    def restart(self):
        """Süreç yeniden başlar: aynı defter + aynı borsa, YENİ çalışma zamanı; eski SUBMITTED satırlar başka EPOCH'a ait olur."""
        ar.get_store().db.execute("UPDATE reservations SET epoch='OLD' WHERE state IN ('RESERVED','SUBMITTED')")
        r2 = Rig(store=self.rig.store, ex=self.rig.ex, table=dict(ONE))
        r2.now = self.rig.now
        self.rig = r2
        self.runtime = self.build()
        return self.runtime

    def go(self):
        """Kullanıcı START: kurtarma → start → bir tur."""
        report = self.runtime.prepare_start()
        self.runtime.orchestrator.start()
        return report, self.runtime.orchestrator.tick(self.rig.now)

    @property
    def orch(self):
        return self.runtime.orchestrator


def rows_all(owner=OWNER):
    return ar.rows(owner, states=(ar.RESERVED, ar.SUBMITTED, ar.CONSUMED, ar.RELEASED))


def live_kf(ex):
    return sorted(x.client_id for x in ex.orders.values() if x.status in (NEW, PARTIAL) and x.client_id.startswith("kf-"))


def seed_submitted(store, symbol, gid, *, kind="FARMER", old_epoch=False, owner=OWNER):
    """Önceki süreçten kalan SUBMITTED rezervasyon (+ Farmer ise HELD slot)."""
    cid = farmer_client_order_id(gid, "LONG", 1, "E", 0) if kind == "FARMER" else f"{kind.lower()}-{gid}-L01E0"
    token = ar.reserve(owner, kind, symbol, 0.0, ttl=300.0)
    ar.mark_submitted(token, cid)
    if kind == "FARMER":
        assert store.claim_entry(ACC, symbol, "15m", 1_000_000, gid, 50.0, cap_usdt=100.0, max_slots=2) == "CLAIMED"
    if old_epoch:
        ar.get_store().db.execute("UPDATE reservations SET epoch='OLD' WHERE token=?", (token,))
    return token, cid


class Base(unittest.TestCase):
    def setUp(self):
        self._store = ar.get_store() if ar._store is not None else None
        ar.use_store(ar.ReservationStore())
        self._blockers = fr.__dict__.get("live_entry_blockers")

    def tearDown(self):
        ar.use_store(self._store)


# ====================================================================== çalışma zamanı
class TestRuntimeLifecycle(Base):
    def _install(self, app, **kw):
        async def go():
            return fr.install(app, bridge=SyncBridge(), client_factory=lambda: FakeClient(), live=LiveProxy(FakeClient()),
                              store=FarmerStore(":memory:", check_same_thread=False), **kw)
        return asyncio.run(go())

    def test_runner_created_once_and_stored_on_app_state(self):
        app = new_app()
        first = self._install(app)
        self.assertIsNotNone(first)
        self.assertIs(app.state.live_farmer_runner, first)
        self.assertIs(self._install(app), first)                       # ikinci kurulum AYNI örnek
        asyncio.run(fr.uninstall(app))

    def test_runner_is_not_created_per_request(self):
        """HTTP katmanı örnek KURMAZ: kurulu değilse yalnız 'kullanılamıyor' döner."""
        import inspect
        src = inspect.getsource(sys.modules["app.farmer_api"]) if "app.farmer_api" in sys.modules else Path("app/farmer_api.py").read_text()
        self.assertNotIn("build_runtime", src)
        self.assertNotIn("farmer_runtime", src)
        self.assertNotIn("setattr(", src)

    def test_startup_failure_is_fail_closed(self):
        app = new_app()
        original = fr.build_runtime
        fr.build_runtime = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.assertIsNone(fr.install(app))
        finally:
            fr.build_runtime = original
        self.assertIsNone(getattr(app.state, "live_farmer_runner", None))      # sahte çalıştırıcı YOK
        self.assertEqual(real_live.live_bot_runners(app), [])                  # diğer botlar etkilenmez

    def test_not_installed_in_multi_member_mode(self):
        from app import live_registry
        original = live_registry.enabled
        live_registry.enabled = lambda: True
        try:
            self.assertIsNone(fr.install(new_app()))
        finally:
            live_registry.enabled = original

    def test_shutdown_stops_loop_removes_runner_and_never_closes_positions(self):
        k = Kit()
        k.go()
        ex, before = k.rig.ex, (live_kf(k.rig.ex), len(k.rig.ex.placed), dict(k.rig.ex.positions))
        self.assertEqual(len(before[0]), 2)                                    # STOP + TP
        k.runtime.start_loop = lambda: None
        asyncio.run(fr.uninstall(k.app)) if False else None

        async def stop():
            loop_task = asyncio.create_task(asyncio.sleep(60))
            k.runtime.task = loop_task
            await k.runtime.shutdown()
            return loop_task
        task = asyncio.run(stop())
        self.assertTrue(task.cancelled())
        self.assertTrue(k.runtime.stopping)
        self.assertEqual((live_kf(ex), len(ex.placed), dict(ex.positions)), before)    # kapanış emri yok, STOP/TP yerinde, pozisyon aynı
        self.assertEqual(ex.cancelled, [])
        asyncio.run(fr.uninstall(k.app))                                              # kurulu çalışma zamanı kaldırılır
        self.assertIsNone(k.app.state.live_farmer_runner)

    def test_shutdown_persists_runtime_state(self):
        store = FarmerStore(":memory:", check_same_thread=False)
        k = Kit(rig=Rig(table=dict(ONE), store=store))
        k.go()
        store_db = k.runtime.store.db
        saved_before = store.bot(ACC)
        self.assertEqual(saved_before["state"], o.RUNNING)
        k.runtime.store.db = SimpleNamespace(close=lambda: None, execute=store_db.execute, commit=store_db.commit)   # kapanış defteri kapatmasın (test)
        asyncio.run(k.runtime.shutdown())
        self.assertEqual(store.bot(ACC)["state"], o.RUNNING)                           # durum korundu; yeniden başlatmada kurtarılır

    def test_other_runners_untouched_and_runner_view_is_neutral_when_idle(self):
        grid, dca = SimpleNamespace(symbol_busy=lambda *a, **k: False), SimpleNamespace(symbol_busy=lambda *a, **k: False)
        app = new_app(live_grid_runner=grid, live_dca_runner=dca)
        before = real_live.live_bot_runners(app)
        k = Kit(app=app)
        after = real_live.live_bot_runners(app)
        self.assertEqual(after[:2], before)                                     # Grid/DCA sırası ve kimliği aynı
        self.assertIs(after[2], k.runtime)
        self.assertIs(app.state.live_grid_runner, grid)
        self.assertIs(app.state.live_dca_runner, dca)
        self.assertIsInstance(k.runtime, FarmerRunnerView)
        self.assertFalse(k.runtime.symbol_busy("AAAUSDT"))                      # boşta: doluluk/risk eklemez
        self.assertEqual(k.runtime.reserved_risk(OWNER)["worst_loss_usdt"], 0)

    def test_multi_account_isolation(self):
        a, b = Kit(), Kit()
        self.assertIsNot(a.runtime, b.runtime)
        self.assertIsNot(a.runtime.store, b.runtime.store)
        self.assertTrue(a.runtime.authorize("u1"))
        self.assertFalse(a.runtime.authorize("u2"))                             # başka hesap bağlanamaz
        self.assertFalse(a.runtime.authorize(""))
        self.assertTrue(b.runtime.authorize("u2"))
        a.go()
        self.assertEqual(b.rig.store.holds(ACC), [])                            # a'nın slotu b'nin defterinde yok
        self.assertEqual(len(b.orch.engines), 0)


class TestFarmerOff(Base):
    def test_off_makes_no_exchange_read_no_order_no_reservation_no_demand(self):
        k = Kit()
        ex = k.rig.ex
        reads = dict(ex.reads)
        out = k.orch.tick(k.rig.now)
        self.assertEqual(out, {"state": o.OFF, "idle": True})
        self.assertEqual(ex.reads, reads)
        self.assertEqual(ex.placed, [])
        self.assertEqual(k.rig.source.reads, 0)
        self.assertEqual(k.client.calls, [])
        self.assertEqual(rows_all(), [])
        self.assertEqual(k.rig.demands, [])
        self.assertEqual(k.runtime.needs_recovery(), False)                     # temiz KAPALI: açılışta borsaya dokunulmaz
        self.assertEqual(k.rig.store.holds(ACC), [])

    @unittest.skipUnless(HAS_FASTAPI, "fastapi yok")
    def test_status_reads_have_no_side_effects(self):
        original = api._owner
        api._owner = lambda request: {"id": OWNER, "role": "OWNER"}
        try:
            k = Kit()
            req = SimpleNamespace(app=k.app)
            snap = (dict(k.rig.ex.reads), k.rig.source.reads, list(k.client.calls), rows_all(), k.rig.store.bot(ACC), len(k.rig.ex.placed))
            for _ in range(3):
                asyncio.run(api.farmer_status(req))
            self.assertEqual((dict(k.rig.ex.reads), k.rig.source.reads, list(k.client.calls), rows_all(), k.rig.store.bot(ACC), len(k.rig.ex.placed)), snap)
        finally:
            api._owner = original


# ====================================================================== rezervasyon mutabakatı
class TestReservationReconcile(Base):
    def test_reconcile_callback_is_wired_to_the_existing_function(self):
        k = Kit()
        seed_submitted(k.rig.store, "AAAUSDT", "Fseed0000001")
        k.rig.store.release_hold("Fseed0000001")
        k.runtime.prepare_start()
        asked = [c for c in k.client.calls if c[:2] == ("GET", "/fapi/v1/order")]
        self.assertTrue(asked)                                                    # mevcut mutabakat borsaya clientOrderId ile sordu
        self.assertTrue(asked[0][2]["origClientOrderId"].startswith("kf-"))
        self.assertIsNotNone(k.orch.reservations._reconcile)

    def test_matching_reservation_survives_restart(self):
        k = Kit()
        k.go()
        self.assertEqual(len(k.orch.engines), 1)
        ex, orders = k.rig.ex, live_kf(k.rig.ex)
        placed = len(ex.placed)
        k.restart()
        report, _ = k.go()
        self.assertTrue(k.orch.recovered, report)
        self.assertEqual(len(k.orch.engines), 1)                                  # Farmer pozisyonu + ledger eşleşti: korunur
        self.assertEqual(live_kf(ex), orders)                                     # STOP + TP aynen
        self.assertEqual(len(ex.placed), placed)                                  # yeniden giriş / yeni emir YOK
        self.assertNotEqual(k.orch.base, o.INTERVENTION)
        self.assertEqual([r for r in rows_all() if r["state"] == ar.RELEASED], [])

    def test_open_but_exchange_flat_finalizes_releases_slot_and_queues_pnl(self):
        k = Kit()
        k.go()
        gid = next(iter(k.orch.engines))
        ex = k.rig.ex
        for cid in live_kf(ex):                                                   # STOP/TP borsada tetiklendi (sahte): pozisyon düz, emirler yok
            ex.orders[next(key for key, v in ex.orders.items() if v.client_id == cid)].status = "CANCELED"
        ex.set_position("AAAUSDT", 0.0)
        placed = len(ex.placed)
        k.restart()
        k.runtime.prepare_start()
        self.assertEqual(len(ex.placed), placed)                                  # yeni emir YOK
        self.assertEqual(k.rig.store.holds(ACC)[0]["state"] if k.rig.store.holds(ACC) else "RELEASED", "RELEASED")
        self.assertEqual(k.orch.guard.slots().open, 0)                            # slot bırakıldı
        self.assertIsNotNone(k.rig.store.result(gid))                             # PnL hattına girdi (C8: doğrulanana dek PENDING/UNVERIFIED)

    def pending(self):
        """Giriş emri borsaya ULAŞMADI (yanıt kaybı): HELD slot + SUBMITTED rezervasyon, ledger'da PENDING_SEND."""
        k = Kit()
        k.rig.ex.drop_entry_before = True
        k.go()
        gid = next(iter(k.orch.engines))
        cid = k.rig.store.orders(gid, "E")[0]["client_id"]
        token = next(r["token"] for r in rows_all() if r["kind"] == "FARMER")
        self.assertEqual(ar.get(token)["state"], ar.SUBMITTED)
        return k, gid, cid, token

    def test_held_without_exchange_entry_is_released_safely_and_never_resent(self):
        k, gid, cid, token = self.pending()
        placed = len(k.rig.ex.placed)
        k.restart()
        k.runtime.prepare_start()
        self.assertEqual(k.rig.store.holds(ACC), [])                              # HELD bırakıldı
        self.assertEqual(ar.get(token)["state"], ar.RELEASED)                     # mevcut mutabakat: borsada yok → serbest
        self.assertEqual(len(k.rig.ex.placed), placed)                            # ESKİ giriş yeniden GÖNDERİLMEDİ
        self.assertTrue(k.orch.recovered)

    def test_submitted_proven_absent_is_released_and_entry_never_resent(self):
        k, gid, cid, token = self.pending()
        placed = len(k.rig.ex.placed)
        k.restart()
        k.go()                                                                    # START sonrası bir tur daha
        self.assertEqual(ar.get(token)["state"], ar.RELEASED)
        self.assertNotIn(cid, [p[2] for p in k.rig.ex.placed[placed:]])
        self.assertEqual([p for p in k.rig.ex.placed[placed:] if p[1] == "MARKET" and p[2] == cid], [])

    def test_submitted_with_matching_order_is_adopted(self):
        k, gid, cid, token = self.pending()
        row = k.rig.store.orders(gid, "E")[0]
        k.rig.ex.place_order(client_id=cid, symbol="AAAUSDT", side=row["side"], type="MARKET", qty=row["qty"])    # emir geç ulaştı → doldu
        k.client.orders[cid] = {"status": "FILLED", "executedQty": str(row["qty"])}
        market = len([p for p in k.rig.ex.placed if p[1] == "MARKET"])
        k.restart()
        k.runtime.prepare_start()
        self.assertEqual(ar.get(token)["state"], ar.CONSUMED)                     # dolum kanıtlı: tüketildi (bırakılmadı)
        self.assertEqual(k.orch.guard.slots().open, 1)
        self.assertEqual(len([p for p in k.rig.ex.placed if p[1] == "MARKET"]), market)       # ikinci giriş yok
        self.assertEqual(len(live_kf(k.rig.ex)), 2)                               # STOP + TP kuruldu
        self.assertTrue(k.orch.recovered)

    def test_unreadable_exchange_keeps_reservation_and_fails_closed(self):
        k, gid, cid, token = self.pending()
        k.restart()
        k.client.fail = True                                                      # borsa okunamıyor (mutabakat + görüntü)
        k.rig.source.fail = True
        k.rig.ex.fail_reads = True
        placed = len(k.rig.ex.placed)
        k.runtime.prepare_start()
        self.assertNotEqual(ar.get(token)["state"], ar.RELEASED)                  # körlemesine bırakılmadı
        self.assertEqual(k.orch.base, o.INTERVENTION)
        self.assertFalse(k.orch.recovered)
        self.assertEqual(k.orch.start(), o.INTERVENTION)                          # RUNNING'e dönüş yok
        self.assertEqual(len(k.rig.ex.placed), placed)
        self.assertEqual(k.rig.store.slot_counts(ACC)["HELD"], 1)

    def test_ambiguous_ownership_position_without_order_is_intervention(self):
        k, gid, cid, token = self.pending()
        k.rig.ex.set_position("AAAUSDT", 1.5)                                     # emir yok ama pozisyon var: kanıtsız
        k.restart()
        k.runtime.prepare_start()
        self.assertEqual(k.orch.base, o.INTERVENTION)
        self.assertEqual(k.orch.guard.slots().used, 1)                            # slot bırakılmadı

    def test_orphan_kf_order_is_not_deleted_and_causes_intervention(self):
        k = Kit()
        ex, gid = k.rig.ex, "Forph0000001"
        ex.prices.setdefault("AAAUSDT", 100.0)
        ex.set_position("AAAUSDT", 1.0)
        stray = farmer_client_order_id(gid, "LONG", 1, "S", 0)
        ex.place_order(client_id=stray, symbol="AAAUSDT", side="SELL", type="STOP_MARKET", qty=1.0, price=0.0, stop_price=90.0, reduce_only=True)
        k.rig.store.claim_entry(ACC, "AAAUSDT", "15m", 1_000_000, gid, 50.0, cap_usdt=100.0, max_slots=2)       # ledger'sız kf- sahipliği: kanıtlanamaz
        k.restart()
        k.runtime.prepare_start()
        self.assertEqual(k.orch.base, o.INTERVENTION)
        self.assertEqual(ex.cancelled, [])                                        # otomatik SİLİNMEDİ
        self.assertIn(stray, live_kf(ex))

    def test_missing_farmer_reservation_is_reconciled_without_error(self):
        k = Kit()
        k.go()
        ar.get_store().db.execute("DELETE FROM reservations")                    # rezervasyon kaybı
        k.restart()
        k.runtime.prepare_start()
        self.assertTrue(k.orch.recovered)
        self.assertEqual(len(k.orch.engines), 1)                                  # pozisyon korunur
        self.assertTrue(k.runtime.symbol_busy("AAAUSDT"))                         # doluluk çalıştırıcı görünümünden sürer (başka bot giremez)
        self.assertEqual(k.rig.ex.cancelled, [])

    def test_foreign_reservations_are_never_touched(self):
        k = Kit()
        foreign = {kind: seed_submitted(k.rig.store, sym, f"x{idx}", kind=kind)
                   for idx, (kind, sym) in enumerate((("GRID", "GRIDUSDT"), ("DCA", "DCAUSDT"), ("MANUAL", "MANUSDT"), ("ORIGINAL_V2", "ORIGUSDT")))}
        k.restart()
        k.runtime.prepare_start()
        asked = {c[2]["origClientOrderId"] for c in k.client.calls if c[:2] == ("GET", "/fapi/v1/order")}
        for kind, (token, cid) in foreign.items():
            self.assertEqual(ar.get(token)["state"], ar.SUBMITTED, kind)          # durumları değişmedi
            self.assertNotIn(cid, asked, kind)                                    # borsaya bile sorulmadı

    def test_kinds_default_keeps_existing_behavior(self):
        """`kinds` verilmezse (mevcut tüm çağıranlar) davranış eskisiyle aynı: tüm türler çözülür."""
        token, cid = seed_submitted(None, "GRIDUSDT", "g1", kind="GRID")
        client, live = FakeClient(), LiveProxy(FakeClient())
        asyncio.run(real_live.reconcile_account_reservations(client, new_state(), OWNER, None))
        self.assertEqual(ar.get(token)["state"], ar.RELEASED)


# ====================================================================== API (gerçek çalışma zamanı + gerçek işleyiciler)
@unittest.skipUnless(HAS_FASTAPI, "fastapi yok (REAL ortamda çalışır)")
class TestApiAgainstRealRuntime(Base):
    def setUp(self):
        super().setUp()
        self._owner, self._blockers = api._owner, api.live_entry_blockers
        self.who = {"id": OWNER, "role": "OWNER"}
        api._owner = lambda request: self.who
        api.live_entry_blockers = lambda: []

    def tearDown(self):
        api._owner, api.live_entry_blockers = self._owner, self._blockers
        super().tearDown()

    def req(self, k):
        return SimpleNamespace(app=k.app)

    def configured(self, k):
        asyncio.run(api.farmer_settings(self.req(k), {"margin_usdt": 50, "leverage": 3, "max_positions": 2}))

    def test_status_start_stop_use_the_real_runner(self):
        k = Kit()
        req = self.req(k)
        status = asyncio.run(api.farmer_status(req))
        self.assertTrue(status["available"])
        self.assertEqual(status["state"], o.OFF)
        self.configured(k)
        started = asyncio.run(api.farmer_start(req))
        self.assertEqual(started["state"], o.RUNNING)
        self.assertEqual(k.orch.base, o.RUNNING)                                  # gerçek orkestratör değişti
        k.orch.tick(k.rig.now)
        self.assertEqual(asyncio.run(api.farmer_status(req))["state"], o.RUNNING)
        self.assertEqual(len(asyncio.run(api.farmer_status(req))["positions"]), 1)
        stopped = asyncio.run(api.farmer_stop_bot(req))
        self.assertEqual(stopped["state"], o.STOPPING)
        self.assertEqual(len(live_kf(k.rig.ex)), 2)                               # pozisyon ve korumalar kapanmadı

    def test_start_requires_saved_settings(self):
        k = Kit()
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(api.farmer_start(self.req(k)))
        self.assertEqual((cm.exception.status_code, cm.exception.detail["code"]), (409, "FARMER_NOT_CONFIGURED"))
        self.assertEqual(k.orch.base, o.OFF)

    def test_emergency_close_reports_real_result(self):
        k = Kit()
        self.configured(k)
        asyncio.run(api.farmer_start(self.req(k)))
        k.orch.tick(k.rig.now)
        result = asyncio.run(api.farmer_emergency_close(self.req(k)))
        self.assertEqual((result["closed"], result["failed"]), (1, 0))
        self.assertTrue(result["complete"])
        self.assertEqual(k.rig.ex.position("AAAUSDT"), 0.0)                       # gerçekten düz (doğrulanmış)

    def test_acknowledge_follows_safe_intervention_flow(self):
        k = Kit()
        self.configured(k)
        k.orch._intervene("test")
        with self.assertRaises(HTTPException):
            asyncio.run(api.farmer_start(self.req(k)))                            # INTERVENTION'dan doğrudan başlatılamaz
        self.assertEqual(k.orch.base, o.INTERVENTION)
        acked = asyncio.run(api.farmer_acknowledge(self.req(k)))
        self.assertEqual(acked["state"], o.OFF)                                   # temiz uzlaştırma → OFF (RUNNING değil)
        self.assertEqual(asyncio.run(api.farmer_start(self.req(k)))["state"], o.RUNNING)    # ayrı, açık start

    def test_settings_persist_through_the_real_store_and_restart(self):
        k = Kit()
        asyncio.run(api.farmer_settings(self.req(k), {"margin_usdt": 25, "leverage": 2, "target_usdt": 0.8, "max_loss_usdt": 1.2, "max_positions": 3}))
        saved = k.rig.store.bot(ACC)["settings"]
        self.assertEqual((saved["margin_usdt"], saved["leverage"], saved["max_positions"]), (25.0, 2.0, 3))
        again = fr.settings_from_store(k.rig.store, ACC)
        self.assertTrue(again[1])
        self.assertEqual(again[0].confirm.margin_usdt, 25.0)
        self.assertEqual(k.restart().orchestrator.settings.confirm.margin_usdt, 25.0)   # yeniden başlatmada ayar geri yüklenir
        self.assertTrue(k.runtime.configured)

    def test_settings_refused_while_active(self):
        k = Kit()
        self.configured(k)
        asyncio.run(api.farmer_start(self.req(k)))
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(api.farmer_settings(self.req(k), {"leverage": 2}))
        self.assertEqual(cm.exception.status_code, 409)

    def test_unauthorized_account_cannot_control_or_see_another_accounts_farmer(self):
        k = Kit()
        self.configured(k)                                                        # u1 bağlandı
        self.who = {"id": "u2", "role": "OWNER"}
        req = self.req(k)
        self.assertFalse(asyncio.run(api.farmer_status(req))["available"])         # durum sızmaz
        for call, args in ((api.farmer_start, ()), (api.farmer_stop_bot, ()), (api.farmer_emergency_close, ()), (api.farmer_acknowledge, ()),
                           (api.farmer_settings, ({"leverage": 2},))):
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(call(req, *args))
            self.assertEqual((cm.exception.status_code, cm.exception.detail["code"]), (403, "FARMER_NOT_YOUR_ACCOUNT"), call.__name__)
        self.assertEqual(k.orch.base, o.OFF)

    def test_gates_closed_keeps_live_off(self):
        k = Kit()
        self.configured(k)
        api.live_entry_blockers = lambda: ["LIVE_FLAG_OFF"]
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(api.farmer_start(self.req(k)))
        self.assertEqual(cm.exception.status_code, 423)
        self.assertEqual(k.orch.base, o.OFF)
        self.assertEqual(k.rig.ex.placed, [])


# ====================================================================== ücretsiz deneme / yetki (GERÇEK execution_owner)
@unittest.skipUnless(HAS_FASTAPI, "fastapi yok (REAL ortamda çalışır)")
class TestBackendGating(Base):
    def owner_for(self, path, *, mode="FREE_TRIAL", master=False, entitled=False, method="POST"):
        from app import live_access
        patched = {(real_live, "authenticated_user"): lambda request: {"id": "u9", "role": "CUSTOMER"},
                   (real_live, "subscription_for_request"): lambda request, user: {"mode": mode, "master_trade_access": master, "plan": "X"},
                   (real_live, "guard_execution_ownership"): lambda *a, **kw: None,
                   (real_live, "live_registry_enabled"): lambda: False,
                   (live_access, "entitled"): lambda subscription: entitled}
        saved = {key: getattr(key[0], key[1]) for key in patched}
        for (mod, name), fn in patched.items():
            setattr(mod, name, fn)
        try:
            request = SimpleNamespace(state=SimpleNamespace(), method=method, url=SimpleNamespace(path=path))
            return real_live.execution_owner(request)
        finally:
            for (mod, name), fn in saved.items():
                setattr(mod, name, fn)

    def test_free_trial_cannot_start_or_change_settings(self):
        for path in ("/api/v25/farmer/start", "/api/v25/farmer/settings"):
            with self.assertRaises(HTTPException) as cm:
                self.owner_for(path)
            self.assertEqual(cm.exception.status_code, 403, path)

    def test_free_trial_can_still_stop_and_emergency_close(self):
        for path in ("/api/v25/farmer/stop-bot", "/api/v25/farmer/emergency-close"):
            self.assertEqual(self.owner_for(path)["id"], "u9")                    # güvenlik eylemleri her zaman açık

    def test_unentitled_live_account_cannot_start_even_with_master_access(self):
        with self.assertRaises(HTTPException) as cm:
            self.owner_for("/api/v25/farmer/start", mode="PAID", master=True, entitled=False)
        self.assertEqual(cm.exception.status_code, 403)

    def test_entitled_account_passes_the_account_gate(self):
        self.assertEqual(self.owner_for("/api/v25/farmer/start", mode="PAID", master=True, entitled=True)["id"], "u9")

    def test_status_read_is_allowed_without_master_access(self):
        self.assertEqual(self.owner_for("/api/v25/farmer/status", master=False, method="GET")["id"], "u9")


# ====================================================================== izolasyon
class TestDayConvention(Base):
    def test_farmer_today_uses_the_same_utc_day_as_the_account_wide_ledger(self):
        """C10'un 'bugünkü net' günü ile hesap-geneli günlük zarar defterinin günü AYNI (UTC); ikinci bir gün tanımı yok."""
        from datetime import datetime, timezone
        from app import daily_ledger, farmer_view
        for stamp in (1_762_560_000.0 + 3600, 1_762_560_000.0 + 86399, 1_762_646_400.0):
            ledger_start = daily_ledger.day_start_ms(datetime.fromtimestamp(stamp, tz=timezone.utc)) / 1000
            inside = {"closed_at": ledger_start + 1, "pnl_state": "VERIFIED", "verified_net_pnl": 1.0}
            before = {"closed_at": ledger_start - 1, "pnl_state": "VERIFIED", "verified_net_pnl": 5.0}
            out = farmer_view._today_totals([inside, before], stamp)
            self.assertEqual((out["basis"], out["verified_net_pnl"], out["verified_count"]), ("UTC_DAY", 1.0, 1))


class TestIsolation(Base):
    def test_farmer_uses_only_kf_ids_and_leaves_foreign_alone(self):
        k = Kit()
        ex = k.rig.ex
        for sym in ("GRIDUSDT", "DCAUSDT", "MANUSDT"):
            ex.prices[sym] = 100.0
            ex.set_position(sym, 3.0)
        ex.place_order(client_id="kg-gridid-L01E0", symbol="GRIDUSDT", side="BUY", type="LIMIT", qty=1.0, price=50.0)
        ex.place_order(client_id="kd-dcaid-L01E0", symbol="DCAUSDT", side="BUY", type="LIMIT", qty=1.0, price=50.0)
        foreign_before = {s: ex.position(s) for s in ("GRIDUSDT", "DCAUSDT", "MANUSDT")}
        grid_runner = SimpleNamespace(symbol_busy=lambda *a, **kw: False)
        k.app.state.live_grid_runner = grid_runner
        k.go()
        farmer_ids = [p[2] for p in ex.placed if p[0] == "AAAUSDT"]
        self.assertTrue(farmer_ids)
        self.assertTrue(all(c.startswith("kf-") for c in farmer_ids))
        self.assertEqual({s: ex.position(s) for s in foreign_before}, foreign_before)
        self.assertEqual(ex.cancelled, [])
        self.assertIs(k.app.state.live_grid_runner, grid_runner)
        self.assertEqual([r for r in rows_all() if r["kind"] != "FARMER"], [])

    def test_production_code_does_not_import_test_or_harness_code(self):
        root = Path(__file__).parents[1] / "app"
        for name in ("farmer_runtime.py", "farmer_api.py", "farmer_view.py"):
            text = (root / name).read_text()
            for banned in ("tests.", "from tests", "import tests", "fake_exchange", "FakeExchange", "farmer-harness", "unittest", "mock"):
                self.assertNotIn(banned, text, f"{name}: {banned}")

    def test_frontend_production_code_does_not_import_the_harness(self):
        src = Path(__file__).parents[2] / "frontend" / "src"
        for path in src.rglob("*.ts*"):
            if path.name.endswith((".test.mts", ".test.ts")):
                continue
            text = path.read_text()
            self.assertNotIn("farmer-harness", text, str(path))
            self.assertNotIn("lucide-shim", text, str(path))


if __name__ == "__main__":
    unittest.main()
