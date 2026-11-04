"""Canlı grid: 24 saatlik ARM süresi dolunca grid duraklar (yeni giriş ve tur durur, bekleyen girişler iptal, korumalar sürer);
yeniden ARM verilip "Devam ettir" denince iptal edilen girişler güncel fiyata göre yeniden açılır — AĞ YOK, GERÇEK EMİR YOK."""
from __future__ import annotations

import asyncio
import time
import unittest
from typing import Any
from unittest.mock import patch

from app import grid_live
from app.grid_engine.engine import PAUSED, RUNNING, STOPPED
from app.grid_engine.live_adapter import BinanceLiveGridAdapter
from app.grid_engine.ports import NEW, STOP_MARKET
from app.grid_engine.store import GridStore

from test_grid_live import POLICY, SYM, ExchangeFake, FakeBinance, prepare


class ArmBase(unittest.TestCase):
    def setUp(self) -> None:
        self.ex = ExchangeFake()
        self.ex.set_price(SYM, 100.0)
        self.api = FakeBinance(self.ex)
        self.api.mark = 100.0

        async def price(symbol):
            return self.api.mark
        self.price = price
        self.runner = grid_live.LiveGridRunner(GridStore(":memory:", check_same_thread=False))
        self.events: list[tuple[str, str]] = []
        self.runner._record = lambda state, kind, message, **extra: self.events.append((kind, message))
        self.runner.blocked_check = lambda s: False
        self.armed = True
        self.runner.entry_authority = lambda state: self.armed
        self.state: dict[str, Any] = {"real_trading_locked": False, "emergency": {}, "events": [], "policy": POLICY}
        patcher = patch.object(grid_live, "TICK_SECONDS", 0.005)
        patcher.start()
        self.addCleanup(patcher.stop)

    def scenario(self, body, plan=None):
        async def go():
            self.adapter = BinanceLiveGridAdapter(self.api, asyncio.get_running_loop(), timeout=5)
            out = await self.runner.start(None, self.state, self.adapter, plan or prepare(recycle=True), SYM, user_id="u1", owner=False)
            gid = out["started_grid_id"]
            try:
                return await body(gid)
            finally:
                for task in self.runner._tasks.values():
                    task.cancel()
                await asyncio.gather(*self.runner._tasks.values(), return_exceptions=True)
        return asyncio.run(go())

    async def until(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise AssertionError("koşul gerçekleşmedi")
            await asyncio.sleep(0.01)

    def status(self, gid):
        return self.runner.store.grid(gid)["status"]

    def open_entries(self):
        return [o for o in self.ex.orders.values() if o.type == "LIMIT" and not o.reduce_only and o.status == NEW]

    def live_stops(self):
        return [o for o in self.ex.orders.values() if o.type == STOP_MARKET and o.status in (NEW, "PARTIALLY_FILLED")]


class TestArmExpiry(ArmBase):
    def test_arm_expiry_pauses_cancels_entries_and_keeps_protection(self):
        async def body(gid):
            self.ex.set_price(SYM, 98.9)                                  # 1. kademe doldu
            await self.until(lambda: len(self.live_stops()) == 1)
            self.armed = False                                            # ARM süresi doldu
            await self.until(lambda: self.status(gid) == PAUSED)
            await asyncio.sleep(0.05)                                     # birkaç tur daha geçsin
            self.assertEqual(self.status(gid), PAUSED)
            self.assertEqual(self.runner.store.grid(gid)["halt_reason"], grid_live.ARM_PAUSE_REASON)
            self.assertEqual(self.open_entries(), [])                     # bekleyen giriş iptal
            self.assertEqual([round(o.qty, 8) for o in self.live_stops()], [round(self.ex.positions[SYM], 8)])
            self.assertIn("LIVE_GRID_ARM_PAUSED", [k for k, _ in self.events])
        self.scenario(body)

    def test_flat_arm_paused_grid_waits_instead_of_finishing(self):
        async def body(gid):
            self.armed = False
            await self.until(lambda: self.status(gid) == PAUSED)
            await asyncio.sleep(0.08)
            self.assertEqual(self.status(gid), PAUSED)                    # pozisyon yok ama bitmez: ARM bekler
            self.assertTrue(self.runner.loop_running(gid))
        self.scenario(body)

    def test_ordinary_pause_still_finishes_when_flat(self):
        async def body(gid):
            await self.runner.pause(self.state, self.adapter, user_id="u1", grid_id=gid, owner=False)
            self.assertEqual(self.status(gid), STOPPED)
        self.scenario(body)

    def test_no_new_cycle_while_arm_is_missing(self):
        async def body(gid):
            self.ex.set_price(SYM, 98.9)
            await self.until(lambda: len(self.live_stops()) == 1)
            self.armed = False
            await self.until(lambda: self.status(gid) == PAUSED)
            before = len([o for o in self.ex.orders.values() if o.type == "LIMIT" and not o.reduce_only])
            self.ex.set_price(SYM, 100.2)                                 # hedef doldu: tur kapanırdı
            await asyncio.sleep(0.08)
            after = len([o for o in self.ex.orders.values() if o.type == "LIMIT" and not o.reduce_only])
            self.assertEqual(before, after)                               # ARM yokken yeni tur girişi yok
        self.scenario(body)

    def test_resume_after_rearm_reopens_valid_entries_only(self):
        async def body(gid):
            self.armed = False
            await self.until(lambda: self.status(gid) == PAUSED)
            self.armed = True
            self.ex.set_price(SYM, 98.5)                                  # fiyat 99'un altında: 99 kademesi artık yanlış tarafta
            self.api.mark = 98.5
            await self.runner.resume(None, self.state, self.adapter, user_id="u1", grid_id=gid, owner=False, reopen=True,
                                     price_lookup=self.price)
            self.assertEqual(self.status(gid), RUNNING)
            self.assertEqual(self.runner.store.grid(gid)["halt_reason"], "")
            await self.until(lambda: len(self.open_entries()) == 1)
            self.assertEqual([o.price for o in self.open_entries()], [98.0])
            message = next(m for k, m in self.events if k == "LIVE_GRID_ARM_RESUMED")
            self.assertIn("1 giriş", message)
            self.assertIn("1 kademe açılmadı", message)
        self.scenario(body)

    def test_resume_without_arm_keeps_grid_paused(self):
        async def body(gid):
            self.armed = False
            await self.until(lambda: self.status(gid) == PAUSED)
            await self.runner.resume(None, self.state, self.adapter, user_id="u1", grid_id=gid, owner=False, reopen=False)
            await asyncio.sleep(0.05)
            self.assertEqual(self.status(gid), PAUSED)
            self.assertEqual(self.open_entries(), [])
        self.scenario(body)

    def test_unreadable_price_reopens_nothing(self):
        async def body(gid):
            self.armed = False
            await self.until(lambda: self.status(gid) == PAUSED)
            self.armed = True
            self.api.mark = 0.0
            with self.assertRaises(grid_live.LiveGridError):
                await self.runner.resume(None, self.state, self.adapter, user_id="u1", grid_id=gid, owner=False, reopen=True,
                                     price_lookup=self.price)
            self.assertEqual(self.status(gid), PAUSED)
            self.assertEqual(self.open_entries(), [])
        self.scenario(body)

    def test_user_stop_finishes_an_arm_paused_grid(self):
        async def body(gid):
            self.armed = False
            await self.until(lambda: self.status(gid) == PAUSED)
            self.runner._tasks[gid].cancel()
            await asyncio.sleep(0)
            await self.runner.pause(self.state, self.adapter, user_id="u1", grid_id=gid, owner=False)
            self.assertEqual(self.status(gid), STOPPED)
        self.scenario(body)


class TestAuthoritySource(unittest.TestCase):
    def test_demo_grids_have_no_arm(self):
        runner = grid_live.LiveGridRunner(GridStore(":memory:"), account="DEMO")
        self.assertTrue(runner.entry_authority({}))

    def test_live_authority_follows_arm_or_supervised_session(self):
        from app import v25_execution
        runner = grid_live.LiveGridRunner(GridStore(":memory:"))
        state = v25_execution.initial_state()
        state.update(armed_until=time.time() + 600, real_trading_locked=False)
        self.assertTrue(runner.entry_authority(state))
        state.update(armed_until=time.time() - 1)
        self.assertFalse(runner.entry_authority(state))
        self.assertTrue(state["real_trading_locked"])                    # ARM dolumu mevcut kilit kuralını izler
        state["auto"].update(enabled=True, session_until=time.time() + 600)
        state["live_auto_trade"] = True
        self.assertTrue(runner.entry_authority(state))                   # gözetimli otomatik oturum da yetki sayılır


if __name__ == "__main__":
    unittest.main()
