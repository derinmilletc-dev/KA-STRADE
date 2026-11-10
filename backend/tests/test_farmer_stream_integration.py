"""KAIS FARMER C4 — Original V2 paylaşımlı akış talebine eklemeli entegrasyon.
`original_v2_live` fastapi/websockets ister; yoksa bu dosya atlanır (CI'da çalışır)."""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))

try:
    from app import live_registry
    from app import original_v2_live as live
    SKIP = ""
except ImportError as exc:                                    # pragma: no cover - ortam bağımlı
    live = live_registry = None                               # type: ignore[assignment]
    SKIP = f"original_v2_live içe aktarılamadı: {exc}"

from app.farmer_engine.demand import STATE_ATTR  # noqa: E402


class RecordingStream:
    def __init__(self):
        self.configured: list[tuple[list[str], tuple[str, ...]]] = []

    async def configure(self, symbols, intervals):
        self.configured.append((symbols, tuple(intervals)))


def make_app(farmer=None):
    state = SimpleNamespace(original_kline_stream=RecordingStream())
    if farmer is not None:
        setattr(state, STATE_ATTR, farmer)
    return SimpleNamespace(state=state)


@unittest.skipIf(bool(SKIP), SKIP)
class TestStreamDemand(unittest.TestCase):
    def setUp(self):
        self._enabled = live_registry.enabled
        live_registry.enabled = lambda: False          # tek kullanıcılı yol; çoklu üye yolu aşağıda ayrıca

    def tearDown(self):
        live_registry.enabled = self._enabled

    def test_farmer_off_original_demand_passes_through_unchanged(self):
        app = make_app()
        symbols = ["AAAUSDT", "BBBUSDT"]
        asyncio.run(live.configure_original_kline_stream(app, symbols))
        sent, intervals = app.state.original_kline_stream.configured[-1]
        self.assertIs(sent, symbols)                   # bit bit aynı nesne: Farmer kapalıyken davranış değişmez
        self.assertEqual(intervals, ("15m", "30m", "1h", "4h"))

    def test_farmer_demand_is_added_to_original_demand(self):
        app = make_app(farmer=["CCCUSDT", "AAAUSDT"])
        asyncio.run(live.configure_original_kline_stream(app, ["AAAUSDT", "BBBUSDT"]))
        self.assertEqual(app.state.original_kline_stream.configured[-1][0], ["AAAUSDT", "BBBUSDT", "CCCUSDT"])

    def test_original_stop_keeps_farmer_symbols(self):
        app = make_app(farmer=["CCCUSDT"])
        asyncio.run(live.configure_original_kline_stream(app, []))      # Original V2 durdu
        self.assertEqual(app.state.original_kline_stream.configured[-1][0], ["CCCUSDT"])

    def test_apply_farmer_demand_keeps_original_base_and_can_be_withdrawn(self):
        app = make_app()
        asyncio.run(live.configure_original_kline_stream(app, ["AAAUSDT"]))
        merged = asyncio.run(live.apply_farmer_stream_demand(app, ["BBBUSDT"]))
        self.assertEqual(merged, ["AAAUSDT", "BBBUSDT"])
        self.assertEqual(app.state.original_kline_stream.configured[-1][0], ["AAAUSDT", "BBBUSDT"])
        merged = asyncio.run(live.apply_farmer_stream_demand(app, []))   # Farmer kapandı
        self.assertEqual(merged, ["AAAUSDT"])
        self.assertEqual(app.state.original_kline_stream.configured[-1][0], ["AAAUSDT"])

    def test_apply_farmer_demand_without_original_running(self):
        app = make_app()
        merged = asyncio.run(live.apply_farmer_stream_demand(app, ["BBBUSDT"]))
        self.assertEqual(merged, ["BBBUSDT"])

    def test_no_stream_returns_empty(self):
        app = SimpleNamespace(state=SimpleNamespace())
        self.assertEqual(asyncio.run(live.apply_farmer_stream_demand(app, ["BBBUSDT"])), [])

    def test_original_symbols_are_never_displaced_by_farmer_overflow(self):
        base = [f"S{i}USDT" for i in range(100)]
        app = make_app(farmer=["ZZZUSDT"])
        asyncio.run(live.configure_original_kline_stream(app, base))
        self.assertEqual(app.state.original_kline_stream.configured[-1][0], base)


@unittest.skipIf(bool(SKIP), SKIP)
class TestMultiMemberPath(unittest.TestCase):
    def test_registry_path_unions_members_then_adds_farmer(self):
        original = live_registry.enabled
        live_registry.enabled = lambda: True
        try:
            app = make_app(farmer=["FFFUSDT"])
            app.state.original_symbol_demands = {"other": ["OOOUSDT"]}
            asyncio.run(live.configure_original_kline_stream(app, ["AAAUSDT"]))
            sent = app.state.original_kline_stream.configured[-1][0]
            self.assertEqual(sent, ["OOOUSDT", "AAAUSDT", "FFFUSDT"])
            self.assertEqual(app.state.original_symbol_demands["other"], ["OOOUSDT"])      # başka üyenin talebi bozulmadı
            merged = asyncio.run(live.apply_farmer_stream_demand(app, ["GGGUSDT"]))
            self.assertEqual(merged[:2], ["OOOUSDT", "AAAUSDT"])
            self.assertIn("GGGUSDT", merged)
        finally:
            live_registry.enabled = original


if __name__ == "__main__":
    unittest.main()
