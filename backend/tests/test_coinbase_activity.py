"""Coinbase işlem geçmişi ve bildirim izleyicisi: sınıflama ve bildirim kararları. Ağ ve gerçek veritabanı yok."""
import asyncio
import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.coinbase import activity, journal, market, watcher  # noqa: E402
from app.coinbase.client import READ_ONLY_PATTERNS  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def fill(order_id, side, price, size, time, product="SLP-20DEC30-CDE", commission="0.1"):
    return {"order_id": order_id, "product_id": product, "side": side, "price": price, "size": size, "trade_time": time, "commission": commission}


class FakeClient:
    def __init__(self):
        self.fills = []
        self.positions = []

    async def get(self, path, params=None):
        if path.endswith("/orders/historical/fills"):
            return {"fills": self.fills}
        if path.endswith("/cfm/positions"):
            return {"positions": self.positions}
        if path.endswith("/brokerage/products"):
            return {"products": [{"product_id": "SLP-20DEC30-CDE", "display_name": "SOL PERP", "price": "66", "status": "online",
                                  "future_product_details": {"contract_root_unit": "SOL", "contract_size": "5"}}]}
        raise AssertionError(path)


class FakePool:
    def __init__(self, roles=None):
        self.watch = None
        self.events = []
        self.roles = roles or {}

    async def execute(self, sql, *args):
        text = sql.strip()
        if text.startswith("INSERT INTO kaistrade_coinbase_watch"):
            if self.watch is None:
                self.watch = {"last_fill_time": args[1], "positions": args[2]}
        elif text.startswith("UPDATE kaistrade_coinbase_watch"):
            self.watch = {"last_fill_time": args[1], "positions": args[2]}
        elif text.startswith("INSERT INTO kaistrade_coinbase_events"):
            self.events.append({"outcome": args[5], "order_id": args[3], "product_id": args[2], "detail": json.loads(args[6]) if args[6] else None})

    async def fetchrow(self, sql, *args):
        if "kaistrade_coinbase_watch" in sql:
            return self.watch
        if "SELECT 1 AS hit" in sql:
            _user, outcome, product, key, _days = args
            for event in self.events:
                if event["outcome"] == outcome and (product is None or event["product_id"] == product) and (key is None or (event["detail"] or {}).get("key") == key):
                    return {"hit": 1}
        return None

    async def fetch(self, sql, *args):
        if "role = ANY" in sql:
            wanted = set(args[1])
            return [{"order_id": oid} for oid, role in self.roles.items() if role in wanted]
        return []


class ActivityTests(unittest.TestCase):
    def setUp(self):
        market.reset_cache()
        journal.reset_schema_cache()
        watcher.reset_schema_cache()

    def test_fills_path_is_read_only_allowlisted(self):
        self.assertTrue(any(p.fullmatch("/api/v3/brokerage/orders/historical/fills") for p in READ_ONLY_PATTERNS))

    def test_normalize_skips_spot_and_bad_rows_and_unifies_time(self):
        rows = activity.normalize_fills({"fills": [
            fill("a", "BUY", "66", "2", "2026-11-21T05:00:00.1Z"),
            fill("b", "BUY", "66", "2", "2026-11-21T05:00:00Z", product="SOL-USD"),
            fill("c", "HOLD", "66", "2", "2026-11-21T05:00:00Z"),
            {"order_id": "d"},
        ]})
        self.assertEqual([r["order_id"] for r in rows], ["a"])
        self.assertEqual(rows[0]["time"], "2026-11-21T05:00:00.100000+00:00")

    def test_group_partial_fills_weighted(self):
        rows = activity.normalize_fills({"fills": [fill("a", "BUY", "60", "1", "2026-11-21T05:00:00Z"), fill("a", "BUY", "63", "2", "2026-11-21T05:00:01Z")]})
        grouped = activity.group_fills(rows)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["size"], Decimal("3"))
        self.assertEqual(grouped[0]["price"], Decimal("62"))

    def test_feed_classifies_entry_stop_target_and_manual_close(self):
        rows = activity.normalize_fills({"fills": [
            fill("e1", "BUY", "60", "2", "2026-11-21T05:00:00Z"),
            fill("p1", "SELL", "66", "2", "2026-11-21T06:00:00Z"),
            fill("e2", "SELL", "66", "1", "2026-11-21T07:00:00Z"),
            fill("p2", "BUY", "68", "1", "2026-11-21T08:00:00Z"),
            fill("e3", "BUY", "68", "1", "2026-11-21T09:00:00Z"),
            fill("c3", "SELL", "69", "1", "2026-11-21T10:00:00Z"),
        ]})
        roles = {"e1": "entry", "p1": "stop", "e2": "entry", "p2": "stop", "e3": "entry", "c3": "close"}
        feed = activity.build_feed(activity.group_fills(rows), roles, {"SLP-20DEC30-CDE": Decimal("5")})
        kinds = [item["kind"] for item in feed]
        self.assertEqual(kinds, ["manual_close", "entry", "stop", "entry", "target", "entry"])
        self.assertEqual(feed[4]["pnl"], "60.00")   # (66-60) * 2 * 5
        self.assertEqual(feed[2]["pnl"], "-10.00")  # SHORT 66 -> 68, 1 * 5
        self.assertEqual(feed[0]["pnl"], "5.00")

    def test_unknown_close_without_position_is_not_guessed(self):
        rows = activity.normalize_fills({"fills": [fill("x", "SELL", "66", "2", "2026-11-21T06:00:00Z")]})
        feed = activity.build_feed(activity.group_fills(rows), {}, {})
        self.assertEqual(feed[0]["kind"], "other")

    def test_watcher_first_run_is_silent_then_notifies_target(self):
        client, pool, sent = FakeClient(), FakePool(roles={"e1": "entry"}), []

        async def send(_pool, user_id, category, title, body):
            sent.append((category, title, body))

        client.fills = [fill("e1", "BUY", "60", "2", "2026-11-21T05:00:00Z")]
        client.positions = [{"product_id": "SLP-20DEC30-CDE", "side": "FUTURES_POSITION_SIDE_LONG", "number_of_contracts": "2", "avg_entry_price": "60"}]
        self.assertEqual(run(watcher.check_user(pool, "u1", client, send=send)), [])
        self.assertEqual(sent, [])
        self.assertIsNotNone(pool.watch)

        client.fills = [fill("tp", "SELL", "66", "2", "2026-11-21T06:00:00Z")] + client.fills
        client.positions = []
        events = run(watcher.check_user(pool, "u1", client, send=send))
        self.assertEqual([e["kind"] for e in events], ["target"])
        self.assertEqual(sent[0][0], "close")
        self.assertIn("hedef", sent[0][1])
        self.assertIn("60.00", sent[0][2])
        self.assertEqual(pool.events[-1]["outcome"], "target")

        # aynı dolum ikinci kez bildirilmez
        self.assertEqual(run(watcher.check_user(pool, "u1", client, send=send)), [])
        self.assertEqual(len(sent), 1)

    def test_watcher_skips_own_entry_notification_but_tells_resting_fill(self):
        client, pool, sent = FakeClient(), FakePool(roles={"e1": "entry", "r1": "entry_rest"}), []

        async def send(_pool, user_id, category, title, body):
            sent.append((category, title))

        run(watcher.check_user(pool, "u1", client, send=send))
        client.fills = [fill("e1", "BUY", "60", "1", "2026-11-21T05:00:00Z"), fill("r1", "BUY", "61", "1", "2026-11-21T05:01:00Z", product="SLP-20DEC30-CDE")]
        run(watcher.check_user(pool, "u1", client, send=send))
        self.assertEqual(len(sent), 1)
        self.assertIn("bekleyen", sent[0][1])

    def test_watcher_send_failure_does_not_break(self):
        client, pool = FakeClient(), FakePool()

        async def broken(*_args):
            raise RuntimeError("push down")

        run(watcher.check_user(pool, "u1", client, send=broken))
        client.positions = []
        pool.watch["positions"] = json.dumps({"SLP-20DEC30-CDE": {"side": "LONG", "entry": "60"}})
        client.fills = [fill("s1", "SELL", "55", "1", "2026-11-21T06:00:00Z")]
        events = run(watcher.check_user(pool, "u1", client, send=broken))
        self.assertEqual([e["kind"] for e in events], ["stop"])
        self.assertEqual(pool.events[-1]["outcome"], "stop")


    def test_expiry_warning_once_and_only_inside_24h(self):
        from datetime import datetime, timezone
        pool, sent = FakePool(), []

        async def send(_pool, user_id, category, title, body):
            sent.append((category, title))

        now = datetime(2026, 11, 26, 20, 0, tzinfo=timezone.utc)
        rows = [{"product_id": "SLP-27NOV26-CDE", "side": "FUTURES_POSITION_SIDE_LONG", "expiration_time": "2026-11-27T16:00:00Z"},
                {"product_id": "SLP-24DEC26-CDE", "side": "FUTURES_POSITION_SIDE_SHORT", "expiration_time": "2026-12-24T16:00:00Z"},
                {"product_id": "SLP-20DEC30-CDE", "side": "FUTURES_POSITION_SIDE_LONG", "expiration_time": "2030-12-20T16:00:00Z"}]
        self.assertEqual(run(watcher.check_expiry(pool, "u1", rows, send=send, now=now)), ["SLP-27NOV26-CDE"])
        self.assertIn("20 saat", sent[0][1])
        self.assertEqual(run(watcher.check_expiry(pool, "u1", rows, send=send, now=now)), [])
        self.assertEqual(len(sent), 1)

    def test_weekly_summary_monday_once(self):
        from datetime import datetime, timezone
        client, pool, sent = FakeClient(), FakePool(roles={"e1": "entry", "p1": "stop", "e2": "entry", "p2": "stop"}), []

        async def send(_pool, user_id, category, title, body):
            sent.append((title, body))

        client.fills = [fill("e1", "BUY", "60", "2", "2026-11-20T05:00:00Z"), fill("p1", "SELL", "66", "2", "2026-11-20T06:00:00Z"),
                        fill("e2", "SELL", "66", "1", "2026-11-21T07:00:00Z"), fill("p2", "BUY", "68", "1", "2026-11-21T08:00:00Z")]
        tuesday = datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc)
        self.assertIsNone(run(watcher.check_weekly(pool, "u1", client, send=send, now=tuesday)))
        monday = datetime(2026, 11, 23, 7, 0, tzinfo=timezone.utc)
        summary = run(watcher.check_weekly(pool, "u1", client, send=send, now=monday))
        self.assertEqual((summary["trades"], summary["wins"], summary["total"]), (2, 1, "50.00"))
        self.assertIn("2 işlem", sent[0][1])
        self.assertIsNone(run(watcher.check_weekly(pool, "u1", client, send=send, now=monday)))
        self.assertEqual(len(sent), 1)

    def test_weekly_without_trades_sends_nothing(self):
        from datetime import datetime, timezone
        client, pool, sent = FakeClient(), FakePool(), []

        async def send(*args):
            sent.append(args)

        self.assertIsNone(run(watcher.check_weekly(pool, "u1", client, send=send, now=datetime(2026, 11, 23, 7, 0, tzinfo=timezone.utc))))
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
