"""Original V2 LIVE: çalışan oturumu uzatma. Yalnız bitiş ileri alınır; başka hiçbir şey değişmez."""
import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import original_v2_live as original, v25_execution as live
from test_original_live_risk import context


def _setup(monkeypatch, *, left=3000, consent_left=48 * 3600, persist=None, running=True):
    request, state, _ = context()
    state["recovery_ready"] = True
    now = time.time()
    config = state["original_v2_live"]
    config.update(enabled=running, paused=False, session_expires_at_epoch=now + left, session_hours=1,
                  session_warning_sent=True, session_authority_limited=False)
    state["auto"]["session_until"] = now + left
    state["auto_authorization"]["expires_at_epoch"] = now + left
    monkeypatch.setenv(original.FLAG, "true")
    monkeypatch.setattr(original.live_access, "require_live_member", Mock(return_value={"id": "owner", "role": "OWNER"}))
    monkeypatch.setattr(live, "execution_owner", Mock())
    understanding = AsyncMock()
    monkeypatch.setattr(live, "require_live_understanding", understanding)
    monkeypatch.setattr(live, "persist_state", Mock())
    monkeypatch.setattr(live, "consent_status", Mock(return_value={
        "active": True, "expires_at": datetime.fromtimestamp(now + consent_left, timezone.utc).isoformat()}))
    monkeypatch.setattr(original, "persist_required", persist or AsyncMock())
    monkeypatch.setattr(original, "status", AsyncMock(side_effect=lambda req: original.public_config(state)))
    return request, state, now, understanding


def _extend(request, hours=12, understood=True):
    return asyncio.run(original.extend_session(request, original.ExtendSession(session_hours=hours, understood=understood)))


def test_extension_moves_only_the_end_time_and_resets_the_one_hour_warning(monkeypatch):
    request, state, now, understanding = _setup(monkeypatch)
    config = state["original_v2_live"]
    before = {key: config[key] for key in ("risk_percent", "daily_loss_percent", "owner_id", "fingerprint", "enabled", "paused")}
    plans_before = dict(state["plans"])
    _extend(request, 12)
    assert config["session_expires_at_epoch"] == pytest.approx(now + 12 * 3600, abs=30)
    assert state["auto"]["session_until"] == config["session_expires_at_epoch"]
    assert state["auto_authorization"]["expires_at_epoch"] == config["session_expires_at_epoch"]
    assert config["session_hours"] == 12 and config["session_warning_sent"] is False
    assert config["session_authority_limited"] is False
    assert {key: config[key] for key in before} == before
    assert state["plans"] == plans_before
    assert [event["kind"] for event in state["events"]].count("ORIGINAL_SESSION_EXTENDED") == 1
    understanding.assert_awaited_once()


def test_extension_is_capped_by_the_risk_consent_and_says_so(monkeypatch):
    request, state, now, _ = _setup(monkeypatch, consent_left=5 * 3600)
    _extend(request, 24)
    config = state["original_v2_live"]
    assert config["session_expires_at_epoch"] == pytest.approx(now + 5 * 3600, abs=30)
    assert config["session_authority_limited"] is True
    notice = [event for event in state["events"] if event["kind"] == "ORIGINAL_SESSION_EXTENDED"][0]
    assert "risk iziniyle sınırlı" in notice["message"]


@pytest.mark.parametrize("hours,consent_left", [(1, 48 * 3600), (4, 3030)])
def test_extension_that_would_not_lengthen_the_session_is_refused_and_changes_nothing(monkeypatch, hours, consent_left):
    request, state, now, _ = _setup(monkeypatch, left=3 * 3600 + 3000 if hours == 1 else 3000, consent_left=consent_left)
    config = state["original_v2_live"]
    before = (config["session_expires_at_epoch"], state["auto"]["session_until"], config["session_warning_sent"])
    with pytest.raises(HTTPException) as error:
        _extend(request, hours)
    assert error.value.status_code == 409
    assert (config["session_expires_at_epoch"], state["auto"]["session_until"], config["session_warning_sent"]) == before


def test_not_running_or_expired_session_cannot_be_extended(monkeypatch):
    request, state, _, _ = _setup(monkeypatch, running=False)
    with pytest.raises(HTTPException) as error:
        _extend(request)
    assert error.value.status_code == 409
    request, state, _, _ = _setup(monkeypatch, left=-5)
    with pytest.raises(HTTPException) as error:
        _extend(request)
    assert error.value.status_code == 409


def test_paused_session_cannot_be_extended(monkeypatch):
    request, state, _, _ = _setup(monkeypatch)
    state["original_v2_live"]["paused"] = True
    with pytest.raises(HTTPException) as error:
        _extend(request)
    assert error.value.status_code == 409


def test_failed_durable_save_restores_every_session_field(monkeypatch):
    failing = AsyncMock(side_effect=live.LiveExchangeError("kayıt yok", http_status=503))
    request, state, now, _ = _setup(monkeypatch, persist=failing)
    config = state["original_v2_live"]
    before = {key: config.get(key) for key in ("session_expires_at_epoch", "session_hours", "session_warning_sent", "session_authority_limited")}
    auto_before = (state["auto"]["session_until"], state["auto_authorization"]["expires_at_epoch"])
    with pytest.raises(HTTPException) as error:
        _extend(request, 24)
    assert error.value.status_code == 503
    assert {key: config.get(key) for key in before} == before
    assert (state["auto"]["session_until"], state["auto_authorization"]["expires_at_epoch"]) == auto_before
    assert not [event for event in state["events"] if event["kind"] == "ORIGINAL_SESSION_EXTENDED"]


def test_only_the_owner_extends_and_consent_must_be_verified(monkeypatch):
    request, state, _, _ = _setup(monkeypatch)
    state["original_v2_live"]["owner_id"] = "baska-hesap"
    with pytest.raises(HTTPException) as error:
        _extend(request)
    assert error.value.status_code == 403
    request, state, _, _ = _setup(monkeypatch)
    monkeypatch.setattr(live, "consent_status", Mock(return_value={"active": False}))
    with pytest.raises(HTTPException) as error:
        _extend(request)
    assert error.value.status_code == 423


@pytest.mark.parametrize("hours", [0, 2, 48, 1.5, "4", True])
def test_only_known_durations_are_accepted(hours):
    with pytest.raises(ValidationError):
        original.ExtendSession(session_hours=hours)
