"""Coinbase: kullanıcı limitleri, hazır öneriler (ön kontrolden geçenler) ve korumasız pozisyon uyarısı. Ağ yok."""
import asyncio
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from coinbase_fake_exchange import PRODUCT, FakeExchange  # noqa: E402

from app.coinbase import client as cb_client  # noqa: E402
from app.coinbase import flow, user_limits, watcher  # noqa: E402
from app.coinbase.errors import CoinbaseError  # noqa: E402
from app.coinbase.limits import current_limits  # noqa: E402
from app.coinbase.preflight import run_preflight  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class MemoryPool:
    """user_limits ve watcher tablolarını bellekte taklit eder."""

    def __init__(self):
        self.limits = {}
        self.alerts = {}

    async def execute(self, sql, *args):
        text = " ".join(sql.split())
        if text.startswith("INSERT INTO kaistrade_coinbase_user_limits"):
            self.limits[args[0]] = json.loads(args[1])
        elif text.startswith("UPDATE kaistrade_coinbase_watch SET alerts"):
            self.alerts[args[0]] = json.loads(args[1])

    async def fetchrow(self, sql, *args):
        text = " ".join(sql.split())
        if text.startswith("SELECT settings FROM kaistrade_coinbase_user_limits"):
            return {"settings": json.dumps(self.limits[args[0]])} if args[0] in self.limits else None
        if text.startswith("SELECT alerts FROM kaistrade_coinbase_watch"):
            return {"alerts": json.dumps(self.alerts[args[0]])} if args[0] in self.alerts else None
        return None


class UserLimitsTests(unittest.TestCase):
    def setUp(self):
        user_limits.reset_schema_cache()
        self.pool = MemoryPool()

    def test_default_is_server_limits(self):
        self.assertEqual(run(user_limits.effective(self.pool, "u1")), current_limits())

    def test_user_can_tighten_and_it_is_applied(self):
        run(user_limits.save(self.pool, "u1", {"max_loss_usd": "5", "daily_loss_limit_usd": "20", "leverage_max": "5", "stop_limit_gap_pct": "0.005"}))
        limits = run(user_limits.effective(self.pool, "u1"))
        self.assertEqual((limits.max_loss_usd, limits.daily_loss_limit_usd, limits.leverage_max, limits.stop_limit_gap_pct),
                         (Decimal("5"), Decimal("20"), Decimal("5"), Decimal("0.005")))

    def test_user_cannot_exceed_server_ceiling(self):
        os.environ["COINBASE_MAX_LOSS_USD"] = "10"
        self.addCleanup(os.environ.pop, "COINBASE_MAX_LOSS_USD", None)
        with self.assertRaises(CoinbaseError):
            run(user_limits.save(self.pool, "u1", {"max_loss_usd": "50"}))

    def test_invalid_values_are_rejected_not_silently_fixed(self):
        for bad in ({"max_loss_usd": "abc"}, {"max_loss_usd": "0"}, {"leverage_max": "0.5"}, {"stop_limit_gap_pct": "0.5"}, {"daily_loss_limit_usd": "-3"}):
            with self.subTest(bad=bad), self.assertRaises(CoinbaseError):
                run(user_limits.save(self.pool, "u1", bad))

    def test_empty_field_resets_to_default(self):
        run(user_limits.save(self.pool, "u1", {"max_loss_usd": "5"}))
        run(user_limits.save(self.pool, "u1", {"max_loss_usd": ""}))
        self.assertEqual(run(user_limits.effective(self.pool, "u1")).max_loss_usd, current_limits().max_loss_usd)

    def test_describe_lists_bounds_for_ui(self):
        info = user_limits.describe(current_limits(), {"max_loss_usd": "5"})
        self.assertEqual(info["custom"], {"max_loss_usd": "5"})
        self.assertEqual(info["effective"]["max_loss_usd"], "5")
        self.assertIn("leverage_max", info["bounds"])
        self.assertEqual(info["max_open_orders"], current_limits().max_open_orders)


class SuggestTests(unittest.TestCase):
    def setUp(self):
        cb_client.reset_limiters()
        self.ex = FakeExchange()
        self.client = self.ex.client()
        self.limits = current_limits()

    def suggest(self, **over):
        request = {"product_id": PRODUCT, "side": "BUY", "margin_usd": "60", **over}
        return run(flow.suggest(self.client, self.limits, request))

    def test_every_ok_suggestion_passes_the_real_preflight(self):
        result = self.suggest()
        self.assertEqual([item["stop_pct"] for item in result["items"]], [1, 2, 3, 5])
        passing = [item for item in result["items"] if item["ok"]]
        self.assertTrue(passing, result)
        for item in passing:
            check = run(flow.preflight(self.client, self.limits, {
                "product_id": PRODUCT, "side": "BUY", "margin_usd": "60", "stop_price": item["stop_price"], "take_profit_price": item["take_profit_price"]}))
            self.assertTrue(check["ok"], (item, check["reasons"]))
            self.assertEqual(check["plan"]["contracts"], item["contracts"])

    def test_levels_are_on_the_correct_side_for_long_and_short(self):
        long_items = self.suggest()["items"]
        short_items = self.suggest(side="SELL")["items"]
        for item in long_items:
            self.assertLess(Decimal(item["stop_price"]), Decimal(self.suggest()["state"]["bid"]))
            self.assertGreater(Decimal(item["take_profit_price"]), Decimal(self.suggest()["state"]["ask"]))
        for item in short_items:
            self.assertGreater(Decimal(item["stop_price"]), Decimal(self.suggest(side="SELL")["state"]["ask"]))
            self.assertLess(Decimal(item["take_profit_price"]), Decimal(self.suggest(side="SELL")["state"]["bid"]))

    def test_failing_suggestions_say_why_and_none_is_recommended_when_all_fail(self):
        result = self.suggest(margin_usd="0.01")
        self.assertFalse(any(item["ok"] for item in result["items"]))
        self.assertIsNone(result["recommended_stop_pct"])
        self.assertTrue(all(item["reasons"] for item in result["items"]))

    def test_loss_limit_disables_wide_stops_only(self):
        from dataclasses import replace
        self.limits = replace(self.limits, max_loss_usd=Decimal("1"))
        result = self.suggest()
        flags = {item["stop_pct"]: item["ok"] for item in result["items"]}
        self.assertTrue(flags[1] or not any(flags.values()))
        for item in result["items"]:
            if item["ok"]:
                self.assertLessEqual(Decimal(item["worst_case_loss_usd"]), Decimal("1"))

    def test_suggest_sends_no_orders(self):
        before = len(self.ex.active_stops())
        self.suggest()
        self.assertEqual(len(self.ex.active_stops()), before)
        self.assertFalse(self.ex.positions)


NOW = datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc)


def item(state="protected", product="ETP-20DEC30-CDE"):
    return {"product_id": product, "state": state, "message": "STOP YOK! Hemen stop koy veya pozisyonu kapat." if state != "protected" else "Korunuyor."}


class ProtectionAlertTests(unittest.TestCase):
    def test_first_sighting_is_silent_then_alerts_on_second(self):
        notes, state = watcher.plan_protection_alerts([item("UNPROTECTED")], {}, NOW)
        self.assertEqual(notes, [])
        notes, state = watcher.plan_protection_alerts([item("UNPROTECTED")], state, NOW + timedelta(seconds=45))
        self.assertEqual(len(notes), 1)
        self.assertIn("KORUMASIZ", notes[0][1])
        self.assertTrue(state["ETP-20DEC30-CDE"]["notified"])

    def test_no_repeat_until_realert_interval(self):
        _, state = watcher.plan_protection_alerts([item("UNPROTECTED")], {}, NOW)
        _, state = watcher.plan_protection_alerts([item("UNPROTECTED")], state, NOW + timedelta(seconds=45))
        notes, state = watcher.plan_protection_alerts([item("UNPROTECTED")], state, NOW + timedelta(minutes=10))
        self.assertEqual(notes, [])
        notes, _ = watcher.plan_protection_alerts([item("UNPROTECTED")], state, NOW + timedelta(minutes=31))
        self.assertEqual(len(notes), 1)

    def test_recovery_notifies_once_and_clears(self):
        _, state = watcher.plan_protection_alerts([item("UNPROTECTED")], {}, NOW)
        _, state = watcher.plan_protection_alerts([item("UNPROTECTED")], state, NOW + timedelta(seconds=45))
        notes, state = watcher.plan_protection_alerts([item("protected")], state, NOW + timedelta(seconds=90))
        self.assertEqual(len(notes), 1)
        self.assertIn("yeniden korumada", notes[0][1])
        self.assertEqual(state, {})
        notes, _ = watcher.plan_protection_alerts([item("protected")], state, NOW + timedelta(seconds=135))
        self.assertEqual(notes, [])

    def test_momentary_gap_that_recovers_never_alerts(self):
        _, state = watcher.plan_protection_alerts([item("UNPROTECTED")], {}, NOW)
        notes, state = watcher.plan_protection_alerts([item("protected")], state, NOW + timedelta(seconds=45))
        self.assertEqual((notes, state), ([], {}))

    def test_protected_positions_are_quiet(self):
        self.assertEqual(watcher.plan_protection_alerts([item("protected")], {}, NOW), ([], {}))

    def test_check_protection_uses_report_and_persists_state(self):
        class Client:
            pass

        async def fake_report(_client):
            return {"positions": [item("UNPROTECTED")]}

        sent = []

        async def send(pool, user_id, category, title, body):
            sent.append(title)

        original = flow.protection_report
        flow.protection_report = fake_report
        self.addCleanup(setattr, flow, "protection_report", original)
        watcher.reset_schema_cache()
        pool = MemoryPool()
        pool.alerts = {}
        pool.events = []

        async def execute(sql, *args, _orig=pool.execute):
            text = " ".join(sql.split())
            if text.startswith(("CREATE TABLE", "ALTER TABLE")) or text.startswith("INSERT INTO kaistrade_coinbase_events"):
                return None
            return await _orig(sql, *args)

        pool.execute = execute
        run(watcher.check_protection(pool, "u1", Client(), send=send, now=NOW))
        self.assertEqual(sent, [])
        run(watcher.check_protection(pool, "u1", Client(), send=send, now=NOW + timedelta(seconds=45)))
        self.assertEqual(len(sent), 1)
        self.assertTrue(pool.alerts["u1"]["ETP-20DEC30-CDE"]["notified"])


if __name__ == "__main__":
    unittest.main()
