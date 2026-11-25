"""Manuel pozisyona ek alım: miktar/giriş güncellemesi, TP büyütme, risk sınırları, red ve stop doğrulaması."""
import asyncio
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import v25_execution as execution  # noqa: E402
from test_manual_order_hardening import FakeExchange, ManualActionBase, open_manual_plan  # noqa: E402


class AddExchange(FakeExchange):
    """Non-reduceOnly market emri pozisyonu büyütür ve giriş fiyatını ağırlıklı ortalar."""

    def __init__(self, *args, leverage="10", reject=False, unknown=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.leverage, self.reject, self.unknown = leverage, reject, unknown

    async def signed(self, method, path, params=None):
        params = dict(params or {})
        if (method, path) == ("GET", "/fapi/v3/positionRisk"):
            self.calls.append((method, path, params))
            return [{"symbol": "BTCUSDT", "positionAmt": str(self.amount), "markPrice": self.mark, "entryPrice": self.entry, "leverage": self.leverage}]
        if (method, path) == ("POST", "/fapi/v1/order") and params.get("reduceOnly") != "true":
            self.calls.append((method, path, params))
            if self.reject:
                raise execution.LiveExchangeError("Margin yetersiz.", http_status=400)
            if self.unknown:
                raise execution.LiveExchangeError("zaman aşımı", unknown_execution=True)
            quantity = Decimal(params["quantity"])
            old = abs(self.amount)
            self.entry = str((Decimal(self.entry) * old + Decimal(self.mark) * quantity) / (old + quantity))
            self.amount += quantity if self.amount > 0 else -quantity
            self.orders.append(params)
            return {"orderId": 91, "status": "FILLED", "clientOrderId": params["newClientOrderId"]}
        return await super().signed(method, path, params)


class ManualAddTests(ManualActionBase):
    def setUp(self):
        super().setUp()
        patcher = patch.object(execution, "find_order", AsyncMock(return_value=None))
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, **kwargs):
        exchange = AddExchange(**kwargs)
        direction = kwargs.get("direction", "LONG")
        state, plan = open_manual_plan(exchange, direction=direction, stop_loss="99" if direction == "LONG" else "101")
        state["policy"] = {**state["policy"], "max_loss_per_trade": 25}
        return exchange, state, plan

    def add(self, exchange, state, plan, margin=10):
        return asyncio.run(execution.manual_add_to_position(exchange, state, plan, margin_usdt=margin))

    def test_add_increases_position_and_resizes_targets(self):
        exchange, state, plan = self.make()
        result = self.add(exchange, state, plan, margin=10.5)  # 10.5*10/105 = 1.0 adet
        order = exchange.orders[0]
        self.assertEqual((order["type"], order["side"], order["quantity"]), ("MARKET", "BUY", "1"))
        self.assertNotIn("reduceOnly", order)
        self.assertEqual(result["added_quantity"], "1")
        self.assertEqual(plan["quantity"], "2")
        self.assertEqual(plan["requested_quantity"], "2")  # sahiplik kontrolü pozisyon > beklenen görmesin
        self.assertEqual(Decimal(plan["entry_price"]), Decimal("102.5"))
        by_client = {row["clientAlgoId"]: row for row in exchange.algos.values()}
        self.assertEqual(by_client[execution.client_id_for("TP12", plan["intent_id"])]["quantity"], "1.2")
        self.assertEqual(by_client[execution.client_id_for("TP2", plan["intent_id"])]["quantity"], "0.4")
        self.assertEqual(len(self.stops(exchange)), 1)  # stop tam-kapatma, dokunulmadı
        self.assertEqual(plan["add_count"], 1)
        self.assertTrue(any(event.get("kind") == "MANUAL_ADD" for event in state["events"]))
        # sahiplik doğrulaması yeni toplamla geçer
        position = asyncio.run(execution.live_plan_position(exchange, "BTCUSDT"))
        self.assertTrue(execution.confirm_live_plan_provenance({**plan, "entry_order_id": 1, "entry_client_order_id": "x"}, position))

    def test_short_add_sells(self):
        exchange, state, plan = self.make(entry="100", mark="95", direction="SHORT")
        self.add(exchange, state, plan, margin=9.5)
        self.assertEqual(exchange.orders[0]["side"], "SELL")

    def test_exchange_rejection_rolls_back_expected_quantity(self):
        exchange, state, plan = self.make(reject=True)
        before = plan.get("requested_quantity")
        with self.assertRaises(execution.ManualPlanActionError) as raised:
            self.add(exchange, state, plan)
        self.assertIn("reddedildi", str(raised.exception))
        self.assertEqual(plan.get("requested_quantity"), before)
        self.assertEqual(plan.get("add_count", 0), 0)
        self.assertEqual(plan.get("add_client_order_ids"), [])

    def test_unknown_result_keeps_expected_quantity_raised(self):
        exchange, state, plan = self.make(unknown=True)
        with self.assertRaises(execution.ManualPlanActionError) as raised:
            self.add(exchange, state, plan, margin=10.5)
        self.assertEqual(raised.exception.http_status, 502)
        self.assertEqual(plan["requested_quantity"], "2")

    def test_loss_limit_blocks_before_any_order(self):
        exchange, state, plan = self.make()
        state["policy"]["max_loss_per_trade"] = 3
        with self.assertRaises(execution.ManualPlanActionError) as raised:
            self.add(exchange, state, plan, margin=10.5)
        self.assertIn("zarar", str(raised.exception))
        self.assertEqual(exchange.orders, [])
        self.assertNotIn("requested_quantity", plan)

    def test_margin_ceiling_blocks_before_any_order(self):
        exchange, state, plan = self.make()
        with patch.object(execution, "HARD_MAX_MARGIN_USDT", 20.0):
            with self.assertRaises(execution.ManualPlanActionError) as raised:
                self.add(exchange, state, plan, margin=15)
        self.assertIn("tavan", str(raised.exception))
        self.assertEqual(exchange.orders, [])

    def test_too_small_add_is_rejected(self):
        exchange, state, plan = self.make(leverage="1")
        with self.assertRaises(execution.ManualPlanActionError):
            self.add(exchange, state, plan, margin=5)  # 5/105 = 0.047 adet -> notional 5.0 ama min_qty ok? 0.047*105=4.99 < 5
        self.assertEqual(exchange.orders, [])

    def test_missing_stop_after_fill_triggers_safety_close(self):
        exchange, state, plan = self.make()
        original = AddExchange.signed

        async def signed(method, path, params=None):
            result = await original(exchange, method, path, params)
            if (method, path) == ("POST", "/fapi/v1/order") and (params or {}).get("reduceOnly") != "true":
                exchange.algos.pop(plan["stop_algo_id"], None)  # stop düştü
            return result

        exchange.signed = signed
        with patch.object(execution, "close_tracked_symbol", AsyncMock(return_value={"orderId": 5})) as close:
            with self.assertRaises(execution.ManualPlanActionError) as raised:
                self.add(exchange, state, plan, margin=10.5)
        self.assertIn("güvenlik", str(raised.exception))
        self.assertEqual(close.await_count, 1)

    def test_non_manual_and_no_stop_plans_refused(self):
        exchange, state, plan = self.make()
        plan["source"] = "V25_AUTO"
        with self.assertRaises(execution.ManualPlanActionError):
            self.add(exchange, state, plan)
        self.assertEqual(exchange.orders, [])
        exchange, state, plan = self.make()
        with patch.object(execution, "plan_is_user_no_stop", return_value=True):
            with self.assertRaises(execution.ManualPlanActionError):
                self.add(exchange, state, plan)
        self.assertEqual(exchange.orders, [])
