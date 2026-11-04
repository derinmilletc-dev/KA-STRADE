"""Manuel LIMIT: süreli emir GTD, süresiz emir GTC; süre Binance sunucu saatine göre; kurtarmada süre tipi doğrulanır."""
import asyncio
import sys
import time
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import v25_execution as execution  # noqa: E402

RULES = {"tick": Decimal("0.1"), "step": Decimal("0.001"), "min_qty": Decimal("0.001"),
         "min_notional": Decimal("5"), "max_qty": Decimal("1000")}


def build_spec(client=None, **fields):
    order = dict(symbol="BTCUSDT", direction="LONG", order_type="LIMIT", limit_price=50000, margin_usdt=100, leverage=10,
                 stop_loss=49500, tp1=50500, tp2=51000, tp3=51500)
    order.update(fields)
    policy = {**execution.initial_state()["policy"], "allow_long": True, "allow_short": True, "allowed_symbols": [],
              "max_margin_per_trade": 100, "max_leverage": 10, "max_loss_per_trade": 5, "minimum_net_reward_usdt": 0}
    with patch.object(execution, "ticker_price", AsyncMock(return_value=Decimal("50100"))), \
            patch.object(execution, "live_symbol_rules", AsyncMock(return_value=RULES)):
        return asyncio.run(execution.build_live_spec(client or object(), execution.LiveOrderRequest(**order), policy,
                                                     manual_targets=True))


class FakeExchange(execution.BinanceLiveClient):
    """Yalnız emir uçları: GET (sorgu) ve POST (gönderim)."""

    def __init__(self, existing=None, offset_ms=0, post_error=None, after_post=None):
        self.existing = existing
        self.time_offset_ms = offset_ms
        self.post_error = post_error
        self.after_post = after_post
        self.posts = []

    async def signed(self, method, path, params=None):
        if method == "GET" and path == "/fapi/v1/order":
            if self.existing is None:
                raise execution.LiveExchangeError("yok", exchange_code=-2013)
            return self.existing
        if method == "POST":
            self.posts.append(dict(params or {}))
            if self.post_error is not None:
                self.existing = self.after_post
                raise self.post_error
            return {"orderId": 77, "status": "NEW"}
        raise AssertionError((method, path))


def exchange_order(spec, client_id, **overrides):
    row = {"orderId": 77, "clientOrderId": client_id, "symbol": spec["symbol"], "side": "BUY", "positionSide": "BOTH",
           "type": "LIMIT", "origQty": spec["quantity"], "status": "NEW", "timeInForce": spec["time_in_force"]}
    if spec.get("good_till_ms"):
        row["goodTillDate"] = spec["good_till_ms"]
    row.update(overrides)
    return row


class LimitTimeInForceTests(unittest.TestCase):
    def test_spec_marks_gtc_without_expiry_and_gtd_with_expiry(self):
        self.assertEqual(build_spec()["time_in_force"], "GTC")
        self.assertNotIn("good_till_ms", build_spec())
        timed = build_spec(limit_expiry_minutes=240)
        self.assertEqual(timed["time_in_force"], "GTD")
        self.assertEqual(timed["limit_expiry_minutes"], 240)
        market = build_spec(order_type="MARKET", limit_price=None, limit_expiry_minutes=60)
        self.assertNotIn("time_in_force", market)  # piyasa emrinde süre tipi yok
        self.assertNotIn("good_till_ms", market)

    def test_deadline_follows_binance_server_clock(self):
        offset = 7 * 60_000  # yerel saat Binance'ten 7 dk geride
        before = int(time.time() * 1000)
        spec = build_spec(SimpleNamespace(time_offset_ms=offset), limit_expiry_minutes=60)
        expected = before + offset + 3_600_000
        self.assertLess(abs(spec["good_till_ms"] - expected), 2_000)

    def submit(self, client, spec, cid="PTB_ENTRY_test"):
        return asyncio.run(execution.submit_entry(client, {**spec, "side": "BUY"}, cid, test_only=False))

    def test_submit_sends_gtc_or_gtd_params(self):
        plain = build_spec()
        client = FakeExchange()
        self.submit(client, plain)
        self.assertEqual(client.posts[-1]["timeInForce"], "GTC")
        self.assertNotIn("goodTillDate", client.posts[-1])
        timed = build_spec(limit_expiry_minutes=60)
        self.submit(client, timed)
        self.assertEqual(client.posts[-1]["timeInForce"], "GTD")
        self.assertEqual(client.posts[-1]["goodTillDate"], timed["good_till_ms"])

    def test_late_gtd_submission_is_refused_without_sending(self):
        spec = build_spec(limit_expiry_minutes=60)
        spec["good_till_ms"] = int(time.time() * 1000) + 5 * 60_000  # yeniden deneme gecikti: 5 dk kaldı
        client = FakeExchange()
        with self.assertRaises(execution.LiveExchangeError) as raised:
            self.submit(client, spec)
        self.assertEqual(raised.exception.check_key, "limit_expiry")
        self.assertEqual(client.posts, [])

    def test_recovered_order_must_have_the_same_time_in_force(self):
        spec = build_spec(limit_expiry_minutes=60)
        cid = "PTB_ENTRY_test"
        same = FakeExchange(existing=exchange_order(spec, cid, goodTillDate=spec["good_till_ms"] // 1000 * 1000))
        self.assertTrue(self.submit(same, spec, cid)["recovered"])  # saniyeye yuvarlanmış süre kabul
        wrong_type = FakeExchange(existing=exchange_order(spec, cid, timeInForce="GTC"))
        with self.assertRaises(execution.LiveExchangeError) as raised:
            self.submit(wrong_type, spec, cid)
        self.assertTrue(raised.exception.unknown_execution)
        wrong_deadline = FakeExchange(existing=exchange_order(spec, cid, goodTillDate=spec["good_till_ms"] + 3_600_000))
        with self.assertRaises(execution.LiveExchangeError):
            self.submit(wrong_deadline, spec, cid)
        self.assertEqual(wrong_type.posts + wrong_deadline.posts, [])  # yabancı emir varken yeni emir gönderilmez

    def test_timeout_then_recovery_checks_time_in_force(self):
        spec = build_spec()
        cid = "PTB_ENTRY_test"
        timeout = execution.LiveExchangeError("zaman aşımı", unknown_execution=True)
        good = FakeExchange(post_error=timeout, after_post=exchange_order(spec, cid))
        with patch.object(execution, "ORDER_RECONCILIATION_DELAYS_SECONDS", ()):
            self.assertTrue(self.submit(good, spec, cid)["recovered"])
            bad = FakeExchange(post_error=timeout, after_post=exchange_order(spec, cid, timeInForce="GTD"))
            with self.assertRaises(execution.LiveExchangeError):
                self.submit(bad, spec, cid)

    def test_specs_rebuilt_from_old_plans_keep_old_behaviour(self):
        order = {"clientOrderId": "c", "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT", "origQty": "0.01", "timeInForce": "GTD"}
        rebuilt = {"symbol": "BTCUSDT", "side": "BUY", "order_type": "LIMIT", "quantity": "0.01"}
        self.assertTrue(execution.order_matches_spec(order, rebuilt, "c"))  # süre bilgisi yok: kontrol atlanır
        self.assertTrue(execution.order_matches_spec({**order, "timeInForce": None}, {**rebuilt, "time_in_force": "GTC"}, "c"))

    def test_partially_filled_then_expired_gtd_entry_settles_without_lock(self):
        deadline = int(time.time() * 1000) + 3_600_000
        plan = {"id": "p1", "symbol": "BTCUSDT", "direction": "LONG", "order_type": "LIMIT", "quantity": "0.004",
                "requested_quantity": "0.010", "entry_order_id": 77, "entry_client_order_id": f"{execution.LIVE_CLIENT_PREFIX}ENTRY_x",
                "good_till_ms": deadline}
        row = {"orderId": 77, "clientOrderId": plan["entry_client_order_id"], "symbol": "BTCUSDT", "side": "BUY",
               "positionSide": "BOTH", "type": "LIMIT", "origQty": "0.010", "executedQty": "0.004", "status": "EXPIRED",
               "timeInForce": "GTD", "goodTillDate": deadline}
        state = execution.initial_state()
        with patch.object(execution, "persist_state"):
            asyncio.run(execution.settle_live_entry_remainder(FakeExchange(existing=row), state, plan))
        self.assertEqual(plan["entry_cancellation_state"], "EXPIRED")
        self.assertFalse(execution.entry_remainder_needs_settlement(plan))
        self.assertFalse(state.get("reconciliation_required"))


if __name__ == "__main__":
    unittest.main()
