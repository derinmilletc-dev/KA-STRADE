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


def test_unclean_refusal_opens_pending_and_force_clears_with_audit_event():
    state = locked_state()
    with patch.object(v25_execution, "reconcile", new=AsyncMock()), \
            patch.object(v25_execution, "live_credentials_status", return_value=("k", "s", "fp")), \
            patch.object(v25_execution, "account_snapshot", new=AsyncMock(return_value={"open_orders": [], "open_algo_orders": []})), \
            patch.object(v25_execution, "client_for", return_value=object()), \
            patch.object(v25_execution, "persist_state"):
        with pytest.raises(HTTPException):
            asyncio.run(v25_execution._clear_emergency_locked(request(), {"id": "owner"}, state))
        pending = state["emergency_clear_pending"]
        assert pending["user_id"] == "owner" and pending["expires"] > 0
        result = asyncio.run(v25_execution._clear_emergency_locked(request(), {"id": "owner"}, state, force=True))
    assert result["ok"] is True and result["forced"] is True
    assert state["emergency"]["active"] is False
    assert state["execution_state"] == "LOCKED" and state["real_trading_locked"] is True and state["armed_until"] == 0.0
    assert "emergency_clear_pending" not in state
    assert any(event.get("kind") == "LIVE_EMERGENCY_FORCE_CLEARED" for event in state["events"])


def test_force_request_without_valid_pending_is_refused_before_any_clear():
    from app.v25_execution import EmergencyClearRequest
    state = locked_state()
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(v25_execution=state)))
    body = EmergencyClearRequest(confirmation="ACİL DURDURMAYI KALDIRIYORUM", force_after_warning=True)
    with patch.object(v25_execution, "execution_owner", return_value={"id": "owner"}):
        with pytest.raises(HTTPException) as err:
            asyncio.run(v25_execution.v25_emergency_clear(req, body))
    assert err.value.status_code == 409 and state["emergency"]["active"] is True
    state["emergency_clear_pending"] = {"user_id": "someone-else", "expires": 9e18, "blockers": []}
    with patch.object(v25_execution, "execution_owner", return_value={"id": "owner"}):
        with pytest.raises(HTTPException):
            asyncio.run(v25_execution.v25_emergency_clear(req, body))
    state["emergency_clear_pending"] = {"user_id": "owner", "expires": 1.0, "blockers": []}
    with patch.object(v25_execution, "execution_owner", return_value={"id": "owner"}):
        with pytest.raises(HTTPException):
            asyncio.run(v25_execution.v25_emergency_clear(req, body))
    assert state["emergency"]["active"] is True
