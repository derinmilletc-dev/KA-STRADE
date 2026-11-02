"""Nötr grid motoru — SAHTE borsa (tek yönlü pozisyon modeli). Ağ/anahtar/emir yok."""
from __future__ import annotations

import unittest

from app.grid_engine.engine import EMERGENCY_CLOSED, PAUSED, RUNNING, STOPPED, GridLevelSpec, GridSpec, grid_stats
from app.grid_engine.fake_exchange import FakeExchange
from app.grid_engine.neutral import NEUTRAL, NeutralGridEngine
from app.grid_engine.ports import BUY, LONG, NEW, PARTIAL, SELL, SHORT, STOP_MARKET
from app.grid_engine.store import GridStore

SYM = "BTCUSDT"


def neutral_spec(gid="n1") -> GridSpec:
    # Çizgiler 97..103; fiyata (100) en yakın çizgide emir yok.
    levels = [GridLevelSpec(97.0, 98.0, 0.5, LONG), GridLevelSpec(98.0, 99.0, 0.5, LONG), GridLevelSpec(99.0, 100.0, 0.5, LONG),
              GridLevelSpec(101.0, 100.0, 0.5, SHORT), GridLevelSpec(102.0, 101.0, 0.5, SHORT), GridLevelSpec(103.0, 102.0, 0.5, SHORT)]
    return GridSpec(gid, SYM, NEUTRAL, levels, 95.0, recycle=True, stop_price_upper=105.0)


class NeutralBase(unittest.TestCase):
    def setUp(self):
        self.ex = FakeExchange()
        self.ex.prices[SYM] = 100.0
        self.store = GridStore()
        self.engine = NeutralGridEngine(self.ex, self.store, neutral_spec())
        self.assertEqual(self.engine.start(), RUNNING)

    def move(self, *prices, ticks=2):
        for price in prices:
            self.ex.set_price(SYM, price)
            for _ in range(ticks):
                self.engine.tick()

    def live(self, role=None):
        return [o for o in self.ex.orders.values() if o.status in (NEW, PARTIAL) and (role is None or o.client_id.split("-")[-1][3] == role)]

    def stops(self):
        return [o for o in self.live() if o.type == STOP_MARKET]

    def pos(self):
        return round(self.ex.positions.get(SYM, 0.0), 8)


class TestNeutralGrid(NeutralBase):
    def test_start_places_buys_below_and_sells_above_with_no_order_at_the_price(self):
        entries = self.live("E")
        self.assertEqual(sorted((o.side, o.price) for o in entries),
                         [(BUY, 97.0), (BUY, 98.0), (BUY, 99.0), (SELL, 101.0), (SELL, 102.0), (SELL, 103.0)])
        self.assertTrue(all(not o.reduce_only for o in entries))
        self.assertEqual(self.stops(), [])

    def test_buy_fill_gets_a_sell_target_and_a_lower_stop(self):
        self.move(98.9)
        self.assertEqual(self.pos(), 0.5)
        targets = self.live("T")
        self.assertEqual([(o.side, o.price, o.qty) for o in targets], [(SELL, 100.0, 0.5)])
        self.assertEqual([(o.side, o.stop_price, o.qty, o.reduce_only) for o in self.stops()], [(SELL, 95.0, 0.5, True)])

    def test_round_trip_counts_a_cycle_and_reopens_the_level(self):
        self.move(98.9, 100.05)
        self.assertEqual(self.pos(), 0.0)
        self.assertEqual(self.stops(), [])
        self.assertIn((BUY, 99.0), [(o.side, o.price) for o in self.live("E")])
        stats = self.engine.stats()
        self.assertEqual(stats["cycles"], 1)
        self.assertAlmostEqual(stats["gross_usdt"], 0.5)

    def test_sell_fill_gets_a_buy_target_and_an_upper_stop(self):
        self.move(101.1)
        self.assertEqual(self.pos(), -0.5)
        self.assertEqual([(o.side, o.price) for o in self.live("T")], [(BUY, 100.0)])
        self.assertEqual([(o.side, o.stop_price, o.qty) for o in self.stops()], [(BUY, 105.0, 0.5)])
        self.move(99.95)                                  # short turu kapanır
        self.assertEqual(self.pos(), 0.0)
        self.assertEqual(self.engine.stats()["cycles"], 1)
        self.assertAlmostEqual(self.engine.stats()["gross_usdt"], 0.5)

    def test_gap_across_the_middle_flips_side_and_the_stop_follows(self):
        self.move(98.9)                                   # long 0.5
        self.move(101.5)                                  # long hedefi + short girişi aynı anda
        self.assertEqual(self.pos(), -0.5)
        self.assertEqual([(o.side, o.stop_price, o.qty) for o in self.stops()], [(BUY, 105.0, 0.5)])
        self.assertEqual(self.engine.status, RUNNING)
        self.assertEqual(self.engine.grid_net(), -0.5)

    def test_falling_through_the_lower_stop_closes_everything(self):
        self.move(96.9)                                   # üç alış dolar: long 1.5, stop büyür
        self.assertEqual(self.pos(), 1.5)
        self.assertEqual([(o.side, o.qty) for o in self.stops()], [(SELL, 1.5)])
        self.move(94.0)
        self.assertEqual(self.engine.status, STOPPED)
        self.assertEqual(self.pos(), 0.0)
        self.assertEqual(self.live(), [])

    def test_rising_through_the_upper_stop_closes_everything(self):
        self.move(103.5)
        self.assertEqual(self.pos(), -1.5)
        self.move(106.0)
        self.assertEqual(self.engine.status, STOPPED)
        self.assertEqual(self.pos(), 0.0)
        self.assertEqual(self.live(), [])

    def test_price_already_beyond_stop_is_closed_at_market(self):
        self.ex.set_price(SYM, 96.9)                      # alışlar dolar, motor görmeden fiyat stopun altına iner
        self.ex.prices[SYM] = 94.0
        self.engine.tick()
        self.assertEqual(self.pos(), 0.0)
        self.assertEqual(self.engine.status, STOPPED)

    def test_emergency_closes_a_short_position_with_a_buy(self):
        self.move(102.1)
        self.assertEqual(self.pos(), -1.0)
        self.assertEqual(self.engine.emergency_stop(), EMERGENCY_CLOSED)
        self.assertEqual(self.pos(), 0.0)
        self.assertEqual(self.live(), [])

    def test_pause_keeps_protection_and_opens_no_new_cycle(self):
        self.move(98.9)
        self.engine.pause()
        self.assertEqual(self.engine.status, PAUSED)
        self.assertEqual(self.live("E"), [])
        self.assertEqual(len(self.stops()), 1)
        self.move(100.05)                                 # hedef dolar; duraklatılmışken yeni giriş yok
        self.assertEqual(self.live("E"), [])

    def test_foreign_position_blocks_the_start(self):
        ex = FakeExchange()
        ex.prices[SYM] = 100.0
        ex.set_position(SYM, 0.2)
        engine = NeutralGridEngine(ex, GridStore(), neutral_spec("n2"))
        self.assertNotEqual(engine.start(), RUNNING)
        self.assertEqual([o for o in ex.orders.values()], [])

    def test_invalid_specs_are_refused(self):
        bad = neutral_spec("n3")
        bad.stop_price_upper = 0.0
        with self.assertRaises(ValueError):
            NeutralGridEngine(self.ex, GridStore(), bad)
        bad = neutral_spec("n4")
        bad.levels[0] = GridLevelSpec(97.0, 96.0, 0.5, LONG)
        with self.assertRaises(ValueError):
            NeutralGridEngine(self.ex, GridStore(), bad)

    def test_many_swings_stay_consistent_with_the_exchange(self):
        for price in (98.9, 100.05, 101.1, 99.95, 97.9, 101.6, 99.4, 102.3, 98.1, 100.0):
            self.move(price)
            self.assertEqual(self.engine.status, RUNNING)
            self.assertAlmostEqual(self.engine.grid_net(), self.pos())
            net = self.pos()
            stops = self.stops()
            if net > 0:
                self.assertEqual([(o.side, o.qty) for o in stops], [(SELL, net)])
            elif net < 0:
                self.assertEqual([(o.side, o.qty) for o in stops], [(BUY, -net)])
            else:
                self.assertEqual(stops, [])
        self.assertGreater(self.engine.stats()["cycles"], 3)
        self.assertGreater(self.engine.stats()["realized_usdt"], 0)


class TestNeutralStats(unittest.TestCase):
    def test_open_levels_carry_their_side(self):
        spec = neutral_spec()
        rows = [{"role": "E", "level": 4, "cycle": 0, "price": 101.0, "filled": 0.5, "status": "FILLED"}]
        out = grid_stats(spec, rows)
        self.assertEqual(out["open_levels"], [{"level": 4, "cycle": 0, "entry": 101.0, "qty": 0.5, "side": SHORT}])


if __name__ == "__main__":
    unittest.main()
