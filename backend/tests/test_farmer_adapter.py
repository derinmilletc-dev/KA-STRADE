"""KAIS FARMER C1 — adaptör/altyapı testleri (ağ yok, fastapi gerekmez).
Çalıştırma: cd backend && python -B -m unittest tests.test_farmer_adapter -v"""
from __future__ import annotations

import asyncio
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.farmer_engine.ids import (MAX_CLIENT_ID_LENGTH, farmer_client_order_id, farmer_grid_prefix,  # noqa: E402
                                   is_farmer_client_id)
from app.grid_engine import live_adapter as la  # noqa: E402
from app.grid_engine.engine import GridEngine, client_order_id  # noqa: E402
from app.grid_engine.fake_exchange import FakeExchange  # noqa: E402
from app.grid_engine.ports import (BUY, CANCELED, FILLED, LONG, NEW, SELL, SHORT, STOP_MARKET, TAKE_PROFIT_MARKET,  # noqa: E402
                                   WOULD_TRIGGER_IMMEDIATELY, ExchangeRejected)

SYM = "BTCUSDT"


class FakeClient:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.algo_rows: list[dict] = []

    async def signed(self, method, path, params=None):
        self.calls.append((method, path, dict(params or {})))
        if path == "/fapi/v1/openAlgoOrders":
            return list(self.algo_rows)
        if path == "/fapi/v1/algoOrder" and method == "POST":
            return {"algoId": 77, "algoStatus": "NEW", "clientAlgoId": (params or {}).get("clientAlgoId")}
        if path == "/fapi/v1/algoOrder" and method == "DELETE":
            self.canceled = [{**row, "algoStatus": "CANCELED"} for row in self.algo_rows]
            self.algo_rows = []
            return {}
        if path == "/fapi/v1/allAlgoOrders":
            return list(getattr(self, "canceled", []))
        if path == "/fapi/v1/order" and method == "POST":
            return {"orderId": 1, "clientOrderId": params["newClientOrderId"], "symbol": SYM, "side": params["side"],
                    "type": params["type"], "status": "NEW", "price": params.get("price", "0"), "origQty": params["quantity"],
                    "executedQty": "0", "reduceOnly": False}
        return {}


class AdapterBase(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.client = FakeClient()
        self.adapter = la.BinanceLiveGridAdapter(self.client, self.loop, timeout=5)

    def tearDown(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(2)
        self.loop.close()


class TestFarmerIds(unittest.TestCase):
    def test_prefix_does_not_collide_with_grid_or_dca(self):
        fid = farmer_client_order_id("F1abc", LONG, 0, "E")
        self.assertTrue(fid.startswith("kf-"))
        self.assertFalse(fid.startswith(("kg-", "kd-")))
        self.assertTrue(is_farmer_client_id(fid))
        self.assertFalse(is_farmer_client_id(client_order_id("G1", LONG, 0, "E")))

    def test_length_limit(self):
        fid = farmer_client_order_id("x" * 80, SHORT, 3, "T", 12345)
        self.assertLessEqual(len(fid), MAX_CLIENT_ID_LENGTH)

    def test_roles_read_by_existing_parser(self):
        for role in "ETSC":
            self.assertEqual(la.order_role(farmer_client_order_id("F1", LONG, 0, role, 2)), role)

    def test_grid_prefix_matches_ids(self):
        self.assertTrue(farmer_client_order_id("F1", LONG, 0, "E").startswith(farmer_grid_prefix("F1")))

    def test_ids_are_unique_per_slot_side_role(self):
        ids = {farmer_client_order_id(g, s, 0, r, 0) for g in ("F1", "F2") for s in (LONG, SHORT) for r in "ETSC"}
        self.assertEqual(len(ids), 16)


class TestAlgoRouting(unittest.TestCase):
    def test_farmer_target_and_stop_are_algo(self):
        self.assertTrue(la.is_algo_order(farmer_client_order_id("F1", LONG, 0, "T")))
        self.assertTrue(la.is_algo_order(farmer_client_order_id("F1", LONG, 0, "S")))

    def test_farmer_entry_and_close_are_normal(self):
        self.assertFalse(la.is_algo_order(farmer_client_order_id("F1", LONG, 0, "E")))
        self.assertFalse(la.is_algo_order(farmer_client_order_id("F1", LONG, 0, "C")))

    def test_grid_and_dca_targets_stay_on_normal_endpoint(self):
        self.assertFalse(la.is_algo_order("kg-G1-L00T0"))
        self.assertFalse(la.is_algo_order("kd-D1-L00T0"))

    def test_grid_and_dca_stops_still_algo(self):
        self.assertTrue(la.is_algo_order("kg-G1-L00S0"))
        self.assertTrue(la.is_algo_order("kd-D1-S01S0"))


class TestLiveAdapterTakeProfit(AdapterBase):
    def test_tp_market_sent_to_algo_endpoint_with_reduce_only(self):
        info = self.adapter.place_order(client_id="kf-F1-L00T0", symbol=SYM, side=SELL, type=TAKE_PROFIT_MARKET, qty=0.5,
                                        stop_price=101.5, reduce_only=True)
        method, path, params = self.client.calls[-1]
        self.assertEqual((method, path), ("POST", "/fapi/v1/algoOrder"))
        self.assertEqual(params["type"], "TAKE_PROFIT_MARKET")
        self.assertEqual(params["algoType"], "CONDITIONAL")
        self.assertEqual(params["reduceOnly"], "true")
        self.assertEqual(params["quantity"], "0.5")
        self.assertEqual(params["triggerPrice"], "101.5")
        self.assertEqual(params["workingType"], "MARK_PRICE")
        self.assertNotIn("closePosition", params)
        self.assertEqual(params["clientAlgoId"], "kf-F1-L00T0")
        self.assertEqual(info.type, TAKE_PROFIT_MARKET)
        self.assertTrue(info.reduce_only)

    def test_stop_market_unchanged(self):
        info = self.adapter.place_order(client_id="kf-F1-L00S0", symbol=SYM, side=SELL, type=STOP_MARKET, qty=0.5,
                                        stop_price=95, reduce_only=True)
        self.assertEqual(self.client.calls[-1][2]["type"], "STOP_MARKET")
        self.assertEqual(info.type, STOP_MARKET)

    def test_grid_limit_target_uses_normal_order_endpoint(self):
        self.adapter.place_order(client_id="kg-G1-L00T0", symbol=SYM, side=SELL, type="LIMIT", qty=0.5, price=101, reduce_only=True)
        self.assertEqual(self.client.calls[-1][1], "/fapi/v1/order")

    def test_get_and_cancel_farmer_target_use_algo_endpoints(self):
        self.client.algo_rows = [{"clientAlgoId": "kf-F1-L00T0", "symbol": SYM, "side": SELL, "orderType": "TAKE_PROFIT_MARKET",
                                  "triggerPrice": "101.5", "quantity": "0.5", "reduceOnly": "true", "algoStatus": "NEW"}]
        got = self.adapter.get_order(SYM, "kf-F1-L00T0")
        self.assertEqual(got.type, TAKE_PROFIT_MARKET)
        self.assertEqual(self.client.calls[-1][1], "/fapi/v1/openAlgoOrders")
        self.adapter.cancel_order(SYM, "kf-F1-L00T0")
        self.assertIn(("DELETE", "/fapi/v1/algoOrder"), [(c[0], c[1]) for c in self.client.calls])

    def test_open_algo_row_without_type_defaults_to_stop(self):
        self.client.algo_rows = [{"clientAlgoId": "kg-G1-L00S0", "symbol": SYM, "side": SELL, "triggerPrice": "95", "quantity": "1"}]
        self.assertEqual(self.adapter.get_order(SYM, "kg-G1-L00S0").type, STOP_MARKET)


class TestFakeTakeProfit(unittest.TestCase):
    def setUp(self):
        self.ex = FakeExchange()
        self.ex.prices[SYM] = 100.0
        self.ex.set_position(SYM, 1.0)

    def test_sell_tp_rejected_when_mark_already_above(self):
        self.ex.prices[SYM] = 102.0
        with self.assertRaises(ExchangeRejected) as cm:
            self.ex.place_order(client_id="kf-F1-L00T0", symbol=SYM, side=SELL, type=TAKE_PROFIT_MARKET, qty=1.0,
                                stop_price=101.0, reduce_only=True)
        self.assertEqual(cm.exception.code, WOULD_TRIGGER_IMMEDIATELY)

    def test_buy_tp_rejected_when_mark_already_below(self):
        self.ex.set_position(SYM, -1.0)
        with self.assertRaises(ExchangeRejected):
            self.ex.place_order(client_id="kf-F1-S00T0", symbol=SYM, side=BUY, type=TAKE_PROFIT_MARKET, qty=1.0,
                                stop_price=101.0, reduce_only=True)

    def test_long_target_fills_reduce_only_at_trigger(self):
        self.ex.place_order(client_id="kf-F1-L00T0", symbol=SYM, side=SELL, type=TAKE_PROFIT_MARKET, qty=1.0,
                            stop_price=101.0, reduce_only=True)
        self.ex.set_price(SYM, 100.5)
        self.assertEqual(self.ex.get_order(SYM, "kf-F1-L00T0").status, NEW)
        self.ex.set_price(SYM, 101.2)
        self.assertEqual(self.ex.get_order(SYM, "kf-F1-L00T0").status, FILLED)
        self.assertAlmostEqual(self.ex.position(SYM), 0.0)

    def test_slippage_goes_against_the_trader(self):
        self.ex.tp_slippage = 0.3
        self.ex.place_order(client_id="kf-F1-L00T0", symbol=SYM, side=SELL, type=TAKE_PROFIT_MARKET, qty=1.0,
                            stop_price=101.0, reduce_only=True)
        self.ex.set_price(SYM, 101.0)
        self.assertAlmostEqual(self.ex.get_order(SYM, "kf-F1-L00T0").avg_price, 100.7, places=6)

    def test_target_without_position_is_canceled_not_opening(self):
        self.ex.place_order(client_id="kf-F1-L00T0", symbol=SYM, side=SELL, type=TAKE_PROFIT_MARKET, qty=1.0,
                            stop_price=101.0, reduce_only=True)
        self.ex.set_position(SYM, 0.0)
        self.ex.set_price(SYM, 102.0)
        self.assertEqual(self.ex.get_order(SYM, "kf-F1-L00T0").status, CANCELED)
        self.assertAlmostEqual(self.ex.position(SYM), 0.0)

    def test_reduce_only_larger_than_position_rejected_at_placement(self):
        with self.assertRaises(ExchangeRejected):
            self.ex.place_order(client_id="kf-F1-L00T0", symbol=SYM, side=SELL, type=TAKE_PROFIT_MARKET, qty=2.0,
                                stop_price=101.0, reduce_only=True)

    def test_quantity_clamped_when_position_shrinks_before_trigger(self):
        self.ex.place_order(client_id="kf-F1-L00T0", symbol=SYM, side=SELL, type=TAKE_PROFIT_MARKET, qty=1.0,
                            stop_price=101.0, reduce_only=True)
        self.ex.set_position(SYM, 0.4)
        self.ex.set_price(SYM, 101.5)
        self.assertAlmostEqual(self.ex.position(SYM), 0.0)


class TestMinLevelsHook(unittest.TestCase):
    def test_default_unchanged(self):
        self.assertEqual(GridEngine.MIN_LEVELS, 2)

    def test_subclass_may_lower_minimum(self):
        class One(GridEngine):
            MIN_LEVELS = 1
        self.assertEqual(One.MIN_LEVELS, 1)


if __name__ == "__main__":
    unittest.main()
