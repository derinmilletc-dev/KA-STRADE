"""KAIS FARMER C9 — ARM / STOP / acil kapanış / yeniden başlatma kurtarması / izolasyon / fail-closed (sahte borsa; ağ yok)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.farmer_engine import orchestrator as o  # noqa: E402
from app.farmer_engine import pnl as p  # noqa: E402
from app.farmer_engine.ids import farmer_grid_prefix  # noqa: E402
from app.grid_engine.ports import EPS, NEW, PARTIAL  # noqa: E402
from tests.test_farmer_orchestrator import ACC, Rig  # noqa: E402
from tests.test_farmer_pnl import Source  # noqa: E402

ONE = {"BBBUSDT": {"confidence": 50}, "CCCUSDT": {"confidence": 50}}      # yalnız AAAUSDT onaylanır


def one(**kw):
    r = Rig(table=dict(ONE), **kw)
    r.run()
    assert len(r.orch.engines) == 1
    return r


def live_orders(ex, prefix="kf-"):
    return sorted(x.client_id for x in ex.orders.values() if x.status in (NEW, PARTIAL) and x.client_id.startswith(prefix))


def restart(r, table=None, **kw):
    r2 = Rig(store=r.store, ex=r.ex, table=dict(table or ONE), **kw)
    r2.now = r.now
    return r2


def add_foreign(r):
    """Grid (kg-), DCA (kd-), Manuel ve Original V2 pozisyon/emirleri — Farmer'ın DOKUNMAMASI gereken her şey."""
    for sym in ("GRIDUSDT", "DCAUSDT", "MANUSDT", "ORIGUSDT"):
        r.ex.prices[sym] = 100.0
        r.ex.set_position(sym, 3.0)
    r.ex.place_order(client_id="kg-gridid-L01E0", symbol="GRIDUSDT", side="BUY", type="LIMIT", qty=1.0, price=50.0)
    r.ex.place_order(client_id="kd-dcaid-L01E0", symbol="DCAUSDT", side="BUY", type="LIMIT", qty=1.0, price=50.0)
    r.ex.place_order(client_id="manual-1", symbol="MANUSDT", side="BUY", type="LIMIT", qty=1.0, price=50.0)
    r.ex.place_order(client_id="orig-v2-1", symbol="ORIGUSDT", side="BUY", type="LIMIT", qty=1.0, price=50.0)
    # aynı sembolde (Farmer'ın sembolünde) başka botun emri de olabilir
    r.ex.place_order(client_id="kg-gridid-L02E0", symbol="AAAUSDT", side="BUY", type="LIMIT", qty=1.0, price=40.0)
    return {sym: r.ex.position(sym) for sym in ("GRIDUSDT", "DCAUSDT", "MANUSDT", "ORIGUSDT")}, live_orders(r.ex, "")


def foreign_intact(tc, r, positions, orders_before):
    for sym, qty in positions.items():
        tc.assertEqual(r.ex.position(sym), qty, sym)
    for cid in ("kg-gridid-L01E0", "kd-dcaid-L01E0", "manual-1", "orig-v2-1", "kg-gridid-L02E0"):
        tc.assertIn(cid, live_orders(r.ex, ""), cid)
        tc.assertNotIn(cid, r.ex.cancelled, cid)
    tc.assertFalse([x for x in r.ex.placed if x[0] in ("GRIDUSDT", "DCAUSDT", "MANUSDT", "ORIGUSDT")][4:])      # kurulum emirleri hariç Farmer emri yok


class TestArm(unittest.TestCase):
    def test_arm_valid_entry_allowed(self):
        r = Rig(table=dict(ONE))
        self.assertEqual(r.run()["state"], o.RUNNING)
        self.assertEqual(len(r.orch.engines), 1)

    def test_arm_expired_blocks_entry_keeps_protection_does_not_close(self):
        r = Rig(table={**ONE, "CCCUSDT": {"confidence": 94}})
        r.run()
        before = live_orders(r.ex)
        self.assertEqual(len(before), 4)                              # 2 pozisyon × (STOP + TP)
        pos = {s: r.ex.position(s) for s in ("AAAUSDT", "BBBUSDT")}
        r.gates.arm = ("ARM_EXPIRED",)
        placed = len(r.ex.placed)
        for _ in range(3):
            self.assertEqual(r.tick()["state"], o.ENTRY_PAUSED_ARM)
        self.assertEqual(live_orders(r.ex), before)                   # STOP/TP aynen duruyor
        self.assertEqual({s: r.ex.position(s) for s in pos}, pos)     # ARM bitişi pozisyon KAPATMAZ
        self.assertEqual(len(r.ex.placed), placed)                    # emir yok (giriş de, kapanış da)
        self.assertEqual(r.orch.guard.slots().open, 2)

    def test_arm_expired_stop_and_tp_still_fire(self):
        r = Rig(table=dict(ONE))
        r.run()
        r.gates.arm = ("ARM_EXPIRED",)
        eng = next(iter(r.orch.engines.values()))
        r.ex.set_price("AAAUSDT", eng.tp_price() + 0.5)
        r.tick()
        self.assertEqual(r.store.results(ACC)[0]["close_reason"], "TARGET")

    def test_arm_renewed_resumes_entry_automatically(self):
        r = Rig(table=dict(ONE))
        r.orch.recover()
        r.orch.start()
        r.gates.arm = ("ARM_EXPIRED",)
        r.tick()
        self.assertEqual((r.orch.state, len(r.ex.placed)), (o.ENTRY_PAUSED_ARM, 0))
        r.gates.arm = ()
        r.ev.table.pop("BBBUSDT", None)
        self.assertEqual(r.tick()["state"], o.RUNNING)
        self.assertEqual(len(r.orch.engines), 1)

    def test_arm_expiry_across_restart_keeps_entry_blocked(self):
        r = one()
        r.gates.arm = ("ARM_EXPIRED",)
        r2 = restart(r)
        r2.gates.arm = ("ARM_EXPIRED",)
        r2.orch.recover()
        self.assertEqual(r2.orch.state, o.ENTRY_PAUSED_ARM)
        r2.tick()
        self.assertEqual(r2.ex.placed, r.ex.placed)                    # restart ARM'ı atlatmaz


class TestStopBot(unittest.TestCase):
    def test_stop_blocks_entry_keeps_position_and_protection(self):
        r = one()
        before, pos = live_orders(r.ex), r.ex.position("AAAUSDT")
        self.assertEqual(r.orch.stop_bot(), o.STOPPING)
        placed = len(r.ex.placed)
        r.next_candle()
        r.tick()
        self.assertEqual((live_orders(r.ex), r.ex.position("AAAUSDT"), len(r.ex.placed)), (before, pos, placed))
        self.assertEqual(r.orch.guard.slots().open, 1)

    def test_stop_then_natural_close_then_off(self):
        r = one()
        r.orch.stop_bot()
        eng = next(iter(r.orch.engines.values()))
        r.ex.set_price("AAAUSDT", eng.stop_target_price() - 0.5)
        r.tick()
        r.tick()
        self.assertEqual((r.orch.state, r.store.results(ACC)[0]["close_reason"]), (o.OFF, "STOP"))
        self.assertEqual(live_orders(r.ex), [])

    def test_stop_survives_restart_without_entries(self):
        r = one()
        r.orch.stop_bot()
        r2 = restart(r)
        r2.orch.recover()
        self.assertEqual(r2.orch.state, o.STOPPING)
        placed = len(r2.ex.placed)
        r2.next_candle()
        self.assertEqual(len(r2.ex.placed), placed)


class TestEmergency(unittest.TestCase):
    def test_closes_only_farmer_reduce_only_flat_cleanup(self):
        r = Rig(table={**ONE, "CCCUSDT": {"confidence": 94}})
        r.run()
        foreign = add_foreign(r)
        rep = r.orch.emergency_close()
        self.assertEqual((len(rep["closed"]), rep["failed"], rep["unowned"]), (2, [], []))
        closes = [x for x in r.ex.placed_ro if x[1] == "MARKET" and "-C" in x[0] or x[0].endswith("C0")]
        self.assertTrue(closes and all(ro for _cid, _t, ro in closes))      # kapanış reduce-only
        self.assertEqual((r.ex.position("AAAUSDT"), r.ex.position("BBBUSDT")), (0.0, 0.0))
        self.assertEqual(live_orders(r.ex), [])                              # kf- temiz
        foreign_intact(self, r, *foreign)
        self.assertEqual(r.orch.state, o.OFF)
        self.assertTrue(all(x["close_reason"] == "EMERGENCY" for x in r.store.results(ACC)))
        self.assertEqual(r.orch.guard.slots().used, 0)

    def test_flat_unverifiable_is_intervention_and_keeps_slot(self):
        r = one()
        r.ex.fail_reads = True
        rep = r.orch.emergency_close()
        self.assertTrue(rep["failed"])
        self.assertEqual(r.orch.state, o.INTERVENTION)
        self.assertEqual(r.orch.guard.slots().used, 1)                       # körlemesine bırakılmaz

    def test_unknown_ownership_kf_order_intervention_and_not_cancelled(self):
        r = one()
        r.ex.place_order(client_id="kf-ghost-L01T0", symbol="AAAUSDT", side="SELL", type="LIMIT", qty=1.0, price=500.0)
        rep = r.orch.emergency_close()
        self.assertEqual(rep["unowned"], ["kf-ghost-L01T0"])
        self.assertEqual(r.orch.state, o.INTERVENTION)
        self.assertIn("kf-ghost-L01T0", live_orders(r.ex))
        self.assertNotIn("kf-ghost-L01T0", r.ex.cancelled)
        self.assertEqual(r.ex.position("AAAUSDT"), 0.0)                      # kanıtlı pozisyon yine de kapandı

    def test_held_entry_without_fill_released_not_marked_closed(self):
        r = Rig(table=dict(ONE))
        r.ex.drop_entry_before = True
        r.run()
        self.assertEqual(r.orch.guard.slots().held, 1)
        rep = r.orch.emergency_close()
        self.assertEqual(rep["failed"], [])
        self.assertEqual(r.orch.guard.slots().used, 0)
        self.assertEqual(r.store.results(ACC), [])                           # hiç pozisyon olmadı → sonuç satırı yok
        self.assertEqual(r.res.state[next(iter(r.res.state))], "RELEASED")

    def test_emergency_does_not_resume_to_running(self):
        r = one()
        r.orch.emergency_close()
        self.assertEqual(r.orch.state, o.OFF)
        r.tick()
        self.assertEqual(r.orch.state, o.OFF)


class TestRecoveryOpen(unittest.TestCase):
    def test_normal_open_recovery_no_duplicate_no_new_entry(self):
        r = one()
        orders, pos, placed = live_orders(r.ex), r.ex.position("AAAUSDT"), len(r.ex.placed)
        r2 = restart(r)
        rep = r2.run()
        self.assertTrue(r2.orch.recovered)
        self.assertEqual(r2.orch.state, o.RUNNING)
        self.assertEqual((live_orders(r2.ex), r2.ex.position("AAAUSDT")), (orders, pos))
        self.assertEqual(len([x for x in r2.ex.placed[placed:] if x[1] == "MARKET"]), 0)       # yeni giriş yok (aynı mum)
        self.assertEqual(r2.orch.guard.slots().open, 1)

    def test_open_ledger_target_triggered_while_down(self):
        r = one()
        eng = next(iter(r.orch.engines.values()))
        r.ex.set_price("AAAUSDT", eng.tp_price() + 0.5)                       # süreç yokken hedef tetiklendi
        self.assertEqual(r.ex.position("AAAUSDT"), 0.0)
        r2 = restart(r)
        r2.orch.recover()
        res = r2.store.results(ACC)[0]
        self.assertEqual(res["close_reason"], "TARGET")
        self.assertEqual(res["pnl_state"], "UNVERIFIED")                      # C8 hattına girdi, tahmin yazılmadı
        self.assertIsNone(res["verified_net_pnl"])
        self.assertEqual(r2.orch.guard.slots().used, 0)
        self.assertEqual(r2.store.holds(ACC, active_only=False)[0]["state"], "RELEASED")
        self.assertEqual(live_orders(r2.ex), [])

    def test_open_ledger_externally_closed_flat_finalized(self):
        r = one()
        r.ex.set_position("AAAUSDT", 0.0)                                      # kullanıcı elle kapattı; STOP/TP borsada kaldı
        self.assertEqual(len(live_orders(r.ex)), 2)
        r2 = restart(r)
        r2.orch.recover()
        res = r2.store.results(ACC)[0]
        self.assertEqual(res["close_reason"], "MARKET_CLOSE")
        self.assertEqual(res["pnl_state"], "UNVERIFIED")
        self.assertEqual(live_orders(r2.ex), [])                               # ledger'da kimliği kanıtlı Farmer emirleri temizlendi
        self.assertEqual(r2.orch.guard.slots().used, 0)
        self.assertTrue(r2.orch.recovered)

    def test_open_ledger_position_mismatch_not_flat_is_intervention(self):
        r = one()
        r.ex.set_position("AAAUSDT", 0.3)                                      # ledger 1.5, borsa 0.3: kanıtsız
        r2 = restart(r)
        r2.orch.recover()
        self.assertEqual(r2.orch.state, o.INTERVENTION)
        self.assertEqual(r2.orch.guard.slots().used, 1)

    def test_missing_stop_reinstalled(self):
        r = one()
        stop = next(c for c in live_orders(r.ex) if "S0" in c)
        r.ex.cancel_order("AAAUSDT", stop)
        r2 = restart(r)
        r2.orch.recover()
        self.assertTrue(r2.orch.recovered)
        self.assertEqual(len(live_orders(r2.ex)), 2)

    def test_missing_tp_reinstalled(self):
        r = one()
        tp = next(c for c in live_orders(r.ex) if "T0" in c)
        r.ex.cancel_order("AAAUSDT", tp)
        r2 = restart(r)
        r2.orch.recover()
        self.assertTrue(r2.orch.recovered)
        self.assertEqual(len(live_orders(r2.ex)), 2)

    def test_both_protections_missing_reinstalled_stop_first(self):
        r = one()
        for c in live_orders(r.ex):
            r.ex.cancel_order("AAAUSDT", c)
        n = len(r.ex.placed)
        r2 = restart(r)
        r2.orch.recover()
        new = [x[1] for x in r2.ex.placed[n:]]
        self.assertEqual(new[:1], ["STOP_MARKET"])                             # önce stop (C3 sözleşmesi)
        self.assertTrue(r2.orch.recovered)

    def test_duplicate_protection_is_intervention(self):
        r = one()
        eng = next(iter(r.orch.engines.values()))
        r.ex.place_order(client_id=eng.prefix + "L01T9", symbol="AAAUSDT", side="SELL", type="LIMIT", qty=1.0, price=500.0)
        r2 = restart(r)
        r2.orch.recover()
        self.assertEqual(r2.orch.state, o.INTERVENTION)
        self.assertEqual(r2.orch.guard.slots().used, 1)                        # pozisyon slotu tutuluyor


class TestRecoveryPending(unittest.TestCase):
    def pending(self):
        r = Rig(table=dict(ONE))
        r.ex.drop_entry_before = True                                          # giriş emri borsaya ULAŞMADI
        r.run()
        gid = next(iter(r.orch.engines))
        return r, gid

    def test_held_no_exchange_entry_released_and_never_resent(self):
        r, gid = self.pending()
        self.assertEqual(r.orch.guard.slots().held, 1)
        n = len(r.ex.placed)
        r2 = restart(r)
        rep = r2.orch.recover()
        self.assertEqual(r2.orch.guard.slots().used, 0)
        self.assertEqual(len(r2.ex.placed), n)                                 # ESKİ giriş yeniden GÖNDERİLMEDİ
        self.assertEqual(r2.store.attempt(ACC, "AAAUSDT", "15m", r.feed.last_closed_open_ms(r.now))["outcome"], "NO_FILL")
        self.assertTrue(r2.orch.recovered)
        r2.orch.start()
        r2.tick()
        self.assertEqual([x for x in r2.ex.placed[n:] if x[1] == "MARKET"], [])  # aynı mum + aynı sembol: kilit kalıcı

    def test_submitted_no_order_reservation_released(self):
        r, gid = self.pending()
        self.assertEqual(r.res.state[gid], "SUBMITTED")
        r2 = restart(r)
        r2.res = r2.orch.reservations
        r2.orch.recover()
        self.assertEqual(r2.res.state.get(gid), "RELEASED")

    def test_submitted_matching_exchange_order_adopted(self):
        r, gid = self.pending()
        eng = r.orch.engines[gid]
        row = r.store.orders(gid, "E")[0]
        r.ex.place_order(client_id=row["client_id"], symbol="AAAUSDT", side=row["side"], type="MARKET", qty=row["qty"])   # emir geç ulaştı → doldu
        self.assertGreater(abs(r.ex.position("AAAUSDT")), EPS)
        n = len(r.ex.placed)
        r2 = restart(r)
        r2.orch.recover()
        self.assertEqual(r2.orch.guard.slots().open, 1)
        self.assertEqual(r2.res.state.get(gid), "CONSUMED")
        self.assertEqual(len([x for x in r2.ex.placed[n:] if x[1] == "MARKET"]), 0)       # ikinci giriş yok
        self.assertEqual(len(live_orders(r2.ex)), 2)                            # koruma kuruldu
        self.assertTrue(r2.orch.recovered)

    def test_submitted_order_read_fails_intervention_keeps_reservation(self):
        r, gid = self.pending()
        r2 = restart(r)
        r.ex.fail_reads = True
        r2.orch.recover()
        self.assertEqual(r2.orch.state, o.INTERVENTION)
        self.assertNotEqual(r2.res.state.get(gid), "RELEASED")
        self.assertEqual(r2.orch.guard.slots().used, 1)

    def test_submitted_not_found_but_position_exists_is_ambiguous(self):
        r, gid = self.pending()
        r.ex.set_position("AAAUSDT", 1.5)                                       # emir yok ama pozisyon var: kanıtsız
        r2 = restart(r)
        r2.orch.recover()
        self.assertEqual(r2.orch.state, o.INTERVENTION)
        self.assertEqual(r2.orch.guard.slots().used, 1)

    def test_held_without_ledger_row_released_when_nothing_on_exchange(self):
        r = Rig(table=dict(ONE))
        r.store.claim_entry(ACC, "AAAUSDT", "15m", 5, "Gx1", 50.0, cap_usdt=100.0)
        r.orch.recover()
        self.assertEqual(r.orch.guard.slots().used, 0)
        self.assertTrue(r.orch.recovered)

    def test_held_without_ledger_row_but_kf_order_on_exchange_is_intervention(self):
        r = Rig(table=dict(ONE))
        r.store.claim_entry(ACC, "AAAUSDT", "15m", 5, "Gx1", 50.0, cap_usdt=100.0)
        r.ex.place_order(client_id=farmer_grid_prefix("Gx1") + "L01E0", symbol="AAAUSDT", side="BUY", type="LIMIT", qty=1.0, price=10.0)
        r.orch.recover()
        self.assertEqual(r.orch.state, o.INTERVENTION)
        self.assertEqual(r.orch.guard.slots().used, 1)

    def test_unknown_foreign_order_untouched_when_releasing_held(self):
        r, gid = self.pending()
        r.ex.place_order(client_id="manual-77", symbol="AAAUSDT", side="BUY", type="LIMIT", qty=1.0, price=10.0)
        r2 = restart(r)
        r2.orch.recover()
        self.assertIn("manual-77", live_orders(r2.ex, ""))
        self.assertNotIn("manual-77", r2.ex.cancelled)


class TestOrphans(unittest.TestCase):
    def test_exchange_farmer_position_without_ledger_is_intervention_untouched(self):
        r = Rig(table=dict(ONE))
        r.ex.set_position("AAAUSDT", 1.5)
        r.ex.place_order(client_id="kf-ghost-L01S0", symbol="AAAUSDT", side="SELL", type="STOP_MARKET", qty=1.5, stop_price=90.0, reduce_only=True)
        r.orch.recover()
        self.assertEqual(r.orch.state, o.INTERVENTION)
        self.assertEqual((r.ex.position("AAAUSDT"), r.ex.cancelled, r.ex.placed_ro[1:]), (1.5, [], []))
        self.assertEqual(r.orch.start(), o.INTERVENTION)

    def test_orphan_kf_order_fail_closed_not_deleted(self):
        r = Rig(table=dict(ONE))
        r.ex.place_order(client_id="kf-ghost-L01T0", symbol="BBBUSDT", side="BUY", type="LIMIT", qty=1.0, price=10.0)
        r.orch.recover()
        self.assertEqual(r.orch.state, o.INTERVENTION)
        self.assertIn("kf-ghost-L01T0", live_orders(r.ex))
        r.tick()
        self.assertEqual(r.ex.placed_ro[-1][0], "kf-ghost-L01T0")              # Farmer yeni emir göndermedi

    def test_unknown_non_farmer_order_without_ledger_ignored(self):
        r = Rig(table=dict(ONE))
        r.ex.place_order(client_id="mystery-1", symbol="AAAUSDT", side="BUY", type="LIMIT", qty=1.0, price=10.0)
        r.orch.recover()
        self.assertTrue(r.orch.recovered)
        self.assertIn("mystery-1", live_orders(r.ex, ""))
        self.assertEqual(r.ex.cancelled, [])

    def test_orphan_reservation_fail_closed(self):
        r = Rig(table=dict(ONE))
        r.res.orphan = ["Gx"]
        r.orch.recover()
        self.assertEqual(r.orch.state, o.INTERVENTION)

    def test_reservation_read_failure_fail_closed(self):
        r = Rig(table=dict(ONE))

        def boom():
            raise RuntimeError("db")

        r.res.orphans = boom
        r.orch.recover()
        self.assertEqual(r.orch.state, o.INTERVENTION)

    def test_reservation_reconcile_failure_fail_closed(self):
        r = Rig(table=dict(ONE))

        def boom():
            raise RuntimeError("x")

        r.res.reconcile = boom
        r.orch.recover()
        self.assertEqual(r.orch.state, o.INTERVENTION)

    def test_reservation_reconcile_called_first(self):
        r = Rig(table=dict(ONE))
        calls = []
        r.res.reconcile = lambda: calls.append(r.source.reads)
        r.orch.recover()
        self.assertEqual(calls, [0])

    def test_snapshot_read_failure_at_recovery_fail_closed(self):
        r = Rig(table=dict(ONE))
        r.source.fail = True
        r.orch.recover()
        self.assertFalse(r.orch.recovered)
        self.assertEqual(r.orch.state, o.INTERVENTION)


class TestIntervention(unittest.TestCase):
    def test_persists_across_restart_and_never_auto_resumes(self):
        r = Rig(table=dict(ONE))
        r.ex.place_order(client_id="kf-ghost-L01T0", symbol="BBBUSDT", side="BUY", type="LIMIT", qty=1.0, price=10.0)
        r.orch.recover()
        r.ex.cancel_order("BBBUSDT", "kf-ghost-L01T0")                         # neden ortadan kalktı
        r2 = restart(r)
        r2.orch.recover()
        self.assertEqual(r2.orch.state, o.INTERVENTION)                        # kayıtlı müdahale kendiliğinden kalkmaz
        self.assertEqual(r2.orch.start(), o.INTERVENTION)
        r2.tick()
        self.assertEqual(r2.ex.placed_ro[-1][0], "kf-ghost-L01T0")

    def test_acknowledge_requires_clean_reconciliation(self):
        r = Rig(table=dict(ONE))
        r.ex.place_order(client_id="kf-ghost-L01T0", symbol="BBBUSDT", side="BUY", type="LIMIT", qty=1.0, price=10.0)
        r.orch.recover()
        self.assertEqual(r.orch.acknowledge_intervention(), o.INTERVENTION)    # sorun sürüyor
        r.ex.cancel_order("BBBUSDT", "kf-ghost-L01T0")                         # kullanıcı temizledi
        self.assertEqual(r.orch.acknowledge_intervention(), o.OFF)             # RUNNING'e değil OFF'a
        self.assertEqual(r.orch.start(), o.RUNNING)

    def test_intervention_keeps_protecting_open_position(self):
        r = one()
        r.orch._intervene("test")
        eng = next(iter(r.orch.engines.values()))
        r.ex.set_price("AAAUSDT", eng.tp_price() + 0.5)
        r.tick()
        self.assertEqual(r.store.results(ACC)[0]["close_reason"], "TARGET")
        self.assertEqual(r.orch.state, o.INTERVENTION)


class TestCooldownRestart(unittest.TestCase):
    def test_same_candle_not_reentered_after_restart_new_candle_allowed(self):
        r = one()
        eng = next(iter(r.orch.engines.values()))
        r.ex.set_price("AAAUSDT", eng.tp_price() + 0.5)
        r.tick()
        self.assertEqual(r.orch.guard.slots().used, 0)
        released = r.store.holds(ACC, active_only=False)[0]["released_at"]
        self.assertGreater(released, 0)                                         # flat doğrulanınca zaman damgası ledger'da
        r.ex.set_price("AAAUSDT", 100.0)
        n = len(r.ex.placed)
        r2 = restart(r)
        r2.run()
        self.assertEqual([x for x in r2.ex.placed[n:] if x[1] == "MARKET"], [])    # aynı sembol+mum kilidi yeniden başlatmada da geçerli
        r2.next_candle()
        self.assertEqual(len([x for x in r2.ex.placed[n:] if x[1] == "MARKET"]), 1)


class TestPnlFinality(unittest.TestCase):
    def setUp(self):
        self.src = Source(funding=[(-0.05, 2000, "f1")])

    def rig(self):
        r = Rig(table=dict(ONE))
        r.orch = r.build(pnl=p.FarmerPnlVerifier(r.store, self.src, clock=lambda: self.t))
        self.t = 0.0
        return r

    def close(self, r):
        r.run()
        eng = next(iter(r.orch.engines.values()))
        r.ex.set_price("AAAUSDT", eng.tp_price() + 0.5)
        r.tick()
        return r.store.results(ACC)[0]

    def test_funding_not_final_is_unverified_not_zero(self):
        r = self.rig()
        self.t = 0.0                                                            # kapanıştan ÖNCE/aynı an: funding kesinleşmedi
        res = self.close(r)
        self.assertEqual(res["pnl_state"], "UNVERIFIED")
        self.assertIn("FUNDING_NOT_FINAL", res["verify_error"])
        self.assertTrue(res["funding"] is None and res["verified_net_pnl"] is None)       # funding=0 YAZILMAZ
        self.assertEqual(r.store.holds(ACC, active_only=False)[0]["state"], "RELEASED")  # slot PnL'den önce/bağımsız

    def test_funding_eventually_appears_then_verified(self):
        r = self.rig()
        res = self.close(r)
        gid = res["grid_id"]
        self.assertEqual(res["pnl_state"], "UNVERIFIED")
        self.t = res["closed_at"] + p.FUNDING_SETTLE_SECONDS + 1
        r.now += 10_000
        r.orch._pnl_due[gid] = 0
        r.orch.retry_pnl()
        res = r.store.result(gid)
        self.assertEqual((res["pnl_state"], res["verify_attempts"]), ("VERIFIED", 2))
        self.assertAlmostEqual(res["funding"], -0.05)

    def test_retry_schedule_preserved(self):
        self.assertEqual(o.PNL_RETRY_BACKOFF_SECONDS, (5, 15, 60, 300, 900))

    def test_verified_result_not_rewritten_after_more_funding(self):
        r = self.rig()
        res = self.close(r)
        self.t = res["closed_at"] + 1000
        r.orch._pnl_due[res["grid_id"]] = 0
        r.orch.retry_pnl()
        before = dict(r.store.result(res["grid_id"]))
        self.src.funding.append((-9.0, 2100, "f2"))
        r.orch._verify_pnl(res["grid_id"])
        self.assertEqual(r.store.result(res["grid_id"]), before)

    def test_funding_unavailable_stays_unverified(self):
        r = self.rig()
        self.src.fail[p.FUNDING] = "INCOME_READ_FAILED:Timeout"
        self.t = 1e12
        res = self.close(r)
        self.assertEqual(res["pnl_state"], "UNVERIFIED")
        self.assertIn("INCOME_READ_FAILED", res["verify_error"])

    def test_pnl_source_unavailable_slot_still_freed(self):
        r = self.rig()
        self.src.fail["plan"] = "TRADES_NOT_PROVEN"
        res = self.close(r)
        self.assertEqual(res["pnl_state"], "UNVERIFIED")
        self.assertEqual(r.orch.guard.slots().used, 0)

    def test_slot_released_before_pnl_verification_runs(self):
        seen = []
        outer = self

        class Spy:
            def verify(self, result):
                seen.append(outer.r.store.holds(ACC, active_only=False)[0]["state"])
                return o.PnlOutcome("UNVERIFIED", error="X")

        self.r = r = Rig(table=dict(ONE))
        r.orch = r.build(pnl=Spy())
        self.close(r)
        self.assertEqual(seen, ["RELEASED"])


class TestIsolationAndSafety(unittest.TestCase):
    def test_full_lifecycle_never_touches_foreign(self):
        r = Rig(table=dict(ONE))
        r.run()
        foreign = add_foreign(r)
        eng = next(iter(r.orch.engines.values()))
        r.ex.set_price("AAAUSDT", eng.tp_price() + 0.5)
        r.tick()
        r2 = restart(r)
        r2.orch.recover()
        r2.orch.emergency_close()
        foreign_intact(self, r2, *foreign)

    def test_recovery_with_foreign_everywhere_does_not_touch(self):
        r = one()
        foreign = add_foreign(r)
        r2 = restart(r)
        r2.orch.recover()
        r2.orch.emergency_close()
        foreign_intact(self, r2, *foreign)

    def test_daily_loss_blocks_entry(self):
        r = Rig(table=dict(ONE))
        r.orch = r.build(daily_loss=lambda: ("DAILY_LOSS_LIMIT",))
        r.run()
        self.assertEqual((r.orch.state, r.ex.placed), (o.ENTRY_PAUSED_SAFETY, []))

    def test_unreadable_daily_ledger_blocks_entry_but_not_protection(self):
        r = one()
        r.orch = r.build(daily_loss=lambda: ("DAILY_LEDGER_UNAVAILABLE",))
        r.orch.engines = {}
        r2 = Rig(table=dict(ONE))
        r2.orch = r2.build(daily_loss=lambda: ("DAILY_LEDGER_UNAVAILABLE",))
        r2.run()
        self.assertEqual((r2.orch.state, r2.ex.placed), (o.ENTRY_PAUSED_SAFETY, []))

    def test_margin_cap_respected(self):
        r = Rig(table={"BBBUSDT": {"confidence": 94}, "CCCUSDT": {"confidence": 94}})
        r.run()
        self.assertEqual(len(r.orch.engines), 2)
        self.assertLessEqual(r.store.held_margin(ACC), 100.0)

    def test_account_reservation_respected(self):
        r = Rig(table=dict(ONE))
        r.res.deny = True
        r.run()
        self.assertEqual(r.ex.placed, [])

    def test_occupancy_respected(self):
        r = Rig(table=dict(ONE))
        r.gates.foreign["AAAUSDT"] = ("OTHER_BOT",)
        r.run()
        self.assertEqual(r.ex.placed, [])

    def test_exchange_failure_during_tick_does_not_open_or_crash(self):
        r = one()
        r.ex.fail_reads = True
        r.tick()                                                                # okuma hataları motor turunu bozmaz, giriş yok
        self.assertEqual(r.orch.guard.slots().open, 1)

    def test_off_orchestrator_makes_no_exchange_read(self):
        r = Rig(table=dict(ONE))
        r.orch.recover()
        reads, src = dict(r.ex.reads), r.source.reads
        r.orch.tick(r.now)
        self.assertEqual((dict(r.ex.reads), r.source.reads, r.ex.placed), (reads, src, []))


if __name__ == "__main__":
    unittest.main()
