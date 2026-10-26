"""Canlı grid testleri — AĞ YOK, GERÇEK EMİR YOK.

Gerçek GridEngine, canlı adaptöre (BinanceLiveGridAdapter) bağlanır; adaptörün konuştuğu "istemci" Binance REST
yanıtlarını taklit eden sahte bir istemcidir. Böylece adaptörün emir eşlemesi, algo (stop) yolu, hata sınıflandırması,
acil durdurma doğrulaması ve sert sınırlar (2 kademe / 10 USDT) uçtan uca denenir.
"""
from __future__ import annotations

import asyncio
import threading
import unittest
from typing import Any

from app import grid_live
from app.grid_engine.engine import (BLOCKED, COMPLETED, EMERGENCY_CLOSED, INTERVENTION_REQUIRED, PAUSED, RUNNING, STOPPED, GridEngine,
                                    GridLevelSpec, GridSpec, client_order_id)
from app.grid_engine.fake_exchange import FakeExchange
from app.grid_engine.live_adapter import BinanceLiveGridAdapter, order_role, translate_error
from app.grid_engine.ports import (BUY, CANCELED, FILLED, LIMIT, LONG, NEW, SELL, SHORT, STOP_MARKET, ExchangeRejected, ExchangeTimeout,
                                   ExchangeUnreachable)
from app.grid_engine.store import GridStore

SYM = "BTCUSDT"


class FakeLiveError(RuntimeError):
    """LiveExchangeError taklidi (adaptör yalnızca bu alanlara bakar)."""

    def __init__(self, message: str, *, exchange_code: int | None = None, unknown_execution: bool = False, timed_out: bool = False,
                 transient_read_failure: bool = False, http_status: int = 502) -> None:
        super().__init__(message)
        self.exchange_code, self.unknown_execution, self.timed_out = exchange_code, unknown_execution, timed_out
        self.transient_read_failure, self.http_status = transient_read_failure, http_status


class FakeBinance:
    """Binance REST yüzeyi (yalnızca adaptörün kullandığı uçlar) — arkasında FakeExchange durumu."""

    def __init__(self, ex: FakeExchange) -> None:
        self.ex, self.calls, self._ids, self.fail_reads = ex, [], {}, False

    def _oid(self, cid: str) -> int:
        return self._ids.setdefault(cid, 1000 + len(self._ids))

    def _cid_for(self, order_id: int) -> str | None:
        return next((cid for cid, oid in self._ids.items() if oid == order_id), None)

    def _row(self, o) -> dict[str, Any]:
        return {"orderId": self._oid(o.client_id), "clientOrderId": o.client_id, "symbol": o.symbol, "side": o.side, "type": o.type,
                "price": str(o.price), "stopPrice": str(o.stop_price), "origQty": str(o.qty), "executedQty": str(o.filled),
                "status": o.status, "reduceOnly": o.reduce_only, "positionSide": "BOTH"}

    def _algo_row(self, o) -> dict[str, Any]:
        status = {"NEW": "NEW", "PARTIALLY_FILLED": "NEW", "FILLED": "TRIGGERED", "CANCELED": "CANCELED"}[o.status]
        return {"algoId": self._oid(o.client_id), "clientAlgoId": o.client_id, "symbol": o.symbol, "side": o.side, "orderType": "STOP_MARKET",
                "algoStatus": status, "triggerPrice": str(o.stop_price), "quantity": str(o.qty), "reduceOnly": "true",
                "actualOrderId": str(self._oid(o.client_id) + 5000) if o.status == FILLED else ""}

    def _wrap(self, fn):
        try:
            return fn()
        except ExchangeRejected as exc:
            raise FakeLiveError(str(exc), exchange_code=exc.code, http_status=400) from exc
        except ExchangeTimeout as exc:
            raise FakeLiveError(str(exc), unknown_execution=True, timed_out=True) from exc
        except ExchangeUnreachable as exc:
            raise FakeLiveError(str(exc), unknown_execution=True) from exc

    async def signed(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        params = dict(params or {})
        self.calls.append((method, path, params))
        if self.fail_reads and method == "GET":
            raise FakeLiveError("okuma başarısız", transient_read_failure=True, http_status=503)
        ex = self.ex
        if (method, path) == ("POST", "/fapi/v1/order"):
            o = self._wrap(lambda: ex.place_order(
                client_id=params["newClientOrderId"], symbol=params["symbol"], side=params["side"], type=params["type"],
                qty=float(params["quantity"]), price=float(params.get("price", 0) or 0), reduce_only=params.get("reduceOnly") == "true"))
            return self._row(o)
        if (method, path) == ("POST", "/fapi/v1/algoOrder"):
            assert params["algoType"] == "CONDITIONAL" and params["type"] == "STOP_MARKET"
            o = self._wrap(lambda: ex.place_order(
                client_id=params["clientAlgoId"], symbol=params["symbol"], side=params["side"], type="STOP_MARKET",
                qty=float(params["quantity"]), stop_price=float(params["triggerPrice"]), reduce_only=params.get("reduceOnly") == "true"))
            return {"algoId": self._oid(o.client_id), "clientAlgoId": o.client_id, "algoStatus": "NEW"}
        if (method, path) == ("GET", "/fapi/v1/order"):
            cid = params.get("origClientOrderId") or self._cid_for(int(params["orderId"]) - 5000) or self._cid_for(int(params["orderId"]))
            o = self.ex.orders.get(cid) if cid else None
            if o is None:
                raise FakeLiveError("Order does not exist.", exchange_code=-2013, http_status=400)
            return self._row(o)
        if (method, path) == ("DELETE", "/fapi/v1/order"):
            o = self._wrap(lambda: ex.cancel_order(params["symbol"], params["origClientOrderId"]))
            return self._row(o)
        if (method, path) == ("DELETE", "/fapi/v1/algoOrder"):
            self._wrap(lambda: ex.cancel_order(params["symbol"], params["clientAlgoId"]))
            return {"code": "200", "msg": "success"}
        if (method, path) == ("GET", "/fapi/v1/openOrders"):
            return [self._row(o) for o in ex.orders.values() if o.symbol == params["symbol"] and o.type != STOP_MARKET and o.status in (NEW, "PARTIALLY_FILLED")]
        if (method, path) == ("GET", "/fapi/v1/openAlgoOrders"):
            return [self._algo_row(o) for o in ex.orders.values() if o.symbol == params["symbol"] and o.type == STOP_MARKET and o.status in (NEW, "PARTIALLY_FILLED")]
        if (method, path) == ("GET", "/fapi/v1/allAlgoOrders"):
            return [self._algo_row(o) for o in ex.orders.values() if o.symbol == params["symbol"] and o.type == STOP_MARKET]
        if (method, path) == ("GET", "/fapi/v3/positionRisk"):
            return [{"symbol": params["symbol"], "positionAmt": str(ex.positions.get(params["symbol"], 0.0)), "positionSide": "BOTH"}]
        raise AssertionError(f"beklenmeyen çağrı {method} {path}")


class LoopThread:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()


def make_grid(side: str = LONG, *, price: float = 100.0):
    ex = ExchangeFake()
    ex.set_price(SYM, price)
    api = FakeBinance(ex)
    loop = LoopThread()
    adapter = BinanceLiveGridAdapter(api, loop.loop, timeout=5)
    store = GridStore(":memory:")
    if side == LONG:
        levels = [GridLevelSpec(99.0, 100.0, 0.1), GridLevelSpec(98.0, 99.0, 0.1)]
        stop = 95.0
    else:
        levels = [GridLevelSpec(101.0, 100.0, 0.1), GridLevelSpec(102.0, 101.0, 0.1)]
        stop = 105.0
    engine = GridEngine(adapter, store, GridSpec("g1", SYM, side, levels, stop))
    return ex, api, adapter, store, engine, loop


def ExchangeFake() -> FakeExchange:
    return FakeExchange(tick=0.1, step=0.001, min_notional=5.0)


class TestAdapterWithEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.ex, self.api, self.adapter, self.store, self.engine, self.loop = make_grid()

    def tearDown(self) -> None:
        self.loop.close()

    def test_start_places_only_the_two_limit_entries(self):
        self.assertEqual(self.engine.start(), RUNNING)
        placed = [(m, p) for m, p, _ in self.api.calls if m == "POST"]
        self.assertEqual(placed, [("POST", "/fapi/v1/order")] * 2)
        self.assertEqual(sorted(o.price for o in self.ex.orders.values()), [98.0, 99.0])
        self.assertTrue(all(o.type == LIMIT and not o.reduce_only for o in self.ex.orders.values()))

    def test_fill_installs_algo_stop_and_reduce_only_target(self):
        self.engine.start()
        self.ex.set_price(SYM, 98.9)            # seviye 1 dolar
        self.engine.tick()
        stops = [o for o in self.ex.orders.values() if o.type == STOP_MARKET]
        self.assertEqual(len(stops), 1)
        self.assertEqual((stops[0].qty, stops[0].stop_price, stops[0].reduce_only, stops[0].side), (0.1, 95.0, True, SELL))
        algo_posts = [p for m, path, p in self.api.calls if path == "/fapi/v1/algoOrder" and m == "POST"]
        self.assertEqual(len(algo_posts), 1)
        self.assertEqual(algo_posts[0]["algoType"], "CONDITIONAL")
        self.assertEqual(algo_posts[0]["reduceOnly"], "true")
        self.assertEqual(algo_posts[0]["workingType"], "MARK_PRICE")
        self.assertTrue(algo_posts[0]["clientAlgoId"].startswith("kg-"))
        self.assertNotIn("newClientOrderId", algo_posts[0])
        targets = [o for o in self.ex.orders.values() if o.type == LIMIT and o.reduce_only]
        self.assertEqual([(t.price, t.qty) for t in targets], [(100.0, 0.1)])

    def test_stop_hit_is_read_back_through_algo_history(self):
        self.engine.start()
        self.ex.set_price(SYM, 98.9)
        self.engine.tick()
        self.ex.set_price(SYM, 94.0)            # stop tetiklenir (algo → TRIGGERED, actualOrderId)
        self.engine.tick()
        self.assertEqual(self.engine.status, STOPPED)
        self.assertEqual(self.ex.positions.get(SYM, 0.0), 0.0)
        self.assertTrue(all(o.status in (FILLED, CANCELED) for o in self.ex.orders.values()))

    def test_emergency_stop_closes_verifies_and_only_then_cancels_stops(self):
        self.engine.start()
        self.ex.set_price(SYM, 97.9)            # iki kademe de dolar
        self.engine.tick()
        self.assertAlmostEqual(self.ex.positions[SYM], 0.2)
        self.assertEqual(self.engine.emergency_stop(), EMERGENCY_CLOSED)
        self.assertEqual(self.ex.positions[SYM], 0.0)
        self.assertEqual([o for o in self.ex.orders.values() if o.status in (NEW, "PARTIALLY_FILLED")], [])

    def test_emergency_stop_leaves_stops_when_close_cannot_be_verified(self):
        self.engine.start()
        self.ex.set_price(SYM, 98.9)
        self.engine.tick()
        self.api.fail_reads = True               # borsadan okunamıyor → kapandığı doğrulanamaz
        self.engine.max_close_attempts = 1
        status = self.engine.emergency_stop()
        self.assertEqual(status, INTERVENTION_REQUIRED)
        live_stops = [o for o in self.ex.orders.values() if o.type == STOP_MARKET and o.status in (NEW, "PARTIALLY_FILLED")]
        self.assertTrue(live_stops, "doğrulanamayan acil durdurmada koruyucu stop iptal EDİLMEMELİ")

    def test_lost_response_is_resolved_by_client_id_without_double_order(self):
        cid = client_order_id("g1", LONG, 1, "E", 0)
        self.ex.lose_response_for.add(cid)       # emir işlenir, yanıt kaybolur
        self.engine.start()
        self.assertEqual(self.ex.placed_count[cid], 1)
        self.engine.tick()
        self.assertEqual(self.ex.placed_count[cid], 1)

    def test_foreign_position_blocks_grid_before_any_order(self):
        _, api, _, store, _, loop = make_grid()
        loop.close()
        ex, api, adapter, store, engine, loop2 = make_grid()
        try:
            ex.set_position(SYM, 0.5)
            self.assertEqual(engine.start(), BLOCKED)
            self.assertEqual([c for c in api.calls if c[0] == "POST"], [])
        finally:
            loop2.close()


class TestAdapterUnits(unittest.TestCase):
    def test_role_is_parsed_from_client_id(self):
        for role in "ETSC":
            self.assertEqual(order_role(client_order_id("grid123", LONG, 2, role, 3)), role)
            self.assertEqual(order_role(client_order_id("grid123", SHORT, 1, role, 0)), role)
        self.assertEqual(order_role("manual-123"), "")

    def test_error_translation_never_turns_uncertainty_into_a_reject(self):
        self.assertIsInstance(translate_error(FakeLiveError("x", unknown_execution=True), read=False), ExchangeTimeout)
        self.assertIsInstance(translate_error(FakeLiveError("x", timed_out=True), read=False), ExchangeTimeout)
        self.assertIsInstance(translate_error(FakeLiveError("x", transient_read_failure=True), read=True), ExchangeUnreachable)
        self.assertIsInstance(translate_error(FakeLiveError("x", http_status=503), read=False), ExchangeUnreachable)
        rejected = translate_error(FakeLiveError("x", exchange_code=-2021), read=False)
        self.assertIsInstance(rejected, ExchangeRejected)
        self.assertEqual(rejected.code, -2021)
        self.assertIsInstance(translate_error(FakeLiveError("kapı", http_status=423), read=True), ExchangeUnreachable)
        self.assertIsInstance(translate_error(FakeLiveError("kapı", http_status=423), read=False), ExchangeRejected)

    def test_calling_from_the_event_loop_thread_is_refused(self):
        async def scenario():
            adapter = BinanceLiveGridAdapter(FakeBinance(ExchangeFake()), asyncio.get_running_loop())
            with self.assertRaises(RuntimeError):
                adapter.position(SYM)
        asyncio.run(scenario())

    def test_adapter_holds_no_credentials_and_repr_is_safe(self):
        loop = LoopThread()
        try:
            adapter = BinanceLiveGridAdapter(FakeBinance(ExchangeFake()), loop.loop)
            self.assertNotIn("key", repr(adapter).lower().replace("anahtar gizli", ""))
            self.assertFalse(any("secret" in name or "api_key" in name for name in vars(adapter)))
        finally:
            loop.close()

    def test_non_hedge_position_rows_are_not_trusted(self):
        class Hedge(FakeBinance):
            async def signed(self, method, path, params=None):
                return [{"symbol": SYM, "positionAmt": "1", "positionSide": "LONG"}]
        loop = LoopThread()
        try:
            adapter = BinanceLiveGridAdapter(Hedge(ExchangeFake()), loop.loop)
            with self.assertRaises(ExchangeUnreachable):
                adapter.position(SYM)
        finally:
            loop.close()


RULES = {"tick": 0.1, "step": 0.001, "min_qty": 0.001, "min_notional": 5.0, "max_qty": 1000}
POLICY = {"max_leverage": 10, "max_loss_per_trade": 3.0, "daily_loss_limit": 10.0, "max_total_exposure_usdt": 350.0,
          "max_positions": 5, "max_stop_distance_pct": 5.0}


def prepare(**over):
    args = dict(direction=LONG, lower=98.0, upper=99.0, count=2, margin_usdt=10.0, leverage=2, stop=97.0, price=100.0, rules=RULES,
                policy=POLICY, realized_pnl_today=0.0, existing_exposure=0.0, open_positions=0)
    args.update(over)
    return grid_live.prepare_live_grid(**args)


class TestHardLimits(unittest.TestCase):
    def test_limits_are_the_agreed_ones(self):
        self.assertEqual((grid_live.LIVE_MAX_LEVELS, grid_live.LIVE_MAX_MARGIN_USDT), (2, 10.0))

    def test_default_is_disabled(self):
        import os
        old = os.environ.pop(grid_live.ENV_FLAG, None)
        try:
            self.assertFalse(grid_live.live_enabled())
            with self.assertRaises(Exception):
                grid_live.require_enabled()
            os.environ[grid_live.ENV_FLAG] = "1"
            self.assertTrue(grid_live.live_enabled())
        finally:
            os.environ.pop(grid_live.ENV_FLAG, None)
            if old is not None:
                os.environ[grid_live.ENV_FLAG] = old

    def test_valid_plan_is_rounded_to_exchange_steps_and_stays_inside_caps(self):
        plan = prepare()
        self.assertEqual(len(plan["levels"]), 2)
        self.assertLessEqual(plan["total_margin_usdt"], 10.0 + 1e-9)
        self.assertEqual([r["entry"] for r in plan["levels"]], [99.0, 98.0])
        self.assertEqual([r["target"] for r in plan["levels"]], [100.0, 99.0])
        for r in plan["levels"]:
            self.assertAlmostEqual(r["qty"] / 0.001, round(r["qty"] / 0.001), places=6)
        self.assertEqual(plan["stop_price"], 97.0)

    def test_three_levels_are_refused(self):
        with self.assertRaises(grid_live.LiveGridError):
            prepare(count=3, lower=97.0, upper=99.0)

    def test_margin_above_ten_is_refused(self):
        with self.assertRaises(grid_live.LiveGridError):
            prepare(margin_usdt=10.01)

    def test_loss_above_trade_limit_is_refused(self):
        with self.assertRaises(grid_live.LiveGridError) as ctx:
            prepare(leverage=10, stop=96.0, policy={**POLICY, "max_loss_per_trade": 0.5})
        self.assertIn("En kötü zarar", str(ctx.exception))

    def test_daily_loss_allowance_is_respected(self):
        with self.assertRaises(grid_live.LiveGridError):
            prepare(realized_pnl_today=-9.9)
        prepare(realized_pnl_today=5.0)          # kâr hakkı artırmaz ama engellemez

    def test_exposure_and_position_limits(self):
        with self.assertRaises(grid_live.LiveGridError):
            prepare(existing_exposure=349.0)
        with self.assertRaises(grid_live.LiveGridError):
            prepare(open_positions=5)

    def test_wrong_side_entries_would_fill_immediately_and_are_refused(self):
        with self.assertRaises(grid_live.LiveGridError):
            prepare(price=98.5)

    def test_short_geometry(self):
        plan = prepare(direction=SHORT, lower=101.0, upper=102.0, stop=103.0, price=100.0)
        self.assertEqual([r["entry"] for r in plan["levels"]], [101.0, 102.0])
        self.assertEqual([r["target"] for r in plan["levels"]], [100.0, 101.0])
        self.assertEqual(plan["stop_price"], 103.0)
        with self.assertRaises(grid_live.LiveGridError):
            prepare(direction=SHORT, lower=101.0, upper=102.0, stop=101.5, price=100.0)

    def test_stop_too_close_to_liquidation_is_refused(self):
        with self.assertRaises(grid_live.LiveGridError):
            prepare(leverage=10, lower=98.0, upper=99.0, stop=91.0, policy={**POLICY, "max_loss_per_trade": 50, "daily_loss_limit": 50,
                                                                             "max_stop_distance_pct": 9})

    def test_tiny_orders_below_exchange_minimum_are_refused(self):
        with self.assertRaises(grid_live.LiveGridError):
            prepare(margin_usdt=2.0, leverage=1)

    def test_rounding_collision_is_refused(self):
        with self.assertRaises(grid_live.LiveGridError):
            prepare(lower=98.0, upper=98.04)

    def test_missing_exchange_rules_never_start_a_grid(self):
        with self.assertRaises(grid_live.LiveGridError):
            prepare(rules={})


class TestRunnerStatus(unittest.TestCase):
    def test_status_without_grid(self):
        runner = grid_live.LiveGridRunner(GridStore(":memory:"))
        payload = runner.status_payload()
        self.assertIsNone(payload["grid"])
        self.assertEqual((payload["max_levels"], payload["max_margin_usdt"]), (2, 10.0))

    def test_final_grid_does_not_occupy_the_slot_but_blocked_one_does(self):
        store = GridStore(":memory:")
        runner = grid_live.LiveGridRunner(store)
        store.create_grid("a", SYM, LONG, {"stop_price": 1, "levels": []})
        store.set_grid("a", status=EMERGENCY_CLOSED)
        self.assertIsNone(runner.occupying_row())
        store.create_grid("b", SYM, LONG, {"stop_price": 1, "levels": []})
        store.set_grid("b", status=INTERVENTION_REQUIRED)
        self.assertIsNotNone(runner.occupying_row())

    def test_runner_flow_start_pause_emergency_with_fake_exchange(self):
        ex = ExchangeFake()
        ex.set_price(SYM, 100.0)
        api = FakeBinance(ex)
        store = GridStore(":memory:", check_same_thread=False)
        runner = grid_live.LiveGridRunner(store)
        state: dict[str, Any] = {"real_trading_locked": False, "emergency": {}, "events": [], "policy": POLICY}
        plan = prepare()

        async def scenario():
            adapter = BinanceLiveGridAdapter(api, asyncio.get_running_loop(), timeout=5)
            runner._record = lambda *a, **k: None          # v25 günlüğü bu testin konusu değil
            runner.blocked_check = lambda s: False
            runner._ensure_loop = lambda *a, **k: None     # döngü ayrıca denenir
            started = await runner.start(None, state, adapter, plan, SYM)
            self.assertEqual(started["grid"]["status"], RUNNING)
            with self.assertRaises(grid_live.LiveGridError):     # ikinci grid reddedilir
                await runner.start(None, state, adapter, plan, SYM)
            ex.set_price(SYM, 98.9)
            await asyncio.to_thread(runner._tick_once, runner._engine)
            self.assertAlmostEqual(runner.status_payload()["grid"]["position_qty"], plan["levels"][0]["qty"])
            done = await runner.emergency(None, state, adapter)
            self.assertEqual(done["grid"]["status"], EMERGENCY_CLOSED)
            self.assertEqual(ex.positions[SYM], 0.0)
        asyncio.run(scenario())

    def test_pause_without_fill_ends_as_stopped(self):
        ex = ExchangeFake()
        ex.set_price(SYM, 100.0)
        api = FakeBinance(ex)
        runner = grid_live.LiveGridRunner(GridStore(":memory:", check_same_thread=False))
        state: dict[str, Any] = {"real_trading_locked": False, "emergency": {}, "events": [], "policy": POLICY}

        async def scenario():
            adapter = BinanceLiveGridAdapter(api, asyncio.get_running_loop(), timeout=5)
            runner._record = lambda *a, **k: None
            runner.blocked_check = lambda s: False
            runner._ensure_loop = lambda *a, **k: None
            await runner.start(None, state, adapter, prepare(), SYM)
            result = await runner.pause(state, adapter)
            self.assertEqual(result["grid"]["status"], STOPPED)
            self.assertEqual([o for o in ex.orders.values() if o.status in (NEW, "PARTIALLY_FILLED")], [])
        asyncio.run(scenario())


class TestStartEndpointGates(unittest.TestCase):
    """Başlatma ucu kaynağında sert kapılar yerinde mi (FastAPI gerektirmeyen yapısal kontrol)."""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] / "app" / "v25_execution.py").read_text()
        start = text.index("async def v25_grid_live_start")
        cls.body = text[start:text.index("@router.post(\"/grid/live/stop\")", start)]

    def test_every_gate_precedes_the_first_order(self):
        order = ["require_enabled", "CONFIRM_START", "require_fill_unlocked", "is_armed(state)", "live_execution_blocked(state)",
                 "real_trading_locked", "readiness_for", "symbol_occupancy_status", "prepare_live_grid", "set_live_isolated_margin",
                 "apply_live_verified_leverage", "runner.start"]
        positions = [self.body.index(name) for name in order]
        self.assertEqual(positions, sorted(positions))

    def test_stop_and_emergency_work_even_when_the_flag_is_off(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] / "app" / "v25_execution.py").read_text()
        for name in ("v25_grid_live_stop", "v25_grid_live_emergency"):
            chunk = text[text.index(f"async def {name}"):][:700]
            self.assertNotIn("require_enabled", chunk)
        self.assertIn("require_enabled", text[text.index("async def v25_grid_live_resume"):][:500])


if __name__ == "__main__":
    unittest.main()
