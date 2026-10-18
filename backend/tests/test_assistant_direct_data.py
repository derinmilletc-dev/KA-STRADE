"""Hızlı yol (model çağırmayan) niyetleri, salt-okunur piyasa/Demo araçları ve "bayat veri" etiketi. Ağ/veritabanı yok."""
import unittest
from datetime import datetime, timedelta, timezone
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import assistant_fastpath as fastpath
from app import readonly_demo_service as service
from app.assistant_llm import has_old_data
from app.assistant_tools import AssistantTools

NOW = datetime(2026, 10, 18, 20, 0, tzinfo=timezone.utc)


class FakeTools:
    def __init__(self, data, stale=False, error=None):
        self.data, self.stale, self.error = data, stale, error

    async def dispatch(self, user_id, name, arguments):
        if self.error:
            raise self.error
        return {"data": self.data, "stale": self.stale, "fetched_at": "2026-10-18T20:00:00Z"}


def tool_self(*, premium=True, role="OWNER"):
    fake = SimpleNamespace(premium=lambda user_id: premium, user=lambda user_id: {"id": user_id, "role": role},
                           clock=lambda: NOW.timestamp(), config=SimpleNamespace(request_timeout_seconds=1), request=None)
    fake.result = partial(AssistantTools.result, fake)
    return fake


def scan(age_minutes, eligible, candidate_count=None):
    return {"last_scan_at": (NOW - timedelta(minutes=age_minutes)).isoformat(), "timeframe": "15m", "coins_scanned": 50,
            "candidate_count": len(eligible) if candidate_count is None else candidate_count, "eligible": eligible}


def row(symbol, direction, opportunity, confidence=70):
    return {"symbol": symbol, "direction": direction, "opportunity_score": opportunity, "analysis_score": 80,
            "confidence_value": confidence, "confidence_label": "Yüksek"}


class DirectIntentTests(unittest.TestCase):
    def test_read_only_position_questions_use_the_positions_path(self):
        for message in ("İşlemlerim ne durumda?", "işlemlerim ne durumda", "Şu an işlemlerim ne durum", "Açık işlemim var mı?", "açık işlemim var mı",
                        "Pozisyonlarımı göster.", "kar zarar durumum ne", "How many open positions do I have?", "Show my positions.", "what are my positions"):
            self.assertEqual(fastpath.intent(message), "get_demo_positions", message)

    def test_action_requests_are_never_captured_by_read_only_paths(self):
        for message in ("pozisyonlarımı kapat", "işlemlerimi kapat", "tüm işlemlerimi sat", "açık pozisyonlarımı kapatır mısın",
                        "close my positions", "sell all my trades", "cancel my open positions", "BTC long emir hazırla", "pozisyon aç"):
            self.assertIsNone(fastpath.intent(message), message)

    def test_unlisted_action_verbs_and_ambiguous_requests_fall_through_to_the_model(self):
        for message in ("Pozisyonumu bitir.", "Açık işlemlerimi durdur.", "Stop my positions.", "İşlemlerimden çık.", "Pozisyonlarımı sıfırla.",
                        "End my trades.", "pozisyonlarım", "my positions", "açık işlemlerim", "kar zarar"):
            self.assertIsNone(fastpath.intent(message), message)

    def test_educational_questions_are_not_answered_with_lists_or_rankings(self):
        for message in ("açık pozisyon nasıl kapatılır", "pozisyon açma nasıl yapılır", "best long strategy explained",
                        "en iyi long stratejisi nedir", "how do I find the best long", "karar nasıl veriliyor", "pozisyonlarım nasıl"):
            self.assertIsNone(fastpath.intent(message), message)

    def test_yes_no_or_symbol_questions_keep_their_question_semantics(self):
        for message in ("BTC en iyi long mu", "ETH en çok yükselen mi", "is SOL the best long", "en iyi long BTC mi"):
            self.assertIsNone(fastpath.intent(message), message)

    def test_explicit_ranking_questions_use_the_market_path(self):
        for message in ("en çok yükselen coin hangisi", "en iyi long hangisi", "güven skoru en yüksek olan hangisi", "en iyi short ne", "what is the best long"):
            self.assertEqual(fastpath.intent(message), "get_market_overview", message)


class MarketOverviewToolTests(unittest.IsolatedAsyncioTestCase):
    async def overview(self, scanner=None, movers=None, *, premium=True, scanner_error=None, movers_error=None):
        read_scan = patch.object(service, "read_scanner_candidates", side_effect=scanner_error or (lambda request: scanner))
        read_movers = patch.object(service, "read_market_movers", AsyncMock(side_effect=movers_error, return_value=movers or []))
        with read_scan, read_movers:
            return await AssistantTools.get_market_overview(tool_self(premium=premium), "u")

    async def test_fresh_scan_ranks_only_eligible_candidates_and_carries_the_timestamp(self):
        result = await self.overview(scan(10, [row("SOLUSDT", "LONG", 81), row("ADAUSDT", "LONG", 90, 60), row("ETHUSDT", "SHORT", 75, 90)]))
        data = result["data"]["scan"]
        self.assertEqual(data["status"], "fresh")
        self.assertEqual(data["best_long"]["symbol"], "ADAUSDT")
        self.assertEqual(data["best_short"]["symbol"], "ETHUSDT")
        self.assertEqual(data["highest_confidence"]["symbol"], "ETHUSDT")
        self.assertEqual(data["last_scan_at"], (NOW - timedelta(minutes=10)).isoformat())
        self.assertFalse(result["stale"])

    async def test_stale_scan_never_returns_a_ranking(self):
        result = await self.overview(scan(service.MAX_SIGNAL_AGE_SECONDS // 60 + 1, [row("SOLUSDT", "LONG", 81)]))
        data = result["data"]["scan"]
        self.assertEqual(data["status"], "stale")
        self.assertTrue(result["stale"])
        self.assertFalse({"best_long", "best_short", "highest_confidence", "highest_opportunity"} & set(data))

    async def test_no_scan_invalid_timestamp_and_unavailable_scanner_are_distinguished(self):
        self.assertEqual((await self.overview({"last_scan_at": None, "candidate_count": 0, "eligible": []}))["data"]["scan"]["status"], "no_scan")
        self.assertEqual((await self.overview(scan(5, [], candidate_count=0)))["data"]["scan"]["status"], "no_scan")
        future = {**scan(0, [row("SOLUSDT", "LONG", 81)]), "last_scan_at": (NOW + timedelta(hours=1)).isoformat()}
        self.assertEqual((await self.overview(future))["data"]["scan"]["status"], "unavailable")
        slightly_future = {**future, "last_scan_at": (NOW + timedelta(seconds=1)).isoformat()}  # tolerans yok
        self.assertEqual((await self.overview(slightly_future))["data"]["scan"]["status"], "unavailable")
        self.assertEqual((await self.overview(scan(0, [row("SOLUSDT", "LONG", 81)])))["data"]["scan"]["status"], "fresh")
        self.assertEqual((await self.overview({**future, "last_scan_at": "not-a-date"}))["data"]["scan"]["status"], "unavailable")
        self.assertEqual((await self.overview(scanner_error=PermissionError()))["data"]["scan"]["status"], "unavailable")

    async def test_no_eligible_long_or_short_does_not_force_a_winner(self):
        data = (await self.overview(scan(5, [], candidate_count=12)))["data"]["scan"]
        self.assertEqual(data["status"], "fresh")
        self.assertIsNone(data["best_long"])
        self.assertIsNone(data["best_short"])
        self.assertIsNone(data["highest_confidence"])

    async def test_gainers_are_strictly_positive_and_losers_strictly_negative(self):
        movers = [{"symbol": "AUSDT", "change": -1.5, "price": 1}, {"symbol": "BUSDT", "change": -4.0, "price": 1}, {"symbol": "CUSDT", "change": 0.0, "price": 1}]
        data = (await self.overview(scan(5, []), movers))["data"]["movers"]
        self.assertEqual(data["top_gainers"], [])
        self.assertEqual([r["symbol"] for r in data["top_losers"]], ["BUSDT", "AUSDT"])
        data = (await self.overview(scan(5, []), [{"symbol": "XUSDT", "change": 3.2, "price": 2}, *movers]))["data"]["movers"]
        self.assertEqual([r["symbol"] for r in data["top_gainers"]], ["XUSDT"])

    async def test_missing_premium_and_market_failures_are_reported(self):
        result = await self.overview(premium=False)
        self.assertEqual(result["data"], {"reason": "PREMIUM_REQUIRED"})
        result = await self.overview(scan(5, []), movers_error=TimeoutError())
        self.assertEqual(result["data"]["movers"]["status"], "unavailable")
        self.assertTrue(result["stale"])


class DemoPositionsToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_owner_and_unavailable_demo_are_reported_without_data(self):
        result = await AssistantTools.get_demo_positions(tool_self(role="MEMBER"), "u")
        self.assertEqual(result["data"]["reason"], "DEMO_OWNER_ONLY")
        with patch.object(service, "read_demo_positions", AsyncMock(side_effect=RuntimeError("key"))):
            result = await AssistantTools.get_demo_positions(tool_self(), "u")
        self.assertEqual(result["data"]["reason"], "DEMO_UNAVAILABLE")
        with patch.object(service, "read_demo_positions", AsyncMock(return_value=[])):
            result = await AssistantTools.get_demo_positions(tool_self(), "u")
        self.assertEqual(result["data"]["positions"], [])
        self.assertFalse(result["stale"])


class ReadonlyServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_scanner_requires_a_member_session_and_uses_the_project_eligibility_rule(self):
        with self.assertRaises(PermissionError):
            service.read_scanner_candidates(SimpleNamespace(state=SimpleNamespace(member=None)))
        good = {"symbol": "SOLUSDT", "direction": "LONG", "status": "SELECTED", "analysis_score": 80, "opportunity_score": 80,
                "signal_age_seconds": 60, "risk_reward": 2, "entry": 1, "stop_loss": 0.9, "tp1": 1.1, "tp2": 1.2, "tp3": 1.3}
        weak = {**good, "symbol": "ADAUSDT", "analysis_score": 60}
        rejected = {**good, "symbol": "XRPUSDT", "status": "REJECTED"}
        state = {"settings": {"allow_long": False}, "scanner": {"last_scan_at": NOW.isoformat(), "all_candidates": [good, weak, rejected]}}
        request = SimpleNamespace(state=SimpleNamespace(member={"id": "u"}))
        with patch.object(service.v21_demo, "state_for", return_value=state), \
                patch.object(service.v21_demo, "candidate_is_tradeable", wraps=service.v21_demo.candidate_is_tradeable) as gate:
            result = service.read_scanner_candidates(request)
        self.assertEqual(gate.call_count, 3)
        self.assertEqual(result["candidate_count"], 3)
        # allow_long=False bir yön anahtarıdır, kalite kuralı değildir; zayıf ve reddedilen adaylar elenir.
        self.assertEqual([row["symbol"] for row in result["eligible"]], ["SOLUSDT"])
        self.assertFalse({"entry", "stop_loss", "tp1", "tp2", "tp3"} & set(result["eligible"][0]))

    async def test_movers_drop_non_finite_and_low_volume_rows(self):
        rows = [{"symbol": "AUSDT", "change": 2.0, "price": 1.0, "volume": 9e6}, {"symbol": "BUSDT", "change": float("nan"), "price": 1.0, "volume": 9e6},
                {"symbol": "CUSDT", "change": 5.0, "price": 1.0, "volume": 10.0}, {"symbol": "DUSDT", "change": None, "price": 1.0, "volume": 9e6}]
        with patch.object(service.v21_demo, "auto_trade_market_universe", AsyncMock(return_value=rows)):
            result = await service.read_market_movers(SimpleNamespace(app=None), 1)
        self.assertEqual([r["symbol"] for r in result], ["AUSDT"])


class DirectAnswerTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_demo_positions_say_no_open_trade_and_no_pnl(self):
        result = await fastpath.demo_positions_answer(FakeTools({"positions": [], "account": "demo"}), "u", "tr")
        self.assertIn("açık Demo işleminiz yok", result["reply"])

    async def test_positions_report_profit_or_loss_without_levels(self):
        data = {"positions": [{"symbol": "BTCUSDT", "direction": "LONG", "unrealized_pnl": 12.345}, {"symbol": "ETHUSDT", "direction": "SHORT", "unrealized_pnl": -3}]}
        reply = (await fastpath.demo_positions_answer(FakeTools(data), "u", "tr"))["reply"]
        self.assertIn("(kâr)", reply)
        self.assertIn("(zarar)", reply)
        self.assertNotIn("entry", reply.lower())

    async def test_unverifiable_positions_are_never_reported_as_empty(self):
        for tools in (FakeTools({"positions": [], "reason": "DEMO_UNAVAILABLE"}, True),):
            result = await fastpath.demo_positions_answer(tools, "u", "tr")
            self.assertIn("doğrulayamadım", result["reply"])
            self.assertNotIn("işleminiz yok", result["reply"])
            self.assertTrue(result["stale"])
        # OWNER olmayan üye: hızlı yol cevap vermez, soru normal akışa (kendi pozisyon aracı) gider.
        self.assertIsNone(await fastpath.demo_positions_answer(FakeTools({"positions": [], "reason": "DEMO_OWNER_ONLY"}, True), "u", "tr"))
        self.assertIsNone(await fastpath.demo_positions_answer(FakeTools({}, error=RuntimeError()), "u", "tr"))

    async def test_market_fresh_reply_has_scan_time_and_no_levels(self):
        data = {"scan": {"status": "fresh", "last_scan_at": "2026-10-18T20:00:00", "best_long": {"symbol": "SOLUSDT", "opportunity_score": 81, "confidence_label": "Yüksek"},
                         "best_short": None, "highest_confidence": None},
                "movers": {"status": "fresh", "top_gainers": [{"symbol": "XYZUSDT", "change_24h_percent": 18.2, "price": 1}]}}
        reply = (await fastpath.market_overview_answer(FakeTools(data), "u", "tr", "en iyi long hangisi"))["reply"]
        self.assertIn("SOLUSDT", reply)
        self.assertIn("2026-10-18T20:00:00", reply)
        self.assertIn("al/sat talimatı değildir", reply)
        self.assertNotIn("XYZUSDT", reply)  # yalnızca sorulan sıralama
        reply = (await fastpath.market_overview_answer(FakeTools(data), "u", "tr", "en iyi short hangisi"))["reply"]
        self.assertIn("short adayı yok", reply)
        reply = (await fastpath.market_overview_answer(FakeTools(data), "u", "tr", "en çok yükselen coin hangisi"))["reply"]
        self.assertIn("XYZUSDT %18.2", reply)

    async def test_market_stale_no_scan_premium_and_no_gainer_replies_never_rank(self):
        movers = {"status": "fresh", "top_gainers": [], "top_losers": [{"symbol": "AUSDT", "change_24h_percent": -2, "price": 1}]}
        stale = await fastpath.market_overview_answer(FakeTools({"scan": {"status": "stale", "age_seconds": 9000, "max_age_seconds": 7200}, "movers": movers}, True), "u", "tr", "en iyi long hangisi")
        self.assertIn("güncel sayılmıyor", stale["reply"])
        self.assertTrue(stale["stale"])
        none = await fastpath.market_overview_answer(FakeTools({"scan": {"status": "no_scan", "note": "NO_SCAN_RESULTS_YET"}, "movers": movers}, True), "u", "tr", "en iyi long hangisi")
        self.assertIn("Henüz tarama sonucu yok", none["reply"])
        unavailable = await fastpath.market_overview_answer(FakeTools({"scan": {"status": "unavailable"}, "movers": movers}, True), "u", "en", "best short")
        self.assertIn("could not access the scan data", unavailable["reply"])
        premium = await fastpath.market_overview_answer(FakeTools({"reason": "PREMIUM_REQUIRED"}, True), "u", "tr", "en iyi long hangisi")
        self.assertIn("premium", premium["reply"])
        gainer = await fastpath.market_overview_answer(FakeTools({"scan": {"status": "fresh"}, "movers": movers}), "u", "tr", "en çok yükselen coin hangisi")
        self.assertIn("yükselişte olan coin yok", gainer["reply"])
        self.assertNotIn("AUSDT", gainer["reply"])
        failed = await fastpath.market_overview_answer(FakeTools({"scan": {"status": "fresh"}, "movers": {"status": "unavailable"}}, True), "u", "tr", "en çok yükselen coin hangisi")
        self.assertIn("alamadım", failed["reply"])


class StaleLabelTests(unittest.TestCase):
    def test_unavailable_is_not_labelled_stale_but_real_old_data_is(self):
        self.assertFalse(has_old_data({}))
        self.assertFalse(has_old_data({"get_my_positions": {"stale": False, "data": {"positions": []}}}))
        self.assertFalse(has_old_data({"get_my_positions": {"stale": True, "data": {"positions": [], "reason": "ACCOUNT_NOT_VERIFIED"}}}))
        self.assertFalse(has_old_data({"get_market_overview": {"stale": True, "data": {"scan": {"note": "NO_SCAN_RESULTS_YET"}, "movers": {}}}}))
        self.assertFalse(has_old_data({"x": {"stale": True, "data": {"error": "Could not verify tool data."}}}))
        self.assertTrue(has_old_data({"get_market_overview": {"stale": True, "data": {"scan": {"status": "stale", "age_seconds": 9000}, "movers": {"status": "fresh"}}}}))
        self.assertTrue(has_old_data({"get_my_positions": {"stale": True, "data": {"positions": [{"symbol": "BTCUSDT"}], "data_age_seconds": 900}}}))


if __name__ == "__main__":
    unittest.main()
