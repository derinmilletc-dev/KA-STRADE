"""Nötr (klasik) grid motoru.

Fiyatın altındaki kademelerde ALIŞ (LONG), üstündekilerde SATIŞ (SHORT) limit emirleri durur. Bir alış dolunca bir üst
çizgide satış (hedef), bir satış dolunca bir alt çizgide alış (hedef) kurulur; hedef dolunca aynı kademede yeniden giriş
açılır (tur). Fiyata en yakın çizgide emir yoktur; böylece bir tarafın hedefleri, karşı tarafın girişlerinden önce dolar.

Tek yönlü (one-way) hesapta pozisyon işaretlidir: grid'in katkısı = Σ alış dolumu − Σ satış dolumu. Hedefler reduceOnly
DEĞİLDİR (aynı pozisyon üzerinde netleşir); koruma her zaman açık miktarın yönüne göre kurulur:
  net > 0 (LONG)  → alt stopta SATIŞ STOP_MARKET (reduceOnly), miktar = net
  net < 0 (SHORT) → üst stopta ALIŞ STOP_MARKET (reduceOnly), miktar = |net|
Ters yöndeki stop iptal edilir; yeni stop doğrulanmadan eskisi iptal edilmez. Motorun diğer kuralları tek yönlü grid ile aynıdır.
"""
from __future__ import annotations

from .engine import (BLOCKED, COMPLETED, EMERGENCY, EMERGENCY_CLOSED, INTERVENTION_REQUIRED, RUNNING, STOPPED, GridEngine)
from .ports import BUY, EPS, LIMIT, LONG, MARKET, NEW, PARTIAL, REJECTED, SELL, SHORT, STOP_MARKET, WOULD_TRIGGER_IMMEDIATELY
from .store import PENDING_SEND, TERMINAL, UNKNOWN

NEUTRAL = "NEUTRAL"


class NeutralGridEngine(GridEngine):
    SIDES = (NEUTRAL,)

    def __init__(self, exchange, store, spec, **kw) -> None:
        if spec.side == NEUTRAL:
            if not spec.stop_price_upper or spec.stop_price_upper <= spec.stop_price:
                raise ValueError("nötr grid için üst stop, alt stoptan büyük olmalı")
            for lv in spec.levels:
                if lv.side not in (LONG, SHORT):
                    raise ValueError("nötr grid'te her kademenin yönü LONG/SHORT olmalı")
                better = lv.target > lv.entry if lv.side == LONG else lv.target < lv.entry
                if not better:
                    raise ValueError("kademe hedefi girişten ileride olmalı")
        super().__init__(exchange, store, spec, **kw)
        self.sign = 1

    # ---------- kademe yönü ----------
    def _entry_side(self, level: int) -> str:
        return BUY if self.spec.levels[level - 1].side == LONG else SELL

    def _exit_side(self, level: int) -> str:
        return SELL if self.spec.levels[level - 1].side == LONG else BUY

    def _level_tag(self, level: int) -> str:
        return self.spec.levels[level - 1].side if level > 0 else LONG

    # ---------- hesap ----------
    def grid_net(self) -> float:
        """İşaretli: + LONG, − SHORT. Rol fark etmez; her dolum yönüyle sayılır."""
        net = 0.0
        for o in self.store.orders(self.gid):
            net += o["filled"] if o["side"] == BUY else -o["filled"]
        return round(net, 8)

    def _position_delta(self) -> float:
        return self.grid_net()

    # ---------- koruma ----------
    def _ensure_protection(self) -> None:
        if self.status in (EMERGENCY, EMERGENCY_CLOSED, STOPPED, COMPLETED, BLOCKED, INTERVENTION_REQUIRED):
            return
        net = self.grid_net()
        stops = self.store.orders(self.gid, "S")
        live = [s for s in stops if s["status"] in (NEW, PARTIAL, PENDING_SEND, UNKNOWN)]
        need_side = SELL if net > EPS else BUY if net < -EPS else None
        right = [s for s in live if s["side"] == need_side]
        wrong = [s for s in live if s["side"] != need_side]
        if need_side is None:
            for old in live:                        # pozisyon yok: stop gereksiz
                self._cancel(old["client_id"])
        else:
            covered = round(sum(s["qty"] - s["filled"] for s in right), 8)
            if abs(covered - abs(net)) > 1e-8:
                stop_price = self.spec.stop_price if need_side == SELL else self.spec.stop_price_upper
                self._last_reject = 0
                cid = self._intent(role="S", level=0, rev=len(stops), side=need_side, type=STOP_MARKET, price=0.0, stop_price=stop_price,
                                   qty=abs(net), reduce_only=1, tag=LONG if need_side == SELL else SHORT)
                self._send(self.store.order(cid))
                status = self.store.order(cid)["status"]
                if status in (NEW, PARTIAL):
                    for old in live:                # yenisi doğrulandı: eski (eksik ya da ters yöndeki) stoplar iptal
                        self._cancel(old["client_id"])
                elif self._last_reject == WOULD_TRIGGER_IMMEDIATELY:
                    self._alert("uyarı", "Fiyat stop seviyesini geçmiş; grid miktarı piyasadan kapatılıyor.")
                    self._cancel_all(roles=("E", "T"))
                    self._close(net)
                    self._sync_for_emergency()
                    self._stop_after_forced_close()
                    return
                elif status == REJECTED:
                    self._cancel_all(roles=("E",))
                    self._set(INTERVENTION_REQUIRED, "Koruyucu stop kurulamadı")
                    self._alert("kritik", "MÜDAHALE GEREKLİ: koruyucu stop kurulamadı (borsa reddetti). Yeni giriş emirleri iptal edildi; eski stoplar iptal EDİLMEDİ.")
                else:
                    self._alert("kritik", "Yeni koruyucu stop henüz doğrulanamadı; eski stoplar korunuyor, tekrar denenecek.")
            elif wrong:
                for old in wrong:                   # doğru yön tam korunuyor: ters yöndeki eski stop kalmasın
                    self._cancel(old["client_id"])
        # Hedefler: her giriş dolum artışı için karşı yönde limit (reduceOnly değil; aynı pozisyonda netleşir)
        targets = self.store.orders(self.gid, "T")
        for e in self.store.orders(self.gid, "E"):
            covered = sum(t["qty"] for t in targets if t["level"] == e["level"] and t["cycle"] == e["cycle"])
            delta = round(e["filled"] - covered, 8)
            if delta > EPS:
                lv = self.spec.levels[e["level"] - 1]
                cid = self._intent(role="T", level=e["level"], rev=sum(1 for t in targets if t["level"] == e["level"]), cycle=e["cycle"],
                                   side=self._exit_side(e["level"]), type=LIMIT, price=lv.target, stop_price=0.0, qty=delta, reduce_only=0,
                                   tag=self._level_tag(e["level"]))
                self._send(self.store.order(cid))
                targets = self.store.orders(self.gid, "T")
        if self.status == RUNNING and not self._protect_only:
            self._recycle()

    def _detect_end(self) -> None:
        if self.status in (EMERGENCY, EMERGENCY_CLOSED, INTERVENTION_REQUIRED, STOPPED, COMPLETED):
            return                                  # bitmiş grid'in sonucu sonradan değişmez
        stops = self.store.orders(self.gid, "S")
        if abs(self.grid_net()) <= EPS and any(s["filled"] > EPS for s in stops):
            self._cancel_all(roles=("E", "T", "S"))
            self._set(STOPPED, "Stop tetiklendi")
            self._alert("bilgi", "Stop tetiklendi: bekleyen emirler iptal edildi, yeni emir açılmayacak.")

    def _close(self, qty: float) -> None:
        """İşaretli net miktarı reduceOnly piyasa emriyle kapatır (LONG → satış, SHORT → alış)."""
        rows = [r for r in self.store.orders(self.gid, "C") if r["status"] not in TERMINAL]
        if rows:
            self._send(rows[0])
            return
        if abs(qty) <= EPS:
            return
        side = SELL if qty > 0 else BUY
        cid = self._intent(role="C", level=0, rev=len(self.store.orders(self.gid, "C")), side=side, type=MARKET, price=0.0, stop_price=0.0,
                           qty=abs(qty), reduce_only=1, tag=LONG if side == SELL else SHORT)
        self._send(self.store.order(cid))
