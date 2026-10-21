"""Premium olmayan üyeye asistanda günlük sınırlı, kredili piyasa özeti; admin/premium durumunun modele sunucudan verilmesi."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import assistant_fastpath as fastpath
from app import readonly_demo_service as service
from app.analyst_credits import AnalystCredits, CreditConfig, CreditStore
from app.assistant_config import AssistantConfig
from app.assistant_tools import AssistantTools

NOW = datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, value: float):
        self.value = value

    def __call__(self) -> float:
        return self.value


def credits_service(directory: str, clock: Clock, total: int = 100) -> AnalystCredits:
    application = SimpleNamespace(state=SimpleNamespace(db_pool=None))
    store = CreditStore(application, path=Path(directory) / "credits.sqlite3")
    return AnalystCredits(store, CreditConfig(total=total, cost=10), clock=clock)


class ChargeMarketOverviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_overviews_a_day_each_costing_credits_then_the_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            credits = credits_service(directory, Clock(NOW.timestamp()))
            results = [await credits.charge_market_overview("u", "2026-10-21", 3) for _ in range(4)]
            self.assertEqual([row["allowed"] for row in results], [True, True, True, False])
            self.assertEqual([row["remaining_credits"] for row in results[:3]], [90, 80, 70])
            self.assertEqual([row["used_today"] for row in results[:3]], [1, 2, 3])
            self.assertEqual(results[3]["reason"], "FREE_DAILY_LIMIT")
            self.assertEqual(results[3]["remaining_credits"], 70)  # sınır aşılınca kredi düşmez
            snapshot = await credits.credits("u", False)
            self.assertEqual(snapshot["remaining"], 70)
            # Ertesi gün sayaç yenilenir (kredi penceresi ayrı işler).
            self.assertTrue((await credits.charge_market_overview("u", "2026-10-22", 3))["allowed"])

    async def test_not_enough_credits_is_reported_without_charging(self):
        with tempfile.TemporaryDirectory() as directory:
            credits = credits_service(directory, Clock(NOW.timestamp()), total=15)
            first = await credits.charge_market_overview("u", "2026-10-21", 3)
            second = await credits.charge_market_overview("u", "2026-10-21", 3)
            self.assertTrue(first["allowed"])
            self.assertEqual((second["allowed"], second["reason"], second["remaining_credits"]), (False, "CREDITS_EXHAUSTED", 5))

    async def test_members_are_counted_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            credits = credits_service(directory, Clock(NOW.timestamp()))
            for _ in range(3):
                await credits.charge_market_overview("a", "2026-10-21", 3)
            self.assertTrue((await credits.charge_market_overview("b", "2026-10-21", 3))["allowed"])


def scan_payload():
    eligible = [{"symbol": "SOLUSDT", "direction": "LONG", "opportunity_score": 81, "analysis_score": 80, "confidence_value": 70, "confidence_label": "Yüksek"}]
    return {"last_scan_at": (NOW - timedelta(minutes=5)).isoformat(), "timeframe": "15m", "coins_scanned": 50, "candidate_count": 1, "eligible": eligible}


def tool_self(*, premium, credits=None, limit=3):
    fake = SimpleNamespace(premium=lambda user_id: premium, user=lambda user_id: {"id": user_id, "role": "CUSTOMER"},
                           clock=lambda: NOW.timestamp(), config=SimpleNamespace(request_timeout_seconds=1, free_market_daily_limit=limit),
                           request=None, credits=lambda: credits)
    fake.result = partial(AssistantTools.result, fake)
    return fake


class FreeMarketToolTests(unittest.IsolatedAsyncioTestCase):
    async def overview(self, fake, scanner=None, scanner_error=None, movers_error=None):
        read_scan = patch.object(service, "read_scanner_candidates", side_effect=scanner_error or (lambda request: scanner))
        read_movers = patch.object(service, "read_market_movers", AsyncMock(side_effect=movers_error, return_value=[]))
        with read_scan, read_movers:
            return await AssistantTools.get_market_overview(fake, "u")

    async def test_free_member_gets_the_overview_and_usage_until_the_daily_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = tool_self(premium=False, credits=credits_service(directory, Clock(NOW.timestamp())))
            first = await self.overview(fake, scan_payload())
            self.assertEqual(first["data"]["scan"]["best_long"]["symbol"], "SOLUSDT")
            self.assertEqual(first["data"]["free_usage"], {"cost": 10, "daily_limit": 3, "used_today": 1, "remaining_credits": 90})
            await self.overview(fake, scan_payload())
            await self.overview(fake, scan_payload())
            limited = await self.overview(fake, scan_payload())
            self.assertEqual(limited["data"]["reason"], "FREE_DAILY_LIMIT")
            self.assertNotIn("scan", limited["data"])

    async def test_nothing_to_show_charges_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            credits = credits_service(directory, Clock(NOW.timestamp()))
            result = await self.overview(tool_self(premium=False, credits=credits), scanner_error=PermissionError(), movers_error=TimeoutError())
            self.assertNotIn("free_usage", result["data"])
            self.assertEqual((await credits.credits("u", False))["remaining"], 100)

    async def test_zero_limit_keeps_the_overview_premium_only(self):
        result = await self.overview(tool_self(premium=False, limit=0), scan_payload())
        self.assertEqual(result["data"], {"reason": "PREMIUM_REQUIRED"})

    async def test_premium_member_is_never_charged(self):
        result = await self.overview(tool_self(premium=True, credits=None), scan_payload())
        self.assertNotIn("free_usage", result["data"])
        self.assertEqual(result["data"]["scan"]["status"], "fresh")


class FakeTools:
    def __init__(self, data):
        self.data = data

    async def dispatch(self, user_id, name, arguments):
        return {"data": self.data, "stale": False, "fetched_at": NOW.isoformat()}


class FreeMarketReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_replies_state_cost_and_limits(self):
        movers = {"status": "fresh", "top_gainers": [{"symbol": "XUSDT", "change_24h_percent": 3.2, "price": 2}], "top_losers": []}
        usage = {"cost": 10, "daily_limit": 3, "used_today": 1, "remaining_credits": 90}
        ok = await fastpath.market_overview_answer(FakeTools({"scan": {"status": "fresh"}, "movers": movers, "free_usage": usage}), "u", "tr", "en çok yükselen coin hangisi")
        self.assertIn("10 kredi düştü", ok["reply"])
        self.assertIn("bugün 2 piyasa özeti hakkın kaldı", ok["reply"])
        limit = await fastpath.market_overview_answer(FakeTools({"reason": "FREE_DAILY_LIMIT", "free_usage": {**usage, "used_today": 3}}), "u", "tr", "en iyi long hangisi")
        self.assertIn("3 piyasa özeti hakkını kullandın", limit["reply"])
        empty = await fastpath.market_overview_answer(FakeTools({"reason": "CREDITS_EXHAUSTED", "free_usage": {**usage, "remaining_credits": 5}}), "u", "en", "best long")
        self.assertIn("needs 10 credits", empty["reply"])


class IntentTests(unittest.TestCase):
    def test_most_sensible_coin_questions_use_the_fast_market_path(self):
        for message in ("en mantıklı coin hangisi", "En mantıklı coin hangisi?", "en iyi coin hangisi", "hangi coin daha mantıklı", "which coin is best"):
            self.assertEqual(fastpath.intent(message), "get_market_overview", message)
        for message in ("en mantıklı strateji nasıl kurulur", "BTC en mantıklı mı", "en mantıklı coini al"):
            self.assertIsNone(fastpath.intent(message), message)


class AccountContextTests(unittest.TestCase):
    def context(self, role, rights, language="tr"):
        fake = SimpleNamespace(user=lambda user_id: {"id": user_id, "role": role}, request=None,
                               config=AssistantConfig(), credits=lambda: SimpleNamespace(config=CreditConfig()))
        with patch("app.assistant_tools.access_for_request", return_value=rights):
            return AssistantTools.account_context(fake, "u", language)

    def test_admin_and_premium_are_told_they_have_access(self):
        admin = self.context("OWNER", {"isPremium": True, "entitlements": {"unlimitedAnalyst": True}})
        self.assertIn("yöneticisi", admin)
        self.assertIn("Premium erişimi VAR", admin)
        premium = self.context("CUSTOMER", {"isPremium": True, "entitlements": {"unlimitedAnalyst": True}}, "en")
        self.assertIn("HAS premium access with unlimited analyses", premium)

    def test_free_member_is_told_the_credit_rules(self):
        free = self.context("CUSTOMER", {"isPremium": False, "entitlements": {}})
        self.assertIn("premium DEĞİL", free)
        self.assertIn("günde en fazla 3 kez, her seferinde 10 kredi", free)
        self.assertIn("hata uydurma", free)
