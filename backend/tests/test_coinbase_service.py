"""Coinbase okuma servisi: ham cevap -> sabit biçim. Eksik alan sessizce geçilmez."""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.coinbase import service  # noqa: E402
from app.coinbase.errors import CoinbaseError  # noqa: E402


def usd(value):
    return {"value": str(value), "currency": "USD"}


def window(kind="FCM_MARGIN_WINDOW_TYPE_INTRADAY", level="MARGIN_LEVEL_TYPE_BASE", initial="10"):
    return {
        "margin_window_type": kind, "margin_level": level, "initial_margin": initial,
        "maintenance_margin": "5", "liquidation_buffer": "40", "total_hold": "0", "futures_buying_power": "90",
    }


def balance_payload(intraday_level="MARGIN_LEVEL_TYPE_BASE", overnight_level="MARGIN_LEVEL_TYPE_BASE"):
    return {"balance_summary": {
        "futures_buying_power": usd(90), "total_usd_balance": usd(120), "cbi_usd_balance": usd(20),
        "cfm_usd_balance": usd(100), "total_open_orders_hold_amount": usd(0), "unrealized_pnl": usd("1.5"),
        "daily_realized_pnl": usd(0), "initial_margin": usd(10), "available_margin": usd(110),
        "liquidation_threshold": usd(5), "liquidation_buffer_amount": usd(105), "liquidation_buffer_percentage": "95.5",
        "intraday_margin_window_measure": window(level=intraday_level),
        "overnight_margin_window_measure": window("FCM_MARGIN_WINDOW_TYPE_OVERNIGHT", overnight_level, "25"),
        "total_pending_transfers_amount": usd(0),
    }}


class FakeClient:
    def __init__(self, **by_path):
        self.by_path = by_path
        self.paths = []

    async def get(self, path, params=None):
        self.paths.append(path)
        item = self.by_path[path]
        if isinstance(item, Exception):
            raise item
        return item


def run(coro):
    return asyncio.run(coro)


class BalanceTests(unittest.TestCase):
    def test_normalizes_amounts_and_both_windows(self):
        result = service.normalize_balance_summary(balance_payload())
        self.assertEqual(result["futures_buying_power"], {"value": "90", "currency": "USD"})
        self.assertEqual(result["liquidation_buffer_percentage"], "95.5")
        self.assertEqual(result["intraday_window"]["initial_margin"], "10")
        self.assertEqual(result["overnight_window"]["initial_margin"], "25")
        self.assertIsNone(result["funding_pnl"])  # alan yoksa None, uydurma yok

    def test_worst_window_follows_the_worse_margin_level(self):
        result = service.normalize_balance_summary(balance_payload(overnight_level="MARGIN_LEVEL_TYPE_DANGER"))
        self.assertEqual(result["worst_margin_window"], "overnight")
        self.assertEqual(result["worst_margin_level"], "MARGIN_LEVEL_TYPE_DANGER")
        result = service.normalize_balance_summary(balance_payload(intraday_level="MARGIN_LEVEL_TYPE_LIQUIDATION"))
        self.assertEqual(result["worst_margin_window"], "intraday")

    def test_unknown_level_counts_as_worst(self):
        self.assertGreater(service.level_rank("SOMETHING_NEW"), service.level_rank("MARGIN_LEVEL_TYPE_LIQUIDATION"))
        self.assertGreater(service.level_rank(None), service.level_rank("LIQUIDATION"))
        result = service.normalize_balance_summary(balance_payload(overnight_level="MARGIN_LEVEL_TYPE_UNSPECIFIED"))
        self.assertEqual(result["worst_margin_window"], "overnight")

    def test_missing_pieces_are_explicit_errors(self):
        broken = {
            "no summary": {},
            "summary not dict": {"balance_summary": []},
            "no buying power": {"balance_summary": {**balance_payload()["balance_summary"], "futures_buying_power": None}},
            "no overnight": {"balance_summary": {**balance_payload()["balance_summary"], "overnight_margin_window_measure": None}},
            "no intraday": {"balance_summary": {**balance_payload()["balance_summary"], "intraday_margin_window_measure": "x"}},
        }
        for name, payload in broken.items():
            with self.subTest(name), self.assertRaises(CoinbaseError) as caught:
                service.normalize_balance_summary(payload)
            self.assertEqual(caught.exception.code, "BAD_RESPONSE")


class PositionsAndWindowTests(unittest.TestCase):
    def test_positions_normalized(self):
        rows = service.normalize_positions({"positions": [{
            "product_id": "BIT-28JUL23-CDE", "side": "LONG", "number_of_contracts": "2", "avg_entry_price": "30000",
            "current_price": "30100", "unrealized_pnl": "2", "daily_realized_pnl": "0", "expiration_time": "2027-01-01T00:00:00Z",
            "extra_field_we_do_not_forward": "x",
        }]})
        self.assertEqual(rows[0]["product_id"], "BIT-28JUL23-CDE")
        self.assertEqual(rows[0]["number_of_contracts"], "2")
        self.assertNotIn("extra_field_we_do_not_forward", rows[0])

    def test_empty_forms(self):
        self.assertEqual(service.normalize_positions({"positions": []}), [])
        self.assertEqual(service.normalize_positions({}), [])

    def test_unexpected_shapes_are_errors_not_empty(self):
        for payload in ({"something": 1}, {"positions": "x"}, {"positions": [{"side": "LONG"}]}, {"positions": ["x"]}):
            with self.subTest(payload), self.assertRaises(CoinbaseError):
                service.normalize_positions(payload)

    def test_margin_window(self):
        result = service.normalize_margin_window({
            "margin_window": {"margin_window_type": "FCM_MARGIN_WINDOW_TYPE_OVERNIGHT", "end_time": "2026-11-13T13:00:00Z"},
            "is_intraday_margin_killswitch_enabled": True,
        })
        self.assertEqual(result["margin_window_type"], "FCM_MARGIN_WINDOW_TYPE_OVERNIGHT")
        self.assertTrue(result["intraday_killswitch"])
        self.assertFalse(result["intraday_enrollment_killswitch"])
        with self.assertRaises(CoinbaseError):
            service.normalize_margin_window({})


class CheckKeyTests(unittest.TestCase):
    ACCOUNTS = service.ACCOUNTS_PATH
    PERMS = service.KEY_PERMISSIONS_PATH

    def test_verified_permissions(self):
        client = FakeClient(**{self.ACCOUNTS: {"accounts": []}, self.PERMS: {"can_view": True, "can_trade": True, "can_transfer": False}})
        result = run(service.check_key(client))
        self.assertTrue(result["permissions_checked"])
        self.assertIs(result["can_transfer"], False)
        self.assertIsNone(result["warning"])

    def test_transfer_permission_is_reported(self):
        client = FakeClient(**{self.ACCOUNTS: {}, self.PERMS: {"can_view": True, "can_trade": True, "can_transfer": True}})
        self.assertIs(run(service.check_key(client))["can_transfer"], True)

    def test_unavailable_permission_endpoint_is_a_visible_warning_not_silence(self):
        client = FakeClient(**{self.ACCOUNTS: {}, self.PERMS: CoinbaseError("NOT_FOUND", "bulunamadı (404)")})
        result = run(service.check_key(client))
        self.assertFalse(result["permissions_checked"])
        self.assertIsNone(result["can_transfer"])
        self.assertIn("transfer", result["warning"])

    def test_odd_permission_payload_is_a_warning(self):
        client = FakeClient(**{self.ACCOUNTS: {}, self.PERMS: {"can_view": "yes"}})
        result = run(service.check_key(client))
        self.assertFalse(result["permissions_checked"])
        self.assertIsNotNone(result["warning"])

    def test_transient_errors_are_not_swallowed(self):
        for code in ("RATE_LIMITED", "NETWORK", "CLOCK_SKEW"):
            client = FakeClient(**{self.ACCOUNTS: {}, self.PERMS: CoinbaseError(code, "x")})
            with self.subTest(code), self.assertRaises(CoinbaseError):
                run(service.check_key(client))

    def test_bad_key_fails_before_anything_else(self):
        client = FakeClient(**{self.ACCOUNTS: CoinbaseError("AUTH_FAILED", "401")})
        with self.assertRaises(CoinbaseError) as caught:
            run(service.check_key(client))
        self.assertEqual(caught.exception.code, "AUTH_FAILED")
        self.assertEqual(client.paths, [self.ACCOUNTS])


class FetchTests(unittest.TestCase):
    def test_fetch_account_uses_only_read_endpoints(self):
        client = FakeClient(**{
            service.BALANCE_PATH: balance_payload(),
            service.MARGIN_WINDOW_PATH: {"margin_window": {"margin_window_type": "X", "end_time": "t"}},
        })
        result = run(service.fetch_account(client))
        self.assertEqual(set(result), {"balance", "margin_window"})
        self.assertEqual(client.paths, [service.BALANCE_PATH, service.MARGIN_WINDOW_PATH])


if __name__ == "__main__":
    unittest.main()
