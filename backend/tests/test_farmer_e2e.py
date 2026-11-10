"""KAIS FARMER C11 — gerçek runtime/store/rezervasyon/kurtarma yollarıyla uçtan uca doğrulama (Binance'e emir YOK, canlı kapılar KAPALI).

GERÇEK: farmer_runtime, Orchestrator, FarmerStore, account_reservations, v25_execution kapıları ve `reconcile_account_reservations`, farmer_api işleyicileri.
SAHTE (STUB): Binance (CountingExchange/FakeClient), kline akışı, fastapi (kabuk/yok). HTTP yığını (gerçek FastAPI/uvicorn) burada ÇALIŞMAZ."""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import account_reservations as ar  # noqa: E402
from app.farmer_engine import orchestrator as o  # noqa: E402
from app.farmer_engine.store import FarmerStore  # noqa: E402
from tests.test_farmer_orchestrator import ACC, Rig  # noqa: E402
from tests.test_farmer_runtime import (HAS_FASTAPI, OPEN_ENV, OWNER, Base, Kit, live_kf, new_app, new_state, rows_all)  # noqa: E402

if HAS_FASTAPI:
    from fastapi import HTTPException  # noqa: E402
    from app import farmer_api as api  # noqa: E402

TWO = {"BBBUSDT": {"confidence": 50}}      # AAA + CCC onaylı


def stops_and_tps(ex):
    import re
    ids = live_kf(ex)
    return [c for c in ids if re.search(r"-L\d\dS\d+$", c)], [c for c in ids if re.search(r"-L\d\dT\d+$", c)]


class TestArmExpiry(Base):
    def test_arm_expiry_pauses_entries_only_and_resume_restores_running(self):
        k = Kit()
        k.go()
        ex, state = k.rig.ex, k.app.state.v25_execution
        protection, placed = live_kf(ex), len(ex.placed)
        state["armed_until"] = 0.0                                              # ARM süresi doldu
        self.assertEqual(k.orch.state, o.ENTRY_PAUSED_ARM)
        k.rig.now += 20 * 60_000
        k.orch.tick(k.rig.now)
        self.assertEqual(len(ex.placed), placed)                                # yeni emir yok
        self.assertEqual(live_kf(ex), protection)                               # STOP/TP aynen
        self.assertEqual(ex.cancelled, [])
        self.assertEqual(len(k.orch.engines), 1)                                # pozisyon otomatik kapanmadı
        self.assertNotEqual(ex.position("AAAUSDT"), 0.0)
        import time
        self.assertTrue(state["real_trading_locked"])                           # mevcut sistem: ARM bitince canlı kilit kapanır
        state["armed_until"], state["real_trading_locked"] = time.time() + 3600, False       # gerçek ARM akışının sonucu (yeniden ARM)
        self.assertEqual(k.orch.state, o.RUNNING)                               # yalnız yeni girişe izin döner

    def test_arm_expiry_blocks_new_entry_on_next_candle(self):
        from app import farmer_view
        k = Kit(rig=Rig(table=dict(TWO), store=FarmerStore(":memory:", check_same_thread=False)))
        k.runtime.apply_settings(farmer_view.parse_settings({"margin_usdt": 50, "max_positions": 2}))
        k.go()
        k.app.state.v25_execution["armed_until"] = 0.0
        before = {p[0] for p in k.rig.ex.placed}
        k.rig.next_candle() if False else None
        k.rig.now = (k.rig.now // 900_000 + 1) * 900_000 + 5000
        k.rig.stream.advance(k.rig.now)
        k.orch.tick(k.rig.now)
        self.assertEqual({p[0] for p in k.rig.ex.placed}, before)               # ikinci aday ARM yokken açılmadı


class TestStopBot(Base):
    def test_stop_bot_pauses_entries_keeps_position_and_protection_across_restart(self):
        k = Kit()
        k.go()
        ex = k.rig.ex
        protection, pos = live_kf(ex), ex.position("AAAUSDT")
        k.orch.stop_bot()
        self.assertEqual(k.orch.base, o.STOPPING)
        k.orch.tick(k.rig.now + 1000)
        self.assertEqual((live_kf(ex), ex.position("AAAUSDT"), ex.cancelled), (protection, pos, []))   # kapatma YOK, koruma iptal YOK
        placed = len(ex.placed)
        k.restart()
        k.runtime.prepare_start()
        self.assertNotEqual(k.orch.base, o.RUNNING)                             # yeniden başlatma STOP'u RUNNING'e çevirmez
        k.orch.tick(k.rig.now + 2000)
        self.assertEqual((len(ex.placed), live_kf(ex), ex.position("AAAUSDT")), (placed, protection, pos))
        self.assertEqual(len(k.orch.engines), 1)

    def test_stop_bot_is_not_emergency_close(self):
        k = Kit()
        k.go()
        k.orch.stop_bot()
        self.assertEqual([r for r in k.rig.store.results(ACC) if r.get("closed_at") or r["close_reason"] != "OPEN"], [])      # kapanış kaydı yok (yalnız OPEN yer tutucu)
        self.assertEqual(k.orch.guard.slots().used, 1)                          # slot hâlâ dolu


@unittest.skipUnless(HAS_FASTAPI, "fastapi yok")
class TestInterventionFlow(Base):
    def setUp(self):
        super().setUp()
        self._o, self._b = api._owner, api.live_entry_blockers
        api._owner = lambda request: {"id": OWNER, "role": "OWNER"}
        api.live_entry_blockers = lambda: []

    def tearDown(self):
        api._owner, api.live_entry_blockers = self._o, self._b
        super().tearDown()

    def test_intervention_never_goes_straight_to_running(self):
        k = Kit()
        req = SimpleNamespace(app=k.app)
        asyncio.run(api.farmer_settings(req, {"margin_usdt": 50, "leverage": 3, "max_positions": 2}))
        asyncio.run(api.farmer_start(req))
        k.orch.tick(k.rig.now)
        protection, placed = live_kf(k.rig.ex), len(k.rig.ex.placed)
        k.orch._intervene("test")
        status = asyncio.run(api.farmer_status(req))
        self.assertEqual(status["state"], o.INTERVENTION)
        k.orch.tick(k.rig.now + 1000)
        self.assertEqual((len(k.rig.ex.placed), live_kf(k.rig.ex)), (placed, protection))      # yeni giriş yok, koruma durur
        with self.assertRaises(HTTPException):
            asyncio.run(api.farmer_start(req))                                  # INTERVENTION → start reddedilir
        acked = asyncio.run(api.farmer_acknowledge(req))
        self.assertNotEqual(acked["state"], o.RUNNING)
        self.assertEqual(acked["state"], o.OFF)                                 # INTERVENTION → ACK → OFF
        self.assertEqual(asyncio.run(api.farmer_start(req))["state"], o.RUNNING)       # ayrı, açık START

    def test_acknowledge_with_unresolved_problem_stays_in_intervention(self):
        k = Kit()
        req = SimpleNamespace(app=k.app)
        asyncio.run(api.farmer_settings(req, {"margin_usdt": 50, "leverage": 3, "max_positions": 2}))
        asyncio.run(api.farmer_start(req))
        k.orch.tick(k.rig.now)
        k.orch._intervene("test")
        k.rig.ex.fail_reads = True; k.rig.source.fail = True                    # borsa okunamıyor: temiz doğrulama yok
        try:
            out = asyncio.run(api.farmer_acknowledge(req))
        except HTTPException as exc:
            out = {"state": o.INTERVENTION, "code": exc.detail}
        self.assertEqual(out["state"], o.INTERVENTION)
        self.assertEqual(k.orch.base, o.INTERVENTION)


@unittest.skipUnless(HAS_FASTAPI, "fastapi yok")
class TestApiEndToEnd(Base):
    """settings → GET durum → yeniden başlat → durum → start; hepsi gerçek işleyici + gerçek çalışma zamanı + gerçek store."""
    def setUp(self):
        super().setUp()
        self._o, self._b = api._owner, api.live_entry_blockers
        self.who = {"id": OWNER, "role": "OWNER"}
        api._owner = lambda request: self.who
        api.live_entry_blockers = lambda: []

    def tearDown(self):
        api._owner, api.live_entry_blockers = self._o, self._b
        super().tearDown()

    def test_settings_survive_restart_and_start_runs(self):
        k = Kit()
        req = SimpleNamespace(app=k.app)
        body = {"margin_usdt": 25, "leverage": 2, "target_usdt": 0.8, "max_loss_usdt": 1.2, "max_positions": 3, "min_score": 93}
        asyncio.run(api.farmer_settings(req, body))
        first = asyncio.run(api.farmer_status(req))
        k.restart()
        req = SimpleNamespace(app=k.app)
        again = asyncio.run(api.farmer_status(req))
        self.assertEqual(again["config"], first["config"])                      # restart varsayılana DÖNMEZ
        self.assertTrue(again["configured"])
        self.assertEqual(again["config"]["margin_usdt"], 25.0)
        self.assertEqual(asyncio.run(api.farmer_start(req))["state"], o.RUNNING)

    def test_two_accounts_see_and_change_only_their_own(self):
        a = Kit()
        b = Kit()
        self.who = {"id": "uA", "role": "OWNER"}
        asyncio.run(api.farmer_settings(SimpleNamespace(app=a.app), {"margin_usdt": 25, "leverage": 2, "max_positions": 2}))
        asyncio.run(api.farmer_start(SimpleNamespace(app=a.app)))
        a.orch.tick(a.rig.now)
        self.who = {"id": "uB", "role": "OWNER"}
        status_b = asyncio.run(api.farmer_status(SimpleNamespace(app=b.app)))
        self.assertEqual((status_b["state"], status_b["positions"]), (o.OFF, []))
        self.assertFalse(status_b["configured"])
        # B, A'nın çalışma zamanına erişemez
        for call in (api.farmer_start, api.farmer_stop_bot, api.farmer_emergency_close, api.farmer_acknowledge):
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(call(SimpleNamespace(app=a.app)))
            self.assertEqual(cm.exception.status_code, 403)
        self.assertFalse(asyncio.run(api.farmer_status(SimpleNamespace(app=a.app)))["available"])      # A'nın durumu B'ye sızmaz
        self.assertEqual(a.orch.base, o.RUNNING)                                # A etkilenmedi
        self.assertEqual(b.rig.ex.placed, [])


class TestEmergencyCloseScope(Base):
    def foreign(self, k):
        ex = k.rig.ex
        for sym, cid in (("GRIDUSDT", "kg-g1-L01E0"), ("DCAUSDT", "kd-d1-L01E0"), ("MANUSDT", None), ("ORIGUSDT", None)):
            ex.prices[sym] = 100.0
            ex.set_position(sym, 2.0)
            if cid:
                ex.place_order(client_id=cid, symbol=sym, side="BUY", type="LIMIT", qty=1.0, price=50.0)
        return {s: ex.position(s) for s in ("GRIDUSDT", "DCAUSDT", "MANUSDT", "ORIGUSDT")}

    def test_closes_only_farmer_positions_and_releases_slots_after_flat(self):
        k = Kit()
        k.go()
        before = self.foreign(k)
        report = k.orch.emergency_close()
        self.assertEqual((len(report["closed"]), report["failed"], report["unowned"]), (1, [], []))
        self.assertEqual(k.rig.ex.position("AAAUSDT"), 0.0)
        self.assertEqual(live_kf(k.rig.ex), [])                                 # yalnız sahipli kf- koruma temizlendi
        self.assertEqual({s: k.rig.ex.position(s) for s in before}, before)     # Grid/DCA/Manuel/Original V2 pozisyonları aynı
        self.assertEqual(sorted(c for c in k.rig.ex.orders if c.startswith(("kg-", "kd-"))), ["kd-d1-L01E0", "kg-g1-L01E0"])
        self.assertEqual(k.orch.guard.slots().used, 0)

    def test_unowned_kf_order_is_not_deleted_and_goes_to_intervention(self):
        k = Kit()
        k.go()
        k.rig.ex.place_order(client_id="kf-ghost-L01T0", symbol="AAAUSDT", side="SELL", type="LIMIT", qty=1.0, price=500.0)
        report = k.orch.emergency_close()
        self.assertEqual(report["unowned"], ["kf-ghost-L01T0"])
        self.assertEqual(k.orch.state, o.INTERVENTION)
        self.assertIn("kf-ghost-L01T0", live_kf(k.rig.ex))

    def test_flat_not_verified_keeps_slot(self):
        k = Kit()
        k.go()
        k.rig.ex.fail_reads = True
        report = k.orch.emergency_close()
        self.assertTrue(report["failed"])
        self.assertEqual(k.orch.state, o.INTERVENTION)
        self.assertEqual(k.orch.guard.slots().used, 1)                          # flat doğrulanamadı: slot BIRAKILMAZ


class TestProtectionReinstallThroughRuntime(Base):
    def test_missing_stop_is_reinstalled_first(self):
        k = Kit()
        k.go()
        ex = k.rig.ex
        for cid in live_kf(ex):
            ex.cancel_order("AAAUSDT", cid)
        n = len(ex.placed)
        k.restart()
        k.runtime.prepare_start()
        new = [p[1] for p in ex.placed[n:]]
        self.assertEqual(new[:1], ["STOP_MARKET"])                              # önce STOP (C3 sözleşmesi)
        self.assertEqual(len(live_kf(ex)), 2)
        self.assertTrue(k.orch.recovered)

    def test_missing_tp_only_is_reinstalled_after_stop_is_confirmed(self):
        k = Kit()
        k.go()
        ex = k.rig.ex
        stops, tps = stops_and_tps(ex)
        ex.cancel_order("AAAUSDT", tps[0])
        k.restart()
        k.runtime.prepare_start()
        stops2, tps2 = stops_and_tps(ex)
        self.assertEqual((stops2, len(tps2)), (stops, 1))                       # STOP olduğu gibi, TP geri geldi
        self.assertTrue(k.orch.recovered)


class TestMissingFarmerReservationIsFailClosed(Base):
    """Rezervasyon satırı yokken pozisyon açık: risk/slot/doluluk GÜVENLİ kalmalı (fail-open değil)."""
    def setUp(self):
        super().setUp()

    def kit(self):
        from app import farmer_view
        k = Kit(rig=Rig(table=dict(TWO), store=FarmerStore(":memory:", check_same_thread=False)))
        k.runtime.apply_settings(farmer_view.parse_settings({"margin_usdt": 50, "max_positions": 1}))      # kapasite 1 (gerçek store'a yazılır)
        return k

    def test_risk_slot_and_occupancy_survive_without_reservation(self):
        k = self.kit()
        k.go()
        self.assertEqual(len(k.orch.engines), 1)
        ar.get_store().db.execute("DELETE FROM reservations")                  # rezervasyon kaybı
        self.assertEqual(rows_all(), [])
        k.restart()
        k.runtime.prepare_start()
        self.assertTrue(k.orch.recovered)
        risk = k.runtime.reserved_risk(OWNER)
        self.assertGreater(risk["worst_loss_usdt"], 0)                          # risk muhasebesi sıfırlanmadı (çalışma zamanı görünümü)
        self.assertEqual(risk["symbols"], [next(iter(k.orch.engines.values())).sym])
        self.assertTrue(k.runtime.symbol_busy(risk["symbols"][0]))              # başka bot aynı sembole giremez
        self.assertEqual(k.orch.guard.slots().used, 1)                          # slot dolu
        self.assertEqual(k.orch.guard.slots().open, 1)

    def test_second_entry_cannot_use_the_slot_and_restart_sends_no_duplicate(self):
        k = self.kit()
        k.go()
        ar.get_store().db.execute("DELETE FROM reservations")
        k.restart()
        k.runtime.prepare_start()
        ex = k.rig.ex
        placed = len(ex.placed)
        k.orch.start()
        for _ in range(3):
            k.rig.now = (k.rig.now // 900_000 + 1) * 900_000 + 5000
            k.rig.stream.advance(k.rig.now)
            k.orch.tick(k.rig.now)
        self.assertEqual(len(ex.placed), placed)                               # tek slot dolu: ikinci giriş YOK, çift emir YOK
        self.assertEqual(len(k.orch.engines), 1)
        self.assertEqual(len(live_kf(ex)), 2)
        self.assertEqual([r for r in rows_all() if r["kind"] != "FARMER"], [])


class TestKindsFilterMutation(Base):
    """`kinds` süzgeci kaldırılırsa yabancı rezervasyon testi DÜŞER (süzgeç mevcutken geçer)."""
    def _foreign_untouched(self, patched):
        from app import v25_execution as live
        source = Path(live.__file__).read_text()
        self.assertIn('if kinds and row.get("kind") not in kinds:', source)
        k = Kit()
        tokens = {}
        for idx, kind in enumerate(("GRID", "DCA", "MANUAL", "ORIGINAL_V2")):
            token = ar.reserve(OWNER, kind, f"S{idx}USDT", 0.0, ttl=300.0)
            ar.mark_submitted(token, f"{kind.lower()}-x{idx}-L01E0")
            tokens[kind] = token
        k.restart()
        k.runtime.prepare_start()
        return all(ar.get(t)["state"] == ar.SUBMITTED for t in tokens.values())

    def test_with_filter_foreign_rows_untouched(self):
        self.assertTrue(self._foreign_untouched(False))

    def test_without_filter_foreign_rows_would_be_resolved(self):
        from app import v25_execution as live
        original = live.reconcile_account_reservations

        async def unfiltered(client, state, owner, snapshot=None, kinds=None):
            return await original(client, state, owner, snapshot, None)         # süzgeç YOK (mutasyon)
        live.reconcile_account_reservations = unfiltered
        try:
            self.assertFalse(self._foreign_untouched(True))
        finally:
            live.reconcile_account_reservations = original


class TestLifespanWiring(unittest.TestCase):
    """Gerçek FastAPI yığını bu ortamda yok (BLOCKED); lifespan sıralaması kaynaktan doğrulanır (STATİK, çalıştırma değil)."""
    def test_install_before_yield_and_uninstall_before_v25_shutdown(self):
        text = (Path(__file__).parents[1] / "app" / "main.py").read_text()
        install, uninstall = text.index("farmer_runtime.install(app)"), text.index("farmer_runtime.uninstall(app)")
        yield_at, v25_shutdown = text.index("yield", install), text.index("shutdown_v25_execution(app)")
        self.assertLess(install, yield_at)
        self.assertLess(yield_at, uninstall)
        self.assertLess(uninstall, v25_shutdown)

    def test_live_gates_default_off_in_repo_config(self):
        root = Path(__file__).parents[2]
        for path in list(root.glob("*.env*")) + list(root.glob("backend/*.env*")) + list(root.glob("**/docker-compose*.y*ml")):
            if "node_modules" in str(path):
                continue
            text = path.read_text(errors="ignore")
            for flag in ("KAISTRADE_FARMER_LIVE", "KAISTRADE_FARMER_TPM_VERIFIED"):
                for line in text.splitlines():
                    if line.strip().startswith(flag):
                        self.assertNotRegex(line.split("=", 1)[-1].strip().strip("\"'").lower(), r"^(1|true|on|yes)$", f"{path}: {line}")


class TestOffAndLiveGates(Base):
    def test_default_gates_keep_entries_blocked_even_when_running(self):
        k = Kit(env={})                                                         # ortam: KAISTRADE_FARMER_LIVE / TPM_VERIFIED yok
        k.go()
        self.assertEqual(k.rig.ex.placed, [])                                   # canlı kapılar KAPALI: emir YOK
        self.assertEqual(rows_all(), [])
        self.assertEqual(k.orch.state, o.ENTRY_PAUSED_SAFETY)

    def test_start_up_without_credentials_places_nothing(self):
        k = Kit()
        k.client.fail = True
        k.rig.source.fail = True
        k.runtime.prepare_start()
        self.assertEqual(k.rig.ex.placed, [])


if __name__ == "__main__":
    unittest.main()
