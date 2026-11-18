"""Kapatma bitince hesap görünümü hemen tazelenir; tazeleme başarısızsa kapatma sonucu bozulmaz."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import v25_execution  # noqa: E402


def _setup():
    plan = {"id": "plan-123456", "provenance_state": "CONFIRMED", "symbol": "BTCUSDT", "status": "KORUMA AKTİF",
            "close_client_order_ids": []}
    state = v25_execution.initial_state()
    state["lock"] = asyncio.Lock()
    state["plans"] = {plan["id"]: plan}
    state["snapshot"] = {"positions": [{"symbol": "BTCUSDT"}]}
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(v25_execution=state)))
    body = v25_execution.CloseRequest(plan_id=plan["id"], confirmation="CANLI POZİSYONU KAPAT")
    return state, request, body


def test_close_updates_snapshot_right_away():
    state, request, body = _setup()
    fresh = {"positions": [], "wallet_balance": 100.0}

    async def scenario():
        with patch.object(v25_execution, "execution_owner", return_value={"id": "owner"}), \
                patch.object(v25_execution, "client_for", return_value=object()), \
                patch.object(v25_execution, "close_tracked_symbol", new=AsyncMock(return_value={"orderId": 3})), \
                patch.object(v25_execution, "account_snapshot", new=AsyncMock(return_value=fresh)), \
                patch.object(v25_execution, "persist_state"):
            return await v25_execution.v25_close(request, body)

    result = asyncio.run(scenario())
    assert result["ok"] is True
    assert state["snapshot"] == fresh, "kapanan pozisyon listede kalmamalı"


def test_close_still_succeeds_when_snapshot_read_fails():
    state, request, body = _setup()
    before = state["snapshot"]

    async def scenario():
        with patch.object(v25_execution, "execution_owner", return_value={"id": "owner"}), \
                patch.object(v25_execution, "client_for", return_value=object()), \
                patch.object(v25_execution, "close_tracked_symbol", new=AsyncMock(return_value={"orderId": 3})), \
                patch.object(v25_execution, "account_snapshot", new=AsyncMock(side_effect=RuntimeError("boom"))), \
                patch.object(v25_execution, "persist_state"):
            return await v25_execution.v25_close(request, body)

    result = asyncio.run(scenario())
    assert result["ok"] is True and state["snapshot"] is before
