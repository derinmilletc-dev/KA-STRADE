"""KAIS FARMER orkestratörü (C7) — hesap başına TEK örnek. SAF çekirdek: ağ/FastAPI yok; her dış bağımlılık bir PORT'tur.

Sorumluluk: Farmer durumu, motor koleksiyonu, aday önbelleği kullanımı, slot/yeniden-doldurma, paylaşımlı borsa görüntüsü, ARM/güvenlik kapıları,
kapanmış-mum ölü kontrolü, kurtarma, acil kapanış, arayüz görüntüsü. Portföy mantığı tek-pozisyon motoruna (`FarmerEngine`) GİRMEZ.

Değişmezler:
  * Giriş yalnız RUNNING'de; ARM bitti/güvenlik engeli/STOP → YENİ GİRİŞ durur, açık pozisyonların TP/STOP korumasi (motor turu) SÜRER.
  * Slot, flat doğrulanınca bırakılır; PnL doğrulaması slotu bloke etmez.
  * `max_positions` "N işlem aç" değildir: onaydan geçen kadar, boş slot kadar.
  * Beş motor ayrı ayrı REST sorgusu yapmaz: okumalar tur başına TEK `ExchangeSnapshot`'tan (`SharedExchange`) servis edilir."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

from ..grid_engine.engine import (BLOCKED, COMPLETED, EMERGENCY, EMERGENCY_CLOSED, INTERVENTION_REQUIRED, RUNNING as ENGINE_RUNNING, STOPPED)
from ..grid_engine.ports import EPS, LONG, SHORT, ExchangeError, OrderInfo
from ..grid_engine.store import TERMINAL
from . import DISPLAY_NAME
from .confirm import (ARM_BLOCKED, CONFIRMED, ENTRY_LOCKED, MARGIN_CAP, OCCUPIED, SAFETY_GATE, ConfirmParams, RiskView)
from .feed import FarmerFeed
from .ids import farmer_client_order_id, farmer_grid_prefix, is_farmer_client_id
from .margin import MarginGuard
from .store import FarmerStore

OFF, RUNNING, ENTRY_PAUSED_ARM, ENTRY_PAUSED_SAFETY, STOPPING, INTERVENTION = (
    "OFF", "RUNNING", "ENTRY_PAUSED_ARM", "ENTRY_PAUSED_SAFETY", "STOPPING", "INTERVENTION_REQUIRED")
STATES = (OFF, RUNNING, ENTRY_PAUSED_ARM, ENTRY_PAUSED_SAFETY, STOPPING, INTERVENTION)
CANDLE_MS = 15 * 60 * 1000
_ENDED = (COMPLETED, STOPPED, EMERGENCY_CLOSED)
# PnL yeniden deneme aralıkları (sn): mevcut v25_execution.ADOPTION_PERSISTENCE_RETRY_BACKOFF_SECONDS ile aynı standart. Tükenince UNVERIFIED kalır.
PNL_RETRY_BACKOFF_SECONDS = (5, 15, 60, 300, 900)
PNL_MAX_ATTEMPTS = len(PNL_RETRY_BACKOFF_SECONDS) + 1
_TRANSIENT_REJECTS = {ARM_BLOCKED, SAFETY_GATE, MARGIN_CAP, OCCUPIED}     # yalnız bunlar aynı mumda yeniden denenebilir


def _b36(n: int) -> str:
    digits, out = "0123456789abcdefghijklmnopqrstuvwxyz", ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out or "0"


# ------------------------------------------------------------------ portlar
@dataclass
class ExchangeSnapshot:
    """Tur başına TEK okuma: pozisyonlar (işaretli), açık emirler (algo dahil), işaret fiyatları."""
    ok: bool
    at: float
    positions: dict[str, float] = field(default_factory=dict)
    open_orders: list[OrderInfo] = field(default_factory=list)
    mark_prices: dict[str, float] = field(default_factory=dict)
    error: str = ""


class SnapshotSource(Protocol):
    def read(self) -> ExchangeSnapshot: ...


@dataclass(frozen=True)
class GateState:
    arm_blocked: tuple[str, ...] = ()
    safety_blocked: tuple[str, ...] = ()


class Gates(Protocol):
    def entry_gates(self) -> GateState: ...                       # ARM / canlı kilit / yürütme-hazırlık / bakım / account_fence ...
    def foreign_occupied(self, symbol: str) -> tuple[str, ...]: ...  # Original V2 / Manuel / Grid / DCA doluluğu (mevcut sistem)


class Reservations(Protocol):
    def reserve(self, symbol: str, grid_id: str) -> bool: ...
    def mark_submitted(self, grid_id: str, client_order_id: str) -> None: ...   # mevcut mutabakat bu kimlikle borsaya sorar
    def consume(self, grid_id: str) -> None: ...
    def release(self, grid_id: str) -> None: ...
    def orphans(self) -> list[str]: ...                           # Farmer'a ait SUBMITTED, çözülmemiş rezervasyonların grid_id'leri


class Universe(Protocol):
    def symbols(self) -> list[str]: ...


class EngineFactory(Protocol):
    def create(self, grid_id: str, candidate: Any, params: ConfirmParams, exchange: Any) -> Any: ...   # fiyat/filtre/izole-marjin/kaldıraç HAZIRLIĞI; emir YOK
    def rebuild(self, grid_row: Any, exchange: Any) -> Any: ...


@dataclass(frozen=True)
class PnlOutcome:
    state: str                                                    # VERIFIED | UNVERIFIED | PENDING
    gross: float | None = None
    commission: float | None = None
    funding: float | None = None
    net: float | None = None
    error: str = ""


class PnlVerifier(Protocol):
    def verify(self, result: Mapping[str, Any]) -> PnlOutcome: ...


class UnconfiguredPnl:
    """C8'e kadar: doğrulayıcı yok → UNVERIFIED (varsayım yok). Slot bırakmayı etkilemez."""
    def verify(self, result: Mapping[str, Any]) -> PnlOutcome:
        return PnlOutcome("UNVERIFIED", error="PNL_VERIFIER_NOT_CONFIGURED")


# ------------------------------------------------------------------ paylaşımlı borsa
class SharedExchange:
    """Motorların kullandığı `ExchangePort`. Okumalar (position/open_orders) tur görüntüsünden gelir; yazmalar gerçek borsaya gider ve o sembolün
    görüntüsünü geçersiz kılar (sonraki okuma doğrudan). Görüntü yok/bayat/okunamaz → doğrudan okuma (davranış motorun mevcut hâli)."""

    def __init__(self, real: Any, *, max_age_s: float = 15.0, clock: Callable[[], float] = time.time) -> None:
        self.real, self.max_age_s, self.clock = real, max_age_s, clock
        self._snap: ExchangeSnapshot | None = None
        self._dirty: set[str] = set()
        self.served = 0
        self.passthrough = 0

    def use(self, snap: ExchangeSnapshot | None) -> None:
        self._snap, self._dirty = snap, set()

    def _fresh(self, symbol: str) -> bool:
        s = self._snap
        return bool(s is not None and s.ok and symbol not in self._dirty and 0 <= self.clock() - s.at <= self.max_age_s)

    def position(self, symbol: str) -> float:
        if self._fresh(symbol):
            self.served += 1
            return float(self._snap.positions.get(symbol, 0.0))       # type: ignore[union-attr]
        self.passthrough += 1
        return self.real.position(symbol)

    def open_orders(self, symbol: str) -> list[OrderInfo]:
        if self._fresh(symbol):
            self.served += 1
            return [o for o in self._snap.open_orders if o.symbol == symbol]   # type: ignore[union-attr]
        self.passthrough += 1
        return self.real.open_orders(symbol)

    def get_order(self, symbol: str, client_id: str) -> OrderInfo | None:
        self.passthrough += 1
        return self.real.get_order(symbol, client_id)

    def place_order(self, **kw: Any) -> OrderInfo:
        self._dirty.add(kw["symbol"])
        return self.real.place_order(**kw)

    def cancel_order(self, symbol: str, client_id: str) -> OrderInfo:
        self._dirty.add(symbol)
        return self.real.cancel_order(symbol, client_id)

    def __getattr__(self, name: str) -> Any:                      # adaptörün diğer (okuma) yetenekleri aynen geçer
        return getattr(self.real, name)


# ------------------------------------------------------------------ ayarlar
@dataclass(frozen=True)
class FarmerSettings:
    confirm: ConfirmParams
    max_positions: int = 5
    total_margin_cap: float = 100.0
    dead_score: float | None = None            # None = ölü pozisyon kapanışı kapalı
    safety_margin_usdt: float = 0.05
    dead_confirmations: int = 2

    def as_dict(self) -> dict[str, Any]:
        c = self.confirm
        return {"variant": c.variant, "min_score": c.min_score, "margin_usdt": c.margin_usdt, "leverage": c.leverage, "target_usdt": c.target_usdt,
                "max_loss_usdt": c.max_loss_usdt, "max_positions": self.max_positions, "total_margin_cap": self.total_margin_cap,
                "dead_score": self.dead_score, "safety_margin_usdt": self.safety_margin_usdt, "dead_confirmations": self.dead_confirmations}


class Orchestrator:
    def __init__(self, *, account_key: str, store: FarmerStore, settings: FarmerSettings, exchange: Any, snapshot_source: SnapshotSource,
                 feed: FarmerFeed, gates: Gates, reservations: Reservations, universe: Universe, factory: EngineFactory,
                 set_demand: Callable[[list[str]], None] = lambda symbols: None, pnl: PnlVerifier | None = None,
                 daily_loss: Callable[[], tuple[str, ...]] | None = None, clock: Callable[[], float] = time.time,
                 new_grid_id: Callable[[], str] | None = None) -> None:
        self.account_key, self.store, self.settings = account_key, store, settings
        self.shared = SharedExchange(exchange, clock=clock)
        self.snapshot_source, self.feed, self.gates, self.reservations = snapshot_source, feed, gates, reservations
        self.universe, self.factory, self._set_demand, self.pnl = universe, factory, set_demand, pnl or UnconfiguredPnl()
        self.guard = MarginGuard(store, account_key, max_positions=settings.max_positions, total_margin_cap=settings.total_margin_cap,
                                 position_margin=settings.confirm.margin_usdt, daily_loss=daily_loss)
        self.clock = clock
        self._seq = 0
        self._new_gid = new_grid_id or self._default_gid
        self.engines: dict[str, Any] = {}
        self.meta: dict[str, dict[str, Any]] = {}
        self.dead_info: dict[str, dict[str, Any]] = {}
        self.base = OFF
        self.reason = ""
        self.snapshot: ExchangeSnapshot | None = None
        self._scan_candle = 0
        self._dead_candle: dict[str, int] = {}
        self._tried: dict[str, str] = {}           # sembol -> bu mumda verilen kalıcı ret (yeniden onaylanmaz)
        self._tried_candle = 0
        self._demand: list[str] = []
        self._pnl_due: dict[str, float] = {}
        self.recovered = False
        self.events: list[str] = []

    # ---------- yardımcılar ----------
    def _default_gid(self) -> str:
        """Kimlik ≤ 16 alfanümerik karakter (emir kimliği `kf-<16>-...` bunu keser; kesilmiş hâli de BENZERSİZ olmalı)."""
        while True:
            self._seq += 1
            gid = "F" + _b36(int(self.clock() * 1000)).rjust(9, "0")[-9:] + _b36(self._seq).rjust(3, "0")[-3:]
            if self.store.grid(gid) is None:
                return gid

    def _event(self, text: str) -> None:
        self.events.append(text)
        del self.events[:-50]

    def _persist(self) -> None:
        self.store.save_bot(self.account_key, self.base, self.settings.as_dict(), pause_reason=self.reason)

    def held_symbols(self) -> list[str]:
        return [e.sym for e in self.engines.values()]

    # ---------- durum ----------
    @property
    def state(self) -> str:
        if self.base != RUNNING:
            return self.base
        gate = self.gates.entry_gates()
        if gate.arm_blocked:
            return ENTRY_PAUSED_ARM
        if gate.safety_blocked or self._snapshot_bad() or (self.guard.daily_loss() if self.guard.daily_loss else ()):
            return ENTRY_PAUSED_SAFETY
        return RUNNING

    def _snapshot_bad(self) -> bool:
        s = self.snapshot
        return s is None or not s.ok

    def start(self) -> str:
        if self.base == INTERVENTION:
            return self.base
        if not self.recovered:
            self.reason = "RECOVERY_NOT_DONE"
            return self.base
        self.base, self.reason = RUNNING, ""
        self._persist()
        return self.base

    def stop_bot(self) -> str:
        """Yeni giriş yok; açık pozisyonlar KAPANMAZ, koruma sürer."""
        if self.base == INTERVENTION:
            return self.base
        self.base = STOPPING if self.engines else OFF
        self._persist()
        return self.base

    def acknowledge_intervention(self) -> str:
        """Kullanıcı müdahalesi sonrası: uzlaştırma TEMİZ çıkarsa OFF'a döner (RUNNING'e KENDİLİĞİNDEN dönmez; start() ayrıca gerekir)."""
        if self.base == INTERVENTION:
            self.shared.use(None)
            self.snapshot = self._read_snapshot()
            if not self._audit(self.snapshot):
                self.base, self.reason, self.recovered = OFF, "", True
                self._persist()
        return self.base

    @staticmethod
    def _engine_blocking(engine: Any) -> bool:
        return engine.status in (INTERVENTION_REQUIRED, BLOCKED, EMERGENCY)

    # ---------- ana tur ----------
    def tick(self, now_ms: int | None = None) -> dict[str, Any]:
        now_ms = int(self.clock() * 1000) if now_ms is None else int(now_ms)
        if self.base == OFF and not self.engines:
            self.retry_pnl()                                        # yalnız bekleyen PnL doğrulaması (varsa); başka okuma yok
            return {"state": OFF, "idle": True}                    # Farmer kapalı ve pozisyon yok: hiçbir borsa/akış okuması yok
        self.snapshot = self._read_snapshot()
        self.shared.use(self.snapshot)
        self._service_engines()
        self.retry_pnl()
        if self.base == STOPPING and not self.engines:
            self.base = OFF
            self._persist()
        candle = self.feed.last_closed_open_ms(now_ms)
        if self.engines or self.base == RUNNING:
            self._refresh_demand()
            if candle != self._scan_candle:
                self.feed.scan(now_ms, self._demand)
                self._scan_candle, self._tried, self._tried_candle = candle, {}, candle
                self._dead_checks(now_ms)
        opened = self._refill(now_ms) if self.state == RUNNING else []
        return {"state": self.state, "opened": opened, "slots": self.guard.slots().as_dict()}

    def _read_snapshot(self) -> ExchangeSnapshot:
        try:
            return self.snapshot_source.read()
        except Exception as exc:                                    # noqa: BLE001 — okunamayan görüntü = giriş yok, koruma motor turuyla sürer
            return ExchangeSnapshot(False, self.clock(), error=exc.__class__.__name__)

    # ---------- motor turu (koruma HER ZAMAN) ----------
    def _service_engines(self) -> None:
        for gid, engine in list(self.engines.items()):
            try:
                if engine.status == EMERGENCY:
                    engine.emergency_stop()
                else:
                    engine.tick()
            except ExchangeError as exc:
                self._event(f"{DISPLAY_NAME}: {engine.sym} tur hatası {exc.__class__.__name__}")
                continue
            self._settle(gid, engine)

    def _settle(self, gid: str, engine: Any) -> None:
        holds = {h["grid_id"]: h for h in self.store.holds(self.account_key)}
        hold = holds.get(gid)
        if hold is None:
            return
        net = engine.grid_net()
        status = engine.status
        if hold["state"] == "HELD":
            if abs(net) > EPS:
                self.guard.entry_filled(gid)
                spec = engine.spec.meta
                self.store.open_result(gid, self.account_key, engine.sym, engine.spec.side, target_usdt=float(spec["target_usdt"]),
                                       max_loss_usdt=float(spec["max_loss_usdt"]), est_cost_usdt=float(spec["est_cost_usdt"]))
                self.reservations.consume(gid)
                hold = {**hold, "state": "OPEN"}
            elif status in _ENDED or (status in (BLOCKED, INTERVENTION_REQUIRED) and not self._pending_orders(gid)):
                self.guard.entry_failed(gid, "NO_FILL" if status in _ENDED else "BLOCKED_NO_ORDER")
                self.reservations.release(gid)
                self.engines.pop(gid, None)
                return
        if engine.status in (INTERVENTION_REQUIRED, BLOCKED) and abs(net) > EPS:
            self._intervene(f"{engine.sym}: {engine.status}")
        if hold["state"] == "OPEN" and (engine.close_reason() != "OPEN" or status in _ENDED):
            self.guard.closing_started(gid)
            hold = {**hold, "state": "CLOSING"}
        if hold["state"] == "CLOSING" and status in _ENDED:
            flat, why = engine.verify_flat()
            if not flat:
                self._event(f"{engine.sym}: flat doğrulanamadı ({why}); slot tutuluyor")
                return
            reason = engine.close_reason()
            self.store.close_result(gid, reason if reason != "OPEN" else "MARKET_CLOSE")
            self.guard.release_when_flat(gid, True)                 # PnL'den ÖNCE ve PnL'den BAĞIMSIZ
            self.engines.pop(gid, None)
            self._verify_pnl(gid)

    def _pending_orders(self, gid: str) -> bool:
        return any(o["status"] not in TERMINAL for o in self.store.orders(gid))

    def _verify_pnl(self, gid: str) -> None:
        """Slot ZATEN bırakılmıştır. VERIFIED asla yeniden yazılmaz (idempotent); hata UNVERIFIED bırakır, 0/tahmin yazılmaz."""
        result = self.store.result(gid)
        if result is None or result["pnl_state"] == "VERIFIED" or int(result["verify_attempts"]) >= PNL_MAX_ATTEMPTS:
            return
        try:
            out = self.pnl.verify(result)
            if out.state == "VERIFIED":
                self.store.set_pnl(gid, state="VERIFIED", gross=out.gross, commission=out.commission, funding=out.funding, net=out.net)
            else:
                self.store.set_pnl(gid, state=out.state, error=out.error)        # UNVERIFIED/PENDING: sayısal alanlar NULL kalır
        except Exception as exc:                                    # noqa: BLE001 — doğrulama hatası UNVERIFIED bırakır; slot zaten bırakıldı
            self.store.set_pnl(gid, state="UNVERIFIED", error=f"VERIFY_ERROR:{exc.__class__.__name__}")
        attempts = int(self.store.result(gid)["verify_attempts"])
        if self.store.result(gid)["pnl_state"] != "VERIFIED" and attempts < PNL_MAX_ATTEMPTS:
            self._pnl_due[gid] = self.clock() + PNL_RETRY_BACKOFF_SECONDS[attempts - 1]
        else:
            self._pnl_due.pop(gid, None)

    def retry_pnl(self) -> list[str]:
        """Vadesi gelen UNVERIFIED sonuçları yeniden dener (kısa aralıklar, sınırlı deneme). Yeniden başlatma sonrası da çalışır."""
        now, done = self.clock(), []
        for row in self.store.unresolved_pnl(self.account_key):
            gid = row["grid_id"]
            if int(row["verify_attempts"]) >= PNL_MAX_ATTEMPTS:
                continue
            due = self._pnl_due.get(gid)
            if due is None:
                self._pnl_due[gid] = due = now                          # süreç yeniden başladı: hemen
            if due <= now:
                self._verify_pnl(gid)
                done.append(gid)
        return done

    def _intervene(self, why: str) -> None:
        if self.base != INTERVENTION:
            self.base, self.reason = INTERVENTION, why
            self._persist()
            self._event(f"{DISPLAY_NAME}: müdahale gerekli — {why}")

    # ---------- talep + ölü kontrol ----------
    def _refresh_demand(self) -> None:
        held = self.held_symbols()
        wanted = list(held)                                         # tutulan semboller evrenden düşse de talepte KALIR
        for symbol in (self.universe.symbols() if self.base == RUNNING else []):
            if symbol not in wanted:
                wanted.append(symbol)
        if wanted != self._demand:
            self._demand = wanted
            self._set_demand(list(wanted))

    def _dead_checks(self, now_ms: int) -> None:
        dead_score = self.settings.dead_score
        if dead_score is None:
            return
        for gid, engine in list(self.engines.items()):
            hold = next((h for h in self.store.holds(self.account_key) if h["grid_id"] == gid), None)
            if hold is None or hold["state"] != "OPEN" or engine.status != ENGINE_RUNNING:
                continue
            inp = self.feed.dead_check_input(engine.sym, now_ms)
            if inp is None or self._dead_candle.get(gid) == inp.candle_open_ms:
                continue
            mark = (self.snapshot.mark_prices.get(engine.sym) if self.snapshot is not None and self.snapshot.ok else None)
            price, source = (mark, "MARK") if mark and mark > 0 else (inp.price, inp.price_source)
            self._dead_candle[gid] = inp.candle_open_ms
            if not price or price <= 0:
                self.dead_info[gid] = {"result": "NO_PRICE", "source": None}
                continue
            result = engine.dead_check(inp.analysis, inp.candle_open_ms, float(price), dead_score=dead_score,
                                       safety_margin_usdt=self.settings.safety_margin_usdt, confirmations=self.settings.dead_confirmations)
            self.dead_info[gid] = {"result": result, "source": source, "candle_open_ms": inp.candle_open_ms}
            self._settle(gid, engine)

    # ---------- giriş / yeniden doldurma ----------
    def _occupied(self, symbol: str) -> tuple[str, ...]:
        out: list[str] = []
        if symbol in self.held_symbols():
            out.append("FARMER_HELD")
        s = self.snapshot
        if s is not None and s.ok:
            if abs(s.positions.get(symbol, 0.0)) > EPS:
                out.append("EXCHANGE_POSITION")
            if any(o.symbol == symbol for o in s.open_orders):
                out.append("EXCHANGE_OPEN_ORDER")
        out.extend(self.gates.foreign_occupied(symbol))
        return tuple(out)

    def _refill(self, now_ms: int) -> list[str]:
        """Aynı mumda slot boşalırsa tam tarama YOK: önbellek yeniden kullanılır, onay yeniden çalışabilir. Yalnız boş slot kadar, yalnız onaylı."""
        opened: list[str] = []
        if self.guard.slots().free <= 0:
            return opened
        gate = self.gates.entry_gates()
        for item in self.feed.cache.entry_candidates():
            if self.guard.slots().free <= 0:
                break
            if self._tried.get(item.symbol) not in (None, *_TRANSIENT_REJECTS):
                continue                                            # kalıcı ret: aynı mumda tekrar onay yok
            if item.candle_open_ms != self.feed.last_closed_open_ms(now_ms):
                continue
            ok, _why = self.feed.entry_data_ok(item.symbol, now_ms)
            if not ok:
                continue
            if self.store.attempt(self.account_key, item.symbol, item.interval, item.candle_open_ms) is not None:
                continue                                            # bu sembol+mum için gerçek deneme zaten yapıldı
            risk = RiskView(occupied=self._occupied(item.symbol), arm_blocked=gate.arm_blocked,
                            safety_blocked=tuple(gate.safety_blocked) + (("SNAPSHOT_UNAVAILABLE",) if self._snapshot_bad() else ()))
            gid = self._new_gid()
            decision = self.guard.attempt_entry(item.to_farmer_candidate(), self.settings.confirm, risk, grid_id=gid)
            if decision.status != CONFIRMED:
                self._tried[item.symbol] = decision.reason
                continue
            if self._open(gid, item, decision):
                opened.append(item.symbol)
        return opened

    def _open(self, gid: str, item: Any, decision: Any) -> bool:
        """Kilit+marjin alındı. Emir YALNIZ burada, rezervasyon ve motor hazırlığı başarılıysa gider."""
        if not self.reservations.reserve(item.symbol, gid):
            self.guard.entry_failed(gid, "RESERVATION_DENIED")
            self._tried[item.symbol] = SAFETY_GATE
            return False
        try:
            engine = self.factory.create(gid, item, self.settings.confirm, self.shared)
        except Exception as exc:                                    # noqa: BLE001 — hazırlık emirden önce: emir yok
            self.guard.entry_failed(gid, f"PREPARE_FAILED:{exc.__class__.__name__}")
            self.reservations.release(gid)
            self._event(f"{item.symbol}: giriş hazırlığı başarısız ({exc.__class__.__name__})")
            return False
        self.engines[gid] = engine
        self.meta[gid] = {"score": item.confidence, "candle_open_ms": item.candle_open_ms, "opened_ms": int(self.clock() * 1000),
                          "feasibility": decision.confirmation.feasibility.as_dict()}
        self.reservations.mark_submitted(gid, engine.store.orders(gid, "E")[0]["client_id"])
        try:
            engine.start()
        except Exception as exc:                                    # noqa: BLE001 — emir gitmiş olabilir: slot ve rezervasyon TUTULUR, motor çözer
            self._event(f"{item.symbol}: giriş sonucu belirsiz ({exc.__class__.__name__}); slot tutuluyor")
        self._settle(gid, engine)
        return True

    # ---------- denetim (kurtarma + müdahale onayı ortak) ----------
    def _audit(self, snapshot: ExchangeSnapshot) -> list[str]:
        """Belirsizlik listesi. Boş = ledger ↔ borsa tutarlı. Sahipsiz `kf-` emri OTOMATİK silinmez/benimsenmez."""
        problems: list[str] = []
        if not snapshot.ok:
            return ["borsa görüntüsü okunamadı"]
        prefixes = {e.prefix for e in self.engines.values()}
        strays = sorted({o.client_id for o in snapshot.open_orders if is_farmer_client_id(o.client_id)
                         and not any(o.client_id.startswith(p) for p in prefixes)})
        if strays:
            problems.append(f"sahipsiz Farmer emri (sahiplik kanıtlanamadı): {strays}")
        try:
            for gid in self.reservations.orphans():
                if gid not in self.engines:
                    problems.append(f"yetim SUBMITTED rezervasyon: {gid}")
        except Exception as exc:                                    # noqa: BLE001 — rezervasyon okunamadı → fail-closed
            problems.append(f"rezervasyon okunamadı: {exc.__class__.__name__}")
        for engine in self.engines.values():
            if self._engine_blocking(engine):
                problems.append(f"{engine.sym}: motor {engine.status}")
        return problems

    # ---------- acil kapanış (yalnız Farmer) ----------
    def emergency_close(self) -> dict[str, Any]:
        """CLOSE REQUESTED ≠ FLAT VERIFIED. Yalnız ledger'da kimliği kanıtlı (`kf-<grid>` öneki) pozisyonlar/emirler. Tüm okumalar DOĞRUDAN
        (paylaşımlı görüntü kapalı). Flat doğrulanamazsa ya da sahiplik belirsizse INTERVENTION_REQUIRED; çocuk sembole/emre dokunulmaz."""
        self.base = STOPPING
        self.shared.use(None)
        report: dict[str, Any] = {"closed": [], "failed": [], "unowned": []}
        for gid, engine in list(self.engines.items()):
            engine.emergency_stop()
            flat, why = engine.verify_flat()
            if not flat:
                report["failed"].append({"grid_id": gid, "symbol": engine.sym, "why": why})
                continue
            if not self._cleanup_owned(engine):
                report["failed"].append({"grid_id": gid, "symbol": engine.sym, "why": "koruma emri temizliği doğrulanamadı"})
                continue
            hold = next((h for h in self.store.holds(self.account_key) if h["grid_id"] == gid), None)
            if hold is not None and hold["state"] != "HELD":
                self.guard.closing_started(gid)
                self.store.close_result(gid, "EMERGENCY")
                self.guard.release_when_flat(gid, True)             # flat DOĞRULANDI → slot; PnL ayrı
                self._verify_pnl(gid)
            elif hold is not None:
                self.guard.entry_failed(gid, "EMERGENCY_NO_FILL")
                self.reservations.release(gid)
            self.engines.pop(gid, None)
            report["closed"].append(gid)
        try:                                                        # sahiplik: ledger'da karşılığı olmayan `kf-` emri varsa DOKUNMA
            known = {e.prefix for e in self.engines.values()} | {farmer_grid_prefix(g["grid_id"]) for g in self.store.holds(self.account_key, active_only=False)}
            for symbol in {e.sym for e in self.engines.values()} | {self.guard_symbol(h) for h in report["closed"]}:
                if symbol:
                    for order in self.shared.real.open_orders(symbol):
                        if is_farmer_client_id(order.client_id) and not any(order.client_id.startswith(p) for p in known):
                            report["unowned"].append(order.client_id)
        except ExchangeError:
            report["unowned"].append("OPEN_ORDERS_UNREADABLE")
        if report["failed"] or report["unowned"]:
            self._intervene("EMERGENCY_CLOSE: " + ("flat/temizlik doğrulanamadı" if report["failed"] else "sahiplik belirsiz"))
        else:
            self.base = OFF
            self._persist()
        return report

    def guard_symbol(self, gid: str) -> str:
        row = self.store.grid(gid)
        return str(row["symbol"]) if row is not None else ""

    def _cleanup_owned(self, engine: Any) -> bool:
        """YALNIZ bu motorun (`kf-<grid>-`) kalan emirleri. Okunamazsa / iptal edilemezse False (flat olsa bile temizlik kanıtsız bırakılmaz)."""
        try:
            leftovers = [o for o in self.shared.real.open_orders(engine.sym) if o.client_id.startswith(engine.prefix)]
            for order in leftovers:
                self.shared.cancel_order(engine.sym, order.client_id)
            return not [o for o in self.shared.real.open_orders(engine.sym) if o.client_id.startswith(engine.prefix)]
        except ExchangeError as exc:
            self._event(f"{engine.sym}: Farmer emri temizliği tamamlanamadı ({exc.__class__.__name__})")
            return False

    # ---------- kurtarma ----------
    def _entry_client_ids(self, gid: str) -> list[str]:
        return [farmer_client_order_id(gid, side, 1, "E", 0) for side in (LONG, SHORT)]

    def _resolve_pending_entry(self, gid: str, engine: Any, report: dict[str, Any]) -> bool:
        """Giriş emri borsada DOLMAMIŞ/gönderilmemiş olabilir (çökme anı). Yeniden başlatmada ESKİ giriş ASLA yeniden gönderilmez.
        Kimlikle sorulur: varsa benimsenir; yoksa YALNIZ borsada pozisyon yok + bu grid'e ait emir yok ise 'hiç gitmedi' kanıtlanmış sayılır.
        Okunamaz / belirsizse False → INTERVENTION. True → çözüldü (motor devam edebilir ya da kaldırıldı)."""
        entries = [r for r in self.store.orders(gid, "E")]
        if not entries or all(r["status"] not in ("PENDING_SEND", "UNKNOWN") for r in entries):
            return True
        try:
            for row in entries:
                if row["status"] in ("PENDING_SEND", "UNKNOWN"):
                    info = engine.ex.get_order(engine.sym, row["client_id"])
                    if info is not None:
                        engine._apply(row["client_id"], info)       # borsada VAR: benimse (yeniden gönderme yok)
            if any(r["status"] in ("PENDING_SEND", "UNKNOWN") for r in self.store.orders(gid, "E")):
                baseline = float(self.store.grid(gid)["baseline"])
                if abs(engine.ex.position(engine.sym) - baseline) > 1e-6 or [o for o in engine.ex.open_orders(engine.sym) if o.client_id.startswith(engine.prefix)]:
                    return False                                    # emir bulunamadı ama pozisyon/emir var: belirsiz
                for row in self.store.orders(gid, "E"):
                    if row["status"] in ("PENDING_SEND", "UNKNOWN"):
                        self.store.set_order(row["client_id"], status="CANCELED")
                engine._set(STOPPED, "KAIS FARMER: giriş emri borsada bulunamadı (yeniden başlatma); gönderilmedi")
        except ExchangeError:
            return False
        return True

    def _finalize_external_flat(self, gid: str, engine: Any) -> bool:
        """Ledger'da pozisyon var ama borsa DÜZ (ve motor tutarsızlık yüzünden durdu): düz borsadan doğrulanır, kalan Farmer emirleri
        (ledger'da kimliği kanıtlı) temizlenir, motor kapatılır; slot flat'ten sonra bırakılır, PnL C8 hattında (kimlikle kanıtlanamazsa UNVERIFIED)."""
        try:
            baseline = float(self.store.grid(gid)["baseline"])
            if abs(engine.ex.position(engine.sym) - baseline) > 1e-6:
                return False
            engine._cancel_all(roles=("E", "T", "S"))
            flat, _why = engine.verify_flat()
            if not flat:
                return False
        except ExchangeError:
            return False
        engine._set(STOPPED, "KAIS FARMER: pozisyon borsada düz doğrulandı (kapanış defterde yok); kapanış nedeni kanıtlanamadı")
        return True

    def recover(self) -> dict[str, Any]:
        """Yeniden başlatma. Sıra: rezervasyon mutabakatı → ledger → borsa → uzlaştırma. Hiçbir giriş yeniden gönderilmez.
        Belirsiz her durum INTERVENTION_REQUIRED (otomatik RUNNING'e dönülmez; kayıtlı INTERVENTION korunur)."""
        report: dict[str, Any] = {"rebuilt": [], "released": [], "problems": []}
        reconcile = getattr(self.reservations, "reconcile", None)
        if callable(reconcile):
            try:
                reconcile()
            except Exception as exc:                                  # noqa: BLE001 — mutabakat yapılamadı → fail-closed
                report["problems"].append(f"rezervasyon mutabakatı yapılamadı: {exc.__class__.__name__}")
        self.snapshot = self._read_snapshot()
        self.shared.use(None)                                       # motor/uzlaştırma okumaları DOĞRUDAN (bayat görüntü yok)
        saved = self.store.bot(self.account_key)
        for hold in self.store.holds(self.account_key):
            gid = hold["grid_id"]
            row = self.store.grid(gid)
            if row is None:
                if self._entry_exists_on_exchange(gid, self.snapshot):
                    report["problems"].append(f"ledger'sız giriş emri borsada: {gid}")
                    continue
                self.store.set_attempt_outcome(gid, "NO_ORDER_SENT")
                self.store.release_hold(gid)
                self.reservations.release(gid)
                report["released"].append(gid)
                continue
            engine = self.factory.rebuild(row, self.shared)
            self.engines[gid] = engine
            if not self._resolve_pending_entry(gid, engine, report):
                report["problems"].append(f"{engine.sym}: giriş emri durumu kanıtlanamadı")
                report["rebuilt"].append({"grid_id": gid, "symbol": engine.sym, "ok": False, "why": "entry unresolved"})
                continue
            ok, why = (True, "giriş gönderilmedi") if engine.status in (STOPPED, COMPLETED, EMERGENCY_CLOSED) and abs(engine.grid_net()) <= EPS else engine.recover()
            if not ok and engine.status in (BLOCKED, INTERVENTION_REQUIRED) and abs(engine.grid_net()) > EPS and self._finalize_external_flat(gid, engine):
                ok, why = True, "borsa düz; defter kapatıldı"
            report["rebuilt"].append({"grid_id": gid, "symbol": engine.sym, "ok": ok, "why": why})
            if not ok:
                report["problems"].append(f"{engine.sym}: {why}")
            elif abs(engine.grid_net()) > EPS and engine.status not in _ENDED:
                self.reservations.consume(gid)                       # dolum kanıtlı: ESKİ süreçten kalan SUBMITTED satırı kapat (idempotent)
            self._settle(gid, engine)
        if report["rebuilt"]:
            self.snapshot = self._read_snapshot()                    # kurtarma emir iptal etmiş olabilir: denetim GÜNCEL borsa durumuna karşı
        report["problems"].extend(self._audit(self.snapshot))
        self.recovered = not report["problems"]
        if saved and saved["state"] == INTERVENTION:                 # önceki müdahale durumu kendiliğinden kalkmaz
            self.base, self.reason = INTERVENTION, saved.get("pause_reason") or "RECOVERY: önceki müdahale durumu"
        elif report["problems"]:
            self._intervene("RECOVERY: " + "; ".join(report["problems"]))
        elif saved and saved["state"] in (RUNNING, ENTRY_PAUSED_ARM, ENTRY_PAUSED_SAFETY) and self.base == OFF:
            self.base = RUNNING
        elif saved and saved["state"] == STOPPING and self.engines:
            self.base = STOPPING
        return report

    def _entry_exists_on_exchange(self, gid: str, snapshot: ExchangeSnapshot) -> bool:
        prefix = farmer_grid_prefix(gid)
        if not snapshot.ok or any(o.client_id.startswith(prefix) for o in snapshot.open_orders):
            return True                                             # okunamadı ya da emir var: 'yok' denemez
        return False

    def scanner_view(self, now_ms: int | None = None) -> list[dict[str, Any]]:
        """Arayüz için aday listesi (YALNIZ OKUMA; önbellek ve ret kayıtları aynen yansıtılır, yeni hesap/uydurma durum yok).
        status: QUALIFIED (pozisyon açık/korumalı) | WAITING_CONFIRMATION | REJECTED | COOLING_DOWN (bu mumda deneme zaten yapıldı)."""
        now_ms = int(self.clock() * 1000) if now_ms is None else int(now_ms)
        held = {engine.sym for engine in self.engines.values() if engine.status not in _ENDED}
        rows: list[dict[str, Any]] = []
        for item in self.feed.cache.entry_candidates():
            tried = self._tried.get(item.symbol)
            attempted = self.store.attempt(self.account_key, item.symbol, item.interval, item.candle_open_ms) is not None
            if item.symbol in held:
                status, reason = "QUALIFIED", None
            elif item.confidence is None or item.confidence < self.settings.confirm.min_score:
                status, reason = "REJECTED", "SCORE_TOO_LOW"
            elif tried is not None and tried not in _TRANSIENT_REJECTS:
                status, reason = "REJECTED", tried
            elif attempted:
                status, reason = "COOLING_DOWN", "ENTRY_LOCKED"
            else:
                status, reason = "WAITING_CONFIRMATION", tried
            rows.append({"symbol": item.symbol, "direction": item.direction, "score": item.confidence, "status": status, "reason": reason})
        return rows

    # ---------- arayüz görüntüsü ----------
    def ui_snapshot(self, now_ms: int | None = None) -> dict[str, Any]:
        now_ms = int(self.clock() * 1000) if now_ms is None else int(now_ms)
        holds = {h["grid_id"]: h for h in self.store.holds(self.account_key)}
        rows: list[dict[str, Any]] = []
        for gid, engine in self.engines.items():
            spec, meta = engine.spec.meta, self.meta.get(gid, {})
            feas = meta.get("feasibility") or {}
            result = self.store.result(gid) or {}
            est_cost, target = float(spec.get("est_cost_usdt") or 0.0), float(spec.get("target_usdt") or 0.0)
            mark = self.snapshot.mark_prices.get(engine.sym) if self.snapshot is not None and self.snapshot.ok else None
            live = engine.snapshot() if engine.status not in _ENDED else {}
            rows.append({
                "grid_id": gid, "symbol": engine.sym, "side": engine.spec.side, "engine_status": engine.status,
                "slot_state": (holds.get(gid) or {}).get("state"), "age_s": round((now_ms - int(meta.get("opened_ms", now_ms))) / 1000, 1),
                "score": meta.get("score"), "target_usdt": spec.get("target_usdt"), "max_loss_usdt": spec.get("max_loss_usdt"),
                "dead_score": self.settings.dead_score, "dead_consecutive": self.store.dead_count(gid, 0, False),
                "dead_check": self.dead_info.get(gid), "cooldown_s": round(max(0, (now_ms // CANDLE_MS + 1) * CANDLE_MS - now_ms) / 1000, 1),
                "margin_usdt": spec.get("margin_usdt"), "notional_usdt": spec.get("notional"), "est_cost_usdt": est_cost,
                "target_pct": spec.get("target_pct"), "stop_pct": spec.get("stop_pct"), "target_over_atr": feas.get("target_over_atr"),
                "cost_over_target": round(est_cost / target, 6) if target > 0 else None, "close_reason": engine.close_reason(),
                "pnl_state": result.get("pnl_state", "PENDING"), "verified_net_pnl": result.get("verified_net_pnl"),
                # C10 (EKLEMELİ, yalnız okuma): ekran için gerçek giriş/hedef/stop fiyatı ve tahmini anlık net. Veri yoksa None (uydurma yok).
                "entry_price": live.get("avg_entry"), "tp_price": live.get("tp_price"), "stop_price": live.get("stop_price"),
                "mark_price": mark if mark and mark > 0 else None,
                "unrealized_net_usdt": (engine.unrealized_net(float(mark)) if mark and mark > 0 and engine.status not in _ENDED else None)})
        return {"name": DISPLAY_NAME, "state": self.state, "reason": self.reason, "slots": self.guard.slots().as_dict(),
                "effective_capacity": self.guard.capacity, "positions": rows, "demand": list(self._demand),
                "snapshot_ok": not self._snapshot_bad(), "shared_reads": {"served": self.shared.served, "passthrough": self.shared.passthrough},
                "recent_results": self.store.results(self.account_key)[-10:], "events": list(self.events[-10:])}
