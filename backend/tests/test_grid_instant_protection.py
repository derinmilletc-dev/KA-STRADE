"""Canlı grid: dolumda koruma 5 sn'lik turu beklemez (Binance kullanıcı akışı tetikli) — AĞ YOK, GERÇEK EMİR YOK."""
from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from typing import Any

from app import grid_live
from app.grid_engine.engine import PAUSED, RUNNING, STOPPED, client_order_id
from app.grid_engine.live_adapter import BinanceLiveGridAdapter
from app.grid_engine.ports import LONG, NEW, STOP_MARKET
from app.grid_engine.store import GridStore

from test_grid_live import POLICY, SYM, ExchangeFake, FakeBinance, prepare


def trade_event(client_id: str, x: str = "TRADE", status: str = "PARTIALLY_FILLED") -> dict[str, Any]:
    return {"e": "ORDER_TRADE_UPDATE", "E": 1, "o": {"s": SYM, "c": client_id, "x": x, "X": status}}


class TestStreamEventParsing(unittest.TestCase):
    def test_only_grid_fills_and_stop_triggers_count(self):
        cid = client_order_id("g0123456789ab", LONG, 1, "E")
        self.assertEqual(grid_live.grid_stream_client_id(trade_event(cid)), cid)
        self.assertIsNone(grid_live.grid_stream_client_id(trade_event(cid, x="NEW", status="NEW")))
        self.assertIsNone(grid_live.grid_stream_client_id(trade_event("PTB_ENTRY_abc")))      # V25 emri: grid değil
        self.assertIsNone(grid_live.grid_stream_client_id(trade_event("kg-bad id")))
        stop = client_order_id("g0123456789ab", LONG, 0, "S", 1)
        algo = {"e": "ALGO_UPDATE", "o": {"s": SYM, "caid": stop, "X": "TRIGGERED"}}
        self.assertEqual(grid_live.grid_stream_client_id(algo), stop)
        self.assertIsNone(grid_live.grid_stream_client_id({"e": "ALGO_UPDATE", "o": {"caid": stop, "X": "NEW"}}))
        self.assertIsNone(grid_live.grid_stream_client_id("bozuk"))


class InstantBase(unittest.TestCase):
    def setUp(self) -> None:
        self.ex = ExchangeFake()
        self.ex.set_price(SYM, 100.0)
        self.api = FakeBinance(self.ex)
        self.runner = grid_live.LiveGridRunner(GridStore(":memory:", check_same_thread=False))
        self.runner._record = lambda *a, **k: None
        self.runner.blocked_check = lambda s: False
        self.state: dict[str, Any] = {"real_trading_locked": False, "emergency": {}, "events": [], "policy": POLICY}

    def scenario(self, body):
        async def go():
            adapter = BinanceLiveGridAdapter(self.api, asyncio.get_running_loop(), timeout=5)
            idle = asyncio.Event()
            # Normal döngü yerine boşta bekleyen görev: "döngü çalışıyor" sayılır ama 5 sn turları koşmaz.
            self.runner._ensure_loop = lambda app, state, gid=None: self.runner._tasks.__setitem__(
                gid, asyncio.ensure_future(idle.wait()))
            out = await self.runner.start(None, self.state, adapter, prepare(recycle=True), SYM, user_id="u1", owner=False)
            try:
                return await body(out["started_grid_id"])
            finally:
                idle.set()
        return asyncio.run(go())

    def live_stops(self):
        return [o for o in self.ex.orders.values() if o.type == STOP_MARKET and o.status in (NEW, "PARTIALLY_FILLED")]

    def entry_cid(self, gid, level=1):
        return client_order_id(gid, LONG, level, "E")


class TestInstantProtection(InstantBase):
    def test_fill_gets_stop_and_target_without_waiting_for_the_loop(self):
        async def body(gid):
            self.ex.set_price(SYM, 98.9)                             # 1. kademe doldu; döngü bu sırada çalışmıyor
            self.assertEqual(self.live_stops(), [])
            entries_before = len([o for o in self.ex.orders.values() if o.type == "LIMIT" and not o.reduce_only])
            started = time.monotonic()
            ok = await self.runner.protect_from_stream(self.entry_cid(gid))
            elapsed_ms = (time.monotonic() - started) * 1000
            self.assertTrue(ok)
            self.assertEqual([round(o.qty, 8) for o in self.live_stops()], [round(self.ex.positions[SYM], 8)])
            targets = [o for o in self.ex.orders.values() if o.type == "LIMIT" and o.reduce_only and o.status == NEW]
            self.assertEqual(len(targets), 1)
            entries_after = len([o for o in self.ex.orders.values() if o.type == "LIMIT" and not o.reduce_only])
            self.assertEqual(entries_before, entries_after)      # anlık yol yeni giriş açmaz
            self.assertLess(elapsed_ms, 1000)
            self.assertTrue(self.runner._last_instant_protect[gid]["ok"])
        self.scenario(body)

    def test_burst_of_fills_is_merged_and_never_doubles_the_stop(self):
        async def body(gid):
            self.ex.set_price(SYM, 97.9)                             # iki kademe birden doldu
            calls_before = len(self.api.calls)
            results = await asyncio.gather(*(self.runner.protect_from_stream(self.entry_cid(gid, 1 + i % 2)) for i in range(8)))
            self.assertEqual(sum(results), len([r for r in results if r]))
            self.assertLessEqual(sum(1 for r in results if r), 2)    # en çok 1 çalışan + 1 kuyrukta
            stops = self.live_stops()
            self.assertEqual(len(stops), 1)
            self.assertAlmostEqual(stops[0].qty, self.ex.positions[SYM])
            self.assertLess(len(self.api.calls) - calls_before, 60)
        self.scenario(body)

    def test_unknown_or_stopped_grid_does_nothing(self):
        async def body(gid):
            self.api.calls.clear()
            self.assertFalse(await self.runner.protect_from_stream(client_order_id("gotherxxxxxx", LONG, 1, "E")))
            self.runner._tasks[gid].cancel()                         # döngü durmuş grid: bildirim yok sayılır
            await asyncio.sleep(0)
            self.assertFalse(await self.runner.protect_from_stream(self.entry_cid(gid)))
            self.assertEqual(self.api.calls, [])
        self.scenario(body)

    def test_exchange_read_failure_sends_nothing(self):
        async def body(gid):
            self.ex.set_price(SYM, 98.9)
            self.api.fail_reads = True
            self.api.calls.clear()
            self.assertFalse(await self.runner.protect_from_stream(self.entry_cid(gid)))
            self.assertEqual([c for c in self.api.calls if c[0] == "POST"], [])
            self.api.fail_reads = False
            self.assertTrue(await self.runner.protect_from_stream(self.entry_cid(gid)))
            self.assertEqual(len(self.live_stops()), 1)
        self.scenario(body)

    def test_paused_grid_is_still_protected_instantly(self):
        async def body(gid):
            engine = self.runner._engines[gid]
            self.ex.set_price(SYM, 98.9)                             # dolum, duraklatma yarışında geldi
            self.runner.store.set_grid(gid, status=PAUSED)
            self.assertTrue(await self.runner.protect_from_stream(self.entry_cid(gid)))
            self.assertEqual(len(self.live_stops()), 1)
            self.assertEqual(engine.status, PAUSED)
        self.scenario(body)

    def test_stop_trigger_ends_grid_and_cancels_entries_at_once(self):
        async def body(gid):
            self.ex.set_price(SYM, 98.9)
            await self.runner.protect_from_stream(self.entry_cid(gid))
            stop = self.live_stops()[0]
            self.ex.set_price(SYM, 96.0)                             # stop tetiklendi
            self.assertTrue(await self.runner.protect_from_stream(stop.client_id))
            self.assertEqual(self.runner.store.grid(gid)["status"], STOPPED)
            self.assertEqual([o for o in self.ex.orders.values() if o.status in (NEW, "PARTIALLY_FILLED")], [])
        self.scenario(body)

    def test_waits_for_a_running_tick_instead_of_racing_it(self):
        async def body(gid):
            self.ex.set_price(SYM, 98.9)
            lock = self.runner.grid_lock(gid)
            await lock.acquire()                                     # normal tur sürüyor
            task = asyncio.ensure_future(self.runner.protect_from_stream(self.entry_cid(gid)))
            await asyncio.sleep(0.05)
            self.assertFalse(task.done())
            self.assertEqual(self.live_stops(), [])
            lock.release()
            self.assertTrue(await task)
            self.assertEqual(len(self.live_stops()), 1)
        self.scenario(body)


class TestStreamWiring(unittest.TestCase):
    def test_schedules_only_for_grid_events_and_existing_runner(self):
        from app import v25_execution

        calls = []

        class Runner:
            async def protect_from_stream(self, client_id):
                calls.append(client_id)
                return True

        cid = client_order_id("g0123456789ab", LONG, 1, "E")

        async def go():
            with_runner = SimpleNamespace(state=SimpleNamespace(live_grid_runner=Runner()))
            without = SimpleNamespace(state=SimpleNamespace())
            self.assertIsNone(v25_execution.schedule_grid_instant_protection(without, trade_event(cid)))
            self.assertIsNone(v25_execution.schedule_grid_instant_protection(with_runner, trade_event("PTB_ENTRY_x")))
            task = v25_execution.schedule_grid_instant_protection(with_runner, trade_event(cid))
            await task
        asyncio.run(go())
        self.assertEqual(calls, [cid])


if __name__ == "__main__":
    unittest.main()
