"""KAIS FARMER C10 — arayüz görünüm katmanı (saf) ve orkestratör aday görünümü. Ağ/emir yok."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import farmer_view as v  # noqa: E402
from app.farmer_engine import orchestrator as o  # noqa: E402
from tests.test_farmer_orchestrator import ACC, Rig  # noqa: E402

ONE = {"BBBUSDT": {"confidence": 50}, "CCCUSDT": {"confidence": 50}}
ARM_ON = {"active": True, "expires_at": 4102444800.0}


def runtime_of(r):
    return SimpleNamespace(orchestrator=r.orch)


class TestSettings(unittest.TestCase):
    def test_defaults_are_recommended(self):
        s = v.parse_settings({})
        self.assertEqual((s.confirm.margin_usdt, s.confirm.leverage, s.confirm.target_usdt, s.confirm.max_loss_usdt, s.confirm.min_score),
                         (50.0, 3.0, 1.0, 1.5, 92))
        self.assertEqual((s.max_positions, s.total_margin_cap, s.dead_score, s.dead_confirmations), (5, 100.0, 75, 2))

    def test_invalid_values_rejected_not_clamped(self):
        for body, field in [({"margin_usdt": 60}, "margin_usdt"), ({"leverage": 0}, "leverage"), ({"leverage": 2.5}, "leverage"),
                            ({"target_usdt": -1}, "target_usdt"), ({"max_loss_usdt": "x"}, "max_loss_usdt"), ({"min_score": 101}, "min_score"),
                            ({"total_margin_cap": 150}, "total_margin_cap"), ({"max_positions": 6}, "max_positions"),
                            ({"dead_score": 10}, "dead_score"), ({"target_usdt": True}, "target_usdt"), ({"target_usdt": float("nan")}, "target_usdt")]:
            with self.assertRaises(v.SettingsError, msg=str(body)) as cm:
                v.parse_settings(body)
            self.assertEqual(cm.exception.field, field)

    def test_hard_cap_cannot_be_raised(self):
        with self.assertRaises(v.SettingsError):
            v.parse_settings({"total_margin_cap": 100.01})

    def test_margin_above_cap_rejected(self):
        with self.assertRaises(v.SettingsError) as cm:
            v.parse_settings({"margin_usdt": 100, "total_margin_cap": 50})
        self.assertEqual(cm.exception.code, "MARGIN_ABOVE_CAP")

    def test_dead_score_can_be_disabled(self):
        self.assertIsNone(v.parse_settings({"dead_score": None}).dead_score)

    def test_base_values_used_for_missing_fields(self):
        s = v.parse_settings({"target_usdt": 2.0}, {"margin_usdt": 25, "min_score": 95})
        self.assertEqual((s.confirm.margin_usdt, s.confirm.min_score, s.confirm.target_usdt), (25.0, 95, 2.0))


class TestPreview(unittest.TestCase):
    def test_numbers_come_from_backend_feasibility(self):
        p = v.preview(v.parse_settings({}))
        self.assertEqual((p["notional_usdt"], p["effective_capacity"], p["margin_usdt"], p["leverage"]), (150.0, 2, 50.0, 3.0))
        self.assertAlmostEqual(p["est_cost_usdt"], 150 * 0.0005 * 2 + 0.05)
        self.assertEqual(p["verdict"], "GOOD")
        self.assertIn("MARGIN_CAP_LIMITS", p["warnings"])               # 5 istendi, bütçe 2'ye izin veriyor

    def test_tiny_target_is_tight(self):
        p = v.preview(v.parse_settings({"target_usdt": 0.2}))
        self.assertEqual(p["verdict"], "TIGHT")
        self.assertIn("TARGET_TOO_SMALL", p["warnings"])

    def test_loss_below_cost_is_not_recommended(self):
        p = v.preview(v.parse_settings({"max_loss_usdt": 0.1}))
        self.assertEqual(p["verdict"], "NOT_RECOMMENDED")

    def test_high_leverage_warning(self):
        self.assertIn("HIGH_LEVERAGE", v.preview(v.parse_settings({"leverage": 5}))["warnings"])


class TestStatusUnavailable(unittest.TestCase):
    def test_no_runtime_is_honest(self):
        s = v.build_status(None, arm={"active": False, "expires_at": None}, env={}, now=1e9)
        self.assertEqual((s["available"], s["state"], s["name"]), (False, "OFF", "KAIS FARMER"))
        self.assertEqual(s["blockers"], ["LIVE_FLAG_OFF", "EXCHANGE_BEHAVIOUR_NOT_DECLARED_VERIFIED"])
        self.assertIsNone(s["slots"])
        self.assertEqual(s["why"]["code"], "UNAVAILABLE")
        self.assertTrue(s["provisional"])

    def test_gate_codes_clear_when_both_set(self):
        env = {"KAISTRADE_FARMER_LIVE": "1", "KAISTRADE_FARMER_TPM_VERIFIED": "1"}
        self.assertEqual(v.entry_blockers_public(env), [])

    def test_no_internal_names_in_payload(self):
        s = v.build_status(None, arm=ARM_ON, env={}, now=1e9)
        text = repr(s).lower()
        for banned in ("apf", "farmer v1", "farmer_v1"):
            self.assertNotIn(banned, text)


class TestStatusLive(unittest.TestCase):
    def test_open_position_view(self):
        r = Rig(table=dict(ONE))
        r.run()
        s = v.build_status(runtime_of(r), arm=ARM_ON, env={}, now=r.now / 1000)
        self.assertEqual((s["available"], s["state"], s["effective_capacity"]), (True, o.RUNNING, 2))
        self.assertEqual(len(s["positions"]), 1)
        pos = s["positions"][0]
        self.assertEqual((pos["symbol"], pos["pnl_state"], pos["verified_net_pnl"]), ("AAAUSDT", "PENDING", None))   # bilinmeyen PnL sayı değil None
        self.assertTrue(pos["entry_price"] and pos["tp_price"] and pos["stop_price"])                              # ekran için gerçek seviyeler
        self.assertLess(pos["stop_price"], pos["entry_price"])
        self.assertGreater(pos["tp_price"], pos["entry_price"])
        self.assertEqual(s["slots"]["open"], 1)
        self.assertEqual((s["margin_used_usdt"], s["available_margin_usdt"]), (50.0, 50.0))                       # sunucu hesaplar; arayüz toplamaz
        self.assertEqual(s["why"]["code"], "SCORE_TOO_LOW")                       # kalan adaylar skor altında: zorla işlem yok
        self.assertEqual(sorted(x["status"] for x in s["scanner"]), ["QUALIFIED", "REJECTED", "REJECTED"])

    def test_today_totals_only_verified_counted(self):
        d = 86400.0 * 3
        rows = [{"closed_at": d + 100, "pnl_state": "VERIFIED", "verified_net_pnl": 1.25}, {"closed_at": d + 101, "pnl_state": "UNVERIFIED", "verified_net_pnl": None},
                {"closed_at": d + 102, "pnl_state": "PENDING", "verified_net_pnl": None}, {"closed_at": 0, "pnl_state": "PENDING"},
                {"closed_at": d - 50, "pnl_state": "VERIFIED", "verified_net_pnl": 9.0}]          # dünün sonucu sayılmaz
        t = v._today_totals(rows, d + 500)
        self.assertEqual((t["verified_net_pnl"], t["verified_count"], t["unverified_count"], t["pending_count"]), (1.25, 1, 1, 1))

    def test_today_unknown_is_none_not_zero(self):
        t = v._today_totals([{"closed_at": 100.0, "pnl_state": "UNVERIFIED", "verified_net_pnl": None}], 200.0)
        self.assertIsNone(t["verified_net_pnl"])

    def test_arm_paused_state_reported(self):
        r = Rig(table=dict(ONE))
        r.run()
        r.gates.arm = ("ARM_EXPIRED",)
        r.tick()
        s = v.build_status(runtime_of(r), arm={"active": False, "expires_at": None}, env={}, now=r.now / 1000)
        self.assertEqual((s["state"], s["why"]["code"]), (o.ENTRY_PAUSED_ARM, "ARM_EXPIRED"))
        self.assertEqual(len(s["positions"]), 1)                                  # koruma/pozisyon görünür kalır

    def test_intervention_reported(self):
        r = Rig(table=dict(ONE))
        r.orch.recover()
        r.orch._intervene("x")
        s = v.build_status(runtime_of(r), arm=ARM_ON, env={}, now=r.now / 1000)
        self.assertEqual((s["state"], s["why"]["code"]), (o.INTERVENTION, "INTERVENTION"))

    def test_off_with_no_positions(self):
        r = Rig(table=dict(ONE))
        r.orch.recover()
        s = v.build_status(runtime_of(r), arm=ARM_ON, env={}, now=r.now / 1000)
        self.assertEqual((s["state"], s["why"]["code"], s["positions"]), (o.OFF, "OFF", []))

    def test_stop_bot_reported(self):
        r = Rig(table=dict(ONE))
        r.run()
        r.orch.stop_bot()
        s = v.build_status(runtime_of(r), arm=ARM_ON, env={}, now=r.now / 1000)
        self.assertEqual((s["state"], s["why"]["code"]), (o.STOPPING, "STOP_BOT"))


class TestWhy(unittest.TestCase):
    base = {"state": o.RUNNING, "slots": {"free": 2}, "effective_capacity": 2}

    def test_priority_and_codes(self):
        w = v.why_no_position
        self.assertEqual(w({**self.base, "state": o.ENTRY_PAUSED_SAFETY}, [], ("DAILY_LOSS_LIMIT",))["code"], "DAILY_LOSS")
        self.assertEqual(w({**self.base, "state": o.ENTRY_PAUSED_SAFETY}, [])["code"], "SAFETY_GATE")
        self.assertEqual(w({**self.base, "effective_capacity": 0}, [])["code"], "CAPACITY_ZERO")
        self.assertEqual(w({**self.base, "slots": {"free": 0}}, [])["code"], "CAPACITY_FULL")
        self.assertEqual(w(self.base, [])["code"], "NO_CANDIDATES")
        rej = [{"symbol": "A", "status": "REJECTED", "reason": "MTF_NOT_CONFIRMED"}, {"symbol": "B", "status": "REJECTED", "reason": "SCORE_TOO_LOW"},
               {"symbol": "C", "status": "REJECTED", "reason": "MTF_NOT_CONFIRMED"}]
        out = w(self.base, rej)
        self.assertEqual((out["code"], out["details"][0]), ("MTF_NOT_CONFIRMED", {"code": "MTF_NOT_CONFIRMED", "count": 2}))
        cool = [{"symbol": "A", "status": "COOLING_DOWN", "reason": "ENTRY_LOCKED"}]
        self.assertEqual(w(self.base, cool)["code"], "COOLDOWN")
        wait = [{"symbol": "A", "status": "WAITING_CONFIRMATION", "reason": None}]
        self.assertEqual(w(self.base, wait)["code"], "WAITING_CONFIRMATION")


class TestScannerView(unittest.TestCase):
    def test_statuses_reflect_cache_and_rejections(self):
        r = Rig(table={"AAAUSDT": {"confidence": 96}, "BBBUSDT": {"confidence": 70}, "CCCUSDT": {"confidence": 95}})
        r.run()
        rows = {x["symbol"]: x for x in r.orch.scanner_view(r.now)}
        self.assertEqual(rows["BBBUSDT"]["status"], "REJECTED")
        self.assertEqual(rows["BBBUSDT"]["reason"], "SCORE_TOO_LOW")
        self.assertEqual(rows["AAAUSDT"]["status"], "QUALIFIED")
        self.assertTrue(all(x["score"] is not None for x in rows.values()))

    def test_scanner_view_is_read_only(self):
        r = Rig(table=dict(ONE))
        r.run()
        before = (len(r.ex.placed), r.orch.guard.slots().as_dict(), dict(r.orch._tried))
        r.orch.scanner_view(r.now)
        self.assertEqual((len(r.ex.placed), r.orch.guard.slots().as_dict(), dict(r.orch._tried)), before)


if __name__ == "__main__":
    unittest.main()
