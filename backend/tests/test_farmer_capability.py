"""KAIS FARMER C0 — yetenek kapısı testleri (ağ yok, fastapi gerekmez).
Çalıştırma: cd backend && python -B -m unittest tests.test_farmer_capability -v"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.farmer_engine import DISPLAY_NAME  # noqa: E402
from app.farmer_engine import capability as cap  # noqa: E402


class TestCapabilityGate(unittest.TestCase):
    def test_display_name(self):
        self.assertEqual(DISPLAY_NAME, "KAIS FARMER")

    def test_all_eleven_items_are_listed_once(self):
        keys = [c.key for c in cap.CAPABILITIES]
        self.assertEqual(len(keys), 11)
        self.assertEqual(len(set(keys)), 11)
        self.assertTrue(all(c.status in (cap.REPO_EVIDENCE, cap.UNVERIFIED) for c in cap.CAPABILITIES))
        self.assertTrue(all(c.evidence.strip() for c in cap.CAPABILITIES))

    def test_unverified_items_are_not_claimed_verified(self):
        names = {c.key for c in cap.unverified()}
        # canlı Binance'te denenmeden doğrulandı sayılmaması gerekenler
        self.assertTrue({"full_position_by_quantity", "price_protect_effect", "triggered_record_shape", "already_crossed_error"} <= names)

    def test_default_environment_blocks_live(self):
        reasons = cap.live_entry_blockers({})
        self.assertEqual(len(reasons), 2)
        self.assertIn(cap.LIVE_ENV_FLAG, reasons[0])
        self.assertIn(cap.TP_MARKET_VERIFIED_ENV, reasons[1])

    def test_live_flag_alone_is_not_enough(self):
        reasons = cap.live_entry_blockers({cap.LIVE_ENV_FLAG: "1"})
        self.assertEqual(len(reasons), 1)
        self.assertIn("doğrulanmadı", reasons[0])
        self.assertIn("full_position_by_quantity", reasons[0])

    def test_declaration_alone_is_not_enough(self):
        self.assertEqual(len(cap.live_entry_blockers({cap.TP_MARKET_VERIFIED_ENV: "1"})), 1)

    def test_both_flags_open_the_gate(self):
        self.assertEqual(cap.live_entry_blockers({cap.LIVE_ENV_FLAG: "true", cap.TP_MARKET_VERIFIED_ENV: "yes"}), [])

    def test_falsey_values_do_not_open(self):
        for value in ("0", "false", "off", "", "no", "anything"):
            self.assertEqual(len(cap.live_entry_blockers({cap.LIVE_ENV_FLAG: value, cap.TP_MARKET_VERIFIED_ENV: value})), 2, value)


if __name__ == "__main__":
    unittest.main()
