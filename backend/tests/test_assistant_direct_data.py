"""Hızlı yol (model çağırmayan) niyetleri ve "bayat veri" etiketi kuralı. Saf fonksiyon testleri; ağ/veritabanı yok."""
import unittest

from app import assistant_fastpath as fastpath
from app.assistant_llm import has_old_data


class FakeTools:
    def __init__(self, data, stale=False, error=None):
        self.data, self.stale, self.error = data, stale, error

    async def dispatch(self, user_id, name, arguments):
        if self.error:
            raise self.error
        return {"data": self.data, "stale": self.stale, "fetched_at": "2026-10-18T20:00:00Z"}


class DirectIntentTests(unittest.TestCase):
    def test_positions_and_market_questions_are_detected(self):
        for message in ("Şu an işlemlerim ne durum", "açık işlemim var mı", "pozisyonlarım nasıl", "kar zarar durumum ne", "what are my positions"):
            self.assertEqual(fastpath.intent(message), "get_demo_positions", message)
        for message in ("en çok yükselen coin hangisi", "en iyi long hangisi", "güven skoru en yüksek olan hangisi", "en iyi short ne", "what is the best long"):
            self.assertEqual(fastpath.intent(message), "get_market_overview", message)

    def test_generic_or_order_questions_are_not_captured(self):
        for message in ("pozisyon açma nasıl yapılır", "BTC long emir hazırla", "karar nasıl veriliyor"):
            self.assertIsNone(fastpath.intent(message), message)


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

    async def test_unverifiable_data_falls_through_to_the_model_flow(self):
        self.assertIsNone(await fastpath.demo_positions_answer(FakeTools({"positions": [], "reason": "DEMO_UNAVAILABLE"}, True), "u", "tr"))
        self.assertIsNone(await fastpath.demo_positions_answer(FakeTools({}, error=RuntimeError()), "u", "tr"))
        self.assertIsNone(await fastpath.market_overview_answer(FakeTools({"reason": "PREMIUM_REQUIRED"}, True), "u", "tr"))
        self.assertIsNone(await fastpath.market_overview_answer(FakeTools({"scan": {"note": "NO_SCAN_RESULTS_YET"}, "movers": {}}, True), "u", "tr"))

    async def test_market_overview_reply_has_scan_time_and_no_levels(self):
        data = {"scan": {"last_scan_at": "2026-10-18T20:00:00", "best_long": {"symbol": "SOLUSDT", "opportunity_score": 81, "confidence_label": "Yüksek"}, "best_short": None, "highest_confidence": None},
                "movers": {"top_gainers": [{"symbol": "XYZUSDT", "change_24h_percent": 18.2, "price": 1}]}}
        reply = (await fastpath.market_overview_answer(FakeTools(data), "u", "tr"))["reply"]
        self.assertIn("XYZUSDT %18.2", reply)
        self.assertIn("SOLUSDT", reply)
        self.assertIn("2026-10-18T20:00:00", reply)
        self.assertIn("al/sat talimatı değildir", reply)


class StaleLabelTests(unittest.TestCase):
    def test_unavailable_is_not_labelled_stale_but_real_old_data_is(self):
        self.assertFalse(has_old_data({}))
        self.assertFalse(has_old_data({"get_my_positions": {"stale": False, "data": {"positions": []}}}))
        self.assertFalse(has_old_data({"get_my_positions": {"stale": True, "data": {"positions": [], "reason": "ACCOUNT_NOT_VERIFIED"}}}))
        self.assertFalse(has_old_data({"get_market_overview": {"stale": True, "data": {"scan": {"note": "NO_SCAN_RESULTS_YET"}, "movers": {}}}}))
        self.assertFalse(has_old_data({"x": {"stale": True, "data": {"error": "Could not verify tool data."}}}))
        self.assertTrue(has_old_data({"get_my_positions": {"stale": True, "data": {"positions": [{"symbol": "BTCUSDT"}], "data_age_seconds": 900}}}))
        self.assertTrue(has_old_data({"a": {"stale": True, "data": {"reason": "R"}}, "b": {"stale": True, "data": {"positions": [1]}}}))


if __name__ == "__main__":
    unittest.main()
