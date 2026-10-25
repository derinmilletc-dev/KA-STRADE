"""Grid motoru. Kurallar:
 1. Emir önce deftere yazılır, sonra gönderilir; kimliği önceden bellidir (aynı kimlik = aynı emir).
 2. Yanıt gelmezse emir "UNKNOWN" olur; yeniden göndermeden ÖNCE borsadan kimlikle sorulur.
 3. Defter ile borsa uyuşmazsa yeni emir gönderilmez, grid durur ve uyarı yazılır.
 4. Koruyucu stop, dolu miktarı her zaman kapsar; yeni stop doğrulanmadan eskisi iptal edilmez.
 5. Sadece bu grid'in emirleri ve miktarı yönetilir (kimlik öneki + defter); başka pozisyonlara dokunulmaz.
Aşama 1'deki hesap/risk katmanı (frontend gridMath) planı onaylar; motor onaylı planı alır.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .ports import (BUY, CANCELED, DUPLICATE_CLIENT_ID, EPS, FILLED, LIMIT, LONG, MARKET, NEW, PARTIAL, REJECTED, SELL,
                    SHORT, STOP_MARKET, UNKNOWN_ORDER, WOULD_TRIGGER_IMMEDIATELY, ExchangeError, ExchangePort, ExchangeRejected, ExchangeTimeout,
                    ExchangeUnreachable, OrderInfo)
from .store import PENDING_SEND, TERMINAL, UNKNOWN, GridStore

# Durumlar
CREATED, RUNNING, PAUSED, STOPPED, COMPLETED = "CREATED", "RUNNING", "PAUSED", "STOPPED", "COMPLETED"
EMERGENCY, EMERGENCY_CLOSED, INTERVENTION_REQUIRED, BLOCKED = "EMERGENCY", "EMERGENCY_CLOSED", "INTERVENTION_REQUIRED", "BLOCKED"
MAX_LEVELS = 5  # manual_grid.py ve önizleme ile aynı; ayrı bir geliştirme olmadan artırılmaz
NO_NEW_ORDERS = {PAUSED, STOPPED, COMPLETED, EMERGENCY, EMERGENCY_CLOSED, INTERVENTION_REQUIRED, BLOCKED}


def client_order_id(grid_id: str, side: str, level: int, role: str, rev: int = 0) -> str:
    """kg-<grid>-<L|S><kademe><rol><rev>; ≤36 karakter. Grid kimliği, yön, kademe, rol ve sürüm kimliğin parçasıdır."""
    safe = re.sub(r"[^A-Za-z0-9]", "", grid_id)[:16]
    return f"kg-{safe}-{'L' if side == LONG else 'S'}{level:02d}{role}{rev}"[:36]


@dataclass
class GridLevelSpec:
    entry: float
    target: float
    qty: float


@dataclass
class GridSpec:
    grid_id: str
    symbol: str
    side: str
    levels: list[GridLevelSpec]
    stop_price: float

    def as_json(self) -> dict:
        return {"stop_price": self.stop_price, "levels": [vars(l) for l in self.levels]}


class GridEngine:
    def __init__(self, exchange: ExchangePort, store: GridStore, spec: GridSpec, *,
                 risk_gate: Callable[[], tuple[bool, str]] | None = None, max_close_attempts: int = 5, absence_confirmations: int = 2,
                 sleep: Callable[[float], None] = lambda s: None) -> None:
        if spec.side not in (LONG, SHORT):
            raise ValueError("yön LONG/SHORT olmalı")
        if not 2 <= len(spec.levels) <= MAX_LEVELS:
            raise ValueError(f"kademe sayısı 2-{MAX_LEVELS} arasında olmalı")
        self.ex, self.store, self.spec = exchange, store, spec
        self.gid, self.sym, self.risk_gate = spec.grid_id, spec.symbol, risk_gate
        self.max_close_attempts, self.sleep = max_close_attempts, sleep
        self.absence_confirmations = absence_confirmations
        self.entry_side, self.exit_side = (BUY, SELL) if spec.side == LONG else (SELL, BUY)
        self.sign = 1 if spec.side == LONG else -1
        self._last_reject = 0
        self.prefix = f"kg-{re.sub(r'[^A-Za-z0-9]', '', spec.grid_id)[:16]}-"
        if store.create_grid(self.gid, self.sym, spec.side, spec.as_json()):
            for i, lv in enumerate(spec.levels, 1):
                self._intent(role="E", level=i, rev=0, side=self.entry_side, type=LIMIT, price=lv.entry, stop_price=0.0, qty=lv.qty, reduce_only=0)

    # ---------- durum ----------
    @property
    def status(self) -> str:
        return self.store.grid(self.gid)["status"]

    def _set(self, status: str, reason: str = "") -> None:
        self.store.set_grid(self.gid, status=status, **({"halt_reason": reason} if reason else {}))

    def _alert(self, level: str, text: str) -> None:
        self.store.alert(self.gid, level, text)

    def _halt(self, reason: str) -> None:
        if self.status not in (EMERGENCY, EMERGENCY_CLOSED, INTERVENTION_REQUIRED, STOPPED):
            self._set(BLOCKED, reason)
        self._alert("kritik", reason)

    def _intent(self, **row) -> str:
        cid = client_order_id(self.gid, self.spec.side, row["level"], row["role"], row["rev"])
        self.store.add_order({"client_id": cid, "grid_id": self.gid, "status": PENDING_SEND, **row})
        return cid

    def grid_net(self) -> float:
        """Bu grid'in pozisyonu (pozitif = grid yönünde açık miktar)."""
        net = 0.0
        for o in self.store.orders(self.gid):
            net += o["filled"] if o["role"] == "E" else -o["filled"] if o["role"] in ("T", "S", "C") else 0.0
        return round(net, 8)

    # ---------- gönderim ----------
    def _send(self, row) -> None:
        """Defterdeki tek emri gönderir. Yanıt kaybolursa önce kimlikle sorar; yoksa aynı kimlikle tekrar dener."""
        cid = row["client_id"]
        try:
            info = self.ex.place_order(client_id=cid, symbol=self.sym, side=row["side"], type=row["type"], qty=row["qty"],
                                       price=row["price"], stop_price=row["stop_price"], reduce_only=bool(row["reduce_only"]))
            self._apply(cid, info)
        except ExchangeRejected as exc:
            if exc.code == DUPLICATE_CLIENT_ID:      # zaten var: yenisini açma, mevcut olanı oku
                self._adopt(cid)
            else:
                self._last_reject = exc.code
                self.store.set_order(cid, status=REJECTED)
                self._alert("uyarı", f"Emir reddedildi ({cid}): {exc}")
        except (ExchangeTimeout, ExchangeUnreachable):
            self.store.set_order(cid, status=UNKNOWN)
            self._adopt(cid, quiet=True)             # sorabiliyorsak hemen öğren

    def _adopt(self, cid: str, quiet: bool = False) -> None:
        try:
            info = self.ex.get_order(self.sym, cid)
        except ExchangeError:
            if not quiet:
                self._alert("uyarı", f"Emir durumu okunamadı ({cid}); tekrar denenecek.")
            return
        if info is not None:
            self._apply(cid, info)
        # borsada yok → UNKNOWN kalır; sonraki turda aynı kimlikle tekrar gönderilir (çift emir olamaz)

    def _apply(self, cid: str, info: OrderInfo) -> None:
        self.store.set_order(cid, status=info.status, filled=info.filled)

    def _send_pending(self) -> None:
        for row in self.store.orders(self.gid):
            if row["role"] == "E" and self.status in NO_NEW_ORDERS:
                continue
            if row["status"] == PENDING_SEND:
                self._send(row)
            elif row["status"] == UNKNOWN:
                self._resolve_unknown(row)

    def _resolve_unknown(self, row) -> None:
        """Durumu belirsiz emir: ÖNCE borsadan sor. Varsa benimse, yeniden GÖNDERME. Yalnızca art arda
        `absence_confirmations` kez başarılı sorguda 'yok' çıkarsa aynı kimlikle tekrar gönder. Sorgu başarısızsa bekle."""
        cid = row["client_id"]
        try:
            info = self.ex.get_order(self.sym, cid)
        except ExchangeError:
            return
        if info is not None:
            self._apply(cid, info)
            self.store.set_order(cid, misses=0)
            return
        misses = row["misses"] + 1
        self.store.set_order(cid, misses=misses)
        if misses >= self.absence_confirmations:
            self._send(self.store.db.execute("SELECT * FROM orders WHERE client_id=?", (cid,)).fetchone())

    # ---------- başlatma ----------
    def start(self) -> str:
        if self.status in NO_NEW_ORDERS:
            return self.status
        if self.risk_gate:
            ok, why = self.risk_gate()
            if not ok:
                self._set(BLOCKED, f"Risk kapısı: {why}")
                return BLOCKED
        if self.status == CREATED:
            try:
                base = self.ex.position(self.sym)
                foreign = [o for o in self.ex.open_orders(self.sym) if not o.client_id.startswith(self.prefix)]
            except ExchangeError:
                self._set(BLOCKED, "Borsaya ulaşılamadı; başlangıç durumu doğrulanamadı")
                return BLOCKED
            if abs(base) > EPS:
                self._set(BLOCKED, f"{self.sym} üzerinde başka pozisyon var ({base}); aynı coinde grid açılmaz")
                return BLOCKED
            if foreign:
                self._set(BLOCKED, f"{self.sym} üzerinde başka bekleyen emir var ({len(foreign)} adet); aynı coinde grid açılmaz")
                return BLOCKED
            self.store.set_grid(self.gid, baseline=base)
            self._set(RUNNING)
        self.tick()
        return self.status

    # ---------- ana döngü ----------
    def tick(self) -> None:
        """Bir tur: önce borsayla eşitle, sonra bekleyen emirleri gönder, sonra korumayı kur."""
        if self.status in (CREATED, EMERGENCY_CLOSED, COMPLETED, STOPPED):
            return
        if not self.sync():
            return
        self._ensure_protection()          # önce dolu miktarı koru, sonra yeni emir
        self._send_pending()
        if not self.sync():
            return
        self._ensure_protection()

    def sync(self) -> bool:
        """Defter ↔ borsa eşitlemesi. Tutarsızlık varsa False döner ve grid durur."""
        try:
            open_orders = {o.client_id: o for o in self.ex.open_orders(self.sym)}
            for row in self.store.orders(self.gid):
                if row["status"] in TERMINAL or row["status"] == PENDING_SEND:
                    continue
                info = open_orders.get(row["client_id"]) or self.ex.get_order(self.sym, row["client_id"])
                if info is None:
                    if row["status"] == UNKNOWN:
                        continue   # hiç ulaşmamış: tekrar gönderilecek
                    self._halt(f"Defterde var, borsada yok: {row['client_id']}")
                    return False
                self._apply(row["client_id"], info)
            known = {r["client_id"] for r in self.store.orders(self.gid)}
            orphans = [cid for cid in open_orders if cid.startswith(self.prefix) and cid not in known]
            if orphans:
                for cid in orphans:
                    try:
                        self.ex.cancel_order(self.sym, cid)
                    except ExchangeRejected:
                        pass
                self._halt(f"Defterde olmayan grid emri bulundu ve iptal edildi: {orphans}")
                return False
            expected = self.store.grid(self.gid)["baseline"] + self.sign * self.grid_net()
            actual = self.ex.position(self.sym)
            if abs(actual - expected) > 1e-6:
                self._halt(f"Pozisyon uyuşmuyor: borsa {actual}, defter {expected}")
                return False
        except ExchangeError as exc:
            self._alert("uyarı", f"Eşitleme yapılamadı: {exc}")
            return False
        self._detect_end()
        return True

    def _detect_end(self) -> None:
        if self.status in (EMERGENCY, EMERGENCY_CLOSED, INTERVENTION_REQUIRED):
            return
        stops = self.store.orders(self.gid, "S")
        if abs(self.grid_net()) <= EPS and any(s["filled"] > EPS for s in stops):
            self._cancel_all(roles=("E", "T", "S"))
            self._set(STOPPED, "Stop tetiklendi")
            self._alert("bilgi", "Stop tetiklendi: bekleyen emirler iptal edildi, yeni emir açılmayacak.")
            return
        entries = self.store.orders(self.gid, "E")
        if entries and all(e["status"] in TERMINAL for e in entries) and abs(self.grid_net()) <= EPS and any(e["filled"] > EPS for e in entries):
            self._cancel_all(roles=("S", "T"))
            self._set(COMPLETED)

    # ---------- koruma (TP + stop) ----------
    def _ensure_protection(self) -> None:
        if self.status in (EMERGENCY, EMERGENCY_CLOSED, STOPPED, COMPLETED, BLOCKED, INTERVENTION_REQUIRED):
            return
        # 1) Stop (ÖNCE): dolu miktarın tamamını kapsamalı; önce yeni stop, doğrulanınca eskiler iptal
        net = self.grid_net()
        active = [s for s in self.store.orders(self.gid, "S") if s["status"] in (NEW, PARTIAL, PENDING_SEND, UNKNOWN)]
        covered = round(sum(s["qty"] - s["filled"] for s in active), 8)
        if net <= EPS:
            for old in active:                    # pozisyon yok: artık gereksiz (aşırı büyük) stop kalmasın
                self._cancel(old["client_id"])
            return
        if abs(covered - net) > 1e-8:
            rev = len(self.store.orders(self.gid, "S"))
            self._last_reject = 0
            cid = self._intent(role="S", level=0, rev=rev, side=self.exit_side, type=STOP_MARKET, price=0.0, stop_price=self.spec.stop_price, qty=net, reduce_only=1)
            self._send(self.store.db.execute("SELECT * FROM orders WHERE client_id=?", (cid,)).fetchone())
            status = self.store.db.execute("SELECT status FROM orders WHERE client_id=?", (cid,)).fetchone()["status"]
            if status in (NEW, PARTIAL):
                for old in active:
                    self._cancel(old["client_id"])
            elif self._last_reject == WOULD_TRIGGER_IMMEDIATELY:
                # Fiyat stopu zaten geçti: korumasız bırakma, kalan miktarı hemen kapat.
                self._alert("uyarı", "Fiyat stop seviyesini geçmiş; kalan grid miktarı piyasadan kapatılıyor.")
                self._cancel_all(roles=("E", "T"))
                self._close(net)
                self._sync_for_emergency()
                self._detect_end()
            elif status == REJECTED:
                # Kesin ret ve fiyat geçilmiş değil: dolu miktar korunamıyor → yeni giriş durdurulur, müdahale istenir.
                self._cancel_all(roles=("E",))
                self._set(INTERVENTION_REQUIRED, "Koruyucu stop kurulamadı")
                self._alert("kritik", "MÜDAHALE GEREKLİ: koruyucu stop kurulamadı (borsa reddetti). Yeni giriş emirleri iptal edildi; eski stoplar iptal EDİLMEDİ.")
            else:
                self._alert("kritik", "Yeni koruyucu stop henüz doğrulanamadı; eski stoplar korunuyor, tekrar denenecek.")
        # 2) Her dolum artışı için sadece o artışın hedefi (reduceOnly limit)
        for e in self.store.orders(self.gid, "E"):
            covered = sum(t["qty"] for t in self.store.orders(self.gid, "T") if t["level"] == e["level"])
            delta = round(e["filled"] - covered, 8)
            if delta > EPS:
                rev = sum(1 for t in self.store.orders(self.gid, "T") if t["level"] == e["level"])
                lv = self.spec.levels[e["level"] - 1]
                cid = self._intent(role="T", level=e["level"], rev=rev, side=self.exit_side, type=LIMIT, price=lv.target, stop_price=0.0, qty=delta, reduce_only=1)
                self._send(self.store.db.execute("SELECT * FROM orders WHERE client_id=?", (cid,)).fetchone())

    # ---------- iptal ----------
    def _cancel(self, cid: str) -> bool:
        try:
            info = self.ex.cancel_order(self.sym, cid)
            self._apply(cid, info)
            return True
        except ExchangeRejected as exc:
            if exc.code == UNKNOWN_ORDER:       # dolmuş/iptal olmuş olabilir: gerçek durumu oku
                self._adopt(cid)
                row = self.store.db.execute("SELECT status FROM orders WHERE client_id=?", (cid,)).fetchone()
                return row["status"] in TERMINAL
            return False
        except ExchangeError:
            return False

    def _cancel_all(self, roles: tuple[str, ...]) -> bool:
        ok = True
        for row in self.store.orders(self.gid):
            if row["role"] not in roles or row["status"] in TERMINAL:
                continue
            if row["status"] == PENDING_SEND:
                self.store.set_order(row["client_id"], status=CANCELED)   # hiç gönderilmedi
                continue
            ok = self._cancel(row["client_id"]) and ok
        return ok

    # ---------- durdurma ----------
    def pause(self) -> str:
        """GRID'İ DURDUR: yeni emir yok, bekleyen girişler iptal; açık pozisyon stop ve hedefleriyle kalır."""
        if self.status in (STOPPED, COMPLETED, EMERGENCY_CLOSED, INTERVENTION_REQUIRED):
            return self.status
        self._set(PAUSED)
        for _ in range(3):
            self._cancel_all(roles=("E",))
            if not self.sync():
                break
            if all(e["status"] in TERMINAL for e in self.store.orders(self.gid, "E")):
                break
        self._ensure_protection_paused()
        return self.status

    def _ensure_protection_paused(self) -> None:
        """Durdurulmuş grid'te de (iptal yarışında) dolan miktar korunmalı."""
        saved = self.status
        self.store.set_grid(self.gid, status=RUNNING)
        try:
            self._ensure_protection()
        finally:
            self.store.set_grid(self.gid, status=saved)

    def emergency_stop(self) -> str:
        """ACİL DURDUR: yeni emir yok → bekleyen girişleri iptal → hedefleri iptal → reduceOnly piyasa ile kapat →
        borsadan doğrula → ANCAK ONDAN SONRA koruyucu stopları iptal et. Doğrulanamazsa INTERVENTION_REQUIRED, stoplar kalır."""
        if self.status in (EMERGENCY_CLOSED, STOPPED, COMPLETED):
            return self.status
        self._set(EMERGENCY)
        base = self.store.grid(self.gid)["baseline"]
        for attempt in range(self.max_close_attempts):
            # yeni dolum yarışına karşı her turda girişleri yeniden iptal et ve defteri yeniden eşitle
            self._cancel_all(roles=("E",))
            if not self._sync_for_emergency():
                self.sleep(0.5 * (attempt + 1))
                continue
            self._cancel_all(roles=("T",))   # hedefler koruyucu değil; reduceOnly kapanışla çakışmasın
            net = self.grid_net()
            entries_open = [e for e in self.store.orders(self.gid, "E") if e["status"] not in TERMINAL]
            if net <= EPS and not entries_open:
                break
            if net > EPS:
                self._close(net)
            self.sleep(0.5 * (attempt + 1))
        verified = False
        try:
            self._sync_for_emergency()
            actual = self.ex.position(self.sym)
            entries_open = [e for e in self.store.orders(self.gid, "E") if e["status"] not in TERMINAL]
            verified = abs(self.grid_net()) <= EPS and not entries_open and abs(actual - base) <= 1e-6
        except ExchangeError:
            verified = False
        if verified:
            stops_ok = self._cancel_all(roles=("S", "T"))
            self._set(EMERGENCY_CLOSED)
            if not stops_ok:
                self._alert("uyarı", "Pozisyon kapandı ama bazı koruyucu stoplar iptal edilemedi; borsada elle kontrol edin.")
            self._alert("bilgi", "Acil durdurma tamamlandı: grid pozisyonu kapandı ve borsadan doğrulandı.")
        else:
            self._set(INTERVENTION_REQUIRED, "Acil durdurma doğrulanamadı")
            self._alert("kritik", "MÜDAHALE GEREKLİ: grid pozisyonunun kapandığı doğrulanamadı. Koruyucu stoplar iptal EDİLMEDİ. Borsayı elle kontrol edin.")
        return self.status

    def _sync_for_emergency(self) -> bool:
        """Acil durumda defter↔borsa uyuşmazlığı grid'i BLOCKED yapmasın; sadece doldurmaları oku."""
        try:
            for row in self.store.orders(self.gid):
                if row["status"] in TERMINAL or row["status"] == PENDING_SEND:
                    continue
                info = self.ex.get_order(self.sym, row["client_id"])
                if info is not None:
                    self._apply(row["client_id"], info)
            return True
        except ExchangeError:
            return False

    def _close(self, qty: float) -> None:
        rows = [r for r in self.store.orders(self.gid, "C") if r["status"] not in TERMINAL]
        if rows:                                  # önceki kapanış emri bekliyor/belirsiz: önce onu çöz
            self._send(rows[0])
            return
        rev = len(self.store.orders(self.gid, "C"))
        cid = self._intent(role="C", level=0, rev=rev, side=self.exit_side, type=MARKET, price=0.0, stop_price=0.0, qty=qty, reduce_only=1)
        self._send(self.store.db.execute("SELECT * FROM orders WHERE client_id=?", (cid,)).fetchone())
