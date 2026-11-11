"""DCA kârı koru + trend dönüşü — SAHTE borsa, AĞ YOK, GERÇEK EMİR YOK.
Çalıştırma: python -m pytest tests/test_dca_protect.py -q
Kurallar: stop her an borsada doğrulanmış kalır, yalnız kâr yönüne gider; kâr korununca/trend dönünce ek alım durur ve geri kurulmaz;
trend dönünce kârdaysa kapatılır, zarardaysa stop fiyata yaklaşır ama fiyata çok yakın konmaz.
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.dca_engine.engine import DcaEngine, spec_from_plan  # noqa: E402
from app.dca_engine.plan import prepare_dca_plan  # noqa: E402
from app.grid_engine.engine import RUNNING, STOPPED  # noqa: E402
from app.grid_engine.fake_exchange import FakeExchange  # noqa: E402
from app.grid_engine.ports import LONG, NEW, PARTIAL, SHORT  # noqa: E402
from app.grid_engine.store import GridStore  # noqa: E402

SYM = "ETHUSDT"
RULES = {"tick": 0.01, "step": 0.001, "min_qty": 0.001, "min_notional": 5}
POLICY = {"max_leverage": 5, "max_loss_per_trade": 50, "daily_loss_limit": 100, "max_total_exposure_usdt": 5000, "max_positions": 5, "max_stop_distance_pct": 5}


class Base(unittest.TestCase):
    direction = LONG

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"KAISTRADE_DCA_BREAKEVEN": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ex = FakeExchange(tick=0.01, step=0.001)
        self.ex.prices[SYM] = 100.0
        self.store = GridStore()
        self.plan = prepare_dca_plan(direction=self.direction, price=100.0, base_margin_usdt=10.0, safety_orders=3, step_pct=1.5, volume_multiplier=1.5,
                                     take_profit_pct=1.0, stop_gap_pct=1.5, leverage=3, rules=RULES, policy=POLICY)
        self.eng = DcaEngine(self.ex, self.store, spec_from_plan("p1", SYM, self.plan, tick=0.01))
        self.assertEqual(self.eng.start(), RUNNING)
        self.eng.tick()
        self.sign = 1 if self.direction == LONG else -1

    def live(self, kind):
        return [o for o in self.ex.orders.values() if o.status in (NEW, PARTIAL) and o.type == kind]

    def safeties(self):
        return [o for o in self.live("LIMIT") if not o.reduce_only]

    def stop(self):
        (stop,) = self.live("STOP_MARKET")
        return stop

    def move(self, pct):
        self.ex.set_price(SYM, round(100.0 * (1 + self.sign * pct / 100), 2))
        self.eng.tick()


class BreakevenLong(Base):
    def test_small_move_keeps_original_stop_and_safeties(self):
        self.move(0.2)
        self.assertEqual(self.stop().stop_price, self.plan["stop_price"])
        self.assertTrue(self.safeties())

    def test_half_target_moves_stop_past_entry_and_cancels_safeties(self):
        self.assertTrue(self.safeties())
        self.move(0.6)
        stop = self.stop()
        self.assertGreater(self.sign * (stop.stop_price - 100.0), 0)               # stop girişin kâr tarafında
        self.assertGreater(self.sign * (100.6 if self.sign > 0 else 99.4) - self.sign * stop.stop_price, 0)
        self.assertEqual(self.safeties(), [])                                       # ek alım yok
        self.assertTrue(self.eng.stats()["breakeven"])
        self.move(0.3)                                                              # geri çekilme: stop geri gitmez, ek alım geri kurulmaz
        self.assertEqual(self.safeties(), [])

    def test_breakeven_stop_exit_counts_as_win_not_loss(self):
        self.move(0.6)
        be = self.stop().stop_price
        self.ex.set_price(SYM, round(be - self.sign * 0.05, 2))                    # fiyat geri döndü: stop tetiklenir
        self.eng.tick(); self.eng.tick()
        self.assertNotEqual(self.store.grid(self.eng.gid)["halt_reason"], "Stop tetiklendi")
        self.assertGreaterEqual(self.eng.stats()["realized_usdt"], -0.05)            # ücret düşülse de zarar neredeyse yok

    def test_disabled_flag_keeps_old_behaviour(self):
        with mock.patch.dict(os.environ, {"KAISTRADE_DCA_BREAKEVEN": "0"}):
            self.move(0.6)
            self.assertEqual(self.stop().stop_price, self.plan["stop_price"])


class BreakevenShort(BreakevenLong):
    direction = SHORT


class TrendLong(Base):
    def test_in_loss_tightens_stop_and_stops_safeties(self):
        self.move(-0.8)                                                             # ters gitti, henüz ek alım yok
        before = self.stop().stop_price
        self.assertEqual(self.eng.trend_reversal("Kais ters %85"), "tightened")
        after = self.stop().stop_price
        self.assertGreater(self.sign * (after - before), 0)                         # stop yaklaştı (zarar küçüldü)
        price = 100.0 * (1 - self.sign * 0.8 / 100)
        self.assertGreaterEqual(self.sign * (price - after) / price * 100, 0.29)     # fiyata çok yakın değil
        self.assertEqual(self.safeties(), [])
        self.eng.tick()
        self.assertEqual(self.safeties(), [])                                       # geri kurulmadı
        self.assertTrue(self.eng.stats()["trend_hold"])
        self.assertEqual(self.eng.trend_reversal("tekrar"), "skip")                  # bir kez

    def test_in_profit_closes_and_bot_stops(self):
        self.move(0.25)
        self.assertEqual(self.eng.trend_reversal("Kais ters %90"), "closed")
        self.eng.tick(); self.eng.tick()
        self.assertEqual(self.eng.grid_net(), 0)
        self.assertEqual(self.eng.status, STOPPED)
        self.assertIn("Trend döndü", self.store.grid(self.eng.gid)["halt_reason"])
        self.assertEqual(self.live("STOP_MARKET"), [])
        self.assertEqual(self.safeties(), [])

    def test_never_moves_stop_backwards(self):
        self.move(-1.4)                                                             # stopa çok yakın
        before = self.stop().stop_price
        self.eng.trend_reversal("Kais ters")
        self.assertGreaterEqual(self.sign * (self.stop().stop_price - before), 0)


class TrendShort(TrendLong):
    direction = SHORT


class RunnerTrendGuard(unittest.TestCase):
    def test_opposite_strong_signal_triggers_once(self):
        from app import dca_live
        calls = []

        class Eng:
            gid, sym, status = "g", "ETHUSDT", RUNNING
            spec = type("S", (), {"side": LONG, "meta": {}})()
            def grid_net(self): return 1.0
            def trend_reversal(self, reason): calls.append(reason); return "tightened"

        runner = dca_live.DcaRunner(GridStore())
        async def rows(): return [{"symbol": "ETHUSDT", "direction": "SHORT", "confidence": 88}]
        runner.trend_fetch = rows
        runner._record = lambda *a, **k: None
        eng = Eng()
        asyncio.run(runner._trend_guard(eng, {}))
        self.assertEqual(len(calls), 1)
        self.assertIn("SHORT", calls[0])

    def test_weak_or_same_direction_or_unreadable_does_nothing(self):
        from app import dca_live
        calls = []

        class Eng:
            gid, sym, status = "g", "ETHUSDT", RUNNING
            spec = type("S", (), {"side": LONG, "meta": {}})()
            def grid_net(self): return 1.0
            def trend_reversal(self, reason): calls.append(reason); return "tightened"

        for rows_value in ([{"symbol": "ETHUSDT", "direction": "SHORT", "confidence": 60}], [{"symbol": "ETHUSDT", "direction": "LONG", "confidence": 95}], None):
            runner = dca_live.DcaRunner(GridStore())
            async def rows(v=rows_value):
                if v is None:
                    raise RuntimeError("okunamadı")
                return v
            runner.trend_fetch = rows
            runner._record = lambda *a, **k: None
            asyncio.run(runner._trend_guard(Eng(), {}))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
