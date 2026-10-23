import asyncio
from unittest.mock import AsyncMock, patch

from app import v25_execution


def test_positive_auth_reused_briefly_negative_never_cached():
    state = {}
    v25_execution._BACKGROUND_AUTH_RECENT.clear()
    with patch.object(v25_execution, "background_authorization_active", new=AsyncMock(return_value=True)) as full:
        assert asyncio.run(v25_execution.background_authorization_recent(None, state)) is True
        assert asyncio.run(v25_execution.background_authorization_recent(None, state)) is True
        assert full.await_count == 1
    v25_execution._BACKGROUND_AUTH_RECENT.clear()
    other = {}
    with patch.object(v25_execution, "background_authorization_active", new=AsyncMock(return_value=False)) as full:
        assert asyncio.run(v25_execution.background_authorization_recent(None, other)) is False
        assert asyncio.run(v25_execution.background_authorization_recent(None, other)) is False
        assert full.await_count == 2


def test_cache_expires():
    state = {}
    v25_execution._BACKGROUND_AUTH_RECENT.clear()
    with patch.object(v25_execution, "background_authorization_active", new=AsyncMock(return_value=True)) as full:
        asyncio.run(v25_execution.background_authorization_recent(None, state))
        v25_execution._BACKGROUND_AUTH_RECENT[id(state)] -= v25_execution.BACKGROUND_AUTH_RECENT_SECONDS + 1
        asyncio.run(v25_execution.background_authorization_recent(None, state))
        assert full.await_count == 2
