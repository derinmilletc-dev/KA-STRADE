import importlib.util
import json
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("premium_access_teaser", Path(__file__).resolve().parents[1] / "app" / "premium_access.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)  # type: ignore[union-attr]


class ScannerTeaserTests(unittest.TestCase):
    def test_teaser_exposes_counts_only(self):
        scanner = {
            "scan_status": "TAMAMLANDI", "running": False, "last_scan_at": "2026-10-18T09:00:00+00:00",
            "coins_scanned": 20, "eligible_count": 4, "last_error": None,
            "top_candidates": [{"symbol": "BTCUSDT", "direction": "SHORT", "score": 81.4, "entry": 1}],
            "all_candidates": [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}],
            "selected_symbols": ["BTCUSDT"], "diagnostics": {"sample_error": "secret"},
        }
        teaser = module.scanner_teaser(scanner)
        self.assertTrue(teaser["locked"])
        self.assertEqual((teaser["coins_scanned"], teaser["eligible_count"], teaser["locked_count"]), (20, 4, 2))
        self.assertEqual(teaser["all_candidates"], [])
        self.assertEqual(teaser["top_candidates"], [])
        dumped = json.dumps(teaser)
        for leaked in ("BTCUSDT", "ETHUSDT", "81.4", "secret", "selected_symbols", "diagnostics"):
            self.assertNotIn(leaked, dumped)

    def test_error_text_is_generic_and_bad_input_is_safe(self):
        self.assertEqual(module.scanner_teaser({"last_error": "Binance 451 internal"})["last_error"], "Tarama tamamlanamadı. Biraz sonra tekrar dene.")
        for bad in (None, [], "x", {"coins_scanned": True, "eligible_count": -3}):
            teaser = module.scanner_teaser(bad)
            self.assertEqual((teaser["coins_scanned"], teaser["eligible_count"], teaser["locked_count"]), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
