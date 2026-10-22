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


def test_fresh_shared_offset_is_reused_without_a_time_request(monkeypatch):
    monkeypatch.setattr(live.time, "monotonic", lambda: 10_000.0)
    calls = []
    live._SHARED_CLOCK.update(offset_ms=42, synced_at=9_995.0)
    client = live.BinanceLiveClient(recording_http(calls), "offline-key", "offline-secret")
    asyncio.run(client.sync_clock())
    assert calls == []
    assert client.time_offset_ms == 42


def test_public_cooldown_reuses_older_offset_instead_of_calling_binance(monkeypatch):
    monkeypatch.setattr(live.time, "monotonic", lambda: 10_000.0)
    calls = []
    live._SHARED_CLOCK.update(offset_ms=-17, synced_at=9_880.0)
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


def record_ban(route):
    BINANCE_GLOBAL_BAN.record(httpx.Response(418, headers={"Retry-After": "600"}, json={"code": -1003},
                                             request=httpx.Request("GET", "https://fapi.binance.com/fapi/v1/klines")),
                              source="original_live", egress_route=route)


def test_render_ip_ban_does_not_block_account_requests_through_quotaguard(monkeypatch):
    monkeypatch.setenv("QUOTAGUARD_URL", "http://user:pass@proxy.invalid:9293")
    record_ban("render_direct")
    assert BINANCE_GLOBAL_BAN.public_status()["active"]  # ekrandaki genel durum yine görünür
    assert not BINANCE_GLOBAL_BAN.public_status("quotaguard")["active"]
    client = live.BinanceLiveClient(recording_http([]), "offline-key", "offline-secret")
    client._require_global_ban_clear("GET", "/fapi/v3/account")  # hesap yolu engelli değil: hata yok


def test_render_ip_ban_still_blocks_accounts_without_a_proxy(monkeypatch):
    monkeypatch.delenv("QUOTAGUARD_URL", raising=False)
    record_ban("render_direct")
    client = live.BinanceLiveClient(recording_http([]), "offline-key", "offline-secret")
    with pytest.raises(live.LiveRateLimitError):
        client._require_global_ban_clear("GET", "/fapi/v3/account")


def test_proxy_ip_ban_blocks_accounts_but_not_public_market_data(monkeypatch):
    from app.public_market_data import PublicMarketData
    monkeypatch.setenv("QUOTAGUARD_URL", "http://user:pass@proxy.invalid:9293")
    record_ban("quotaguard")
    assert PublicMarketData().blocked("/fapi/v1/klines") is None
    client = live.BinanceLiveClient(recording_http([]), "offline-key", "offline-secret")
    with pytest.raises(live.LiveRateLimitError):
        client._require_global_ban_clear("GET", "/fapi/v3/account")
    record_ban("render_direct")  # iki yol birden engelliyse her şey durur (güvenli taraf)
    assert PublicMarketData().blocked("/fapi/v1/klines") is not None
