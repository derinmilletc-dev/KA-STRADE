"""KAIS FARMER C8 — PnL + funding doğrulaması (sahte borsa/kaynak; ağ yok)."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.farmer_engine import orchestrator as o  # noqa: E402
from app.farmer_engine import pnl as p  # noqa: E402
from app.farmer_engine.engine import farmer_spec  # noqa: E402
from app.dca_engine.plan import TAKER_FEE  # noqa: E402
from tests.test_farmer_orchestrator import ACC, Rig  # noqa: E402

try:
    from app import v25_execution as V25
    SKIP = ""
except Exception as exc:                                              # noqa: BLE001
    V25, SKIP = None, f"v25_execution içe aktarılamadı: {exc.__class__.__name__}"


def iso_ms(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


class Source:
    """Yapılandırılabilir borsa kaynağı. Gelir satırı zamanları pozisyon girişine göre (offset ms)."""

    def __init__(self, gross=1.2, commission=0.2, funding=(), income_gross=None, commission_rows=None, **over):
        self.gross, self.commission = gross, commission
        self.income_gross = gross if income_gross is None else income_gross
        self.commission_rows = commission_rows if commission_rows is not None else [(-commission / 2, 1000, "c1"), (-commission / 2, 1500, "c2")]
        self.funding = list(funding)                                   # (tutar, offset, tranId[, symbol, asset])
        self.out_over, self.fail, self.foreign, self.calls, self.entry_ms = over, {}, [], [], 0

    def plan_pnl(self, plan):
        self.calls.append(("plan", plan))
        if "plan" in self.fail:
            raise p.PnlUnavailable(self.fail["plan"])
        self.entry_ms = int(datetime.fromisoformat(plan["created_at"]).timestamp() * 1000)
        out = {"gross_realized_pnl": self.gross, "commission_usdt": self.commission, "realized_pnl": 0, "trade_count": 2, "exit_price": 101.0,
               "opened_at": iso_ms(self.entry_ms), "non_usdt_commission_assets": [], "funding_included": False, "attribution": "ORDER_IDS"}
        out.update(self.out_over)
        return out

    def _rows(self, kind, items):
        out = []
        for it in items:
            amount, offset, tran = it[:3]
            out.append({"incomeType": kind, "tranId": tran, "time": str(self.entry_ms + offset), "income": str(amount),
                        "symbol": it[3] if len(it) > 3 else "AAAUSDT", "asset": it[4] if len(it) > 4 else "USDT"})
        return out

    def income_rows(self, symbol, kind, start, end):
        self.calls.append((kind, start, end))
        if kind in self.fail:
            raise p.PnlUnavailable(self.fail[kind])
        if kind == p.REALIZED:
            return self._rows(kind, [(self.income_gross, 1200, "r1")])
        if kind == p.COMMISSION:
            return self._rows(kind, self.commission_rows)
        return self._rows(kind, self.funding)

    def foreign_overlaps(self, symbol, start, end):
        return list(self.foreign)


def closed_rig(source, reason="TARGET", **rig_kw):
    """Bir pozisyon açıp kapatır; (rig, grid_id). Doğrulayıcı bu kaynakla kurulur."""
    r = Rig(table={"BBBUSDT": {"confidence": 50}, "CCCUSDT": {"confidence": 50}}, **rig_kw)
    r.orch = r.build(pnl=p.FarmerPnlVerifier(r.store, source, clock=r.clock))
    r.run()
    gid = next(iter(r.orch.engines))
    eng = r.orch.engines[gid]
    if reason == "TARGET":
        r.ex.set_price("AAAUSDT", eng.tp_price() + 0.5)
        r.tick()
    elif reason == "STOP":
        r.ex.set_price("AAAUSDT", eng.stop_target_price() - 0.5)
        r.tick()
    elif reason == "EMERGENCY":
        r.orch.emergency_close()
    return r, gid


class TestNetPnl(unittest.TestCase):
    def test_target_verified_with_paid_funding(self):
        src = Source(gross=1.20, commission=0.20, funding=[(-0.05, 2000, "f1")])
        r, gid = closed_rig(src)
        res = r.store.result(gid)
        self.assertEqual((res["close_reason"], res["pnl_state"]), ("TARGET", "VERIFIED"))
        self.assertAlmostEqual(res["gross_pnl"], 1.20)
        self.assertAlmostEqual(res["commission"], 0.20)
        self.assertAlmostEqual(res["funding"], -0.05)
        self.assertAlmostEqual(res["verified_net_pnl"], 0.95)         # 1.20 − 0.20 + (−0.05)
        self.assertNotEqual(res["verified_net_pnl"], res["target_usdt"])      # hedef zorla yazılmaz

    def test_stop_with_received_funding(self):
        src = Source(gross=-1.40, commission=0.15, funding=[(0.02, 2000, "f1")])
        r, gid = closed_rig(src, "STOP")
        res = r.store.result(gid)
        self.assertEqual((res["close_reason"], res["pnl_state"]), ("STOP", "VERIFIED"))
        self.assertAlmostEqual(res["verified_net_pnl"], -1.53)        # −1.40 − 0.15 + 0.02
        self.assertLess(res["verified_net_pnl"], -res["max_loss_usdt"])       # gerçekleşen zarar maks. zarar değerine ZORLANMAZ

    def test_actual_commission_not_estimate_and_not_added(self):
        src = Source(gross=1.20, commission=0.07)
        r, gid = closed_rig(src)
        res = r.store.result(gid)
        self.assertAlmostEqual(res["commission"], 0.07)
        self.assertNotAlmostEqual(res["commission"], res["est_cost_usdt"])
        self.assertAlmostEqual(res["verified_net_pnl"], 1.13)

    def test_no_funding_rows_is_zero_verified(self):
        r, gid = closed_rig(Source())
        res = r.store.result(gid)
        self.assertEqual((res["pnl_state"], res["funding"]), ("VERIFIED", 0.0))

    def test_emergency_close_reason(self):
        r, gid = closed_rig(Source(), "EMERGENCY")
        res = r.store.result(gid)
        self.assertEqual((res["close_reason"], res["pnl_state"]), ("EMERGENCY", "VERIFIED"))

    def test_dead_and_market_close_reasons_preserved(self):
        for reason in ("DEAD_CLOSE", "MARKET_CLOSE"):
            r, gid = closed_rig(Source(), "NONE")
            r.store.close_result(gid, reason)
            r.store.release_hold(gid)
            r.orch.engines.pop(gid)
            r.orch._verify_pnl(gid)
            res = r.store.result(gid)
            self.assertEqual((res["close_reason"], res["pnl_state"]), (reason, "VERIFIED"), reason)


class TestUnverified(unittest.TestCase):
    def check(self, src, code):
        r, gid = closed_rig(src)
        res = r.store.result(gid)
        self.assertEqual(res["pnl_state"], "UNVERIFIED", code)
        self.assertIn(code, res["verify_error"])
        self.assertTrue(all(res[k] is None for k in ("gross_pnl", "commission", "funding", "verified_net_pnl")))   # 0/tahmin YAZILMAZ
        return r, gid

    def test_missing_gross(self):
        self.check(Source(gross=None), "GROSS_MISSING")

    def test_missing_commission(self):
        self.check(Source(commission=None, commission_rows=[]), "COMMISSION_MISSING")

    def test_conflicting_gross_sources(self):
        self.check(Source(gross=1.2, income_gross=1.1), "GROSS_SOURCES_DISAGREE")

    def test_commission_sources_disagree_doubled_rows(self):
        self.check(Source(commission=0.2, commission_rows=[(-0.1, 1000, "c1"), (-0.1, 1100, "c2"), (-0.1, 1200, "c3"), (-0.1, 1300, "c4")]),
                   "COMMISSION_SOURCES_DISAGREE")

    def test_commission_row_missing(self):
        self.check(Source(commission=0.2, commission_rows=[(-0.1, 1000, "c1")]), "COMMISSION_SOURCES_DISAGREE")

    def test_non_usdt_commission(self):
        self.check(Source(non_usdt_commission_assets=["BNB"]), "COMMISSION_ASSET_NOT_USDT")

    def test_symbol_window_attribution_rejected(self):
        self.check(Source(attribution="SYMBOL_WINDOW"), "ATTRIBUTION_NOT_BY_ORDER_ID")

    def test_funding_contract_change_fails_closed(self):
        self.check(Source(funding_included=True), "FUNDING_CONTRACT_MISMATCH")

    def test_entry_not_proven(self):
        self.check(Source(opened_at=None), "ENTRY_OR_EXIT_NOT_PROVEN")

    def test_trades_not_proven_flat_but_no_trade(self):
        r, gid = self.check_fail("plan", "TRADES_NOT_PROVEN")
        self.assertEqual(r.store.holds(ACC, active_only=False)[0]["state"], "RELEASED")     # slot yine de serbest

    def check_fail(self, kind, code):
        src = Source()
        src.fail[kind] = code
        return self.check(src, code)

    def test_funding_unreadable(self):
        self.check_fail(p.FUNDING, "INCOME_READ_FAILED:Timeout")

    def test_funding_duplicate_conflict(self):
        self.check(Source(funding=[(-0.05, 2000, "f1"), (-0.07, 2000, "f1")]), "INCOME_DUPLICATE_CONFLICT")

    def test_funding_non_usdt(self):
        self.check(Source(funding=[(-0.05, 2000, "f1", "AAAUSDT", "BNB")]), "INCOME_ASSET_NOT_USDT")

    def test_funding_window_overlaps_other_bot(self):
        src = Source()
        src.foreign = ["V25_PLAN"]
        self.check(src, "FUNDING_WINDOW_OVERLAPS_OTHER_BOT")

    def test_funding_window_overlaps_other_farmer_result(self):
        r, gid = closed_rig(Source(), "TARGET")
        r.store.open_result("Gother", ACC, "AAAUSDT", "LONG", target_usdt=1, max_loss_usdt=1.5, est_cost_usdt=0.2)
        row = r.store.result(gid)
        r.store.db.execute("UPDATE farmer_result SET pnl_state='UNVERIFIED', verify_attempts=0, gross_pnl=NULL WHERE grid_id=?", (gid,))
        r.store.db.commit()
        r.orch._verify_pnl(gid)
        self.assertIn("FUNDING_WINDOW_OVERLAPS_FARMER", r.store.result(gid)["verify_error"])

    def test_error_text_is_short_and_safe(self):
        class Boom(Source):
            def plan_pnl(self, plan):
                raise RuntimeError("apiKey=SECRET123 https://x/?signature=abc")

        r, gid = closed_rig(Boom())
        err = r.store.result(gid)["verify_error"]
        self.assertNotIn("SECRET", err)
        self.assertLessEqual(len(err), 80)


class TestFundingAttribution(unittest.TestCase):
    def test_other_symbol_and_out_of_window_rows_excluded(self):
        funding = [(-0.05, 2000, "f1"), (-9.0, 2000, "f2", "ZZZUSDT"), (-7.0, 900_000_000, "f3")]
        r, gid = closed_rig(Source(funding=funding))
        res = r.store.result(gid)
        self.assertEqual(res["pnl_state"], "VERIFIED")
        self.assertAlmostEqual(res["funding"], -0.05)

    def test_duplicate_identical_funding_row_counted_once(self):
        r, gid = closed_rig(Source(funding=[(-0.05, 2000, "f1"), (-0.05, 2000, "f1")]))
        self.assertAlmostEqual(r.store.result(gid)["funding"], -0.05)

    def test_two_funding_events_summed(self):
        r, gid = closed_rig(Source(funding=[(-0.05, 2000, "f1"), (0.02, 2100, "f2")]))
        self.assertAlmostEqual(r.store.result(gid)["funding"], -0.03)

    def test_sum_income_helper_rules(self):
        row = {"incomeType": "FUNDING_FEE", "tranId": "1", "time": "100", "income": "0.1", "symbol": "A", "asset": "USDT"}
        self.assertEqual(p.sum_income([row, dict(row)], "FUNDING_FEE", "A", 0, 200), p.Decimal("0.1"))
        with self.assertRaises(p.PnlUnavailable):
            p.sum_income([{"incomeType": "FUNDING_FEE"}], "FUNDING_FEE", "A", 0, 200)


class TestRetry(unittest.TestCase):
    class Flaky(Source):
        def __init__(self, fails, **kw):
            super().__init__(**kw)
            self.fails = fails

        def plan_pnl(self, plan):
            if self.fails > 0:
                self.fails -= 1
                raise p.PnlUnavailable("TRADES_NOT_PROVEN")
            return super().plan_pnl(plan)

    def test_first_fails_second_succeeds(self):
        r, gid = closed_rig(self.Flaky(1))
        res = r.store.result(gid)
        self.assertEqual((res["pnl_state"], res["verify_attempts"]), ("UNVERIFIED", 1))
        self.assertEqual(r.orch.retry_pnl(), [])                       # vade gelmedi
        r.now += 6000
        self.assertEqual(r.orch.retry_pnl(), [gid])
        res = r.store.result(gid)
        self.assertEqual((res["pnl_state"], res["verify_attempts"]), ("VERIFIED", 2))
        self.assertAlmostEqual(res["verified_net_pnl"], 1.0)

    def test_all_attempts_fail_then_stop(self):
        r, gid = closed_rig(self.Flaky(99))
        for step in o.PNL_RETRY_BACKOFF_SECONDS:
            r.now += (step + 1) * 1000
            r.orch.retry_pnl()
        res = r.store.result(gid)
        self.assertEqual((res["pnl_state"], res["verify_attempts"]), ("UNVERIFIED", o.PNL_MAX_ATTEMPTS))
        r.now += 10_000_000
        self.assertEqual(r.orch.retry_pnl(), [])                       # tükendi: agresif polling yok
        self.assertEqual(r.store.result(gid)["verify_attempts"], o.PNL_MAX_ATTEMPTS)
        self.assertIsNone(r.store.result(gid)["verified_net_pnl"])

    def test_backoff_uses_existing_standard(self):
        self.assertEqual(o.PNL_RETRY_BACKOFF_SECONDS, (5, 15, 60, 300, 900))

    @unittest.skipIf(bool(SKIP), SKIP)
    def test_backoff_matches_v25_constant(self):
        self.assertEqual(o.PNL_RETRY_BACKOFF_SECONDS, tuple(V25.ADOPTION_PERSISTENCE_RETRY_BACKOFF_SECONDS))

    def test_retry_after_restart_picks_up_unverified(self):
        r, gid = closed_rig(self.Flaky(1))
        r2 = Rig(store=r.store, ex=r.ex)
        r2.orch = r2.build(pnl=p.FarmerPnlVerifier(r2.store, Source(), clock=r2.clock))
        self.assertEqual(r2.orch.retry_pnl(), [gid])
        self.assertEqual(r2.store.result(gid)["pnl_state"], "VERIFIED")


class TestIdempotency(unittest.TestCase):
    def test_verify_twice_same_outcome(self):
        src = Source(gross=1.2, commission=0.2, funding=[(-0.05, 2000, "f1")])
        r, gid = closed_rig(src)
        res = r.store.result(gid)
        v = p.FarmerPnlVerifier(r.store, src)
        a, b = v.verify(res), v.verify(res)
        self.assertEqual(a, b)
        self.assertAlmostEqual(a.net, 0.95)

    def test_verified_never_rewritten(self):
        src = Source()
        r, gid = closed_rig(src)
        before = dict(r.store.result(gid))
        src.gross, src.income_gross = 99.0, 99.0
        r.orch._verify_pnl(gid)
        self.assertEqual(r.store.result(gid), before)

    def test_same_income_rows_on_retry_do_not_change_net(self):
        src = Source(funding=[(-0.05, 2000, "f1")])
        r, gid = closed_rig(src)
        net = r.store.result(gid)["verified_net_pnl"]
        v = p.FarmerPnlVerifier(r.store, src)
        for _ in range(3):
            self.assertAlmostEqual(v.verify(r.store.result(gid)).net, net)


class TestSlotIndependence(unittest.TestCase):
    def test_flat_and_pnl_unverified_slot_free_and_refilled(self):
        src = Source()
        src.fail["plan"] = "TRADES_NOT_PROVEN"
        r, gid = closed_rig(src)
        self.assertEqual(r.store.result(gid)["pnl_state"], "UNVERIFIED")
        self.assertEqual(r.orch.guard.slots().closing, 0)
        self.assertEqual(r.orch.guard.slots().used, len(r.orch.engines))
        self.assertEqual(r.store.holds(ACC, active_only=False)[0]["state"], "RELEASED")


class TestIdentity(unittest.TestCase):
    def test_plan_contains_only_farmer_ids(self):
        r, gid = closed_rig(Source(), "TARGET")
        plan = p.farmer_plan(r.store, r.store.result(gid))
        ids = [plan["entry_client_order_id"], plan["stop_client_id"], *plan["close_client_order_ids"], *plan["smart_guard"]["client_ids"]]
        self.assertTrue(ids and all(i.startswith("kf-") for i in ids if i), ids)
        self.assertEqual(plan["exchange_order_ids"], [])
        self.assertEqual(plan["symbol"], "AAAUSDT")
        self.assertTrue(plan["entry_client_order_id"].endswith("E0"))
        self.assertTrue(any(i.endswith("T0") for i in plan["smart_guard"]["client_ids"]))

    def test_verifier_receives_plan_for_this_position_only(self):
        src = Source()
        r, gid = closed_rig(src, "TARGET")
        plan = next(c[1] for c in src.calls if c[0] == "plan")
        self.assertIn(gid[:16], plan["entry_client_order_id"])


@unittest.skipIf(bool(SKIP), SKIP)
class TestRealVerifiedPlanPnlContract(unittest.TestCase):
    """GERÇEK `verified_plan_pnl` ile (sahte istemci): Farmer kimlikleri dahil; Grid/DCA/Manuel emirleri aynı sembolde olsa da DAHİL DEĞİL."""

    def run_case(self, trades, orders, algos):
        import asyncio

        class Client:
            async def signed(self, method, path, params=None):
                params = params or {}
                if path == "/fapi/v1/userTrades":
                    return trades
                if path == "/fapi/v1/allOrders":
                    return orders
                if path == "/fapi/v1/allAlgoOrders":
                    return algos
                raise AssertionError(path)

        r, gid = closed_rig(Source(), "TARGET")
        plan = p.farmer_plan(r.store, r.store.result(gid))
        entry, target = plan["entry_client_order_id"], next(i for i in plan["smart_guard"]["client_ids"] if i.endswith("T0"))
        now = V25._iso_epoch_ms(plan["created_at"])
        return plan, entry, target, now, asyncio.run(V25.verified_plan_pnl(Client(), plan))

    def build(self, entry, target, now, extra=()):
        trades = [
            {"id": 1, "orderId": 101, "side": "BUY", "price": "100", "qty": "1.5", "realizedPnl": "0", "commission": "0.075", "commissionAsset": "USDT", "time": now + 1000},
            {"id": 2, "orderId": 102, "side": "SELL", "price": "101", "qty": "1.5", "realizedPnl": "1.5", "commission": "0.0757", "commissionAsset": "USDT", "time": now + 5000},
            # aynı sembolde başka botlar
            {"id": 3, "orderId": 201, "side": "SELL", "price": "100", "qty": "9", "realizedPnl": "7", "commission": "0.5", "commissionAsset": "USDT", "time": now + 2000},
            {"id": 4, "orderId": 301, "side": "SELL", "price": "100", "qty": "9", "realizedPnl": "5", "commission": "0.5", "commissionAsset": "USDT", "time": now + 2500},
            {"id": 5, "orderId": 401, "side": "SELL", "price": "100", "qty": "9", "realizedPnl": "3", "commission": "0.5", "commissionAsset": "USDT", "time": now + 2600},
            *extra]
        orders = [{"orderId": 101, "clientOrderId": entry}, {"orderId": 201, "clientOrderId": "kg-gridid-S01T0"},
                  {"orderId": 301, "clientOrderId": "kd-dcaid-S01T0"}, {"orderId": 401, "clientOrderId": "manual-close-1"}]
        algos = [{"clientAlgoId": target, "algoId": 9001, "actualOrderId": "102"}]
        return trades, orders, algos

    def test_only_farmer_orders_counted(self):
        r, gid = closed_rig(Source(), "TARGET")
        plan = p.farmer_plan(r.store, r.store.result(gid))
        now = V25._iso_epoch_ms(plan["created_at"])
        entry, target = plan["entry_client_order_id"], next(i for i in plan["smart_guard"]["client_ids"] if i.endswith("T0"))
        trades, orders, algos = self.build(entry, target, now)
        import asyncio

        class Client:
            async def signed(self, method, path, params=None):
                return {"/fapi/v1/userTrades": trades, "/fapi/v1/allOrders": orders, "/fapi/v1/allAlgoOrders": algos}[path]

        out = asyncio.run(V25.verified_plan_pnl(Client(), plan))
        self.assertAlmostEqual(out["gross_realized_pnl"], 1.5)        # Grid(7)/DCA(5)/Manuel(3) hariç
        self.assertAlmostEqual(out["commission_usdt"], 0.075 + 0.0757)
        self.assertEqual((out["attribution"], out["funding_included"], out["trade_count"]), ("ORDER_IDS", False, 2))
        self.assertTrue(out["opened_at"])

    def test_symbol_only_match_is_not_enough(self):
        r, gid = closed_rig(Source(), "TARGET")
        plan = p.farmer_plan(r.store, r.store.result(gid))
        now = V25._iso_epoch_ms(plan["created_at"])
        import asyncio

        class Client:
            async def signed(self, method, path, params=None):
                if path == "/fapi/v1/userTrades":      # yalnız başka bot işlemleri var
                    return [{"id": 3, "orderId": 201, "side": "SELL", "price": "100", "qty": "9", "realizedPnl": "7", "commission": "0.5",
                             "commissionAsset": "USDT", "time": now + 2000}]
                return []

        with self.assertRaises(V25.LiveExchangeError):
            asyncio.run(V25.verified_plan_pnl(Client(), plan))        # kimlikle kanıtlanamadı → doğrulanmadı

        # ve Farmer doğrulayıcısı bunu UNVERIFIED'a çevirir (canlı kaynak sarmalayıcısı üzerinden)
        from app.farmer_engine import live as fl

        class Live:
            LiveExchangeError = V25.LiveExchangeError
            verified_plan_pnl = staticmethod(V25.verified_plan_pnl)

        class Bridge:
            def __call__(self, coro):
                return asyncio.run(coro)

        src = fl.LivePnlSource(Bridge(), Client, Live)
        with self.assertRaises(p.PnlUnavailable):
            src.plan_pnl(plan)


class TestLiveSource(unittest.TestCase):
    def make(self, rows):
        import asyncio
        from app.farmer_engine import live as fl

        calls = []

        class Client:
            async def signed(self, method, path, params=None):
                calls.append((method, path, dict(params)))
                if isinstance(rows, Exception):
                    raise rows
                return rows

        class Bridge:
            def __call__(self, coro):
                return asyncio.run(coro)

        class Live:
            class LiveExchangeError(Exception):
                pass

        return fl.LivePnlSource(Bridge(), Client, Live), calls

    def test_income_request_shape(self):
        src, calls = self.make([])
        src.income_rows("AAAUSDT", "FUNDING_FEE", 10, 20)
        self.assertEqual(calls, [("GET", "/fapi/v1/income", {"symbol": "AAAUSDT", "incomeType": "FUNDING_FEE", "startTime": 10, "endTime": 20, "limit": 1000})])

    def test_page_limit_and_invalid_and_error(self):
        src, _ = self.make([{}] * 1000)
        with self.assertRaises(p.PnlUnavailable):
            src.income_rows("A", "FUNDING_FEE", 1, 2)
        src, _ = self.make({"not": "list"})
        with self.assertRaises(p.PnlUnavailable):
            src.income_rows("A", "FUNDING_FEE", 1, 2)
        src, _ = self.make(RuntimeError("secret=abc"))
        with self.assertRaises(p.PnlUnavailable) as cm:
            src.income_rows("A", "FUNDING_FEE", 1, 2)
        self.assertNotIn("abc", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
