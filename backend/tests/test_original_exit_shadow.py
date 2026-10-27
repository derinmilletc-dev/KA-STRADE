"""Akıllı çıkış gölge modu: yalnız kayıt tutar, emir göndermez. Saf fonksiyon testleri + tick'in emir göndermediği."""
from __future__ import annotations

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app import original_exit_shadow as shadow_mod

START = 1_700_000_000.0


def trend(direction: str = "LONG", n: int = 80, start: float = 100.0, step: float = 0.2, wiggle: float = 0.3):
    d = 1 if direction == "LONG" else -1
    rows, price = [], start
    for i in range(n):
        o = price
        c = price + d * step
        rows.append({"time": int(START) - (n - i) * 900, "open": o, "close": c,
                     "high": max(o, c) + wiggle, "low": min(o, c) - wiggle, "volume": 1.0})
        price = c
    return rows


def fresh(model: str = shadow_mod.PULLBACK_MODEL):
    return shadow_mod.new_shadow(START, model)


def run(shadow, *, direction="LONG", entry=100.0, stop=98.0, mark=100.0, quantity=1.0, initial=1.0, now=START, candles=None):
    return shadow_mod.evaluate(shadow, direction=direction, entry=entry, initial_stop=stop, mark=mark, quantity=quantity,
                               initial_quantity=initial, cost_bps=16.0, now=now, candles=candles)


class PhaseTests(unittest.TestCase):
    def test_watch_after_half_r(self):
        s = run(fresh(), mark=101.1)  # R=2 → +0,55R
        self.assertEqual(s["phase"], "WATCH")
        self.assertIsNone(s["proposed_stop"])

    def test_breakeven_after_tp1_long_and_short(self):
        s = run(fresh(), mark=102.0, quantity=0.3)
        self.assertEqual(s["phase"], "BREAKEVEN")
        self.assertAlmostEqual(s["proposed_stop"], 100.0 + 100.0 * 16 / 10000)
        s = run(fresh(), direction="SHORT", stop=102.0, mark=98.0, quantity=0.3)
        self.assertEqual(s["phase"], "BREAKEVEN")
        self.assertAlmostEqual(s["proposed_stop"], 100.0 - 100.0 * 16 / 10000)

    def test_no_breakeven_before_tp1(self):
        s = run(fresh(), mark=102.0, quantity=1.0)
        self.assertIsNone(s["proposed_stop"])

    def test_trail_only_tightens(self):
        s = fresh()
        s["atr"] = 1.0
        run(s, mark=104.0, quantity=0.3)  # +2R → iz süren stop 103
        self.assertEqual(s["phase"], "TRAIL")
        self.assertAlmostEqual(s["proposed_stop"], 103.0)
        run(s, mark=103.5, quantity=0.3)  # fiyat geri çekildi: stop gevşemez
        self.assertAlmostEqual(s["proposed_stop"], 103.0)
        run(s, mark=105.0, quantity=0.3)
        self.assertAlmostEqual(s["proposed_stop"], 104.0)

    def test_trail_short_mirror(self):
        s = fresh()
        s["atr"] = 1.0
        run(s, direction="SHORT", stop=102.0, mark=96.0, quantity=0.3)
        self.assertAlmostEqual(s["proposed_stop"], 97.0)


class VirtualExitTests(unittest.TestCase):
    def test_shadow_stop_hit_records_and_freezes(self):
        s = run(fresh(), mark=102.0, quantity=0.3)
        run(s, mark=100.1, quantity=0.3)  # break-even seviyesinin altına indi
        self.assertEqual(s["virtual_exit"]["reason"], "SHADOW_STOP")
        frozen = dict(s)
        run(s, mark=110.0, quantity=0.3)
        self.assertEqual(s["virtual_exit"], frozen["virtual_exit"])

    def test_structure_exit_only_for_new_model_and_in_profit(self):
        falling = trend("SHORT", 80, start=120.0, step=0.15)
        mark = falling[-1]["close"]
        # LONG pozisyon, yapı bozuk ama giriş fiyatının altındayız → zarardayken sanal çıkış yok
        s = run(fresh(), entry=mark + 1, stop=mark - 1, mark=mark, candles=falling)
        self.assertIsNone(s["virtual_exit"])
        # Kârdayız ve yapı bozuk → STRUCTURE
        s = run(fresh(), entry=mark - 2, stop=mark - 4, mark=mark, candles=falling)
        self.assertEqual(s["virtual_exit"]["reason"], "STRUCTURE")
        # Eski model: yapı kuralı uygulanmaz
        s = run(fresh("legacy"), entry=mark - 2, stop=mark - 4, mark=mark, candles=falling)
        self.assertIsNone(s["virtual_exit"])

    def test_intact_structure_does_not_exit(self):
        rising = trend("LONG", 80)
        mark = rising[-1]["close"]
        s = run(fresh(), entry=mark - 2, stop=mark - 4, mark=mark, candles=rising)
        self.assertIsNone(s["virtual_exit"])

    def test_short_data_never_counts_as_broken(self):
        self.assertEqual(shadow_mod.structure_broken(trend("SHORT", 30), "LONG", 2), (False, None))

    def test_time_exit_requires_profit_and_no_progress(self):
        later = START + 7 * 3600
        s = run(fresh(), mark=100.5, now=later)  # +0,25R, net kârda
        self.assertEqual(s["virtual_exit"]["reason"], "TIME")
        s = run(fresh(), mark=99.5, now=later)  # zararda: çıkış yok
        self.assertIsNone(s["virtual_exit"])

    def test_bad_data_is_ignored(self):
        s = run(fresh(), stop=100.0)
        self.assertEqual(s["last_error"], "veri_gecersiz")
        self.assertIsNone(s["virtual_exit"])


class FlagAndTickTests(unittest.TestCase):
    def test_flag_default_off(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(shadow_mod.ENV, None)
            self.assertFalse(shadow_mod.enabled())
        for value, expected in (("1", True), ("on", True), ("0", False), ("", False)):
            with patch.dict(os.environ, {shadow_mod.ENV: value}):
                self.assertEqual(shadow_mod.enabled(), expected)

    def test_tick_disabled_touches_nothing(self):
        state = {"plans": {"p": {"symbol": "BTCUSDT"}}}
        with patch.dict(os.environ, {shadow_mod.ENV: "0"}):
            asyncio.run(shadow_mod.tick(state, {"positions": []}, None))
        self.assertNotIn(shadow_mod.SHADOW_KEY, state["plans"]["p"])

    def test_tick_records_and_never_calls_the_exchange(self):
        """Gölge mod: istemci üzerindeki HİÇBİR yöntem çağrılmaz (emir yok); yalnız plan kaydı güncellenir."""
        import sys
        plan = {"id": "p", "symbol": "BTCUSDT", "direction": "LONG", "status": "KORUMA AKTİF", "entry_price": "100",
                "initial_stop_loss": "98", "initial_quantity": "1", "original_cost_bps": 16,
                "original_record": {"entry": {"entry_model": shadow_mod.PULLBACK_MODEL}}}
        state = {"plans": {"p": plan}}
        snapshot = {"positions": [{"symbol": "BTCUSDT", "positionAmt": "0.3", "entry_price": "100", "mark_price": "102"}]}
        live = SimpleNamespace(live_plan_is_active=lambda p: True, persist_state=Mock())
        original = SimpleNamespace(plan_is_original=lambda p: True)
        guard = SimpleNamespace(view_of=_view_of)
        client = Mock()
        fetch = AsyncMock(return_value=trend("LONG", 80))
        with patch.dict(os.environ, {shadow_mod.ENV: "1"}), \
                patch.dict(sys.modules, {"app.v25_execution": live, "app.original_v2_live": original,
                                         "app.original_profit_guard": guard}):
            import app
            with patch.object(app, "v25_execution", live, create=True), patch.object(app, "original_v2_live", original, create=True), \
                    patch.object(app, "original_profit_guard", guard, create=True):
                asyncio.run(shadow_mod.tick(state, snapshot, client, now=START, candle_fetch=fetch))
        recorded = plan[shadow_mod.SHADOW_KEY]
        self.assertEqual(recorded["phase"], "BREAKEVEN")
        self.assertEqual(recorded["orders_sent"], 0)
        self.assertEqual(client.method_calls, [])
        live.persist_state.assert_called_once()


def _view_of(row):
    from decimal import Decimal
    amount = Decimal(str(row["positionAmt"]))
    return {"symbol": row["symbol"], "direction": "LONG" if amount > 0 else "SHORT", "quantity": abs(amount),
            "entry": Decimal(str(row["entry_price"])), "mark": Decimal(str(row["mark_price"]))}


if __name__ == "__main__":
    unittest.main()
