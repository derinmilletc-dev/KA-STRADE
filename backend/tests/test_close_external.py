import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).parents[1]
sys.path.insert(0, str(BACKEND))

from fastapi import HTTPException  # noqa: E402

from app import v25_execution as live  # noqa: E402


def _request(state):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(v25_execution=state)))


def _run(state, body):
    with patch.object(live, "execution_owner", return_value={"id": "owner"}), patch.object(live, "client_for", return_value=object()), \
            patch.object(live, "persist_state", lambda s: None):
        return asyncio.run(live.v25_close_external(_request(state), body))


class CloseExternalTests(unittest.TestCase):
    def test_closes_open_exchange_position_reduce_only(self):
        state = {**live.initial_state(), "lock": asyncio.Lock()}
        close = AsyncMock(return_value={"orderId": 5, "avgPrice": "0.0038", "status": "FILLED"})
        with patch.object(live, "close_tracked_symbol", close):
            result = _run(state, live.CloseExternalRequest(symbol="PUMPUSDT", confirmation="CANLI POZİSYONU KAPAT"))
        self.assertTrue(result["ok"])
        self.assertEqual(close.await_args.args[1], "PUMPUSDT")
        self.assertTrue(close.await_args.args[2].startswith("manual-close-ext-PUMPUSDT-"))

    def test_wrong_phrase_and_missing_position_are_rejected(self):
        state = {**live.initial_state(), "lock": asyncio.Lock()}
        with patch.object(live, "close_tracked_symbol", AsyncMock()) as close:
            with self.assertRaises(HTTPException) as ctx:
                _run(state, live.CloseExternalRequest(symbol="PUMPUSDT", confirmation="evet"))
            self.assertEqual(ctx.exception.status_code, 422)
            close.assert_not_awaited()
        with patch.object(live, "close_tracked_symbol", AsyncMock(return_value=None)):
            with self.assertRaises(HTTPException) as ctx:
                _run(state, live.CloseExternalRequest(symbol="PUMPUSDT", confirmation="CANLI POZİSYONU KAPAT"))
            self.assertEqual(ctx.exception.status_code, 409)

    def test_symbol_is_validated(self):
        with self.assertRaises(Exception):
            live.CloseExternalRequest(symbol="pump;drop", confirmation="x")


if __name__ == "__main__":
    unittest.main()


class CloseUnverifiedPlanTests(unittest.TestCase):
    def test_unverified_plan_is_closed_by_symbol(self):
        plan = {"id": "plan-pump-1", "symbol": "PUMPUSDT", "direction": "LONG", "provenance_state": "BROKEN"}
        state = {**live.initial_state(), "lock": asyncio.Lock(), "plans": {"plan-pump-1": plan}}
        close = AsyncMock(return_value={"orderId": 9, "avgPrice": "0.0038", "status": "FILLED"})
        with patch.object(live, "execution_owner", return_value={"id": "owner"}), patch.object(live, "client_for", return_value=object()), \
                patch.object(live, "persist_state", lambda s: None), patch.object(live, "close_tracked_symbol", close):
            result = asyncio.run(live.v25_close(_request(state), live.CloseRequest(plan_id="plan-pump-1", confirmation="CANLI POZİSYONU KAPAT")))
        self.assertTrue(result["ok"])
        self.assertEqual(close.await_args.args[1], "PUMPUSDT")


class CloseRetryTests(unittest.TestCase):
    def _client(self, existing_status):
        calls = []

        class Client:
            async def signed(self, method, path, params):
                calls.append((method, path, dict(params)))
                if path == "/fapi/v3/positionRisk":
                    return [{"symbol": "PUMPUSDT", "positionAmt": "-26001"}]
                if method == "GET" and path == "/fapi/v1/order":
                    return {"orderId": 1, "status": existing_status}
                return {"orderId": 2, "status": "FILLED", "avgPrice": "0.0038"}
        return Client(), calls

    def test_stale_previous_close_order_is_not_reused(self):
        for status in ("FILLED", "CANCELED", "EXPIRED", "REJECTED"):
            client, calls = self._client(status)
            result = asyncio.run(live.close_tracked_symbol(client, "PUMPUSDT", "manual-close-plan-1"))
            self.assertEqual(result["orderId"], 2, status)
            post = [c for c in calls if c[0] == "POST"][0][2]
            self.assertEqual(post["side"], "BUY")
            self.assertEqual(post["reduceOnly"], "true")
            self.assertEqual(post["quantity"], "26001")

    def test_in_flight_close_order_is_reused(self):
        client, calls = self._client("NEW")
        result = asyncio.run(live.close_tracked_symbol(client, "PUMPUSDT", "manual-close-plan-1"))
        self.assertEqual(result["orderId"], 1)
        self.assertFalse([c for c in calls if c[0] == "POST"])
