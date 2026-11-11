"""DCA güvenlik testleri — SAHTE borsa (gerçek anahtar/para/ağ yok).
Kapsam: borsa ortalaması (source of truth), koruma sırası ve stop güvenliği, yanıt kaybı, çökme/yeniden başlatma, defter↔borsa uyuşmazlığı, tur kuralları.
Çalıştırma: python -m unittest tests.test_dca_safety -v
"""
from __future__ import annotations

import ast
import os
from unittest import mock
import math
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.dca_engine.engine import CYCLE_DONE, DcaEngine, dca_client_order_id, spec_from_plan  # noqa: E402
from app.dca_engine.plan import prepare_dca_plan  # noqa: E402
from app.grid_engine.engine import BLOCKED, COMPLETED, EMERGENCY_CLOSED, INTERVENTION_REQUIRED, RUNNING, STOPPED  # noqa: E402
from app.grid_engine.fake_exchange import FakeExchange  # noqa: E402
from app.grid_engine.ports import (CANCELED, EPS, LONG, NEW, PARTIAL, SHORT, ExchangeRejected, ExchangeTimeout,  # noqa: E402
                                   ExchangeUnreachable)
from app.grid_engine.store import GridStore  # noqa: E402

SYM = "ETHUSDT"
RULES = {"tick": 0.01, "step": 0.001, "min_qty": 0.001, "min_notional": 5}
POLICY = {"max_leverage": 5, "max_loss_per_trade": 50, "daily_loss_limit": 100, "max_total_exposure_usdt": 5000, "max_positions": 5, "max_stop_distance_pct": 5}


class Crash(BaseException):
    """Süreç ölümü simülasyonu: motorun yakaladığı borsa hatalarından değildir, doğrudan yukarı çıkar."""


class HidingExchange(FakeExchange):
    """Emri borsada VAR ama açık emir listesinde henüz görünmüyor (gecikmeli tutarlılık): durumu yalnız get_order bilir."""
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.hidden: set[str] = set()

    def open_orders(self, symbol):
        return [o for o in super().open_orders(symbol) if o.client_id not in self.hidden]


def plan_for(direction=LONG, price=100.0, **kw):
    args = dict(direction=direction, price=price, base_margin_usdt=10.0, safety_orders=3, step_pct=1.5, volume_multiplier=1.5,
                take_profit_pct=1.0, stop_gap_pct=1.5, leverage=3, rules=RULES, policy=POLICY)
    args.update(kw)
    return prepare_dca_plan(**args)


class Base(unittest.TestCase):
    direction = LONG

    def setUp(self):
        self.ex = FakeExchange(tick=0.01, step=0.001)
        self.ex.prices[SYM] = 100.0
        self.store = GridStore()
        self.plan = plan_for(self.direction)
        self.spec = spec_from_plan("d1", SYM, self.plan, tick=0.01)

    def engine(self, spec=None, **kw):
        return DcaEngine(self.ex, self.store, spec or self.spec, **kw)

    def live(self, role=None):
        out = [o for o in self.ex.orders.values() if o.status in (NEW, PARTIAL)]
        if role == "S":
            return [o for o in out if o.type == "STOP_MARKET"]
        if role == "T":
            return [o for o in out if o.reduce_only and o.type == "LIMIT"]
        if role == "E":
            return [o for o in out if not o.reduce_only and o.type == "LIMIT"]
        return out

    def cid(self, level, role, rev=0):
        return dca_client_order_id("d1", self.direction, level, role, rev)

    def stage_price(self, i):
        return self.plan["stages"][i]["entry"]

    def place_calls(self):
        return sum(1 for method, _ in self.ex.calls if method == "place_order")

    def stop_cover(self):
        return sum(o.qty - o.filled for o in self.live("S"))

    def assert_consistent(self, label=""):
        """Hiçbir kimlik iki kez borsaya kabul edilmedi ve açık pozisyonun TAMAMI doğrulanmış stopla korunuyor."""
        for key, count in self.ex.placed_count.items():
            self.assertLessEqual(count, 1, f"{label}: {key} {count} kez kabul edildi (çift emir)")
        net = abs(self.ex.positions.get(SYM, 0.0))
        if net > EPS:
            self.assertAlmostEqual(self.stop_cover(), net, msg=f"{label}: pozisyon {net}, doğrulanmış stop {self.stop_cover()}")

    def expected_tp(self, avg):
        if self.direction == LONG:
            return math.ceil(avg * 1.01 / 0.01 - 1e-9) * 0.01
        return math.floor(avg * 0.99 / 0.01 + 1e-9) * 0.01

    def block(self, pattern, error=lambda: ExchangeTimeout("test: yol kapalı"), methods=("place_order",)):
        """Kimliği `pattern`'e uyan emirlerin `methods` çağrılarını hata ile düşürür. Dönen sözlükte on=False yapılınca yol açılır."""
        flag = {"on": True}

        def hook(method, info):
            if flag["on"] and method in methods and re.search(pattern, info.get("client_id", "")):
                raise error()
        self.ex.hooks.append(hook)
        return flag

    def grow_to_first_safety(self, eng):
        """Taban + 1. ek emir dolu pozisyon (3. ve 4. kademe hâlâ bekliyor)."""
        eng.start()
        self.ex.set_price(SYM, self.stage_price(1))
        eng.tick()
        self.assertEqual(len(self.live("E")), 2)
        return abs(self.ex.positions[SYM])


# ---------------------------------------------------------------------------------------------------------------------------
# 1) Borsadaki GERÇEK ortalama giriş = hedefin kaynağı
# ---------------------------------------------------------------------------------------------------------------------------
class AverageTruthMixin:
    # Bu testler kısmi hedef dolumundan sonra yeni ek alımı inceler; 'kârı koru' (ek alımı durdurur) burada kapalı tutulur.
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"KAISTRADE_DCA_BREAKEVEN": "0"})
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()

    def test_partial_tp_then_new_dca_uses_binance_average(self):
        """BASE → hedefin bir kısmı doldu → pozisyon küçüldü → yeni DCA doldu → ortalama değişti → hedef BORSA ortalamasına göre yeniden hesaplanır."""
        eng = self.engine()
        eng.start()
        self.ex.set_price(SYM, self.stage_price(1))
        eng.tick()
        self.ex.partial_fill(self.live("T")[0].client_id, 0.2)           # Binance kısmi kapanışta entryPrice'ı değiştirmez
        eng.tick()
        self.assertEqual(eng.status, RUNNING)
        entry_after_partial = self.ex.entries[SYM]
        self.ex.set_price(SYM, self.stage_price(2))                      # yeni ek emir doldu: borsa ortalaması küçülen pozisyonla ağırlıklanır
        eng.tick()
        self.assertEqual(eng.status, RUNNING)
        real = self.ex.position_info(SYM).entry_price
        self.assertNotAlmostEqual(real, entry_after_partial, places=3)
        (tp,) = self.live("T")
        self.assertAlmostEqual(tp.price, self.expected_tp(real), delta=0.0051)       # hedef BORSA ortalamasından
        self.assertAlmostEqual(tp.qty, abs(self.ex.positions[SYM]))
        local_tp = eng.tp_price()                                                    # defterin tahmini (eski davranış)
        self.assertGreater(abs(local_tp - tp.price), 0.05)                           # gerçekten farklıydı ve KULLANILMADI
        self.assertAlmostEqual(self.stop_cover(), abs(self.ex.positions[SYM]))
        self.assert_consistent("partial-tp-sonrasi-dca")

    def test_falls_back_to_local_average_only_when_binance_info_is_unavailable(self):
        eng = self.engine()
        eng.start()
        self.ex.position_info_down = True
        self.ex.set_price(SYM, self.stage_price(1))
        eng.tick()
        (tp,) = self.live("T")
        self.assertAlmostEqual(tp.price, self.plan["stages"][1]["tp_price"], delta=0.02)    # yerel plan hesabı (yedek)
        self.assertTrue(any("geçici olarak" in text for _, text in self.store.alerts("d1")))   # sessiz değil: uyarı yazıldı
        self.ex.position_info_down = False
        eng.tick()
        self.assertEqual(eng.status, RUNNING)


class TestAverageTruthLong(AverageTruthMixin, Base):
    direction = LONG


class TestAverageTruthShort(AverageTruthMixin, Base):
    direction = SHORT


class TestAverageMismatch(Base):
    def test_mismatch_without_partial_exit_requires_intervention(self):
        eng = self.engine()
        eng.start()
        self.ex.set_price(SYM, self.stage_price(1))
        eng.tick()
        placed = dict(self.ex.placed_count)
        self.ex.entries[SYM] = 90.0                                       # borsa ortalaması defterle anlamlı biçimde uyuşmuyor
        eng.tick()
        self.assertEqual(eng.status, INTERVENTION_REQUIRED)
        self.assertEqual(len(self.live("E")), 0)                          # yeni girişler iptal
        self.assertAlmostEqual(self.stop_cover(), abs(self.ex.positions[SYM]))   # stop yerinde
        self.assertEqual(len(self.live("T")), 1)                          # mevcut hedef silinmedi
        eng.tick()
        self.assertEqual(self.ex.placed_count, placed)                    # yanlış hedef / yeni emir üretilmedi

    def test_single_base_fill_with_different_exchange_average_adopts_exchange_price(self):
        """AGT olayı: tek dolum (taban piyasa emri); defterdeki fiyat planlanan tahminse borsanın gerçek ortalaması benimsenir, bot kilitlenmez."""
        eng = self.engine()
        eng.start()
        self.ex.entries[SYM] = self.plan["stages"][0]["entry"] * 1.01     # %1 fark: sınırın (%0,5) üstünde
        eng.tick()
        eng.tick()
        self.assertEqual(eng.status, RUNNING)
        (tp,) = self.live("T")
        self.assertAlmostEqual(tp.price, self.expected_tp(self.ex.entries[SYM]), delta=0.0051)   # hedef borsanın gerçek ortalamasından

    def test_average_outside_fill_range_after_partial_exit_requires_intervention(self):
        eng = self.engine()
        eng.start()
        self.ex.set_price(SYM, self.stage_price(1))
        eng.tick()
        self.ex.partial_fill(self.live("T")[0].client_id, 0.2)
        eng.tick()
        self.assertEqual(eng.status, RUNNING)                             # kısmi çıkış sonrası meşru sapma: müdahale YOK
        self.ex.entries[SYM] = 50.0                                       # ama dolum aralığının çok dışında
        eng.tick()
        self.assertEqual(eng.status, INTERVENTION_REQUIRED)
        self.assertEqual(len(self.live("E")), 0)

    def test_exchange_liquidation_beyond_stop_requires_intervention(self):
        eng = self.engine()
        eng.start()
        self.ex.liq_prices[SYM] = self.plan["stop_price"] + 2.0           # borsa: likidasyon stopun ÖNÜNDE (stop likidasyondan sonra)
        eng.tick()
        self.assertEqual(eng.status, INTERVENTION_REQUIRED)
        self.assertEqual(len(self.live("E")), 0)
        self.assertEqual(len(self.live("S")), 1)

    def test_exchange_liquidation_far_beyond_stop_is_fine(self):
        eng = self.engine()
        eng.start()
        self.ex.liq_prices[SYM] = self.plan["stop_price"] * 0.7
        eng.tick()
        self.assertEqual(eng.status, RUNNING)


# ---------------------------------------------------------------------------------------------------------------------------
# 2) Koruma sırası ve stop güvenliği
# ---------------------------------------------------------------------------------------------------------------------------
class TestProtectionOrder(Base):
    def test_protection_is_set_before_any_safety_order(self):
        eng = self.engine()
        eng.start()
        calls = [cid for method, cid in self.ex.calls if method == "place_order"]
        index = lambda pred: next(i for i, c in enumerate(calls) if pred(c))     # noqa: E731
        base, stop = index(lambda c: c == self.cid(1, "E")), index(lambda c: c.endswith("L00S0"))
        target, first_safety = index(lambda c: c.endswith("L00T0")), index(lambda c: re.search(r"L0[2-9]E", c) is not None)
        self.assertLess(base, stop)
        self.assertLess(stop, first_safety)
        self.assertLess(target, first_safety)

    def test_safeties_not_sent_while_stop_is_unverified(self):
        down = {"on": True}

        def hook(method, info):
            if down["on"] and method == "place_order" and re.search(r"S\d+$", info.get("client_id", "")):
                raise ExchangeTimeout("stop yolu kapalı")
        self.ex.hooks.append(hook)
        eng = self.engine()
        eng.start()
        self.assertGreater(abs(self.ex.positions[SYM]), 0)                # taban doldu, stop henüz doğrulanmadı
        self.assertEqual(len(self.live("S")), 0)
        self.assertEqual(len(self.live("E")), 0)                          # → hiçbir ek emir açılmadı
        for level in (2, 3, 4):
            self.assertEqual(self.ex.placed_count.get(self.cid(level, "E"), 0), 0)
        down["on"] = False
        for _ in range(4):
            eng.tick()
        self.assertEqual(eng.status, RUNNING)
        self.assertEqual(len(self.live("S")), 1)                          # stop doğrulandıktan SONRA ek emirler açıldı
        self.assertEqual(len(self.live("E")), 3)
        self.assert_consistent("stop-sonrasi-ek-emirler")

    def test_stop_never_verifiable_closes_position_instead_of_waiting_for_user(self):
        def hook(method, info):
            if method == "place_order" and re.search(r"S\d+$", info.get("client_id", "")):
                raise ExchangeTimeout("stop yolu kapalı")
        self.ex.hooks.append(hook)
        eng = self.engine()
        eng.start()
        for _ in range(6):
            eng.tick()
        self.assertEqual(eng.status, EMERGENCY_CLOSED)                    # kullanıcıyı beklemedi: pozisyon güvenle kapatıldı
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)
        self.assertEqual(len(self.live()), 0)
        for level in (2, 3, 4):
            self.assertEqual(self.ex.placed_count.get(self.cid(level, "E"), 0), 0)     # bu sürede hiçbir ek emir açılmadı
        self.assertEqual(self.ex.placed_count[self.cid(0, "C")], 1)       # tek kapanış emri

    def test_single_slow_tick_does_not_lock_the_bot(self):
        down = {"on": True}

        def hook(method, info):
            if down["on"] and method == "place_order" and re.search(r"S\d+$", info.get("client_id", "")):
                raise ExchangeTimeout("tek seferlik")
        self.ex.hooks.append(hook)
        eng = self.engine()
        eng.start()
        down["on"] = False
        for _ in range(5):
            eng.tick()
        self.assertEqual(eng.status, RUNNING)


class TestStopUpdateSafety(Base):
    def test_stop_update_rejected_after_dca_fill_cancels_dca_keeps_old_stop_then_recovers(self):
        eng = self.engine()
        eng.start()
        (old,) = self.live("S")
        self.ex.set_price(SYM, self.stage_price(1))                        # DCA doldu: stop büyümeli
        self.ex.reject_next = [(-4164, "ret")] * 3                         # ama yeni stop bu kontrolde reddedilir (yeniden denemeler dahil)
        eng.tick()
        self.assertEqual(eng.status, RUNNING)                              # kullanıcıyı beklemiyor: yeniden deniyor
        self.assertEqual(len(self.live("E")), 0)                           # korumasızken bekleyen ek emirler iptal edildi
        self.assertEqual([o.client_id for o in self.live("S")][:1], [old.client_id])   # eski stop İPTAL EDİLMEDİ
        for _ in range(3):                                                 # stop bu sefer kurulur → ek emirler geri kurulur
            eng.tick()
        self.assertEqual(eng.status, RUNNING)
        net = abs(self.ex.positions[SYM])
        self.assertAlmostEqual(self.stop_cover(), net)
        self.assertEqual(len(self.live("E")), 2)                           # 3. ve 4. kademe yeniden kuruldu
        self.assert_consistent("stop-reddi-sonrasi")

    def test_old_stop_not_cancelled_until_new_stop_is_verified(self):
        eng = self.engine()
        eng.start()
        (old,) = self.live("S")
        down = {"on": True}

        def hook(method, info):
            if down["on"] and method == "place_order" and re.search(r"S[1-9]\d*$", info.get("client_id", "")):
                raise ExchangeTimeout("yeni stop doğrulanamadı")
        self.ex.hooks.append(hook)
        self.ex.set_price(SYM, self.stage_price(1))
        eng.tick()
        self.assertGreater(abs(self.ex.positions[SYM]), self.plan["stages"][0]["qty"])
        self.assertEqual(self.ex.orders[old.client_id].status, NEW)        # yeni stop doğrulanmadı → eskisi yerinde
        self.assertEqual(eng.status, RUNNING)

    def test_new_stop_covers_whole_position_then_old_is_cancelled(self):
        eng = self.engine()
        eng.start()
        (old,) = self.live("S")
        self.ex.set_price(SYM, self.stage_price(1))
        eng.tick()
        net = abs(self.ex.positions[SYM])
        (stop,) = self.live("S")
        self.assertNotEqual(stop.client_id, old.client_id)
        self.assertAlmostEqual(stop.qty, net)
        self.assertEqual(self.ex.orders[old.client_id].status, CANCELED)

    def test_price_already_past_stop_closes_position_and_opens_nothing(self):
        eng = self.engine()
        eng.start()
        self.ex.set_price(SYM, self.stage_price(1))
        eng.tick()
        stop_id = self.live("S")[0].client_id
        self.ex.cancel_order(SYM, stop_id)
        self.store.set_order(stop_id, status=CANCELED)
        self.ex.prices[SYM] = self.plan["stop_price"] - 1
        before = self.place_calls()
        eng.tick()
        self.assertEqual(eng.status, STOPPED)
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)
        self.assertEqual(len(self.live()), 0)
        self.assertLessEqual(self.place_calls() - before, 2)               # yalnız yeni stop denemesi + kapanış; yeni ek emir yok


# ---------------------------------------------------------------------------------------------------------------------------
# 2b) Doğrulanmış stop yokken HİÇBİR (mevcut ya da yeni) DCA emri pozisyonu büyütemez
# ---------------------------------------------------------------------------------------------------------------------------
class TestNoGrowthWithoutVerifiedStop(Base):
    def test_stop_update_fails_pending_dca_orders_are_cancelled_and_verified(self):
        eng = self.engine()
        net = self.grow_to_first_safety(eng)
        (old,) = self.live("S")
        self.block(r"S[1-9]\d*$")                                          # yeni (büyütülmüş) stop kurulamıyor
        self.ex.set_price(SYM, self.stage_price(1) + 0.4)                  # fiyat dolum seviyesinin biraz üstünde; tetik yok
        self.ex.partial_fill(self.cid(3, "E"), 0.0) if False else None
        # bir kademe daha doldur ki stop güncellemesi gereksin: 2. ek emir
        self.ex.set_price(SYM, self.stage_price(2))
        eng.tick()
        self.assertEqual(len(self.live("E")), 0)                           # kalan ek emirler iptal edildi...
        for level in (3, 4):
            self.assertIn(self.store.order(self.cid(level, "E"))["status"], (CANCELED, "FILLED"))   # ...ve defter de borsayla aynı
        open_rows = [o for o in self.ex.orders.values() if o.status in (NEW, PARTIAL) and not o.reduce_only and o.type == "LIMIT"]
        self.assertEqual(open_rows, [])                                    # borsada hiçbiri aktif değil
        self.assertEqual(self.ex.orders[old.client_id].status, NEW)        # eski stop yerinde
        self.assertEqual(eng.status, RUNNING)

    def test_cancel_response_lost_status_is_queried_from_exchange(self):
        eng = self.engine()
        self.grow_to_first_safety(eng)
        self.ex.lose_cancel_response_for |= {self.cid(3, "E"), self.cid(4, "E")}   # iptal işlendi ama yanıt kayboldu
        self.block(r"S[1-9]\d*$")
        self.ex.set_price(SYM, self.stage_price(1) + 0.3)
        self.ex.set_position(SYM, self.ex.positions[SYM])                  # (no-op) pozisyon değişmedi
        # stop güncellemesinin gerekmesi için pozisyonu büyüt: 2. ek emir dolsun, sonra yalnız 4. kademe bekler
        self.ex.set_price(SYM, self.stage_price(2))
        self.ex.lose_cancel_response_for = {self.cid(4, "E")}
        calls_before = len(self.ex.calls)
        eng.tick()
        asked = [cid for method, cid in self.ex.calls[calls_before:] if method == "get_order"]
        self.assertIn(self.cid(4, "E"), asked)                             # iptal yanıtı kayıp → emrin durumu borsadan sorulur
        self.assertEqual(self.store.order(self.cid(4, "E"))["status"], CANCELED)
        self.assertEqual(len(self.live("E")), 0)
        self.assertEqual(eng.status, RUNNING)                              # doğrulandığı için kapatmaya/müdahaleye gerek yok
        self.assert_consistent_orders_only()

    def test_dca_price_reached_without_stop_does_not_grow_position(self):
        """STOP UPDATE başarısız → mevcut DCA limit emri açık → fiyat DCA seviyesine geliyor → DCA fill olmamalı."""
        eng = self.engine()
        self.grow_to_first_safety(eng)
        self.block(r"S[1-9]\d*$")
        self.ex.set_price(SYM, self.stage_price(2))                        # 2. ek emir dolar: stop güncellemesi başarısız
        eng.tick()
        net_after_fill = abs(self.ex.positions[SYM])
        self.assertEqual(len(self.live("E")), 0)                           # 4. kademe artık bekleyen emir değil
        self.ex.set_price(SYM, self.stage_price(3))                        # fiyat bir sonraki DCA seviyesine kadar iner
        eng.tick()
        self.assertAlmostEqual(abs(self.ex.positions[SYM]), net_after_fill)    # pozisyon BÜYÜMEDİ
        self.assertNotEqual(self.store.order(self.cid(4, "E"))["status"], "FILLED")

    def test_no_stop_and_dca_cancel_fails_closes_position_and_ends_in_intervention(self):
        eng = self.engine()
        self.grow_to_first_safety(eng)
        self.block(r"S[1-9]\d*$")                                          # stop kurulamıyor
        self.block(r"E\d+$", error=lambda: ExchangeRejected(-1000, "iptal edilemiyor"), methods=("cancel_order",))   # ek emir iptal edilemiyor
        self.ex.set_price(SYM, self.stage_price(2))
        eng.tick()
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)                # pozisyon piyasadan kapatıldı (daha fazla büyümesin)
        self.assertEqual(eng.status, INTERVENTION_REQUIRED)                # iptal edilemeyen emir kaldı → son güvenlik durumu
        self.assertTrue(any("iptal" in text for _, text in self.store.alerts("d1")))
        self.assertEqual(self.ex.placed_count[self.cid(0, "C")], 1)

    def test_intervention_is_last_resort_when_close_also_fails(self):
        eng = self.engine()
        self.grow_to_first_safety(eng)
        (old,) = self.live("S")
        self.block(r"S[1-9]\d*$")
        self.block(r"C\d+$", error=lambda: ExchangeRejected(-1000, "kapatılamıyor"))
        self.block(r"E\d+$", error=lambda: ExchangeRejected(-1000, "iptal edilemiyor"), methods=("cancel_order",))
        self.ex.set_price(SYM, self.stage_price(2))
        eng.tick()
        self.assertEqual(eng.status, INTERVENTION_REQUIRED)
        self.assertEqual(self.ex.orders[old.client_id].status, NEW)        # koruyucu stoplar iptal EDİLMEDİ
        self.assertGreater(abs(self.ex.positions[SYM]), 0)

    def test_stop_unverifiable_for_several_ticks_closes_without_duplicate_close_order(self):
        close_id = self.cid(0, "C")
        self.ex.lose_response_for.add(close_id)                            # piyasa kapanış emri işlenir ama yanıt kaybolur
        self.block(r"S\d+$")
        eng = self.engine()
        eng.start()
        for _ in range(8):
            eng.tick()
        self.assertEqual(eng.status, EMERGENCY_CLOSED)
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)
        self.assertEqual(self.ex.placed_count[close_id], 1)                # kapanış emri borsaya TEK kez kabul edildi
        self.assertEqual(len(self.store.orders("d1", "C")), 1)             # defterde de tek kapanış satırı

    def test_close_response_lost_and_status_unreadable_never_resends(self):
        close_id = self.cid(0, "C")
        self.ex.lose_response_for.add(close_id)
        self.block(r"S\d+$")
        flag = self.block(rf"^{re.escape(close_id)}$", error=lambda: ExchangeUnreachable("sorgu yok"), methods=("get_order",))
        eng = self.engine()
        eng.start()
        for _ in range(8):
            eng.tick()
        self.assertEqual(self.ex.placed_count[close_id], 1)                # durum sorulamadığı için tekrar GÖNDERİLMEDİ
        self.assertEqual(len(self.store.orders("d1", "C")), 1)
        self.assertEqual(eng.status, INTERVENTION_REQUIRED)                # doğrulanamadı → son güvenlik durumu (stoplar iptal edilmedi)
        flag["on"] = False
        self.assertEqual(self.ex.placed_count[close_id], 1)

    def test_normal_dca_flow_is_not_disturbed_by_the_guard(self):
        eng = self.engine()
        eng.start()
        for i in (1, 2, 3):
            self.ex.set_price(SYM, self.stage_price(i))
            eng.tick()
            self.assertEqual(eng.status, RUNNING)
            self.assertAlmostEqual(self.stop_cover(), abs(self.ex.positions[SYM]))
        self.assertEqual(self.store.order(self.cid(4, "E"))["status"], "FILLED")
        self.assert_consistent("normal-akis")

    def assert_consistent_orders_only(self):
        for key, count in self.ex.placed_count.items():
            self.assertLessEqual(count, 1, key)


class TestRestartWithOpenDcaAndStop(Base):
    def test_restart_with_unverified_stop_cancels_resting_dca_then_rearms_after_stop(self):
        eng = self.engine()
        self.grow_to_first_safety(eng)
        flag = self.block(r"S[1-9]\d*$")
        self.ex.set_price(SYM, self.stage_price(2))
        eng.tick()                                                         # stop güncellenemedi; kalan DCA iptal edildi
        net = abs(self.ex.positions[SYM])
        self.assertEqual(len(self.live("E")), 0)
        fresh = self.engine()                                              # süreç öldü: aynı defter, yeni motor
        ok, why = fresh.recover()
        self.assertFalse(ok, why)                                          # koruma doğrulanamadı → sistem bunu biliyor
        self.assertEqual(len(self.live("E")), 0)                           # yeniden başlatma bekleyen DCA'yı geri GETİRMEDİ
        self.ex.set_price(SYM, self.stage_price(3))
        fresh.tick()
        self.assertAlmostEqual(abs(self.ex.positions[SYM]), net)           # fiyat DCA'ya değse de pozisyon büyümedi
        flag["on"] = False
        self.ex.set_price(SYM, self.stage_price(1))                        # fiyat tekrar uygun tarafa döndü
        for _ in range(4):
            fresh.tick()
        self.assertEqual(fresh.status, RUNNING)
        self.assertAlmostEqual(self.stop_cover(), abs(self.ex.positions[SYM]))
        self.assertGreaterEqual(len(self.live("E")), 1)                    # stop doğrulandı → ek emirler geri kuruldu
        self.assert_consistent("yeniden-baslatma-sonrasi")

    def test_restart_after_stop_vanished_reinstates_stop_and_keeps_single_dca_set(self):
        eng = self.engine()
        self.grow_to_first_safety(eng)
        (stop,) = self.live("S")
        self.ex.cancel_order(SYM, stop.client_id)                          # stop borsada kayboldu (elle iptal), süreç de öldü
        fresh = self.engine()
        ok, why = fresh.recover()
        self.assertTrue(ok, why)
        self.assertAlmostEqual(self.stop_cover(), abs(self.ex.positions[SYM]))
        for _ in range(3):
            fresh.tick()
        self.assertEqual(len(self.live("E")), 2)                           # DCA emirleri iki kez kurulmadı
        self.assert_consistent("stop-kaybi-yeniden-baslatma")


class TestProtectiveOrdersGateCycle(Base):
    def recycle_spec(self, **kw):
        return spec_from_plan("d1", SYM, plan_for(recycle=True, **kw), tick=0.01)

    def test_safeties_wait_for_target_verification_in_new_cycle(self):
        spec = self.recycle_spec()
        eng = self.engine(spec)
        eng.start()
        tp = self.live("T")[0].price
        self.ex.set_price(SYM, tp)
        eng.tick()
        self.assertEqual(eng.status, CYCLE_DONE)
        flag = self.block(r"T\d+$")                                        # yeni turun hedefi doğrulanamıyor
        self.assertTrue(eng.begin_cycle(self.recycle_spec(price=tp)))
        for _ in range(3):
            eng.tick()
        self.assertGreater(abs(self.ex.positions[SYM]), 0)                 # yeni tur tabanı doldu
        self.assertEqual(len(self.live("S")), 1)                           # stop doğrulandı
        self.assertEqual(len(self.live("T")), 0)
        self.assertEqual(len(self.live("E")), 0)                           # ama hedef doğrulanmadan ek emir yok
        flag["on"] = False
        for _ in range(3):
            eng.tick()
        self.assertEqual(len(self.live("T")), 1)
        self.assertEqual(len(self.live("E")), 3)                           # tüm koruyucu emirler doğrulandı → tur devam etti
        self.assert_consistent("yeni-tur-koruma")

    def test_new_cycle_refused_while_old_protective_order_is_still_live(self):
        spec = self.recycle_spec()
        eng = self.engine(spec)
        eng.start()
        tp = self.live("T")[0].price
        self.block(r"S\d+$", error=lambda: ExchangeUnreachable("iptal yolu kapalı"), methods=("cancel_order",))
        self.ex.set_price(SYM, tp)
        eng.tick()
        self.assertEqual(eng.status, CYCLE_DONE)
        self.assertEqual(len(self.live("S")), 1)                           # eski stop borsada iptal edilemedi
        self.assertFalse(eng.begin_cycle(self.recycle_spec(price=tp)))     # → yeni tur BAŞLAMAZ
        self.assertEqual(eng.status, CYCLE_DONE)


# ---------------------------------------------------------------------------------------------------------------------------
# 3) Yanıt kaybı, bilinmeyen durum, defter ↔ borsa uyuşmazlığı
# ---------------------------------------------------------------------------------------------------------------------------
class TestOrderRecovery(Base):
    def test_lost_response_for_every_order_kind_never_duplicates(self):
        for level, role in ((1, "E"), (2, "E"), (3, "E"), (0, "S"), (0, "T")):
            self.ex.lose_response_for.add(self.cid(level, role))
        eng = self.engine()
        eng.start()
        for _ in range(6):
            eng.tick()
        self.assertEqual(eng.status, RUNNING)
        self.assertEqual(len(self.live("E")), 3)
        self.assertEqual(len(self.live("S")), 1)
        self.assertEqual(len(self.live("T")), 1)
        self.assert_consistent("yanit-kaybi")

    def test_unknown_status_waits_for_exchange_answer_then_resolves_without_duplicate(self):
        self.ex = HidingExchange(tick=0.01, step=0.001)
        self.ex.prices[SYM] = 100.0
        sid = self.cid(2, "E")
        self.ex.lose_response_for.add(sid)                                 # emir borsada, yanıt kayıp
        self.ex.hidden.add(sid)                                            # açık emir listesinde henüz yok
        down = {"on": True}

        def hook(method, info):
            if down["on"] and method == "get_order" and info.get("client_id") == sid:
                raise ExchangeUnreachable("sorgu yok")
        self.ex.hooks.append(hook)
        eng = self.engine()
        eng.start()
        self.assertEqual(self.store.order(sid)["status"], "UNKNOWN")       # E) emir durumu bilinmiyor
        before = self.place_calls()
        for _ in range(3):
            eng.tick()
        self.assertEqual(self.place_calls(), before)                       # borsaya sorulamadı → hiçbir emir gönderilmedi
        self.assertEqual(self.store.order(sid)["status"], "UNKNOWN")
        down["on"] = False
        self.ex.hidden.clear()
        eng.tick()
        self.assertEqual(self.store.order(sid)["status"], NEW)
        self.assertEqual(self.ex.placed_count[sid], 1)
        self.assert_consistent("bilinmeyen-durum")

    def test_order_that_never_reached_exchange_is_resent_once_after_confirmed_absence(self):
        sid = self.cid(2, "E")
        flag = {"on": True}

        def hook(method, info):
            if flag["on"] and method == "place_order" and info.get("client_id") == sid:
                raise ExchangeTimeout("emir hiç ulaşmadı")                 # borsa işlemeden zaman aşımı
        self.ex.hooks.append(hook)
        eng = self.engine()
        eng.start()
        self.assertEqual(self.store.order(sid)["status"], "UNKNOWN")
        flag["on"] = False
        for _ in range(4):
            eng.tick()
        self.assertEqual(self.ex.placed_count[sid], 1)                     # tam bir kez, aynı kimlikle
        self.assertEqual(self.store.order(sid)["status"], NEW)
        self.assert_consistent("hic-ulasmayan-emir")

    def test_exchange_order_missing_from_journal_stops_safely(self):
        eng = self.engine()
        eng.start()
        orphan = "kd-d1-L09E0"                                             # C) Binance'te var, defterde yok
        self.ex.place_order(client_id=orphan, symbol=SYM, side="BUY", type="LIMIT", qty=0.1, price=90.0)
        before = self.place_calls()
        eng.tick()
        self.assertEqual(eng.status, BLOCKED)
        self.assertEqual(self.place_calls(), before)                       # yeni emir gönderilmedi
        self.assertNotIn(orphan, [o.client_id for o in self.live()])       # bilinmeyen emir iptal edildi
        self.assertEqual(len(self.live("S")), 1)                           # koruyucu stop yerinde

    def test_journal_order_missing_on_exchange_stops_safely(self):
        eng = self.engine()
        eng.start()
        del self.ex.orders[self.cid(3, "E")]                               # D) defterde var, Binance'te yok
        before = self.place_calls()
        eng.tick()
        self.assertEqual(eng.status, BLOCKED)
        self.assertEqual(self.place_calls(), before)
        self.assertEqual(len(self.live("S")), 1)

    def test_position_mismatch_stops_without_new_orders(self):
        eng = self.engine()
        eng.start()
        self.ex.set_position(SYM, self.ex.positions[SYM] + 1.0)
        before = self.place_calls()
        eng.tick()
        self.assertEqual(eng.status, BLOCKED)
        self.assertEqual(self.place_calls(), before)


# ---------------------------------------------------------------------------------------------------------------------------
# 4) Yeniden başlatma / çökme: sürecin her aşamada ölmesi
# ---------------------------------------------------------------------------------------------------------------------------
class TestCrashRecovery(Base):
    def restart(self):
        fresh = self.engine()                                              # aynı defter, aynı borsa, yeni süreç
        ok, why = fresh.recover()
        self.assertTrue(ok, why)
        for _ in range(4):
            fresh.tick()
        return fresh

    def test_crash_before_each_order_is_sent(self):
        for name, victim in (("taban", self.cid(1, "E")), ("stop", self.cid(0, "S")), ("hedef", self.cid(0, "T")), ("ek emir", self.cid(2, "E"))):
            with self.subTest(crash_before=name):
                self.setUp()

                def hook(method, info, victim=victim):
                    if method == "place_order" and info.get("client_id") == victim:
                        raise Crash()
                self.ex.hooks.append(hook)
                with self.assertRaises(Crash):
                    self.engine().start()
                self.ex.hooks.clear()
                fresh = self.restart()
                self.assertEqual(fresh.status, RUNNING)
                self.assertAlmostEqual(self.ex.positions[SYM], self.plan["stages"][0]["qty"])
                self.assertEqual(len(self.live("S")), 1)
                self.assertEqual(len(self.live("T")), 1)
                self.assertEqual(len(self.live("E")), 3)
                self.assert_consistent(f"cokme-oncesi-{name}")

    def test_crash_after_order_accepted_but_before_journal_update(self):
        for name, victim in (("taban", self.cid(1, "E")), ("stop", self.cid(0, "S")), ("hedef", self.cid(0, "T")), ("ek emir", self.cid(2, "E"))):
            with self.subTest(crash_after=name):
                self.setUp()
                self.engine().start()
                self.store.set_order(victim, status="PENDING_SEND", filled=0.0, avg_price=0.0, misses=0)   # borsa aldı, defter güncellenmedi
                fresh = self.restart()
                self.assertEqual(fresh.status, RUNNING)                    # BLOCKED DEĞİL: emir kimlikle benimsendi
                self.assertEqual(len(self.live("S")), 1)
                self.assertEqual(len(self.live("T")), 1)
                self.assertEqual(len(self.live("E")), 3)
                self.assert_consistent(f"cokme-sonrasi-{name}")

    def test_crash_after_safety_fill_and_after_target_fill(self):
        eng = self.engine()
        eng.start()
        self.ex.set_price(SYM, self.stage_price(1))                        # DCA doldu, süreç öldü: stop/hedef eski miktarda
        fresh = self.restart()
        self.assertEqual(fresh.status, RUNNING)
        self.assert_consistent("ek-emir-dolumu-sonrasi")
        self.assertAlmostEqual(sum(o.qty - o.filled for o in self.live("T")), abs(self.ex.positions[SYM]))
        self.ex.set_price(SYM, self.live("T")[0].price)                    # hedef doldu, süreç öldü
        again = self.restart()
        self.assertEqual(again.status, COMPLETED)
        self.assertEqual(len(self.live()), 0)
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)
        self.assert_consistent("hedef-dolumu-sonrasi")

    def test_restart_with_foreign_open_order_in_journal_gap_stops_without_new_orders(self):
        eng = self.engine()
        eng.start()
        self.ex.place_order(client_id="kd-d1-L08E3", symbol=SYM, side="BUY", type="LIMIT", qty=0.1, price=90.0)
        fresh = self.engine()
        before = self.place_calls()
        ok, _ = fresh.recover()
        fresh.tick()
        self.assertFalse(ok)
        self.assertEqual(fresh.status, BLOCKED)
        self.assertEqual(self.place_calls(), before)


# ---------------------------------------------------------------------------------------------------------------------------
# 5) Tur kuralları
# ---------------------------------------------------------------------------------------------------------------------------
class TestCycleRules(Base):
    def recycle_spec(self, **kw):
        return spec_from_plan("d1", SYM, plan_for(recycle=True, **kw), tick=0.01)

    def test_no_new_cycle_after_stop(self):
        eng = self.engine(self.recycle_spec())
        eng.start()
        for i in (1, 2, 3):
            self.ex.set_price(SYM, self.stage_price(i))
            eng.tick()
        self.ex.set_price(SYM, self.plan["stop_price"])
        eng.tick()
        self.assertEqual(eng.status, STOPPED)
        placed = dict(self.ex.placed_count)
        self.assertFalse(eng.begin_cycle(self.recycle_spec()))
        eng.tick()
        self.assertEqual(eng.status, STOPPED)
        self.assertEqual(self.ex.placed_count, placed)
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)

    def test_recycle_starts_new_cycle_only_when_exchange_is_clean(self):
        eng = self.engine(self.recycle_spec())
        eng.start()
        tp = self.live("T")[0].price
        self.ex.set_price(SYM, tp)
        eng.tick()
        self.assertEqual(eng.status, CYCLE_DONE)
        self.ex.place_order(client_id="kd-d1-L09E5", symbol=SYM, side="BUY", type="LIMIT", qty=0.1, price=50.0)   # bota ait kalıntı emir
        self.assertFalse(eng.begin_cycle(self.recycle_spec(price=tp)))
        self.assertEqual(eng.status, CYCLE_DONE)
        self.ex.cancel_order(SYM, "kd-d1-L09E5")
        self.assertTrue(eng.begin_cycle(self.recycle_spec(price=tp)))
        eng.tick()
        self.assertEqual(eng.status, RUNNING)
        self.assert_consistent("yeni-tur")


# ---------------------------------------------------------------------------------------------------------------------------
# 6) Korunan sınırlar
# ---------------------------------------------------------------------------------------------------------------------------
class TestProtectedLimits(unittest.TestCase):
    def test_leverage_is_capped_at_five_even_if_policy_allows_more(self):
        from app.dca_engine import plan as dca
        self.assertEqual(dca.MAX_LEVERAGE, 5)
        with self.assertRaises(dca.DcaPlanError) as ctx:
            plan_for(leverage=6, policy={**POLICY, "max_leverage": 50})
        self.assertIn("5x", str(ctx.exception))
        self.assertEqual(plan_for(leverage=5, policy={**POLICY, "max_leverage": 50})["leverage"], 5)

    def test_max_three_bots_per_user_is_still_enforced_in_routes(self):
        """Rota dosyaları fastapi gerektirir (bu ortamda yok); sınır sabiti ve iki rotadaki kontrol kaynak düzeyinde doğrulanır."""
        root = Path(__file__).parents[1] / "app"
        tree = ast.parse((root / "dca_live.py").read_text(encoding="utf-8"))
        values = [n.value.value for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
                  and any(getattr(t, "id", "") == "MAX_BOTS_PER_USER" for t in n.targets)]
        self.assertEqual(values, [3])
        for name in ("v25_execution.py", "binance_demo.py"):
            text = (root / name).read_text(encoding="utf-8")
            self.assertRegex(text, r"len\(runner\.occupying_rows\(user_id, owner=owner\)\) >= dca_live\.MAX_BOTS_PER_USER")


if __name__ == "__main__":
    unittest.main()
