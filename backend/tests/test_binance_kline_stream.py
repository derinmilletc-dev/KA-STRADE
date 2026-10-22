import asyncio
import json

import pytest

from app.binance_kline_stream import BinanceKlineStream, KlineStreamUnavailable
from app.binance_rate_limit import BINANCE_GLOBAL_BAN


def run(awaitable):
    return asyncio.run(awaitable)


class FakeSocket:
    def __init__(self, *, recv_error=None, messages=()):
        self.recv_error = recv_error
        self.messages = list(messages)
        self.sent = []
        self.recv_started = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def recv(self):
        self.recv_started.set()
        if self.messages:
            return self.messages.pop(0)
        if self.recv_error is not None:
            raise self.recv_error
        await asyncio.Event().wait()


class FakeConnector:
    def __init__(self, sockets):
        self.sockets = iter(sockets)
        self.calls = 0
        self.urls = []

    def __call__(self, url, *args, **kwargs):
        self.calls += 1
        self.urls.append(url)
        return next(self.sockets)


def kline_message(symbol="BTCUSDT", interval="15m", open_time=0, close="1", wrapped=False):
    event = {
        "e": "kline",
        "s": symbol,
        "k": {"t": open_time, "T": open_time + 1, "i": interval, "o": "1", "h": "1", "l": "1",
              "c": close, "v": "1", "q": "1", "n": 1, "V": "1", "Q": "1", "B": "0"},
    }
    if wrapped:
        event = {"stream": f"{symbol.lower()}@kline_{interval}", "data": event}
    return json.dumps(event)


def test_configure_subscribes_to_100_symbols_and_all_original_intervals():
    async def scenario():
        socket = FakeSocket()
        connector = FakeConnector([socket])
        stream = BinanceKlineStream(connector=connector)
        symbols = [f"TOKEN{index}USDT" for index in range(100)]

        await stream.configure(symbols)
        try:
            await asyncio.wait_for(socket.recv_started.wait(), timeout=1)
            subscription = socket.sent[0]
            assert subscription["method"] == "SUBSCRIBE"
            assert len(subscription["params"]) == 300
            assert subscription["params"][0] == "token0usdt@kline_15m"
            assert "token99usdt@kline_4h" in subscription["params"]
            assert stream.status()["stream_count"] == 300
        finally:
            await stream.close()

    run(scenario())


def test_ingested_kline_is_cached_and_replaces_the_same_open_candle():
    async def scenario():
        socket = FakeSocket()
        stream = BinanceKlineStream(connector=FakeConnector([socket]))
        await stream.configure(["BTCUSDT"])
        try:
            stream._ingest({
                "e": "kline",
                "s": "BTCUSDT",
                "k": {
                    "i": "15m", "t": 900_000, "T": 1_799_999,
                    "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "10",
                    "q": "15", "n": 4, "V": "5", "Q": "7.5", "B": "0",
                },
            })
            first = stream.candles("BTCUSDT", "15m")
            assert len(first) == 1
            assert first[0][4] == "1.5"

            stream._ingest({
                "e": "kline",
                "s": "BTCUSDT",
                "k": {
                    "i": "15m", "t": 900_000, "T": 1_799_999,
                    "o": "1", "h": "3", "l": "0.5", "c": "2.5", "v": "11",
                },
            })
            cached = stream.candles("BTCUSDT", "15m")
            assert len(cached) == 1
            assert cached[0][4] == "2.5"
            assert stream.messages_received == 2
        finally:
            await stream.close()

    run(scenario())


def test_bootstrap_backfill_runs_once_then_uses_current_stream_cache():
    async def scenario():
        socket = FakeSocket()
        stream = BinanceKlineStream(
            connector=FakeConnector([socket]),
            wall_clock=lambda: 446_400,
        )
        await stream.configure(["BTCUSDT"])
        backfill_calls = []
        rows = [
            [index * 900_000, "1", "2", "0.5", "1.5", "10"]
            for index in range(237, 497)
        ]

        async def fetch(params):
            backfill_calls.append(params)
            return rows

        try:
            first = await stream.get_candles("BTCUSDT", "15m", 260, fetch)
            second = await stream.get_candles("BTCUSDT", "15m", 260, fetch)
            assert len(first) == len(second) == 260
            assert len(backfill_calls) == 1
            assert stream.rest_backfills == 1
        finally:
            await stream.close()

    run(scenario())


def test_active_global_ban_blocks_missing_cache_backfill(monkeypatch):
    async def scenario():
        socket = FakeSocket()
        stream = BinanceKlineStream(connector=FakeConnector([socket]))
        await stream.configure(["BTCUSDT"])

        async def forbidden_fetch(_params):
            raise AssertionError("REST backfill must be blocked during a global ban")

        monkeypatch.setattr(BINANCE_GLOBAL_BAN, "remaining_seconds", lambda: 120)

        try:
            with pytest.raises(KlineStreamUnavailable) as error:
                await stream.get_candles("BTCUSDT", "15m", 260, forbidden_fetch)
            assert error.value.retry_after == 120
            assert stream.rest_backfills == 0
        finally:
            await stream.close()

    run(scenario())


def test_active_ban_keeps_websocket_cache_available_without_any_rest_request(monkeypatch):
    async def scenario():
        socket = FakeSocket()
        stream = BinanceKlineStream(
            connector=FakeConnector([socket]), wall_clock=lambda: 446_400,
        )
        await stream.configure(["BTCUSDT"])
        rows = [[index * 900_000, "1", "2", "0.5", "1.5", "10"] for index in range(237, 497)]
        stream._merge_rows("BTCUSDT", "15m", rows)
        monkeypatch.setattr(BINANCE_GLOBAL_BAN, "remaining_seconds", lambda: 120)

        async def forbidden_fetch(_params):
            raise AssertionError("REST must not be used during a ban")

        try:
            await asyncio.wait_for(socket.recv_started.wait(), timeout=1)
            assert await stream.get_candles("BTCUSDT", "15m", 260, forbidden_fetch) == rows
            assert stream.connected
            assert stream.rest_backfills == 0
        finally:
            await stream.close()

    run(scenario())


def test_stream_reconnects_after_socket_failure(monkeypatch):
    async def scenario():
        second_socket = FakeSocket()
        connector = FakeConnector([FakeSocket(recv_error=OSError("disconnected")), second_socket])
        stream = BinanceKlineStream(connector=connector)
        real_sleep = asyncio.sleep
        monkeypatch.setattr("app.binance_kline_stream.asyncio.sleep", lambda _seconds: real_sleep(0))
        await stream.configure(["BTCUSDT"])

        try:
            await asyncio.wait_for(second_socket.recv_started.wait(), timeout=1)
            assert connector.calls >= 2
            assert stream.reconnects >= 1
        finally:
            await stream.close()

    run(scenario())


def test_stream_connects_to_the_market_route_not_the_retired_legacy_url():
    async def scenario():
        socket = FakeSocket()
        connector = FakeConnector([socket])
        stream = BinanceKlineStream(connector=connector)
        await stream.configure(["BTCUSDT"])
        try:
            await asyncio.wait_for(socket.recv_started.wait(), timeout=1)
            assert connector.urls == ["wss://fstream.binance.com/market/ws"]
        finally:
            await stream.close()

    run(scenario())


def test_raw_and_combined_stream_payloads_update_the_last_candle_in_place():
    stream = BinanceKlineStream(connector=FakeConnector([]))
    stream._symbols, stream._intervals = ("BTCUSDT",), ("15m",)
    stream._subscribed = frozenset({("BTCUSDT", "15m")})
    stream.set_candles("BTCUSDT", "15m", [[0, "1", "1", "1", "1", "1"], [900_000, "1", "1", "1", "1", "1"]])

    assert stream._ingest(json.loads(kline_message(open_time=900_000, close="2")))
    assert stream._ingest(json.loads(kline_message(open_time=1_800_000, close="3", wrapped=True)))
    assert stream._ingest(json.loads(kline_message(open_time=450_000, close="old")))
    assert not stream._ingest(json.loads(kline_message(symbol="ETHUSDT", open_time=1_800_000)))

    rows = stream.candles("BTCUSDT", "15m")
    assert [row[0] for row in rows] == [0, 450_000, 900_000, 1_800_000]
    assert rows[2][4] == "2" and rows[3][4] == "3"
    assert stream.messages_received == 3


def test_silent_connections_back_off_instead_of_reconnecting_every_few_seconds(monkeypatch):
    async def scenario():
        sleeps = []
        done = asyncio.Event()
        sockets = [FakeSocket(recv_error=asyncio.TimeoutError()) for _ in range(4)] + [FakeSocket()]
        connector = FakeConnector(sockets)
        stream = BinanceKlineStream(connector=connector)

        async def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 4:
                done.set()

        monkeypatch.setattr("app.binance_kline_stream.asyncio.sleep", fake_sleep)
        await stream.configure(["BTCUSDT"])
        try:
            await asyncio.wait_for(done.wait(), timeout=1)
            assert sleeps == [1, 2, 4, 8]
            assert stream.last_disconnect_reason == "no_data"
        finally:
            await stream.close()

    run(scenario())


def test_receiving_candles_marks_the_connection_healthy_and_resets_backoff(monkeypatch):
    async def scenario():
        sleeps = []
        last = FakeSocket()
        sockets = [
            FakeSocket(recv_error=asyncio.TimeoutError()),
            FakeSocket(recv_error=asyncio.TimeoutError()),
            FakeSocket(recv_error=OSError("reset"), messages=[kline_message(open_time=900_000)]),
            last,
        ]
        stream = BinanceKlineStream(connector=FakeConnector(sockets))

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr("app.binance_kline_stream.asyncio.sleep", fake_sleep)
        await stream.configure(["BTCUSDT"])
        try:
            await asyncio.wait_for(last.recv_started.wait(), timeout=1)
            assert sleeps == [1, 2, 1]
            assert stream.last_disconnect_reason == "closed"
            assert stream.candles("BTCUSDT", "15m")[-1][0] == 900_000
        finally:
            await stream.close()

    run(scenario())


def test_subscription_error_from_binance_is_reported_and_retried(monkeypatch):
    async def scenario():
        sleeps = []
        last = FakeSocket()
        rejected = FakeSocket(messages=[json.dumps({"error": {"code": 2, "msg": "Invalid request"}, "id": 1})])
        stream = BinanceKlineStream(connector=FakeConnector([rejected, last]))

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr("app.binance_kline_stream.asyncio.sleep", fake_sleep)
        await stream.configure(["BTCUSDT"])
        try:
            await asyncio.wait_for(last.recv_started.wait(), timeout=1)
            assert stream.last_disconnect_reason == "subscribe_rejected"
            assert sleeps == [1]
        finally:
            await stream.close()

    run(scenario())
