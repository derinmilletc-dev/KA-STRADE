import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from app import binance_demo  # noqa: E402
from app.binance_demo import BinanceDemoError  # noqa: E402
import test_demo_stop_move as _base  # noqa: E402
from test_demo_stop_move import FakeDemoExchange, manual_plan  # noqa: E402


def setup(direction="LONG"):
    exchange, plan = FakeDemoExchange(direction, mark="110" if direction == "LONG" else "90"), manual_plan(direction)
    side = "SELL" if direction == "LONG" else "BUY"
    long_side = direction == "LONG"
    plan["targets"] = ["115", "120", "130"] if long_side else ["85", "80", "70"]
    exchange.add(102, "TAKE_PROFIT_MARKET", side, plan["targets"][0], "BOT_TP1_old")
    exchange.add(104, "TAKE_PROFIT_MARKET", side, plan["targets"][1], "BOT_TP2_old")
    plan.update({"tp1_algo_id": 102, "tp2_algo_id": 104, "tp1_quantity": "0.3", "tp2_quantity": "0.3",
                 "protection_ids": [101, 102, 103, 104]})
    return exchange, plan


class DemoTargetMoveTests(_base.DemoStopMoveTests):
    async def test_move_tp1_and_tp3_long(self):
        exchange, plan = setup()
        result = await binance_demo.move_demo_targets(exchange, self.state(plan), plan, {1: 116, 3: 140})
        self.assertEqual(result["changed"], [1, 3])
        self.assertEqual(plan["targets"], ["116", "120", "140"])
        prices = sorted(Decimal_(r["triggerPrice"]) for r in exchange.algos.values() if r["orderType"] == "TAKE_PROFIT_MARKET")
        self.assertEqual(prices, [116, 120, 140])
        self.assertNotIn(102, plan["protection_ids"])
        self.assertNotIn(103, plan["protection_ids"])
        self.assertEqual(len(plan["protection_ids"]), 4)
        self.assertIn(101, exchange.algos)  # stop untouched
        self.assertEqual(plan["tp1_algo_id"] in exchange.algos, True)

    async def test_short_targets(self):
        exchange, plan = setup("SHORT")
        await binance_demo.move_demo_targets(exchange, self.state(plan), plan, {2: 78})
        self.assertEqual(plan["targets"], ["85", "78", "70"])

    async def test_bad_order_or_side_rejected_without_touching_exchange(self):
        for wanted, message in (({1: 125}, "sırayla"), ({1: 105}, "kâr tarafında"), ({1: 115}, "zaten")):
            with self.subTest(wanted=wanted):
                exchange, plan = setup()
                with self.assertRaises(BinanceDemoError) as raised:
                    await binance_demo.move_demo_targets(exchange, self.state(plan), plan, wanted)
                self.assertIn(message, str(raised.exception))
                self.assertFalse([c for c in exchange.calls if c[0] in {"POST", "DELETE"}])

    async def test_failed_new_tp_restores_old_one(self):
        exchange, plan = setup()
        exchange.fail_new_prefixes = {"TPM"}
        with self.assertRaises(BinanceDemoError) as raised:
            await binance_demo.move_demo_targets(exchange, self.state(plan), plan, {1: 116})
        self.assertIn("geri kuruldu", str(raised.exception))
        self.assertEqual(plan["targets"], ["115", "120", "130"])
        self.assertEqual(plan["tp1_algo_id"] in exchange.algos, True)
        self.assertEqual(Decimal_(exchange.algos[plan["tp1_algo_id"]]["triggerPrice"]), 115)
        self.assertIn(101, exchange.algos)

    async def test_both_new_and_restore_fail_goes_to_monitoring_stop_stays(self):
        exchange, plan = setup()
        exchange.fail_new_prefixes = {"TPM", "TPR"}
        with self.assertRaises(BinanceDemoError):
            await binance_demo.move_demo_targets(exchange, self.state(plan), plan, {1: 116})
        self.assertIn("TP1", plan["monitoring_targets"])
        self.assertIn(101, exchange.algos)

    async def test_filled_tp_is_not_touched(self):
        exchange, plan = setup()
        exchange.algos.pop(102)
        with self.assertRaises(BinanceDemoError):
            await binance_demo.move_demo_targets(exchange, self.state(plan), plan, {1: 116})
        self.assertFalse([c for c in exchange.calls if c[0] in {"POST", "DELETE"}])

    async def test_only_manual(self):
        exchange, plan = setup()
        plan["source"] = "AUTO"
        with self.assertRaises(BinanceDemoError):
            await binance_demo.move_demo_targets(exchange, self.state(plan), plan, {1: 116})


def Decimal_(value):
    from decimal import Decimal
    return Decimal(str(value))




for _name in [n for n in dir(_base.DemoStopMoveTests) if n.startswith("test_")]:
    if _name not in DemoTargetMoveTests.__dict__:
        setattr(DemoTargetMoveTests, _name, None)
