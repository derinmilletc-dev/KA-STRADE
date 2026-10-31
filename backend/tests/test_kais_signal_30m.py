"""Kais Long canlı sinyali: 30 dk kapanmış mumlar + güven puanı >= 86 (yalnız canlı giriş taraması)."""
import asyncio
import os
from unittest import mock

import pytest

from app import original_v2_demo_execution as demo_execution
from app.binance_kline_stream import ALLOWED_INTERVALS, INTERVAL_MILLISECONDS
from app.backtest_data import INTERVALS as SERIES_SECONDS, normalize_candles
from app.strategies import original_v2_demo as strategy
import test_original_v2_demo as decisions
from test_original_v2_live import CandleClient


def run(awaitable):
    return asyncio.run(awaitable)


def scan(client, interval=None, kline_fetch=None):
    env = {} if interval is None else {strategy.LIVE_SIGNAL_INTERVAL_FLAG: interval}
    with mock.patch.dict(os.environ, env):
        if interval is None:
            os.environ.pop(strategy.LIVE_SIGNAL_INTERVAL_FLAG, None)
        return run(demo_execution.live_candidates(
            client, decisions.fixtures.AT, set(), list(decisions.SYMBOLS),
            require_complete=True, kline_fetch=kline_fetch,
        ))


def test_defaults_are_30m_signal_and_confidence_86():
    assert strategy.LIVE_SIGNAL_INTERVAL_DEFAULT == "30m"
    assert strategy.KAIS2_MIN_CONFIDENCE == 86
    assert strategy.KAIS2_CONFIDENCE_REJECT_REASON == "KAIS2_CONFIDENCE_BELOW_86"
    with mock.patch.dict(os.environ, {}):
        os.environ.pop(strategy.LIVE_SIGNAL_INTERVAL_FLAG, None)
        assert strategy.live_signal_interval() == "30m"


@pytest.mark.parametrize("value,expected", [("30m", "30m"), ("15m", "15m"), (" 30M ", "30m")])
def test_signal_interval_flag_accepts_only_known_values(value, expected):
    with mock.patch.dict(os.environ, {strategy.LIVE_SIGNAL_INTERVAL_FLAG: value}):
        assert strategy.live_signal_interval() == expected


@pytest.mark.parametrize("value", ["1h", "5m", "", "otuz"])
def test_invalid_signal_interval_stops_the_scan(value):
    with mock.patch.dict(os.environ, {strategy.LIVE_SIGNAL_INTERVAL_FLAG: value}):
        with pytest.raises(strategy.DemoDecisionError, match="INVALID_KAIS_SIGNAL_INTERVAL"):
            strategy.live_signal_interval()
        with pytest.raises(strategy.DemoDecisionError, match="INVALID_KAIS_SIGNAL_INTERVAL"):
            scan(CandleClient(), interval=value)


def test_30m_is_a_supported_candle_interval_everywhere_it_is_needed():
    assert SERIES_SECONDS["30m"] == 1800
    assert "30m" in ALLOWED_INTERVALS and INTERVAL_MILLISECONDS["30m"] == 1_800_000
    assert strategy.LIVE_KLINE_INTERVALS == ("15m", "30m", "1h", "4h")
    # Demo/backtest/kâr koruma pencereleri değişmedi.
    assert strategy.INTERVALS == ("15m", "1h", "4h")


def test_30m_series_closes_only_finished_candles():
    at = decisions.fixtures.AT + 900  # 30 dk mumun ortası
    rows = [{"time": at - 1800 * 3 - 900 + 1800 * i, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1}
            for i in range(4)]
    series, _ = normalize_candles(rows, "30m")
    closed = series.closed(at)
    assert closed[-1]["time"] + 1800 <= at  # oluşmakta olan mum asla analize girmez
    assert series.history_complete(at)


def test_live_scan_feeds_30m_candles_to_the_strategy():
    seen = []
    original = strategy.evaluate_live_universe

    def spy(request, **kwargs):
        seen.append(request)
        return original(request, **kwargs)

    with mock.patch.object(strategy, "evaluate_live_universe", side_effect=spy):
        _, results = scan(CandleClient(), interval="30m")
    assert seen and results
    at = decisions.fixtures.AT
    for request in seen:
        primary = request.candles_by_timeframe["15m"]
        assert set(request.candles_by_timeframe) == {"15m", "1h", "4h"}
        assert len(primary) == 259
        assert all(right["time"] - left["time"] == 1800 for left, right in zip(primary, primary[1:]))
        assert primary[-1]["time"] == at - 1800
    for result in results.values():
        assert result.signal.signal_open_time == at - 1800


def test_30m_path_reads_the_same_prices_as_15m_path_only_from_30m_candles():
    """Sabit veride 30 dk mumlar 15 dk değerlerinin kopyası: karar, puan, stop ve hedefler birebir aynı çıkmalı."""
    selected_30, results_30 = scan(CandleClient(), interval="30m")
    selected_15, results_15 = scan(CandleClient(), interval="15m")
    assert set(results_30) == set(results_15)
    for symbol in results_15:
        a, b = results_30[symbol].signal, results_15[symbol].signal
        assert (a.decision, a.strategy_eligible, a.direction, a.entry, a.stop, a.tp1, a.tp2, a.tp3, a.atr, a.reasons) == \
               (b.decision, b.strategy_eligible, b.direction, b.entry, b.stop, b.tp1, b.tp2, b.tp3, b.atr, b.reasons)
        assert results_30[symbol].legacy["analysis"]["confidence"] == results_15[symbol].legacy["analysis"]["confidence"]
        assert results_30[symbol].legacy["analysis"]["momentum"] == results_15[symbol].legacy["analysis"]["momentum"]
    assert [c["symbol"] for c, _ in selected_30] == [c["symbol"] for c, _ in selected_15]


def test_flag_15m_restores_the_previous_fetch_set_exactly():
    calls = []
    fetcher = CandleClient()

    async def fetch(symbol, interval, limit):
        calls.append(interval)
        return await fetcher.public_get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})

    scan(CandleClient(), interval="15m", kline_fetch=fetch)
    assert set(calls) == set(strategy.INTERVALS)
    calls.clear()
    scan(CandleClient(), interval="30m", kline_fetch=fetch)
    assert set(calls) == set(strategy.LIVE_KLINE_INTERVALS)


def test_missing_30m_history_rejects_only_that_symbol_without_falling_back_to_15m():
    class Gap30(CandleClient):
        async def public_get(self, path, params=None):
            rows = await super().public_get(path, params)
            if path == "/fapi/v1/klines" and params["interval"] == "30m" and params["symbol"] == decisions.SYMBOLS[0]:
                rows = rows[:50] + rows[51:]
            return rows

    diagnostics = {}
    with mock.patch.dict(os.environ, {strategy.LIVE_SIGNAL_INTERVAL_FLAG: "30m"}):
        _, results = run(demo_execution.live_candidates(
            Gap30(), decisions.fixtures.AT, set(), list(decisions.SYMBOLS),
            require_complete=True, diagnostics=diagnostics,
        ))
    assert decisions.SYMBOLS[0] not in results
    assert set(results) == set(decisions.SYMBOLS[1:])


def test_demo_and_backtest_scans_never_use_30m():
    calls = []

    class Recording(CandleClient):
        async def public_get(self, path, params=None):
            if path == "/fapi/v1/klines":
                calls.append(params["interval"])
            return await super().public_get(path, params)

    with mock.patch.dict(os.environ, {strategy.LIVE_SIGNAL_INTERVAL_FLAG: "30m"}):
        run(demo_execution.candidates(Recording(), decisions.fixtures.AT, set(), require_complete=True))
    assert calls and "30m" not in calls


def test_kais2_gate_blocks_long_below_86_and_allows_86():
    from test_kais2_live_gate import check, make
    with mock.patch.dict(os.environ, {strategy.KAIS2_GATE_FLAG: "true"}):
        assert not strategy.apply_live_kais2_gate(make("LONG", 85.99)).signal.strategy_eligible
        assert not strategy.apply_live_kais2_gate(make("LONG", 84)).signal.strategy_eligible
        allowed = strategy.apply_live_kais2_gate(make("LONG", 86))
        assert allowed.signal.strategy_eligible
        assert check(allowed, "kais2_confidence").threshold == 86
