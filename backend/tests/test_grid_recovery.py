"""Canlı grid: sunucu yeniden başladıktan sonra otomatik kurtarma — AĞ YOK, GERÇEK EMİR YOK.

Senaryo: grid çalışırken sunucu kapanır (döngü yok), bu sırada borsada dolumlar/stop olur, sonra açılışta kurtarma koşar.
Kural: borsa ile defter eşitlenmeden hiçbir emir gönderilmez; koruma doğrulanmadan döngü başlamaz.
"""
from __future__ import annotations

import asyncio
import unittest
from typing import Any

from app import grid_live
from app.grid_engine.engine import BLOCKED, EMERGENCY, EMERGENCY_CLOSED, PAUSED, RUNNING, STOPPED
from app.grid_engine.live_adapter import BinanceLiveGridAdapter
from app.grid_engine.ports import FILLED, LONG, NEW, STOP_MARKET
from app.grid_engine.store import GridStore

from test_grid_live import POLICY, SYM, ExchangeFake, FakeBinance, prepare, prepare_neutral

KEYS = ("k" * 16, "s" * 16)


def new_runner(store):
    runner = grid_live.LiveGridRunner(store)
    runner.events = []
    runner._record = lambda state, kind, message, **extra: runner.events.append((kind, message))
    runner.blocked_check = lambda s: False
    runner.loops = []
    runner._ensure_loop = lambda app, state, gid=None: runner.loops.append(gid)
    return runner


class RecoveryBase(unittest.TestCase):
    def setUp(self) -> None:
        self.ex = ExchangeFake()
        self.ex.set_price(SYM, 100.0)
        self.api = FakeBinance(self.ex)
        self.store = GridStore(":memory:", check_same_thread=False)
        self.state: dict[str, Any] = {"real_trading_locked": False, "emergency": {}, "events": [], "policy": POLICY,
                                      "recovery_ready": True}

    def start_grid(self, plan=None, *, user_id="u1"):
        runner = new_runner(self.store)

        async def go():
            adapter = BinanceLiveGridAdapter(self.api, asyncio.get_running_loop(), timeout=5)
            out = await runner.start(None, self.state, adapter, plan or prepare(recycle=True), SYM, user_id=user_id, owner=False)
            return out["started_grid_id"]
        return asyncio.run(go())

    def restart_and_recover(self, *, authority=True, user_id="u1"):
        """Yeni süreç: yeni runner, aynı defter. Döngü yok; yalnız kurtarma."""
        runner = new_runner(self.store)
        self.api.calls.clear()

        async def go():
            adapter = BinanceLiveGridAdapter(self.api, asyncio.get_running_loop(), timeout=5)
            return await runner.recover_after_restart(None, self.state, adapter, user_id=user_id, owner=False, entry_authority=authority)
        return runner, asyncio.run(go())

    def posts(self):
        return [(path, params) for method, path, params in self.api.calls if method == "POST"]

    def first_post_index(self):
        return next((i for i, (method, _, _) in enumerate(self.api.calls) if method == "POST"), None)

    def live_stops(self):
        return [o for o in self.ex.orders.values() if o.type == STOP_MARKET and o.status in (NEW, "PARTIALLY_FILLED")]


class TestStartupRecovery(RecoveryBase):
    def test_fill_while_down_gets_stop_and_target_before_loop_starts(self):
        gid = self.start_grid()
        entries_before = len([o for o in self.ex.orders.values() if o.type == "LIMIT" and not o.reduce_only])
        self.ex.set_price(SYM, 98.9)                      # kapalıyken 1. kademe doldu; stop/hedef kurulmadı
        self.assertEqual(self.live_stops(), [])
        runner, results = self.restart_and_recover()
        self.assertEqual(results[0]["verified"], True)
        self.assertEqual(runner.loops, [gid])              # döngü ancak doğrulamadan sonra
        filled = self.ex.positions[SYM]
        self.assertGreater(filled, 0)
        self.assertEqual([round(o.qty, 8) for o in self.live_stops()], [round(filled, 8)])
        targets = [o for o in self.ex.orders.values() if o.type == "LIMIT" and o.reduce_only and o.status == NEW]
        self.assertEqual(len(targets), 1)
        # Eşitlemeden önce tek emir gitmedi; kurtarmada yeni GİRİŞ emri gönderilmedi (yalnız koruma).
        first_post = self.first_post_index()
        self.assertTrue(all(m == "GET" for m, _, _ in self.api.calls[:first_post]))
        self.assertGreater(first_post, 0)
        entries_after = len([o for o in self.ex.orders.values() if o.type == "LIMIT" and not o.reduce_only])
        self.assertEqual(entries_before, entries_after)
        self.assertIn("LIVE_GRID_RECOVERED", [k for k, _ in runner.events])

    def test_cycle_completed_while_down_is_not_reopened_during_recovery(self):
        gid = self.start_grid()                               # tur açık grid
        runner = new_runner(self.store)

        async def tick_once():
            adapter = BinanceLiveGridAdapter(self.api, asyncio.get_running_loop(), timeout=5)
            self.ex.set_price(SYM, 98.9)
            engine = runner.build_engine(self.store.grid(gid), adapter, self.state)
            await asyncio.to_thread(engine.tick)             # sunucu açıkken: dolum + stop + hedef kuruldu
        asyncio.run(tick_once())
        self.ex.set_price(SYM, 100.2)                          # kapalıyken hedef doldu: tur tamamlandı
        entries = lambda: [o for o in self.ex.orders.values() if o.type == "LIMIT" and not o.reduce_only]
        before = len(entries())
        runner, results = self.restart_and_recover()
        self.assertTrue(results[0]["verified"])
        self.assertEqual(len(entries()), before)              # kurtarma yeni tur girişi AÇMAZ; bunu döngü yapar
        self.assertEqual(runner.loops, [gid])

    def test_exchange_unreachable_means_no_order_and_no_loop_then_retry_succeeds(self):
        gid = self.start_grid()
        self.ex.set_price(SYM, 98.9)
        self.api.fail_reads = True
        runner, results = self.restart_and_recover()
        self.assertFalse(results[0]["verified"])
        self.assertEqual(self.posts(), [])
        self.assertEqual(runner.loops, [])
        self.assertIn("LIVE_GRID_RECOVERY_PENDING", [k for k, _ in runner.events])
        self.api.fail_reads = False
        runner, results = self.restart_and_recover()
        self.assertTrue(results[0]["verified"])
        self.assertEqual(runner.loops, [gid])

    def test_ledger_exchange_mismatch_blocks_without_orders(self):
        self.start_grid()
        self.ex.set_price(SYM, 98.9)
        self.ex.positions[SYM] = round(self.ex.positions[SYM] + 0.5, 8)   # biri elle pozisyon ekledi
        runner, results = self.restart_and_recover()
        self.assertFalse(results[0]["verified"])
        self.assertEqual(results[0]["status"], BLOCKED)
        self.assertEqual(self.posts(), [])
        self.assertEqual(runner.loops, [])

    def test_stop_hit_while_down_finishes_grid(self):
        gid = self.start_grid()
        runner = new_runner(self.store)

        async def tick_once():
            adapter = BinanceLiveGridAdapter(self.api, asyncio.get_running_loop(), timeout=5)
            self.ex.set_price(SYM, 98.9)
            engine = runner.build_engine(self.store.grid(gid), adapter, self.state)
            await asyncio.to_thread(engine.tick)             # dolum + stop kuruldu (sunucu açıkken)
        asyncio.run(tick_once())
        self.assertEqual(len(self.live_stops()), 1)
        self.ex.set_price(SYM, 96.0)                        # kapalıyken stop tetiklendi
        runner, results = self.restart_and_recover()
        self.assertEqual(results[0]["status"], STOPPED)
        self.assertEqual(runner.loops, [])
        self.assertEqual(self.ex.positions[SYM], 0.0)
        self.assertEqual([o for o in self.ex.orders.values() if o.status in (NEW, "PARTIALLY_FILLED")], [])

    def test_without_entry_authority_grid_is_paused_but_protected(self):
        gid = self.start_grid()
        self.ex.set_price(SYM, 98.9)
        runner, results = self.restart_and_recover(authority=False)
        self.assertTrue(results[0]["verified"])
        self.assertEqual(results[0]["status"], PAUSED)
        self.assertEqual(runner.loops, [gid])              # döngü korumayı sürdürür, yeni giriş yok
        self.assertEqual(len(self.live_stops()), 1)
        open_entries = [o for o in self.ex.orders.values() if o.type == "LIMIT" and not o.reduce_only and o.status == NEW]
        self.assertEqual(open_entries, [])                  # bekleyen giriş iptal edildi

    def test_interrupted_emergency_is_completed(self):
        gid = self.start_grid()
        self.ex.set_price(SYM, 98.9)
        self.store.set_grid(gid, status=EMERGENCY)          # acil durdurma sürerken sunucu kapandı
        runner, results = self.restart_and_recover()
        self.assertEqual(results[0]["status"], EMERGENCY_CLOSED)
        self.assertEqual(self.ex.positions[SYM], 0.0)
        self.assertEqual(runner.loops, [])

    def test_needs_user_grids_are_left_alone(self):
        gid = self.start_grid()
        self.store.set_grid(gid, status=BLOCKED)
        runner, results = self.restart_and_recover()
        self.assertEqual(results, [])
        self.assertEqual(self.posts(), [])

    def test_other_members_grids_are_not_touched(self):
        self.start_grid(user_id="u2")
        self.ex.set_price(SYM, 98.9)
        runner, results = self.restart_and_recover(user_id="u1")
        self.assertEqual(results, [])
        self.assertEqual(self.api.calls, [])

    def test_neutral_grid_recovery_protects_the_open_side(self):
        gid = self.start_grid(prepare_neutral())
        self.ex.set_price(SYM, 98.4)                         # alt kademeler doldu: net LONG
        runner, results = self.restart_and_recover()
        self.assertTrue(results[0]["verified"], results[0]["detail"])
        stops = self.live_stops()
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0].side, "SELL")
        self.assertAlmostEqual(stops[0].qty, self.ex.positions[SYM])


class TestStartupTask(RecoveryBase):
    def run_startup(self, *, credentials, ready=True, attempts=3):
        sleeps = []
        self.state["recovery_ready"] = ready
        app = type("App", (), {})()
        app.state = type("S", (), {})()
        runner = new_runner(self.store)
        app.state.live_grid_runner = runner

        async def go():
            loop = asyncio.get_running_loop()

            async def fake_sleep(seconds):
                sleeps.append(seconds)
                if len(sleeps) == 2:
                    self.state["recovery_ready"] = True     # canlı durum kurtarması biraz sonra hazır oldu

            return await grid_live.startup_recovery(
                app, self.state, user_id="u1", owner=False, credentials=credentials,
                adapter_factory=lambda a, keys: BinanceLiveGridAdapter(self.api, loop, timeout=5),
                sleep=fake_sleep, retry_seconds=1.0, max_attempts=attempts, ready_wait_seconds=60)
        return runner, sleeps, asyncio.run(go())

    def test_waits_for_live_state_then_recovers(self):
        gid = self.start_grid()
        self.ex.set_price(SYM, 98.9)

        async def creds():
            return KEYS
        runner, sleeps, results = self.run_startup(credentials=creds, ready=False)
        self.assertEqual(sleeps[:2], [5.0, 5.0])
        self.assertEqual(runner.loops, [gid])
        self.assertTrue(results[0]["verified"])

    def test_missing_keys_retries_without_any_order(self):
        self.start_grid()
        self.ex.set_price(SYM, 98.9)
        self.api.calls.clear()
        calls = []

        async def creds():
            calls.append(1)
            return ("", "") if len(calls) < 3 else KEYS
        runner, sleeps, results = self.run_startup(credentials=creds, attempts=5)
        self.assertEqual(len(calls), 3)
        self.assertTrue(results[0]["verified"])
        self.assertIn("LIVE_GRID_RECOVERY_PENDING", [k for k, _ in runner.events])

    def test_nothing_to_recover_does_nothing(self):
        async def creds():
            raise AssertionError("anahtar istenmemeli")
        runner, sleeps, results = self.run_startup(credentials=creds)
        self.assertEqual((results, sleeps), ([], []))


if __name__ == "__main__":
    unittest.main()


class TestStartupWiring(unittest.TestCase):
    def make_app(self, store, *, state, registry=None):
        from types import SimpleNamespace
        app = SimpleNamespace(state=SimpleNamespace(v25_execution=state, live_grid_runner=grid_live.LiveGridRunner(store)))
        if registry is not None:
            app.state.live_registry = registry
        return app

    def test_member_with_active_grid_gets_its_engine_started_at_boot(self):
        from app import v25_execution
        store = GridStore(":memory:", check_same_thread=False)
        store.create_grid("g9", SYM, LONG, {"stop_price": 1, "levels": []}, user_id="u9")
        app = self.make_app(store, state={})
        self.assertTrue(v25_execution._member_has_active_live_grid(app, "u9"))
        self.assertFalse(v25_execution._member_has_active_live_grid(app, "u8"))
        store.set_grid("g9", status=STOPPED)
        self.assertFalse(v25_execution._member_has_active_live_grid(app, "u9"))

    def test_recovery_task_scope_single_user_and_member(self):
        from unittest.mock import patch
        from app import v25_execution
        captured = []

        async def fake_recovery(app, state, **kwargs):
            captured.append((kwargs["user_id"], kwargs["owner"], kwargs["entry_authority"]()))
            return []

        async def run(app):
            await v25_execution.grid_recovery_task(app)

        with patch.object(grid_live, "startup_recovery", fake_recovery), \
                patch.object(v25_execution, "live_entry_authority_open", lambda state: False):
            asyncio.run(run(self.make_app(GridStore(":memory:"), state={})))
            member_state = {"_live_user_id": "u5"}
            from app.live_registry import LiveRegistry
            registry = LiveRegistry.__new__(LiveRegistry)
            asyncio.run(run(self.make_app(GridStore(":memory:"), state=member_state, registry=registry)))
        self.assertEqual(captured, [(None, True, False), ("u5", False, False)])
