"""KAIS FARMER C4 — FarmerFeed + GERÇEK Original V2 stratejisi (legacy.analysis / legacy.mtf) uçtan uca.
Strateji `main` içe aktarmasını ister (fastapi vb.); yoksa atlanır."""
from __future__ import annotations

import asyncio
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

try:
    import app.main  # noqa: F401
    SKIP = ""
except ImportError as exc:                                    # pragma: no cover - ortam bağımlı
    SKIP = f"app.main içe aktarılamadı: {exc}"

from app.farmer_engine import confirm as c  # noqa: E402
from app.farmer_engine import feed as f  # noqa: E402
from tests.test_farmer_feed import NOW, STEP, FakeStream  # noqa: E402


def trend_rows(interval, now_ms, drift, seed, count=262):
    rnd = random.Random(seed)
    step = STEP[interval]
    forming = now_ms // step * step
    price, out = 100.0, []
    for k in range(count - 1, -1, -1):
        t = forming - k * step
        o = price
        price = price * (1 + drift * (step / 900000) ** 0.5 + rnd.uniform(-.003, .003))
        h, l = max(o, price) * (1 + rnd.uniform(0, .002)), min(o, price) * (1 - rnd.uniform(0, .002))
        out.append([t, str(o), str(h), str(l), str(price), str(1000 * (1 + rnd.random())), t + step - 1, str(1e5 * (1 + rnd.random())), 10, "0", "0", "0"])
    return out


@unittest.skipIf(bool(SKIP), SKIP)
class TestRealStrategy(unittest.TestCase):
    def setUp(self):
        self.symbols = ["UPUSDT", "DNUSDT", "FLATUSDT"]
        self.stream = FakeStream(self.symbols)
        for symbol, drift, seed in (("UPUSDT", 0.0012, 3), ("DNUSDT", -0.0012, 5), ("FLATUSDT", 0.0, 7)):
            for interval in f.INTERVALS:
                self.stream.rows[(symbol, interval)] = trend_rows(interval, NOW, drift, seed + len(interval))
        self.feed = f.FarmerFeed(self.stream)                      # varsayılan çerçeve kurucu + varsayılan (gerçek) değerlendirici
        asyncio.run(self.feed.set_demand(self.symbols))

    def test_real_candidates_have_original_v2_fields(self):
        res = self.feed.scan(NOW)
        self.assertEqual((res.scanned, res.unavailable), (3, {}))
        for cand in self.feed.cache.all():
            self.assertIn(cand.direction, ("LONG", "SHORT", "BEKLE"))
            self.assertIsNotNone(cand.confidence)
            self.assertIn(cand.momentum, ("Pozitif", "Negatif", "Nötr"))
            self.assertIsNotNone(cand.trap_score)
            self.assertIsNotNone(cand.breakout_quality)
            self.assertGreater(cand.atr, 0)
            self.assertGreater(cand.entry_price, 0)
            self.assertIn("entry_permission", cand.mtf)
            self.assertIn("alignment", cand.mtf)
            self.assertNotIn("series", cand.snapshot)
            if cand.direction == "SHORT" and cand.mtf.get("short_filter_v2") is not None:
                self.assertIsInstance(cand.short_filter["accepted"], bool)

    def test_short_candidate_carries_short_filter_decision(self):
        self.feed.scan(NOW)
        shorts = [x for x in self.feed.cache.all() if x.direction == "SHORT"]
        self.assertTrue(shorts, "aşağı eğilimli seri SHORT üretmeliydi")
        self.assertTrue(all(isinstance(x.short_filter, dict) and "accepted" in x.short_filter for x in shorts))

    def test_mtf_direction_semantics_match_confirmation_layer(self):
        self.feed.scan(NOW)
        params = c.ConfirmParams(variant=c.STRATEGY_QUALITY, min_score=1, margin_usdt=50, leverage=3, target_usdt=1.0, max_loss_usdt=1.5)
        for cand in self.feed.cache.all():
            result = c.confirm(cand.to_farmer_candidate(), params)
            if cand.direction in ("LONG", "SHORT") and cand.mtf.get("direction") != cand.direction:
                self.assertIn(c.MTF_NOT_CONFIRMED, result.reasons)            # MTF onaylamadıysa onay katmanı reddeder
            if cand.direction == "BEKLE":
                self.assertEqual(result.reasons, (c.INVALID_DIRECTION,))

    def test_same_candle_does_not_recompute_real_strategy(self):
        self.feed.scan(NOW)
        n = self.feed.evaluations
        self.feed.scan(NOW + 60_000)
        self.assertEqual(self.feed.evaluations, n)


if __name__ == "__main__":
    unittest.main()
