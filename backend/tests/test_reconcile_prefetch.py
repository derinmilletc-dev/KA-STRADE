"""Periyodik uzlaştırma: yavaş Binance hesap sorgusu state kilidinin DIŞINDA yapılır (kapat/iptal beklemesin).

Karar mantığı (reconcile) değişmedi; yalnız ağ beklemesi kilit dışına alındı.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import v25_execution  # noqa: E402


def _application():
    state = v25_execution.initial_state()
    state["lock"] = asyncio.Lock()
    return SimpleNamespace(state=SimpleNamespace(v25_execution=state)), state


def test_snapshot_is_fetched_outside_the_lock_and_handed_to_reconcile():
    application, state = _application()
    seen = {}
    snapshot = {"positions": [], "marker": "prefetched"}

    async def fetch(*_args, **_kwargs):
        seen["locked_during_fetch"] = state["lock"].locked()
        return snapshot

    async def reconcile(_application, credentials=None, snapshot=None):
        seen["locked_during_reconcile"] = state["lock"].locked()
        seen["snapshot"] = snapshot

    fetch_mock, reconcile_mock = AsyncMock(side_effect=fetch), AsyncMock(side_effect=reconcile)
    with patch.object(v25_execution, "client_for_with_credentials", return_value=object()), \
            patch.object(v25_execution, "account_snapshot", new=fetch_mock), \
            patch.object(v25_execution, "reconcile", new=reconcile_mock):
        asyncio.run(v25_execution.reconcile_with_prefetch(application, credentials=("k", "s")))
    assert seen["locked_during_fetch"] is False, "Binance sorgusu kilit altında yapılmamalı"
    assert seen["locked_during_reconcile"] is True, "karar mantığı eskisi gibi kilit altında çalışmalı"
    assert seen["snapshot"] is snapshot
    assert fetch_mock.await_count == 1


def test_close_can_take_the_lock_while_the_slow_snapshot_is_in_flight():
    application, state = _application()
    order = []

    async def slow_fetch(*_args, **_kwargs):
        order.append("fetch_start")
        await asyncio.sleep(0.3)
        order.append("fetch_end")
        return {"positions": []}

    async def reconcile(_application, credentials=None, snapshot=None):
        order.append("reconcile")

    async def close_request():
        await asyncio.sleep(0.05)
        started = asyncio.get_event_loop().time()
        async with state["lock"]:
            order.append("close_got_lock")
            return asyncio.get_event_loop().time() - started

    async def scenario():
        loop_task = asyncio.create_task(v25_execution.reconcile_with_prefetch(application, credentials=("k", "s")))
        waited = await close_request()
        await loop_task
        return waited

    with patch.object(v25_execution, "client_for_with_credentials", return_value=object()), \
            patch.object(v25_execution, "account_snapshot", new=slow_fetch), \
            patch.object(v25_execution, "reconcile", new=reconcile):
        waited = asyncio.run(scenario())
    assert waited < 0.1, f"kapatma yavaş sorgu yüzünden {waited:.2f} sn bekledi"
    assert order.index("close_got_lock") < order.index("fetch_end")


def test_stale_prefetch_is_discarded_and_reconcile_fetches_its_own():
    application, state = _application()
    received = {}

    async def reconcile(_application, credentials=None, snapshot=None):
        received["snapshot"] = snapshot

    async def scenario():
        await state["lock"].acquire()                 # başka bir işlem (kapatma/giriş) kilidi tutuyor
        task = asyncio.create_task(v25_execution.reconcile_with_prefetch(application, credentials=("k", "s")))
        await asyncio.sleep(0.12)                     # bu sürede pozisyon değişmiş olabilir
        state["lock"].release()
        await task

    with patch.object(v25_execution, "client_for_with_credentials", return_value=object()), \
            patch.object(v25_execution, "account_snapshot", new=AsyncMock(return_value={"positions": [{"symbol": "BTCUSDT"}]})), \
            patch.object(v25_execution, "reconcile", new=reconcile), \
            patch.object(v25_execution, "PREFETCH_MAX_AGE_SECONDS", 0.05):
        asyncio.run(scenario())
    assert received["snapshot"] is None, "bayat görüntü karar için kullanılmamalı"


def test_prefetch_failure_falls_back_to_reconcile_own_fetch():
    application, _state = _application()
    received = {}

    async def reconcile(_application, credentials=None, snapshot=None):
        received["snapshot"] = snapshot

    with patch.object(v25_execution, "client_for_with_credentials", return_value=object()), \
            patch.object(v25_execution, "account_snapshot", new=AsyncMock(side_effect=v25_execution.LiveRateLimitError("limit", retry_after=3))), \
            patch.object(v25_execution, "reconcile", new=reconcile):
        asyncio.run(v25_execution.reconcile_with_prefetch(application, credentials=("k", "s")))
    assert received["snapshot"] is None


def test_reconcile_errors_still_propagate_and_release_the_lock():
    application, state = _application()

    async def scenario():
        with pytest.raises(v25_execution.LiveRateLimitError):
            await v25_execution.reconcile_with_prefetch(application, credentials=("k", "s"))
        return state["lock"].locked()

    with patch.object(v25_execution, "client_for_with_credentials", return_value=object()), \
            patch.object(v25_execution, "account_snapshot", new=AsyncMock(return_value={"positions": []})), \
            patch.object(v25_execution, "reconcile", new=AsyncMock(side_effect=v25_execution.LiveRateLimitError("limit", retry_after=3))):
        still_locked = asyncio.run(scenario())
    assert still_locked is False


def test_reconcile_uses_a_given_snapshot_without_a_second_binance_query():
    application, state = _application()
    fetch = AsyncMock(side_effect=AssertionError("ikinci sorgu yapılmamalı"))
    snapshot = {"positions": [], "open_algo_orders": []}
    stop = RuntimeError("snapshot kabul edildi")
    with patch.object(v25_execution, "client_for_with_credentials", return_value=SimpleNamespace(time_offset_ms=0)), \
            patch.object(v25_execution, "account_snapshot", new=fetch), \
            patch.object(v25_execution, "update_account_snapshot", side_effect=stop):
        with pytest.raises(RuntimeError) as raised:
            asyncio.run(v25_execution.reconcile(application, credentials=("k", "s"), snapshot=snapshot))
    assert raised.value is stop and fetch.await_count == 0


def test_reconcile_without_snapshot_still_queries_binance_like_before():
    application, state = _application()
    fetch = AsyncMock(return_value={"positions": []})
    stop = RuntimeError("devam etti")
    with patch.object(v25_execution, "client_for_with_credentials", return_value=SimpleNamespace(time_offset_ms=0)), \
            patch.object(v25_execution, "account_snapshot", new=fetch), \
            patch.object(v25_execution, "update_account_snapshot", side_effect=stop):
        with pytest.raises(RuntimeError):
            asyncio.run(v25_execution.reconcile(application, credentials=("k", "s")))
    assert fetch.await_count == 1


def test_loop_uses_the_prefetching_wrapper_not_a_bare_locked_reconcile():
    source = Path(v25_execution.__file__).read_text(encoding="utf-8")
    assert "await reconcile_with_prefetch(application, credentials=credentials)" in source
    assert 'async with application.state.v25_execution["lock"]:\n                await reconcile(application, credentials=credentials)' not in source
