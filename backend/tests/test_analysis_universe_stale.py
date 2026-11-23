"""Analiz evreni: bayat sonuç anında döner, yenileme arka planda (ilk açılışta uzun bekleme olmaz)."""
import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from app import main
except Exception:  # pragma: no cover - ortamda ağır bağımlılık yoksa atla
    main = None


@unittest.skipIf(main is None, "app.main yüklenemedi")
class StaleUniverseTests(unittest.TestCase):
    def setUp(self):
        main.ANALYSIS_UNIVERSE_CACHE.clear()
        main._UNIVERSE_REFRESHING.clear()

    def call(self, age):
        main.ANALYSIS_UNIVERSE_CACHE["15m"] = (time.monotonic() - age, [{"symbol": "BTCUSDT"}])
        compute = AsyncMock(return_value={"cached": False, "results": []})

        async def run():
            with patch.object(main, "_analysis_universe", compute), patch.object(main, "_schedule_universe_refresh") as schedule:
                result = await main.analysis_universe("15m", 100)
            return result, schedule, compute
        return asyncio.run(run())

    def test_stale_returns_immediately_and_schedules_refresh(self):
        result, schedule, compute = self.call(400)
        self.assertTrue(result["stale"]); self.assertEqual(result["results"], [{"symbol": "BTCUSDT"}])
        schedule.assert_called_once_with("15m", 100); compute.assert_not_awaited()

    def test_fresh_and_too_old_use_normal_path(self):
        for age in (10, 4000):
            result, schedule, compute = self.call(age)
            self.assertNotIn("stale", result); compute.assert_awaited_once(); schedule.assert_not_called()


if __name__ == "__main__":
    unittest.main()
