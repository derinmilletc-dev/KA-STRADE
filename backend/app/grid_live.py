"""Manuel GRID — canlı çalıştırma katmanı (ilk canlı test: sıkı sınırlar, varsayılan KAPALI).

- Açılması için sunucuda KAISTRADE_GRID_LIVE=1 olmalı (kullanıcı kendisi açar). Kapalıyken başlatma reddedilir.
- Sert sınırlar kodda sabittir: en fazla 2 kademe, toplam marjin en fazla 10 USDT, kaldıraç en fazla 10x.
- Plan, ekrandaki önizlemeyle AYNI hesaptan geçer (gridMath.ts): kademe/miktar borsa adımlarına yuvarlanır, en kötü zarar
  tek işlem sınırını ve günlük zarar hakkını aşarsa grid HİÇ başlamaz.
- Emirler mevcut canlı bağlantıdan (BinanceLiveClient) gider; bu dosya anahtar istemez/okumaz.
- Her dolan miktar için borsada koruyucu stop vardır (motor: önce stop, sonra hedef).
- ACİL DURDUR: girişleri iptal eder, piyasadan kapatır, kapandığını borsadan doğrular; doğrulayamazsa stoplara DOKUNMAZ.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import HTTPException

from .grid_engine.engine import (BLOCKED, COMPLETED, EMERGENCY_CLOSED, INTERVENTION_REQUIRED, PAUSED, RUNNING, STOPPED, GridEngine,
                                 GridLevelSpec, GridSpec)
from .grid_engine.live_adapter import BinanceLiveGridAdapter
from .grid_engine.ports import EPS, LONG, SHORT
from .grid_engine.store import TERMINAL, GridStore
from .web_security import env_flag

logger = logging.getLogger(__name__)

ENV_FLAG = "KAISTRADE_GRID_LIVE"
LIVE_MAX_LEVELS = 2
LIVE_MAX_MARGIN_USDT = 10.0
LIVE_MAX_LEVERAGE = 10
CONFIRM_START = "CANLI GRID BAŞLAT"
CONFIRM_EMERGENCY = "GRID ACİL DURDUR"
MAKER_FEE, TAKER_FEE = 0.0002, 0.0005          # gridMath.ts ile aynı
TICK_SECONDS = 5.0
MAX_LOOP_ERRORS = 6
FINAL_STATUSES = {STOPPED, COMPLETED, EMERGENCY_CLOSED}
NEEDS_USER = {BLOCKED, INTERVENTION_REQUIRED}


class LiveGridError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def live_enabled() -> bool:
    return env_flag(ENV_FLAG)


def limits_payload() -> dict[str, Any]:
    return {"enabled": live_enabled(), "max_levels": LIVE_MAX_LEVELS, "max_margin_usdt": LIVE_MAX_MARGIN_USDT, "max_leverage": LIVE_MAX_LEVERAGE}


# ---------------------------------------------------------------- saf hesap (ağ yok)
def _dec(value: Any) -> Decimal:
    return Decimal(repr(float(value))) if not isinstance(value, Decimal) else value


def _snap(value: Any, tick: Decimal, mode: str = ROUND_HALF_UP) -> Decimal:
    return (_dec(value) / tick).to_integral_value(rounding=mode) * tick


def prepare_live_grid(*, direction: str, lower: float, upper: float, count: int, margin_usdt: float, leverage: int, stop: float,
                      price: float, rules: dict[str, Any], policy: dict[str, Any], realized_pnl_today: float,
                      existing_exposure: float, open_positions: int) -> dict[str, Any]:
    """Önizlemeyle aynı hesap + canlı sert sınırlar. Herhangi bir kontrol başarısızsa LiveGridError (grid başlamaz)."""
    direction = str(direction).upper()
    if direction not in (LONG, SHORT):
        raise LiveGridError("Yön LONG veya SHORT olmalı.")
    values = (lower, upper, margin_usdt, leverage, stop, price)
    if not all(isinstance(v, (int, float)) and v == v and v > 0 and v != float("inf") for v in values):
        raise LiveGridError("Fiyat, marjin ve kaldıraç sıfırdan büyük sayı olmalı.")
    count = int(count)
    problems: list[str] = []
    if not 2 <= count <= LIVE_MAX_LEVELS:
        raise LiveGridError(f"İlk canlı testte kademe sayısı 2 ile {LIVE_MAX_LEVELS} arasında olmalı.")
    if margin_usdt > LIVE_MAX_MARGIN_USDT + 1e-9:
        raise LiveGridError(f"İlk canlı testte toplam marjin en fazla {LIVE_MAX_MARGIN_USDT:g} USDT olabilir.")
    max_lev = min(LIVE_MAX_LEVERAGE, int(float(policy.get("max_leverage", LIVE_MAX_LEVERAGE))))
    if leverage > max_lev:
        raise LiveGridError(f"Kaldıraç en fazla {max_lev}x olabilir.")
    if lower >= upper:
        raise LiveGridError("Alt sınır, üst sınırdan küçük olmalı.")
    if direction == LONG and stop >= lower:
        raise LiveGridError("LONG grid için stop, alt sınırın altında olmalı.")
    if direction == SHORT and stop <= upper:
        raise LiveGridError("SHORT grid için stop, üst sınırın üstünde olmalı.")
    try:
        tick, step = _dec(rules["tick"]), _dec(rules["step"])
        min_qty, min_notional = _dec(rules.get("min_qty", 0)), _dec(rules.get("min_notional", 0))
        max_qty = _dec(rules["max_qty"]) if rules.get("max_qty") is not None else None
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise LiveGridError("Borsa fiyat/miktar kuralları okunamadı; grid başlatılmadı.", 503) from None
    if not (tick > 0 and step > 0):
        raise LiveGridError("Borsa fiyat/miktar kuralları geçersiz; grid başlatılmadı.", 503)

    spacing = (upper - lower) / (count - 1)
    per_margin = margin_usdt / count
    rows: list[dict[str, Any]] = []
    for i in range(count):
        entry_raw = upper - spacing * i if direction == LONG else lower + spacing * i
        target_raw = entry_raw + spacing if direction == LONG else entry_raw - spacing
        entry, target = _snap(entry_raw, tick), _snap(target_raw, tick)
        qty = _snap(per_margin * leverage / float(entry), step, ROUND_FLOOR)
        rows.append({"level": i + 1, "entry": float(entry), "target": float(target), "qty": float(qty)})
    stop_price = float(_snap(stop, tick, ROUND_FLOOR if direction == LONG else ROUND_CEILING))

    if len({r["entry"] for r in rows}) != len(rows):
        problems.append("Yuvarlamadan sonra iki kademe aynı fiyata düşüyor.")
    for r in rows:
        notional = r["qty"] * r["entry"]
        if r["qty"] <= 0 or Decimal(repr(r["qty"])) < min_qty or notional + 1e-9 < max(float(min_notional), 5.0):
            problems.append(f"Kademe {r['level']} borsanın en düşük emir değerinin altında.")
        if max_qty is not None and Decimal(repr(r["qty"])) > max_qty:
            problems.append(f"Kademe {r['level']} borsanın en büyük miktarını aşıyor.")
        better = r["target"] > r["entry"] if direction == LONG else r["target"] < r["entry"]
        if not better:
            problems.append(f"Kademe {r['level']} hedefi girişten ileride değil.")
        wrong_side = r["entry"] >= price if direction == LONG else r["entry"] <= price
        if wrong_side:
            problems.append(f"Kademe {r['level']} güncel fiyatın yanlış tarafında; limit emir hemen dolar.")
        beyond = r["entry"] <= stop_price if direction == LONG else r["entry"] >= stop_price
        if beyond:
            problems.append(f"Kademe {r['level']} stopun ötesinde.")

    total_notional = sum(r["qty"] * r["entry"] for r in rows)
    total_margin = total_notional / leverage
    raw_loss = sum(r["qty"] * abs(r["entry"] - stop_price) for r in rows)
    worst_loss = raw_loss + total_notional * MAKER_FEE + sum(r["qty"] * stop_price for r in rows) * TAKER_FEE
    far_entry = min(r["entry"] for r in rows) if direction == LONG else max(r["entry"] for r in rows)
    stop_distance_pct = abs(far_entry - stop_price) / far_entry * 100

    if total_margin > LIVE_MAX_MARGIN_USDT + 1e-9 or total_margin > margin_usdt + 1e-6:
        problems.append(f"Gereken marjin {total_margin:.2f} USDT, sınırı aşıyor.")
    loss_limit = float(policy.get("max_loss_per_trade", 3))
    if worst_loss > loss_limit + 1e-9:
        problems.append(f"En kötü zarar {worst_loss:.2f} USDT, tek işlem sınırı {loss_limit:g} USDT.")
    daily_left = float(policy.get("daily_loss_limit", 10)) + min(0.0, float(realized_pnl_today))
    if daily_left <= 0 or worst_loss > daily_left + 1e-9:
        problems.append(f"En kötü zarar {worst_loss:.2f} USDT, bugün kalan zarar hakkı {max(daily_left, 0):.2f} USDT.")
    exposure_limit = float(policy.get("max_total_exposure_usdt", 350))
    if existing_exposure + total_notional > exposure_limit + 1e-9:
        problems.append(f"Toplam maruziyet {existing_exposure + total_notional:.2f}, sınır {exposure_limit:g} USDT.")
    if open_positions >= int(policy.get("max_positions", 5)):
        problems.append("Açık pozisyon sınırı dolu.")
    max_stop_pct = float(policy.get("max_stop_distance_pct", 5))
    if stop_distance_pct > max_stop_pct + 1e-9:
        problems.append(f"Stop mesafesi %{stop_distance_pct:.2f}, sınır %{max_stop_pct:g}.")
    if stop_distance_pct * leverage > 70:
        problems.append("Stop, tahmini likidasyona çok yakın; kaldıracı düşür ya da stopu yakınlaştır.")
    if problems:
        raise LiveGridError(" ".join(dict.fromkeys(problems)))
    return {"direction": direction, "stop_price": stop_price, "levels": rows, "total_margin_usdt": round(total_margin, 6),
            "total_notional_usdt": round(total_notional, 6), "worst_loss_usdt": round(worst_loss, 6),
            "stop_distance_pct": round(stop_distance_pct, 4), "leverage": int(leverage)}


def grid_spec_from_plan(grid_id: str, symbol: str, plan: dict[str, Any]) -> GridSpec:
    return GridSpec(grid_id, symbol, plan["direction"], [GridLevelSpec(r["entry"], r["target"], r["qty"]) for r in plan["levels"]], plan["stop_price"])


def grid_spec_from_row(row: Any) -> GridSpec:
    spec = json.loads(row["spec"])
    return GridSpec(row["grid_id"], row["symbol"], row["side"], [GridLevelSpec(**lv) for lv in spec["levels"]], float(spec["stop_price"]))


# ---------------------------------------------------------------- çalıştırıcı
class LiveGridRunner:
    """Tek aktif canlı grid. Motor ayrı iş parçacığında koşar; borsa çağrıları olay döngüsüne köprülenir."""

    def __init__(self, store: GridStore) -> None:
        self.store = store
        self._engine: GridEngine | None = None
        self._task: asyncio.Task | None = None
        self._lock: asyncio.Lock | None = None
        self._adapter: BinanceLiveGridAdapter | None = None
        self._paused_for_block = False
        self.loop_errors = 0

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    # ---- defter okuma
    def current_row(self) -> Any:
        return self.store.db.execute("SELECT * FROM grid ORDER BY rowid DESC LIMIT 1").fetchone()

    def occupying_row(self) -> Any:
        row = self.current_row()
        return row if row is not None and row["status"] not in FINAL_STATUSES else None

    def loop_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status_payload(self) -> dict[str, Any]:
        row = self.current_row()
        out: dict[str, Any] = {**limits_payload(), "loop_running": self.loop_running(), "grid": None}
        if row is None:
            return out
        gid = row["grid_id"]
        orders = self.store.orders(gid)
        net = 0.0
        for o in orders:
            net += o["filled"] if o["role"] == "E" else -o["filled"] if o["role"] in ("T", "S", "C") else 0.0
        out["grid"] = {
            "grid_id": gid, "symbol": row["symbol"], "side": row["side"], "status": row["status"], "reason": row["halt_reason"],
            "position_qty": round(net, 8), "spec": json.loads(row["spec"]), "needs_user": row["status"] in NEEDS_USER,
            "orders": [{"client_id": o["client_id"], "role": o["role"], "level": o["level"], "side": o["side"], "type": o["type"],
                        "price": o["price"], "stop_price": o["stop_price"], "qty": o["qty"], "filled": o["filled"], "status": o["status"]}
                       for o in orders],
            "alerts": [{"level": level, "text": text} for level, text in self.store.alerts(gid)[-12:]],
        }
        return out

    # ---- motor kurma
    def build_engine(self, row: Any, adapter: BinanceLiveGridAdapter, state: dict[str, Any]) -> GridEngine:
        return self._new_engine(grid_spec_from_row(row), adapter, state)

    @staticmethod
    def blocked_check(state: dict[str, Any]) -> bool:
        """Canlı yürütme kilitli mi (acil durum, belirsiz emir, hız sınırı)? Testlerde değiştirilebilir."""
        from . import v25_execution as v25
        return bool(v25.live_execution_blocked(state))

    def _new_engine(self, spec: GridSpec, adapter: BinanceLiveGridAdapter, state: dict[str, Any]) -> GridEngine:
        def risk_gate() -> tuple[bool, str]:
            if self.blocked_check(state):
                return False, "canlı yürütme kilitli"
            if state.get("real_trading_locked") is not False:
                return False, "gerçek işlem kilidi kapalı"
            return True, ""

        return GridEngine(adapter, self.store, spec, risk_gate=risk_gate, sleep=time.sleep)

    def _record(self, state: dict[str, Any], kind: str, message: str, **extra: Any) -> None:
        from . import v25_execution as v25
        try:
            v25.add_event(state, kind, message, **extra)
            v25.persist_state(state)
        except Exception:  # günlük yazılamasa da grid korunmalı
            logger.warning("LIVE_GRID_EVENT_FAILED kind=%s", kind)

    # ---- yaşam döngüsü
    async def start(self, app: Any, state: dict[str, Any], adapter: BinanceLiveGridAdapter, plan: dict[str, Any], symbol: str) -> dict[str, Any]:
        async with self.lock:
            if self.occupying_row() is not None:
                raise LiveGridError("Zaten bir canlı grid var (çalışıyor ya da müdahale bekliyor). Önce onu durdur.", 409)
            grid_id = "g" + uuid.uuid4().hex[:12]
            engine = self._new_engine(grid_spec_from_plan(grid_id, symbol, plan), adapter, state)
            self._engine, self._adapter, self._paused_for_block, self.loop_errors = engine, adapter, False, 0
            self._record(state, "LIVE_GRID_START", f"{symbol} canlı grid başlatılıyor ({plan['direction']}, {len(plan['levels'])} kademe, "
                         f"marjin {plan['total_margin_usdt']} USDT).", symbol=symbol, grid_id=grid_id)
            status = await asyncio.to_thread(engine.start)
            if status == BLOCKED:
                self._record(state, "LIVE_GRID_BLOCKED", f"{symbol} grid başlamadı: {self.store.grid(grid_id)['halt_reason']}", symbol=symbol, grid_id=grid_id)
            else:
                self._ensure_loop(app, state)
            return self.status_payload()

    def _ensure_loop(self, app: Any, state: dict[str, Any]) -> None:
        if not self.loop_running():
            self._task = asyncio.get_running_loop().create_task(self._run(app, state), name="live-grid-loop")

    async def resume(self, app: Any, state: dict[str, Any], adapter: BinanceLiveGridAdapter) -> dict[str, Any]:
        async with self.lock:
            row = self.occupying_row()
            if row is None:
                raise LiveGridError("Devam ettirilecek canlı grid yok.", 404)
            if row["status"] in NEEDS_USER:
                raise LiveGridError("Grid müdahale bekliyor; önce ACİL DURDUR ile kapat ya da borsayı elle kontrol et.", 409)
            self._engine, self._adapter, self._paused_for_block, self.loop_errors = self.build_engine(row, adapter, state), adapter, False, 0
            self._ensure_loop(app, state)
            return self.status_payload()

    async def _run(self, app: Any, state: dict[str, Any]) -> None:
        last_status = ""
        while True:
            await asyncio.sleep(TICK_SECONDS)
            engine = self._engine
            if engine is None:
                return
            try:
                async with self.lock:
                    if engine is not self._engine:
                        return
                    if not self._paused_for_block and self.blocked_check(state) and engine.status == RUNNING:
                        self._paused_for_block = True       # kilit/acil durum: yeni giriş yok; pozisyon korunur
                        await asyncio.to_thread(engine.pause)
                        self._record(state, "LIVE_GRID_PAUSED", f"{engine.sym} canlı yürütme kilitlendi; grid duraklatıldı, korumalar sürüyor.", grid_id=engine.gid)
                    status = await asyncio.to_thread(self._tick_once, engine)
                self.loop_errors = 0
            except Exception as exc:  # ağ/ayrıştırma: sonraki tur tekrar dener; sürekli ise duraklat
                self.loop_errors += 1
                logger.warning("LIVE_GRID_TICK_FAILED type=%s count=%s", type(exc).__name__, self.loop_errors)
                if self.loop_errors >= MAX_LOOP_ERRORS:
                    try:
                        async with self.lock:
                            await asyncio.to_thread(engine.pause)
                    except Exception:
                        pass
                    self._record(state, "LIVE_GRID_LOOP_FAILED", f"{engine.sym} grid döngüsü art arda hata verdi; yeni giriş durduruldu. Borsayı kontrol edin.", grid_id=engine.gid)
                    return
                continue
            if status != last_status:
                last_status = status
                self._record(state, "LIVE_GRID_STATUS", f"{engine.sym} grid durumu: {status}", grid_id=engine.gid, status=status)
            if status in FINAL_STATUSES or status in NEEDS_USER:
                return

    def _tick_once(self, engine: GridEngine) -> str:
        engine.tick()
        status = engine.status
        if status == BLOCKED:               # tutarsızlık: bekleyen girişler iptal edilir, açık pozisyon stop/hedefle kalır
            engine.pause()
            if engine.status == PAUSED:     # kullanıcı görsün: durum BLOCKED kalır, döngü biter, müdahale beklenir
                self.store.set_grid(engine.gid, status=BLOCKED)
            status = engine.status
        if status == PAUSED and self._flat_and_entries_done(engine):
            self.store.set_grid(engine.gid, status=STOPPED, halt_reason="Duraklatıldı; açık pozisyon ve bekleyen giriş yok")
            status = STOPPED
        return status

    @staticmethod
    def _flat_and_entries_done(engine: GridEngine) -> bool:
        entries = engine.store.orders(engine.gid, "E")
        return abs(engine.grid_net()) <= EPS and bool(entries) and all(e["status"] in TERMINAL for e in entries)

    async def pause(self, state: dict[str, Any], adapter: BinanceLiveGridAdapter) -> dict[str, Any]:
        async with self.lock:
            row = self.current_row()
            if row is None or row["status"] in FINAL_STATUSES:
                raise LiveGridError("Durdurulacak canlı grid yok.", 404)
            engine = self._engine if self._engine is not None and self._engine.gid == row["grid_id"] else self.build_engine(row, adapter, state)
            self._engine = engine
            status = await asyncio.to_thread(engine.pause)
            if status == PAUSED and self._flat_and_entries_done(engine):
                self.store.set_grid(engine.gid, status=STOPPED, halt_reason="Kullanıcı durdurdu; açık pozisyon yok")
            self._record(state, "LIVE_GRID_PAUSED", f"{engine.sym} grid durduruldu (bekleyen girişler iptal; açık pozisyon stop/hedefle kalır).", grid_id=engine.gid)
            return self.status_payload()

    async def emergency(self, app: Any, state: dict[str, Any], adapter: BinanceLiveGridAdapter) -> dict[str, Any]:
        async with self.lock:
            row = self.current_row()
            if row is None or row["status"] in FINAL_STATUSES:
                raise LiveGridError("Acil durdurulacak canlı grid yok.", 404)
            engine = self._engine if self._engine is not None and self._engine.gid == row["grid_id"] else self.build_engine(row, adapter, state)
            self._engine = engine
            status = await asyncio.to_thread(engine.emergency_stop)
            self._record(state, "LIVE_GRID_EMERGENCY", f"{engine.sym} grid ACİL DURDUR sonucu: {status}", grid_id=engine.gid, status=status)
            return self.status_payload()


def runner_for(app: Any) -> LiveGridRunner:
    existing = getattr(app.state, "live_grid_runner", None)
    if existing is not None:
        return existing
    from .local_storage import DATA_DIR
    runner = LiveGridRunner(GridStore(str(DATA_DIR / "grid_live.sqlite3"), check_same_thread=False))
    app.state.live_grid_runner = runner
    return runner


# ---------------------------------------------------------------- uç noktaların iş mantığı (v25_execution.py çağırır)
def build_adapter(app: Any, request: Any) -> BinanceLiveGridAdapter:
    from . import v25_execution as v25
    api_key, secret_key = v25.live_credentials_status(request)[:2]
    client = v25.BinanceLiveClient(app.state.http, api_key, secret_key, diagnostic_state=getattr(app.state, "v25_execution", None),
                                   application=app, background_session=False)
    return BinanceLiveGridAdapter(client, asyncio.get_running_loop())


def require_enabled() -> None:
    if not live_enabled():
        raise HTTPException(403, f"Canlı grid kapalı. Sunucuda {ENV_FLAG}=1 ile açılır.")


def http_error(exc: LiveGridError) -> HTTPException:
    return HTTPException(exc.status, str(exc))
