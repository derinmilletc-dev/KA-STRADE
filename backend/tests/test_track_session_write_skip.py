import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app import account_settings


def _run(doc, *, edit_called):
    payload = {"sub": "u1", "jti": "j1", "iat": 100}
    edit = MagicMock(side_effect=lambda *a, **k: edit_called.append(1) or (_ for _ in ()).throw(RuntimeError("edit")))
    with patch.object(account_settings, "verify_token", return_value=payload), \
         patch.object(account_settings.auth, "runtime", return_value={"secret": "s"}), \
         patch.object(account_settings.store, "read", AsyncMock(return_value=doc)), \
         patch.object(account_settings.store, "edit", edit):
        return asyncio.run(account_settings.track_session(SimpleNamespace(), "tok"))


def test_recent_seen_skips_write():
    calls = []
    doc = {"sessions": {"j1": {"issued_at": 200, "last_seen_at": account_settings.iso(time.time() - 5)}}}
    _run(doc, edit_called=calls)
    assert calls == []


def test_stale_seen_writes():
    calls = []
    doc = {"sessions": {"j1": {"issued_at": 200, "last_seen_at": account_settings.iso(time.time() - 600)}}}
    with pytest.raises(RuntimeError):
        _run(doc, edit_called=calls)
    assert calls == [1]


def test_revoked_still_rejected_without_write():
    calls = []
    doc = {"sessions": {"j1": {"issued_at": 200, "revoked": True, "last_seen_at": account_settings.iso()}}}
    with pytest.raises(HTTPException) as exc:
        _run(doc, edit_called=calls)
    assert exc.value.status_code == 401 and calls == []
