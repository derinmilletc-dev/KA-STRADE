"""KAIS FARMER tek-pozisyon yaşam döngüsü motoru (C3).

Bir Farmer pozisyonu = bir `DcaEngine` alt sınıfı satırı (tek kademe). Orkestratör, aday önbelleği, slot yönetimi ve çoklu pozisyon
koordinasyonu BURADA YOKTUR (C7). Grid/DCA davranışı değişmez: bu sınıf yalnız kendi `kf-` emirlerini yönetir.

Akış: MARKET giriş → (dolan GERÇEK miktar) → ÖNCE STOP_MARKET (reduceOnly, miktar) → SONRA TAKE_PROFIT_MARKET (reduceOnly, miktar; closePosition YOK).
Hedef ve stop fiyatı GERÇEK ortalama girişten hesaplanır. Hedef borsada reddedilirse stop yerinde kalır; "zaten geçildi" ise reduce-only piyasa
kapanışı yapılır. Kapanış nedeni: TARGET | STOP | MARKET_CLOSE | DEAD_CLOSE | EMERGENCY | OPEN — hepsi borsa dolumuyla kanıtlanır.
Buradaki PnL yalnız TAHMİNdir; doğrulanmış sonuç C8'de verified_plan_pnl'den gelir."""
from __future__ import annotations

import json
import re
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, Mapping

from . import DISPLAY_NAME
from ..dca_engine.engine import ACTIVE, DcaEngine
from ..dca_engine.plan import TAKER_FEE
from ..grid_engine.engine import (BLOCKED, COMPLETED, EMERGENCY, EMERGENCY_CLOSED, INTERVENTION_REQUIRED, PAUSED, RUNNING, STOPPED,
                                  GridLevelSpec, GridSpec)
from ..grid_engine.ports import (EPS, LONG, MARKET, NEW, PARTIAL, REJECTED, SHORT, STOP_MARKET, TAKE_PROFIT_MARKET,
                                 WOULD_TRIGGER_IMMEDIATELY)
from ..grid_engine.store import TERMINAL
from .ids import farmer_client_order_id, farmer_grid_prefix
from .store import FarmerStore

KIND = "FARMER"
DEAD_CLOSE_MARK = "DEAD_CLOSE"
DEFAULT_DEAD_CONFIRMATIONS = 2          # Faz B §6.4: art arda 2 KAPANMIŞ mum
_FINAL = (EMERGENCY, EMERGENCY_CLOSED, INTERVENTION_REQUIRED, STOPPED, COMPLETED)


_BRAND = re.compile(r"\b(?:DCA|grid)\b", re.IGNORECASE)


def brand(text: str) -> str:
    """Miras alınan motor iletilerindeki 'grid'/'DCA' sözcükleri kullanıcıya görünmez; ürün adı yalnız KAIS FARMER."""
    return _BRAND.sub(DISPLAY_NAME, text)


def _round(value: float, tick: float, up: bool) -> float:
    q = (Decimal(repr(value)) / Decimal(repr(tick))).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
    return float(q * Decimal(repr(tick)))


def round_cost(notional: float, buffer_usdt: float) -> float:
    """Tahmini gidiş-dönüş maliyet = notional·taker·2 + tampon (confirm.feasibility ile aynı formül)."""
    return notional * TAKER_FEE * 2 + buffer_usdt


def levels_from_avg(side: str, avg: float, qty: float, *, target_usdt: float, max_loss_usdt: float, tick: float,
                    buffer_usdt: float) -> tuple[float, float | None]:
    """(hedef fiyatı, stop fiyatı) — GERÇEK ortalama giriş ve GERÇEK miktar üzerinden. Stop mesafesi <= 0 ise stop None
    (maliyet maks. zararı yiyor: korumalı pozisyon kurulamaz)."""
    sign = 1 if side == LONG else -1
    cost = round_cost(avg * qty, buffer_usdt)
    tp = _round(avg + sign * (target_usdt + cost) / qty, tick, up=side == LONG)
    distance = (max_loss_usdt - cost) / qty
    stop = _round(avg - sign * distance, tick, up=side == LONG) if distance > 0 else None      # stop girişe doğru yuvarlanır: zarar tavanı aşılmaz
    return tp, stop


def farmer_spec(grid_id: str, symbol: str, side: str, price: float, *, margin: float, leverage: float, target_usdt: float,
                max_loss_usdt: float, tick: float, step: float, buffer_usdt: float = 0.05) -> GridSpec:
    """Planlanan fiyattan tek girişli plan. Gerçek dolumdan sonra hedef/stop motor tarafından GERÇEK ortalamaya göre yeniden hesaplanır."""
    if side not in (LONG, SHORT) or price <= 0 or tick <= 0 or step <= 0:
        raise ValueError("geçersiz Farmer planı")
    qty = float((Decimal(repr(margin * leverage / price)) / Decimal(repr(step))).to_integral_value(rounding=ROUND_FLOOR) * Decimal(repr(step)))
    if qty <= 0:
        raise ValueError("miktar adımın altında")
    tp, stop = levels_from_avg(side, price, qty, target_usdt=target_usdt, max_loss_usdt=max_loss_usdt, tick=tick, buffer_usdt=buffer_usdt)
    if stop is None:
        raise ValueError("tahmini maliyet maksimum zararı aşıyor; stop mesafesi yok")
    sign = 1 if side == LONG else -1
    notional = qty * price
    meta = {"kind": KIND, "target_usdt": target_usdt, "max_loss_usdt": max_loss_usdt, "buffer_usdt": buffer_usdt,
            "est_cost_usdt": round(round_cost(notional, buffer_usdt), 6), "tick": tick, "step": step, "notional": round(notional, 6),
            "margin_usdt": margin, "leverage": leverage, "planned_entry": price,
            "target_pct": round((tp - price) / price * 100 * sign, 5), "stop_pct": round((price - stop) / price * 100 * sign, 5)}
    return GridSpec(grid_id, symbol, side, [GridLevelSpec(price, tp, qty)], stop, recycle=False, meta=meta)


class FarmerEngine(DcaEngine):
    MIN_LEVELS = 1

    def __init__(self, exchange, store: FarmerStore, spec: GridSpec, **kw) -> None:
        if len(spec.levels) != 1 or spec.meta.get("kind") != KIND:
            raise ValueError("Farmer tek girişlidir ve kind=FARMER olmalı")
        if not isinstance(store, FarmerStore):
            raise TypeError("FarmerEngine FarmerStore ister")
        super().__init__(exchange, store, spec, **kw)
        self.prefix = farmer_grid_prefix(spec.grid_id)

    # ---------- kullanıcıya görünen metin ----------
    def _alert(self, level: str, text: str) -> None:
        super()._alert(level, brand(text))

    def _set(self, status: str, reason: str = "") -> None:
        super()._set(status, brand(reason) if reason else reason)

    # ---------- kimlik / giriş ----------
    def _intent(self, tag: str | None = None, **row) -> str:
        cid = farmer_client_order_id(self.gid, tag or self.spec.side, row["level"], row["role"], row["rev"])
        if row.get("role") == "E":
            row = {**row, "type": MARKET, "price": 0.0}              # giriş HER ZAMAN piyasadan
        self.store.add_order({"client_id": cid, "grid_id": self.gid, "status": "PENDING_SEND", "cycle": 0, **row})
        return cid

    def _entry(self):
        return next(iter(self.store.orders(self.gid, "E")), None)

    def position_qty(self) -> float:
        return self.grid_net()

    # ---------- gerçek ortalamadan hedef / stop ----------
    def _levels(self) -> tuple[float, float | None] | None:
        avg, qty = self.avg_entry(), self.grid_net()
        if avg is None or qty <= EPS:
            return None
        m = self.spec.meta
        return levels_from_avg(self.spec.side, avg, qty, target_usdt=float(m["target_usdt"]), max_loss_usdt=float(m["max_loss_usdt"]),
                               tick=float(m["tick"]), buffer_usdt=float(m.get("buffer_usdt", 0.05)))

    def tp_price(self) -> float | None:
        levels = self._levels()
        return levels[0] if levels else None

    def stop_target_price(self) -> float | None:
        levels = self._levels()
        return levels[1] if levels else None

    # ---------- koruma: ÖNCE stop, SONRA hedef ----------
    def _ensure_protection(self) -> None:
        if self.status in (EMERGENCY, EMERGENCY_CLOSED, STOPPED, COMPLETED, BLOCKED, INTERVENTION_REQUIRED):
            return
        if not self._ensure_stop():
            return
        self._ensure_target()

    def _ensure_stop(self) -> bool:
        net = self.grid_net()
        stops = self.store.orders(self.gid, "S")
        active = [s for s in stops if s["status"] in ACTIVE]
        if net <= EPS:
            for old in active:
                self._cancel(old["client_id"])
            return True
        price = self.stop_target_price()
        tick = float(self.spec.meta["tick"])
        if price is None:                                            # maliyet maks. zararı yiyor: korumalı kurulamaz → kapat
            return self._forced_close("Tahmini maliyet maksimum zararı aşıyor; pozisyon piyasadan kapatılıyor.")
        covered = round(sum(s["qty"] - s["filled"] for s in active), 8)
        same_price = bool(active) and all(abs(s["stop_price"] - price) <= tick / 2 for s in active)
        if abs(covered - net) <= 1e-8 and same_price:
            return True
        if price != self.spec.stop_price:                            # gerçek ortalamaya göre stop: plan kaydı güncellenir
            self.spec.stop_price = price
            self.store.set_grid(self.gid, spec=json.dumps(self.spec.as_json()))
        self._last_reject = 0
        cid = self._intent(role="S", level=0, rev=len(stops), cycle=0, side=self.exit_side, type=STOP_MARKET, price=0.0,
                           stop_price=price, qty=net, reduce_only=1)
        self._send(self.store.order(cid))
        status = self.store.order(cid)["status"]
        if status in (NEW, PARTIAL):
            for old in active:                                       # yeni stop doğrulandı → eskiler iptal
                self._cancel(old["client_id"])
            return True
        if self._last_reject == WOULD_TRIGGER_IMMEDIATELY:
            return self._forced_close("Fiyat stop seviyesini geçmiş; Farmer pozisyonu piyasadan kapatılıyor.")
        if status == REJECTED:
            self._cancel_all(roles=("E",))
            self._set(INTERVENTION_REQUIRED, "Koruyucu stop kurulamadı")
            self._alert("kritik", "MÜDAHALE GEREKLİ: koruyucu stop kurulamadı (borsa reddetti). Eski stoplar iptal EDİLMEDİ.")
        else:
            self._alert("kritik", "Yeni koruyucu stop henüz doğrulanamadı; eski stoplar korunuyor, tekrar denenecek.")
        return False

    def _forced_close(self, message: str) -> bool:
        self._alert("uyarı", message)
        self._cancel_all(roles=("E", "T"))
        self._close(self.grid_net())
        self._sync_for_emergency()
        self._stop_after_forced_close()
        return False

    def _ensure_target(self) -> None:
        net = self.grid_net()
        active = [t for t in self.store.orders(self.gid, "T") if t["status"] in ACTIVE]
        if net <= EPS:
            for old in active:
                self._cancel(old["client_id"])
            return
        price = self.tp_price()
        if price is None:
            return
        tick = float(self.spec.meta["tick"])
        covered = round(sum(t["qty"] - t["filled"] for t in active), 8)
        same_price = bool(active) and all(abs(t["stop_price"] - price) <= tick / 2 for t in active)
        if abs(covered - net) <= 1e-8 and same_price:
            return                                                   # tam gerçek miktarı kapsayan hedef zaten borsada
        for old in active:                                           # hedef koruyucu değil; stop yerinde kalır
            if not self._cancel(old["client_id"]):
                self._alert("uyarı", "Eski hedef iptal edilemedi; yeni hedef konmadı, tekrar denenecek.")
                return
        net = self.grid_net()
        if net <= EPS:
            return
        self._last_reject = 0
        cid = self._intent(role="T", level=1, rev=len(self.store.orders(self.gid, "T")), cycle=0, side=self.exit_side,
                           type=TAKE_PROFIT_MARKET, price=0.0, stop_price=price, qty=net, reduce_only=1)
        self._send(self.store.order(cid))
        if self._last_reject == WOULD_TRIGGER_IMMEDIATELY:
            # Fiyat hedefi zaten geçti → kârı beklemeden reduce-only piyasa kapanışı. Hata KODU varsayılmaz: yalnız motorun
            # WOULD_TRIGGER_IMMEDIATELY eşlemesi (C0: already_crossed_error UNVERIFIED) kullanılır; başka ret → stop yerinde kalır.
            self._alert("bilgi", "Fiyat hedefi geçmiş; pozisyon piyasadan (reduce-only) kapatılıyor.")
            self._cancel_all(roles=("T",))
            self._close(net)
            self._sync_for_emergency()
            self._detect_end()
        elif self.store.order(cid)["status"] == REJECTED:
            self._alert("kritik", "Hedef emri reddedildi; koruyucu stop yerinde, hedef yok. Tekrar denenecek.")

    # ---------- bitiş / neden ----------
    def _detect_end(self) -> None:
        if self.status in _FINAL:
            return
        if abs(self.grid_net()) > EPS:
            return
        orders = self.store.orders(self.gid)
        entry = self._entry()
        if entry is not None and entry["status"] == REJECTED and entry["filled"] <= EPS:
            self._cancel_all(roles=("E", "T", "S"))
            self._set(STOPPED, "Giriş reddedildi")
            self._alert("kritik", "Giriş emri borsa tarafından reddedildi; Farmer pozisyonu açılmadı.")
            return
        filled = lambda role: any(o["role"] == role and o["filled"] > EPS for o in orders)  # noqa: E731
        if not any(o["role"] == "E" and o["filled"] > EPS for o in orders):
            return                                                   # giriş hiç dolmadı: bitiş yok
        reason = self.store.grid(self.gid)["halt_reason"]
        if filled("S"):
            self._cancel_all(roles=("E", "T", "S"))
            self._set(STOPPED, "Stop tetiklendi")
        elif filled("T") or filled("C"):
            self._cancel_all(roles=("E", "T", "S"))
            self._set(COMPLETED, DEAD_CLOSE_MARK if reason == DEAD_CLOSE_MARK else "Hedef/kapanış doldu")

    def close_reason(self) -> str:
        st = self.status
        if st in (EMERGENCY, EMERGENCY_CLOSED):
            return "EMERGENCY"
        orders = self.store.orders(self.gid)
        if self.store.grid(self.gid)["halt_reason"] == DEAD_CLOSE_MARK and any(o["role"] == "C" and o["filled"] > EPS for o in orders):
            return "DEAD_CLOSE"
        for role, reason in (("S", "STOP"), ("T", "TARGET"), ("C", "MARKET_CLOSE")):
            if any(o["role"] == role and o["filled"] > EPS for o in orders):
                return reason
        return "OPEN"

    def is_flat(self) -> bool:
        """Defter DÜZ mü (tahmin). Borsa doğrulaması için `verify_flat()`."""
        return abs(self.grid_net()) <= EPS

    def verify_flat(self) -> tuple[bool, str]:
        """Borsadan doğrulama: pozisyon başlangıca döndü, bu motorun açık emri yok. Okunamazsa (False, neden) — 'düz' varsayılmaz."""
        try:
            actual = self.ex.position(self.sym)
            baseline = float(self.store.grid(self.gid)["baseline"])
            leftovers = [o.client_id for o in self.ex.open_orders(self.sym) if o.client_id.startswith(self.prefix)]
        except Exception as exc:                                     # noqa: BLE001 — okunamayan durum düz sayılmaz
            return False, f"borsa okunamadı: {exc.__class__.__name__}"
        if abs(actual - baseline) > 1e-6:
            return False, f"borsada pozisyon var ({actual - baseline:g})"
        if leftovers:
            return False, f"açık emir kaldı: {leftovers}"
        return True, "düz"

    # ---------- ÖLÜ pozisyon (C4/C7 çağırır; veriyi vermek çağıranın işi, karar burada) ----------
    def is_adverse(self, analysis: Mapping[str, Any], dead_score: float) -> bool:
        """confidence < dead_score VE yön ters VE momentum ters. BEKLE/Nötr ters sayılmaz; eksik veri ters sayılmaz."""
        try:
            confidence = float(analysis.get("confidence"))
        except (TypeError, ValueError):
            return False
        opposite_dir, opposite_mom = ("SHORT", "Negatif") if self.spec.side == LONG else ("LONG", "Pozitif")
        return confidence < dead_score and analysis.get("direction") == opposite_dir and analysis.get("momentum") == opposite_mom

    def dead_check(self, analysis: Mapping[str, Any], candle_open_ms: int, mark_price: float, *, dead_score: float | None,
                   safety_margin_usdt: float, confirmations: int = DEFAULT_DEAD_CONFIRMATIONS) -> str:
        """Yalnız KAPANMIŞ mum için çağrılmalı (aynı mum iki kez sayılmaz). Dönüş:
        DISABLED | NOT_OPEN | NOT_DEAD | DEAD_WAIT | DEAD_LOSS_HOLD | DEAD_PROFIT_TOO_SMALL | DEAD_CLOSE_FAILED | DEAD_CLOSED.
        Zararda/yetersiz kârda KAPATMAZ (borsadaki stop yönetir; zaman aşımı yok)."""
        if dead_score is None:
            return "DISABLED"
        if self.status != RUNNING or self.grid_net() <= EPS:
            return "NOT_OPEN"
        count = self.store.dead_count(self.gid, int(candle_open_ms), self.is_adverse(analysis, dead_score))
        if count == 0:
            return "NOT_DEAD"
        if count < confirmations:
            return "DEAD_WAIT"
        est = self.unrealized_net(mark_price)
        if est is None:
            return "DEAD_LOSS_HOLD"
        if est <= 0:
            return "DEAD_LOSS_HOLD"
        if est <= safety_margin_usdt:
            return "DEAD_PROFIT_TOO_SMALL"
        return "DEAD_CLOSED" if self.reduce_if_dead_profit() else "DEAD_CLOSE_FAILED"

    def unrealized_net(self, mark_price: float) -> float | None:
        """Tahmini net (ücretler düşülmüş): işaret·(mark−ort)·miktar − giriş ücreti − çıkış ücreti. Veri yoksa None."""
        avg, qty = self.avg_entry(), self.grid_net()
        if avg is None or qty <= EPS or not mark_price > 0:
            return None
        gross = self.sign * (mark_price - avg) * qty
        return gross - (avg * qty + mark_price * qty) * TAKER_FEE

    def reduce_if_dead_profit(self) -> bool:
        """Hedefi iptal et → reduce-only piyasa kapanışı → borsadan oku → bittiyse True. Hedef iptal edilemezse KAPATMAZ."""
        if self.status != RUNNING or self.grid_net() <= EPS:
            return False
        if not self._cancel_all(roles=("T",)):
            self._alert("uyarı", "Ölü pozisyon kapanışı: hedef iptal edilemedi; kapatılmadı.")
            return False
        self._set(RUNNING, DEAD_CLOSE_MARK)
        self._close(self.grid_net())
        self._sync_for_emergency()
        self._detect_end()
        if self.status == COMPLETED:
            return True
        self._alert("uyarı", "Ölü pozisyon kapanışı henüz doğrulanamadı; sonraki turda tekrar okunacak.")
        return False

    # ---------- gösterim (TAHMİN) ----------
    def estimate(self) -> dict[str, Any]:
        orders = self.store.orders(self.gid)
        entry = self.avg_entry()
        exits = [o for o in orders if o["role"] in ("T", "S", "C") and o["filled"] > EPS]
        px = lambda o: o["avg_price"] or o["price"] or o["stop_price"]  # noqa: E731
        gross = sum(self.sign * (px(o) - entry) * o["filled"] for o in exits) if entry else 0.0
        fees = sum(px(o) * o["filled"] * TAKER_FEE for o in exits) + (entry or 0) * sum(o["filled"] for o in exits) * TAKER_FEE
        return {"target_usdt": self.spec.meta["target_usdt"], "estimated_net_usdt": round(gross - fees, 6), "gross_usdt": round(gross, 6),
                "est_fees_usdt": round(fees, 6), "close_reason": self.close_reason(), "verified": False}

    def snapshot(self) -> dict[str, Any]:
        levels = self._levels()
        return {"grid_id": self.gid, "symbol": self.sym, "side": self.spec.side, "status": self.status, "qty": self.grid_net(),
                "avg_entry": self.avg_entry(), "tp_price": levels[0] if levels else None, "stop_price": levels[1] if levels else None,
                "close_reason": self.close_reason(), "estimate": self.estimate()}
