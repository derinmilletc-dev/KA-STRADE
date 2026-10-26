"""Manuel emir sertleştirmesi: TP2 gerçek emir, küçük pozisyon ön kontrolü, LIMIT dolumunda erken koruma."""
import asyncio
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


sys.path.insert(0, str(Path(__file__).parents[1]))

from app import v25_execution as execution  # noqa: E402
from test_v25_partial_fills import PartialFillClient, make_plan  # noqa: E402


def manual_plan(**overrides):
    base = make_plan(
        source="MANUAL", entry_order_status="FILLED", quantity="0.010",
        initial_risk_usdt="10", min_notional="5",
    )
    base.update(overrides)
    return base


class FilledClient(PartialFillClient):
    def __init__(self, plan, quantity="0.010", fail_client_ids=()):
        super().__init__(plan, quantity=quantity)
        self.fail_client_ids = set(fail_client_ids)

    async def signed(self, method, path, params=None):
        if (method, path) == ("POST", "/fapi/v1/algoOrder") and (params or {}).get("clientAlgoId") in self.fail_client_ids:
            self.calls.append((method, path, dict(params or {})))
            raise execution.LiveExchangeError("Binance reddetti.", http_status=400)
        return await super().signed(method, path, params)


class ManualTp2Tests(unittest.TestCase):
    def setUp(self):
        for target, value in (("persist_state", None), ("STOP_RETRY_BASE_SECONDS", 0)):
            patcher = patch.object(execution, target, value) if value is not None else patch.object(execution, target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def install(self, client, plan):
        state = execution.initial_state()
        state["plans"] = {plan["id"]: plan}
        asyncio.run(execution.install_protection(client, state, plan))
        return state

    def tp_orders(self, client):
        return {row["clientAlgoId"]: row for row in client.algos if row.get("type") == "TAKE_PROFIT_MARKET"}

    def test_manual_plan_places_tp2_as_real_reduce_only_order(self):
        plan = manual_plan()
        client = FilledClient(plan)
        self.install(client, plan)
        orders = self.tp_orders(client)
        tp1 = orders[execution.client_id_for("TP12", plan["intent_id"])]
        tp2 = orders[execution.client_id_for("TP2", plan["intent_id"])]
        tp3 = orders[execution.client_id_for("TP3", plan["intent_id"])]
        self.assertEqual((tp1["triggerPrice"], tp1["quantity"]), ("51000", "0.006"))
        self.assertEqual((tp2["triggerPrice"], tp2["quantity"], tp2["reduceOnly"]), ("52000", "0.002", "true"))
        self.assertNotIn("closePosition", tp2)
        self.assertEqual((tp3["triggerPrice"], tp3["closePosition"]), ("53000", "true"))
        self.assertEqual(plan["tp2_status"], "PLACED")
        self.assertIn(plan["tp2_algo_id"], plan["protection_ids"])
        self.assertEqual(plan["protection_state"], "MATCHED")
        self.assertEqual(plan["status"], "KORUMA AKTİF")

    def test_non_manual_plans_are_unchanged(self):
        for source in ("V25_AUTO",):
            with self.subTest(source=source):
                plan = manual_plan(source=source)
                client = FilledClient(plan)
                self.install(client, plan)
                self.assertNotIn(execution.client_id_for("TP2", plan["intent_id"]), self.tp_orders(client))
                self.assertNotIn("tp2_status", plan)

    def test_too_small_tp2_merges_into_tp3_without_lock(self):
        plan = manual_plan(min_notional="150")  # TP1 0.006*50000=300 geçer, TP2 0.002*50000=100 geçmez
        client = FilledClient(plan)
        state = self.install(client, plan)
        orders = self.tp_orders(client)
        self.assertNotIn(execution.client_id_for("TP2", plan["intent_id"]), orders)
        self.assertIn(execution.client_id_for("TP3", plan["intent_id"]), orders)
        self.assertEqual(plan["tp2_status"], "MERGED_INTO_TP3")
        self.assertEqual(plan["protection_state"], "MATCHED")
        self.assertFalse(state.get("reconciliation_required"))

    def test_rejected_tp2_never_blocks_stop_or_tp3(self):
        plan = manual_plan()
        client = FilledClient(plan, fail_client_ids={execution.client_id_for("TP2", plan["intent_id"])})
        state = self.install(client, plan)
        orders = self.tp_orders(client)
        self.assertIn(execution.client_id_for("TP3", plan["intent_id"]), orders)
        self.assertEqual(plan["tp2_status"], "NOT_PLACED")
        self.assertEqual(plan["protection_state"], "MATCHED")
        self.assertFalse(state.get("reconciliation_required"))
        self.assertTrue(any(event.get("kind") == "MANUAL_TP2_NOT_PLACED" for event in state["events"]))

    def test_tp2_not_attempted_when_tp1_is_not_on_exchange(self):
        plan = manual_plan()
        client = FilledClient(plan, fail_client_ids={execution.client_id_for("TP12", plan["intent_id"])})
        self.install(client, plan)
        self.assertNotIn(execution.client_id_for("TP2", plan["intent_id"]), self.tp_orders(client))
        self.assertNotIn("tp2_status", plan)

    def test_tp2_display_follows_open_exchange_order(self):
        plan = manual_plan(provenance_state="CONFIRMED")
        client = FilledClient(plan)
        self.install(client, plan)
        with patch.object(execution, "live_plan_can_mutate", return_value=True):
            view = execution.exchange_protection_view(plan, client.snapshot())
            self.assertTrue(view["tp2"])
            snapshot = client.snapshot()
            snapshot["open_algo_orders"] = [row for row in snapshot["open_algo_orders"] if row["client_algo_id"] != plan["tp2_client_id"]]
            self.assertFalse(execution.exchange_protection_view(plan, snapshot)["tp2"])


RULES = {"tick": Decimal("0.1"), "step": Decimal("0.001"), "min_qty": Decimal("0.001"), "min_notional": Decimal("5"), "max_qty": Decimal("1000")}


class ManualTargetSizeTests(unittest.TestCase):
    def spec(self, manual_targets, **rules):
        order = execution.LiveOrderRequest(
            symbol="BTCUSDT", direction="LONG", order_type="MARKET", margin_usdt=100, leverage=10,
            stop_loss=49500, tp1=50500, tp2=51000, tp3=51500,
        )
        policy = {**execution.initial_state()["policy"], "allow_long": True, "allow_short": True, "allowed_symbols": [],
                  "max_margin_per_trade": 100, "max_leverage": 10, "max_loss_per_trade": 5, "minimum_net_reward_usdt": 0}
        with patch.object(execution, "ticker_price", AsyncMock(return_value=Decimal("50000"))), \
                patch.object(execution, "live_symbol_rules", AsyncMock(return_value={**RULES, **rules})):
            return asyncio.run(execution.build_live_spec(object(), order, policy, manual_targets=manual_targets))

    def test_target_size_helper(self):
        self.assertEqual(execution.manual_target_sizes_ok(Decimal("0.010"), Decimal("50000"), RULES), (True, True))
        self.assertEqual(execution.manual_target_sizes_ok(Decimal("0.010"), Decimal("50000"), {**RULES, "min_notional": Decimal("150")}), (True, False))
        self.assertEqual(execution.manual_target_sizes_ok(Decimal("0.010"), Decimal("50000"), {**RULES, "min_notional": Decimal("400")}), (False, False))
        self.assertEqual(execution.manual_target_sizes_ok(Decimal("0.001"), Decimal("50000"), RULES), (False, False))

    def test_manual_order_rejected_when_tp1_cannot_be_placed(self):
        base = self.spec(False)
        notional = Decimal(base["quantity"]) * Decimal("50000")
        limit = (notional * Decimal("0.8")).quantize(Decimal("0.01"))  # tam emir geçer, %60'lık TP1 geçmez
        with self.assertRaises(execution.LiveExchangeError) as raised:
            self.spec(True, min_notional=limit)
        self.assertEqual(raised.exception.check_key, "target_size")
        self.assertIn("Pozisyon çok küçük", str(raised.exception))
        # Manuel olmayan yol (otomasyon) değişmedi: aynı kurallarla kabul edilir.
        self.assertNotIn("tp2_on_exchange", self.spec(False, min_notional=limit))

    def test_manual_order_reports_tp2_merge(self):
        base = self.spec(False)
        notional = Decimal(base["quantity"]) * Decimal("50000")
        spec = self.spec(True, min_notional=(notional * Decimal("0.3")).quantize(Decimal("0.01")))
        self.assertIs(spec["tp2_on_exchange"], False)
        self.assertIs(self.spec(True)["tp2_on_exchange"], True)


def fill_event(client_id, status="FILLED", execution_type="TRADE", symbol="BTCUSDT"):
    return {"e": "ORDER_TRADE_UPDATE", "E": 1, "o": {"s": symbol, "c": client_id, "x": execution_type, "X": status, "i": 42}}


class EarlyProtectionTests(unittest.TestCase):
    def state_with_limit(self, **overrides):
        plan = manual_plan(**{"order_type": "LIMIT", "status": "DOLUM BEKLİYOR", **overrides})
        state = execution.initial_state()
        state["plans"] = {plan["id"]: plan}
        return state, plan

    def test_detects_only_full_fill_of_own_manual_limit_entry(self):
        state, plan = self.state_with_limit()
        own = plan["entry_client_order_id"]
        self.assertEqual(execution.manual_limit_entry_filled(state, fill_event(own)), "BTCUSDT")
        self.assertIsNone(execution.manual_limit_entry_filled(state, fill_event(own, status="PARTIALLY_FILLED")))
        self.assertIsNone(execution.manual_limit_entry_filled(state, fill_event(own, execution_type="NEW", status="NEW")))
        self.assertIsNone(execution.manual_limit_entry_filled(state, fill_event(execution.client_id_for("ENTRY", "other"))))
        self.assertIsNone(execution.manual_limit_entry_filled(state, fill_event(own, symbol="ETHUSDT")))
        self.assertIsNone(execution.manual_limit_entry_filled(state, {"e": "ACCOUNT_UPDATE"}))
        for change in ({"source": "V25_AUTO"}, {"order_type": "MARKET"}, {"status": "KORUMA AKTİF"}):
            with self.subTest(change=change):
                other_state, other_plan = self.state_with_limit(**change)
                self.assertIsNone(execution.manual_limit_entry_filled(other_state, fill_event(other_plan["entry_client_order_id"])))

    def run_early(self, positions):
        state, _plan = self.state_with_limit()
        state["lock"] = asyncio.Lock()
        application = SimpleNamespace(state=SimpleNamespace(v25_execution=state))
        reconcile = AsyncMock()
        with patch.object(execution, "EARLY_PROTECTION_CHECK_DELAY_SECONDS", 0), \
                patch.object(execution, "client_for_with_credentials", return_value=object()), \
                patch.object(execution, "live_plan_position", AsyncMock(side_effect=positions)), \
                patch.object(execution, "reconcile", reconcile), \
                patch.object(execution, "persist_state"):
            result = asyncio.run(execution.early_protection_reconcile(application, ("k", "s"), "BTCUSDT"))
        return result, reconcile, state

    def test_runs_normal_reconcile_once_after_position_is_visible(self):
        result, reconcile, state = self.run_early([None, {"symbol": "BTCUSDT"}])
        self.assertTrue(result)
        self.assertEqual(reconcile.await_count, 1)
        self.assertTrue(any(event.get("kind") == "EARLY_PROTECTION_RECONCILE" for event in state["events"]))

    def test_never_reconciles_before_position_is_visible(self):
        result, reconcile, _state = self.run_early([None] * execution.EARLY_PROTECTION_POSITION_CHECKS)
        self.assertFalse(result)
        self.assertEqual(reconcile.await_count, 0)

    def test_errors_are_swallowed_and_normal_loop_stays_backup(self):
        result, reconcile, _state = self.run_early(execution.LiveExchangeError("ağ hatası"))
        self.assertFalse(result)
        self.assertEqual(reconcile.await_count, 0)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------------------------------------------
# Hedef payı seçimi, LIMIT süresi, stop/hedef değiştirme, kısmi kapatma, başa çekme koruması
# ---------------------------------------------------------------------------------------------------------------

class SplitAndExpiryTests(unittest.TestCase):
    def spec(self, **order_fields):
        fields = dict(symbol="BTCUSDT", direction="LONG", order_type="MARKET", margin_usdt=100, leverage=10,
                      stop_loss=49500, tp1=50500, tp2=51000, tp3=51500)
        fields.update(order_fields)
        policy = {**execution.initial_state()["policy"], "allow_long": True, "allow_short": True, "allowed_symbols": [],
                  "max_margin_per_trade": 100, "max_leverage": 10, "max_loss_per_trade": 5, "minimum_net_reward_usdt": 0}
        with patch.object(execution, "ticker_price", AsyncMock(return_value=Decimal("50000"))), \
                patch.object(execution, "live_symbol_rules", AsyncMock(return_value=dict(RULES))):
            return asyncio.run(execution.build_live_spec(object(), execution.LiveOrderRequest(**fields), policy, manual_targets=True))

    def test_selected_split_is_carried_in_spec(self):
        spec = self.spec(tp_split="40-30-30")
        self.assertEqual((Decimal(spec["tp1_fraction"]), Decimal(spec["tp2_fraction"])), (Decimal("0.4"), Decimal("0.3")))
        default = self.spec()
        self.assertEqual((Decimal(default["tp1_fraction"]), Decimal(default["tp2_fraction"])), (Decimal("0.6"), Decimal("0.2")))

    def test_unknown_split_is_rejected_by_schema(self):
        with self.assertRaises(Exception):
            execution.LiveOrderRequest(symbol="BTCUSDT", direction="LONG", margin_usdt=10, leverage=1,
                                       stop_loss=1, tp1=2, tp2=3, tp3=4, tp_split="90-5-5")

    def test_limit_expiry_sets_good_till_and_gtd(self):
        import time as _time
        before = _time.time()
        spec = self.spec(order_type="LIMIT", limit_price=50000, limit_expiry_minutes=60)
        self.assertGreaterEqual(spec["good_till_ms"], int((before + 3600) * 1000) - 1000)
        self.assertLessEqual(spec["good_till_ms"], int((_time.time() + 3600) * 1000) + 1000)
        self.assertNotIn("good_till_ms", self.spec(order_type="LIMIT", limit_price=50000))
        self.assertNotIn("good_till_ms", self.spec(limit_expiry_minutes=60))  # piyasa emrinde süre yok

        class Capture(execution.BinanceLiveClient):
            def __init__(self):
                self.params = None

            async def signed(self, method, path, params=None):
                self.params = dict(params or {})
                return {"orderId": 1}

        client = Capture()
        asyncio.run(execution.submit_entry(client, {**spec, "side": "BUY"}, "cid-1", test_only=True))
        self.assertEqual(client.params["timeInForce"], "GTD")
        self.assertEqual(client.params["goodTillDate"], spec["good_till_ms"])
        plain = self.spec(order_type="LIMIT", limit_price=50000)
        asyncio.run(execution.submit_entry(client, {**plain, "side": "BUY"}, "cid-2", test_only=True))
        self.assertEqual(client.params["timeInForce"], "GTC")
        self.assertNotIn("goodTillDate", client.params)

    def test_install_uses_plan_split(self):
        for target, value in (("persist_state", None), ("STOP_RETRY_BASE_SECONDS", 0)):
            patcher = patch.object(execution, target, value) if value is not None else patch.object(execution, target)
            patcher.start()
            self.addCleanup(patcher.stop)
        plan = manual_plan(tp1_fraction="0.4", tp2_fraction="0.3")
        client = FilledClient(plan)
        state = execution.initial_state()
        state["plans"] = {plan["id"]: plan}
        asyncio.run(execution.install_protection(client, state, plan))
        orders = {row["clientAlgoId"]: row for row in client.algos}
        self.assertEqual(orders[execution.client_id_for("TP12", plan["intent_id"])]["quantity"], "0.004")
        self.assertEqual(orders[execution.client_id_for("TP2", plan["intent_id"])]["quantity"], "0.003")

    def test_tp2_fraction_guard(self):
        self.assertEqual(execution.plan_tp2_fraction({}), Decimal("0.20"))
        self.assertEqual(execution.plan_tp2_fraction({"tp1_fraction": "0.5", "tp2_fraction": "0.3"}), Decimal("0.3"))
        self.assertEqual(execution.plan_tp2_fraction({"tp1_fraction": "0.5", "tp2_fraction": "0.6"}), Decimal("0.20"))
        self.assertEqual(execution.plan_tp2_fraction({"tp2_fraction": "abc"}), Decimal("0.20"))


class FakeExchange(execution.BinanceLiveClient):
    """Pozisyon + açık algo emirleri tutan sahte borsa (gerçek ağ yok)."""

    application = None
    time_offset_ms = 0

    def __init__(self, quantity="1", entry="100", mark="105", direction="LONG"):
        sign = Decimal(1) if direction == "LONG" else Decimal(-1)
        self.amount = Decimal(quantity) * sign
        self.entry, self.mark = entry, mark
        self.algos: dict[int, dict] = {}
        self.next_id = 500
        self.fail_post_prefixes: set[str] = set()
        self.calls = []
        self.orders = []

    def add_algo(self, **row):
        self.next_id += 1
        self.algos[self.next_id] = {"algoId": self.next_id, "algoStatus": "NEW", "workingType": "MARK_PRICE", **row}
        return self.next_id

    async def signed(self, method, path, params=None):
        params = dict(params or {})
        self.calls.append((method, path, params))
        if (method, path) == ("GET", "/fapi/v3/positionRisk"):
            return [{"symbol": "BTCUSDT", "positionAmt": str(self.amount), "markPrice": self.mark, "entryPrice": self.entry}]
        if (method, path) == ("GET", "/fapi/v1/openAlgoOrders"):
            return [dict(row) for row in self.algos.values()]
        if (method, path) == ("DELETE", "/fapi/v1/algoOrder"):
            self.algos.pop(int(params["algoId"]), None)
            return {"code": 200}
        if (method, path) == ("POST", "/fapi/v1/algoOrder"):
            if any(str(params.get("clientAlgoId", "")) == cid for cid in self.fail_post_prefixes):
                raise execution.LiveExchangeError("Binance reddetti.", http_status=400)
            if params.get("closePosition") == "true" and any(
                row.get("closePosition") == "true" and row.get("orderType") == params["type"] for row in self.algos.values()
            ):
                raise execution.LiveExchangeError("-4130 aynı yönde kapatma emri var.", http_status=400, exchange_code=-4130)
            row = {key: value for key, value in params.items() if key not in {"algoType", "priceProtect", "type"}}
            algo_id = self.add_algo(orderType=params["type"], **row)
            return {"algoId": algo_id}
        if (method, path) == ("POST", "/fapi/v1/order"):
            quantity = Decimal(params["quantity"])
            self.amount -= quantity if self.amount > 0 else -quantity
            self.orders.append(params)
            return {"orderId": 77, "status": "FILLED", "clientOrderId": params["newClientOrderId"]}
        if (method, path) == ("GET", "/fapi/v1/order"):
            return {}
        raise AssertionError(f"Beklenmeyen istek: {method} {path}")


def open_manual_plan(exchange: FakeExchange, direction="LONG", **extra):
    intent = "manual-intent"
    side = "SELL" if direction == "LONG" else "BUY"
    stop, targets = ("95", ["110", "115", "120"]) if direction == "LONG" else ("105", ["90", "85", "80"])
    plan = {
        "id": "manual-plan-1", "intent_id": intent, "symbol": "BTCUSDT", "direction": direction, "source": "MANUAL",
        "status": "KORUMA AKTİF", "provenance_state": "CONFIRMED", "protection_state": "MATCHED",
        "stop_loss": stop, "stop_client_id": execution.client_id_for("SL", intent), "targets": targets,
        "quantity": "1", "step": "0.001", "min_qty": "0.001", "min_notional": "5",
    }
    plan.update(extra)
    plan["stop_algo_id"] = exchange.add_algo(symbol="BTCUSDT", side=side, orderType="STOP_MARKET", triggerPrice=plan["stop_loss"],
                                             closePosition="true", clientAlgoId=plan["stop_client_id"])
    plan["tp12_algo_id"] = exchange.add_algo(symbol="BTCUSDT", side=side, orderType="TAKE_PROFIT_MARKET", triggerPrice=targets[0],
                                             quantity="0.6", reduceOnly="true", clientAlgoId=execution.client_id_for("TP12", intent))
    plan["tp2_algo_id"] = exchange.add_algo(symbol="BTCUSDT", side=side, orderType="TAKE_PROFIT_MARKET", triggerPrice=targets[1],
                                            quantity="0.2", reduceOnly="true", clientAlgoId=execution.client_id_for("TP2", intent))
    exchange.add_algo(symbol="BTCUSDT", side=side, orderType="TAKE_PROFIT_MARKET", triggerPrice=targets[2],
                      closePosition="true", clientAlgoId=execution.client_id_for("TP3", intent))
    state = execution.initial_state()
    state["policy"] = {**state["policy"], "max_loss_per_trade": 3}
    state["plans"] = {plan["id"]: plan}
    return state, plan


MANUAL_RULES = {"tick": Decimal("0.1"), "step": Decimal("0.001"), "min_qty": Decimal("0.001"), "min_notional": Decimal("5"), "max_qty": Decimal("1000")}


class ManualActionBase(unittest.TestCase):
    def setUp(self):
        for target, value in (("persist_state", None), ("STOP_RETRY_BASE_SECONDS", 0),
                              ("live_symbol_rules", AsyncMock(return_value=dict(MANUAL_RULES))),
                              ("live_plan_can_mutate", lambda plan: True)):
            patcher = patch.object(execution, target, value) if value is not None else patch.object(execution, target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def stops(self, exchange):
        return [row for row in exchange.algos.values() if row["orderType"] == "STOP_MARKET"]

    def update(self, exchange, state, plan, **kwargs):
        return asyncio.run(execution.manual_update_protection(exchange, state, plan,
                                                              stop_loss=kwargs.get("stop"), targets=kwargs.get("targets", {})))



class ManualPositionActionTests(ManualActionBase):
    def test_move_stop_closer_is_verified_and_replaces_old(self):
        exchange = FakeExchange()
        state, plan = open_manual_plan(exchange)
        old_algo = plan["stop_algo_id"]
        result = self.update(exchange, state, plan, stop=98)
        self.assertEqual(result["stop"], "MOVED")
        stops = self.stops(exchange)
        self.assertEqual(len(stops), 1)
        self.assertNotEqual(stops[0]["algoId"], old_algo)
        self.assertEqual(Decimal(stops[0]["triggerPrice"]), Decimal("98"))
        self.assertEqual(Decimal(plan["stop_loss"]), Decimal("98"))
        self.assertEqual(plan["stop_algo_id"], stops[0]["algoId"])
        self.assertEqual(plan["stop_client_id"], stops[0]["clientAlgoId"])
        self.assertEqual(plan["protection_state"], "MATCHED")

    def test_stop_on_profit_side_or_wider_than_risk_limit_is_rejected_untouched(self):
        for stop, message in ((106, "zarar tarafında"), (90, "risk sınırın")):
            with self.subTest(stop=stop):
                exchange = FakeExchange()
                state, plan = open_manual_plan(exchange)
                before = dict(exchange.algos)
                with self.assertRaises(execution.ManualPlanActionError) as raised:
                    self.update(exchange, state, plan, stop=stop)
                self.assertIn(message, str(raised.exception))
                self.assertEqual(exchange.algos, before)
                self.assertEqual(plan["stop_loss"], "95")

    def test_failed_new_stop_restores_old_stop(self):
        exchange = FakeExchange()
        state, plan = open_manual_plan(exchange)
        exchange.fail_post_prefixes.add(execution.client_id_for("SLMV1", plan["intent_id"]))
        with self.assertRaises(execution.ManualPlanActionError) as raised:
            self.update(exchange, state, plan, stop=98)
        self.assertIn("eski stop geri kuruldu", str(raised.exception))
        stops = self.stops(exchange)
        self.assertEqual(len(stops), 1)
        self.assertEqual(Decimal(stops[0]["triggerPrice"]), Decimal("95"))
        self.assertEqual(plan["stop_loss"], "95")
        self.assertEqual(plan["protection_state"], "MATCHED")

    def test_when_no_stop_can_be_installed_the_position_is_closed(self):
        exchange = FakeExchange()
        state, plan = open_manual_plan(exchange)
        exchange.fail_post_prefixes.update({execution.client_id_for("SLMV1", plan["intent_id"]), execution.client_id_for("SLRB1", plan["intent_id"])})
        close = AsyncMock(return_value={"orderId": 9})
        with patch.object(execution, "close_tracked_symbol", close):
            with self.assertRaises(execution.ManualPlanActionError) as raised:
                self.update(exchange, state, plan, stop=98)
        self.assertEqual(raised.exception.http_status, 502)
        self.assertEqual(close.await_count, 1)
        self.assertIn(execution.client_id_for("CLOSE", f"stopmove-{plan['id']}-1"), plan["close_client_order_ids"])

    def test_move_open_target_keeps_identity_and_quantity(self):
        exchange = FakeExchange()
        state, plan = open_manual_plan(exchange)
        result = self.update(exchange, state, plan, targets={2: 116})
        self.assertEqual(result["targets"], {"TP2": "MOVED"})
        tp2 = [row for row in exchange.algos.values() if row["clientAlgoId"] == execution.client_id_for("TP2", plan["intent_id"])]
        self.assertEqual(len(tp2), 1)
        self.assertEqual((Decimal(tp2[0]["triggerPrice"]), tp2[0]["quantity"]), (Decimal("116"), "0.2"))
        self.assertEqual(Decimal(plan["targets"][1]), Decimal("116"))
        self.assertEqual(plan["tp2_algo_id"], tp2[0]["algoId"])

    def test_target_rules(self):
        cases = (
            ({1: 104}, "kâr tarafında"),          # fiyatın altında
            ({2: 125}, "sırayla"),                # TP2 > TP3
        )
        for targets, message in cases:
            with self.subTest(targets=targets):
                exchange = FakeExchange()
                state, plan = open_manual_plan(exchange)
                with self.assertRaises(execution.ManualPlanActionError) as raised:
                    self.update(exchange, state, plan, targets=targets)
                self.assertIn(message, str(raised.exception))
        exchange = FakeExchange()
        state, plan = open_manual_plan(exchange)
        exchange.algos.pop(plan["tp12_algo_id"])  # TP1 gerçekleşmiş
        with self.assertRaises(execution.ManualPlanActionError) as raised:
            self.update(exchange, state, plan, targets={1: 111})
        self.assertIn("açık değil", str(raised.exception))

    def test_only_manual_and_verified_plans(self):
        exchange = FakeExchange()
        state, plan = open_manual_plan(exchange, source="ORIGINAL_V2_LIVE")
        with self.assertRaises(execution.ManualPlanActionError):
            self.update(exchange, state, plan, stop=98)
        state, plan = open_manual_plan(FakeExchange(), protection_state="UNKNOWN")
        with self.assertRaises(execution.ManualPlanActionError):
            self.update(FakeExchange(), state, plan, stop=98)

    def test_short_stop_move(self):
        exchange = FakeExchange(entry="100", mark="95", direction="SHORT")
        state, plan = open_manual_plan(exchange, direction="SHORT")
        result = self.update(exchange, state, plan, stop=102)
        self.assertEqual(result["stop"], "MOVED")
        self.assertEqual(Decimal(self.stops(exchange)[0]["triggerPrice"]), Decimal("102"))

    def test_partial_close_reduces_position_and_resizes_targets(self):
        exchange = FakeExchange()
        state, plan = open_manual_plan(exchange)
        result = asyncio.run(execution.manual_reduce_position(exchange, state, plan, percent=50))
        self.assertEqual((result["reduced_quantity"], result["remaining_quantity"]), ("0.5", "0.5"))
        order = exchange.orders[0]
        self.assertEqual((order["type"], order["reduceOnly"], order["quantity"]), ("MARKET", "true", "0.5"))
        self.assertIn(order["newClientOrderId"], plan["close_client_order_ids"])
        by_client = {row["clientAlgoId"]: row for row in exchange.algos.values()}
        self.assertEqual(by_client[execution.client_id_for("TP12", plan["intent_id"])]["quantity"], "0.3")
        self.assertEqual(by_client[execution.client_id_for("TP2", plan["intent_id"])]["quantity"], "0.1")
        self.assertEqual(plan["tp12_quantity"], "0.3")
        self.assertEqual(len(self.stops(exchange)), 1)  # stop tam-kapatma emri; dokunulmadı
        self.assertEqual(plan["quantity"], "1")  # plan miktarı değişmez (sahiplik/dolum kontrolleri bozulmasın)

    def test_partial_close_rejected_when_remainder_too_small(self):
        exchange = FakeExchange(quantity="0.08")
        state, plan = open_manual_plan(exchange)
        with self.assertRaises(execution.ManualPlanActionError):
            asyncio.run(execution.manual_reduce_position(exchange, state, plan, percent=75))  # kalan 0.02*105=2.1 < 5
        self.assertEqual(exchange.orders, [])

    def test_breakeven_never_worsens_a_stop_already_past_entry(self):
        exchange = FakeExchange(quantity="0.4")
        state, plan = open_manual_plan(exchange, stop_loss="101")
        exchange.algos.pop(plan["tp12_algo_id"])  # TP1 doldu

        async def signed(method, path, params=None):
            if (method, path) == ("GET", "/fapi/v1/algoOrder"):
                return {"actualOrderId": 5}
            if (method, path) == ("GET", "/fapi/v1/order"):
                return {"status": "FILLED", "reduceOnly": True, "symbol": "BTCUSDT"}
            return await FakeExchange.signed(exchange, method, path, params)

        exchange.signed = signed
        rows = execution.normalize_open_algo_rows([dict(row) for row in exchange.algos.values()])
        position = {"quantity": "0.4", "entry_price": "100", "mark_price": "105"}
        moved = asyncio.run(execution.manual_breakeven_after_tp1(exchange, state, plan, position, rows))
        self.assertFalse(moved)
        self.assertTrue(plan["breakeven_done"])
        self.assertEqual(plan["stop_loss"], "101")
        self.assertFalse(any(call[0] == "DELETE" for call in exchange.calls))


class StopBridgeTests(ManualActionBase):
    """Stop değişiminde eski stop silinirken pozisyonu örten geçici 'köprü' stop (miktarlı, yalnız-azaltan)."""

    def bridge_calls(self, exchange, plan):
        prefix_ids = {execution.client_id_for(f"SLBR{tag}", plan["intent_id"]) for tag in (1, 2, "BE")}
        return [call for call in exchange.calls if call[0] == "POST" and call[1] == "/fapi/v1/algoOrder"
                and call[2].get("clientAlgoId") in prefix_ids]

    def test_bridge_is_placed_before_the_old_stop_is_deleted_and_removed_after(self):
        exchange = FakeExchange()
        state, plan = open_manual_plan(exchange)
        old_algo = plan["stop_algo_id"]
        result = self.update(exchange, state, plan, stop=98)
        self.assertEqual(result["stop"], "MOVED")
        bridge = self.bridge_calls(exchange, plan)
        self.assertEqual(len(bridge), 1)
        params = bridge[0][2]
        self.assertEqual((params["type"], params["reduceOnly"], params["quantity"]), ("STOP_MARKET", "true", "1"))
        self.assertNotIn("closePosition", params)
        self.assertEqual(Decimal(params["triggerPrice"]), Decimal("95"))        # eski stop fiyatı
        order = [(call[0], call[1], call[2].get("algoId")) for call in exchange.calls]
        bridge_index = exchange.calls.index(bridge[0])
        delete_old = next(i for i, call in enumerate(exchange.calls)
                          if call[0] == "DELETE" and call[2].get("algoId") == old_algo)
        self.assertLess(bridge_index, delete_old)                                  # önce köprü, sonra eski stop silinir
        self.assertEqual(len(self.stops(exchange)), 1)                             # iş bitince köprü kalmaz
        self.assertEqual(Decimal(self.stops(exchange)[0]["triggerPrice"]), Decimal("98"))
        self.assertTrue(order)

    def test_rejected_bridge_never_blocks_the_move(self):
        exchange = FakeExchange()
        state, plan = open_manual_plan(exchange)
        exchange.fail_post_prefixes.add(execution.client_id_for("SLBR1", plan["intent_id"]))
        result = self.update(exchange, state, plan, stop=98)
        self.assertEqual(result["stop"], "MOVED")
        self.assertEqual(len(self.stops(exchange)), 1)
        self.assertEqual(Decimal(plan["stop_loss"]), Decimal("98"))

    def test_bridge_removed_when_old_stop_is_restored(self):
        exchange = FakeExchange()
        state, plan = open_manual_plan(exchange)
        exchange.fail_post_prefixes.add(execution.client_id_for("SLMV1", plan["intent_id"]))
        with self.assertRaises(execution.ManualPlanActionError):
            self.update(exchange, state, plan, stop=98)
        stops = self.stops(exchange)
        self.assertEqual(len(stops), 1)
        self.assertEqual(Decimal(stops[0]["triggerPrice"]), Decimal("95"))
        self.assertEqual(plan["protection_state"], "MATCHED")

    def test_bridge_covers_the_position_while_new_stop_is_failing(self):
        exchange = FakeExchange()
        state, plan = open_manual_plan(exchange)
        exchange.fail_post_prefixes.update({execution.client_id_for("SLMV1", plan["intent_id"]),
                                            execution.client_id_for("SLRB1", plan["intent_id"])})
        with patch.object(execution, "close_tracked_symbol", AsyncMock(return_value={"orderId": 9})):
            with self.assertRaises(execution.ManualPlanActionError):
                self.update(exchange, state, plan, stop=98)
        # Yeni ve geri-kurulan stop kurulamadı: köprü hâlâ borsada (kapanış doğrulanana kadar örter).
        self.assertEqual(len(self.stops(exchange)), 1)
        self.assertIn(execution.client_id_for("SLBR1", plan["intent_id"]), plan["stop_bridge_client_ids"])

    def test_bridge_ids_count_as_owned_protection_and_never_confuse_the_main_stop(self):
        exchange = FakeExchange()
        state, plan = open_manual_plan(exchange)
        bridge_client = execution.client_id_for("SLBR1", plan["intent_id"])
        plan["stop_bridge_client_ids"] = [bridge_client]
        rows = execution.normalize_open_algo_rows([dict(row) for row in exchange.algos.values()] + [
            {"algoId": 999, "algoStatus": "NEW", "symbol": "BTCUSDT", "side": "SELL", "orderType": "STOP_MARKET",
             "triggerPrice": "95", "quantity": "1", "reduceOnly": "true", "workingType": "MARK_PRICE", "clientAlgoId": bridge_client}])
        state_name, _, _ = execution.classify_plan_protection(plan, rows)
        self.assertEqual(state_name, "MATCHED")
        owned = {row["client_algo_id"] for row in execution.owned_protection_rows(plan, rows)}
        self.assertIn(bridge_client, owned)
        self.assertEqual(execution.close_reason_for_client_id(plan, bridge_client), "STOP")
