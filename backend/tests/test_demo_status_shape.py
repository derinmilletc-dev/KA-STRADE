import importlib.util
import json
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("premium_access_demo_shape", Path(__file__).resolve().parents[1] / "app" / "premium_access.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)  # type: ignore[union-attr]

LIMITS = {"max_margin_usdt": 50.0, "max_leverage": 5, "max_notional_usdt": 250.0, "max_open_positions": 2, "arm_minutes": 30}
STATUS = {
    "version": "21.0.0", "mode": "BINANCE_FUTURES_DEMO_ONLY", "configured": True, "connected": True, "armed": False,
    "armed_until": None, "rest_host": "https://demo", "websocket_host": "wss://demo", "real_trading_locked": True,
    "limits": LIMITS, "last_checked": None, "last_error": "Binance 451 internal", "events": [{"title": "BTCUSDT LONG"}],
    "reconciliation": {"internal_active_plans": 3},
}
ACCOUNT = {
    **STATUS, "wallet_balance": 1000.0, "available_balance": 900.0, "margin_balance": 1000.0, "unrealized_pnl": -4.5,
    "positions": [{"symbol": "BTCUSDT", "quantity": 0.01, "entry_price": 82000.0, "mark_price": 82100.0,
                   "liquidation_price": 70000.0, "unrealized_pnl": 1.0}],
    "open_orders": [{"symbol": "BTCUSDT", "price": 81000.0}], "open_algo_orders": [{"trigger_price": 80000.0}],
    "plans": [{"symbol": "BTCUSDT", "stop_loss": 80000.0, "tp1": 84000.0}],
}


def project(payload):
    return module.public_projection(json.loads(json.dumps(payload)), allowlist=True)


class DemoStatusShapeTests(unittest.TestCase):
    def test_allowlist_alone_breaks_frontend_contract(self):
        self.assertNotIn("limits", project(STATUS))
        self.assertNotIn("plans", project(ACCOUNT))

    def test_status_keeps_limits_and_flags(self):
        shaped = module.demo_status_shape(project(STATUS), STATUS)
        self.assertEqual(shaped["limits"], LIMITS)
        self.assertEqual((shaped["configured"], shaped["connected"], shaped["armed"], shaped["armed_until"]), (True, True, False, None))
        self.assertIs(shaped["real_trading_locked"], True)
        dumped = json.dumps(shaped)
        for leaked in ("events", "reconciliation", "451", "rest_host"):
            self.assertNotIn(leaked, dumped)

    def test_account_returns_empty_lists_without_leaks(self):
        shaped = module.demo_account_shape(project(ACCOUNT), ACCOUNT)
        for key in ("positions", "open_orders", "open_algo_orders", "plans"):
            self.assertEqual(shaped[key], [])
        self.assertEqual(shaped["wallet_balance"], 1000.0)
        dumped = json.dumps(shaped)
        for leaked in ("82000", "81000", "80000", "84000", "70000", "stop_loss"):
            self.assertNotIn(leaked, dumped)

    def test_bad_limits_and_unconfigured_account_are_safe(self):
        bad = {**STATUS, "configured": "yes", "limits": {"max_margin_usdt": True, "max_leverage": -1, "arm_minutes": "30"}}
        shaped = module.demo_status_shape(project(bad), bad)
        self.assertEqual(shaped["limits"], {})
        self.assertIs(shaped["configured"], False)
        empty = {**STATUS, "configured": False}
        account = module.demo_account_shape(project(empty), empty)
        self.assertEqual(account["positions"], [])
        self.assertNotIn("wallet_balance", account)


if __name__ == "__main__":
    unittest.main()
