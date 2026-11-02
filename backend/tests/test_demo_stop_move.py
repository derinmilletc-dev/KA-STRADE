import asyncio
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND = Path(__file__).parents[1]
sys.path.insert(0, str(BACKEND))

from app import binance_demo  # noqa: E402
from app.binance_demo import BinanceDemoError  # noqa: E402


class FakeDemoExchange:
    """Tiny exchange model: one BTCUSDT LONG position (1 @ 100, mark 110) with one closePosition stop and a TP3."""

    def __init__(self, direction="LONG", mark="110"):
        self.direction = direction
        self.mark = mark
        self.next_id = 500
        self.algos = {}
        self.fail_new_prefixes: set[str] = set()
        self.fail_delete = False
        self.positions_amt = "1" if direction == "LONG" else "-1"
        self.calls = []
        side = "SELL" if direction == "LONG" else "BUY"
        self.add(101, "STOP_MARKET", side, "95" if direction == "LONG" else "105", "BOT_SL_old")
        self.add(103, "TAKE_PROFIT_MARKET", side, "130" if direction == "LONG" else "70", "BOT_TP3_old")

    def add(self, algo_id, kind, side, trigger, client_id):
        self.algos[algo_id] = {"algoId": algo_id, "symbol": "BTCUSDT", "orderType": kind, "side": side,
                               "algoStatus": "NEW", "triggerPrice": trigger, "clientAlgoId": client_id}

    async def signed(self, method, path, params=None):
        params = dict(params or {})
        self.calls.append((method, path, params))
        if path == "/fapi/v3/positionRisk":
            return [{"symbol": "BTCUSDT", "positionAmt": self.positions_amt, "entryPrice": "100",
                     "markPrice": self.mark, "positionSide": "BOTH"}]
        if path == "/fapi/v1/openAlgoOrders":
            return [dict(row) for row in self.algos.values()]
        if method == "DELETE" and path == "/fapi/v1/algoOrder":
            if self.fail_delete:
                raise BinanceDemoError("delete failed", http_status=502)
            self.algos.pop(int(params["algoId"]), None)
            return {"algoId": params["algoId"]}
        if method == "POST" and path == "/fapi/v1/algoOrder":
            client_id = params["clientAlgoId"]
            if any(prefix in client_id for prefix in self.fail_new_prefixes):
                raise BinanceDemoError("place failed", http_status=400)
            self.next_id += 1
            self.add(self.next_id, params["type"], params["side"], params["triggerPrice"], client_id)
            return dict(self.algos[self.next_id])
        raise AssertionError((method, path, params))

    def stops(self):
        return [row for row in self.algos.values() if row["orderType"] == "STOP_MARKET"]


def manual_plan(direction="LONG"):
    long_side = direction == "LONG"
    return {
        "id": "plan-manual-1", "user_id": "u1", "symbol": "BTCUSDT", "direction": direction, "source": "MANUAL",
        "provenance_state": "CONFIRMED", "status": "OPEN", "position_status": "OPEN",
        "stop_loss": "95" if long_side else "105", "initial_stop_loss": "95" if long_side else "105",
        "targets": ["110", "120", "130"] if long_side else ["90", "80", "70"],
        "protection_ids": [101, 103], "stop_algo_id": 101, "tp3_algo_id": 103,
    }


POLICY = {"max_loss_per_trade": 10}


class DemoStopMoveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        binance_demo.STOP_MOVE_ACTIVE.clear()
        binance_demo.STOP_MOVE_DONE_AT.clear()
        self.patches = [
            patch.object(binance_demo, "persist_runtime"),
            patch.object(binance_demo, "symbol_rules", new=self._rules),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()

    @staticmethod
    async def _rules(_client, _symbol):
        return {"tick": Decimal("0.1"), "step": Decimal("0.001"), "min_qty": Decimal("0.001"),
                "max_qty": Decimal("1000"), "min_notional": Decimal("5")}

    def state(self, plan):
        return {"plans": {plan["id"]: plan}, "events": []}

    async def move(self, exchange, plan, stop, policy=POLICY):
        return await binance_demo.move_demo_stop(exchange, self.state(plan), plan, stop, policy)

    async def test_long_stop_moves_to_breakeven_and_ownership_follows(self):
        exchange, plan = FakeDemoExchange(), manual_plan()
        result = await self.move(exchange, plan, 100)
        self.assertEqual(result["stop"], "MOVED")
        stops = exchange.stops()
        self.assertEqual(len(stops), 1)
        self.assertEqual(Decimal(stops[0]["triggerPrice"]), Decimal("100"))
        self.assertEqual(plan["stop_algo_id"], stops[0]["algoId"])
        self.assertEqual(plan["stop_loss"], "100")
        self.assertEqual(plan["previous_stop_loss"], "95")
        self.assertEqual(sorted(plan["protection_ids"]), sorted([103, stops[0]["algoId"]]))
        self.assertNotIn(101, plan["protection_ids"])
        self.assertIn(103, exchange.algos)  # take-profit untouched

    async def test_short_stop_moves_down(self):
        exchange, plan = FakeDemoExchange("SHORT", mark="90"), manual_plan("SHORT")
        await self.move(exchange, plan, 100)
        self.assertEqual(Decimal(exchange.stops()[0]["triggerPrice"]), Decimal("100"))

    async def test_loss_side_risk_limit_and_target_rules_reject_without_touching_exchange(self):
        cases = ((111, "zarar tarafında"), (80, "risk sınırın"), (95, "zaten"))
        for stop, message in cases:
            with self.subTest(stop=stop):
                exchange, plan = FakeDemoExchange(), manual_plan()
                with self.assertRaises(BinanceDemoError) as raised:
                    await self.move(exchange, plan, stop)
                self.assertIn(message, str(raised.exception))
                self.assertEqual(sorted(exchange.algos), [101, 103])
                self.assertEqual(plan["stop_loss"], "95")
                self.assertFalse([call for call in exchange.calls if call[0] in {"POST", "DELETE"}])

    async def test_stop_beyond_a_target_is_rejected(self):
        exchange, plan = FakeDemoExchange(), manual_plan()
        plan["targets"] = ["99", "120", "130"]
        with self.assertRaises(BinanceDemoError) as raised:
            await self.move(exchange, plan, 100)
        self.assertIn("hedeflerin", str(raised.exception))

    async def test_bot_and_unowned_plans_are_never_touched(self):
        for patch_fields, message in (({"source": "AUTO"}, "manuel"), ({"provenance_state": "UNKNOWN"}, "sahipliği")):
            with self.subTest(patch_fields=patch_fields):
                exchange, plan = FakeDemoExchange(), manual_plan()
                plan.update(patch_fields)
                with self.assertRaises(BinanceDemoError) as raised:
                    await self.move(exchange, plan, 100)
                self.assertIn(message, str(raised.exception))
                self.assertEqual(exchange.calls, [])

    async def test_old_stop_that_cannot_be_deleted_changes_nothing(self):
        exchange, plan = FakeDemoExchange(), manual_plan()
        exchange.fail_delete = True
        with self.assertRaises(BinanceDemoError) as raised:
            await self.move(exchange, plan, 100)
        self.assertIn("kaldırılamadı", str(raised.exception))
        self.assertEqual(plan["stop_algo_id"], 101)
        self.assertIn(101, exchange.algos)
        self.assertEqual(plan["stop_loss"], "95")

    async def test_failed_new_stop_restores_the_old_price(self):
        exchange, plan = FakeDemoExchange(), manual_plan()
        exchange.fail_new_prefixes.add("SLMV")
        with self.assertRaises(BinanceDemoError) as raised:
            await self.move(exchange, plan, 100)
        self.assertIn("geri kuruldu", str(raised.exception))
        stops = exchange.stops()
        self.assertEqual(len(stops), 1)
        self.assertEqual(Decimal(stops[0]["triggerPrice"]), Decimal("95"))
        self.assertEqual(plan["stop_loss"], "95")
        self.assertEqual(plan["stop_algo_id"], stops[0]["algoId"])
        self.assertIn(stops[0]["algoId"], plan["protection_ids"])

    async def test_new_and_restore_both_failing_closes_the_position(self):
        exchange, plan = FakeDemoExchange(), manual_plan()
        exchange.fail_new_prefixes.update({"SLMV", "SLRB"})
        closed = []

        async def abort(_client, _state, abort_plan, _error, _event, close_message, **_kwargs):
            closed.append(abort_plan["id"])
            raise BinanceDemoError(close_message, http_status=409)

        with patch.object(binance_demo, "_abort_protection_installation", new=abort):
            with self.assertRaises(BinanceDemoError):
                await self.move(exchange, plan, 100)
        self.assertEqual(closed, ["plan-manual-1"])

    async def test_repair_loop_cannot_use_a_snapshot_older_than_the_move(self):
        exchange, plan = FakeDemoExchange(), manual_plan()
        snapshot_at = binance_demo.time.monotonic()
        await self.move(exchange, plan, 100)
        self.assertTrue(binance_demo.stop_move_blocks_repair(plan, snapshot_at))
        self.assertFalse(binance_demo.stop_move_blocks_repair(plan, binance_demo.time.monotonic() + 1))
        self.assertEqual(binance_demo.STOP_MOVE_ACTIVE, set())

    async def test_move_is_refused_while_another_protection_job_holds_the_lock(self):
        exchange, plan = FakeDemoExchange(), manual_plan()
        async with binance_demo.protection_lock(plan):
            with self.assertRaises(BinanceDemoError) as raised:
                await self.move(exchange, plan, 100)
        self.assertIn("sürüyor", str(raised.exception))
        self.assertEqual(exchange.calls, [])


    async def test_repair_function_refuses_a_stale_snapshot_after_a_move(self):
        exchange, plan = FakeDemoExchange(), manual_plan()
        state = self.state(plan)
        stale_at = binance_demo.time.monotonic()
        await binance_demo.move_demo_stop(exchange, state, plan, 100, POLICY)
        posted = []

        async def post_algo(_client, params):
            posted.append(params)
            return {}

        stale_snapshot = {"open_algo_orders_available": True, "open_algo_orders_quality": "CONFIRMED", "open_algo_orders": []}
        with patch.object(binance_demo, "post_algo", new=post_algo):
            changed = await binance_demo._repair_missing_stop_protection(
                exchange, state, plan, stale_snapshot, plans=[plan], snapshot_at=stale_at,
            )
        self.assertFalse(changed)
        self.assertEqual(posted, [])


class DemoTrailingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        binance_demo.STOP_MOVE_ACTIVE.clear()
        binance_demo.STOP_MOVE_DONE_AT.clear()
        self.patches = [patch.object(binance_demo, "persist_runtime"), patch.object(binance_demo, "symbol_rules", new=DemoStopMoveTests._rules)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()

    def setup_plan(self, direction="LONG", mark="120", pct=2):
        from app import manual_trail

        exchange, plan = FakeDemoExchange(direction, mark=mark), manual_plan(direction)
        plan["trail"] = manual_trail.trail_config(Decimal(str(pct)), "now")
        state = {"plans": {plan["id"]: plan}, "events": []}
        row = {"markPrice": mark, "positionAmt": exchange.positions_amt}
        return exchange, plan, state, row

    async def test_trailing_tightens_stop_and_beyond_a_filled_target(self):
        exchange, plan, state, row = self.setup_plan()  # mark 120, %2 -> 117.6; TP1 110 çoktan aşıldı
        self.assertTrue(await binance_demo.demo_trailing_step(exchange, state, plan, row))
        self.assertEqual(Decimal(exchange.stops()[0]["triggerPrice"]), Decimal("117.6"))
        self.assertEqual(plan["trail"]["failures"], 0)
        self.assertGreater(plan["trail"]["last_move_epoch"], 0)

    async def test_trailing_is_throttled_and_never_loosens(self):
        exchange, plan, state, row = self.setup_plan()
        await binance_demo.demo_trailing_step(exchange, state, plan, row)
        calls_after_first = len(exchange.calls)
        self.assertFalse(await binance_demo.demo_trailing_step(exchange, state, plan, row))  # aralık dolmadı
        self.assertEqual(len(exchange.calls), calls_after_first)
        plan["trail"]["last_move_epoch"] = 0
        row_down = {**row, "markPrice": "100"}
        exchange.mark = "100"
        self.assertFalse(await binance_demo.demo_trailing_step(exchange, state, plan, row_down))
        self.assertEqual(Decimal(exchange.stops()[0]["triggerPrice"]), Decimal("117.6"))

    async def test_short_trailing_moves_down(self):
        exchange, plan, state, row = self.setup_plan("SHORT", mark="80")
        self.assertTrue(await binance_demo.demo_trailing_step(exchange, state, plan, row))
        self.assertEqual(Decimal(exchange.stops()[0]["triggerPrice"]), Decimal("81.6"))

    async def test_three_failures_switch_trailing_off_and_keep_the_stop(self):
        exchange, plan, state, row = self.setup_plan()
        exchange.fail_new_prefixes.add("SLMV")
        for attempt in range(3):
            plan["trail"]["last_move_epoch"] = 0 if "trail" in plan else 0
            await binance_demo.demo_trailing_step(exchange, state, plan, row)
        self.assertNotIn("trail", plan)
        stops = exchange.stops()
        self.assertEqual(len(stops), 1)  # her denemede eski stop geri kuruldu
        self.assertEqual(Decimal(stops[0]["triggerPrice"]), Decimal("95"))
        self.assertIn(stops[0]["algoId"], plan["protection_ids"])

    async def test_bot_plan_is_ignored(self):
        exchange, plan, state, row = self.setup_plan()
        plan["source"] = "AUTO"
        self.assertFalse(await binance_demo.demo_trailing_step(exchange, state, plan, row))
        self.assertEqual(exchange.calls, [])


if __name__ == "__main__":
    unittest.main()
