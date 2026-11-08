"""KAIS FARMER C3 — tek pozisyon yaşam döngüsü motoru testleri (sahte borsa, ağ yok).
Çalıştırma: cd backend && python -B -m unittest tests.test_farmer_engine -v"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.farmer_engine.engine import DEAD_CLOSE_MARK, FarmerEngine, farmer_spec, levels_from_avg  # noqa: E402
from app.farmer_engine.store import FarmerStore  # noqa: E402
from app.grid_engine.engine import (BLOCKED, COMPLETED, EMERGENCY_CLOSED, INTERVENTION_REQUIRED, RUNNING, STOPPED, GridEngine,  # noqa: E402
                                    GridLevelSpec, GridSpec)
from app.grid_engine.fake_exchange import FakeExchange  # noqa: E402
from app.grid_engine.ports import (EPS, LONG, MARKET, NEW, SHORT, STOP_MARKET, TAKE_PROFIT_MARKET,  # noqa: E402
                                   WOULD_TRIGGER_IMMEDIATELY, ExchangeRejected)
from app.grid_engine.store import GridStore  # noqa: E402

SYM = "ETHUSDT"


class Hooked(FakeExchange):
    """Test kancaları: belirli emir türünde fiyatı önce kaydır (gerçek 'zaten geçildi' reddi), market girişi kısmi doldur."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.before_place: dict[str, float] = {}
        self.entry_ratio = 1.0
        self.reject_type: dict[str, tuple[int, str]] = {}

    def place_order(self, *, client_id, symbol, side, type, qty, price=0.0, stop_price=0.0, reduce_only=False):
        if type in self.before_place:
            self.prices[symbol] = self.before_place.pop(type)
        if type in self.reject_type:
            code, msg = self.reject_type.pop(type)
            raise ExchangeRejected(code, msg)
        return super().place_order(client_id=client_id, symbol=symbol, side=side, type=type, qty=qty, price=price,
                                  stop_price=stop_price, reduce_only=reduce_only)

    def _fill(self, order, qty, price):
        if order.type == MARKET and not order.reduce_only and self.entry_ratio < 1.0:
            qty = round(order.qty * self.entry_ratio, 8)
        super()._fill(order, qty, price)


class Base(unittest.TestCase):
    side = LONG

    def make(self, price=100.0, mark=None, **spec_kw):
        self.ex = Hooked(tick=0.01, step=0.001)
        self.ex.prices[SYM] = mark if mark is not None else price
        self.store = FarmerStore()
        kw = dict(margin=50.0, leverage=3, target_usdt=1.0, max_loss_usdt=1.5, tick=0.01, step=0.001)
        kw.update(spec_kw)
        self.spec = farmer_spec("F1", SYM, self.side, price, **kw)
        self.eng = FarmerEngine(self.ex, self.store, self.spec)
        return self.eng

    def setUp(self):
        self.make()

    def live(self, type_):
        return [o for o in self.ex.orders.values() if o.type == type_ and o.status in (NEW, "PARTIALLY_FILLED")]


class TestSpecAndConstruction(Base):
    def test_numbers(self):
        self.assertEqual(self.spec.levels[0].qty, 1.5)                   # 50·3/100
        self.assertEqual(self.spec.meta["kind"], "FARMER")
        self.assertAlmostEqual(self.spec.meta["est_cost_usdt"], 0.2, places=6)

    def test_cost_eating_max_loss_is_rejected(self):
        with self.assertRaises(ValueError):
            farmer_spec("F9", SYM, LONG, 100.0, margin=50, leverage=3, target_usdt=1.0, max_loss_usdt=0.2, tick=0.01, step=0.001)

    def test_requires_farmer_store_and_single_farmer_level(self):
        with self.assertRaises(TypeError):
            FarmerEngine(self.ex, GridStore(), self.spec)
        two = GridSpec("F2", SYM, LONG, [GridLevelSpec(100, 101, 1), GridLevelSpec(99, 100, 1)], 98, meta={"kind": "FARMER"})
        with self.assertRaises(ValueError):
            FarmerEngine(self.ex, FarmerStore(), two)
        notfarmer = GridSpec("F3", SYM, LONG, [GridLevelSpec(100, 101, 1)], 98, meta={"kind": "DCA"})
        with self.assertRaises(ValueError):
            FarmerEngine(self.ex, FarmerStore(), notfarmer)

    def test_grid_default_min_levels_still_two(self):
        self.assertEqual(GridEngine.MIN_LEVELS, 2)
        with self.assertRaises(ValueError):
            GridEngine(self.ex, GridStore(), GridSpec("G1", SYM, LONG, [GridLevelSpec(100, 101, 1)], 98))

    def test_ids_are_kf_only(self):
        self.eng.start()
        self.assertTrue(self.ex.orders and all(cid.startswith("kf-") and len(cid) <= 36 for cid in self.ex.orders))


class TestProtectionOrder(Base):
    def test_entry_market_then_stop_then_tp(self):
        self.assertEqual(self.eng.start(), RUNNING)
        placed = [(cid) for (name, cid) in self.ex.calls if name == "place_order"]
        roles = [cid.split("-")[-1][3] for cid in placed]
        self.assertEqual(roles[:3], ["E", "S", "T"])
        self.assertEqual(self.ex.orders[placed[0]].type, MARKET)

    def test_exit_orders_reduce_only_full_qty_types(self):
        self.eng.start()
        stop, tp = self.live(STOP_MARKET)[0], self.live(TAKE_PROFIT_MARKET)[0]
        self.assertTrue(stop.reduce_only and tp.reduce_only)
        self.assertEqual((stop.qty, tp.qty), (1.5, 1.5))
        self.assertEqual(self.ex.positions[SYM], 1.5)

    def test_prices_from_actual_average_not_plan(self):
        self.make(price=100.0, mark=100.2)                                # piyasa girişi planlanandan kötü doldu
        self.eng.start()
        avg = self.eng.avg_entry()
        self.assertAlmostEqual(avg, 100.2)
        tp, stop = levels_from_avg(LONG, avg, 1.5, target_usdt=1.0, max_loss_usdt=1.5, tick=0.01, buffer_usdt=0.05)
        self.assertAlmostEqual(self.live(TAKE_PROFIT_MARKET)[0].stop_price, tp)
        self.assertAlmostEqual(self.live(STOP_MARKET)[0].stop_price, stop)
        self.assertNotAlmostEqual(stop, self.spec.meta["planned_entry"] - 1.0, places=1)
        self.assertAlmostEqual(self.store.grid("F1") and __import__("json").loads(self.store.grid("F1")["spec"])["stop_price"], stop)

    def test_quantities_follow_real_partial_fill(self):
        self.ex.entry_ratio = 0.4
        self.eng.start()
        filled = self.eng.grid_net()
        self.assertAlmostEqual(filled, 0.6)
        self.assertAlmostEqual(self.live(STOP_MARKET)[0].qty, filled)
        self.assertAlmostEqual(self.live(TAKE_PROFIT_MARKET)[0].qty, filled)

    def test_growth_of_position_replaces_stop_before_cancelling_old_and_resizes_tp(self):
        self.ex.entry_ratio = 0.4
        self.eng.start()
        first_stop = self.live(STOP_MARKET)[0].client_id
        entry = self.store.orders("F1", "E")[0]["client_id"]
        self.ex.orders[entry].filled = 1.5                                # kalan da doldu
        self.ex.positions[SYM] = 1.5
        self.eng.tick()
        stops = self.live(STOP_MARKET)
        self.assertEqual([s.qty for s in stops], [1.5])
        self.assertNotEqual(stops[0].client_id, first_stop)
        self.assertEqual([t.qty for t in self.live(TAKE_PROFIT_MARKET)], [1.5])

    def test_idempotent_ticks_do_not_duplicate_orders(self):
        self.eng.start()
        for _ in range(5):
            self.eng.tick()
        self.assertEqual((len(self.live(STOP_MARKET)), len(self.live(TAKE_PROFIT_MARKET))), (1, 1))
        self.assertTrue(all(n == 1 for n in self.ex.placed_count.values()))


class TestEvents(Base):
    def test_target_completes(self):
        self.eng.start()
        self.ex.set_price(SYM, self.spec.levels[0].target)
        self.eng.tick()
        self.assertEqual((self.eng.status, self.eng.close_reason()), (COMPLETED, "TARGET"))
        self.assertEqual(self.live(STOP_MARKET) + self.live(TAKE_PROFIT_MARKET), [])
        self.assertEqual(self.eng.verify_flat(), (True, "düz"))
        self.assertFalse(self.eng.estimate()["verified"])

    def test_stop_stops(self):
        self.eng.start()
        self.ex.set_price(SYM, self.spec.stop_price)
        self.eng.tick()
        self.assertEqual((self.eng.status, self.eng.close_reason()), (STOPPED, "STOP"))
        self.assertEqual(self.live(TAKE_PROFIT_MARKET), [])
        self.assertTrue(self.eng.verify_flat()[0])

    def test_estimate_separates_target_from_realized(self):
        self.ex.tp_slippage = 0.10
        self.eng.start()
        self.ex.set_price(SYM, self.spec.levels[0].target)
        self.eng.tick()
        est = self.eng.estimate()
        self.assertNotAlmostEqual(est["estimated_net_usdt"], est["target_usdt"], places=2)

    def test_entry_rejected_stops_without_position(self):
        self.ex.reject_next.append((-2019, "Margin is insufficient"))
        self.eng.start()
        self.assertEqual(self.eng.status, STOPPED)
        self.assertEqual(self.eng.close_reason(), "OPEN")
        self.assertEqual(self.ex.positions.get(SYM, 0), 0)

    def test_foreign_position_blocks_start(self):
        self.ex.set_position(SYM, 0.5)
        self.assertEqual(self.eng.start(), BLOCKED)
        self.assertEqual([c for c in self.ex.calls if c[0] == "place_order"], [])

    def test_lost_entry_response_never_sends_second_entry(self):
        cid = self.store.orders("F1", "E")[0]["client_id"]
        self.ex.lose_response_for.add(cid)
        self.eng.start()
        for _ in range(3):
            self.eng.tick()
        self.assertEqual(self.ex.placed_count[cid], 1)
        self.assertAlmostEqual(self.ex.positions[SYM], 1.5)


class TestWouldTrigger(Base):
    def test_tp_already_crossed_closes_at_market_with_reduce_only(self):
        self.ex.before_place[TAKE_PROFIT_MARKET] = self.spec.levels[0].target + 1.0
        self.eng.start()
        self.assertEqual(self.eng.status, COMPLETED)
        self.assertEqual(self.eng.close_reason(), "MARKET_CLOSE")
        self.assertTrue(self.eng.verify_flat()[0])
        self.assertEqual(self.ex.positions[SYM], 0)

    def test_tp_other_rejection_keeps_stop_and_stays_running(self):
        self.ex.reject_type[TAKE_PROFIT_MARKET] = (-1111, "precision")
        self.eng.start()
        self.assertEqual(self.eng.status, RUNNING)
        self.assertEqual(len(self.live(STOP_MARKET)), 1)
        self.assertEqual(self.live(TAKE_PROFIT_MARKET), [])
        self.assertAlmostEqual(self.ex.positions[SYM], 1.5)
        self.eng.tick()                                                   # sonraki turda hedef tekrar denenir
        self.assertEqual(len(self.live(TAKE_PROFIT_MARKET)), 1)

    def test_stop_already_crossed_force_closes(self):
        self.ex.before_place[STOP_MARKET] = self.spec.stop_price - 1.0
        self.eng.start()
        self.assertEqual(self.eng.status, STOPPED)
        self.assertEqual(self.eng.close_reason(), "MARKET_CLOSE")
        self.assertEqual(self.ex.positions[SYM], 0)
        self.assertEqual(self.live(STOP_MARKET) + self.live(TAKE_PROFIT_MARKET), [])

    def test_stop_hard_reject_requires_intervention_and_places_no_target(self):
        self.ex.reject_type[STOP_MARKET] = (-4509, "rejected")
        self.eng.start()
        self.assertEqual(self.eng.status, INTERVENTION_REQUIRED)
        self.assertEqual(self.live(TAKE_PROFIT_MARKET), [])               # stop kurulmadan hedef konmaz

    def test_slippage_that_leaves_no_stop_distance_force_closes(self):
        self.make(price=100.0, mark=100.0, max_loss_usdt=0.3)             # plan geçerli (0.3 > 0.2) ama gerçek maliyet artarsa...
        self.eng.spec.meta["buffer_usdt"] = 0.5                           # gerçek tampon maliyeti maks. zararın üstüne çıkarır
        self.eng.start()
        self.assertEqual(self.eng.status, STOPPED)
        self.assertEqual(self.ex.positions[SYM], 0)


class TestShort(Base):
    side = SHORT

    def test_short_target(self):
        self.eng.start()
        self.assertAlmostEqual(self.ex.positions[SYM], -1.5)
        self.assertGreater(self.live(STOP_MARKET)[0].stop_price, 100.0)
        self.assertLess(self.live(TAKE_PROFIT_MARKET)[0].stop_price, 100.0)
        self.ex.set_price(SYM, self.spec.levels[0].target)
        self.eng.tick()
        self.assertEqual((self.eng.status, self.eng.close_reason()), (COMPLETED, "TARGET"))

    def test_short_stop(self):
        self.eng.start()
        self.ex.set_price(SYM, self.spec.stop_price)
        self.eng.tick()
        self.assertEqual((self.eng.status, self.eng.close_reason()), (STOPPED, "STOP"))


class TestEmergencyAndRecovery(Base):
    def test_emergency_stop(self):
        self.eng.start()
        self.assertEqual(self.eng.emergency_stop(), EMERGENCY_CLOSED)
        self.assertEqual(self.eng.close_reason(), "EMERGENCY")
        self.assertEqual(self.ex.positions[SYM], 0)
        self.assertEqual(self.live(STOP_MARKET) + self.live(TAKE_PROFIT_MARKET), [])

    def test_recover_restores_missing_protection(self):
        self.eng.start()
        for o in self.live(STOP_MARKET) + self.live(TAKE_PROFIT_MARKET):
            self.ex.cancel_order(SYM, o.client_id)
        ok, why = self.eng.recover()
        self.assertTrue(ok, why)
        self.assertEqual((len(self.live(STOP_MARKET)), len(self.live(TAKE_PROFIT_MARKET))), (1, 1))

    def test_restart_with_new_instance_continues(self):
        self.eng.start()
        again = FarmerEngine(self.ex, self.store, self.spec)               # sunucu yeniden başladı: aynı defter
        ok, why = again.recover()
        self.assertTrue(ok, why)
        self.assertEqual(len(self.store.orders("F1", "E")), 1)
        self.assertTrue(all(n == 1 for n in self.ex.placed_count.values()))
        self.ex.set_price(SYM, self.spec.levels[0].target)
        again.tick()
        self.assertEqual(again.status, COMPLETED)

    def test_verify_flat_false_when_position_remains_and_when_unreadable(self):
        self.eng.start()
        ok, why = self.eng.verify_flat()
        self.assertFalse(ok)
        self.assertIn("pozisyon", why)
        self.ex.position = lambda symbol: (_ for _ in ()).throw(RuntimeError("x"))     # type: ignore[assignment]
        ok, why = self.eng.verify_flat()
        self.assertFalse(ok)
        self.assertIn("okunamadı", why)


def analysis(direction, momentum, confidence):
    return {"direction": direction, "momentum": momentum, "confidence": confidence}


class TestDead(Base):
    def go(self, ms, an, mark=None, **kw):
        kw.setdefault("dead_score", 75)
        kw.setdefault("safety_margin_usdt", 0.05)
        return self.eng.dead_check(an, ms, mark if mark is not None else self.ex.prices[SYM], **kw)

    def setUp(self):
        super().setUp()
        self.eng.start()
        self.bad_long = analysis("SHORT", "Negatif", 60)

    def test_adverse_definition_long(self):
        e = self.eng
        self.assertTrue(e.is_adverse(self.bad_long, 75))
        self.assertFalse(e.is_adverse(analysis("SHORT", "Negatif", 80), 75))          # skor yeterince yüksek
        self.assertFalse(e.is_adverse(analysis("BEKLE", "Negatif", 60), 75))
        self.assertFalse(e.is_adverse(analysis("SHORT", "Nötr", 60), 75))
        self.assertFalse(e.is_adverse(analysis("SHORT", "Pozitif", 60), 75))
        self.assertFalse(e.is_adverse({"direction": "SHORT", "momentum": "Negatif"}, 75))   # eksik veri ters sayılmaz

    def test_adverse_definition_short(self):
        self.make()
        short = farmer_spec("F5", SYM, SHORT, 100.0, margin=50, leverage=3, target_usdt=1, max_loss_usdt=1.5, tick=0.01, step=0.001)
        eng = FarmerEngine(self.ex, FarmerStore(), short)
        self.assertTrue(eng.is_adverse(analysis("LONG", "Pozitif", 60), 75))
        self.assertFalse(eng.is_adverse(analysis("SHORT", "Negatif", 60), 75))

    def test_disabled(self):
        self.assertEqual(self.go(1, self.bad_long, dead_score=None), "DISABLED")

    def test_two_closed_candles_required_and_same_candle_not_double_counted(self):
        self.assertEqual(self.go(100, self.bad_long, mark=100.5), "DEAD_WAIT")
        self.assertEqual(self.go(100, self.bad_long, mark=100.5), "DEAD_WAIT")         # aynı mum
        self.assertEqual(self.eng.status, RUNNING)
        self.assertEqual(self.go(200, self.bad_long, mark=100.5), "DEAD_CLOSED")

    def test_recovery_of_signal_resets_counter(self):
        self.go(100, self.bad_long, mark=100.5)
        self.assertEqual(self.go(200, analysis("LONG", "Pozitif", 90), mark=100.5), "NOT_DEAD")
        self.assertEqual(self.go(300, self.bad_long, mark=100.5), "DEAD_WAIT")

    def test_dead_in_loss_does_not_close(self):
        self.ex.prices[SYM] = 99.0
        self.go(100, self.bad_long)
        self.assertEqual(self.go(200, self.bad_long), "DEAD_LOSS_HOLD")
        self.assertEqual(self.eng.status, RUNNING)
        self.assertEqual(len(self.live(STOP_MARKET)), 1)
        self.assertAlmostEqual(self.ex.positions[SYM], 1.5)

    def test_dead_with_profit_below_safety_does_not_close(self):
        self.ex.prices[SYM] = 100.2                                                    # brüt 0.30, ücret ~0.15 → net ~0.15
        self.go(100, self.bad_long)
        self.assertEqual(self.go(200, self.bad_long, safety_margin_usdt=0.5), "DEAD_PROFIT_TOO_SMALL")
        self.assertEqual(self.eng.status, RUNNING)

    def test_dead_in_profit_closes_with_reduce_only_and_reason(self):
        self.ex.prices[SYM] = 100.5
        self.go(100, self.bad_long)
        self.assertEqual(self.go(200, self.bad_long), "DEAD_CLOSED")
        self.assertEqual((self.eng.status, self.eng.close_reason()), (COMPLETED, "DEAD_CLOSE"))
        self.assertEqual(self.eng.store.grid("F1")["halt_reason"], DEAD_CLOSE_MARK)
        self.assertTrue(self.eng.verify_flat()[0])
        self.assertEqual(self.live(STOP_MARKET) + self.live(TAKE_PROFIT_MARKET), [])
        closers = [o for o in self.store.orders("F1", "C")]
        self.assertEqual(len(closers), 1)
        self.assertTrue(closers[0]["reduce_only"])

    def test_dead_not_evaluated_when_not_running(self):
        self.eng.emergency_stop()
        self.assertEqual(self.go(100, self.bad_long), "NOT_OPEN")

    def test_target_cancel_failure_means_no_close(self):
        self.ex.prices[SYM] = 100.5
        self.go(100, self.bad_long)
        real = self.ex.cancel_order
        self.ex.cancel_order = lambda s, c: (_ for _ in ()).throw(ExchangeRejected(-1000, "x")) if c.endswith("T0") else real(s, c)  # type: ignore[assignment]
        self.assertEqual(self.go(200, self.bad_long), "DEAD_CLOSE_FAILED")
        self.assertAlmostEqual(self.ex.positions[SYM], 1.5)


if __name__ == "__main__":
    unittest.main()


class TestBranding(Base):
    def test_no_grid_or_dca_wording_in_alerts_or_reasons(self):
        import re
        from app.farmer_engine.engine import brand
        self.assertEqual(brand("DCA tamamlandı; grid durur. Grid'in"), "KAIS FARMER tamamlandı; KAIS FARMER durur. KAIS FARMER'in")
        scenarios = []
        for mutate in (
            lambda s: s.eng.emergency_stop(),
            lambda s: s.ex.before_place.__setitem__(STOP_MARKET, s.spec.stop_price - 1.0),
            lambda s: s.ex.set_position(SYM, 0.5),
            lambda s: s.ex.reject_next.append((-2019, "m")),
        ):
            self.make()
            mutate(self)
            self.eng.start()
            if self.eng.status == RUNNING:
                self.eng.emergency_stop()
            texts = [t for _, t in self.store.alerts("F1")] + [self.store.grid("F1")["halt_reason"]]
            scenarios.append(texts)
            for text in texts:
                self.assertIsNone(re.search(r"\b(grid|dca)\b", text, re.IGNORECASE), text)
        self.assertTrue(any(scenarios))
