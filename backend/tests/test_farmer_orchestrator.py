"""KAIS FARMER C7 — orkestratör testleri (sahte borsa + gerçek FarmerFeed/Store; ağ yok)."""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.farmer_engine import confirm as c  # noqa: E402
from app.farmer_engine import orchestrator as o  # noqa: E402
from app.farmer_engine.engine import FarmerEngine, farmer_spec  # noqa: E402
from app.farmer_engine.feed import FarmerFeed  # noqa: E402
from app.farmer_engine.store import FarmerStore  # noqa: E402
from app.grid_engine.engine import GridLevelSpec, GridSpec  # noqa: E402
from app.grid_engine.fake_exchange import FakeExchange  # noqa: E402
from app.grid_engine.ports import EPS, NEW, PARTIAL, ExchangeRejected, ExchangeTimeout, ExchangeUnreachable  # noqa: E402
from app.farmer_engine.feed import INTERVALS  # noqa: E402
from tests.test_farmer_feed import NOW, STEP, Evaluator, FakeStream  # noqa: E402

ACC = "u1:live"
SYMS = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]


class CountingExchange(FakeExchange):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.reads = {"position": 0, "open_orders": 0, "get_order": 0}
        self.reject_market_once = False
        self.placed = []
        self.placed_ro = []
        self.cancelled = []
        self.fail_reads = False
        self.drop_entry_before = False          # giriş emri borsaya ULAŞMADAN yanıt kaybı (zaman aşımı)

    def position(self, symbol):
        self.reads["position"] += 1
        if self.fail_reads:
            raise ExchangeUnreachable("down")
        return super().position(symbol)

    def open_orders(self, symbol):
        self.reads["open_orders"] += 1
        if self.fail_reads:
            raise ExchangeUnreachable("down")
        return super().open_orders(symbol)

    def get_order(self, symbol, client_id):
        self.reads["get_order"] += 1
        if self.fail_reads:
            raise ExchangeUnreachable("down")
        return super().get_order(symbol, client_id)

    def cancel_order(self, symbol, client_id):
        self.cancelled.append(client_id)
        return super().cancel_order(symbol, client_id)

    def place_order(self, **kw):
        self.placed.append((kw["symbol"], kw["type"], kw["client_id"]))
        self.placed_ro.append((kw["client_id"], kw["type"], bool(kw.get("reduce_only"))))
        if self.drop_entry_before and kw["type"] == "MARKET" and not kw.get("reduce_only"):
            self.drop_entry_before = False
            raise ExchangeTimeout("lost")
        if self.reject_market_once and kw["type"] == "MARKET" and not kw.get("reduce_only"):
            self.reject_market_once = False
            raise ExchangeRejected(-1013, "temporary")
        return super().place_order(**kw)


class Source:
    def __init__(self, ex, clock):
        self.ex, self.clock, self.reads, self.fail = ex, clock, 0, False
        self.marks = None

    def read(self):
        self.reads += 1
        if self.fail:
            raise RuntimeError("down")
        orders = [oi for sym in list(self.ex.prices) for oi in FakeExchange.open_orders(self.ex, sym)]
        return o.ExchangeSnapshot(True, self.clock(), dict(self.ex.positions), orders, dict(self.marks if self.marks is not None else self.ex.prices))


class Gates:
    def __init__(self):
        self.arm, self.safety, self.foreign = (), (), {}

    def entry_gates(self):
        return o.GateState(self.arm, self.safety)

    def foreign_occupied(self, symbol):
        return tuple(self.foreign.get(symbol, ()))


class Reservations:
    def __init__(self):
        self.state, self.deny, self.orphan, self.client_ids = {}, False, [], {}

    def reserve(self, symbol, gid):
        if self.deny:
            return False
        self.state[gid] = "RESERVED"
        return True

    def mark_submitted(self, gid, client_order_id):
        self.state[gid] = "SUBMITTED"
        self.client_ids[gid] = client_order_id

    def consume(self, gid):
        self.state[gid] = "CONSUMED"

    def release(self, gid):
        self.state[gid] = "RELEASED"

    def orphans(self):
        return list(self.orphan)


class Universe:
    def __init__(self, symbols):
        self.items = list(symbols)

    def symbols(self):
        return list(self.items)


def spec_from_row(row):
    s = json.loads(row["spec"])
    return GridSpec(row["grid_id"], row["symbol"], row["side"], [GridLevelSpec(**lv) for lv in s["levels"]], float(s["stop_price"]),
                    recycle=False, meta=dict(s.get("meta") or {}))


class Factory:
    def __init__(self, store, settings):
        self.store, self.settings, self.fail, self.created = store, settings, False, []

    def create(self, gid, item, params, exchange):
        if self.fail:
            raise RuntimeError("prepare")
        self.created.append(gid)
        spec = farmer_spec(gid, item.symbol, item.direction, item.entry_price, margin=params.margin_usdt, leverage=params.leverage,
                           target_usdt=params.target_usdt, max_loss_usdt=params.max_loss_usdt, tick=0.01, step=0.001)
        return FarmerEngine(exchange, self.store, spec)

    def rebuild(self, row, exchange):
        return FarmerEngine(exchange, self.store, spec_from_row(row))


class PnlBoom:
    def verify(self, result):
        raise RuntimeError("funding unreadable")


def make_params(**kw):
    base = dict(variant=c.CONFIDENCE, min_score=92, margin_usdt=50.0, leverage=3.0, target_usdt=1.0, max_loss_usdt=1.5)
    base.update(kw)
    return c.ConfirmParams(**base)


class Rig:
    def __init__(self, symbols=SYMS, table=None, dead_score=None, store=None, ex=None, **settings_kw):
        self.now = NOW
        self.clock = lambda: self.now / 1000
        self.ex = ex or CountingExchange(tick=0.01, step=0.001)
        for s in symbols + ["FOREIGNUSDT"]:
            self.ex.prices.setdefault(s, 100.0)
        self.store = store or FarmerStore()
        self.stream = FakeStream(symbols)
        self.ev = Evaluator(table)
        self.feed = FarmerFeed(self.stream, evaluator=self.ev, clock=self.clock)
        self.settings = o.FarmerSettings(make_params(), dead_score=dead_score, **settings_kw)
        self.source, self.gates, self.res, self.uni = Source(self.ex, self.clock), Gates(), Reservations(), Universe(symbols)
        self.factory = Factory(self.store, self.settings)
        self.demands = []
        self.orch = self.build()

    def build(self, **kw):
        def set_demand(symbols):
            self.demands.append(list(symbols))
            self.feed._demand = list(symbols)
        orch = o.Orchestrator(account_key=ACC, store=self.store, settings=self.settings, exchange=self.ex, snapshot_source=self.source, feed=self.feed,
                              gates=self.gates, reservations=self.res, universe=self.uni, factory=self.factory, set_demand=set_demand,
                              clock=self.clock, **kw)
        return orch

    def run(self, recover=True):
        if recover:
            self.orch.recover()
        self.orch.start()
        return self.orch.tick(self.now)

    def tick(self, advance_ms=1000):
        self.now += advance_ms
        return self.orch.tick(self.now)

    def next_candle(self):
        self.now = (self.now // STEP["15m"] + 1) * STEP["15m"] + 5000
        self.stream.advance(self.now)
        return self.orch.tick(self.now)

    def by_symbol(self, symbol):
        return next(e for e in self.orch.engines.values() if e.sym == symbol)


class TestRefill(unittest.TestCase):
    def test_capacity_two_opens_two_of_three_confirmed(self):
        r = Rig()
        out = r.run()
        self.assertEqual(out["opened"], ["AAAUSDT", "BBBUSDT"])
        self.assertEqual(r.orch.guard.capacity, 2)
        self.assertEqual(r.orch.guard.slots().as_dict(), {"capacity": 2, "held": 0, "open": 2, "closing": 0, "free": 0})
        self.assertAlmostEqual(r.store.held_margin(ACC), 100.0)

    def test_max_positions_five_is_not_open_five(self):
        r = Rig(max_positions=5)
        r.run()
        self.assertEqual(len(r.orch.engines), 2)

    def test_only_one_passes_confirmation_opens_one(self):
        r = Rig(table={"BBBUSDT": {"confidence": 50}, "CCCUSDT": {"confidence": 50}})
        out = r.run()
        self.assertEqual(out["opened"], ["AAAUSDT"])
        self.assertEqual(r.orch.guard.slots().free, 1)             # düşük kaliteli adayla ikinci slot ZORLANMAZ

    def test_freed_slot_refills_from_cache_without_rescan(self):
        r = Rig()
        r.run()
        evals = r.ev.calls[:]
        engine = r.by_symbol("AAAUSDT")
        r.ex.set_price("AAAUSDT", engine.tp_price() + 0.5)         # hedef tetiklenir
        out = r.tick()
        self.assertEqual(out["opened"], ["CCCUSDT"])               # yalnız boşalan slot
        self.assertEqual(r.ev.calls, evals)                        # tam yeniden tarama YOK
        self.assertEqual(r.feed.stream_reads, len(INTERVALS) * 3)               # yalnız ilk taramanın okumaları
        res = r.store.results(ACC)
        self.assertEqual([x["close_reason"] for x in res if x["symbol"] == "AAAUSDT"], ["TARGET"])

    def test_new_candle_refreshes_cache(self):
        r = Rig()
        r.run()
        n = len(r.ev.calls)
        r.next_candle()
        self.assertGreater(len(r.ev.calls), n)

    def test_permanent_reject_not_reconfirmed_each_tick(self):
        r = Rig(table={"BBBUSDT": {"confidence": 50}, "CCCUSDT": {"confidence": 50}})
        r.run()
        rows = r.store.candidates(ACC, status="REJECTED")
        r.tick()
        r.tick()
        self.assertEqual(len(r.store.candidates(ACC, status="REJECTED")), len(rows))


class TestEntryGuards(unittest.TestCase):
    def test_reservation_denied_sends_no_order_and_no_retry(self):
        r = Rig()
        r.res.deny = True
        out = r.run()
        self.assertEqual(out["opened"], [])
        self.assertEqual(r.ex.placed, [])
        self.assertEqual(r.orch.guard.slots().used, 0)
        r.tick()
        self.assertEqual(r.ex.placed, [])
        self.assertEqual(r.store.attempt(ACC, "AAAUSDT", "15m", r.feed.last_closed_open_ms(NOW))["grid_id"][:1], "F")

    def test_submitted_reservation_carries_entry_client_id_before_order(self):
        r = Rig(table={"BBBUSDT": {"confidence": 50}, "CCCUSDT": {"confidence": 50}})
        r.run()
        gid = next(iter(r.res.client_ids))
        self.assertRegex(r.res.client_ids[gid], r"^kf-[A-Za-z0-9]+-L01E0$")
        self.assertEqual(r.res.state[gid], "CONSUMED")

    def test_prepare_failure_sends_no_order(self):
        r = Rig()
        r.factory.fail = True
        r.run()
        self.assertEqual(r.ex.placed, [])
        self.assertEqual(r.orch.guard.slots().used, 0)

    def test_foreign_occupied_symbol_skipped(self):
        r = Rig()
        r.gates.foreign["AAAUSDT"] = ("ORIGINAL_V2",)
        out = r.run()
        self.assertEqual(out["opened"], ["BBBUSDT", "CCCUSDT"])

    def test_exchange_position_on_symbol_skipped(self):
        r = Rig()
        r.ex.set_position("AAAUSDT", 1.0)
        out = r.run()
        self.assertNotIn("AAAUSDT", out["opened"])

    def test_temp_exchange_rejection_no_retry_same_candle(self):
        r = Rig(table={"BBBUSDT": {"confidence": 50}, "CCCUSDT": {"confidence": 50}})
        r.ex.reject_market_once = True
        r.run()
        self.assertEqual(r.orch.guard.slots().used, 0)
        self.assertEqual(r.res.state[next(iter(r.res.state))], "RELEASED")
        first = list(r.ex.placed)
        r.tick()
        r.tick()
        self.assertEqual(r.ex.placed, first)                        # aynı mumda ikinci gerçek deneme yok
        r.next_candle()
        self.assertEqual(len(r.orch.engines), 1)                    # yeni mumda yeniden denenir

    def test_snapshot_unreadable_blocks_entry(self):
        r = Rig()
        r.orch.recover()
        r.orch.start()
        r.source.fail = True
        r.orch.tick(r.now)
        self.assertEqual(r.orch.state, o.ENTRY_PAUSED_SAFETY)
        self.assertEqual(r.ex.placed, [])

    def test_daily_loss_from_existing_ledger_port(self):
        r = Rig()
        r.orch = r.build(daily_loss=lambda: ("DAILY_LOSS_LIMIT",))
        r.run()
        self.assertEqual(r.orch.state, o.ENTRY_PAUSED_SAFETY)
        self.assertEqual(r.ex.placed, [])


class TestStates(unittest.TestCase):
    def test_arm_expiry_stops_entry_keeps_protection_then_resumes(self):
        r = Rig(table={"CCCUSDT": {"confidence": 50}})
        r.run()
        self.assertEqual(len(r.orch.engines), 2)
        r.gates.arm = ("ARM_EXPIRED",)
        self.assertEqual(r.orch.tick(r.now + 1000)["state"], o.ENTRY_PAUSED_ARM)
        engine = r.by_symbol("AAAUSDT")
        stop = engine.stop_target_price()
        r.ex.set_price("AAAUSDT", stop - 0.5)                       # ARM yokken stop tetiklenir: koruma sürüyor
        r.tick()
        self.assertEqual([x["close_reason"] for x in r.store.results(ACC) if x["symbol"] == "AAAUSDT"], ["STOP"])
        placed = len(r.ex.placed)
        r.tick()
        self.assertEqual(len(r.ex.placed), placed)                  # ARM yokken yeni giriş yok
        r.gates.arm = ()
        r.table = None
        r.ev.table["CCCUSDT"] = {}
        r.next_candle()
        self.assertEqual(r.orch.state, o.RUNNING)
        self.assertGreater(len(r.ex.placed), placed)                # ARM geçerli: otomatik devam

    def test_off_does_nothing(self):
        r = Rig()
        r.orch.recover()
        reads = r.source.reads
        out = r.orch.tick(r.now)
        self.assertEqual(out, {"state": o.OFF, "idle": True})
        self.assertEqual((r.source.reads - reads, r.feed.stream_reads, r.ex.placed, r.demands), (0, 0, [], []))

    def test_start_requires_recovery(self):
        r = Rig()
        self.assertEqual(r.orch.start(), o.OFF)

    def test_stop_bot_keeps_positions_and_protection(self):
        r = Rig()
        r.run()
        self.assertEqual(r.orch.stop_bot(), o.STOPPING)
        self.assertEqual(len(r.orch.engines), 2)
        self.assertTrue(all(abs(r.ex.position(s)) > EPS for s in ("AAAUSDT", "BBBUSDT")))
        placed = len(r.ex.placed)
        r.tick()
        self.assertEqual(len(r.ex.placed), placed)
        for sym in ("AAAUSDT", "BBBUSDT"):
            r.ex.set_price(sym, r.by_symbol(sym).tp_price() + 0.5)
        r.tick()
        r.tick()
        self.assertEqual(r.orch.state, o.OFF)
        self.assertEqual(r.orch.guard.slots().used, 0)

    def test_stop_with_no_positions_goes_off(self):
        r = Rig()
        r.orch.recover()
        r.orch.start()
        self.assertEqual(r.orch.stop_bot(), o.OFF)

    def test_state_persisted(self):
        r = Rig()
        r.run()
        self.assertEqual(r.store.bot(ACC)["state"], o.RUNNING)


class TestClose(unittest.TestCase):
    def test_pnl_does_not_block_slot_release(self):
        r = Rig()
        r.run()
        r.ex.set_price("AAAUSDT", r.by_symbol("AAAUSDT").tp_price() + 0.5)
        r.tick()
        res = [x for x in r.store.results(ACC) if x["symbol"] == "AAAUSDT"][0]
        self.assertEqual(res["pnl_state"], "UNVERIFIED")
        self.assertEqual(res["verify_error"], "PNL_VERIFIER_NOT_CONFIGURED")
        self.assertEqual(r.store.holds(ACC, active_only=False)[0]["state"], "RELEASED")

    def test_pnl_verifier_exception_leaves_unverified_but_slot_free(self):
        r = Rig()
        r.orch = r.build(pnl=PnlBoom())
        r.run()
        r.ex.set_price("AAAUSDT", r.by_symbol("AAAUSDT").tp_price() + 0.5)
        r.tick()
        res = [x for x in r.store.results(ACC) if x["symbol"] == "AAAUSDT"][0]
        self.assertEqual((res["pnl_state"], res["verify_error"]), ("UNVERIFIED", "VERIFY_ERROR:RuntimeError"))

    def test_not_flat_keeps_slot(self):
        r = Rig()
        r.run()
        engine = r.by_symbol("AAAUSDT")
        r.ex.set_price("AAAUSDT", engine.tp_price() + 0.5)
        engine.verify_flat = lambda: (False, "test")
        r.orch.tick(r.now + 1000)
        self.assertEqual(r.orch.guard.slots().closing, 1)

    def test_emergency_close_only_farmer(self):
        r = Rig()
        r.run()
        r.ex.set_position("FOREIGNUSDT", 2.0)
        rep = r.orch.emergency_close()
        self.assertEqual((len(rep["closed"]), rep["failed"]), (2, []))
        self.assertEqual(r.ex.position("FOREIGNUSDT"), 2.0)
        self.assertEqual(r.ex.position("AAAUSDT"), 0.0)
        self.assertFalse([x for x in r.ex.orders.values() if x.status in (NEW, PARTIAL) and x.client_id.startswith("kf-")])
        self.assertEqual(r.orch.state, o.OFF)
        self.assertTrue(all(x["close_reason"] == "EMERGENCY" for x in r.store.results(ACC)))
        self.assertEqual(r.orch.guard.slots().used, 0)

    def test_emergency_not_flat_is_intervention(self):
        r = Rig()
        r.run()
        r.by_symbol("AAAUSDT").verify_flat = lambda: (False, "test")
        rep = r.orch.emergency_close()
        self.assertEqual(len(rep["failed"]), 1)
        self.assertEqual(r.orch.state, o.INTERVENTION)
        self.assertEqual(r.orch.start(), o.INTERVENTION)


class TestDemandAndDead(unittest.TestCase):
    def test_held_symbol_stays_in_demand_when_dropped_from_universe(self):
        r = Rig()
        r.run()
        r.uni.items = ["CCCUSDT"]
        r.next_candle()
        self.assertIn("AAAUSDT", r.demands[-1])
        self.assertIn("BBBUSDT", r.demands[-1])
        self.assertEqual(r.demands[-1][:2], ["AAAUSDT", "BBBUSDT"])

    def test_dead_check_prefers_mark_price(self):
        r = Rig(dead_score=60, table={"CCCUSDT": {"confidence": 50}})
        r.run()
        r.ev.table["AAAUSDT"] = {"direction": "SHORT", "momentum": "Negatif", "confidence": 40}
        r.source.marks = {"AAAUSDT": 100.2, "BBBUSDT": 100.0}
        r.next_candle()
        info = r.orch.dead_info[next(g for g, e in r.orch.engines.items() if e.sym == "AAAUSDT")]
        self.assertEqual(info["source"], "MARK")
        self.assertEqual(info["result"], "DEAD_WAIT")

    def test_dead_check_falls_back_to_kline_last(self):
        r = Rig(dead_score=60, table={"CCCUSDT": {"confidence": 50}})
        r.run()
        r.ev.table["AAAUSDT"] = {"direction": "SHORT", "momentum": "Negatif", "confidence": 40}
        r.source.marks = {}
        r.next_candle()
        info = r.orch.dead_info[next(g for g, e in r.orch.engines.items() if e.sym == "AAAUSDT")]
        self.assertEqual(info["source"], "KLINE_LAST")

    def test_dead_check_once_per_closed_candle(self):
        r = Rig(dead_score=60, table={"CCCUSDT": {"confidence": 50}})
        r.run()
        r.ev.table["AAAUSDT"] = {"direction": "SHORT", "momentum": "Negatif", "confidence": 40}
        r.next_candle()
        gid = next(g for g, e in r.orch.engines.items() if e.sym == "AAAUSDT")
        r.tick()
        r.tick()
        self.assertEqual(r.store.dead_count(gid, 0, False), 1)


class TestSharedReads(unittest.TestCase):
    def test_one_snapshot_per_tick_and_engines_do_not_poll(self):
        r = Rig()
        r.run()
        r.tick()                                                    # oturmuş durum
        for k in r.ex.reads:
            r.ex.reads[k] = 0
        reads = r.source.reads
        r.tick()
        self.assertEqual(r.source.reads, reads + 1)
        # Source.read() kendisi FakeExchange.open_orders'ı çağırır (3+1 sembol); motorların KENDİ okuması sıfır olmalı: sayaç yalnız Source'tan
        self.assertLessEqual(r.ex.reads["position"], 0)
        self.assertGreater(r.orch.shared.served, 0)

    def test_write_invalidates_symbol_snapshot(self):
        r = Rig()
        r.run()
        r.orch.shared.use(r.source.read())
        r.orch.shared.place_order(client_id="kf-x-L01E9", symbol="AAAUSDT", side="BUY", type="LIMIT", qty=10.0, price=1.0)
        before = r.orch.shared.passthrough
        r.orch.shared.open_orders("AAAUSDT")
        self.assertEqual(r.orch.shared.passthrough, before + 1)

    def test_stale_snapshot_falls_back(self):
        r = Rig()
        r.run()
        r.orch.shared.use(o.ExchangeSnapshot(True, r.clock() - 100))
        before = r.orch.shared.passthrough
        r.orch.shared.position("AAAUSDT")
        self.assertEqual(r.orch.shared.passthrough, before + 1)


class TestRecovery(unittest.TestCase):
    def test_restart_rebuilds_positions_from_ledger_and_exchange(self):
        r = Rig()
        r.run()
        r2 = Rig(store=r.store, ex=r.ex)
        rep = r2.orch.recover()
        self.assertEqual(len(rep["rebuilt"]), 2)
        self.assertTrue(r2.orch.recovered)
        self.assertEqual(r2.orch.state, o.RUNNING)                   # kayıtlı RUNNING geri gelir
        self.assertEqual(r2.orch.guard.slots().open, 2)
        r2.ex.set_price("AAAUSDT", r2.by_symbol("AAAUSDT").tp_price() + 0.5)
        r2.orch.tick(r2.now + 1000)
        self.assertEqual([x["close_reason"] for x in r2.store.results(ACC) if x["symbol"] == "AAAUSDT"], ["TARGET"])

    def test_held_without_ledger_row_released_no_order(self):
        r = Rig()
        r.store.claim_entry(ACC, "AAAUSDT", "15m", 1, "Gorphan", 50.0, cap_usdt=100.0)
        rep = r.orch.recover()
        self.assertEqual(rep["released"], ["Gorphan"])
        self.assertEqual(r.store.attempt(ACC, "AAAUSDT", "15m", 1)["outcome"], "NO_ORDER_SENT")
        self.assertEqual(r.orch.guard.slots().used, 0)
        self.assertTrue(r.orch.recovered)

    def test_orphan_submitted_reservation_fails_closed(self):
        r = Rig()
        r.res.orphan = ["Gx"]
        r.orch.recover()
        self.assertFalse(r.orch.recovered)
        self.assertEqual(r.orch.state, o.INTERVENTION)
        self.assertEqual(r.orch.start(), o.INTERVENTION)
        r.orch.tick(r.now)
        self.assertEqual(r.ex.placed, [])

    def test_stray_farmer_order_on_exchange_fails_closed(self):
        r = Rig()
        r.ex.place_order(client_id="kf-ghost-L01T0", symbol="AAAUSDT", side="BUY", type="LIMIT", qty=1.0, price=10.0)
        r.orch.recover()
        self.assertEqual(r.orch.state, o.INTERVENTION)

    def test_recovery_unreadable_snapshot_fails_closed(self):
        r = Rig()
        r.source.fail = True
        r.orch.recover()
        self.assertFalse(r.orch.recovered)


class TestUiSnapshot(unittest.TestCase):
    def test_fields(self):
        r = Rig(dead_score=60)
        r.run()
        ui = r.orch.ui_snapshot(r.now)
        self.assertEqual((ui["name"], ui["state"], ui["effective_capacity"]), ("KAIS FARMER", o.RUNNING, 2))
        self.assertEqual(ui["slots"], {"capacity": 2, "held": 0, "open": 2, "closing": 0, "free": 0})
        row = ui["positions"][0]
        for key in ("symbol", "side", "age_s", "score", "target_usdt", "max_loss_usdt", "dead_score", "dead_consecutive", "cooldown_s", "margin_usdt",
                    "notional_usdt", "est_cost_usdt", "target_pct", "stop_pct", "target_over_atr", "cost_over_target", "close_reason", "pnl_state",
                    "slot_state"):
            self.assertIn(key, row)
        self.assertEqual((row["slot_state"], row["close_reason"], row["pnl_state"], row["score"]), ("OPEN", "OPEN", "PENDING", 94.0))
        self.assertAlmostEqual(row["cost_over_target"], 0.2, places=4)
        self.assertNotRegex(json.dumps(ui, default=str), r"\b(APF|V1|DCA|grid|Grid)\b")


class TestOffDoesNotChangeExistingSystem(unittest.TestCase):
    ALLOWED = {"original_v2_live.py"}                                # C4: yalnız talep birleştirme (talep yoksa aynı liste nesnesi)

    def test_no_existing_module_imports_orchestrator(self):
        app = Path(__file__).parents[1] / "app"
        offenders = []
        for path in app.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if re.search(r"farmer_engine\.(orchestrator|margin)|import orchestrator", text):
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_existing_modules_referencing_farmer_are_known(self):
        app = Path(__file__).parents[1] / "app"
        refs = {p.name for p in app.glob("*.py") if "farmer_engine" in p.read_text(encoding="utf-8")}
        self.assertLessEqual(refs, self.ALLOWED | {"farmer_live.py"})

    def test_off_orchestrator_touches_nothing(self):
        r = Rig()
        r.orch.recover()
        r.orch.tick(r.now)
        self.assertEqual((r.ex.placed, dict(r.ex.reads), r.source.reads, r.stream.candle_reads), ([], {"position": 0, "open_orders": 0, "get_order": 0}, 1, 0))


if __name__ == "__main__":
    unittest.main()
