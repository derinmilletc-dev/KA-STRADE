"""compare_coins: canlı 24s veri + taze tarama skorları; kredi harcatan analiz üretmez, skor uydurmaz."""
import tempfile
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app import v21_demo
from app.assistant_tools import ARGUMENT_MODELS, AssistantTools, CompareArguments
from tests.test_assistant_free_market import NOW, Clock, credits_service, tool_self

UNIVERSE = [
    {"symbol": "BTCUSDT", "price": 100000.0, "change": 1.234, "volume": 9e9},
    {"symbol": "ETHUSDT", "price": 4000.0, "change": -0.5, "volume": 5e9},
]


def request_with(scan_age_minutes, rows):
    scanner = {"last_scan_at": (NOW - timedelta(minutes=scan_age_minutes)).isoformat(), "all_candidates": rows}
    return SimpleNamespace(app=SimpleNamespace(), state=SimpleNamespace(member={"id": "u"})), scanner


class CompareTests(unittest.IsolatedAsyncioTestCase):
    async def run_compare(self, fake, scanner, universe=UNIVERSE, symbols=("BTC", "ETH")):
        with patch.object(v21_demo, "auto_trade_market_universe", AsyncMock(return_value=universe)), \
             patch.object(v21_demo, "state_for", lambda request: {"scanner": scanner}):
            return await AssistantTools.compare_coins(fake, "u", list(CompareArguments(symbols=list(symbols)).symbols))

    def test_symbols_are_normalized_and_validated(self):
        self.assertEqual(CompareArguments(symbols=["btc", "ETH/USDT", "btc"]).symbols, ["BTCUSDT", "ETHUSDT"])
        with self.assertRaises(ValidationError):
            CompareArguments(symbols=["BTC"])
        with self.assertRaises(ValidationError):
            CompareArguments(symbols=["BTC", "btc"])
        self.assertIn("compare_coins", ARGUMENT_MODELS)

    async def test_premium_gets_live_and_analyst_scores_side_by_side(self):
        request, scanner = request_with(5, [{"symbol": "BTCUSDT", "direction": "LONG", "opportunity_score": 80, "analysis_score": 78, "confidence_value": 70, "confidence": "Yüksek"}])
        fake = tool_self(premium=True); fake.request = request
        data = (await self.run_compare(fake, scanner))["data"]
        btc, eth = data["coins"]
        self.assertEqual((btc["price"], btc["change_24h_percent"]), (100000.0, 1.23))
        self.assertEqual(btc["analyst"]["opportunity_score"], 80)
        self.assertEqual(eth["analyst"], "not_in_latest_scan")
        self.assertEqual(data["scan"]["status"], "fresh")

    async def test_stale_scan_gives_no_scores(self):
        request, scanner = request_with(600, [{"symbol": "BTCUSDT", "direction": "LONG", "opportunity_score": 80}])
        fake = tool_self(premium=True); fake.request = request
        result = await self.run_compare(fake, scanner)
        self.assertEqual(result["data"]["scan"]["status"], "stale")
        self.assertTrue(all("analyst" not in coin for coin in result["data"]["coins"]))
        self.assertTrue(result["stale"])

    async def test_free_member_is_charged_like_the_market_overview(self):
        with tempfile.TemporaryDirectory() as directory:
            request, scanner = request_with(5, [])
            fake = tool_self(premium=False, credits=credits_service(directory, Clock(NOW.timestamp()))); fake.request = request
            first = await self.run_compare(fake, scanner)
            self.assertEqual(first["data"]["free_usage"]["remaining_credits"], 90)
            for _ in range(2):
                await self.run_compare(fake, scanner)
            limited = await self.run_compare(fake, scanner)
            self.assertEqual(limited["data"]["reason"], "FREE_DAILY_LIMIT")

    async def test_free_member_without_market_access_is_refused(self):
        fake = tool_self(premium=False, limit=0)
        result = await AssistantTools.compare_coins(fake, "u", ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(result["data"], {"reason": "PREMIUM_REQUIRED"})


if __name__ == "__main__":
    unittest.main()
