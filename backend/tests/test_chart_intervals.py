import asyncio
import sys
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
backend_root = Path(__file__).resolve().parents[1]
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

from fastapi import HTTPException

from backend.app import main as main_module


def _rows(count, step_ms):
    return [[1_600_000_000_000 + i * step_ms, "100", "101", "99", "100.5", "1000"] for i in range(count)]


def _response(rows):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=rows)
    return response


class ChartIntervalTests(unittest.TestCase):
    def setUp(self):
        main_module.CANDLE_CACHE.clear()

    def test_chart_endpoint_accepts_week_and_month(self):
        for interval, step in (("1w", 604_800_000), ("1M", 2_592_000_000), ("12h", 43_200_000)):
            with patch.object(main_module, "market_data_request", new=AsyncMock(return_value=_response(_rows(60, step)))):
                candles = asyncio.run(main_module.klines("BTCUSDT", interval, 60))
            self.assertEqual(len(candles), 60, interval)

    def test_trading_paths_still_reject_chart_only_intervals(self):
        for interval in ("1w", "1M", "12h"):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(main_module.fetch_candles("BTCUSDT", interval, 60))
            self.assertEqual(caught.exception.status_code, 400)
            self.assertNotIn(interval, main_module.ALLOWED_INTERVALS)

    def test_unknown_interval_rejected_on_chart_endpoint(self):
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(main_module.klines("BTCUSDT", "2w", 60))
        self.assertEqual(caught.exception.status_code, 400)

    def test_weekly_analysis_works_with_short_history(self):
        with patch.object(main_module, "market_data_request", new=AsyncMock(return_value=_response(_rows(80, 604_800_000)))):
            result = asyncio.run(main_module.technical_analysis("NEWUSDT", "1w"))
        self.assertIn("direction", result)

    def test_trading_interval_analysis_keeps_220_minimum(self):
        with patch.object(main_module, "market_data_request", new=AsyncMock(return_value=_response(_rows(80, 900_000)))):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(main_module.technical_analysis("NEWUSDT", "15m"))
        self.assertEqual(caught.exception.status_code, 422)

    def test_long_interval_cache_expires_within_15_minutes(self):
        calls = AsyncMock(return_value=_response(_rows(60, 604_800_000)))
        with patch.object(main_module, "market_data_request", new=calls), patch.object(main_module.time, "time", return_value=1_700_000_000.0):
            asyncio.run(main_module.klines("BTCUSDT", "1w", 60))
            asyncio.run(main_module.klines("BTCUSDT", "1w", 60))
            self.assertEqual(calls.await_count, 1)
        with patch.object(main_module, "market_data_request", new=calls), patch.object(main_module.time, "time", return_value=1_700_000_000.0 + 1000):
            asyncio.run(main_module.klines("BTCUSDT", "1w", 60))
        self.assertEqual(calls.await_count, 2)


if __name__ == "__main__":
    unittest.main()
