"""Çok üyeli canlı işlem: paylaşılan işler bir kez yapılır, üye verileri asla karışmaz."""
from __future__ import annotations

import asyncio

import pytest

from app import binance_demo
from app import original_v2_demo_execution as execution
from app.binance_kline_stream import BinanceKlineStream
from app.live_registry import CURRENT_USER, LiveRegistry, UserAwareState


def _registry_state():
    state = UserAwareState()
    registry = LiveRegistry.__new__(LiveRegistry)
    registry.account_caches = {}
    registry.current = lambda: None
    state.live_registry = registry
    return state


def test_live_account_cache_is_per_member():
    state = _registry_state()
    token = CURRENT_USER.set("alice")
    try:
        assert not hasattr(state, "live_account_cache")
        state.live_account_cache = {"snapshot": "alice"}
    finally:
        CURRENT_USER.reset(token)
    token = CURRENT_USER.set("bob")
    try:
        assert not hasattr(state, "live_account_cache")  # bob alice'in hesabını asla görmez
        state.live_account_cache = {"snapshot": "bob"}
        assert state.live_account_cache == {"snapshot": "bob"}
    finally:
        CURRENT_USER.reset(token)
    token = CURRENT_USER.set("alice")
    try:
        assert state.live_account_cache == {"snapshot": "alice"}
    finally:
        CURRENT_USER.reset(token)


def test_snapshot_lock_is_per_member():
    lock = binance_demo.DEMO_SNAPSHOT_LOCK

    async def scenario():
        alice = CURRENT_USER.set("alice")
        try:
            await lock.acquire()
            assert lock.locked()
        finally:
            CURRENT_USER.reset(alice)
        bob = CURRENT_USER.set("bob")
        try:
            assert not lock.locked()  # alice'in okuması bob'u bekletmez
            await asyncio.wait_for(lock.acquire(), timeout=0.5)
            lock.release()
        finally:
            CURRENT_USER.reset(bob)
        alice = CURRENT_USER.set("alice")
        try:
            with pytest.raises(asyncio.TimeoutError):  # aynı üyenin okumaları yine sırayla
                await asyncio.wait_for(lock.acquire(), timeout=0.05)
            lock.release()
            assert not lock.locked()
        finally:
            CURRENT_USER.reset(alice)

    asyncio.run(scenario())


def _rows(start: int, count: int, step: int = 900_000):
    return [[start + i * step, "1", "2", "0.5", "1.5", "10", start + (i + 1) * step - 1, "15", 1, "5", "7", "0"]
            for i in range(count)]


def _stream(now_ms: int) -> BinanceKlineStream:
    stream = BinanceKlineStream(wall_clock=lambda: now_ms / 1000)
    stream._subscribed = frozenset({("BTCUSDT", "15m")})
    return stream


def test_concurrent_backfill_fetches_once():
    now = 1_700_000_100_000 // 900_000 * 900_000
    stream = _stream(now)
    calls = []

    async def fetch(params):
        calls.append(params)
        await asyncio.sleep(0.01)
        return _rows(now - 9 * 900_000, 10)

    async def scenario():
        return await asyncio.gather(*(stream.get_candles("BTCUSDT", "15m", 10, fetch) for _ in range(8)))

    results = asyncio.run(scenario())
    assert len(calls) == 1 and stream.rest_backfills == 1  # 8 üye, tek indirme
    assert all(len(rows) == 10 for rows in results)
    assert not stream._backfills


def test_backfill_survives_one_waiter_cancelling():
    now = 1_700_000_100_000 // 900_000 * 900_000
    stream = _stream(now)
    calls = []

    async def fetch(params):
        calls.append(params)
        await asyncio.sleep(0.02)
        return _rows(now - 9 * 900_000, 10)

    async def scenario():
        first = asyncio.ensure_future(stream.get_candles("BTCUSDT", "15m", 10, fetch))
        await asyncio.sleep(0)
        second = asyncio.ensure_future(stream.get_candles("BTCUSDT", "15m", 10, fetch))
        await asyncio.sleep(0)
        first.cancel()
        return await second

    assert len(asyncio.run(scenario())) == 10
    assert len(calls) == 1


def test_joined_caller_needing_longer_window_refetches():
    now = 1_700_000_100_000 // 900_000 * 900_000
    stream = _stream(now)
    calls = []

    async def fetch(params):
        calls.append(params["limit"])
        await asyncio.sleep(0.01)
        return _rows(now - (params["limit"] - 1) * 900_000, params["limit"])

    async def scenario():
        return await asyncio.gather(
            stream.get_candles("BTCUSDT", "15m", 5, fetch), stream.get_candles("BTCUSDT", "15m", 20, fetch),
        )

    short, long = asyncio.run(scenario())
    assert len(short) == 5 and len(long) == 20
    assert calls == [5, 20]


def test_strategy_result_shared_and_copied():
    calls = []
    frames = {"15m": [{"open_time": 1, "close": "1"}]}

    def compute():
        calls.append(1)
        return {"decision": "LONG", "checks": [1]}

    first = execution.shared_strategy_result("universe", "30m", "BTCUSDT", 1000, frames, True, compute)
    second = execution.shared_strategy_result("universe", "30m", "BTCUSDT", 1000, frames, True, compute)
    assert first == second and len(calls) == 1
    second["checks"].append(2)  # bir üyenin değişikliği diğerine sızmaz
    third = execution.shared_strategy_result("universe", "30m", "BTCUSDT", 1000, frames, True, compute)
    assert third == {"decision": "LONG", "checks": [1]}
    other = {"15m": [{"open_time": 1, "close": "2"}]}
    execution.shared_strategy_result("universe", "30m", "BTCUSDT", 1000, other, True, compute)
    execution.shared_strategy_result("pullback", "15m", "BTCUSDT", 1000, frames, True, compute)
    assert len(calls) == 3


def test_strategy_errors_are_not_cached():
    frames = {"15m": []}

    def boom():
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        execution.shared_strategy_result("demo", "15m", "ETHUSDT", 5, frames, True, boom)
    assert execution.shared_strategy_result("demo", "15m", "ETHUSDT", 5, frames, True, lambda: "ok") == "ok"
