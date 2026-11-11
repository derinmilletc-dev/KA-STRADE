"""DCA takip eden hedef — SAHTE borsa, AĞ YOK, GERÇEK EMİR YOK.
Çalıştırma: python -m unittest tests.test_dca_trailing -v
Kural: stop yalnız kâr yönünde ilerler, her an borsada doğrulanmış kalır, reddedilirse eski stop korur; kârlı stop çıkışı hedef dolumu sayılır.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.dca_engine.engine import DcaEngine, spec_from_plan  # noqa: E402
from app.dca_engine.plan import DcaPlanError, prepare_dca_plan  # noqa: E402
from app.grid_engine.engine import COMPLETED, RUNNING, STOPPED  # noqa: E402
from app.grid_engine.fake_exchange import FakeExchange  # noqa: E402
from app.grid_engine.ports import LONG, NEW, PARTIAL, SHORT  # noqa: E402
from app.grid_engine.store import GridStore  # noqa: E402

SYM = "ETHUSDT"
RULES = {"tick": 0.01, "step": 0.001, "min_qty": 0.001, "min_notional": 5}
POLICY = {"max_leverage": 5, "max_loss_per_trade": 50, "daily_loss_limit": 100, "max_total_exposure_usdt": 5000, "max_positions": 5, "max_stop_distance_pct": 5}


def plan_for(direction=LONG, price=100.0, **kw):
    args = dict(direction=direction, price=price, base_margin_usdt=10.0, safety_orders=3, step_pct=1.5, volume_multiplier=1.5,
                take_profit_pct=1.0, stop_gap_pct=1.5, leverage=3, rules=RULES, policy=POLICY)
    args.update(kw)
    return prepare_dca_plan(**args)


class TestPlanValidation(unittest.TestCase):
    def test_valid_trailing_is_stored_in_plan_and_spec(self):
        plan = plan_for(trailing_pct=0.4)
        self.assertEqual(plan["trailing_pct"], 0.4)
        self.assertEqual(spec_from_plan("g", SYM, plan, tick=0.01).meta["trailing_pct"], 0.4)

    def test_no_trailing_by_default(self):
        plan = plan_for()
        self.assertEqual(plan["trailing_pct"], 0.0)
        self.assertNotIn("trailing_pct", spec_from_plan("g", SYM, plan, tick=0.01).meta)

    def test_too_tight_trailing_rejected(self):
        with self.assertRaises(DcaPlanError):
            plan_for(trailing_pct=0.1)

    def test_trailing_must_leave_locked_profit(self):
        with self.assertRaises(DcaPlanError):
            plan_for(take_profit_pct=1.0, trailing_pct=0.9)      # kilitlenen kâr yalnız %0,1: ücreti karşılamaz
        plan_for(take_profit_pct=1.0, trailing_pct=0.8)           # %0,2 kilitlenir: kabul


class TrailBase(unittest.TestCase):
    direction = LONG
    trail = 0.4

    def setUp(self):
        self.ex = FakeExchange(tick=0.01, step=0.001)
        self.ex.prices[SYM] = 100.0
        self.store = GridStore()
        self.plan = plan_for(self.direction, trailing_pct=self.trail)
        self.eng = DcaEngine(self.ex, self.store, spec_from_plan("t1", SYM, self.plan, tick=0.01))
        self.assertEqual(self.eng.start(), RUNNING)

    def stops(self):
        return [o for o in self.ex.orders.values() if o.status in (NEW, PARTIAL) and o.type == "STOP_MARKET"]

    def targets(self):
        return [o for o in self.ex.orders.values() if o.status in (NEW, PARTIAL) and o.reduce_only and o.type == "LIMIT"]

    def move(self, price):
        self.ex.set_price(SYM, price)
        self.eng.tick()


class TestTrailingLong(TrailBase):
    def test_no_limit_target_and_safeties_still_placed(self):
        self.assertEqual(self.targets(), [])                                  # borsada limit hedef yok
        (stop,) = self.stops()
        self.assertEqual(stop.stop_price, self.plan["stop_price"])
        entries = [o for o in self.ex.orders.values() if o.status in (NEW, PARTIAL) and not o.reduce_only and o.type == "LIMIT"]
        self.assertEqual(len(entries), 3)                                      # ek emirler: kapı yalnız doğrulanmış stoptur

    def test_stop_untouched_below_activation(self):
        self.move(100.6)                                                       # hedef 101,00
        (stop,) = self.stops()
        self.assertEqual(stop.stop_price, self.plan["stop_price"])
        self.assertEqual(self.eng.stats()["trail_active"], False)

    def test_stop_moves_up_when_target_reached_and_never_down(self):
        self.move(101.2)                                                       # hedefe ulaşıldı: tepe 101,2 -> stop 100,79
        (stop,) = self.stops()
        self.assertAlmostEqual(stop.stop_price, 100.79, places=2)
        self.assertAlmostEqual(self.eng.spec.stop_price, 100.79, places=2)
        self.assertTrue(self.eng.stats()["trail_active"])
        self.move(102.0)                                                       # yeni tepe: stop 101,59
        (stop,) = self.stops()
        self.assertAlmostEqual(stop.stop_price, 101.59, places=2)
        self.move(101.7)                                                       # geri çekilme: stop geri GİTMEZ
        (stop,) = self.stops()
        self.assertAlmostEqual(stop.stop_price, 101.59, places=2)
        self.assertEqual(len(self.stops()), 1)                                 # her an tek doğrulanmış stop, yetim stop yok

    def test_position_always_covered_by_verified_stop(self):
        for price in (101.2, 101.5, 102.0, 101.9):
            self.move(price)
            self.assertAlmostEqual(sum(o.qty - o.filled for o in self.stops()), abs(self.ex.positions[SYM]), places=6)

    def test_profitable_stop_exit_counts_as_target_not_loss_stop(self):
        self.move(102.0)
        self.move(101.5)                                                       # çekilmiş stop (101,59) tetiklenir
        self.assertAlmostEqual(self.ex.positions.get(SYM, 0.0), 0.0, places=6)
        self.assertEqual(self.eng.status, COMPLETED)                           # STOPPED (zarar stopu) değil
        stats = self.eng.stats()
        self.assertEqual(stats["completed_cycles"], 1)
        self.assertGreater(stats["realized_usdt"], 0)

    def test_loss_stop_before_activation_is_still_a_stop(self):
        self.move(94.0)                                                        # hedefe hiç ulaşılmadı: klasik zarar stopu
        self.assertEqual(self.eng.status, STOPPED)
        self.assertEqual(self.eng.stats()["completed_cycles"], 0)

    def test_rejected_new_stop_keeps_old_stop_then_retries(self):
        self.move(101.2)
        (old,) = self.stops()
        self.ex.reject_next = [(-4130, "x"), (-4130, "x")]                     # tur iki kez korumayı çalıştırır: ikisi de reddedilsin
        self.move(102.0)
        (still,) = self.stops()
        self.assertEqual(still.client_id, old.client_id)                       # eski stop yerinde, pozisyon korumasız kalmadı
        self.assertEqual(self.eng.status, RUNNING)
        self.move(102.0)                                                       # reddedilmeyen turda yeniden denenir
        (new,) = self.stops()
        self.assertAlmostEqual(new.stop_price, 101.59, places=2)

    def test_price_unreadable_does_nothing(self):
        self.move(101.2)
        before = [o.client_id for o in self.stops()]
        self.ex.position_info_down = True
        self.ex.prices[SYM] = 103.0
        self.eng.tick()
        self.assertEqual([o.client_id for o in self.stops()], before)

    def test_trail_state_survives_restart(self):
        self.move(102.0)
        from app.grid_live import grid_spec_from_row
        spec = grid_spec_from_row(self.store.grid("t1"))
        again = DcaEngine(self.ex, self.store, spec)
        self.assertAlmostEqual(again.spec.stop_price, 101.59, places=2)
        self.assertTrue(again.stats()["trail_active"])


class TestTrailingShort(TrailBase):
    direction = SHORT

    def test_mirror_for_short(self):
        self.move(98.8)                                                        # SHORT hedefi 99,00 aşıldı: tepe (dip) 98,8 -> stop 99,20
        (stop,) = self.stops()
        self.assertAlmostEqual(stop.stop_price, 99.20, places=2)
        self.move(98.0)
        (stop,) = self.stops()
        self.assertAlmostEqual(stop.stop_price, 98.40, places=2)
        self.move(98.2)                                                        # geri çekilme: stop yukarı GİTMEZ
        (stop,) = self.stops()
        self.assertAlmostEqual(stop.stop_price, 98.40, places=2)
        self.move(98.5)                                                        # stop tetiklenir
        self.assertEqual(self.eng.status, COMPLETED)
        self.assertGreater(self.eng.stats()["realized_usdt"], 0)


class TestClassicUnchanged(unittest.TestCase):
    def test_without_trailing_single_limit_target_exists(self):
        ex = FakeExchange(tick=0.01, step=0.001)
        ex.prices[SYM] = 100.0
        eng = DcaEngine(ex, GridStore(), spec_from_plan("c1", SYM, plan_for(), tick=0.01))
        eng.start()
        targets = [o for o in ex.orders.values() if o.status in (NEW, PARTIAL) and o.reduce_only and o.type == "LIMIT"]
        self.assertEqual(len(targets), 1)
        self.assertFalse(eng.stats()["trail_active"])


if __name__ == "__main__":
    unittest.main()
