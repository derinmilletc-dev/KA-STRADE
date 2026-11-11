"""DCA motoru — SAHTE borsa testleri. Gerçek anahtar/para/ağ yok.
Çalıştırma: python -m unittest tests.test_dca_engine_fake -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.dca_engine.engine import CYCLE_DONE, DcaEngine, dca_client_order_id, spec_from_plan  # noqa: E402
from app.dca_engine.plan import prepare_dca_plan  # noqa: E402
from app.grid_engine.engine import BLOCKED, COMPLETED, EMERGENCY_CLOSED, INTERVENTION_REQUIRED, PAUSED, RUNNING, STOPPED, GridEngine  # noqa: E402
from app.grid_engine.fake_exchange import FakeExchange  # noqa: E402
from app.grid_engine.ports import EPS, LONG, NEW, PARTIAL, SHORT, ExchangeRejected  # noqa: E402
from app.grid_engine.store import GridStore  # noqa: E402

SYM = "ETHUSDT"
RULES = {"tick": 0.01, "step": 0.001, "min_qty": 0.001, "min_notional": 5}
POLICY = {"max_leverage": 5, "max_loss_per_trade": 50, "daily_loss_limit": 100, "max_total_exposure_usdt": 5000, "max_positions": 5, "max_stop_distance_pct": 5}


def plan_for(direction=LONG, price=100.0, **kw):
    args = dict(direction=direction, price=price, base_margin_usdt=10.0, safety_orders=3, step_pct=1.5, volume_multiplier=1.5,
                take_profit_pct=1.0, stop_gap_pct=1.5, leverage=3, rules=RULES, policy=POLICY)
    args.update(kw)
    return prepare_dca_plan(**args)


class Base(unittest.TestCase):
    direction = LONG

    def setUp(self):
        self.ex = FakeExchange(tick=0.01, step=0.001)
        self.ex.prices[SYM] = 100.0
        self.store = GridStore()
        self.plan = plan_for(self.direction)
        self.spec = spec_from_plan("d1", SYM, self.plan, tick=0.01)

    def engine(self, spec=None, **kw):
        return DcaEngine(self.ex, self.store, spec or self.spec, **kw)

    def live(self, role=None):
        out = [o for o in self.ex.orders.values() if o.status in (NEW, PARTIAL)]
        letter = {"S": "STOP_MARKET"}
        if role == "S":
            return [o for o in out if o.type == "STOP_MARKET"]
        if role == "T":
            return [o for o in out if o.reduce_only and o.type == "LIMIT"]
        if role == "E":
            return [o for o in out if not o.reduce_only and o.type == "LIMIT"]
        return out

    def stage_price(self, i):
        return self.plan["stages"][i]["entry"]


class TestStart(Base):
    def test_base_market_then_safeties_stop_and_single_target(self):
        eng = self.engine()
        self.assertEqual(eng.start(), RUNNING)
        base_qty = self.plan["stages"][0]["qty"]
        self.assertAlmostEqual(self.ex.positions[SYM], base_qty)
        self.assertEqual(len(self.live("E")), 3)                       # 3 ek emir bekliyor
        (stop,) = self.live("S")
        self.assertEqual((stop.qty, stop.stop_price), (base_qty, self.plan["stop_price"]))
        (tp,) = self.live("T")
        self.assertEqual((tp.qty, tp.price), (base_qty, self.plan["stages"][0]["tp_price"]))
        self.assertTrue(all(o.client_id.startswith("kd-d1") for o in self.ex.orders.values()))
        self.assertFalse(any(o.client_id.startswith("kg-") for o in self.ex.orders.values()))

    def test_safeties_not_sent_until_base_filled(self):
        from app.grid_engine.ports import ExchangeUnreachable
        base_id = dca_client_order_id("d1", LONG, 1, "E", 0)
        down = {"on": True}

        def hook(method, info):
            if down["on"] and method == "place_order" and info.get("client_id") == base_id:
                raise ExchangeUnreachable("taban emir yolu kapalı")
        self.ex.hooks.append(hook)
        eng = self.engine()
        self.assertEqual(eng.start(), RUNNING)
        eng.tick()
        self.assertEqual(len(self.ex.orders), 0)                       # taban gitmedi: ek emir ve stop da yok
        down["on"] = False
        for _ in range(4):
            eng.tick()
        self.assertEqual(self.ex.placed_count[base_id], 1)
        self.assertEqual(len(self.live("E")), 3)
        self.assertEqual(len(self.live("S")), 1)

    def test_base_rejected_stops_without_safeties(self):
        self.ex.reject_next = [(-2019, "Margin is insufficient")]
        eng = self.engine()
        self.assertEqual(eng.start(), STOPPED)
        self.assertEqual(len(self.live()), 0)
        self.assertAlmostEqual(self.ex.positions.get(SYM, 0.0), 0.0)

    def test_foreign_position_blocks_start(self):
        self.ex.set_position(SYM, 1.0)
        self.assertEqual(self.engine().start(), BLOCKED)
        self.assertEqual(len(self.ex.orders), 0)


class TestSafetyFills(Base):
    def test_safety_fill_grows_stop_and_moves_target_to_new_average(self):
        eng = self.engine()
        eng.start()
        self.ex.set_price(SYM, self.stage_price(1))                    # 1. ek emir dolar
        eng.tick()
        net = self.plan["stages"][0]["qty"] + self.plan["stages"][1]["qty"]
        self.assertAlmostEqual(self.ex.positions[SYM], net)
        (stop,) = self.live("S")
        self.assertAlmostEqual(stop.qty, net)
        (tp,) = self.live("T")
        self.assertAlmostEqual(tp.qty, net)
        avg = (self.plan["stages"][0]["qty"] * 100.0 + self.plan["stages"][1]["qty"] * self.stage_price(1)) / net
        self.assertAlmostEqual(tp.price, avg * 1.01, delta=0.011)
        self.assertAlmostEqual(tp.price, self.plan["stages"][1]["tp_price"], delta=0.02)
        self.assertEqual(len(self.live("E")), 2)

    def test_never_unprotected_after_any_tick(self):
        eng = self.engine()
        eng.start()
        for i in (1, 2, 3):
            self.ex.set_price(SYM, self.stage_price(i))
            eng.tick()
            net = abs(self.ex.positions[SYM])
            self.assertAlmostEqual(sum(o.qty - o.filled for o in self.live("S")), net)
            self.assertAlmostEqual(sum(o.qty - o.filled for o in self.live("T")), net)

    def test_partial_safety_fill_is_covered(self):
        eng = self.engine()
        eng.start()
        cid = dca_client_order_id("d1", LONG, 2, "E", 0)
        self.ex.partial_fill(cid, 0.1)
        eng.tick()
        net = self.plan["stages"][0]["qty"] + 0.1
        self.assertAlmostEqual(sum(o.qty - o.filled for o in self.live("S")), net)
        self.assertAlmostEqual(sum(o.qty - o.filled for o in self.live("T")), net)


class TestExits(Base):
    def test_target_fill_completes_and_cleans_up(self):
        eng = self.engine()
        eng.start()
        self.ex.set_price(SYM, self.stage_price(1))
        eng.tick()
        tp = self.live("T")[0].price
        self.ex.set_price(SYM, tp)
        eng.tick()
        self.assertEqual(eng.status, COMPLETED)
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)
        self.assertEqual(len(self.live()), 0)
        stats = eng.stats()
        self.assertEqual(stats["completed_cycles"], 1)
        self.assertGreater(stats["realized_usdt"], 0)

    def test_stop_fill_stops_and_cleans_up(self):
        eng = self.engine()
        eng.start()
        for i in (1, 2, 3):
            self.ex.set_price(SYM, self.stage_price(i))
            eng.tick()
        self.ex.set_price(SYM, self.plan["stop_price"])
        eng.tick()
        self.assertEqual(eng.status, STOPPED)
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)
        self.assertEqual(len(self.live()), 0)
        self.assertLess(eng.stats()["realized_usdt"], 0)

    def test_partial_target_fill_keeps_target_matching_remaining(self):
        eng = self.engine()
        eng.start()
        tp = self.live("T")[0]
        self.ex.partial_fill(tp.client_id, 0.1)
        eng.tick()
        remaining = self.plan["stages"][0]["qty"] - 0.1
        self.assertAlmostEqual(sum(o.qty - o.filled for o in self.live("T")), remaining)
        self.assertAlmostEqual(sum(o.qty - o.filled for o in self.live("S")), remaining)
        self.assertEqual(eng.status, RUNNING)

    def test_price_already_past_stop_closes_at_market(self):
        eng = self.engine()
        eng.start()
        stop_id = dca_client_order_id("d1", LONG, 0, "S", 0)
        self.ex.cancel_order(SYM, stop_id)                          # stop kayboldu (ör. elle iptal)
        eng.store.set_order(stop_id, status="CANCELED")
        self.ex.prices[SYM] = self.plan["stop_price"] - 1            # fiyat stopu geçti: yeni stop reddedilir (-2021)
        eng.tick()
        self.assertEqual(eng.status, STOPPED)
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)
        self.assertEqual(len(self.live()), 0)


class TestRecycle(Base):
    def test_cycle_done_then_new_cycle(self):
        spec = spec_from_plan("d1", SYM, plan_for(recycle=True), tick=0.01)
        eng = self.engine(spec)
        eng.start()
        tp = self.live("T")[0].price
        self.ex.set_price(SYM, tp)
        eng.tick()
        self.assertEqual(eng.status, CYCLE_DONE)
        self.assertEqual(len(self.live()), 0)
        self.assertFalse(eng.begin_cycle(spec_from_plan("other", SYM, plan_for(price=tp), tick=0.01)))    # başka bot kimliği
        new_plan = plan_for(price=tp, recycle=True)
        self.assertTrue(eng.begin_cycle(spec_from_plan("d1", SYM, new_plan, tick=0.01)))
        eng.tick()
        self.assertEqual(eng.status, RUNNING)
        self.assertAlmostEqual(self.ex.positions[SYM], new_plan["stages"][0]["qty"])
        self.assertEqual(len(self.live("E")), 3)
        self.assertEqual(len(self.live("S")), 1)
        ids = list(self.ex.orders)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(eng.stats()["completed_cycles"], 1)

    def test_begin_cycle_refuses_when_position_left(self):
        spec = spec_from_plan("d1", SYM, plan_for(recycle=True), tick=0.01)
        eng = self.engine(spec)
        eng.start()
        self.ex.set_price(SYM, self.live("T")[0].price)
        eng.tick()
        self.ex.set_position(SYM, 0.5)
        self.assertFalse(eng.begin_cycle(spec_from_plan("d1", SYM, plan_for(recycle=True), tick=0.01)))


class TestResilience(Base):
    def test_lost_response_on_base_no_duplicate(self):
        base_id = dca_client_order_id("d1", LONG, 1, "E", 0)
        self.ex.lose_response_for.add(base_id)
        eng = self.engine()
        eng.start()
        eng.tick()
        self.assertEqual(self.ex.placed_count[base_id], 1)
        self.assertAlmostEqual(self.ex.positions[SYM], self.plan["stages"][0]["qty"])
        self.assertEqual(len(self.live("E")), 3)

    def test_connection_loss_sends_nothing_and_recovers(self):
        eng = self.engine()
        eng.start()
        placed = len(self.ex.calls)
        self.ex.connected = False
        eng.tick()
        self.assertFalse(any(c[0] == "place_order" for c in self.ex.calls[placed:]))
        self.ex.connected = True
        eng.tick()
        self.assertEqual(eng.status, RUNNING)

    def test_restart_recovers_without_duplicates(self):
        eng = self.engine()
        eng.start()
        self.ex.set_price(SYM, self.stage_price(1))
        eng.tick()
        before = dict(self.ex.placed_count)
        fresh = self.engine()                                      # sunucu yeniden başladı: aynı defter, yeni motor
        ok, why = fresh.recover()
        self.assertTrue(ok, why)
        self.assertEqual(self.ex.placed_count, before)

    def test_restart_after_missed_fill_sets_protection(self):
        eng = self.engine()
        eng.start()
        self.ex.set_price(SYM, self.stage_price(1))                # dolum, sunucu kapalıyken oldu; stop/hedef eski miktarda
        fresh = self.engine()
        ok, why = fresh.recover()
        self.assertTrue(ok, why)
        net = abs(self.ex.positions[SYM])
        self.assertAlmostEqual(sum(o.qty - o.filled for o in self.live("S")), net)

    def test_position_mismatch_blocks(self):
        eng = self.engine()
        eng.start()
        self.ex.set_position(SYM, self.ex.positions[SYM] + 1.0)
        eng.tick()
        self.assertEqual(eng.status, BLOCKED)


class TestStopControls(Base):
    def test_pause_cancels_safeties_keeps_protection(self):
        eng = self.engine()
        eng.start()
        eng.pause()
        self.assertEqual(eng.status, PAUSED)
        self.assertEqual(len(self.live("E")), 0)
        self.assertEqual(len(self.live("S")), 1)
        self.assertEqual(len(self.live("T")), 1)

    def test_reopen_only_safeties_never_base(self):
        eng = self.engine()
        eng.start()
        eng.pause()
        opened, skipped = eng.reopen_entries(100.0)
        self.assertEqual((opened, skipped), (3, 0))
        eng.tick()
        self.assertEqual(self.ex.placed_count[dca_client_order_id("d1", LONG, 1, "E", 0)], 1)   # taban tekrar gönderilmedi
        self.assertEqual(len(self.live("E")), 3)

    def test_emergency_stop_closes_and_verifies(self):
        eng = self.engine()
        eng.start()
        self.ex.set_price(SYM, self.stage_price(1))
        eng.tick()
        self.assertEqual(eng.emergency_stop(), EMERGENCY_CLOSED)
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)
        self.assertEqual(len(self.live()), 0)

    def test_emergency_unverifiable_keeps_stops(self):
        eng = self.engine()
        eng.start()
        eng.max_close_attempts = 1
        self.ex.hooks.append(lambda method, info: (_ for _ in ()).throw(ExchangeRejected(-1, "ret")) if method == "place_order" else None)
        self.assertEqual(eng.emergency_stop(), INTERVENTION_REQUIRED)
        self.assertEqual(len(self.live("S")), 1)


class TestShort(Base):
    direction = SHORT

    def test_short_full_flow(self):
        eng = self.engine()
        eng.start()
        self.assertLess(self.ex.positions[SYM], 0)
        self.ex.set_price(SYM, self.stage_price(1))
        eng.tick()
        net = abs(self.ex.positions[SYM])
        self.assertAlmostEqual(sum(o.qty - o.filled for o in self.live("S")), net)
        (tp,) = self.live("T")
        self.assertLess(tp.price, self.stage_price(0))
        self.ex.set_price(SYM, tp.price)
        eng.tick()
        self.assertEqual(eng.status, COMPLETED)
        self.assertGreater(eng.stats()["realized_usdt"], 0)


class TestBaseGridUntouched(unittest.TestCase):
    def test_grid_engine_ids_still_kg(self):
        from app.grid_engine.engine import client_order_id
        self.assertTrue(client_order_id("g1", LONG, 1, "E").startswith("kg-"))
        self.assertTrue(dca_client_order_id("g1", LONG, 1, "E").startswith("kd-"))
        self.assertIsNot(GridEngine, DcaEngine)


if __name__ == "__main__":
    unittest.main()


class TestManualClose(Base):
    def test_position_closed_by_hand_stops_bot_and_cancels_orders(self):
        eng = self.engine()
        eng.start()
        for order in list(self.ex.orders.values()):
            pass
        self.ex.positions[SYM] = 0.0                                   # kullanıcı pozisyonu başka ekrandan kapattı
        eng.tick()
        self.assertEqual(eng.status, RUNNING)                          # tek seferlik görünüşte hemen karar verilmez
        eng.tick()
        self.assertEqual(eng.status, RUNNING)
        eng.tick()
        self.assertEqual(eng.status, STOPPED)
        self.assertEqual(self.live(), [])                              # bu botun bekleyen emirleri iptal edildi

    def test_partial_difference_still_halts(self):
        eng = self.engine()
        eng.start()
        self.ex.positions[SYM] = self.ex.positions[SYM] * 0.5          # yarısı kapandı: baseline'a dönmedi
        for _ in range(4):
            eng.tick()
        self.assertIn(eng.status, (BLOCKED, INTERVENTION_REQUIRED))
