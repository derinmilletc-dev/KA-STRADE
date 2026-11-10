"""KAIS FARMER sinyal mumu: varsayılan 3m, ortamdan 15m'e dönüş. Modül düzeyi sabitler yüklemede okunduğu için ayrı süreçte sınanır."""
import json
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def probe(env_value, confirm=None):
    code = textwrap.dedent("""
        import json
        from app.farmer_engine import feed, orchestrator
        rows = {}
        step = feed.INTERVAL_MS[feed.SIGNAL_INTERVAL]
        out = {"signal": feed.SIGNAL_INTERVAL, "intervals": list(feed.INTERVALS), "candle_ms": orchestrator.CANDLE_MS,
               "last_closed": feed.FarmerFeed.last_closed_open_ms(1_800_000_000_000)}
        print(json.dumps(out))
    """)
    env = {**os.environ}
    env.pop("KAISTRADE_FARMER_SIGNAL_INTERVAL", None)
    env.pop("KAISTRADE_FARMER_CONFIRM_INTERVALS", None)
    if env_value is not None:
        env["KAISTRADE_FARMER_SIGNAL_INTERVAL"] = env_value
    if confirm is not None:
        env["KAISTRADE_FARMER_CONFIRM_INTERVALS"] = confirm
    done = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


class FarmerSignalIntervalTests(unittest.TestCase):
    def test_default_is_three_minutes(self):
        out = probe(None)
        self.assertEqual(out["signal"], "3m")
        self.assertEqual(out["intervals"], ["3m", "5m", "15m"])        # anlık görünüm: 3m sinyal, 5m ve 15m onay
        self.assertEqual(out["candle_ms"], 180_000)
        self.assertEqual(out["last_closed"], 1_800_000_000_000 // 180_000 * 180_000 - 180_000)

    def test_env_can_return_to_fifteen_minutes(self):
        out = probe("15m")
        self.assertEqual((out["signal"], out["intervals"], out["candle_ms"]), ("15m", ["15m", "1h", "4h"], 900_000))

    def test_confirm_intervals_can_return_to_hour_and_four_hour(self):
        self.assertEqual(probe(None, "1h,4h")["intervals"], ["3m", "1h", "4h"])
        self.assertEqual(probe(None, "2m,7m")["intervals"], ["3m", "1h", "4h"])           # geçersiz: eski, test edilmiş onay mumları

    def test_invalid_value_falls_back_to_the_old_tested_interval(self):
        self.assertEqual(probe("2m")["signal"], "15m")

    def test_three_minute_frames_feed_the_strategy_primary_window(self):
        code = textwrap.dedent("""
            import json
            from app.farmer_engine import feed
            from app import backtest_data
            base = 1_800_000_000_000 // 900_000 * 900_000
            def rows(step, n):
                start = base - step * n
                return [[start + i * step, 10, 11, 9, 10, 1, start + (i + 1) * step - 1, 1] for i in range(n)]
            data = {"3m": rows(180_000, 261), "5m": rows(300_000, 261), "15m": rows(900_000, 261)}
            frames = feed.default_frames_builder("BTCUSDT", data, base // 1000)
            ok = isinstance(frames, dict)
            slots = {k: (frames[k][-1]["time"] if ok else None) for k in ("15m", "1h", "4h")} if ok else {}
            want = {"15m": base // 1000 - 180, "1h": base // 1000 - 300, "4h": base // 1000 - 900}
            print(json.dumps({"keys": sorted(frames) if ok else str(frames), "ok": slots == want}))
        """)
        env = {**os.environ}; env.pop("KAISTRADE_FARMER_SIGNAL_INTERVAL", None); env.pop("KAISTRADE_FARMER_CONFIRM_INTERVALS", None)
        done = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(json.loads(done.stdout.strip().splitlines()[-1])["ok"])      # 15m yuvası=3m, 1h yuvası=5m, 4h yuvası=15m
        self.assertEqual(json.loads(done.stdout.strip().splitlines()[-1])["keys"], ["15m", "1h", "4h"])   # 3m, 5m, 15m strateji yuvalarına konur


if __name__ == "__main__":
    unittest.main()
