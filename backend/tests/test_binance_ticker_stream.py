import asyncio
import json

import httpx

from app.binance_ticker_stream import BinanceTickerStream, MIN_SYMBOLS, WARMUP_SECONDS, MAX_AGE_SECONDS, TICKER_STREAM
from app.public_market_data import PublicMarketData


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeSocket:
    def __init__(self, messages=()):
        self.messages = list(messages)
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def recv(self):
        if self.messages:
            return self.messages.pop(0)
        await asyncio.Event().wait()


class FakeConnector:
    def __init__(self, sockets):
        self.sockets = iter(sockets)
        self.urls = []

    def __call__(self, url, *args, **kwargs):
        self.urls.append(url)
        return next(self.sockets)


def tick(symbol, last="10", percent="1.5", quote="2000000"):
    return {"e": "24hrTicker", "E": 1, "s": symbol, "p": "0.1", "P": percent, "w": "9.9", "c": last, "Q": "1",
            "o": "9.9", "h": "11", "l": "9", "v": "100", "q": quote, "O": 1, "C": 2, "F": 1, "L": 2, "n": 5}


def batch(count, prefix="T"):
    return json.dumps([tick(f"{prefix}{index}USDT") for index in range(count)])


def healthy_stream(clock, symbols=MIN_SYMBOLS):
    stream = BinanceTickerStream(monotonic=clock, connector=FakeConnector([FakeSocket()]))
    stream.enable()
    stream.connected = True
    stream.first_message_at = clock.now
    stream.last_message_at = clock.now
    stream._ingest(json.loads(batch(symbols)))
    return stream


def test_ingest_maps_ws_fields_to_rest_shape_and_rejects_bad_rows():
    stream = BinanceTickerStream()
    bad = tick("BADUSDT", last="nan")
    assert stream._ingest([tick("BTCUSDT", last="65000.5", percent="-2.5", quote="123456789"), bad, {"e": "other"}]) == 1
    stream.enable(); stream.connected = True
    row = stream._tickers["BTCUSDT"]
    assert row["lastPrice"] == "65000.5" and row["priceChangePercent"] == "-2.5" and row["quoteVolume"] == "123456789"
    assert {"symbol", "volume", "count", "openTime", "closeTime"} <= set(row)
    assert stream.invalid_messages == 1
    assert stream._ingest({"stream": "!ticker@arr", "data": [tick("ETHUSDT")]}) == 1


def test_snapshot_requires_warmup_freshness_and_coverage():
    clock = Clock()
    stream = healthy_stream(clock)
    assert stream.snapshot() is None  # ısınmadı
    clock.now += WARMUP_SECONDS + 1
    stream.last_message_at = clock.now
    assert len(stream.snapshot()) == MIN_SYMBOLS
    clock.now += MAX_AGE_SECONDS + 1
    assert stream.snapshot() is None  # bayat
    stream.last_message_at = clock.now
    stream.connected = False
    assert stream.snapshot() is None  # koptu
    stream.connected = True
    stream._tickers = dict(list(stream._tickers.items())[: MIN_SYMBOLS - 1])
    assert stream.snapshot() is None  # eksik kapsama


def test_disabled_stream_never_starts_or_serves():
    async def scenario():
        stream = BinanceTickerStream(connector=FakeConnector([]))
        stream.ensure_started()
        assert stream._task is None and stream.snapshot() is None
    asyncio.run(scenario())


def test_run_subscribes_to_all_market_ticker_and_collects_rows():
    async def scenario():
        socket = FakeSocket([batch(5)])
        connector = FakeConnector([socket])
        stream = BinanceTickerStream(connector=connector)
        stream.enable()
        stream.ensure_started()
        for _ in range(50):
            await asyncio.sleep(0.01)
            if len(stream._tickers) == 5:
                break
        assert connector.urls == ["wss://fstream.binance.com/market/ws"]
        assert socket.sent[0]["method"] == "SUBSCRIBE" and socket.sent[0]["params"] == ["!ticker@arr"]
        assert len(stream._tickers) == 5 and stream.connected
        await stream.close()
        assert not stream.connected
    asyncio.run(scenario())


def test_public_market_data_serves_bulk_ticker_from_stream_without_rest(monkeypatch):
    async def scenario():
        clock = Clock()
        stream = healthy_stream(clock)
        clock.now += WARMUP_SECONDS + 1
        stream.last_message_at = clock.now
        monkeypatch.setattr("app.public_market_data.TICKER_STREAM", stream)
        fetch = _Fetch()
        response = await PublicMarketData().get("/fapi/v1/ticker/24hr", {}, fetch, source="public_api")
        assert response.status_code == 200 and len(response.json()) == MIN_SYMBOLS
        assert fetch.calls == 0
        # Tek sembol sorgusu akıştan değil REST'ten gider.
        await PublicMarketData().get("/fapi/v1/ticker/24hr", {"symbol": "BTCUSDT"}, fetch, source="public_api")
        assert fetch.calls == 1
    asyncio.run(scenario())


def test_public_market_data_falls_back_to_rest_when_stream_unhealthy(monkeypatch):
    async def scenario():
        stream = BinanceTickerStream()
        monkeypatch.setattr("app.public_market_data.TICKER_STREAM", stream)
        fetch = _Fetch()
        response = await PublicMarketData().get("/fapi/v1/ticker/24hr", {}, fetch, source="public_api")
        assert response.status_code == 200 and fetch.calls == 1
    asyncio.run(scenario())


class _Fetch:
    def __init__(self):
        self.calls = 0

    async def __call__(self, path, values):
        self.calls += 1
        return httpx.Response(200, json=[{"symbol": "BTCUSDT", "lastPrice": "1", "priceChangePercent": "0", "quoteVolume": "1"}],
                              request=httpx.Request("GET", "https://fapi.binance.com" + path))


def test_global_singleton_is_disabled_by_default():
    assert TICKER_STREAM.enabled is False
