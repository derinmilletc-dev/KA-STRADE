"""KAIS FARMER C2 — kalıcı durum testleri (ağ yok). Çalıştırma: cd backend && python -B -m unittest tests.test_farmer_store -v"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.farmer_engine.store import CLAIMED, DUPLICATE, OVER_CAP, FarmerStore  # noqa: E402
from app.grid_engine.store import GridStore  # noqa: E402

ACC = "u1:live"


class TestClaim(unittest.TestCase):
    def setUp(self):
        self.s = FarmerStore()

    def claim(self, grid="F1", symbol="BTCUSDT", ms=1000, margin=25.0, cap=100.0, acc=ACC):
        return self.s.claim_entry(acc, symbol, "15m", ms, grid, margin, cap_usdt=cap)

    def test_first_claim_ok_and_held(self):
        self.assertEqual(self.claim(), CLAIMED)
        self.assertAlmostEqual(self.s.held_margin(ACC), 25.0)

    def test_same_candle_same_symbol_is_duplicate_and_holds_nothing_extra(self):
        self.assertEqual(self.claim("F1"), CLAIMED)
        self.assertEqual(self.claim("F2"), DUPLICATE)
        self.assertAlmostEqual(self.s.held_margin(ACC), 25.0)
        self.assertEqual(len(self.s.holds(ACC)), 1)

    def test_temp_reject_does_not_free_the_candle(self):
        self.claim("F1")
        self.s.set_attempt_outcome("F1", "REJECTED_TEMP")
        self.s.release_hold("F1")
        self.assertEqual(self.claim("F2"), DUPLICATE)
        self.assertEqual(self.s.attempt(ACC, "BTCUSDT", "15m", 1000)["outcome"], "REJECTED_TEMP")

    def test_next_candle_allowed_after_release(self):
        self.claim("F1")
        self.s.release_hold("F1")
        self.assertEqual(self.claim("F2", ms=2000), CLAIMED)

    def test_other_symbol_same_candle_allowed(self):
        self.claim("F1")
        self.assertEqual(self.claim("F2", symbol="ETHUSDT"), CLAIMED)

    def test_cap_enforced_and_nothing_written_when_over(self):
        for i in range(4):
            self.assertEqual(self.claim(f"F{i}", symbol=f"S{i}USDT"), CLAIMED)
        self.assertEqual(self.claim("F9", symbol="XUSDT"), OVER_CAP)
        self.assertAlmostEqual(self.s.held_margin(ACC), 100.0)
        self.assertIsNone(self.s.attempt(ACC, "XUSDT", "15m", 1000))

    def test_release_frees_cap_and_is_idempotent(self):
        for i in range(4):
            self.claim(f"F{i}", symbol=f"S{i}USDT")
        self.assertTrue(self.s.release_hold("F0"))
        self.assertFalse(self.s.release_hold("F0"))
        self.assertAlmostEqual(self.s.held_margin(ACC), 75.0)
        self.assertEqual(self.claim("F9", symbol="XUSDT"), CLAIMED)

    def test_accounts_are_independent(self):
        self.claim("F1")
        self.assertEqual(self.claim("F2", acc="u2:live"), CLAIMED)

    def test_invalid_values_rejected(self):
        with self.assertRaises(ValueError):
            self.claim(margin=0)
        with self.assertRaises(ValueError):
            self.claim(cap=-1)

    def test_open_state_still_counts(self):
        self.claim("F1")
        self.s.mark_hold_open("F1")
        self.assertAlmostEqual(self.s.held_margin(ACC), 25.0)


class TestConcurrency(unittest.TestCase):
    def test_parallel_claims_for_same_candle_exactly_one_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "farmer.sqlite3")
            outcomes, stores = [], [FarmerStore(path, check_same_thread=False) for _ in range(8)]

            def run(i):
                outcomes.append(stores[i].claim_entry(ACC, "BTCUSDT", "15m", 5000, f"F{i}", 25.0, cap_usdt=100.0))

            threads = [threading.Thread(target=run, args=(i,)) for i in range(8)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            self.assertEqual(outcomes.count(CLAIMED), 1, outcomes)
            self.assertEqual(outcomes.count(DUPLICATE), 7)
            self.assertAlmostEqual(stores[0].held_margin(ACC), 25.0)

    def test_parallel_claims_never_exceed_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "farmer.sqlite3")
            stores = [FarmerStore(path, check_same_thread=False) for _ in range(10)]
            outcomes = []

            def run(i):
                outcomes.append(stores[i].claim_entry(ACC, f"S{i}USDT", "15m", 5000, f"F{i}", 25.0, cap_usdt=100.0))

            threads = [threading.Thread(target=run, args=(i,)) for i in range(10)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            self.assertEqual(outcomes.count(CLAIMED), 4, outcomes)
            self.assertLessEqual(stores[0].held_margin(ACC), 100.0 + 1e-9)


class TestPersistence(unittest.TestCase):
    def test_survives_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "farmer.sqlite3")
            a = FarmerStore(path)
            a.claim_entry(ACC, "BTCUSDT", "15m", 1000, "F1", 25.0, cap_usdt=100.0)
            a.save_bot(ACC, "RUNNING", {"margin": 25})
            a.db.close()
            b = FarmerStore(path)
            self.assertEqual(b.claim_entry(ACC, "BTCUSDT", "15m", 1000, "F2", 25.0, cap_usdt=100.0), DUPLICATE)
            self.assertEqual(b.bot(ACC)["settings"], {"margin": 25})
            self.assertAlmostEqual(b.held_margin(ACC), 25.0)

    def test_bot_upsert_keeps_created_at(self):
        s = FarmerStore()
        s.save_bot(ACC, "RUNNING", {"a": 1})
        created = s.bot(ACC)["created_at"]
        s.save_bot(ACC, "PAUSED", {"a": 2}, pause_reason="x")
        got = s.bot(ACC)
        self.assertEqual((got["state"], got["settings"], got["pause_reason"], got["created_at"]), ("PAUSED", {"a": 2}, "x", created))

    def test_no_existing_table_altered(self):
        base, farmer = GridStore(), FarmerStore()
        for table in ("grid", "orders", "alerts"):
            cols = lambda st: [r["name"] for r in st.db.execute(f"PRAGMA table_info({table})")]
            self.assertEqual(cols(base), cols(farmer), table)

    def test_farmer_tables_are_prefixed_and_droppable(self):
        s = FarmerStore()
        names = {r["name"] for r in s.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        extra = names - {r["name"] for r in GridStore().db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(extra and all(n.startswith("farmer_") for n in extra), extra)


class TestCandidatesResultsDead(unittest.TestCase):
    def setUp(self):
        self.s = FarmerStore()

    def test_candidates_order_and_update(self):
        self.s.save_candidate(ACC, "A", 1, "LONG", 5.0, {"x": 1})
        self.s.save_candidate(ACC, "B", 1, "SHORT", 9.0, {"y": 2})
        self.assertEqual([c["symbol"] for c in self.s.candidates(ACC)], ["B", "A"])
        self.s.set_candidate_status(ACC, "B", 1, "REJECTED", "trap")
        self.assertEqual(self.s.candidates(ACC, status="REJECTED")[0]["reject_reason"], "trap")
        self.assertEqual(self.s.candidates(ACC)[1]["snapshot"], {"x": 1})

    def test_result_lifecycle_and_verified_requires_all_parts(self):
        self.s.open_result("F1", ACC, "BTCUSDT", "LONG", target_usdt=0.5, max_loss_usdt=1.0, est_cost_usdt=0.1)
        self.assertEqual(self.s.result("F1")["pnl_state"], "PENDING")
        self.assertIsNone(self.s.result("F1")["verified_net_pnl"])
        with self.assertRaises(ValueError):
            self.s.set_pnl("F1", state="VERIFIED", gross=1.0, commission=0.1, net=0.9)     # funding eksik → doğrulanmış sayılmaz
        self.s.set_pnl("F1", state="UNVERIFIED", error="funding okunamadı")
        self.assertEqual(self.s.result("F1")["pnl_state"], "UNVERIFIED")
        self.assertIsNone(self.s.result("F1")["funding"])
        self.s.set_pnl("F1", state="VERIFIED", gross=1.0, commission=0.1, funding=0.0, net=0.9)
        row = self.s.result("F1")
        self.assertEqual((row["pnl_state"], row["verified_net_pnl"], row["verify_attempts"]), ("VERIFIED", 0.9, 2))

    def test_close_reason_only_set_once_and_validated(self):
        self.s.open_result("F1", ACC, "BTCUSDT", "LONG", target_usdt=0.5, max_loss_usdt=1.0, est_cost_usdt=0.1)
        with self.assertRaises(ValueError):
            self.s.close_result("F1", "WHATEVER")
        self.s.close_result("F1", "TARGET")
        self.s.close_result("F1", "STOP")
        self.assertEqual(self.s.result("F1")["close_reason"], "TARGET")

    def test_dead_counter_counts_closed_candles_once(self):
        self.assertEqual(self.s.dead_count("F1", 100, True), 1)
        self.assertEqual(self.s.dead_count("F1", 100, True), 1)      # aynı mum tekrar → artmaz
        self.assertEqual(self.s.dead_count("F1", 200, True), 2)
        self.assertEqual(self.s.dead_count("F1", 150, True), 2)      # eski mum → yok sayılır
        self.assertEqual(self.s.dead_count("F1", 300, False), 0)     # canlanınca sıfırlanır
        self.assertEqual(self.s.dead_count("F1", 400, True), 1)


if __name__ == "__main__":
    unittest.main()
