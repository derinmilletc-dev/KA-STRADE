"""Coinbase emir akışı: durumlu sahte borsa ile. Ağ yok, gerçek emir yok."""
import asyncio
import os
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from coinbase_fake_exchange import PRODUCT, FakeExchange, FakePool  # noqa: E402

from app.coinbase import client as cb_client  # noqa: E402
from app.coinbase import flow, journal  # noqa: E402
from app.coinbase.errors import CoinbaseError  # noqa: E402
from app.coinbase.limits import current_limits  # noqa: E402


def run(coro):
    return asyncio.run(coro)


async def nosleep(_):
    return None


REQ = {"product_id": PRODUCT, "side": "BUY", "stop_price": "2940", "margin_usd": "60"}


class FlowBase(unittest.TestCase):
    def setUp(self):
        os.environ["COINBASE_ENABLED"] = "true"
        os.environ["COINBASE_ORDERS_ENABLED"] = "true"
        cb_client.reset_limiters()
        journal.reset_schema_cache()
        self.ex = FakeExchange()
        self.client = self.ex.client()
        self.pool = FakePool()
        self.limits = current_limits()

    def tearDown(self):
        os.environ.pop("COINBASE_ENABLED", None)
        os.environ.pop("COINBASE_ORDERS_ENABLED", None)

    def open(self, **over):
        return run(flow.open_position(self.client, self.pool, "u1", self.limits, {**REQ, **over}, sleep=nosleep))

    def fails(self, coro_factory):
        with self.assertRaises(CoinbaseError) as ctx:
            coro_factory()
        return ctx.exception


class OpenTests(FlowBase):
    def test_happy_path_opens_and_verifies_stop(self):
        result = self.open()
        self.assertEqual(result["status"], "opened")
        self.assertEqual(self.ex.positions[PRODUCT]["contracts"], result["contracts"])
        stops = self.ex.active_stops()
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]["side"], "SELL")
        self.assertEqual(stops[0]["base_size"], str(result["contracts"]))
        self.assertTrue(stops[0]["client_order_id"].startswith("kais-s-"))
        self.assertIn("stop_verified", self.pool.outcomes())
        # kayıp kuralı
        self.assertLessEqual(Decimal(result["plan"]["worst_case_loss_usd"]), Decimal("5"))
        self.assertGreaterEqual(Decimal(result["plan"]["leverage"]), Decimal("3"))

    def test_short_uses_buy_stop_above(self):
        result = self.open(side="SELL", stop_price="3060", margin_usd="55")
        self.assertEqual(result["status"], "opened")
        stop = self.ex.active_stops()[0]
        self.assertEqual(stop["side"], "BUY")

    def test_preflight_failure_sends_nothing(self):
        err = self.fails(lambda: self.open(stop_price="3100"))  # long için stop fiyatın üstünde
        self.assertEqual(err.code, "PREFLIGHT_FAILED")
        self.assertEqual(self.ex.posts(), [])
        self.assertTrue(err.body()["reasons"])

    def test_missing_stop_is_rejected(self):
        err = self.fails(lambda: self.open(stop_price=""))
        self.assertEqual(err.code, "PREFLIGHT_FAILED")
        self.assertEqual(self.ex.posts(), [])

    def test_loss_over_five_usd_rejected(self):
        err = self.fails(lambda: self.open(contracts="500"))
        self.assertEqual(err.code, "PREFLIGHT_FAILED")
        self.assertEqual(self.ex.posts(), [])

    def test_contract_too_big_for_loss_rule(self):
        self.ex.contract_size = "1"
        err = self.fails(lambda: self.open())
        self.assertIn("sığmıyor", err.message)
        self.assertEqual(self.ex.posts(), [])

    def test_daily_loss_limit(self):
        self.ex.daily_pnl = "-9.5"
        err = self.fails(lambda: self.open())
        self.assertEqual(err.code, "PREFLIGHT_FAILED")
        self.assertIn("Günlük zarar", err.message)

    def test_unhealthy_margin_level_rejected(self):
        self.ex.margin_level = "MARGIN_LEVEL_TYPE_WARNING"
        err = self.fails(lambda: self.open())
        self.assertIn("Marjin seviyesi", err.message)

    def test_insufficient_buying_power(self):
        self.ex.buying_power = "10"
        err = self.fails(lambda: self.open())
        self.assertIn("Alım gücü", err.message)

    def test_existing_position_blocks_add(self):
        self.ex.set_position(PRODUCT, "BUY", 1)
        err = self.fails(lambda: self.open())
        self.assertIn("zaten açık pozisyon", err.message)

    def test_exchange_rejects_entry(self):
        self.ex.entry_mode = "reject"
        err = self.fails(lambda: self.open())
        self.assertEqual(err.code, "ORDER_REJECTED")
        self.assertIn("Bakiye", err.message)
        self.assertEqual(self.ex.positions, {})
        self.assertEqual(self.ex.active_stops(), [])

    def test_entry_not_filled(self):
        self.ex.entry_mode = "nofill"
        err = self.fails(lambda: self.open())
        self.assertEqual(err.code, "ENTRY_NOT_FILLED")
        self.assertEqual(self.ex.positions, {})

    def test_partial_fill_stop_matches_actual_size(self):
        self.ex.entry_mode = "partial"
        self.ex.partial_contracts = 2
        result = self.open()
        self.assertTrue(result["partial_fill"])
        self.assertEqual(self.ex.active_stops()[0]["base_size"], "2")

    def test_stop_rejected_closes_position(self):
        self.ex.stop_mode = "reject"
        err = self.fails(lambda: self.open())
        self.assertEqual(err.code, "STOP_FAILED_POSITION_CLOSED")
        self.assertEqual(self.ex.positions, {})
        self.assertIn("closed_after_stop_failure", self.pool.outcomes())

    def test_stop_wrong_size_closes_position_and_cancels_stop(self):
        self.ex.stop_mode = "wrong_size"
        err = self.fails(lambda: self.open())
        self.assertEqual(err.code, "STOP_FAILED_POSITION_CLOSED")
        self.assertEqual(self.ex.positions, {})
        self.assertEqual(self.ex.active_stops(), [])

    def test_stop_inactive_closes_position(self):
        self.ex.stop_mode = "inactive"
        err = self.fails(lambda: self.open())
        self.assertEqual(err.code, "STOP_FAILED_POSITION_CLOSED")
        self.assertEqual(self.ex.positions, {})

    def test_stop_and_close_both_fail_is_critical(self):
        self.ex.stop_mode = "reject"
        self.ex.close_fails = True
        err = self.fails(lambda: self.open())
        self.assertEqual(err.code, "CRITICAL_UNPROTECTED")
        self.assertTrue(err.body()["critical"])
        self.assertIn(PRODUCT, err.message)
        self.assertIn("CRITICAL_UNPROTECTED", self.pool.outcomes())

    def test_close_request_ok_but_position_remains_is_critical(self):
        self.ex.stop_mode = "reject"
        self.ex.close_noop = True
        err = self.fails(lambda: self.open())
        self.assertEqual(err.code, "CRITICAL_UNPROTECTED")

    def test_lost_response_retry_is_idempotent(self):
        self.ex.entry_mode = "lost_response"
        result = self.open()
        self.assertEqual(result["status"], "opened")
        self.assertEqual(len(self.ex.positions), 1)
        entries = [o for o in self.ex.orders.values() if o["order_type"] == "MARKET"]
        self.assertEqual(len(entries), 1)  # ikinci deneme yeni emir açmadı

    def test_unknown_outcome_uses_real_position_and_protects_it(self):
        self.ex.entry_mode = "lost_always"
        result = self.open()
        self.assertEqual(result["status"], "opened")
        self.assertIsNone(result["entry_order_id"])
        self.assertEqual(len(self.ex.active_stops()), 1)

    def test_unknown_outcome_without_position_is_critical(self):
        self.ex.entry_mode = "lost_always"
        self.ex.close_fails = False
        original = self.ex._entry

        def lost_but_not_filled(body, size):
            self.ex.entry_mode = "nofill"
            try:
                original(body, size)
            finally:
                self.ex.entry_mode = "lost_always"
            raise CoinbaseError("NETWORK", "bağlantı koptu")

        self.ex._entry = lost_but_not_filled
        err = self.fails(lambda: self.open())
        self.assertEqual(err.code, "ORDER_OUTCOME_UNKNOWN")
        self.assertTrue(err.body()["critical"])

    def test_orders_flag_off_sends_nothing(self):
        os.environ["COINBASE_ORDERS_ENABLED"] = "false"
        err = self.fails(lambda: self.open())
        self.assertEqual(err.code, "ORDERS_DISABLED")
        self.assertEqual(self.ex.posts(), [])


class CloseTests(FlowBase):
    def test_close_removes_position_and_stop(self):
        self.open()
        result = run(flow.close_flow(self.client, self.pool, "u1", PRODUCT, sleep=nosleep))
        self.assertEqual(result["status"], "closed")
        self.assertEqual(self.ex.positions, {})
        self.assertEqual(self.ex.active_stops(), [])

    def test_close_without_position(self):
        err = self.fails(lambda: run(flow.close_flow(self.client, self.pool, "u1", PRODUCT, sleep=nosleep)))
        self.assertEqual(err.code, "NO_POSITION")

    def test_close_fails_is_visible(self):
        self.open()
        self.ex.close_fails = True
        err = self.fails(lambda: run(flow.close_flow(self.client, self.pool, "u1", PRODUCT, sleep=nosleep)))
        self.assertEqual(err.code, "ORDER_REJECTED")
        self.assertEqual(len(self.ex.active_stops()), 1)  # stop yerinde kaldı

    def test_close_unconfirmed_is_critical(self):
        self.open()
        self.ex.close_noop = True
        err = self.fails(lambda: run(flow.close_flow(self.client, self.pool, "u1", PRODUCT, sleep=nosleep)))
        self.assertEqual(err.code, "CLOSE_UNCONFIRMED")
        self.assertTrue(err.body()["critical"])

    def test_orphan_stop_after_close_is_critical_warning(self):
        self.open()
        self.ex.cancel_fails = {self.ex.active_stops()[0]["order_id"]}
        err = self.fails(lambda: run(flow.close_flow(self.client, self.pool, "u1", PRODUCT, sleep=nosleep)))
        self.assertEqual(err.code, "ORPHAN_STOP")
        self.assertEqual(self.ex.positions, {})


class StopAndReduceTests(FlowBase):
    def test_tighten_stop(self):
        self.open()
        result = run(flow.set_stop(self.client, self.pool, "u1", self.limits, PRODUCT, "2970", sleep=nosleep))
        self.assertEqual(result["status"], "stop_set")
        stops = self.ex.active_stops()
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]["stop_price"], "2970")

    def test_loosening_is_refused(self):
        self.open()
        err = self.fails(lambda: run(flow.set_stop(self.client, self.pool, "u1", self.limits, PRODUCT, "2900", sleep=nosleep)))
        self.assertEqual(err.code, "STOP_LOOSENING")
        self.assertEqual(self.ex.active_stops()[0]["stop_price"], "2940")

    def test_stop_on_wrong_side_refused(self):
        self.open()
        err = self.fails(lambda: run(flow.set_stop(self.client, self.pool, "u1", self.limits, PRODUCT, "3100", sleep=nosleep)))
        self.assertEqual(err.code, "BAD_REQUEST")

    def test_unprotected_position_gets_stop(self):
        self.ex.set_position(PRODUCT, "BUY", 2)
        result = run(flow.set_stop(self.client, self.pool, "u1", self.limits, PRODUCT, "2940", sleep=nosleep))
        self.assertEqual(result["status"], "stop_set")
        self.assertEqual(self.ex.active_stops()[0]["base_size"], "2")

    def test_new_stop_failure_closes_position(self):
        self.open()
        self.ex.stop_mode = "reject"
        err = self.fails(lambda: run(flow.set_stop(self.client, self.pool, "u1", self.limits, PRODUCT, "2970", sleep=nosleep)))
        self.assertEqual(err.code, "STOP_FAILED_POSITION_CLOSED")
        self.assertEqual(self.ex.positions, {})

    def test_reduce_resizes_stop(self):
        result = self.open()
        total = result["contracts"]
        out = run(flow.reduce_flow(self.client, self.pool, "u1", self.limits, PRODUCT, 2, sleep=nosleep))
        self.assertEqual(out["remaining_contracts"], total - 2)
        stops = self.ex.active_stops()
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]["base_size"], str(total - 2))

    def test_reduce_bounds(self):
        result = self.open()
        for bad in (0, result["contracts"], 999, "x"):
            err = self.fails(lambda b=bad: run(flow.reduce_flow(self.client, self.pool, "u1", self.limits, PRODUCT, b, sleep=nosleep)))
            self.assertEqual(err.code, "BAD_REQUEST")

    def test_reduce_needs_known_stop(self):
        self.ex.set_position(PRODUCT, "BUY", 3)
        err = self.fails(lambda: run(flow.reduce_flow(self.client, self.pool, "u1", self.limits, PRODUCT, 1, sleep=nosleep)))
        self.assertEqual(err.code, "NO_KNOWN_STOP")
        self.assertEqual(self.ex.positions[PRODUCT]["contracts"], 3)


class ProtectionTests(FlowBase):
    def test_protected(self):
        self.open()
        report = run(flow.protection_report(self.client))
        self.assertTrue(report["all_protected"])
        self.assertFalse(report["has_problem"])

    def test_unprotected_and_mismatch(self):
        self.ex.set_position(PRODUCT, "BUY", 2)
        report = run(flow.protection_report(self.client))
        self.assertEqual(report["positions"][0]["state"], "UNPROTECTED")
        self.assertTrue(report["has_problem"])
        self.ex.add_stop(PRODUCT, "BUY", 5)
        report = run(flow.protection_report(self.client))
        self.assertEqual(report["positions"][0]["state"], "MISMATCH")

    def test_orphan_cleanup_only_touches_our_stops(self):
        mine = self.ex.add_stop("OTH-20DEC30-CDE", "BUY", 1)
        foreign = self.ex.add_stop("OTH-20DEC30-CDE", "BUY", 1, prefix="elle-")
        report = run(flow.protection_report(self.client))
        self.assertEqual([o["order_id"] for o in report["orphan_stops"]], [mine])
        out = run(flow.cleanup_orphans(self.client, self.pool, "u1"))
        self.assertEqual(out["cancelled"], [mine])
        self.assertEqual(self.ex.orders[foreign]["status"], "OPEN")


class LockTests(unittest.TestCase):
    def test_second_acquire_fails_until_release(self):
        journal.reset_schema_cache()
        pool = FakePool()
        self.assertTrue(run(journal.acquire_lock(pool, "u1", "a")))
        self.assertFalse(run(journal.acquire_lock(pool, "u1", "b")))
        run(journal.release_lock(pool, "u1", "a"))
        self.assertTrue(run(journal.acquire_lock(pool, "u1", "b")))


if __name__ == "__main__":
    unittest.main()
