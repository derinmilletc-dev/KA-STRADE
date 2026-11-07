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
