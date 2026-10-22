"""Paylaşımlı Render IP'si Binance engeli yerken canlı strateji küçük bütçeyle sabit IP'den (QuotaGuard) okur."""
import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.binance_rate_limit import BINANCE_GLOBAL_BAN
from app.public_market_data import FALLBACK_WEIGHT_PER_MINUTE, PROTECTED_LANE, PublicMarketData, protected_lane_available

KLINES = {"symbol": "BTCUSDT", "interval": "15m", "limit": 260}


def ban(route="render_direct", status=418):
    BINANCE_GLOBAL_BAN.record(httpx.Response(
        status, headers={"Retry-After": "3600"}, json={"code": -1003},
        request=httpx.Request("GET", "https://fapi.binance.com/fapi/v1/klines"),
    ), source="original_live", egress_route=route)


def ok(rows=3):
    return httpx.Response(200, json=[[i, "1", "2", "0", "1", "1", i + 1, "1"] for i in range(rows)],
                          request=httpx.Request("GET", "https://fapi.binance.com/fapi/v1/klines"))


@pytest.fixture
def lane(monkeypatch):
    monkeypatch.setenv("QUOTAGUARD_URL", "http://user:pass@proxy.example:9293")
    protected = AsyncMock(return_value=ok())
    PROTECTED_LANE.configure(protected)
    return protected


def test_live_strategy_reads_through_protected_lane_while_render_ip_is_banned(lane):
    async def run():
        ban()
        direct = AsyncMock()
        service = PublicMarketData()
        response = await service.get("/fapi/v1/klines", KLINES, direct, source="original_live")
        assert response.status_code == 200 and len(response.json()) == 3
        direct.assert_not_awaited()
        lane.assert_awaited_once()
        # Önbellek: aynı mum ikinci kez istenmez.
        await service.get("/fapi/v1/klines", KLINES, direct, source="original_live")
        lane.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize("source", ["public_api", "analysis", "original_warmer"])
def test_other_sources_never_use_the_protected_lane(lane, source):
    async def run():
        ban()
        response = await PublicMarketData().get("/fapi/v1/klines", KLINES, AsyncMock(), source=source)
        assert response.status_code == 418
        lane.assert_not_awaited()
    asyncio.run(run())


def test_heavy_bulk_ticker_stays_off_the_protected_lane(lane):
    async def run():
        ban()
        response = await PublicMarketData().get("/fapi/v1/ticker/24hr", {}, AsyncMock(), source="original_live")
        assert response.status_code == 418
        lane.assert_not_awaited()
    asyncio.run(run())


def test_ban_on_the_account_ip_closes_the_lane(lane):
    async def run():
        ban("quotaguard")
        assert not protected_lane_available()
        # Engel hesap IP'sindeyse Render çıkışı serbesttir: okuma doğrudan gider, korumalı yol kullanılmaz.
        direct = AsyncMock(return_value=ok())
        response = await PublicMarketData().get("/fapi/v1/klines", KLINES, direct, source="original_live")
        assert response.status_code == 200
        direct.assert_awaited_once()
        lane.assert_not_awaited()
    asyncio.run(run())


def test_lane_rate_limit_pauses_lane_without_recording_an_account_ban(lane):
    async def run():
        ban()
        lane.return_value = httpx.Response(429, headers={"Retry-After": "5"}, json={"code": -1003},
                                           request=httpx.Request("GET", "https://fapi.binance.com/fapi/v1/klines"))
        response = await PublicMarketData().get("/fapi/v1/klines", KLINES, AsyncMock(), source="original_live")
        assert response.status_code == 429
        assert not protected_lane_available()
        # Hesap/emir yolu engellenmez.
        assert not BINANCE_GLOBAL_BAN.applies_to("quotaguard")
    asyncio.run(run())


def test_lane_weight_budget_is_bounded(lane):
    async def run():
        ban()
        service = PublicMarketData()
        statuses = []
        for index in range(FALLBACK_WEIGHT_PER_MINUTE):
            response = await service.get("/fapi/v1/klines", {**KLINES, "symbol": f"C{index}USDT"}, AsyncMock(), source="original_live")
            statuses.append(response.status_code)
        # klines limit 260 = ağırlık 2 → dakikada en fazla 60 istek korumalı yoldan gider.
        assert lane.await_count == FALLBACK_WEIGHT_PER_MINUTE // 2
        assert statuses.count(418) == FALLBACK_WEIGHT_PER_MINUTE - FALLBACK_WEIGHT_PER_MINUTE // 2
    asyncio.run(run())


def test_without_quotaguard_the_lane_is_closed():
    async def run():
        ban()
        assert not protected_lane_available()
        response = await PublicMarketData().get("/fapi/v1/klines", KLINES, AsyncMock(), source="original_live")
        assert response.status_code == 418
    asyncio.run(run())


def test_main_configures_lane_only_with_quotaguard(monkeypatch):
    from types import SimpleNamespace
    from app import main

    async def run():
        application = SimpleNamespace(state=SimpleNamespace(public_market_data=PublicMarketData()))
        monkeypatch.delenv("QUOTAGUARD_URL", raising=False)
        application.state.public_market_data.get = AsyncMock(return_value=ok())
        await main.market_data_request(application, "/fapi/v1/klines", KLINES, source="original_live")
        assert PROTECTED_LANE.fetch is None
        monkeypatch.setenv("QUOTAGUARD_URL", "http://user:pass@proxy.example:9293")
        await main.market_data_request(application, "/fapi/v1/klines", KLINES, source="original_live")
        assert PROTECTED_LANE.fetch is not None
    asyncio.run(run())
