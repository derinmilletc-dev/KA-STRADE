import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app import v25_execution
from app.v25_execution import initial_state


def locked_state():
    state = initial_state()
    state["emergency"].update({"active": True, "reason": "RECONCILIATION_FAILURE"})
    state["execution_state"] = "UNKNOWN"
    state["reconciliation_required"] = True
    return state


def request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


def test_clear_runs_fresh_reconcile_and_returns_ok_when_it_clears():
    state = locked_state()

    async def clean_reconcile(app, credentials=None):
        state["emergency"].update({"active": False, "reason": "RECONCILIATION_CLEAN"})
        state["execution_state"] = "LOCKED"
        state["reconciliation_required"] = False

    with patch.object(v25_execution, "reconcile", new=AsyncMock(side_effect=clean_reconcile)) as rec, \
            patch.object(v25_execution, "live_credentials_status", return_value=("k", "s", "fp")):
        result = asyncio.run(v25_execution._clear_emergency_locked(request(), {"id": "owner"}, state))
    assert rec.await_count == 1
    assert result["ok"] is True and result["emergency"] is False


def test_clear_still_refuses_when_reconcile_stays_unclean():
    state = locked_state()
    with patch.object(v25_execution, "reconcile", new=AsyncMock()), \
            patch.object(v25_execution, "live_credentials_status", return_value=("k", "s", "fp")):
        with pytest.raises(HTTPException) as err:
            asyncio.run(v25_execution._clear_emergency_locked(request(), {"id": "owner"}, state))
    assert err.value.status_code == 409
    assert state["emergency"]["active"] is True
