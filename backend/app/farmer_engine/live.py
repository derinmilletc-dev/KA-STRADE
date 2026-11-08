"""KAIS FARMER canlı hesap adaptörleri (C7). ORKESTRATÖR PORTLARINI mevcut sistemin kapılarına bağlar; KENDİ kapı/limit/PnL mantığı YOKTUR.

Her kapı mevcut modüllerden gelir (`v25_execution`, `account_reservations`, `daily_ledger`, `original_v2_live`, `market_universe`).
Bu modül import anında fastapi gerektirmez: `live` (v25_execution) ve `reservations` modülleri ENJEKTE edilir (varsayılan: tembel import).
Hiçbir yerde emir gönderilmez; emir yalnız `FarmerEngine` → borsa adaptöründen gider (kapı: capability.live_entry_blockers).

Canlı varsayılan KAPALI: KAISTRADE_FARMER_LIVE + KAISTRADE_FARMER_TPM_VERIFIED ikisi de açık değilse `entry_gates` her zaman engel döndürür."""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Awaitable, Callable, Mapping

from ..grid_engine.ports import OrderInfo
from . import DISPLAY_NAME
from .capability import live_entry_blockers
from .ids import FARMER_CLIENT_ID, farmer_grid_prefix
from .orchestrator import ExchangeSnapshot, GateState

KIND = "FARMER"
RESERVATION_TTL = 300.0


class LoopBridge:
    """Orkestratör bir iş parçacığında (to_thread) çalışır; mevcut async işlevler olay döngüsüne bu köprüyle gider."""

    def __init__(self, loop: asyncio.AbstractEventLoop, timeout: float = 20.0) -> None:
        self.loop, self.timeout = loop, timeout

    def __call__(self, coro: Awaitable[Any]) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(self.timeout)


# ------------------------------------------------------------------ borsa görüntüsü
def snapshot_from_account(raw: Mapping[str, Any], at: float) -> ExchangeSnapshot:
    """`v25_execution.account_snapshot` çıktısı → ExchangeSnapshot. Algo emir verisi doğrulanmadıysa ok=False (doluluk bilinemez → giriş yok)."""
    if raw.get("open_algo_orders_available") is not True:
        return ExchangeSnapshot(False, at, error="ALGO_ORDERS_UNVERIFIED")
    positions: dict[str, float] = {}
    marks: dict[str, float] = {}
    for row in raw.get("positions") or []:
        symbol, qty = str(row.get("symbol") or "").upper(), float(row.get("quantity") or 0.0)
        if symbol and qty:
            positions[symbol] = qty if row.get("direction") == "LONG" else -qty
            mark = float(row.get("mark_price") or 0.0)
            if mark > 0:
                marks[symbol] = mark
    orders: list[OrderInfo] = []
    for row in raw.get("open_orders") or []:
        orders.append(OrderInfo(str(row.get("client_order_id") or ""), str(row.get("symbol") or "").upper(), str(row.get("side") or ""),
                                str(row.get("type") or ""), float(row.get("price") or 0.0), 0.0, float(row.get("quantity") or 0.0),
                                float(row.get("executed_quantity") or 0.0), str(row.get("status") or "NEW"), bool(row.get("reduce_only"))))
    for row in raw.get("open_algo_orders") or []:
        orders.append(OrderInfo(str(row.get("client_algo_id") or ""), str(row.get("symbol") or "").upper(), str(row.get("side") or ""),
                                str(row.get("type") or ""), 0.0, float(row.get("trigger_price") or 0.0), float(row.get("quantity") or 0.0),
                                0.0, "NEW", bool(row.get("reduce_only"))))
    return ExchangeSnapshot(True, at, positions, orders, marks)


class LiveSnapshotSource:
    """Tur başına TEK hesap görüntüsü (mevcut `account_snapshot`; taze okuma, motor başına değil)."""

    def __init__(self, bridge: LoopBridge, client_factory: Callable[[], Any], live: Any, clock: Callable[[], float] = time.time) -> None:
        self.bridge, self.client_factory, self.live, self.clock = bridge, client_factory, live, clock

    def read(self) -> ExchangeSnapshot:
        raw = self.bridge(self.live.account_snapshot(self.client_factory(), None, None, cached=False, source="farmer"))
        return snapshot_from_account(raw, self.clock())


# ------------------------------------------------------------------ kapılar
class LiveGates:
    """ARM / canlı kilit / yürütme-hazırlık / bakım / canlı bayrakları: mevcut `v25_execution` işlevleri. Farmer'a özgü ek kapı: capability bayrakları."""

    def __init__(self, application: Any, live: Any, *, env: Mapping[str, str] | None = None) -> None:
        self.application, self.live, self.env = application, live, os.environ if env is None else env

    def entry_gates(self) -> GateState:
        live, state = self.live, self.application.state.v25_execution
        arm: list[str] = []
        safety: list[str] = [f"CAPABILITY:{item}" for item in live_entry_blockers(self.env)]
        if not live.is_armed(state):
            arm.append("ARM_EXPIRED_OR_OFF")
        if state.get("real_trading_locked") is not False:
            arm.append("REAL_TRADING_LOCKED")
        if live.live_execution_blocked(state):
            safety.append("EXECUTION_BLOCKED")
        if not state.get("recovery_ready", False):
            safety.append("RECOVERY_NOT_READY")
        try:
            live.guard_new_entry(getattr(self.application.state, "maintenance", None))
        except Exception:                                            # noqa: BLE001 — bakım kapısı HTTPException fırlatır
            safety.append("MAINTENANCE")
        return GateState(tuple(arm), tuple(safety))

    def foreign_occupied(self, symbol: str) -> tuple[str, ...]:
        """Grid/DCA çalıştırıcıları (Farmer görünümü hariç) ve Original V2 / manuel planlar: mevcut doluluk bilgisi."""
        out: list[str] = []
        state = self.application.state.v25_execution
        owner = self.live.live_reservation_owner(state, None)
        for runner in self.live.live_bot_runners(self.application):
            if getattr(runner, "is_farmer_view", False):
                continue
            try:
                if runner.symbol_busy(symbol, owner, owner=True):
                    out.append("OTHER_BOT")
            except Exception:                                        # noqa: BLE001 — okunamayan doluluk = dolu say
                out.append("OCCUPANCY_UNREADABLE")
        for plan in (state.get("plans") or {}).values():
            if isinstance(plan, dict) and str(plan.get("symbol") or "").upper() == symbol and self.live.live_plan_is_active(plan):
                out.append("ACTIVE_PLAN")
        return tuple(out)


def daily_loss_blockers(state: Mapping[str, Any], daily_ledger: Any, *, now: float | None = None) -> tuple[str, ...]:
    """Hesap-geneli günlük zarar: Grid ile AYNI kaynak (`daily_ledger.cached` + politika `daily_loss_limit`). Farmer'a özgü sayaç YOK.
    Limit tanımsızsa engel yok (mevcut Grid davranışı); defter okunamıyorsa yeni giriş kapanır (stoplar sürer)."""
    try:
        limit = float((state.get("policy") or {}).get("daily_loss_limit"))
    except (TypeError, ValueError):
        return ()
    if limit <= 0:
        return ()
    ledger = daily_ledger.cached(state, now=now) if now is not None else daily_ledger.cached(state)
    if ledger is None:
        return ("DAILY_LEDGER_UNAVAILABLE",)
    if -float(ledger["totals"]["net"]) >= limit:
        return ("DAILY_LOSS_LIMIT",)
    return ()


# ------------------------------------------------------------------ rezervasyon (mevcut account_reservations)
class LiveReservations:
    def __init__(self, reservations: Any, owner_of: Callable[[], str]) -> None:
        self.r, self.owner_of = reservations, owner_of
        self._tokens: dict[str, str] = {}

    def reserve(self, symbol: str, grid_id: str) -> bool:
        try:
            self._tokens[grid_id] = self.r.reserve(self.owner_of(), KIND, symbol, 0.0, ttl=RESERVATION_TTL)
            return True
        except (self.r.ReservationConflict, self.r.ReservationUnavailable):
            return False

    def _token(self, grid_id: str) -> str | None:
        token = self._tokens.get(grid_id)
        if token:
            return token
        prefix = farmer_grid_prefix(grid_id)                          # yeniden başlatma sonrası: client_order_id ile bul
        for row in self.r.rows(self.owner_of(), states=self.r.ACTIVE):
            if row.get("kind") == KIND and str(row.get("client_order_id") or "").startswith(prefix):
                return row["token"]
        return None

    def mark_submitted(self, grid_id: str, client_order_id: str) -> None:
        self.r.mark_submitted(self._tokens.get(grid_id), client_order_id)

    def consume(self, grid_id: str) -> None:
        self.r.consume(self._token(grid_id), f"{DISPLAY_NAME}:FILLED")

    def release(self, grid_id: str) -> None:
        self.r.release(self._token(grid_id), f"{DISPLAY_NAME}:NO_ORDER")

    def orphans(self) -> list[str]:
        out: list[str] = []
        for row in self.r.unreconciled(self.owner_of()):
            if row.get("kind") != KIND:
                continue
            match = FARMER_CLIENT_ID.match(str(row.get("client_order_id") or ""))
            out.append(match.group(1) if match else str(row.get("token")))
        return out


# ------------------------------------------------------------------ evren (mevcut üretim yeniden kullanılır; yeni sıralama YOK)
class LiveUniverse:
    """1) Bugünkü Original V2 evreni varsa (yan etkisiz okuma) onu paylaşır. 2) Yoksa AYNI `ranked_original_live_symbols` ile günde bir kez
    kendi evrenini üretir (Original V2 yapılandırmasına/akışına DOKUNMAZ). Okunamazsa boş liste (yeni giriş adayı yok)."""

    def __init__(self, application: Any, bridge: LoopBridge, public_client_factory: Callable[[], Any], original_v2_live: Any, market_universe: Any, *,
                 count: int | None = None, clock: Callable[[], float] = time.time) -> None:
        self.application, self.bridge, self.client_factory = application, bridge, public_client_factory
        self.o2, self.mu, self.count, self.clock = original_v2_live, market_universe, count, clock
        self._day, self._own = "", []

    def symbols(self) -> list[str]:
        state = self.application.state.v25_execution
        try:
            config = self.o2.settings(state)
            shared = self.o2.selected_symbols(config)
            if shared:
                return list(shared[: self.count] if self.count else shared)
        except Exception:                                             # noqa: BLE001 — Original V2 kapalı/ayarsız: kendi evrenine geç
            pass
        day = time.strftime("%Y-%m-%d", time.gmtime(self.clock()))
        if self._day != day:
            try:
                self._own = self.bridge(self._build())
                self._day = day
            except Exception:                                         # noqa: BLE001
                return list(self._own)
        return list(self._own[: self.count] if self.count else self._own)

    async def _build(self) -> list[str]:
        client = self.client_factory()
        info = await client.public_get("/fapi/v1/exchangeInfo")
        tickers = await client.public_get("/fapi/v1/ticker/24hr")
        book = await client.public_get("/fapi/v1/ticker/bookTicker")
        ranked, _eligible = self.mu.ranked_original_live_symbols(info, tickers, book, now_ms=int(self.clock() * 1000))
        return list(ranked)


# ------------------------------------------------------------------ diğer stratejilere görünüm (additive)
class FarmerRunnerView:
    """`live_bot_runners` arayüzünün (symbol_busy / reserved_risk) Farmer karşılığı. Örnek `application.state.live_farmer_runner` ayarlanmadıkça
    mevcut listeye hiçbir şey eklenmez (Farmer KAPALI = liste aynı)."""
    is_farmer_view = True

    def __init__(self, orchestrator_getter: Callable[[], Any]) -> None:
        self._get = orchestrator_getter

    def _rows(self) -> list[Any]:
        orch = self._get()
        return list(orch.engines.values()) if orch is not None else []

    def symbol_busy(self, symbol: str, user_id: str = "", *, owner: bool = False) -> bool:
        return any(e.sym == symbol for e in self._rows())

    def reserved_risk(self, user_id: str, *, owner: bool = False, exclude: str | None = None) -> dict[str, Any]:
        engines = [e for e in self._rows() if exclude is None or e.gid != exclude]
        worst = sum(float(e.spec.meta.get("max_loss_usdt") or 0.0) for e in engines)
        return {"worst_loss_usdt": round(worst, 8), "pending_entry_notional_usdt": 0.0, "symbols": [e.sym for e in engines], "count": len(engines)}
