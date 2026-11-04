"""Hesap seviyesi sıkı risk bütçesi kapısı — AĞ YOK. Bayrak KAPALIyken eski davranış, AÇIKken tek işlem dahil toplam risk denetlenir."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import account_reservations as ar
from app import v25_execution as live
import test_account_reservations as reservation_tests

ON = {live.ACCOUNT_RISK_BUDGET_FLAG: "1"}


def pos(symbol, direction="LONG", qty=1.0, entry=100.0, mark=100.0, leverage=10, margin_type="isolated"):
    return {"symbol": symbol, "direction": direction, "quantity": qty, "entry_price": entry, "mark_price": mark, "leverage": leverage,
            "margin_type": margin_type, "positionAmt": qty if direction == "LONG" else -qty}


def plan(symbol, direction, stop, status="KORUMA AKTİF"):
    return {"symbol": symbol, "direction": direction, "stop_loss": str(stop), "status": status}


class OpenPositionRisk(unittest.TestCase):
    def risk(self, positions, plans=(), skip=()):
        state = {"plans": {str(i): p for i, p in enumerate(plans)}}
        return live.open_position_risk_usdt(state, {"positions": positions}, set(skip))

    def test_distance_to_stop_for_long_and_short(self):
        total, detail = self.risk([pos("ETHUSDT", qty=2, entry=100), pos("SOLUSDT", "SHORT", qty=3, entry=50)],
                                  [plan("ETHUSDT", "LONG", 97), plan("SOLUSDT", "SHORT", 52)])
        self.assertEqual(total, 12.0)                                            # 2*3 + 3*2
        self.assertEqual({d["basis"] for d in detail}, {"STOP"})

    def test_stop_beyond_entry_locks_profit_but_gives_no_credit(self):
        total, _ = self.risk([pos("ETHUSDT", entry=100)], [plan("ETHUSDT", "LONG", 105)])
        self.assertEqual(total, 0.0)

    def test_grid_coins_are_skipped_so_they_are_never_counted_twice(self):
        total, detail = self.risk([pos("ETHUSDT"), pos("SOLUSDT", entry=50)], [plan("SOLUSDT", "LONG", 48)], skip={"ETHUSDT"})
        self.assertEqual((total, [d["symbol"] for d in detail]), (2.0, ["SOLUSDT"]))

    def test_unmanaged_position_falls_back_to_isolated_margin_cap(self):
        total, detail = self.risk([pos("ETHUSDT", qty=2, mark=100, leverage=10)])
        self.assertEqual((total, detail[0]["basis"]), (20.0, "ISOLATED_MARGIN"))

    def test_unknown_risk_fails_closed(self):
        for bad in (pos("ETHUSDT", margin_type="cross"), {**pos("ETHUSDT"), "leverage": None},
                    {**pos("ETHUSDT"), "entry_price": "x"}, {**pos("ETHUSDT"), "quantity": "x"}):
            with self.subTest(bad=bad), self.assertRaises(live.AccountRiskUnknown):
                self.risk([bad])

    def test_price_beyond_the_stop_counts_the_real_unrealized_loss(self):
        beyond = {**pos("ETHUSDT", qty=1, entry=100, mark=95), "unrealized_pnl": -5.0}
        total, detail = self.risk([beyond], [plan("ETHUSDT", "LONG", 99)])
        self.assertEqual((total, detail[0]["basis"]), (5.0, "STOP+UNREALIZED"))
        inside = {**pos("ETHUSDT", qty=1, entry=100, mark=99.5), "unrealized_pnl": -0.5}      # stop mesafesi daha büyük → o kullanılır
        self.assertEqual(self.risk([inside], [plan("ETHUSDT", "LONG", 99)])[0], 1.0)
        profit = {**pos("ETHUSDT", qty=1, entry=100, mark=110), "unrealized_pnl": 10.0}       # kâr riski düşürmez
        self.assertEqual(self.risk([profit], [plan("ETHUSDT", "LONG", 99)])[0], 1.0)

    def test_zero_quantity_is_ignored(self):
        self.assertEqual(self.risk([pos("ETHUSDT", qty=0, margin_type="cross")])[0], 0.0)


class BudgetComposition(unittest.TestCase):
    def budget(self, env=None, realized=0.0, limit=100.0, grid=0.0, grid_symbols=(), positions=(), plans=(), snapshot=True):
        runner = SimpleNamespace(reserved_risk=lambda uid, owner=True: {"worst_loss_usdt": grid, "symbols": list(grid_symbols)})
        app = SimpleNamespace(state=SimpleNamespace(live_grid_runner=runner))
        state = {"policy": {"daily_loss_limit": limit}, "events": [], "plans": {str(i): p for i, p in enumerate(plans)}}
        with patch.dict(os.environ, env or {}, clear=False), patch.object(live, "live_daily_metrics", lambda s: {"realized_pnl": realized}):
            return live.shared_daily_budget(app, state, None, {"positions": list(positions)} if snapshot else None)

    def test_flag_off_keeps_the_previous_behaviour(self):
        os.environ.pop(live.ACCOUNT_RISK_BUDGET_FLAG, None)
        got = self.budget(realized=-2.0, grid=3.0, positions=[pos("ETHUSDT")])
        self.assertEqual((got["budget_left"], got["committed"], "strict" in got), (98.0, 3.0, False))

    def test_flag_on_adds_open_risk_and_uses_the_users_own_limit_without_a_fixed_cap(self):
        got = self.budget(ON, realized=-12.0, limit=250.0, grid=5.0, positions=[pos("ETHUSDT", qty=2)], plans=[plan("ETHUSDT", "LONG", 97)])
        self.assertTrue(got["strict"])
        self.assertEqual(got["budget_left"], 250.0 - 12.0 - 6.0)                  # limit 250 aynen; kalan = 250 - zarar - açık risk
        self.assertEqual(got["committed"], 5.0)
        self.assertEqual(got["breakdown"]["limit"], 250.0)

    def test_large_account_limit_is_not_clipped_to_100(self):
        got = self.budget(ON, realized=-4000.0, limit=100000.0)
        self.assertEqual((got["budget_left"], got["breakdown"]["limit"]), (96000.0, 100000.0))

    def test_profit_never_increases_the_budget(self):
        self.assertEqual(self.budget(ON, realized=40.0, limit=20.0)["budget_left"], 20.0)

    def test_grid_positions_are_not_counted_as_open_risk(self):
        got = self.budget(ON, grid=4.0, grid_symbols=["ETHUSDT"], positions=[pos("ETHUSDT", qty=2)], plans=[plan("ETHUSDT", "LONG", 90)])
        self.assertEqual((got["budget_left"], got["committed"]), (100.0, 4.0))

    def test_missing_data_fails_closed_when_enabled(self):
        with self.assertRaises(live.AccountRiskUnknown):
            self.budget(ON, snapshot=False)


class GridStartRisk(unittest.TestCase):
    def call(self, env, positions, plans=(), grid_symbols=()):
        state = {"plans": {str(i): p for i, p in enumerate(plans)}}
        with patch.dict(os.environ, env, clear=False):
            return live.grid_start_account_open_risk(state, {"positions": positions}, grid_symbols)

    def test_flag_off_adds_nothing(self):
        os.environ.pop(live.ACCOUNT_RISK_BUDGET_FLAG, None)
        self.assertEqual(self.call({}, [pos("ETHUSDT", margin_type="cross")]), 0.0)

    def test_flag_on_adds_non_grid_open_risk_only(self):
        positions = [pos("ETHUSDT", qty=2), pos("SOLUSDT", entry=50)]
        plans = [plan("ETHUSDT", "LONG", 97), plan("SOLUSDT", "LONG", 48)]
        self.assertEqual(self.call(ON, positions, plans), 8.0)
        self.assertEqual(self.call(ON, positions, plans, grid_symbols=["ETHUSDT"]), 2.0)

    def test_unknown_risk_refuses_the_grid_start_with_503(self):
        with self.assertRaises(live.HTTPException) as ctx:
            self.call(ON, [pos("ETHUSDT", margin_type="cross")])
        self.assertEqual(ctx.exception.status_code, 503)


class GridStartCallSite(unittest.TestCase):
    """GERÇEK /grid/live/start uç noktası (önizleme modu: marjin/kaldıraç ayarı, emir ve kayıt YOK): çağrı noktası açık pozisyon
    riskini gerçekten hesaplayıp Grid planının günlük hakkından düşüyor mu?"""

    def start(self, env, positions, plans=None, limit=10.0, grid_rows=()):
        import asyncio
        import test_v25_auto_symbol_scope as scope_tests
        from app import grid_live
        from app.grid_engine.store import GridStore
        application, state = scope_tests.application_for(["BTCUSDT"])
        state["policy"].update({"max_leverage": 10, "max_loss_per_trade": 3.0, "daily_loss_limit": limit, "max_total_exposure_usdt": 350.0,
                                "max_positions": 5, "max_stop_distance_pct": 5.0})
        state["plans"] = plans or {}
        application.state.live_grid_runner = grid_live.LiveGridRunner(GridStore(":memory:", check_same_thread=False))
        snapshot = {"wallet_balance": 100, "available_balance": 100, "positions": positions, "open_orders": [], "open_algo_orders": [],
                    "open_algo_orders_available": True, "algo_orders_quality": "VALID_EMPTY", "hedge_mode": False, "multi_assets_mode": False}
        body = live.GridLiveStartRequest(symbol="BTCUSDT", direction="LONG", lower=98.0, upper=99.0, count=2, margin_usdt=10.0, leverage=2,
                                         stop=97.0, preview=True)
        request = SimpleNamespace(app=application, state=SimpleNamespace(member={"id": "u1"}))
        rules = {"tick": 0.1, "step": 0.001, "min_qty": 0.001, "min_notional": 5.0, "max_qty": 1000}
        with patch.dict(os.environ, env, clear=False), patch.object(live, "execution_owner", lambda r: {"id": "u1", "role": "OWNER"}), \
                patch.object(live, "client_for", lambda *a, **k: SimpleNamespace()), \
                patch.object(live, "account_snapshot", AsyncMock(return_value=snapshot)), \
                patch.object(live, "live_symbol_rules", AsyncMock(return_value=rules)), \
                patch.object(live, "ticker_price", AsyncMock(return_value=100.0)), \
                patch.object(live, "validate_live_isolated_snapshot", lambda *a, **k: None):
            try:
                return asyncio.run(live._grid_live_start(request, body, None))
            except live.HTTPException as exc:
                return exc

    def test_flag_off_the_preview_plan_ignores_open_positions(self):
        os.environ.pop(live.ACCOUNT_RISK_BUDGET_FLAG, None)
        result = self.start({}, [pos("ETHUSDT", qty=2)], {"p": plan("ETHUSDT", "LONG", 90)})     # açık risk 20 USDT, limit 10
        self.assertTrue(isinstance(result, dict) and result.get("preview"), result)

    def test_flag_on_open_position_risk_is_taken_from_the_grid_daily_allowance(self):
        result = self.start(ON, [pos("ETHUSDT", qty=2)], {"p": plan("ETHUSDT", "LONG", 90)})
        self.assertIsInstance(result, live.HTTPException)
        self.assertIn("kalan zarar hakkı", str(result.detail))

    def test_flag_on_small_open_risk_still_leaves_room(self):
        result = self.start(ON, [pos("ETHUSDT", qty=1)], {"p": plan("ETHUSDT", "LONG", 99.9)})   # açık risk 0.1
        self.assertTrue(isinstance(result, dict) and result.get("preview"), result)

    def test_flag_on_unreadable_open_position_refuses_with_503(self):
        result = self.start(ON, [pos("ETHUSDT", margin_type="cross")])
        self.assertEqual((result.status_code), 503)


class EntryGate(unittest.TestCase):
    """execute_live_order: spec riski 2 USDT (estimated_stop_loss_usdt)."""
    h = reservation_tests.ManualEntry("test_concurrent_entry_on_same_coin_is_refused_before_submit")

    def run_entry(self, env=None, **kwargs):
        with patch.dict(os.environ, env or {}, clear=False):
            return self.h._execute(**kwargs)

    def setUp(self):
        os.environ.pop(live.ACCOUNT_RISK_BUDGET_FLAG, None)

    def test_flag_off_a_lone_trade_passes_exactly_as_before(self):
        result, submit, *_ = self.run_entry(daily_limit=1.0)                       # risk 2 > limit 1 ama bayrak kapalı
        self.assertEqual(submit.await_count, 1)

    def test_flag_on_a_lone_trade_cannot_exceed_the_budget(self):
        result, submit, state, owner, _ = self.run_entry(ON, daily_limit=1.0)
        self.assertEqual((result.status_code, submit.await_count), (409, 0))
        self.assertIn("Hesap risk bütçesi yetmiyor", result.detail)
        self.assertEqual(ar.snapshot(owner), [])                                   # reddedilen giriş satır bırakmaz

    def test_flag_on_fits_when_budget_is_enough(self):
        result, submit, *_ = self.run_entry(ON, daily_limit=10.0)
        self.assertEqual(submit.await_count, 1)

    def test_all_terms_are_added_together(self):
        # limit 10: bugünkü zarar 3 + açık pozisyon 3 + Grid 1 = 7; yeni 2 sığar (9). Zarar 5 olursa 11 → sığmaz (10 sınırda sığar).
        runner = SimpleNamespace(reserved_risk=lambda uid, owner=True: {"worst_loss_usdt": 1.0, "symbols": []}, symbol_busy=lambda *a, **k: False)
        kw = dict(daily_limit=10.0, positions=[pos("ETHUSDT", qty=1, entry=100)], plans={"p": plan("ETHUSDT", "LONG", 97)}, runner=runner)
        ok = self.run_entry(ON, ledger_net=-3.0, **kw)
        self.assertEqual(ok[1].await_count, 1)
        no = self.run_entry(ON, ledger_net=-5.0, **kw)
        self.assertEqual((no[0].status_code, no[1].await_count), (409, 0))

    def test_no_fixed_hundred_usdt_ceiling_the_users_own_limit_decides(self):
        ok = self.run_entry(ON, daily_limit=250.0, ledger_net=-148.5)              # 148.5 + 2 = 150.5 ≤ 250 → eskiden 100'de dururdu
        self.assertEqual(ok[1].await_count, 1)
        edge = self.run_entry(ON, daily_limit=250.0, ledger_net=-248.0)            # 248 + 2 = 250 → sınırda sığar
        self.assertEqual(edge[1].await_count, 1)
        no = self.run_entry(ON, daily_limit=250.0, ledger_net=-248.5)              # 248.5 + 2 > 250 → kullanıcının kendi limiti reddeder
        self.assertEqual((no[0].status_code, no[1].await_count), (409, 0))

    def test_grid_position_is_not_counted_twice_end_to_end(self):
        runner = SimpleNamespace(reserved_risk=lambda uid, owner=True: {"worst_loss_usdt": 4.0, "symbols": ["ETHUSDT"]}, symbol_busy=lambda *a, **k: False)
        kw = dict(daily_limit=10.0, positions=[pos("ETHUSDT", qty=1, entry=100)], plans={"p": plan("ETHUSDT", "LONG", 90)}, runner=runner)
        self.assertEqual(self.run_entry(ON, **kw)[1].await_count, 1)               # 4 (Grid) + 2 = 6; pozisyonun 10'u ayrıca sayılsaydı reddedilirdi

    def test_profit_does_not_buy_extra_room_end_to_end(self):
        self.assertEqual(self.run_entry(ON, daily_limit=1.0, ledger_net=500.0)[0].status_code, 409)

    def test_concurrent_reservation_counts_too(self):
        result, submit, state, owner, _ = self.run_entry(ON, daily_limit=10.0, seed=("AUTO", "SOLUSDT", 9.0))
        self.assertEqual((result.status_code, submit.await_count), (409, 0))

    def test_unreadable_open_position_fails_closed(self):
        bad = pos("ETHUSDT", margin_type="cross")
        result, submit, *_ = self.run_entry(ON, daily_limit=50.0, positions=[bad])
        self.assertEqual((result.status_code, submit.await_count), (503, 0))

    def test_unreadable_grid_risk_fails_closed(self):
        def boom(uid, owner=True):
            raise RuntimeError("defter")
        result, submit, *_ = self.run_entry(ON, daily_limit=50.0, runner=SimpleNamespace(reserved_risk=boom, symbol_busy=lambda *a, **k: False))
        self.assertEqual((result.status_code, submit.await_count), (503, 0))

    def test_manual_and_automatic_entries_hit_the_gate_end_to_end(self):
        for source in ("MANUAL", "V25_AUTO"):
            with self.subTest(source=source):
                result, submit, state, owner, _ = self.run_entry(ON, daily_limit=1.0, source=source)
                self.assertEqual((result.status_code, submit.await_count), (409, 0))
                self.assertIn("Hesap risk bütçesi yetmiyor", result.detail)

    def original_v2_entry(self, limit, env=ON, **kwargs):
        """Original V2 / KAIS'in GERÇEK giriş akışı (execute_live_order'ın original_entry dalları: sinyal ve mum doğrulaması, politika,
        Original risk kapıları, plan alanları) çalışır. Yalnız Binance'e dokunan/strateji hesaplayan üç parça yerine konur:
        check_entry (daily_guard Binance gelir okur), execution_policy ve build_spec (Original boyutlandırma çıktısı sabit verilir)."""
        import time as _time
        import test_v25_auto_symbol_scope as scope_tests
        from app import original_v2_live
        strategy_result = SimpleNamespace(signal=SimpleNamespace(symbol="BTCUSDT", decision_time=int(_time.time()) // 900 * 900))
        spec = {**scope_tests.spec_for("BTCUSDT"), "quantity": "2.5", "notional_usdt": 250, "margin_usdt": 5, "leverage": 50,
                "stop_loss": "99.2", "estimated_stop_loss_usdt": 2, "initial_stop_loss": "99.2", "initial_quantity": "2.5",
                "original_risk_budget": "2", "original_cost_bps": "0"}
        original_patches = {"check_entry": AsyncMock(return_value=None), "build_spec": AsyncMock(return_value=spec),
                            "execution_policy": lambda state, snap: {**state["policy"], "allowed_symbols": ["BTCUSDT"]}}
        gate_signal = {"direction": "LONG", "confidence": 100, "radar": {"trap_score": 0}}
        with patch.object(live.original_v2_pullback, "gate_signal", lambda r: gate_signal):
            return self.run_entry(env, daily_limit=limit, source=original_v2_live.SOURCE, strategy_result=strategy_result,
                                  original_patches=original_patches, **kwargs)

    def test_original_v2_real_entry_flow_is_stopped_by_the_gate_before_any_order(self):
        result, submit, state, owner, _ = self.original_v2_entry(1.0)
        self.assertEqual((result.status_code, submit.await_count), (409, 0))
        self.assertIn("Hesap risk bütçesi yetmiyor", result.detail)
        self.assertEqual(ar.snapshot(owner), [])

    def test_original_v2_real_entry_flow_passes_when_budget_allows_and_flag_off_is_unchanged(self):
        ok, submit, state, owner, _ = self.original_v2_entry(10.0)
        self.assertEqual(submit.await_count, 1)
        self.assertEqual(next(iter(state["plans"].values()))["source"], "ORIGINAL_V2_LIVE")
        _, submit_off, *_ = self.original_v2_entry(1.0, env={})                        # bayrak kapalı: aynı limitte emir gider
        self.assertEqual(submit_off.await_count, 1)

    def test_original_v2_sees_manual_open_risk_and_grid_risk(self):
        runner = SimpleNamespace(reserved_risk=lambda uid, owner=True: {"worst_loss_usdt": 6.0, "symbols": []}, symbol_busy=lambda *a, **k: False)
        result, submit, *_ = self.original_v2_entry(7.0, runner=runner, positions=[pos("ETHUSDT")], plans={"p": plan("ETHUSDT", "LONG", 99)})
        self.assertEqual((result.status_code, submit.await_count), (409, 0))           # 6 + 1 + 2 > 7

    def test_gate_is_not_conditional_on_the_entry_source(self):
        """Ek güvence: kapı çağrısı hiçbir source/original_entry koşuluna bağlı değil (kaynak ağacı)."""
        import ast
        tree = ast.parse(Path(live.__file__).read_text(encoding="utf-8"))
        func = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "execute_live_order")
        parents = {child: node for node in ast.walk(func) for child in ast.iter_child_nodes(node)}
        calls = [n for n in ast.walk(func) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "reserve"
                 and getattr(getattr(n.func, "value", None), "id", "") == "account_reservations"]
        self.assertEqual(len(calls), 1)
        node, guards = calls[0], []
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.If):
                guards.append(ast.unparse(node.test))
        self.assertFalse([g for g in guards if any(word in g for word in ("source", "original_entry", "automatic_entry", "MANUAL"))], guards)

    def test_rollback_flag_off_restores_previous_behaviour_without_code_change(self):
        self.assertEqual(self.run_entry(ON, daily_limit=1.0)[1].await_count, 0)
        self.assertEqual(self.run_entry({live.ACCOUNT_RISK_BUDGET_FLAG: "0"}, daily_limit=1.0)[1].await_count, 1)


if __name__ == "__main__":
    unittest.main()
