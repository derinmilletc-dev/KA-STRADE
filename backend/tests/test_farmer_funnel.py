"""KAIS FARMER eleme hunisi (yalnız okuma): adaylar şartları sırayla kaç tanesi geçiyor. Çalıştırma: cd backend && python -B -m unittest tests.test_farmer_funnel -v"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.farmer_engine import confirm as c  # noqa: E402
from app.farmer_engine.feed import candidate_from_legacy  # noqa: E402
from app.farmer_engine.orchestrator import Orchestrator  # noqa: E402
from tests.test_farmer_confirm import legacy, params  # noqa: E402


def item(symbol, direction="LONG", **over):
    return candidate_from_legacy(symbol, "3m", 1000, legacy(direction, **over), 1.0)


def funnel(items, **param_over):
    fake = SimpleNamespace(feed=SimpleNamespace(cache=SimpleNamespace(all=lambda: items)), settings=SimpleNamespace(confirm=params(**param_over)),
                           FUNNEL_STAGES=Orchestrator.FUNNEL_STAGES)
    out = Orchestrator.filter_funnel(fake)
    return out, {s["code"]: s["passed"] for s in out["stages"]}


class TestFunnel(unittest.TestCase):
    def test_counts_each_stage_in_order(self):
        items = [
            item("AAAUSDT"),                                                    # hepsini geçer
            item("BBBUSDT", analysis={"confidence": 80}),                       # skorda elenir
            item("CCCUSDT", analysis={"momentum": "Negatif"}),                  # ivmede elenir
            item("DDDUSDT", mtf={"entry_permission": False}),                   # MTF'de elenir
            item("EEEUSDT", "SHORT", mtf={"short_filter_v2": {"accepted": False}}),   # SHORT filtresinde elenir
            item("FFFUSDT", "BEKLE", analysis={"momentum": "Nötr"}),            # yönsüz
        ]
        out, st = funnel(items)
        self.assertEqual((out["scanned"], out["directional"], out["min_score"]), (6, 5, 92))
        self.assertEqual(st, {"score": 4, "momentum": 3, "mtf": 2, "short_filter": 1, "quality": 1})

    def test_empty_cache(self):
        out, st = funnel([])
        self.assertEqual((out["scanned"], out["directional"]), (0, 0))
        self.assertTrue(all(v == 0 for v in st.values()))

    def test_lower_min_score_lets_more_through(self):
        items = [item("AAAUSDT", analysis={"confidence": 85})]
        self.assertEqual(funnel(items, min_score=92)[1]["score"], 0)
        self.assertEqual(funnel(items, min_score=85)[1]["quality"], 1)


if __name__ == "__main__":
    unittest.main()
