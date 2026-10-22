"""Canlı saat eşitleme: taze ortak ölçüm yeniden kullanılır, hız sınırında Binance'e saat isteği gönderilmez,
saat eşitleme sırasında başlayan Binance engelinde imzalı okuma beklemeden hemen döner."""
import asyncio
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest

from app import v25_execution as live
from app.binance_rate_limit import BINANCE_GLOBAL_BAN


def recording_http(calls):
    async def request(method, url, **kwargs):
        calls.append(urlsplit(url).path)
        return httpx.Response(200, json={"serverTime": int(time.time() * 1000)}, request=httpx.Request(method, url))
    return SimpleNamespace(request=request)


def test_fresh_shared_offset_is_reused_without_a_time_request():
    calls = []
    live._SHARED_CLOCK.update(offset_ms=42, synced_at=time.monotonic() - 5)
    client = live.BinanceLiveClient(recording_http(calls), "offline-key", "offline-secret")
    asyncio.run(client.sync_clock())
    assert calls == []
    assert client.time_offset_ms == 42


def test_public_cooldown_reuses_older_offset_instead_of_calling_binance():
    calls = []
    live._SHARED_CLOCK.update(offset_ms=-17, synced_at=time.monotonic() - 120)
    service = SimpleNamespace(cooling_down=lambda: True)
    client = live.BinanceLiveClient(recording_http(calls), "offline-key", "offline-secret",
                                    application=SimpleNamespace(state=SimpleNamespace(public_market_data=service)))
    asyncio.run(client.sync_clock())
    assert calls == []
    assert client.time_offset_ms == -17


def test_ban_started_during_clock_sync_fails_fast_instead_of_waiting():
    calls = []

    async def ban_then_sync(self):
        BINANCE_GLOBAL_BAN.record(httpx.Response(418, headers={"Retry-After": "120"}, json={"code": -1003},
                                                 request=httpx.Request("GET", "https://fapi.binance.com/fapi/v1/time")),
                                  source="original_live", egress_route="render_direct")

    client = live.BinanceLiveClient(recording_http(calls), "offline-key", "offline-secret")
    client.sync_clock = ban_then_sync.__get__(client)
    started = time.monotonic()
    with pytest.raises(live.LiveRateLimitError):
        asyncio.run(client.signed("GET", "/fapi/v3/account"))
    assert time.monotonic() - started < 5
    assert calls == []
