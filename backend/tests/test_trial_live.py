"""Ücretsiz deneme üyesi: elle canlı işlem, günde 5 yeni pozisyon; otomatik canlı stratejiler kapalı."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import HTTPException

from app import free_trial, live_access
from app import member_entitlements as entitlements
from app.v25_execution import BinanceLiveClient, LiveExchangeError

TRIAL = {"master_trade_access": True, "plan": "TRIAL", "status": "TRIALING", "mode": "FREE_TRIAL",
         "entitlements": {"canAccessMasterTrade": True}}
PAID = {"master_trade_access": True, "plan": "MASTER_MODE", "status": "ACTIVE", "mode": "BINANCE_MANUAL",
        "entitlements": {"canAccessMasterTrade": True}}
ENDED = {**TRIAL, "master_trade_access": False, "status": "EXPIRED"}
ON = {"MULTI_USER_LIVE": "1", "DATABASE_URL": "postgres://x", "TRIAL_LIVE_ENABLED": "true", "FREE_TRIAL_ENABLED": "true"}


class Conn:
    def __init__(self, role="CUSTOMER"):
        self.role, self.counts, self.refunds = role, {}, 0

    async def fetchrow(self, sql, *args):
        return {"role": self.role}

    async def fetchval(self, sql, *args):
        if sql.lstrip().startswith("INSERT INTO live_trial_entries"):
            key = args[0]
            if self.counts.get(key, 0) >= args[1]:
                return None
            self.counts[key] = self.counts.get(key, 0) + 1
            return self.counts[key]
        return None

    async def execute(self, sql, *args):
        if sql.lstrip().startswith("UPDATE live_trial_entries"):
            self.refunds += 1
            self.counts[args[0]] = max(0, self.counts.get(args[0], 0) - 1)


def app_with(conn):
    @asynccontextmanager
    async def acquire():
        yield conn
    return SimpleNamespace(state=SimpleNamespace(db_pool=SimpleNamespace(acquire=acquire), v22_commercial={"state": {}}))


def enter(application, subscription, *, trial_entry=True, count=True, live=True):
    async def run():
        with mock.patch("app.member_entitlements.target_subscription", mock.AsyncMock(return_value=subscription)):
            return await free_trial.require_background_entry(application, "user-a", live=live, trial_entry=trial_entry, count=count)
    return asyncio.run(run())


@pytest.fixture(autouse=True)
def env():
    with mock.patch.dict("os.environ", ON):
        yield


def test_trial_member_gets_exactly_five_new_positions_per_day():
    conn = Conn()
    application = app_with(conn)
    assert [enter(application, TRIAL) for _ in range(5)] == [True] * 5
    with pytest.raises(HTTPException) as error:
        enter(application, TRIAL)
    assert error.value.status_code == 403 and "günde 5" in error.value.detail
    assert list(conn.counts.values()) == [5]


def test_counter_never_stores_the_raw_user_id():
    conn = Conn()
    enter(app_with(conn), TRIAL)
    assert "user-a" not in conn.counts and all(len(key) == 64 for key in conn.counts)


def test_order_test_validation_does_not_use_a_slot():
    conn = Conn()
    application = app_with(conn)
    for _ in range(8):
        assert enter(application, TRIAL, count=False) is False
    assert conn.counts == {}


def test_background_automation_stays_closed_for_trial():
    conn = Conn()
    with pytest.raises(HTTPException) as error:
        enter(app_with(conn), TRIAL, trial_entry=False)
    assert error.value.status_code == 403 and conn.counts == {}


def test_paid_member_is_unlimited_and_never_counted():
    conn = Conn()
    application = app_with(conn)
    for _ in range(12):
        assert enter(application, PAID) is False
    assert conn.counts == {}


def test_ended_trial_and_flag_off_are_closed():
    conn = Conn()
    with pytest.raises(HTTPException):
        enter(app_with(conn), ENDED)
    with mock.patch.dict("os.environ", {"TRIAL_LIVE_ENABLED": "false"}):
        with pytest.raises(HTTPException):
            enter(app_with(conn), TRIAL)
    assert conn.counts == {}


def test_missing_counter_table_fails_closed():
    from asyncpg import UndefinedTableError

    class Broken(Conn):
        async def fetchval(self, sql, *args):
            raise UndefinedTableError("missing")

    with pytest.raises(HTTPException) as error:
        enter(app_with(Broken()), TRIAL)
    assert error.value.status_code == 503


def test_trial_live_flag_defaults_to_off():
    with mock.patch.dict("os.environ", {"MULTI_USER_LIVE": "1", "DATABASE_URL": "postgres://x"}, clear=False):
        import os
        os.environ.pop("TRIAL_LIVE_ENABLED", None)
        assert live_access.trial_manual(TRIAL) is False
        assert live_access.entitled_manual(TRIAL) is False
    assert live_access.trial_manual(TRIAL) is True and live_access.entitled_manual(PAID) is True
    assert live_access.entitled(TRIAL) is False  # otomatik/ücretli kapılar değişmedi


def test_access_exposes_manual_live_only_for_trial_and_keeps_automation_live_closed():
    rights = entitlements.access(TRIAL, {"role": "CUSTOMER"})
    assert rights["canExecuteLive"] is False and rights["canExecuteLiveManual"] is True
    assert rights["liveTrialDailyLimit"] == 5
    paid = entitlements.access(PAID, {"role": "CUSTOMER"})
    assert paid["canExecuteLive"] is True and paid["liveTrialDailyLimit"] is None
    assert entitlements.access(ENDED, {"role": "CUSTOMER"})["canExecuteLiveManual"] is False


def _client(conn):
    client = BinanceLiveClient.__new__(BinanceLiveClient)
    client.application = app_with(conn)
    client.background_session = False
    client.diagnostic_state = {"live_session_authorization": {"user_id": "user-a"}}
    client._require_global_ban_clear = lambda *a, **k: None
    client.sync_clock = mock.AsyncMock()
    return client


def _signed(client, subscription, request):
    async def run():
        client._request = request
        with mock.patch("app.member_entitlements.target_subscription", mock.AsyncMock(return_value=subscription)):
            return await client.signed("POST", "/fapi/v1/order", {"symbol": "BTCUSDT"})
    return asyncio.run(run())


def test_exchange_rejection_gives_the_slot_back_but_unknown_outcome_does_not():
    conn = Conn()
    client = _client(conn)
    rejected = mock.AsyncMock(side_effect=LiveExchangeError("reddedildi", http_status=400))
    with pytest.raises(LiveExchangeError):
        _signed(client, TRIAL, rejected)
    assert conn.refunds == 1 and list(conn.counts.values()) == [0]
    unknown = mock.AsyncMock(side_effect=LiveExchangeError("belirsiz", unknown_execution=True))
    with pytest.raises(LiveExchangeError):
        _signed(client, TRIAL, unknown)
    assert conn.refunds == 1 and list(conn.counts.values()) == [1]


def test_successful_entry_uses_one_slot_and_sixth_is_blocked_before_the_exchange():
    conn = Conn()
    client = _client(conn)
    sent = mock.AsyncMock(return_value={"orderId": 1})
    for _ in range(5):
        assert _signed(client, TRIAL, sent) == {"orderId": 1}
    blocked = mock.AsyncMock(return_value={"orderId": 2})
    with pytest.raises(LiveExchangeError) as error:
        _signed(client, TRIAL, blocked)
    assert error.value.http_status == 403 and blocked.await_count == 0
