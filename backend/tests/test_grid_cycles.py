"""Grid turları (yeniden giriş) ve çoklu grid defteri — SAHTE borsa; ağ/anahtar/emir yok."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.grid_engine.engine import (COMPLETED, PAUSED, RUNNING, STOPPED, GridEngine, GridLevelSpec, GridSpec, client_order_id,
                                    grid_stats)
from app.grid_engine.fake_exchange import FakeExchange
from app.grid_engine.ports import EPS, LONG, NEW, PARTIAL, SHORT
from app.grid_engine.store import GridStore

SYM = "BTCUSDT"


def spec(side=LONG, recycle=True, gid="g1", symbol=SYM) -> GridSpec:
    if side == LONG:
        levels = [GridLevelSpec(98.0, 99.0, 0.5), GridLevelSpec(96.0, 97.0, 0.5)]
        stop = 90.0
    else:
        levels = [GridLevelSpec(102.0, 101.0, 0.5), GridLevelSpec(104.0, 103.0, 0.5)]
        stop = 110.0
    return GridSpec(gid, symbol, side, levels, stop, recycle=recycle)


class CycleBase(unittest.TestCase):
    def setUp(self):
        self.ex = FakeExchange()
        self.ex.prices[SYM] = 100.0
        self.store = GridStore()

    def run_ticks(self, engine, n=2):
        for _ in range(n):
            engine.tick()

    def live(self, role=None):
        return [o for o in self.ex.orders.values() if o.status in (NEW, PARTIAL) and (role is None or o.client_id.split("-")[-1][3] == role)]


class TestRecycle(CycleBase):
    def test_target_fill_reopens_the_same_level_and_counts_a_cycle(self):
        engine = GridEngine(self.ex, self.store, spec())
        self.assertEqual(engine.start(), RUNNING)
        self.ex.set_price(SYM, 97.9)           # 1. kademe dolar
        self.run_ticks(engine)
        self.assertAlmostEqual(self.ex.positions[SYM], 0.5)
        self.ex.set_price(SYM, 99.1)           # hedef dolar → pozisyon kapanır
        self.run_ticks(engine)
        self.assertAlmostEqual(self.ex.positions.get(SYM, 0.0), 0.0)
        entries = [o for o in self.live("E") if o.price == 98.0]
        self.assertEqual(len(entries), 1, "aynı kademede yeni giriş açılmalı")
        self.assertEqual(entries[0].client_id, client_order_id("g1", LONG, 1, "E", 1))
        self.assertEqual(engine.status, RUNNING)
        stats = engine.stats()
        self.assertEqual(stats["cycles"], 1)
        self.assertAlmostEqual(stats["gross_usdt"], 0.5)          # (99-98)*0.5
        self.assertLess(stats["realized_usdt"], stats["gross_usdt"])   # ücret tahmini düşülür
        self.assertEqual(stats["open_levels"], [])

    def test_second_cycle_gets_its_own_target_and_stop_covers_only_the_open_qty(self):
        engine = GridEngine(self.ex, self.store, spec())
        engine.start()
        for price in (97.9, 99.1, 97.9):       # tur 1 tamam, tur 2 girişi doldu
            self.ex.set_price(SYM, price)
            self.run_ticks(engine)
        self.assertAlmostEqual(self.ex.positions[SYM], 0.5)
        targets = self.live("T")
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].qty, 0.5)
        row = self.store.order(targets[0].client_id)
        self.assertEqual(row["cycle"], 1)
        stops = [o for o in self.live() if o.type == "STOP_MARKET"]
        self.assertEqual(len(stops), 1)
        self.assertAlmostEqual(stops[0].qty, 0.5)
        self.assertEqual(engine.stats()["open_levels"], [{"level": 1, "cycle": 1, "entry": 98.0, "qty": 0.5}])

    def test_without_recycle_the_old_one_shot_behaviour_is_kept(self):
        engine = GridEngine(self.ex, self.store, spec(recycle=False))
        engine.start()
        for price in (95.9, 99.5):              # iki kademe dolar, sonra ikisinin hedefi
            self.ex.set_price(SYM, price)
            self.run_ticks(engine)
        self.assertEqual(engine.status, COMPLETED)
        self.assertEqual(self.live("E"), [])

    def test_recycling_grid_does_not_complete_while_running(self):
        engine = GridEngine(self.ex, self.store, spec())
        engine.start()
        for price in (95.9, 99.5):
            self.ex.set_price(SYM, price)
            self.run_ticks(engine)
        self.assertEqual(engine.status, RUNNING)
        self.assertEqual(len(self.live("E")), 2)
        self.assertEqual(engine.stats()["cycles"], 2)

    def test_paused_grid_never_opens_a_new_cycle(self):
        engine = GridEngine(self.ex, self.store, spec())
        engine.start()
        self.ex.set_price(SYM, 97.9)
        self.run_ticks(engine)
        engine.pause()
        self.assertEqual(engine.status, PAUSED)
        self.ex.set_price(SYM, 99.1)            # hedef dolar; duraklatılmışken yeni giriş açılmamalı
        self.run_ticks(engine)
        self.assertEqual(self.live("E"), [])

    def test_stop_ends_a_recycling_grid(self):
        engine = GridEngine(self.ex, self.store, spec())
        engine.start()
        self.ex.set_price(SYM, 97.9)
        self.run_ticks(engine)
        self.ex.set_price(SYM, 89.0)            # stop tetiklenir
        self.run_ticks(engine)
        self.assertEqual(engine.status, STOPPED)
        self.assertEqual(self.live(), [])
        self.assertAlmostEqual(self.ex.positions.get(SYM, 0.0), 0.0)

    def test_short_grid_cycles_too(self):
        self.ex.prices[SYM] = 100.0
        engine = GridEngine(self.ex, self.store, spec(SHORT))
        engine.start()
        for price in (102.1, 100.9):
            self.ex.set_price(SYM, price)
            self.run_ticks(engine)
        self.assertAlmostEqual(self.ex.positions.get(SYM, 0.0), 0.0)
        self.assertEqual(len([o for o in self.live("E") if o.price == 102.0]), 1)
        self.assertEqual(engine.stats()["cycles"], 1)
        self.assertAlmostEqual(engine.stats()["gross_usdt"], 0.5)


class TestStoreMultiGrid(unittest.TestCase):
    def test_grids_are_filtered_by_owner_and_legacy_rows_only_on_request(self):
        store = GridStore()
        store.create_grid("old", SYM, LONG, {"levels": []})                 # çoklu grid öncesi kayıt (sahipsiz)
        store.create_grid("a", SYM, LONG, {"levels": []}, user_id="u1")
        store.create_grid("b", "ETHUSDT", SHORT, {"levels": []}, user_id="u2")
        self.assertEqual([r["grid_id"] for r in store.grids(user_id="u1")], ["a"])
        self.assertEqual({r["grid_id"] for r in store.grids(user_id="u1", include_legacy=True)}, {"a", "old"})
        self.assertEqual([r["grid_id"] for r in store.grids(user_id="u2")], ["b"])
        store.set_grid("a", status="STOPPED")
        self.assertEqual(store.grids(user_id="u1", exclude_statuses={"STOPPED"}), [])

    def test_old_ledger_files_are_upgraded_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "old.sqlite3")
            db = sqlite3.connect(path)
            db.executescript("""
            CREATE TABLE grid(grid_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL, status TEXT NOT NULL,
              spec TEXT NOT NULL, baseline REAL NOT NULL DEFAULT 0, halt_reason TEXT NOT NULL DEFAULT '');
            CREATE TABLE orders(client_id TEXT PRIMARY KEY, grid_id TEXT NOT NULL, role TEXT NOT NULL, level INTEGER NOT NULL, rev INTEGER NOT NULL,
              side TEXT NOT NULL, type TEXT NOT NULL, price REAL NOT NULL, stop_price REAL NOT NULL, qty REAL NOT NULL,
              filled REAL NOT NULL DEFAULT 0, status TEXT NOT NULL, reduce_only INTEGER NOT NULL, misses INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, grid_id TEXT NOT NULL, level TEXT NOT NULL, text TEXT NOT NULL);
            INSERT INTO grid(grid_id,symbol,side,status,spec) VALUES('legacy','BTCUSDT','LONG','RUNNING','{"stop_price":1,"levels":[]}');
            INSERT INTO orders VALUES('kg-legacy-L01E0','legacy','E',1,0,'BUY','LIMIT',98,0,0.5,0.5,'FILLED',0,0);
            """)
            db.commit(); db.close()
            store = GridStore(path)
            row = store.grid("legacy")
            self.assertEqual((row["user_id"], row["status"]), ("", "RUNNING"))
            self.assertEqual(store.order("kg-legacy-L01E0")["cycle"], 0)
            store.alert("legacy", "bilgi", "ok")
            self.assertEqual(store.alerts("legacy"), [("bilgi", "ok")])

    def test_two_engines_on_different_symbols_share_one_ledger_without_mixing(self):
        ex = FakeExchange()
        ex.prices.update({SYM: 100.0, "ETHUSDT": 100.0})
        store = GridStore()
        a = GridEngine(ex, store, spec(gid="ga"), user_id="u1")
        b = GridEngine(ex, store, spec(SHORT, gid="gb", symbol="ETHUSDT"), user_id="u1")
        self.assertEqual((a.start(), b.start()), (RUNNING, RUNNING))
        ex.set_price(SYM, 97.9)
        ex.set_price("ETHUSDT", 102.1)
        for engine in (a, b, a, b):
            engine.tick()
        self.assertAlmostEqual(ex.positions[SYM], 0.5)
        self.assertAlmostEqual(ex.positions["ETHUSDT"], -0.5)
        self.assertEqual((a.status, b.status), (RUNNING, RUNNING))
        self.assertTrue(all(o["grid_id"] == "ga" for o in store.orders("ga")))
        self.assertEqual(store.grid("gb")["user_id"], "u1")


class TestStatsPure(unittest.TestCase):
    def test_stats_of_an_empty_ledger(self):
        out = grid_stats(spec(), [])
        self.assertEqual((out["cycles"], out["realized_usdt"], out["open_levels"]), (0, 0.0, []))
        self.assertLess(abs(out["fees_usdt"]), EPS)



class TestForcedClose(CycleBase):
    def test_recycling_grid_stops_after_price_jumped_beyond_the_stop(self):
        engine = GridEngine(self.ex, self.store, spec())
        engine.start()
        self.ex.set_price(SYM, 95.9)            # iki kademe dolar; motor görmeden fiyat stopun altına iner
        self.ex.prices[SYM] = 89.0
        engine.tick()
        self.assertAlmostEqual(self.ex.positions.get(SYM, 0.0), 0.0)
        self.assertEqual(engine.status, STOPPED)
        engine.tick()
        self.assertEqual(self.live(), [])


if __name__ == "__main__":
    unittest.main()
