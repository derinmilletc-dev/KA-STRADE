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
