"""Hesap geneli günlük zarar defteri — AĞ YOK. Binance gelir geçmişi (REALIZED_PNL + COMMISSION + FUNDING_FEE) tek kaynak."""
from __future__ import annotations

import asyncio
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from app import daily_ledger as dl
from app import grid_live
from app.grid_engine.engine import PAUSED, RUNNING
from app.grid_engine.store import GridStore

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
START, END = dl.day_start_ms(NOW), int(NOW.timestamp() * 1000)


def row(kind, amount, tran, symbol="BTCUSDT", at=None, asset="USDT"):
    return {"asset": asset, "incomeType": kind, "tranId": tran, "time": (at or START + 1000), "income": str(amount), "symbol": symbol}


class FakeClient:
    """Binance'in 2026-10-15 sonrası davranışı: `page` yok sayılır; yalnız startTime/endTime penceresindeki ilk `limit` satır döner."""

    def __init__(self, rows=None, fail=False):
        self.rows, self.fail, self.calls = rows or [], fail, []

    async def signed(self, method, path, params=None):
        self.calls.append((method, path, dict(params or {})))
        if self.fail:
            raise RuntimeError("boom")
        window = [r for r in self.rows if params["startTime"] <= r["time"] <= params["endTime"]]
        return window[:int(params["limit"])]


class LedgerBuild(unittest.TestCase):
    def test_all_income_types_count_and_transfers_do_not(self):
        rows = [row("REALIZED_PNL", -6, 1), row("COMMISSION", -0.4, 2), row("FUNDING_FEE", -0.2, 3, "ETHUSDT"),
                row("REALIZED_PNL", 1.5, 4, "ETHUSDT"), row("TRANSFER", 500, 5, ""), row("INTERNAL_TRANSFER", -90, 6, "")]
        t = dl.build(rows, START, END)["totals"]
        self.assertEqual((t["realized_pnl"], t["commission"], t["funding"], t["net"]), (-4.5, -0.4, -0.2, -5.1))

    def test_unverifiable_rows_are_refused_not_guessed(self):
        for bad in ([row("REALIZED_PNL", -1, 1), row("REALIZED_PNL", -1, 1)],          # çift satır
                    [row("REALIZED_PNL", -1, 1, asset="BNB")],                           # USDT dışı
                    [row("REALIZED_PNL", -1, 1, at=START - 5)],                          # gün dışı
                    [row("REALIZED_PNL", "x", 1)], [{"asset": "USDT"}], ["nope"]):
            with self.assertRaises(dl.LedgerUnavailable):
                dl.build(bad, START, END)

    def test_window_totals_isolate_symbol_and_period(self):
        ledger = dl.build([row("REALIZED_PNL", -3, 1, at=START + 10), row("COMMISSION", -.1, 2, at=START + 11),
                           row("REALIZED_PNL", -9, 3, "ETHUSDT", at=START + 12), row("REALIZED_PNL", -7, 4, at=START + 900)], START, END)
        self.assertEqual(dl.window_totals(ledger, "btcusdt", START, START + 100)["net"], -3.1)


class LedgerFetch(unittest.TestCase):
    def test_over_1000_rows_are_read_completely_without_page_param(self):
        # Eski hata: gün içinde 1000+ satır olunca sayfa parametresi yok sayılıp aynı 1000 satır tekrar okunuyor, defter kapanıyordu.
        rows_in = [row("REALIZED_PNL", -.001, i, at=START + 1000 + i * 5000) for i in range(5000)]
        client = FakeClient(rows_in)
        rows, s, e = asyncio.run(dl.fetch(client, NOW))
        self.assertEqual((len(rows), s), (5000, START))
        self.assertEqual(len({(r["incomeType"], r["tranId"]) for r in rows}), 5000)      # çakışma / tekrar yok
        self.assertTrue(all(c[1] == "/fapi/v1/income" and "page" not in c[2] and c[2]["limit"] == 1000 for c in client.calls))
        self.assertLess(len(client.calls), dl.MAX_REQUESTS)
        ledger = dl.build(rows, s, e)                                                    # defter kurulur (kapı kapanmaz)
        self.assertEqual(ledger["totals"]["realized_pnl"], -5.0)

    def test_exactly_1000_rows_is_not_mistaken_for_complete(self):
        rows_in = [row("REALIZED_PNL", -1, i, at=START + 1000 + i) for i in range(1000)] + [row("COMMISSION", -1, 5000, at=START + 9000)]
        rows, _s, _e = asyncio.run(dl.fetch(FakeClient(rows_in), NOW))
        self.assertEqual(len(rows), 1001)

    def test_failures_still_fail_closed(self):
        with self.assertRaises(dl.LedgerUnavailable):
            asyncio.run(dl.fetch(FakeClient(fail=True), NOW))
        # tek milisaniyede 1000+ satır: bölünemez → eksik hesap yapılmaz
        same_instant = [row("REALIZED_PNL", -.001, i, at=START + 1000) for i in range(1001)]
        with self.assertRaises(dl.LedgerUnavailable):
            asyncio.run(dl.fetch(FakeClient(same_instant), NOW))

    def test_request_cap_fails_closed(self):
        rows_in = [row("REALIZED_PNL", -.001, i, at=START + 1000 + i * 100) for i in range(30000)]
        with self.assertRaises(dl.LedgerUnavailable):
            asyncio.run(dl.fetch(FakeClient(rows_in), NOW))

    def test_refresh_uses_cache_then_stale_then_fails_closed(self):
        state = {}
        ok = FakeClient([row("REALIZED_PNL", -2, 1, at=int(time.time() * 1000) - 1)])
        first = asyncio.run(dl.refresh(ok, state))
        self.assertEqual(first["totals"]["net"], -2)
        asyncio.run(dl.refresh(ok, state))
        self.assertEqual(len(ok.calls), 1)                                              # taze defter: ikinci okuma yok
        state[dl.KEY]["fetched_at"] -= 100                                              # eski ama kabul edilebilir
        stale = asyncio.run(dl.refresh(FakeClient(fail=True), state))
        self.assertTrue(stale["stale"])
        state[dl.KEY]["fetched_at"] -= 1000                                             # çok eski
        with self.assertRaises(dl.LedgerUnavailable):
            asyncio.run(dl.refresh(FakeClient(fail=True), state))
        with self.assertRaises(dl.LedgerUnavailable):
            asyncio.run(dl.refresh(FakeClient(fail=True), {}))

    def test_yesterdays_ledger_is_never_reused(self):
        state = {dl.KEY: {"day": "2000-01-01", "fetched_at": time.time(), "totals": {"net": -99}}}
        self.assertIsNone(dl.cached(state))


class Metrics(unittest.TestCase):
    def setUp(self):
        from app import v25_execution as v25
        self.v25 = v25

    def _state(self, ledger_net=None):
        day = datetime.now(timezone.utc).date().isoformat()
        state = {"events": [{"kind": "LIVE_POSITION_CLOSED", "created_at": day + "T01:00:00+00:00", "realized_pnl": -1.0, "plan_id": "p"}], "plans": {}}
        if ledger_net is not None:
            state[dl.KEY] = {"day": day, "fetched_at": time.time(), "totals": {"net": ledger_net, "realized_pnl": ledger_net, "commission": 0,
                                                                              "funding": 0, "other": 0}, "rows": [], "source": "BINANCE_INCOME"}
        return state

    def test_ledger_replaces_event_only_figure(self):
        self.assertEqual(self.v25.live_daily_metrics(self._state())["realized_pnl"], -1.0)
        m = self.v25.live_daily_metrics(self._state(-12.34))
        self.assertEqual((m["realized_pnl"], m["pnl_source"], m["event_realized_pnl"]), (-12.34, "BINANCE_INCOME", -1.0))

    def test_unreadable_ledger_blocks_entries_with_503(self):
        with self.assertRaises(self.v25.LiveExchangeError) as ctx:
            asyncio.run(self.v25.refresh_account_daily_ledger(FakeClient(fail=True), {}))
        self.assertEqual((ctx.exception.http_status, ctx.exception.check_key), (503, "daily_ledger"))

    def test_daily_loss_gate_trips_on_combined_manual_grid_kais(self):
        # manuel -4, grid stop -5, KAIS -2 + komisyon/fonlama -1.5 → -12.5; limit 10
        ledger = dl.build([row("REALIZED_PNL", -4, 1, "SOLUSDT"), row("REALIZED_PNL", -5, 2, "BTCUSDT"), row("REALIZED_PNL", -2, 3, "ETHUSDT"),
                           row("COMMISSION", -1, 4), row("FUNDING_FEE", -.5, 5)], START, END, fetched_at=time.time())
        ledger["day"] = datetime.now(timezone.utc).date().isoformat()
        state = {"events": [], "plans": {}, dl.KEY: ledger}
        daily = self.v25.live_daily_metrics(state)
        self.assertEqual(daily["realized_pnl"], -12.5)
        self.assertFalse(daily["realized_pnl"] > -10.0)                                   # execution_core kapısı: daily_loss


class GridRunner(unittest.TestCase):
    def _runner(self, ledger_net=None, grid_reached=False, failed_for=None):
        runner = grid_live.LiveGridRunner(GridStore(":memory:", check_same_thread=False))
        events = []
        runner._record = lambda state, kind, message, **extra: events.append(kind)
        runner.store = SimpleNamespace(grid=lambda gid: {"user_id": "u1"}, set_grid=lambda gid, **kw: None)
        runner.day_summary = lambda *a, **k: {"reached": grid_reached, "realized_usdt": 0.0}
        day = datetime.now(timezone.utc).date().isoformat()
        state = {"policy": {"daily_loss_limit": 10.0}}
        if ledger_net is not None:
            state[dl.KEY] = {"day": day, "fetched_at": time.time(), "totals": {"net": ledger_net}}
        engine = SimpleNamespace(gid="g1", sym="BTCUSDT", status=RUNNING, pause=lambda: setattr(engine, "status", PAUSED))
        if failed_for is not None:
            runner._ledger_failed_since["g1"] = time.monotonic() - failed_for
        return runner, engine, state, events

    def test_account_wide_loss_pauses_even_when_grid_alone_is_fine(self):
        runner, engine, state, events = self._runner(ledger_net=-10.5)
        self.assertEqual(runner._check_daily_limit(engine, state), "DAILY_LIMIT")
        self.assertEqual((engine.status, events), (PAUSED, ["LIVE_GRID_DAILY_LIMIT"]))

    def test_below_limit_and_profit_keep_running(self):
        for net in (-9.99, 0.0, 25.0):
            runner, engine, state, _ = self._runner(ledger_net=net)
            self.assertEqual((runner._check_daily_limit(engine, state), engine.status), ("", RUNNING))

    def test_grid_only_limit_still_applies(self):
        runner, engine, state, _ = self._runner(ledger_net=-1.0, grid_reached=True)
        self.assertEqual(runner._check_daily_limit(engine, state), "DAILY_LIMIT")

    def test_ledger_outage_is_tolerated_briefly_then_fails_closed(self):
        runner, engine, state, _ = self._runner(failed_for=30)
        self.assertEqual(runner._check_daily_limit(engine, state), "")
        runner, engine, state, _ = self._runner(failed_for=dl.STALE_OK_SECONDS + 5)
        self.assertEqual((runner._check_daily_limit(engine, state), engine.status), ("DAILY_LIMIT", PAUSED))

    def test_refresh_clears_failure_and_records_it(self):
        runner, engine, state, _ = self._runner()
        runner._adapters["g1"] = SimpleNamespace(client=FakeClient(fail=True))
        asyncio.run(runner._refresh_daily_ledger(engine, state))
        self.assertIn("g1", runner._ledger_failed_since)
        now_ms = int(time.time() * 1000)
        runner._adapters["g1"] = SimpleNamespace(client=FakeClient([row("REALIZED_PNL", -1, 1, at=now_ms - 1)]))
        asyncio.run(runner._refresh_daily_ledger(engine, state))
        self.assertNotIn("g1", runner._ledger_failed_since)
        self.assertEqual(state[dl.KEY]["totals"]["net"], -1)


if __name__ == "__main__":
    unittest.main()
