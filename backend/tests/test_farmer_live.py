"""KAIS FARMER C7 — canlı adaptörler (mevcut kapıları saran ince katman). Enjekte edilmiş sahte modüllerle; fastapi gerekmez.
`live_bot_runners` kimlik testi v25_execution gerektirir (fastapi yoksa atlanır → CI)."""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import account_reservations as ar  # noqa: E402
from app import daily_ledger  # noqa: E402
from app.farmer_engine import live as fl  # noqa: E402
from app.farmer_engine.capability import LIVE_ENV_FLAG, TP_MARKET_VERIFIED_ENV  # noqa: E402

try:
    from app import v25_execution as V25
    SKIP = ""
except Exception as exc:                                              # noqa: BLE001
    V25, SKIP = None, f"v25_execution içe aktarılamadı: {exc.__class__.__name__}"

ENV_ON = {LIVE_ENV_FLAG: "1", TP_MARKET_VERIFIED_ENV: "1"}


class FakeLive:
    def __init__(self, **kw):
        self.armed, self.blocked, self.maint = True, False, False
        self.runners = []
        self.__dict__.update(kw)

    def is_armed(self, state):
        return self.armed

    def live_execution_blocked(self, state):
        return self.blocked

    def guard_new_entry(self, maintenance):
        if self.maint:
            raise RuntimeError("bakım")

    def live_reservation_owner(self, state, request):
        return "owner1"

    def live_bot_runners(self, app):
        return self.runners

    def live_plan_is_active(self, plan):
        return plan.get("status") == "AKTİF"


def app(state=None):
    base = {"real_trading_locked": False, "recovery_ready": True, "policy": {"daily_loss_limit": 10.0}, "plans": {}}
    base.update(state or {})
    return SimpleNamespace(state=SimpleNamespace(v25_execution=base, maintenance=None))


class Runner:
    def __init__(self, busy=()):
        self.busy = set(busy)

    def symbol_busy(self, symbol, user_id="", *, owner=False):
        return symbol in self.busy


class TestGates(unittest.TestCase):
    def test_default_off_blocks_even_when_armed(self):
        g = fl.LiveGates(app(), FakeLive(), env={}).entry_gates()
        self.assertTrue(any(x.startswith("CAPABILITY:") for x in g.safety_blocked))

    def test_all_open(self):
        g = fl.LiveGates(app(), FakeLive(), env=ENV_ON).entry_gates()
        self.assertEqual((g.arm_blocked, g.safety_blocked), ((), ()))

    def test_arm_expired(self):
        g = fl.LiveGates(app(), FakeLive(armed=False), env=ENV_ON).entry_gates()
        self.assertEqual(g.arm_blocked, ("ARM_EXPIRED_OR_OFF",))

    def test_real_trading_locked(self):
        g = fl.LiveGates(app({"real_trading_locked": True}), FakeLive(), env=ENV_ON).entry_gates()
        self.assertEqual(g.arm_blocked, ("REAL_TRADING_LOCKED",))

    def test_execution_recovery_maintenance(self):
        g = fl.LiveGates(app({"recovery_ready": False}), FakeLive(blocked=True, maint=True), env=ENV_ON).entry_gates()
        self.assertEqual(set(g.safety_blocked), {"EXECUTION_BLOCKED", "RECOVERY_NOT_READY", "MAINTENANCE"})

    def test_foreign_occupancy_other_runner_and_plan_but_not_farmer_view(self):
        view = fl.FarmerRunnerView(lambda: None)
        live = FakeLive(runners=[Runner({"AAAUSDT"}), view])
        gates = fl.LiveGates(app({"plans": {"p": {"symbol": "BBBUSDT", "status": "AKTİF"}}}), live, env=ENV_ON)
        self.assertEqual(gates.foreign_occupied("AAAUSDT"), ("OTHER_BOT",))
        self.assertEqual(gates.foreign_occupied("BBBUSDT"), ("ACTIVE_PLAN",))
        self.assertEqual(gates.foreign_occupied("CCCUSDT"), ())

    def test_unreadable_runner_counts_as_occupied(self):
        class Bad:
            def symbol_busy(self, *a, **k):
                raise RuntimeError

        gates = fl.LiveGates(app(), FakeLive(runners=[Bad()]), env=ENV_ON)
        self.assertEqual(gates.foreign_occupied("AAAUSDT"), ("OCCUPANCY_UNREADABLE",))


class TestDailyLoss(unittest.TestCase):
    def state(self, net=None, limit=10.0, fetched=None):
        import time
        s = {"policy": {"daily_loss_limit": limit}}
        if net is not None:
            s = daily_ledger.store(s, {"day": daily_ledger._day_of(daily_ledger.day_start_ms()), "fetched_at": fetched or time.time(),
                                       "totals": {"net": net}}) and s
        return s

    def test_uses_existing_ledger_and_limit(self):
        self.assertEqual(fl.daily_loss_blockers(self.state(-3.0), daily_ledger), ())
        self.assertEqual(fl.daily_loss_blockers(self.state(-10.0), daily_ledger), ("DAILY_LOSS_LIMIT",))

    def test_ledger_missing_closes_entry(self):
        self.assertEqual(fl.daily_loss_blockers(self.state(None), daily_ledger), ("DAILY_LEDGER_UNAVAILABLE",))

    def test_no_limit_no_block(self):
        self.assertEqual(fl.daily_loss_blockers({"policy": {}}, daily_ledger), ())


class TestReservations(unittest.TestCase):
    def setUp(self):
        ar.use_store(ar.ReservationStore())
        self.r = fl.LiveReservations(ar, lambda: "owner1")

    def tearDown(self):
        ar.use_store(None)

    def test_lifecycle_consume(self):
        self.assertTrue(self.r.reserve("AAAUSDT", "Gabc"))
        self.r.mark_submitted("Gabc", "kf-Gabc-L01E0")
        self.assertEqual([x["state"] for x in ar.rows("owner1")], ["SUBMITTED"])
        self.r.consume("Gabc")
        self.assertEqual(ar.rows("owner1"), [])

    def test_symbol_conflict_denied(self):
        self.assertTrue(self.r.reserve("AAAUSDT", "G1"))
        self.assertFalse(self.r.reserve("AAAUSDT", "G2"))

    def test_release(self):
        self.r.reserve("AAAUSDT", "G1")
        self.r.release("G1")
        self.assertEqual(ar.rows("owner1"), [])

    def test_token_found_after_restart_by_client_id(self):
        self.r.reserve("AAAUSDT", "Gabc")
        self.r.mark_submitted("Gabc", "kf-Gabc-L01E0")
        fresh = fl.LiveReservations(ar, lambda: "owner1")             # süreç yeniden başladı: belirteç haritası yok
        fresh.consume("Gabc")
        self.assertEqual(ar.rows("owner1"), [])

    def test_orphans_only_other_epoch_farmer_rows(self):
        self.r.reserve("AAAUSDT", "Gabc")
        self.r.mark_submitted("Gabc", "kf-Gabc-L01E0")
        self.assertEqual(self.r.orphans(), [])                       # bu süreç: yetim değil
        store = ar.get_store()
        store.db.execute("UPDATE reservations SET epoch='OLD'")
        self.assertEqual(self.r.orphans(), ["Gabc"])
        ar.reserve  # noqa: B018
        other = ar.get_store().db
        other.execute("UPDATE reservations SET kind='GRID'")
        self.assertEqual(self.r.orphans(), [])                       # başka tür satırlara dokunmaz

    def test_stale_submitted_blocks_new_reserve_fail_closed(self):
        self.r.reserve("AAAUSDT", "Gabc")
        self.r.mark_submitted("Gabc", "kf-Gabc-L01E0")
        ar.get_store().db.execute("UPDATE reservations SET epoch='OLD'")
        self.assertFalse(self.r.reserve("BBBUSDT", "G2"))            # mevcut mutabakat kuralı: fail-closed


class TestSnapshotMapping(unittest.TestCase):
    RAW = {"open_algo_orders_available": True,
           "positions": [{"symbol": "AAAUSDT", "direction": "SHORT", "quantity": 1.5, "mark_price": 99.5}],
           "open_orders": [{"symbol": "BBBUSDT", "client_order_id": "x1", "side": "BUY", "type": "LIMIT", "price": 5.0, "quantity": 2.0,
                            "executed_quantity": 0.5, "status": "PARTIALLY_FILLED", "reduce_only": False}],
           "open_algo_orders": [{"symbol": "AAAUSDT", "client_algo_id": "kf-G-S01S0", "side": "BUY", "type": "STOP_MARKET",
                                 "trigger_price": 101.0, "quantity": 1.5, "reduce_only": True}]}

    def test_mapping(self):
        s = fl.snapshot_from_account(self.RAW, 5.0)
        self.assertTrue(s.ok)
        self.assertEqual(s.positions, {"AAAUSDT": -1.5})
        self.assertEqual(s.mark_prices, {"AAAUSDT": 99.5})
        algo = next(o for o in s.open_orders if o.client_id == "kf-G-S01S0")
        self.assertEqual((algo.stop_price, algo.qty, algo.reduce_only, algo.type), (101.0, 1.5, True, "STOP_MARKET"))
        self.assertEqual(next(o for o in s.open_orders if o.client_id == "x1").filled, 0.5)

    def test_algo_unverified_not_ok(self):
        self.assertFalse(fl.snapshot_from_account({**self.RAW, "open_algo_orders_available": False}, 1.0).ok)

    def test_source_calls_existing_account_snapshot_once_uncached(self):
        calls = []

        class L:
            async def account_snapshot(self, client, rid, ev, *, cached, source):
                calls.append((client, cached, source))
                return TestSnapshotMapping.RAW

        loop = asyncio.new_event_loop()
        import threading
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        try:
            src = fl.LiveSnapshotSource(fl.LoopBridge(loop), lambda: "CLIENT", L(), clock=lambda: 7.0)
            self.assertTrue(src.read().ok)
        finally:
            loop.call_soon_threadsafe(loop.stop)
        self.assertEqual(calls, [("CLIENT", False, "farmer")])


class TestUniverse(unittest.TestCase):
    def make(self, shared=None, raises=False):
        built = []

        class O2:
            @staticmethod
            def settings(state):
                if raises:
                    raise RuntimeError
                return {}

            @staticmethod
            def selected_symbols(config):
                return shared or []

        class MU:
            @staticmethod
            def ranked_original_live_symbols(info, tickers, book, *, now_ms):
                built.append(now_ms)
                return ["XXXUSDT", "YYYUSDT", "ZZZUSDT"], 3

        class Client:
            async def public_get(self, path):
                return {}

        loop = asyncio.new_event_loop()
        import threading
        threading.Thread(target=loop.run_forever, daemon=True).start()
        self.addCleanup(lambda: loop.call_soon_threadsafe(loop.stop))
        uni = fl.LiveUniverse(app(), fl.LoopBridge(loop), Client, O2, MU, count=2, clock=lambda: 1_800_000_000.0)
        return uni, built

    def test_shares_original_v2_universe_when_present(self):
        uni, built = self.make(shared=["AAAUSDT", "BBBUSDT", "CCCUSDT"])
        self.assertEqual(uni.symbols(), ["AAAUSDT", "BBBUSDT"])
        self.assertEqual(built, [])

    def test_own_universe_when_original_off_built_once_per_day(self):
        uni, built = self.make()
        self.assertEqual(uni.symbols(), ["XXXUSDT", "YYYUSDT"])
        uni.symbols()
        self.assertEqual(len(built), 1)

    def test_original_settings_error_falls_back(self):
        uni, built = self.make(raises=True)
        self.assertEqual(uni.symbols(), ["XXXUSDT", "YYYUSDT"])


class TestRunnerView(unittest.TestCase):
    def test_busy_and_risk(self):
        eng = SimpleNamespace(sym="AAAUSDT", gid="G1", spec=SimpleNamespace(meta={"max_loss_usdt": 1.5}))
        view = fl.FarmerRunnerView(lambda: SimpleNamespace(engines={"G1": eng}))
        self.assertTrue(view.symbol_busy("AAAUSDT", "u", owner=True))
        self.assertFalse(view.symbol_busy("BBBUSDT"))
        self.assertEqual(view.reserved_risk("u", owner=True)["worst_loss_usdt"], 1.5)
        self.assertEqual(view.reserved_risk("u", exclude="G1")["count"], 0)

    def test_no_orchestrator_is_empty(self):
        view = fl.FarmerRunnerView(lambda: None)
        self.assertFalse(view.symbol_busy("AAAUSDT"))
        self.assertEqual(view.reserved_risk("u")["count"], 0)


@unittest.skipIf(bool(SKIP), SKIP)
class TestFarmerOffIdentity(unittest.TestCase):
    def test_runner_list_unchanged_when_farmer_not_installed(self):
        grid, dca = object(), object()
        a = SimpleNamespace(state=SimpleNamespace(live_grid_runner=grid, live_dca_runner=dca))
        self.assertEqual(V25.live_bot_runners(a), [grid, dca])
        self.assertEqual(V25.live_bot_runners(SimpleNamespace(state=SimpleNamespace())), [])

    def test_farmer_view_appended_only_when_installed(self):
        view = object()
        a = SimpleNamespace(state=SimpleNamespace(live_grid_runner=None, live_dca_runner=None, live_farmer_runner=view))
        self.assertEqual(V25.live_bot_runners(a), [view])


if __name__ == "__main__":
    unittest.main()
