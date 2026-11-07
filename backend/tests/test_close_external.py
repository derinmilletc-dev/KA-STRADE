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


class SupersededPlanTests(unittest.TestCase):
    def _state(self, *plans):
        return {**live.initial_state(), "plans": {plan["id"]: plan for plan in plans}}

    def test_old_opposite_plan_is_retired_when_new_matching_plan_exists(self):
        old = {"id": "plan-old", "symbol": "PUMPUSDT", "direction": "LONG", "status": "KORUMA AKTİF"}
        new = {"id": "plan-new", "symbol": "PUMPUSDT", "direction": "SHORT", "status": "KORUMA AKTİF"}
        state = self._state(old, new)
        self.assertTrue(live.retire_superseded_plan(state, old, {"symbol": "PUMPUSDT", "direction": "SHORT", "quantity": "26001"}))
        self.assertEqual(old["status"], "KAPANDI")
        self.assertEqual(new["status"], "KORUMA AKTİF")

    def test_mismatch_without_successor_is_left_alone(self):
        old = {"id": "plan-old", "symbol": "PUMPUSDT", "direction": "LONG", "status": "KORUMA AKTİF"}
        state = self._state(old)
        self.assertFalse(live.retire_superseded_plan(state, old, {"symbol": "PUMPUSDT", "direction": "SHORT"}))
        self.assertEqual(old["status"], "KORUMA AKTİF")

    def test_matching_plan_and_missing_position_untouched(self):
        plan = {"id": "plan-a", "symbol": "PUMPUSDT", "direction": "SHORT", "status": "KORUMA AKTİF"}
        state = self._state(plan)
        self.assertFalse(live.retire_superseded_plan(state, plan, {"symbol": "PUMPUSDT", "direction": "SHORT"}))
        self.assertFalse(live.retire_superseded_plan(state, plan, None))


class OrphanBrokenPlanTests(unittest.TestCase):
    def test_broken_plan_without_position_is_retired(self):
        plan = {"id": "p1", "symbol": "PUMPUSDT", "direction": "LONG", "status": "YÖN UYUŞMAZLIĞI", "provenance_state": "BROKEN"}
        state = {**live.initial_state(), "plans": {"p1": plan}}
        self.assertTrue(live.retire_orphan_broken_plan(state, plan, None))
        self.assertEqual(plan["status"], "KAPANDI")

    def test_position_pending_fill_and_confirmed_plans_untouched(self):
        state = {**live.initial_state(), "plans": {}}
        broken = {"id": "p1", "symbol": "PUMPUSDT", "status": "KORUMA AKTİF", "provenance_state": "BROKEN"}
        self.assertFalse(live.retire_orphan_broken_plan(state, broken, {"symbol": "PUMPUSDT"}))
        pending = {"id": "p2", "symbol": "PUMPUSDT", "status": "DOLUM BEKLİYOR", "provenance_state": "BROKEN"}
        self.assertFalse(live.retire_orphan_broken_plan(state, pending, None))
        ok = {"id": "p3", "symbol": "PUMPUSDT", "status": "KORUMA AKTİF", "provenance_state": "CONFIRMED"}
        self.assertFalse(live.retire_orphan_broken_plan(state, ok, None))
        self.assertEqual(ok["status"], "KORUMA AKTİF")
