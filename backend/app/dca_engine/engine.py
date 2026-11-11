"""DCA motoru: `GridEngine`'in alt sınıfı (sahte borsada test edilir; canlıya BAĞLI DEĞİL).

Bir tur: taban emir (piyasa) dolunca ek (güvenlik) emirleri limit olarak konur. Pozisyon büyüdükçe (a) koruyucu stop miktarı,
(b) TEK kâr hedefi (ortalama girişe göre) güncellenir. Hedef dolunca tur biter; `recycle` açıksa durum CYCLE_DONE olur ve
çağıran taraf (çalıştırıcı) risk kapılarını yeniden geçirip yeni plan hesaplayarak `begin_cycle` ile yeni tur başlatır.

Grid motorundan devralınan kurallar (değişmedi): emir önce deftere yazılır, kimlik önceden bellidir; yanıt kaybında önce borsaya
sorulur; defter↔borsa uyuşmazsa durur; stop dolu miktarın tamamını kapsar ve yeni stop doğrulanmadan eskisi iptal edilmez;
acil durdurma kapanışı borsadan doğrular.
DCA'ya özel: (1) emir kimliği öneki `kd-` (grid'in `kg-` akış eşleştirmesiyle karışmaz); (2) ek emirler taban emir DOLMADAN gönderilmez;
(3) hedef emri tek ve açık miktarın tamamı kadardır; ortalama değişince önce eski hedef iptal edilir, sonra yenisi konur
(hedef koruyucu değildir; koruyucu stop zaten yerinde kalır).
Ortalama giriş (kâr hedefi): KAYNAK BORSADIR. Borsanın bildirdiği gerçek `entryPrice` (kısmi hedef dolumundan sonra yeni ek emir dolsa da
doğru olan budur) hedefi belirler; yerel (defter) ortalama yalnız borsa verisi geçici okunamazsa yedek olarak kullanılır. Borsa ortalaması defterle
anlamlı biçimde uyuşmuyorsa sessizce devam edilmez: yeni girişler iptal edilir ve INTERVENTION_REQUIRED olur (stop ve mevcut hedef yerinde kalır).
Güvenlik sırası: ek emirler yalnız taban DOLUP pozisyonun tamamı borsada doğrulanmış stop VE hedefle korunuyorsa gönderilir. Pozisyon doğrulanmış
stopla tamamen korunmuyorsa borsada bekleyen ek emirler de İPTAL edilir ve iptal borsadan doğrulanır (iptal yanıtı kaybolursa durum sorulur);
böylece pozisyon stopsuz büyüyemez. Stop birkaç tur boyunca kurulamazsa pozisyon (çift emir üretmeden) piyasadan kapatılır; bu da doğrulanamazsa
INTERVENTION_REQUIRED SON çaredir. Stop yeniden doğrulanınca iptal edilen ek emirler (fiyat hâlâ uygunsa) yeniden kurulur."""
from __future__ import annotations

import json
import re
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from ..grid_engine.engine import (BLOCKED, COMPLETED, EMERGENCY, EMERGENCY_CLOSED, INTERVENTION_REQUIRED, PAUSED, RUNNING, STOPPED,
                                  GridEngine, GridLevelSpec, GridSpec)
from ..grid_engine.ports import (BUY, CANCELED, EPS, FILLED, LIMIT, LONG, MARKET, NEW, PARTIAL, REJECTED, SELL, SHORT, STOP_MARKET,
                                 WOULD_TRIGGER_IMMEDIATELY, ExchangeError, PositionInfo)
from ..grid_engine.store import PENDING_SEND, TERMINAL, UNKNOWN
from .risk_config import (AVG_MISMATCH_TOLERANCE_PCT, AVG_RANGE_TOLERANCE_PCT, CANCEL_VERIFY_ATTEMPTS, LIQ_BUFFER_PCT, STOP_RETRY_TICKS,
                          TRAIL_MIN_STEP_PCT)

CYCLE_DONE = "CYCLE_DONE"
ACTIVE = (NEW, PARTIAL, PENDING_SEND, UNKNOWN)
TAKER_FEE = 0.0005


def dca_client_order_id(grid_id: str, side: str, level: int, role: str, rev: int = 0) -> str:
    """kd-<bot>-<L|S><kademe><rol><rev>; ≤36 karakter. Kademe 1 = taban emir, 2.. = ek emirler."""
    safe = re.sub(r"[^A-Za-z0-9]", "", grid_id)[:16]
    return f"kd-{safe}-{'L' if side == LONG else 'S'}{level:02d}{role}{rev}"[:36]


def spec_from_plan(grid_id: str, symbol: str, plan: dict, *, tick: float) -> GridSpec:
    """`prepare_dca_plan` çıktısından motor ayarı. Kademe 1 = taban emir; hedef = o aşamadaki planlanan kâr hedefi."""
    levels = [GridLevelSpec(float(s["entry"]), float(s["tp_price"]), float(s["qty"])) for s in plan["stages"]]
    meta = {"take_profit_pct": float(plan["take_profit_pct"]), "tick": float(tick), "worst_loss_usdt": float(plan["worst_loss_usdt"]),
            "total_margin_usdt": float(plan["total_margin_usdt"]), "leverage": int(plan["leverage"]), "kind": "DCA"}
    if float(plan.get("trailing_pct") or 0.0) > 0:
        meta["trailing_pct"] = float(plan["trailing_pct"])       # takip eden hedef (opsiyonel); yoksa klasik tek limit hedef
    return GridSpec(grid_id, symbol, plan["direction"], levels, float(plan["stop_price"]), recycle=bool(plan.get("recycle")), meta=meta)


class DcaEngine(GridEngine):
    # ---------- kimlik ve emir niyeti ----------
    def __init__(self, exchange, store, spec, **kw) -> None:
        super().__init__(exchange, store, spec, **kw)
        self.prefix = f"kd-{re.sub(r'[^A-Za-z0-9]', '', spec.grid_id)[:16]}-"
        self._flat_seen = 0
        self._stop_unverified = 0               # pozisyon açıkken doğrulanmış stopun art arda görünmediği TUR sayısı
        self._tick_seq = self._watch_seq = 0    # bir turda koruma birkaç kez kontrol edilir; sayaç tur başına en çok bir kez artar
        self._avg_fallback_alerted = False      # borsa ortalaması okunamadığında yerel yedek uyarısı bir kez yazılır

    def _intent(self, tag: str | None = None, **row) -> str:
        cid = dca_client_order_id(self.gid, tag or self.spec.side, row["level"], row["role"], row["rev"])
        if row.get("role") == "E" and row["level"] == 1:       # taban emir piyasadan girer
            row = {**row, "type": MARKET, "price": 0.0}
        self.store.add_order({"client_id": cid, "grid_id": self.gid, "status": PENDING_SEND, "cycle": 0, **row})
        return cid

    # ---------- tur ve ortalama ----------
    def _cycle(self) -> int:
        return max((e["cycle"] for e in self.store.orders(self.gid, "E")), default=0)

    def _entries(self, cycle: int | None = None):
        cycle = self._cycle() if cycle is None else cycle
        return [e for e in self.store.orders(self.gid, "E") if e["cycle"] == cycle]

    def _base(self, cycle: int | None = None):
        return next((e for e in self._entries(cycle) if e["level"] == 1), None)

    def avg_entry(self, cycle: int | None = None) -> float | None:
        filled = [e for e in self._entries(cycle) if e["filled"] > EPS]
        if not filled:
            return None
        def px(e):
            return e["avg_price"] or e["price"] or self.spec.levels[e["level"] - 1].entry
        qty = sum(e["filled"] for e in filled)
        return sum(e["filled"] * px(e) for e in filled) / qty

    def _fill_price(self, e) -> float:
        return e["avg_price"] or e["price"] or self.spec.levels[e["level"] - 1].entry

    def _exchange_position(self) -> PositionInfo | None:
        """Borsanın gerçek pozisyonu (miktar, entryPrice, likidasyon). Desteklenmiyor ya da geçici okunamıyorsa None: çağıran yerel hesaba döner."""
        reader = getattr(self.ex, "position_info", None)
        if reader is None:
            return None
        try:
            return reader(self.sym)
        except ExchangeError:
            return None

    def _avg_mismatch(self, exchange_avg: float) -> str | None:
        """Borsanın ortalama girişi, defterdeki bu tur dolumlarıyla tutarlı mı? Tutarsızsa açıklama, değilse None.
        Kısmi çıkış YOKSA iki ortalama neredeyse aynı olmalıdır. Kısmi çıkıştan sonra defter ortalaması borsadan meşru olarak sapar
        (borsa kısmi kapanışta entryPrice'ı değiştirmez); bu durumda borsa ortalaması turun dolum fiyat aralığında kalmalıdır."""
        local = self.avg_entry()
        fills = [self._fill_price(e) for e in self._entries() if e["filled"] > EPS]
        if local is None or not fills:
            return None
        cycle = self._cycle()
        exited = any(o["filled"] > EPS and o["cycle"] == cycle for o in self.store.orders(self.gid) if o["role"] in ("T", "S", "C"))
        if not exited:
            gap = abs(exchange_avg - local) / local * 100
            if gap > AVG_MISMATCH_TOLERANCE_PCT:
                return f"Borsa ortalama girişi {exchange_avg:g}, defter ortalaması {local:g} (fark %{gap:.2f}, sınır %{AVG_MISMATCH_TOLERANCE_PCT:g})"
            return None
        lo, hi = min(fills) * (1 - AVG_RANGE_TOLERANCE_PCT / 100), max(fills) * (1 + AVG_RANGE_TOLERANCE_PCT / 100)
        if not lo <= exchange_avg <= hi:
            return f"Borsa ortalama girişi {exchange_avg:g}, bu turun dolum fiyat aralığının ({min(fills):g}–{max(fills):g}) dışında"
        return None

    def _intervene(self, reason: str) -> None:
        """Yeni girişleri iptal et ve müdahale iste. Stop ve mevcut hedef YERİNDE kalır (pozisyon korumasız bırakılmaz)."""
        self._cancel_all(roles=("E",))
        self._set(INTERVENTION_REQUIRED, reason)
        self._alert("kritik", f"MÜDAHALE GEREKLİ: {reason}. Yeni giriş emirleri iptal edildi; stop ve mevcut hedef iptal EDİLMEDİ.")

    def _liq_gap_problem(self, info: PositionInfo, avg: float) -> str | None:
        """Borsanın bildirdiği likidasyon fiyatı biliniyorsa stopun ondan yeterince önde olduğunu denetler (tahmine değil borsa verisine dayanır)."""
        if info.liq_price <= 0 or avg <= 0:
            return None
        gap_pct = self.sign * (self.spec.stop_price - info.liq_price) / avg * 100
        if gap_pct < LIQ_BUFFER_PCT:
            return f"Borsanın bildirdiği likidasyon fiyatı {info.liq_price:g} stop fiyatına ({self.spec.stop_price:g}) çok yakın ya da stopun ötesinde (tampon %{gap_pct:.2f}, en az %{LIQ_BUFFER_PCT:g})"
        return None

    def _target_price(self) -> float | None:
        """Kâr hedefi fiyatı: borsanın gerçek ortalama girişine göre. Borsa verisi geçici okunamazsa yerel ortalama (yedek).
        Borsa ile defter tutarsızsa (ortalama ya da likidasyon) hedef konmaz, bot INTERVENTION_REQUIRED olur ve None döner."""
        info = self._exchange_position()
        if info is not None and info.entry_price > 0 and abs(info.amount) > EPS:
            reason = self._avg_mismatch(info.entry_price) or self._liq_gap_problem(info, info.entry_price)
            if reason:
                self._intervene(reason)
                return None
            self._avg_fallback_alerted = False
            return self.tp_price(info.entry_price)
        if not self._avg_fallback_alerted:
            self._avg_fallback_alerted = True
            self._alert("uyarı", "Borsanın ortalama giriş fiyatı okunamadı; kâr hedefi geçici olarak defter ortalamasına göre hesaplanıyor.")
        return self.tp_price()

    def tp_price(self, avg: float | None = None) -> float | None:
        avg = self.avg_entry() if avg is None else avg
        if avg is None:
            return None
        pct, tick = self.spec.meta.get("take_profit_pct"), self.spec.meta.get("tick")
        if pct and tick:
            raw = Decimal(repr(avg * (1 + self.sign * pct / 100))) / Decimal(repr(tick))
            return float(raw.to_integral_value(rounding=ROUND_CEILING if self.spec.side == LONG else ROUND_FLOOR) * Decimal(repr(tick)))
        top = max(e["level"] for e in self._entries() if e["filled"] > EPS)      # meta yoksa: planlanan aşama hedefi
        return self.spec.levels[top - 1].target

    # ---------- gönderim ----------
    def tick(self) -> None:
        """Önce taban emir ve KORUMA (stop + hedef); ek emirler ancak taban dolup korunduktan sonraki turda gönderilir."""
        self._tick_seq += 1
        super().tick()
        base = self._base()
        if self.status == RUNNING and base is not None and base["status"] == FILLED and any(
                r["status"] == PENDING_SEND and r["level"] > 1 for r in self._entries()):
            super().tick()

    def _live_qty(self, role: str) -> float:
        """Defterde borsada DOĞRULANMIŞ (NEW/PARTIAL) çıkış emirlerinin kalan miktarı (stop: 'S', hedef: 'T')."""
        return round(sum(o["qty"] - o["filled"] for o in self.store.orders(self.gid, role)
                         if o["status"] in (NEW, PARTIAL) and o["side"] == self.exit_side), 8)

    def _stop_verified(self) -> bool:
        """Açık pozisyonun TAMAMI borsada doğrulanmış (NEW/PARTIAL) stopla korunuyor mu? Pozisyon yoksa True.
        Fazla kapsama (eski + yeni stop birlikte; ikisi de reduce-only) korumasızlık değildir."""
        net = self.grid_net()
        return net <= EPS or self._live_qty("S") >= net - 1e-8

    def _targets_verified(self) -> bool:
        if self._trail_on():
            return True                           # takip eden hedefte borsada limit hedef YOK; kapı yalnız doğrulanmış stoptur
        net = self.grid_net()
        return net <= EPS or self._live_qty("T") >= net - 1e-8

    def sync(self) -> bool:
        self._adopt_unsent()
        ok = super().sync()
        if ok:
            self._flat_seen = 0
        return ok

    FLAT_CONFIRM_TICKS = 3

    def _on_position_mismatch(self, actual: float, expected: float) -> bool:
        """Borsada pozisyon, bot başlamadan önceki düzeye (baseline) dönmüşse ama defter dolu görünüyorsa pozisyon elle kapatılmıştır.
        Korunacak pozisyon kalmadığı için tehlike yok: art arda FLAT_CONFIRM_TICKS tur aynı görülürse bu botun bekleyen emirleri
        iptal edilir ve bot 'Bitti' olur. Emirler iptal edilemezse bekler (halt etmez, sonraki tur yine dener)."""
        baseline = float(self.store.grid(self.gid)["baseline"])
        if abs(actual - baseline) > 1e-6 or abs(expected - baseline) <= 1e-6 or self.status not in (RUNNING, PAUSED, BLOCKED):
            self._flat_seen = 0
            return False
        self._flat_seen += 1
        if self._flat_seen < self.FLAT_CONFIRM_TICKS:
            return True
        if not self._cancel_all(roles=("E", "T", "S")):
            return True
        self._set(STOPPED, "Pozisyon borsada kapalı (elle kapatıldı)")
        self._alert("bilgi", "Pozisyon borsada kapalı görünüyor (elle kapatılmış). Bekleyen emirler iptal edildi; bot durdu.")
        return True

    def _adopt_unsent(self) -> None:
        """Çökme penceresi: emir borsaya gitti ama süreç ölünce defter PENDING_SEND kaldı. Gönderilmemiş saymadan önce borsaya kimlikle sorulur;
        varsa benimsenir (aksi halde pozisyon defterde yokmuş gibi görünüp bot korumasız pozisyonla BLOCKED kalırdı). Gönderilebilecek durumda
        olmayan ek emirlere (taban dolmadan) sorulmaz: onlar hiç gönderilmemiştir."""
        base = self._base()
        base_ready = base is not None and base["status"] == FILLED and base["filled"] > EPS
        cycle = self._cycle()
        for row in self.store.orders(self.gid):
            if row["status"] != PENDING_SEND:
                continue
            if row["role"] == "E" and row["level"] > 1 and row["cycle"] == cycle and not base_ready:
                continue
            try:
                info = self.ex.get_order(self.sym, row["client_id"])
            except ExchangeError:
                return                            # sorulamadı: bir şey yapma; normal eşitleme zaten hata verip emir göndermez
            if info is not None:
                self._apply(row["client_id"], info)

    def _send_pending(self) -> None:
        self._rearm_safeties()                    # stop yeniden doğrulandıysa, koruma yokken iptal edilen ek emirleri geri kur
        base = self._base()
        base_ready = base is not None and base["status"] == FILLED and base["filled"] > EPS
        protected = None                          # yavaş değerlendirilir: yalnız ek emir gönderilecekse
        for row in self.store.orders(self.gid):
            if row["role"] == "E" and self.status in (PAUSED, STOPPED, COMPLETED, CYCLE_DONE, EMERGENCY, EMERGENCY_CLOSED, INTERVENTION_REQUIRED, BLOCKED):
                continue
            if row["role"] == "E" and row["level"] > 1 and row["cycle"] == self._cycle():
                if not base_ready:
                    continue                      # taban emir dolmadan ek emir gönderilmez
                if row["status"] in (PENDING_SEND, UNKNOWN):
                    protected = (self._stop_verified() and self._targets_verified()) if protected is None else protected
                    if not protected:
                        continue                  # stop VE hedef borsada doğrulanmadan yeni ek emir açılmaz / belirsiz ek emir yeniden gönderilmez
            if row["status"] == PENDING_SEND:
                self._send(row)
            elif row["status"] == UNKNOWN:
                self._resolve_unknown(row)

    # ---------- koruma: stop + tek hedef ----------
    def _ensure_protection(self) -> None:
        if self.status in (EMERGENCY, EMERGENCY_CLOSED, STOPPED, COMPLETED, CYCLE_DONE, BLOCKED, INTERVENTION_REQUIRED):
            return
        stop_ok = self._ensure_stop()
        self._guard_growth()                      # stop tamamen doğrulanmadıysa: bekleyen ek emirleri iptal et + doğrula; kurulamıyorsa kapat
        if not stop_ok or self.status in (EMERGENCY, EMERGENCY_CLOSED, STOPPED, COMPLETED, CYCLE_DONE, BLOCKED, INTERVENTION_REQUIRED):
            return
        self._ensure_target()

    def _flush_protective(self) -> None:
        """Defterde yazılı ama gönderilmemiş (çökme) koruyucu emirleri (stop/hedef) ÖNCE gönder. Aksi halde bu satırlar 'kapsıyor' sayılıp yeni
        emir kurulmaz, ama borsada da yoktur: yeniden başlatmada koruma doğrulanamazdı. Aynı kimlikle gönderilir: borsada varsa kimlik tekrarı
        reddedilir ve mevcut emir benimsenir (çift emir olmaz). Belirsiz (UNKNOWN) satırların çözümü normal turda, 'yok' teyidiyle yapılır."""
        for row in self.store.orders(self.gid):
            if row["role"] in ("S", "T") and row["status"] == PENDING_SEND:
                self._send(row)

    # ---------- doğrulanmış stop yokken pozisyon büyüyemez ----------
    def _open_safety_rows(self) -> list:
        """Borsada AKTİF olabilecek ek (DCA) emir satırları: NEW/PARTIAL ve durumu belirsiz (UNKNOWN). PENDING_SEND borsaya hiç gitmemiştir
        (çökme penceresi `_adopt_unsent` ile eşitlemede kapatılır); gönderim kapısı onları zaten tutar."""
        return [e for e in self._entries() if e["level"] > 1 and e["status"] in (NEW, PARTIAL, UNKNOWN)]

    def _disarm_safeties(self) -> bool:
        """Borsada bekleyen TÜM ek emirleri iptal eder ve iptali borsadan DOĞRULAR. İptal yanıtı kaybolduysa (zaman aşımı) emrin gerçek durumu
        kimlikle sorulur. Dönüş: True = hiçbir ek emir aktif kalmadı; False = en az biri iptal doğrulanamadı (pozisyon büyüyebilir)."""
        rows = self._open_safety_rows()
        if not rows:
            return True
        levels = {int(r["level"]) for r in rows}
        for _ in range(CANCEL_VERIFY_ATTEMPTS):
            for row in self._open_safety_rows():
                self._cancel(row["client_id"])
            for row in self._open_safety_rows():
                self._adopt(row["client_id"], quiet=True)        # iptal yanıtı kayıp olabilir: borsanın gerçek durumunu sor
            if not self._open_safety_rows():
                break
        self._save_disarmed(set(self._disarmed()) | levels)
        remaining = self._open_safety_rows()
        if remaining:
            self._alert("kritik", f"Doğrulanmış stop yokken bekleyen ek emir iptal EDİLEMEDİ/doğrulanamadı: {[r['client_id'] for r in remaining]}")
        else:
            self._alert("uyarı", "Doğrulanmış stop yok: bekleyen ek emirler iptal edildi ve borsada doğrulandı; stop yeniden doğrulanınca yeniden kurulacak.")
        return not remaining

    def _guard_growth(self) -> None:
        """Pozisyon açıkken borsada doğrulanmış stop TAMAMINI kapsamıyorsa pozisyonun büyümesine izin verme:
        1) yeni ek emir zaten gönderilmez (kapı), 2) bekleyen ek emirler iptal edilir ve doğrulanır; doğrulanamazsa HEMEN kapatma yoluna geçilir,
        3) stop her kontrolde yeniden denenir; art arda STOP_RETRY_TICKS TUR kurulamazsa pozisyon piyasadan kapatılır (kullanıcıyı beklemez)."""
        if self.status in (EMERGENCY, EMERGENCY_CLOSED, STOPPED, COMPLETED, CYCLE_DONE, BLOCKED, INTERVENTION_REQUIRED):
            return
        if self.grid_net() <= EPS or self._stop_verified():
            self._stop_unverified = 0
            return
        if not self._disarm_safeties():
            if not self._protect_only:
                self._flatten("Doğrulanmış stop yokken bekleyen DCA emri iptal edilemedi")
            return
        if self._protect_only or self._watch_seq == self._tick_seq:
            return                                # kurtarma/akış yolu sayacı artırmaz; bir turda en çok bir kez sayılır
        self._watch_seq = self._tick_seq
        self._stop_unverified += 1
        if self._stop_unverified >= STOP_RETRY_TICKS:
            self._flatten(f"Açık pozisyon için doğrulanmış koruyucu stop {STOP_RETRY_TICKS} turdur kurulamadı")

    def _flatten(self, reason: str) -> str:
        """Pozisyonu güvenle kapat: mevcut ACİL DURDUR akışı (bekleyen girişleri iptal → kapatma emri → borsadan doğrula). Çift emir riski:
        kapanış emri tek ve kimliklidir; yanıt kaybolursa önce borsaya sorulur, sorulamıyorsa göndermeden beklenir. Kapanış doğrulanamazsa
        emergency_stop INTERVENTION_REQUIRED yapar ve koruyucu stopları İPTAL ETMEZ (son güvenlik durumu)."""
        self._alert("kritik", f"{reason}: pozisyon piyasadan kapatılıyor, yeni giriş yok.")
        status = self.emergency_stop()
        self.store.set_grid(self.gid, halt_reason=f"{reason} → {status}")
        return status

    def _disarmed(self) -> list[int]:
        try:
            return sorted(int(x) for x in (json.loads(self.store.grid(self.gid)["spec"]).get("meta") or {}).get("disarmed", []))
        except (ValueError, TypeError, KeyError):
            return []

    def _save_disarmed(self, levels: set[int]) -> None:
        """Koruma yokken iptal edilen ek emir kademeleri (yeniden başlatmada da kaybolmasın diye defterdeki ayara yazılır)."""
        raw = json.loads(self.store.grid(self.gid)["spec"])
        raw.setdefault("meta", {})["disarmed"] = sorted(levels)
        self.store.set_grid(self.gid, spec=json.dumps(raw))

    def _rearm_safeties(self) -> None:
        """Stop ve hedef borsada yeniden doğrulandıysa, koruma yokken iptal edilen ek emirleri (fiyat hâlâ uygunsa) yeni kimlikle geri kurar.
        Önceki iptaller doğrulanmadan hiçbir ek emir kurulmaz; borsa fiyatı bilinmiyorsa kurulmaz (bekler)."""
        levels = self._disarmed()
        base = self._base()
        if (not levels or self.status != RUNNING or base is None or base["status"] != FILLED or self.grid_net() <= EPS
                or not (self._stop_verified() and self._targets_verified()) or self._open_safety_rows()):
            return
        info = self._exchange_position()
        price = float(info.mark_price) if info is not None else 0.0
        if not price > 0:
            return
        reopened = skipped = 0
        for level in levels:
            mine = [e for e in self._entries() if e["level"] == level]
            last = max(mine, key=lambda e: e["rev"]) if mine else None
            if last is None or last["status"] != CANCELED or last["filled"] > EPS:
                continue
            if not self._entry_still_valid(level, price):
                skipped += 1
                continue
            lv = self.spec.levels[level - 1]
            self._intent(role="E", level=level, rev=max(e["rev"] for e in mine) + 1, cycle=self._cycle(), side=self._entry_side(level),
                         type=LIMIT, price=lv.entry, stop_price=0.0, qty=lv.qty, reduce_only=0, tag=self._level_tag(level))
            reopened += 1
        self._save_disarmed(set())
        self._alert("bilgi", f"Stop yeniden doğrulandı: {reopened} ek emir yeniden kuruldu" + (f", fiyatın yanlış tarafında kalan {skipped} kademe atlandı." if skipped else "."))

    def _ensure_stop(self) -> bool:
        """Grid motorundaki stop kuralı: dolu miktarın tamamı, önce yeni stop doğrulanır, sonra eskiler iptal. False: devam etme."""
        self._flush_protective()
        net = self.grid_net()
        stops = self.store.orders(self.gid, "S")
        active = [s for s in stops if s["status"] in ACTIVE]
        covered = round(sum(s["qty"] - s["filled"] for s in active), 8)
        if net <= EPS:
            for old in active:
                self._cancel(old["client_id"])
            return True
        if abs(covered - net) <= 1e-8:
            return True
        self._last_reject = 0
        cid = self._intent(role="S", level=0, rev=len(stops), cycle=self._cycle(), side=self.exit_side, type=STOP_MARKET, price=0.0,
                           stop_price=self.spec.stop_price, qty=net, reduce_only=1)
        self._send(self.store.order(cid))
        status = self.store.order(cid)["status"]
        if status in (NEW, PARTIAL):
            for old in active:
                self._cancel(old["client_id"])
            return True
        if self._last_reject == WOULD_TRIGGER_IMMEDIATELY:
            self._alert("uyarı", "Fiyat stop seviyesini geçmiş; DCA pozisyonu piyasadan kapatılıyor.")
            self._cancel_all(roles=("E", "T"))
            self._close(net)
            self._sync_for_emergency()
            self._stop_after_forced_close()
        elif status == REJECTED:
            # Kullanıcıyı beklemeden: bekleyen ek emirler `_guard_growth`'ta iptal+doğrulanır, stop sonraki kontrollerde yeniden denenir,
            # STOP_RETRY_TICKS tur sonunda hâlâ kurulamazsa pozisyon kapatılır. Eski stoplar iptal EDİLMEDİ.
            self._alert("kritik", "Koruyucu stop kurulamadı (borsa reddetti); yeniden denenecek. Bekleyen ek emirler iptal ediliyor, eski stoplar korunuyor.")
        else:
            self._alert("kritik", "Yeni koruyucu stop henüz doğrulanamadı; eski stoplar korunuyor, tekrar denenecek.")
        return False

    def _ensure_target(self) -> None:
        net = self.grid_net()
        targets = self.store.orders(self.gid, "T")
        active = [t for t in targets if t["status"] in ACTIVE]
        if net <= EPS:
            for old in active:
                self._cancel(old["client_id"])
            return
        if self._trail_on():                     # takip eden hedef: limit hedef konmaz; var olan hedef emri kaldırılır, stop kâr yönünde çekilir
            for old in active:
                self._cancel(old["client_id"])
            self._ensure_trailing(net)
            return
        price = self._target_price()           # borsanın gerçek ortalamasına göre; tutarsızlıkta INTERVENTION_REQUIRED ve None
        if price is None:
            return
        tick = float(self.spec.meta.get("tick") or 0)
        covered = round(sum(t["qty"] - t["filled"] for t in active), 8)
        same_price = bool(active) and all(abs(t["price"] - price) <= (tick / 2 if tick else 1e-9) for t in active)
        if abs(covered - net) <= 1e-8 and same_price:
            return
        for old in active:                       # hedef koruyucu değil: önce eskiyi kaldır (aynı miktarı iki emir kapatmasın)
            if not self._cancel(old["client_id"]):
                self._alert("uyarı", "Eski hedef iptal edilemedi; yeni hedef konmadı, tekrar denenecek.")
                return
        net = self.grid_net()                    # iptal yarışında hedef dolmuş olabilir: yeniden oku
        if net <= EPS:
            return
        cid = self._intent(role="T", level=0, rev=len(self.store.orders(self.gid, "T")), cycle=self._cycle(), side=self.exit_side, type=LIMIT,
                           price=price, stop_price=0.0, qty=net, reduce_only=1)
        self._send(self.store.order(cid))

    # ---------- takip eden hedef (opsiyonel) ----------
    def _trail_pct(self) -> float:
        try:
            return max(0.0, float((self.spec.meta or {}).get("trailing_pct") or 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _trail_on(self) -> bool:
        return self._trail_pct() > 0

    def _trail_state(self) -> dict:
        raw = (self.spec.meta or {}).get("trail")
        return dict(raw) if isinstance(raw, dict) else {}

    def _ensure_trailing(self, net: float) -> None:
        """Hedefe (borsanın gerçek ortalamasına göre ortalama + kâr hedefi %) ulaşılınca stopu kâr yönünde tepe fiyatın `trailing_pct` gerisine çeker.
        Güvenlik: (1) koruyucu stop her an yerinde: yeni stop borsada doğrulanmadan eskisi iptal edilmez; (2) stop yalnız kâr yönünde ilerler, geri gitmez;
        (3) fiyat okunamazsa ya da borsa ortalaması tutarsızsa hiçbir şey yapılmaz (mevcut stop korur); (4) yeni stop reddedilirse eski stop kalır,
        sonraki turda yeniden denenir; (5) stop dolarsa `_detect_end` kârlı çıkışı hedef dolmuş sayar (tur biter, yeniden başlatma kurallar dahilinde)."""
        pct = self._trail_pct()
        activation = self._target_price()      # borsa ortalamasına göre; tutarsızlıkta INTERVENTION_REQUIRED ve None
        if activation is None or self.status != RUNNING and self.status != PAUSED:
            return
        info = self._exchange_position()
        price = float(info.mark_price) if info is not None else 0.0
        if not price > 0:
            return
        sign = self.sign
        state = self._trail_state()
        if not state and sign * (price - activation) < 0:
            return                             # hedefe henüz ulaşılmadı: klasik stop korur
        peak = float(state.get("peak") or price)
        peak = max(peak, price) if sign > 0 else min(peak, price)
        if state and peak != float(state.get("peak") or 0.0):    # yeni tepe: stop çekilmese bile saklanır (geri çekilmede kaybolmasın)
            self.spec.meta["trail"] = {**state, "peak": peak}
            self.store.set_grid(self.gid, spec=json.dumps(self.spec.as_json()))
            state = self._trail_state()
        tick = float(self.spec.meta.get("tick") or 0)
        raw = peak * (1 - sign * pct / 100)
        if tick > 0:
            unit = Decimal(repr(tick))
            raw = float((Decimal(repr(raw)) / unit).to_integral_value(rounding=ROUND_FLOOR if sign > 0 else ROUND_CEILING) * unit)
        current = float(self.spec.stop_price)
        gain = sign * (raw - current)
        if gain <= 0 or sign * (price - raw) <= 0:
            return                             # stop geriye gitmez; fiyatın yanlış tarafına da konmaz
        if state and gain / price * 100 < TRAIL_MIN_STEP_PCT:
            return                             # iyileşme küçük: emir trafiği yaratma (tepe sonraki turlarda zaten izlenir)
        stops = self.store.orders(self.gid, "S")
        active = [x for x in stops if x["status"] in ACTIVE]
        self._last_reject = 0
        cid = self._intent(role="S", level=0, rev=len(stops), cycle=self._cycle(), side=self.exit_side, type=STOP_MARKET, price=0.0,
                           stop_price=raw, qty=net, reduce_only=1)
        self._send(self.store.order(cid))
        if self.store.order(cid)["status"] not in (NEW, PARTIAL):
            return                             # yeni stop doğrulanamadı/reddedildi: eski stop korur, sonraki turda tekrar denenir
        for old in active:
            self._cancel(old["client_id"])
        first = not state
        self.spec.stop_price = raw
        self.spec.meta["trail"] = {"peak": peak, "stop": raw}
        self.store.set_grid(self.gid, spec=json.dumps(self.spec.as_json()))
        if first:
            self._alert("bilgi", f"Takip eden hedef başladı: stop {raw:g} seviyesine çekildi (tepe {peak:g}, takip %{pct:g}); fiyat yükseldikçe stop kâr yönünde izler.")

    def _trail_exit_profitable(self, stops: list) -> bool:
        """Takip eden hedefte çekilmiş stopla (kârda) çıkıldı mı? Evet ise bu bir hedef dolumudur, zarar stopu değildir."""
        if not self._trail_on() or not self._trail_state():
            return False
        avg = self.avg_entry()
        if avg is None:
            return False
        return any(s["filled"] > EPS and self.sign * ((s["avg_price"] or s["stop_price"]) - avg) > 0 for s in stops if s["cycle"] == self._cycle())

    def _close(self, qty: float) -> None:
        """Piyasadan reduceOnly kapanış (acil durdurma / stop geçilmiş); emir turu taşır (özet için)."""
        rows = [r for r in self.store.orders(self.gid, "C") if r["status"] not in TERMINAL]
        if rows:
            if rows[0]["status"] == UNKNOWN:
                self._resolve_unknown(rows[0])    # kapanış yanıtı kayıp: ÖNCE borsaya sor; yalnız 'yok' teyidiyle aynı kimlikle tekrar gönderilir
            else:
                self._send(rows[0])
            return
        cid = self._intent(role="C", level=0, rev=len(self.store.orders(self.gid, "C")), cycle=self._cycle(), side=self.exit_side, type=MARKET,
                           price=0.0, stop_price=0.0, qty=qty, reduce_only=1)
        self._send(self.store.order(cid))

    # ---------- bitiş ----------
    def _detect_end(self) -> None:
        if self.status in (EMERGENCY, EMERGENCY_CLOSED, INTERVENTION_REQUIRED, STOPPED, COMPLETED, CYCLE_DONE):
            return
        net = abs(self.grid_net())
        base = self._base()
        if base is not None and base["status"] == REJECTED and net <= EPS:
            self._cancel_all(roles=("E", "T", "S"))
            self._set(STOPPED, "Taban emir reddedildi")
            self._alert("kritik", "Taban emir borsa tarafından reddedildi; DCA başlamadı.")
            return
        if net > EPS:
            return
        cycle = self._cycle()
        stops = self.store.orders(self.gid, "S")
        trail_win = self._trail_exit_profitable(stops)
        if any(s["filled"] > EPS for s in stops) and not trail_win:
            self._cancel_all(roles=("E", "T", "S"))
            self._set(STOPPED, "Stop tetiklendi")
            self._alert("bilgi", "Stop tetiklendi: bekleyen emirler iptal edildi, yeni emir açılmayacak.")
            return
        if trail_win or any(t["filled"] > EPS and t["cycle"] == cycle for t in self.store.orders(self.gid, "T")):
            self._cancel_all(roles=("E", "T", "S"))
            if self.spec.recycle:
                self._set(CYCLE_DONE, "Kâr hedefi doldu; yeni tur bekleniyor")
                self._alert("bilgi", "Kâr hedefi doldu: tur tamamlandı, yeni tur için risk kapıları yeniden kontrol edilecek.")
            else:
                self._set(COMPLETED)
                self._alert("bilgi", "Kâr hedefi doldu: DCA tamamlandı.")

    # ---------- yeni tur ----------
    def begin_cycle(self, spec: GridSpec) -> bool:
        """CYCLE_DONE'da, çağıranın yeniden doğruladığı yeni planla yeni tur başlatır. Borsada bu botun emri ya da pozisyonu
        kalmışsa BAŞLAMAZ (False). Emri gönderim normal tur yapar."""
        if self.status != CYCLE_DONE or spec.grid_id != self.gid or spec.symbol != self.sym or spec.side != self.spec.side:
            return False
        try:
            leftovers = [o for o in self.ex.open_orders(self.sym) if o.client_id.startswith(self.prefix)]
            actual = self.ex.position(self.sym)
        except Exception:
            return False
        if leftovers or abs(actual - self.store.grid(self.gid)["baseline"]) > 1e-6 or abs(self.grid_net()) > EPS:
            return False
        cycle = self._cycle() + 1
        self._stop_unverified = 0
        self.spec = spec
        self.store.set_grid(self.gid, spec=json.dumps(spec.as_json()))
        prev = self.store.orders(self.gid, "E")
        for i, lv in enumerate(spec.levels, 1):
            rev = max((e["rev"] for e in prev if e["level"] == i), default=-1) + 1
            self._intent(role="E", level=i, rev=rev, cycle=cycle, side=self._entry_side(i), type=LIMIT, price=lv.entry, stop_price=0.0,
                         qty=lv.qty, reduce_only=0, tag=self._level_tag(i))
        self._set(RUNNING, "")
        return True

    def reopen_entries(self, price: float) -> tuple[int, int]:
        """ARM sonrası devam: yalnız EK emirler yeniden açılır (taban emir asla yeniden piyasadan gönderilmez)."""
        if self.status != PAUSED or not price > 0:
            return 0, 0
        base = self._base()
        opened = skipped = 0
        entries = self._entries()
        if base is not None and base["status"] == FILLED:
            for index, lv in enumerate(self.spec.levels, 1):
                if index == 1:
                    continue
                mine = [e for e in entries if e["level"] == index]
                if not mine:
                    continue
                last = max(mine, key=lambda e: e["rev"])
                if last["status"] != "CANCELED" or last["filled"] > EPS:
                    continue
                if not self._entry_still_valid(index, price):
                    skipped += 1
                    continue
                self._intent(role="E", level=index, rev=max(e["rev"] for e in mine) + 1, cycle=self._cycle(), side=self._entry_side(index),
                             type=LIMIT, price=lv.entry, stop_price=0.0, qty=lv.qty, reduce_only=0, tag=self._level_tag(index))
                opened += 1
        self._set(RUNNING)
        return opened, skipped

    # ---------- özet (gösterim) ----------
    def stats(self) -> dict:
        orders = self.store.orders(self.gid)
        cycle = self._cycle()
        sign = self.sign
        realized = 0.0
        fees = 0.0
        completed = 0
        for c in range(cycle + 1):
            entries = [e for e in orders if e["role"] == "E" and e["cycle"] == c and e["filled"] > EPS]
            if not entries:
                continue
            qty_in = sum(e["filled"] for e in entries)
            avg = self.avg_entry(c) or 0.0
            exits = [o for o in orders if o["role"] in ("T", "S", "C") and o["cycle"] == c and o["filled"] > EPS]
            out_qty = sum(o["filled"] for o in exits)
            for o in exits:
                realized += sign * ((o["avg_price"] or o["price"] or o["stop_price"]) - avg) * o["filled"]
                fees += ((o["avg_price"] or o["price"] or o["stop_price"]) * o["filled"]) * TAKER_FEE
            fees += avg * qty_in * TAKER_FEE
            trail_win = self._trail_on() and any(o["role"] == "S" and sign * ((o["avg_price"] or o["stop_price"]) - avg) > 0 for o in exits)
            if abs(qty_in - out_qty) <= 1e-8 and (any(o["role"] == "T" for o in exits) or trail_win):
                completed += 1
        net = self.grid_net()
        return {"cycle": cycle + 1, "completed_cycles": completed, "position_qty": net, "avg_entry": self.avg_entry(), "tp_price": self.tp_price() if net > EPS else None,
                "stop_price": self.spec.stop_price, "trailing_pct": self._trail_pct() or None, "trail_active": bool(self._trail_state()), "stages_filled": len([e for e in self._entries() if e["filled"] > EPS]),
                "stages_total": len(self.spec.levels), "realized_usdt": round(realized - fees, 8), "fees_usdt": round(fees, 8)}
