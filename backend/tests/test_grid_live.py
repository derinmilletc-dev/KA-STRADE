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
            return [{"symbol": params["symbol"], "positionAmt": str(ex.positions.get(params["symbol"], 0.0)), "positionSide": "BOTH",
                     "unRealizedProfit": str(getattr(self, "upnl", 0.0)),
                     "liquidationPrice": str(getattr(self, "liq", 0.0)), "markPrice": str(getattr(self, "mark", 0.0))}]
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
        self.assertEqual((grid_live.LIVE_MAX_LEVELS, grid_live.LIVE_MAX_MARGIN_USDT), (20, 300.0))

    def test_default_is_enabled_and_zero_disables(self):
        import os
        old = os.environ.pop(grid_live.ENV_FLAG, None)
        try:
            self.assertTrue(grid_live.live_enabled())
            os.environ[grid_live.ENV_FLAG] = "0"
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

    def test_above_twenty_levels_are_refused(self):
        with self.assertRaises(grid_live.LiveGridError):
            prepare(count=21, lower=70.0, upper=99.0)

    def test_margin_above_ceiling_is_refused(self):
        with self.assertRaises(grid_live.LiveGridError):
            prepare(margin_usdt=300.01)

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
        self.assertEqual((payload["max_levels"], payload["max_margin_usdt"]), (20, 300.0))

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


class TestMultiGridRunner(unittest.TestCase):
    """Çoklu grid: kullanıcı başına birden fazla coin, kullanıcılar birbirini görmez, coin başına tek grid, üst sınır."""

    def setUp(self) -> None:
        self.ex = ExchangeFake()
        for sym in (SYM, "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "ADAUSDT"):
            self.ex.set_price(sym, 100.0)
        self.api = FakeBinance(self.ex)
        self.runner = grid_live.LiveGridRunner(GridStore(":memory:", check_same_thread=False))
        self.runner._record = lambda *a, **k: None
        self.runner.blocked_check = lambda s: False
        self.runner._ensure_loop = lambda *a, **k: None
        self.state: dict[str, Any] = {"real_trading_locked": False, "emergency": {}, "events": [], "policy": POLICY}

    def run_async(self, fn):
        async def scenario():
            adapter = BinanceLiveGridAdapter(self.api, asyncio.get_running_loop(), timeout=5)
            return await fn(adapter)
        return asyncio.run(scenario())

    def test_one_member_runs_grids_on_several_coins_and_others_cannot_see_them(self):
        async def flow(adapter):
            await self.runner.start(None, self.state, adapter, prepare(recycle=True), SYM, user_id="u1", owner=False)
            out = await self.runner.start(None, self.state, adapter, prepare(), "ETHUSDT", user_id="u1", owner=False)
            self.assertEqual(out["active_count"], 2)
            self.assertEqual({g["symbol"] for g in out["grids"]}, {SYM, "ETHUSDT"})
            self.assertTrue(next(g for g in out["grids"] if g["symbol"] == SYM)["recycle"])
            with self.assertRaises(grid_live.LiveGridError):        # aynı coinde ikinci grid yok
                await self.runner.start(None, self.state, adapter, prepare(), SYM, user_id="u1", owner=False)
            other = self.runner.status_payload("u2", owner=False)
            self.assertEqual((other["grids"], other["grid"]), ([], None))
            # başka üye aynı coinde kendi grid'ini açabilir (ayrı hesap)
            await self.runner.start(None, self.state, adapter, prepare(), "SOLUSDT", user_id="u2", owner=False)
            self.assertEqual([g["symbol"] for g in self.runner.status_payload("u2", owner=False)["grids"]], ["SOLUSDT"])
            self.assertTrue(self.runner.symbol_busy(SYM, "u1"))
            self.assertFalse(self.runner.symbol_busy(SYM, "u2"))
        self.run_async(flow)

    def test_member_cannot_stop_another_members_grid(self):
        async def flow(adapter):
            out = await self.runner.start(None, self.state, adapter, prepare(), SYM, user_id="u1", owner=False)
            gid = out["started_grid_id"]
            with self.assertRaises(grid_live.LiveGridError) as caught:
                await self.runner.pause(self.state, adapter, user_id="u2", grid_id=gid, owner=False)
            self.assertEqual(caught.exception.status, 404)
            with self.assertRaises(grid_live.LiveGridError):
                await self.runner.emergency(None, self.state, adapter, user_id="u2", grid_id=gid, owner=False)
            done = await self.runner.pause(self.state, adapter, user_id="u1", grid_id=gid, owner=False)
            self.assertEqual(done["grids"][0]["status"], STOPPED)
        self.run_async(flow)

    def test_per_user_grid_cap(self):
        async def flow(adapter):
            for sym in (SYM, "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT")[:grid_live.MAX_GRIDS_PER_USER]:
                await self.runner.start(None, self.state, adapter, prepare(), sym, user_id="u1", owner=False)
            with self.assertRaises(grid_live.LiveGridError) as caught:
                await self.runner.start(None, self.state, adapter, prepare(), "ADAUSDT", user_id="u1", owner=False)
            self.assertEqual(caught.exception.status, 409)
        self.run_async(flow)

    def test_pause_targets_only_the_named_grid(self):
        async def flow(adapter):
            a = (await self.runner.start(None, self.state, adapter, prepare(), SYM, user_id="u1", owner=False))["started_grid_id"]
            await self.runner.start(None, self.state, adapter, prepare(), "ETHUSDT", user_id="u1", owner=False)
            with self.assertRaises(grid_live.LiveGridError):          # iki grid varken hangisi belirtilmeli
                await self.runner.pause(self.state, adapter, user_id="u1", owner=False)
            out = await self.runner.pause(self.state, adapter, user_id="u1", grid_id=a, owner=False)
            statuses = {g["symbol"]: g["status"] for g in out["grids"]}
            self.assertEqual(statuses, {SYM: STOPPED, "ETHUSDT": RUNNING})
            eth_entries = [o for o in self.ex.orders.values() if o.symbol == "ETHUSDT" and o.status == NEW]
            self.assertEqual(len(eth_entries), 2)
        self.run_async(flow)

    def test_reserved_risk_counts_running_grids_only_once(self):
        async def flow(adapter):
            await self.runner.start(None, self.state, adapter, prepare(), SYM, user_id="u1", owner=False)
            reserved = self.runner.reserved_risk("u1")
            plan = prepare()
            self.assertAlmostEqual(reserved["worst_loss_usdt"], plan["worst_loss_usdt"])
            self.assertAlmostEqual(reserved["pending_entry_notional_usdt"], plan["total_notional_usdt"], places=6)
            self.assertEqual(reserved["symbols"], [SYM])
            self.ex.set_price(SYM, 98.95)                               # 1. kademe dolar: artık pozisyon, bekleyen değil
            await asyncio.to_thread(self.runner._tick_once, self.runner._engines[self.runner.occupying_row("u1", SYM, owner=False)["grid_id"]])
            after = self.runner.reserved_risk("u1")
            self.assertLess(after["pending_entry_notional_usdt"], reserved["pending_entry_notional_usdt"])
        self.run_async(flow)

    def test_legacy_ownerless_grid_belongs_to_the_owner_only(self):
        store = self.runner.store
        store.create_grid("legacy", SYM, LONG, {"stop_price": 1, "levels": []})
        store.set_grid("legacy", status=RUNNING)
        self.assertIsNotNone(self.runner.occupying_row("owner-id", SYM, owner=True))
        self.assertIsNone(self.runner.occupying_row("member", SYM, owner=False))
        self.assertEqual(self.runner.status_payload("member", owner=False)["grids"], [])


class TestSmartLimits(unittest.TestCase):
    def test_level_profit_must_beat_fees(self):
        with self.assertRaises(grid_live.LiveGridError) as caught:
            prepare(lower=98.98, upper=99.0, count=2, rules={**RULES, "tick": 0.01})
        self.assertIn("ücret", str(caught.exception))

    def test_running_grids_use_up_the_daily_loss_allowance(self):
        plan = prepare()
        with self.assertRaises(grid_live.LiveGridError) as caught:
            prepare(reserved_worst_loss=POLICY["daily_loss_limit"] - plan["worst_loss_usdt"] / 2)
        self.assertIn("çalışan grid", str(caught.exception))

    def test_pending_entries_of_other_grids_count_towards_exposure(self):
        with self.assertRaises(grid_live.LiveGridError):
            prepare(reserved_pending_notional=POLICY["max_total_exposure_usdt"])
        with self.assertRaises(grid_live.LiveGridError):
            prepare(open_positions=3, reserved_symbols=2)

    def test_plan_carries_cycle_flag_and_profit_estimate(self):
        plan = prepare(recycle=True)
        self.assertTrue(plan["recycle"])
        self.assertGreater(plan["profit_per_cycle_usdt"], 0)
        self.assertEqual((plan["lower"], plan["upper"], plan["count"]), (98.0, 99.0, 2))
        spec = grid_live.grid_spec_from_plan("gx", SYM, plan)
        self.assertTrue(spec.recycle)
        self.assertAlmostEqual(spec.meta["worst_loss_usdt"], plan["worst_loss_usdt"])


def prepare_neutral(**over):
    args = dict(lower=97.0, upper=103.0, count=6, margin_usdt=60.0, leverage=2, stop_lower=95.0, stop_upper=105.0, price=100.0,
                rules=RULES, policy={**POLICY, "max_loss_per_trade": 5.0}, realized_pnl_today=0.0, existing_exposure=0.0, open_positions=0)
    args.update(over)
    return grid_live.prepare_live_neutral_grid(**args)


class TestNeutralPlan(unittest.TestCase):
    def test_lines_skip_the_one_at_the_price_and_split_into_buys_and_sells(self):
        plan = prepare_neutral()
        self.assertEqual(plan["direction"], "NEUTRAL")
        self.assertEqual([(r["side"], r["entry"], r["target"]) for r in plan["levels"]],
                         [(LONG, 97.0, 98.0), (LONG, 98.0, 99.0), (LONG, 99.0, 100.0), (SHORT, 101.0, 100.0), (SHORT, 102.0, 101.0), (SHORT, 103.0, 102.0)])
        self.assertEqual((plan["stop_price"], plan["stop_price_upper"]), (95.0, 105.0))
        self.assertTrue(plan["recycle"])
        self.assertAlmostEqual(plan["worst_loss_usdt"], max(plan["worst_loss_down_usdt"], plan["worst_loss_up_usdt"]))
        self.assertGreater(plan["profit_per_cycle_usdt"], 0)

    def test_price_outside_the_range_or_wrong_stops_are_refused(self):
        for over in ({"price": 96.0}, {"price": 104.0}, {"stop_lower": 97.5}, {"stop_upper": 102.0}, {"count": 1}, {"count": 21}):
            with self.assertRaises(grid_live.LiveGridError, msg=str(over)):
                prepare_neutral(**over)

    def test_one_sided_range_is_refused(self):
        with self.assertRaises(grid_live.LiveGridError) as caught:
            prepare_neutral(price=97.2)
        self.assertIn("iki tarafında", str(caught.exception))

    def test_risk_limits_apply_to_the_worse_side(self):
        with self.assertRaises(grid_live.LiveGridError):
            prepare_neutral(policy={**POLICY, "max_loss_per_trade": 1.0})
        with self.assertRaises(grid_live.LiveGridError):
            prepare_neutral(reserved_worst_loss=9.5)
        with self.assertRaises(grid_live.LiveGridError):       # likidasyona yakın
            prepare_neutral(leverage=10, policy={**POLICY, "max_loss_per_trade": 50.0, "daily_loss_limit": 50.0, "max_leverage": 20,
                                                 "max_stop_distance_pct": 50.0}, stop_lower=90.0, stop_upper=110.0)

    def test_neutral_runner_flow_on_the_live_adapter(self):
        ex = ExchangeFake()
        ex.set_price(SYM, 100.0)
        api = FakeBinance(ex)
        runner = grid_live.LiveGridRunner(GridStore(":memory:", check_same_thread=False))
        runner._record = lambda *a, **k: None
        runner.blocked_check = lambda s: False
        runner._ensure_loop = lambda *a, **k: None
        state: dict[str, Any] = {"real_trading_locked": False, "emergency": {}, "events": [], "policy": POLICY}

        async def scenario():
            adapter = BinanceLiveGridAdapter(api, asyncio.get_running_loop(), timeout=5)
            out = await runner.start(None, state, adapter, prepare_neutral(), SYM, user_id="u1", owner=False)
            gid = out["started_grid_id"]
            self.assertEqual(out["grids"][0]["status"], RUNNING)
            engine = runner._engines[gid]
            for price in (98.9, 100.1, 101.1, 99.9):
                ex.set_price(SYM, price)
                await asyncio.to_thread(runner._tick_once, engine)
                await asyncio.to_thread(runner._tick_once, engine)
                self.assertAlmostEqual(engine.grid_net(), ex.positions.get(SYM, 0.0))
            row = runner.status_payload("u1", owner=False)["grids"][0]
            self.assertGreaterEqual(row["stats"]["cycles"], 2)
            self.assertEqual(runner.store.grid(gid)["side"], "NEUTRAL")
            rebuilt = runner.build_engine(runner.store.grid(gid), adapter, state)       # yeniden başlatmada aynı nötr motor
            self.assertEqual(type(rebuilt).__name__, "NeutralGridEngine")
            done = await runner.emergency(None, state, adapter, user_id="u1", grid_id=gid, owner=False)
            self.assertEqual(done["grids"][0]["status"], EMERGENCY_CLOSED)
            self.assertAlmostEqual(ex.positions.get(SYM, 0.0), 0.0)
        asyncio.run(scenario())



class LiquidationAndLockTests(unittest.TestCase):
    """Çalışırken likidasyon mesafesi izleme ve iz süren kâr kilidi."""

    def test_distance_long_and_short(self):
        self.assertAlmostEqual(grid_live.liq_distance_pct(100.0, 90.0, 1.0), 10.0)
        self.assertAlmostEqual(grid_live.liq_distance_pct(100.0, 108.0, -1.0), 8.0)
        self.assertEqual(grid_live.liq_distance_pct(100.0, 101.0, 1.0), 0.0)     # likidasyon fiyatı zaten aşılmış
        self.assertIsNone(grid_live.liq_distance_pct(100.0, 0.0, 1.0))           # pozisyon yok / veri yok
        self.assertIsNone(grid_live.liq_distance_pct(100.0, 90.0, 0.0))

    def test_levels(self):
        levels = [grid_live.liq_level(v) for v in (None, 50.0, 12.0, 9.0, 7.0, 5.0, 3.5, 1.0)]
        self.assertEqual(levels, ["unknown", "ok", "warn", "warn", "pause", "pause", "close", "close"])

    def _run(self, plan, *, position, liq, mark, stats=None, check_twice=False, peak=None):
        ex = ExchangeFake()
        ex.set_price(SYM, 100.0)
        api = FakeBinance(ex)
        api.liq, api.mark = liq, mark
        runner = grid_live.LiveGridRunner(GridStore(":memory:", check_same_thread=False))
        runner._record = lambda *a, **k: None
        runner.blocked_check = lambda s: False
        runner._ensure_loop = lambda *a, **k: None
        state: dict[str, Any] = {"real_trading_locked": False, "emergency": {}, "events": [], "policy": POLICY}
        original = grid_live.grid_stats

        async def flow():
            adapter = BinanceLiveGridAdapter(api, asyncio.get_running_loop(), timeout=5)
            await runner.start(None, state, adapter, plan, SYM, user_id="u1", owner=False)
            engine = next(iter(runner._engines.values()))
            engine.ex.risk_snapshot = lambda symbol: {"amount": position, "liq": liq, "mark": mark}   # borsa düz kalır; yalnız risk ölçümü verilir
            if peak is not None:
                runner.store.set_grid(engine.gid, peak_pnl=peak)
            if stats is not None:
                grid_live.grid_stats = lambda spec, orders: stats
            try:
                result = await asyncio.to_thread(runner._guard_once, engine, state)
                again = await asyncio.to_thread(runner._guard_once, engine, state) if check_twice else None
            finally:
                grid_live.grid_stats = original
            return result, again, engine, runner.status_payload("u1", owner=False)["grids"][0]
        return asyncio.run(flow())

    def test_far_from_liquidation_does_nothing_and_reports_distance(self):
        result, _, engine, row = self._run(prepare(), position=0.01, liq=70.0, mark=100.0)
        self.assertEqual((result, engine.status), ("", RUNNING))
        self.assertEqual(row["liq"]["level"], "ok")
        self.assertAlmostEqual(row["liq"]["distance_pct"], 30.0)

    def test_warn_zone_only_alerts_once(self):
        result, again, engine, row = self._run(prepare(), position=0.01, liq=90.0, mark=100.0, check_twice=True)
        self.assertEqual((result, engine.status), ("", RUNNING))
        self.assertEqual(row["liq"]["level"], "warn")
        warnings = [a for a in row["alerts"] if "Likidasyona" in a["text"]]
        self.assertEqual(len(warnings), 1)

    def test_pause_zone_stops_new_entries_keeps_position_protection(self):
        result, _, engine, row = self._run(prepare(), position=0.01, liq=94.0, mark=100.0)
        self.assertEqual((result, engine.status), ("LIQ_PAUSE", PAUSED))
        self.assertIn("Likidasyona yaklaşıldı", row["reason"])

    def test_close_zone_closes_position(self):
        result, _, engine, row = self._run(prepare(), position=0.01, liq=97.0, mark=100.0)
        self.assertEqual(result, "LIQ_CLOSE")
        self.assertEqual(engine.status, EMERGENCY_CLOSED)
        self.assertIn("pozisyon kapatıldı", row["reason"])

    def test_short_position_uses_upper_liquidation(self):
        result, _, engine, _ = self._run(prepare(), position=-0.01, liq=103.0, mark=100.0)
        self.assertEqual((result, engine.status), ("LIQ_CLOSE", EMERGENCY_CLOSED))

    def test_no_position_no_action(self):
        result, _, engine, row = self._run(prepare(), position=0.0, liq=0.0, mark=100.0)
        self.assertEqual((result, engine.status), ("", RUNNING))
        self.assertEqual(row["liq"]["level"], "unknown")

    def test_check_is_throttled(self):
        ex = ExchangeFake()
        calls = []
        class Spy:
            def __init__(self, inner): self.inner = inner
            def __getattr__(self, name): return getattr(self.inner, name)
            def risk_snapshot(self, symbol):
                calls.append(symbol)
                return {"amount": 0.0, "liq": 0.0, "mark": 100.0}
        runner = grid_live.LiveGridRunner(GridStore(":memory:", check_same_thread=False))
        spy = Spy(ex)
        class Eng:
            gid, sym, status = "g1", SYM, RUNNING
        Eng.ex = spy
        runner._check_liquidation(Eng, {})
        runner._check_liquidation(Eng, {})
        self.assertEqual(len(calls), 1)

    def test_profit_lock_validation_and_meta(self):
        plan = grid_live.apply_protection(prepare(), None, None, profit_lock_usdt=10.0, close_on_target=True)
        meta = grid_live.grid_spec_from_plan("gx", SYM, plan).meta
        self.assertEqual((meta["profit_lock_usdt"], meta["lock_keep_pct"], meta["close_on_target"]), (10.0, 70.0, True))
        for bad in (0, -1, float("nan"), 2_000_000):
            with self.assertRaises(grid_live.LiveGridError):
                grid_live.apply_protection(prepare(), None, None, profit_lock_usdt=bad)
        self.assertNotIn("profit_lock_usdt", grid_live.apply_protection(prepare(), None, None))

    def test_profit_lock_tracks_peak_and_does_not_trigger_early(self):
        plan = grid_live.apply_protection(prepare(), None, None, profit_lock_usdt=10.0)
        result, _, engine, row = self._run(plan, position=0.0, liq=0.0, mark=100.0, stats={"realized_usdt": 12.0, "open_levels": 0})
        self.assertEqual((result, engine.status), ("", RUNNING))
        self.assertAlmostEqual(row["peak_pnl"], 12.0)

    def test_profit_lock_closes_when_profit_falls_below_keep(self):
        plan = grid_live.apply_protection(prepare(), None, None, profit_lock_usdt=10.0)
        result, _, engine, row = self._run(plan, position=0.0, liq=0.0, mark=100.0, peak=20.0, stats={"realized_usdt": 13.0, "open_levels": 0})
        self.assertEqual(result, "PROFIT_LOCK")
        self.assertEqual(engine.status, EMERGENCY_CLOSED)
        self.assertIn("Kâr kilidi", row["reason"])

    def test_profit_lock_holds_above_keep(self):
        plan = grid_live.apply_protection(prepare(), None, None, profit_lock_usdt=10.0)
        result, _, engine, _ = self._run(plan, position=0.0, liq=0.0, mark=100.0, peak=20.0, stats={"realized_usdt": 15.0, "open_levels": 0})
        self.assertEqual((result, engine.status), ("", RUNNING))

    def test_profit_lock_not_active_before_start_level(self):
        plan = grid_live.apply_protection(prepare(), None, None, profit_lock_usdt=10.0)
        result, _, engine, _ = self._run(plan, position=0.0, liq=0.0, mark=100.0, peak=6.0, stats={"realized_usdt": 1.0, "open_levels": 0})
        self.assertEqual((result, engine.status), ("", RUNNING))

    def test_take_profit_closes_position_when_requested(self):
        plan = grid_live.apply_protection(prepare(), 5.0, None, close_on_target=True)
        result, _, engine, row = self._run(plan, position=0.0, liq=0.0, mark=100.0, stats={"realized_usdt": 6.0, "open_levels": 0})
        self.assertEqual((result, engine.status), ("TAKE_PROFIT", EMERGENCY_CLOSED))
        self.assertIn("pozisyon kapatıldı", row["reason"])

if __name__ == "__main__":
    unittest.main()


class TestDemoRunner(unittest.TestCase):
    """Demo hesap: aynı motor, ayrı defter; canlı kilidi uygulanmaz, Demo risk politikası kullanılır."""

    def test_demo_runner_is_demo_account_and_not_blocked_by_live_lock(self):
        runner = grid_live.LiveGridRunner(GridStore(":memory:", check_same_thread=False), account="DEMO")
        self.assertEqual(runner.account, "DEMO")
        self.assertFalse(runner.blocked_check({"real_trading_locked": True, "emergency": {"active": True}}))

    def test_demo_policy_reads_user_settings_with_safe_defaults(self):
        policy = grid_live.demo_policy({"max_loss_per_trade": 7, "daily_loss_limit": "bad", "max_positions": 1})
        self.assertEqual(policy["max_loss_per_trade"], 7.0)
        self.assertEqual(policy["daily_loss_limit"], 30.0)
        self.assertGreater(policy["max_positions"], grid_live.MAX_GRIDS_PER_USER)
        self.assertEqual(policy["max_total_exposure_usdt"], grid_live.DEMO_MAX_EXPOSURE_USDT)
        self.assertEqual(grid_live.demo_policy(None)["daily_loss_limit"], 30.0)

    def test_demo_flag_defaults_on_and_can_be_turned_off(self):
        import os
        old = os.environ.pop(grid_live.DEMO_ENV_FLAG, None)
        try:
            self.assertTrue(grid_live.demo_enabled())
            os.environ[grid_live.DEMO_ENV_FLAG] = "0"
            self.assertFalse(grid_live.demo_enabled())
        finally:
            os.environ.pop(grid_live.DEMO_ENV_FLAG, None)
            if old is not None:
                os.environ[grid_live.DEMO_ENV_FLAG] = old


class TestProtection(unittest.TestCase):
    """Hedef kâr / zarar sınırı: sunucu döngüsünde uygulanır."""

    def test_apply_protection_validates_and_lands_in_spec_meta(self):
        plan = grid_live.apply_protection(prepare(), 5.0, 8.0)
        self.assertEqual((plan["take_profit_usdt"], plan["loss_limit_usdt"]), (5.0, 8.0))
        meta = grid_live.grid_spec_from_plan("GID1", SYM, plan).meta
        self.assertEqual((meta["take_profit_usdt"], meta["loss_limit_usdt"]), (5.0, 8.0))
        self.assertNotIn("take_profit_usdt", grid_live.apply_protection(prepare(), None, None))
        for bad in (0, -1, float("nan")):
            with self.assertRaises(grid_live.LiveGridError):
                grid_live.apply_protection(prepare(), bad, None)

    def _runner(self):
        ex = ExchangeFake()
        ex.set_price(SYM, 100.0)
        api = FakeBinance(ex)
        runner = grid_live.LiveGridRunner(GridStore(":memory:", check_same_thread=False))
        runner._record = lambda *a, **k: None
        runner.blocked_check = lambda s: False
        runner._ensure_loop = lambda *a, **k: None
        return ex, api, runner

    def _guard(self, plan, stats, upnl=0.0):
        ex, api, runner = self._runner()
        api.upnl = upnl
        state: dict[str, Any] = {"real_trading_locked": False, "emergency": {}, "events": [], "policy": POLICY}
        original = grid_live.grid_stats

        async def flow():
            adapter = BinanceLiveGridAdapter(api, asyncio.get_running_loop(), timeout=5)
            await runner.start(None, state, adapter, plan, SYM, user_id="u1", owner=False)
            engine = next(iter(runner._engines.values()))
            grid_live.grid_stats = lambda spec, orders: stats
            try:
                result = await asyncio.to_thread(runner._guard_once, engine, state)
            finally:
                grid_live.grid_stats = original
            return result, engine.status, runner.status_payload("u1", owner=False)["grids"][0]
        return asyncio.run(flow())

    def test_take_profit_pauses_with_reason(self):
        plan = grid_live.apply_protection(prepare(), 5.0, None)
        result, status, row = self._guard(plan, {"realized_usdt": 6.0, "open_levels": 0})
        self.assertEqual((result, status), ("TAKE_PROFIT", PAUSED))
        self.assertIn("Hedef kâr", row["reason"])

    def test_below_target_does_nothing(self):
        plan = grid_live.apply_protection(prepare(), 5.0, 8.0)
        result, status, _ = self._guard(plan, {"realized_usdt": 1.0, "open_levels": 0})
        self.assertEqual((result, status), ("", RUNNING))

    def test_loss_limit_uses_realized_plus_unrealized_and_emergency_closes(self):
        plan = grid_live.apply_protection(prepare(), None, 8.0)
        result, status, row = self._guard(plan, {"realized_usdt": -2.0, "open_levels": 1}, upnl=-7.0)
        self.assertEqual(result, "LOSS_LIMIT")
        self.assertEqual(status, EMERGENCY_CLOSED)
        self.assertIn("Zarar sınırı", row["reason"])

    def test_loss_limit_not_hit_when_total_is_smaller(self):
        plan = grid_live.apply_protection(prepare(), None, 8.0)
        result, status, _ = self._guard(plan, {"realized_usdt": -2.0, "open_levels": 1}, upnl=-3.0)
        self.assertEqual((result, status), ("", RUNNING))
