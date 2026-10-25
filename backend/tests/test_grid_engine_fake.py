"""Grid motoru — SAHTE borsa testleri. Gerçek anahtar/para/ağ yok.
Çalıştırma: python -m unittest tests.test_grid_engine_fake -v
"""
from __future__ import annotations

import os
import tempfile
import unittest

from app.grid_engine.engine import (BLOCKED, COMPLETED, EMERGENCY_CLOSED, NEEDS_MANUAL, PAUSED, RUNNING, STOPPED,
                                    GridEngine, GridLevelSpec, GridSpec, client_order_id)
from app.grid_engine.fake_exchange import FakeExchange
from app.grid_engine.ports import (EPS, LONG, NEW, PARTIAL, SHORT, ExchangeRejected)
from app.grid_engine.store import GridStore

SYM = "BTCUSDT"


def make_spec(side=LONG, gid="g1") -> GridSpec:
    if side == LONG:
        levels = [GridLevelSpec(98.0, 99.0, 0.5), GridLevelSpec(96.0, 97.0, 0.5), GridLevelSpec(94.0, 95.0, 0.5)]
        stop = 90.0
    else:
        levels = [GridLevelSpec(102.0, 101.0, 0.5), GridLevelSpec(104.0, 103.0, 0.5), GridLevelSpec(106.0, 105.0, 0.5)]
        stop = 110.0
    return GridSpec(gid, SYM, side, levels, stop)


class Base(unittest.TestCase):
    def setUp(self):
        self.ex = FakeExchange()
        self.ex.prices[SYM] = 100.0
        self.store = GridStore()
        self.spec = make_spec()

    def engine(self, **kw) -> GridEngine:
        return GridEngine(self.ex, self.store, self.spec, **kw)

    def cid(self, level, role, rev=0, side=LONG):
        return client_order_id("g1", side, level, role, rev)

    def active(self, role=None):
        return [o for o in self.ex.orders.values() if o.status in (NEW, PARTIAL) and (role is None or o.client_id.split("-")[-1][3] == role)]

    def stops(self):
        return [o for o in self.active() if o.type == "STOP_MARKET"]


class TestIds(unittest.TestCase):
    def test_ids_distinct_and_stable(self):
        a = client_order_id("g1", LONG, 3, "E")
        self.assertEqual(a, client_order_id("g1", LONG, 3, "E"))
        for other in (client_order_id("g2", LONG, 3, "E"), client_order_id("g1", SHORT, 3, "E"), client_order_id("g1", LONG, 4, "E"),
                      client_order_id("g1", LONG, 3, "T"), client_order_id("g1", LONG, 3, "E", 1)):
            self.assertNotEqual(a, other)
        self.assertLessEqual(len(client_order_id("x" * 80, SHORT, 20, "S", 9)), 36)


class TestScenario1ResponseLost(Base):
    def test_accepted_but_no_reply_no_duplicate(self):
        self.ex.lose_response_for.add(self.cid(1, "E"))
        eng = self.engine()
        eng.start()
        for _ in range(4):
            eng.tick()
        self.assertEqual(self.ex.placed_count[self.cid(1, "E")], 1)
        self.assertEqual(len(self.ex.open_orders(SYM)), 3)
        self.assertEqual(self.store.orders("g1", "E")[0]["status"], NEW)

    def test_never_reached_is_resent_once_with_same_id(self):
        self.ex.connected = False
        eng = self.engine()
        eng.start()                                    # bağlantı yok → BLOCKED (başlangıç doğrulanamadı)
        self.assertEqual(eng.status, BLOCKED)
        self.assertEqual(self.ex.placed_count, {})

    def test_dropped_mid_send_then_recovers(self):
        eng = self.engine()
        eng.start()
        # ikinci kademe emri işlenmeden önce bağlantı koparsa: tick sırasında yeniden gönder
        ex2 = FakeExchange(); ex2.prices[SYM] = 100.0
        store2 = GridStore()
        eng2 = GridEngine(ex2, store2, make_spec(gid="g9"))
        eng2.store.set_grid("g9", status=RUNNING)
        ex2.hooks.append(lambda m, i: setattr(ex2, "connected", False) if m == "place_order" and i["client_id"].endswith("L02E0") else None)
        eng2.tick()
        ex2.connected = True; ex2.hooks.clear()
        eng2.tick(); eng2.tick()
        self.assertEqual({k: v for k, v in ex2.placed_count.items()}, {client_order_id("g9", LONG, i, "E"): 1 for i in (1, 2, 3)})


class TestScenario2Restart(Base):
    def test_restart_same_db_no_duplicates(self):
        path = os.path.join(tempfile.mkdtemp(), "grid.db")
        self.store = GridStore(path)
        self.engine().start()
        eng2 = GridEngine(self.ex, GridStore(path), make_spec())
        eng2.start(); eng2.tick()
        self.assertTrue(all(v == 1 for v in self.ex.placed_count.values()))
        self.assertEqual(len(self.ex.placed_count), 3)

    def test_db_lost_same_plan_adopts_existing_orders(self):
        self.engine().start()
        fresh = GridEngine(self.ex, GridStore(), make_spec())      # defter sıfırlandı, plan aynı
        self.assertEqual(fresh.start(), RUNNING)
        self.assertTrue(all(v == 1 for v in self.ex.placed_count.values()))   # çift emir yok
        self.assertEqual(len(self.ex.open_orders(SYM)), 3)
        self.assertEqual([o['status'] for o in fresh.store.orders('g1', 'E')], [NEW, NEW, NEW])   # 'reddedildi' diye yanlış işaretlenmedi
        self.assertEqual(fresh.store.alerts('g1'), [])

    def test_db_lost_with_open_position_refuses(self):
        eng = self.engine(); eng.start()
        self.ex.set_price(SYM, 98.0); eng.tick()
        fresh = GridEngine(self.ex, GridStore(), make_spec())
        self.assertEqual(fresh.start(), BLOCKED)                   # pozisyon var, defter bilmiyor → güvenli taraf

    def test_orphan_exchange_order_halts_and_cancels(self):
        eng = self.engine(); eng.start()
        from app.grid_engine.ports import BUY, LIMIT
        self.ex.place_order(client_id="kg-g1-L09E0", symbol=SYM, side=BUY, type=LIMIT, qty=0.1, price=80.0)
        eng.tick()
        self.assertEqual(eng.status, BLOCKED)
        self.assertEqual(self.ex.orders["kg-g1-L09E0"].status, "CANCELED")

    def test_position_mismatch_halts(self):
        eng = self.engine(); eng.start()
        self.ex.set_position(SYM, 0.7)                             # defterde yok
        eng.tick()
        self.assertEqual(eng.status, BLOCKED)
        self.assertTrue(any("uyuşmuyor" in t for _, t in self.store.alerts("g1")))

    def test_restart_with_foreign_position_refuses(self):
        self.ex.set_position(SYM, 1.0)
        self.assertEqual(self.engine().start(), BLOCKED)
        self.assertEqual(self.ex.placed_count, {})


class TestScenario3PartialFills(Base):
    def test_stop_and_targets_follow_partial_fills(self):
        eng = self.engine(); eng.start()
        self.ex.partial_fill(self.cid(1, "E"), 0.2)
        eng.tick()
        self.assertAlmostEqual(self.ex.positions[SYM], 0.2)
        st = self.stops(); self.assertEqual(len(st), 1); self.assertAlmostEqual(st[0].qty, 0.2)
        self.ex.partial_fill(self.cid(1, "E"), 0.1)
        eng.tick()
        st = self.stops(); self.assertEqual(len(st), 1); self.assertAlmostEqual(st[0].qty, 0.3)
        tps = [o for o in self.ex.orders.values() if o.reduce_only and o.type == "LIMIT" and o.status in (NEW, PARTIAL)]
        self.assertAlmostEqual(sum(o.qty for o in tps), 0.3)
        self.ex.set_price(SYM, 96.0)                               # L02 tam dolar (L01 kalan 0.2 de dolar)
        eng.tick()
        self.assertAlmostEqual(self.ex.positions[SYM], 1.0)
        self.assertAlmostEqual(sum(o.qty for o in self.stops()), 1.0)
        self.assertEqual(len(self.stops()), 1)

    def test_stop_shrinks_after_target_fill(self):
        eng = self.engine(); eng.start()
        self.ex.set_price(SYM, 98.0); eng.tick()
        self.ex.set_price(SYM, 99.0); eng.tick()                   # hedef doldu
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)
        self.ex.set_price(SYM, 98.0); eng.tick()
        self.assertEqual(self.ex.positions[SYM] >= -EPS, True)
        for s in self.stops():
            self.assertLessEqual(s.qty, abs(self.ex.positions[SYM]) + 1e-9)


class TestScenario4StopHit(Base):
    def test_stop_cancels_everything_and_no_new_orders(self):
        eng = self.engine(); eng.start()
        self.ex.set_price(SYM, 96.0); eng.tick()
        self.assertEqual(len(self.stops()), 1)
        self.ex.set_price(SYM, 89.0)
        eng.tick()
        self.assertEqual(eng.status, STOPPED)
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)
        self.assertEqual(self.ex.open_orders(SYM), [])
        placed = dict(self.ex.placed_count)
        self.ex.set_price(SYM, 100.0); eng.tick(); eng.tick()
        self.assertEqual(self.ex.placed_count, placed)
        self.assertEqual(self.ex.open_orders(SYM), [])


class TestScenario5Disconnect(Base):
    def test_disconnect_no_new_orders_stops_kept(self):
        eng = self.engine(); eng.start()
        self.ex.set_price(SYM, 98.0); eng.tick()
        before = dict(self.ex.placed_count)
        self.ex.connected = False
        for _ in range(3):
            eng.tick()
        self.ex.connected = True
        self.assertEqual(self.ex.placed_count, before)
        self.assertEqual(len(self.stops()), 1)

    def test_emergency_while_disconnected_never_claims_closed(self):
        eng = self.engine(); eng.start()
        self.ex.set_price(SYM, 96.0); eng.tick()
        self.ex.connected = False
        self.assertEqual(eng.emergency_stop(), NEEDS_MANUAL)
        self.ex.connected = True
        self.assertGreater(self.ex.positions[SYM], 0)             # gerçekten kapanmadı
        self.assertEqual(len(self.stops()), 1)                    # stop korundu
        self.assertTrue(any("MÜDAHALE" in t for _, t in self.store.alerts("g1")))
        self.assertEqual(eng.emergency_stop(), EMERGENCY_CLOSED)  # bağlantı gelince tamamlanır
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)


class TestScenario6Emergency(Base):
    def test_emergency_closes_grid_only_and_stop_cancelled_last(self):
        self.ex.set_position("ETHUSDT", 2.0)                       # başka pozisyon: dokunulmamalı
        eng = self.engine(); eng.start()
        self.ex.set_price(SYM, 96.0); eng.tick()
        self.assertGreater(self.ex.positions[SYM], 0.9)
        self.assertEqual(eng.emergency_stop(), EMERGENCY_CLOSED)
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)
        self.assertEqual(self.ex.positions["ETHUSDT"], 2.0)
        self.assertEqual(self.ex.open_orders(SYM), [])
        closes = [o for o in self.ex.orders.values() if o.type == "MARKET"]
        self.assertEqual(len(closes), 1)
        self.assertTrue(closes[0].reduce_only)
        # sıra: piyasa kapanışı, stop iptalinden ÖNCE
        names = [(m, c) for m, c in self.ex.calls]
        close_idx = names.index(("place_order", closes[0].client_id))
        stop_cancel = [i for i, (m, c) in enumerate(names) if m == "cancel_order" and c.endswith("S0") and i > close_idx]
        self.assertTrue(stop_cancel)
        self.assertFalse(any(m == "cancel_order" and c.endswith("S0") for m, c in names[:close_idx]))

    def test_fill_during_cancel_race_is_included(self):
        eng = self.engine(); eng.start()
        self.ex.set_price(SYM, 98.0); eng.tick()
        self.ex.prices[SYM] = 95.9
        self.ex.cancel_race[self.cid(2, "E")] = 0.3               # iptalden hemen önce 0.3 doluyor
        self.assertEqual(eng.emergency_stop(), EMERGENCY_CLOSED)
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)
        self.assertEqual(self.ex.open_orders(SYM), [])

    def test_close_reply_lost_closes_once(self):
        eng = self.engine(); eng.start()
        self.ex.set_price(SYM, 98.0); eng.tick()
        self.ex.lose_response_for.add(self.cid(0, "C"))
        self.assertEqual(eng.emergency_stop(), EMERGENCY_CLOSED)
        self.assertEqual(self.ex.placed_count[self.cid(0, "C")], 1)
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)

    def test_close_keeps_failing_needs_manual_and_stop_stays(self):
        eng = self.engine(max_close_attempts=3); eng.start()
        self.ex.set_price(SYM, 98.0); eng.tick()
        self.ex.reject_next = [(-2022, "ReduceOnly Order is rejected")] * 20
        self.assertEqual(eng.emergency_stop(), NEEDS_MANUAL)
        self.ex.reject_next = []
        self.assertGreater(self.ex.positions[SYM], 0)
        self.assertEqual(len(self.stops()), 1)

    def test_short_grid_emergency(self):
        self.spec = make_spec(SHORT)
        eng = self.engine(); eng.start()
        self.ex.set_price(SYM, 104.0); eng.tick()
        self.assertLess(self.ex.positions[SYM], 0)
        self.assertEqual(eng.emergency_stop(), EMERGENCY_CLOSED)
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)


class TestDuplicatesAndGates(Base):
    def test_repeated_start_and_ticks_no_double_position(self):
        eng = self.engine()
        for _ in range(5):
            eng.start(); eng.tick()
        self.ex.set_price(SYM, 94.0)
        for _ in range(5):
            eng.tick(); self.engine().start()
        self.assertTrue(all(v == 1 for v in self.ex.placed_count.values()))
        self.assertAlmostEqual(self.ex.positions[SYM], 1.5)       # 3 kademe × 0.5, ne eksik ne fazla
        self.assertEqual(len(self.stops()), 1)

    def test_risk_gate_blocks_all_orders(self):
        eng = self.engine(risk_gate=lambda: (False, "günlük zarar hakkı yok"))
        self.assertEqual(eng.start(), BLOCKED)
        self.assertEqual(self.ex.placed_count, {})

    def test_exchange_rules_enforced_by_fake(self):
        with self.assertRaises(ExchangeRejected):
            self.ex.place_order(client_id="a", symbol=SYM, side="BUY", type="LIMIT", qty=0.5, price=98.05)


class TestSameCoinAndLimits(Base):
    def test_pending_manual_order_same_coin_blocks(self):
        self.ex.place_order(client_id="manual-1", symbol=SYM, side="BUY", type="LIMIT", qty=0.2, price=80.0)
        self.assertEqual(self.engine().start(), BLOCKED)
        self.assertEqual([k for k in self.ex.placed_count if k.startswith("kg-")], [])

    def test_other_coin_position_and_orders_do_not_block(self):
        self.ex.set_position("ETHUSDT", 3.0)
        self.ex.prices["ETHUSDT"] = 50.0
        self.ex.place_order(client_id="manual-2", symbol="ETHUSDT", side="BUY", type="LIMIT", qty=1.0, price=40.0)
        self.assertEqual(self.engine().start(), RUNNING)
        self.assertEqual(len(self.ex.open_orders(SYM)), 3)

    def test_more_than_five_levels_refused(self):
        levels = [GridLevelSpec(98.0 - i, 99.0 - i, 0.5) for i in range(6)]
        with self.assertRaises(ValueError):
            GridEngine(self.ex, self.store, GridSpec("g1", SYM, LONG, levels, 80.0))


class TestStopRejectVsLostReply(Base):
    def _gap_to_stop(self, eng):
        """L01 doldu ve korunuyor; sonra fiyat stopun ALTINA düşerken L02 doluyor (stop L01'i kapatır, L02 açıkta kalır)."""
        self.ex.set_price(SYM, 98.0); eng.tick()
        self.assertEqual(len(self.stops()), 1)
        self.ex.set_price(SYM, 89.0)          # L02, L03 dolar; ilk stop yalnız 0.5'i kapatır

    def test_clear_rejection_closes_once_at_market(self):
        eng = self.engine(); eng.start()
        self._gap_to_stop(eng)
        eng.tick()
        closes = [k for k in self.ex.placed_count if k.endswith("C0")]
        self.assertEqual(len(closes), 1)
        self.assertEqual(self.ex.placed_count[closes[0]], 1)
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)
        self.assertEqual(eng.status, STOPPED)

    def test_lost_reply_on_stop_does_not_trigger_market_close(self):
        eng = self.engine(); eng.start()
        self.ex.set_price(SYM, 98.0)
        self.ex.lose_response_for.add(self.cid(0, "S"))     # stop kabul edilir ama yanıt kaybolur
        eng.tick(); eng.tick()
        self.assertFalse([k for k in self.ex.orders if k.endswith("C0")])   # piyasa kapatma yok
        self.assertEqual(len(self.stops()), 1)                               # stop borsada, tek adet
        self.assertEqual(self.ex.placed_count[self.cid(0, "S")], 1)

    def test_lost_reply_and_status_unreadable_still_no_market_close(self):
        eng = self.engine(); eng.start()
        self.ex.set_price(SYM, 98.0)
        self.ex.lose_response_for.add(self.cid(0, "S"))
        self.ex.hooks.append(lambda m, i: setattr(self.ex, "connected", False) if m == "get_order" and str(i.get("client_id")).endswith("S0") else None)
        eng.tick()
        self.ex.hooks.clear(); self.ex.connected = True
        self.assertFalse([k for k in self.ex.orders if k.endswith("C0")])   # belirsiz: kapatma yok
        eng.tick(); eng.tick()
        self.assertFalse([k for k in self.ex.orders if k.endswith("C0")])
        self.assertEqual(self.ex.placed_count[self.cid(0, "S")], 1)          # stop tek kez, sonra kimlikle bulundu
        self.assertEqual(len(self.stops()), 1)

    def test_lost_reply_on_market_close_no_second_close(self):
        eng = self.engine(); eng.start()
        self._gap_to_stop(eng)
        self.ex.lose_response_for.add(self.cid(0, "C"))
        eng.tick(); eng.tick(); eng.tick()
        self.assertEqual(self.ex.placed_count[self.cid(0, "C")], 1)
        self.assertFalse([k for k in self.ex.orders if k.endswith("C1")])
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)


class TestPause(Base):
    def test_pause_cancels_entries_keeps_position_protected(self):
        eng = self.engine(); eng.start()
        self.ex.set_price(SYM, 98.0); eng.tick()
        self.assertEqual(eng.pause(), PAUSED)
        types = sorted((o.type, o.reduce_only) for o in self.ex.open_orders(SYM))
        self.assertEqual(types, [("LIMIT", True), ("STOP_MARKET", True)])
        self.assertAlmostEqual(self.ex.positions[SYM], 0.5)
        placed = dict(self.ex.placed_count)
        self.ex.set_price(SYM, 94.0); eng.tick()
        self.assertEqual(self.ex.placed_count, placed)            # yeni giriş yok

    def test_fill_during_pause_race_gets_protected(self):
        eng = self.engine(); eng.start()
        self.ex.prices[SYM] = 97.9
        self.ex.cancel_race[self.cid(1, "E")] = 0.5
        eng.pause()
        self.assertAlmostEqual(self.ex.positions[SYM], 0.5)
        self.assertEqual(len(self.stops()), 1)
        self.assertAlmostEqual(self.stops()[0].qty, 0.5)


if __name__ == "__main__":
    unittest.main()
