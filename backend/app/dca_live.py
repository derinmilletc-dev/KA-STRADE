"""DCA botu çalıştırıcısı (Faz 3): `LiveGridRunner`'ın alt sınıfı; ayrı defter (`dca_live.sqlite3` / `dca_demo.sqlite3`), `kd-` emir kimliği.

Grid'den devralınanlar (değişmedi): kullanıcı başına izolasyon, döngü ve kilitler, açılış kurtarması (önce eşitle, korumayı doğrula,
sonra döngü), ARM süresi dolunca duraklatma, günlük zarar sınırı, likidasyon izleme, acil durdurma.
DCA'ya özel: (1) plan → motor ayarı `spec_from_plan`; plan parametreleri (`params`) ayara yazılır, böylece yeni tur aynı parametrelerle
yeniden hesaplanabilir; (2) tur bitince (CYCLE_DONE) yeni tur ANCAK tüm kapılar yeniden geçilirse başlar: canlı giriş yetkisi, kilitler,
günlük zarar hakkı ve çağıranın verdiği `cycle_planner` (güncel fiyat/kural/risk ile `prepare_dca_plan`) — herhangi biri geçmezse bot DURUR;
(3) canlı DCA ayrı bayrakla (`KAISTRADE_DCA_LIVE`, varsayılan AÇIK; KAISTRADE_DCA_LIVE=0 ile kapanır); ilk canlı testte toplam marjin sınırı `KAISTRADE_DCA_LIVE_MAX_MARGIN` (varsayılan 10 USDT)."""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import asyncio

from fastapi import HTTPException

from .dca_engine.engine import CYCLE_DONE, DcaEngine, spec_from_plan
from .dca_engine.plan import DcaPlanError
from .grid_engine.engine import GridSpec, STOPPED
from .grid_engine.store import GridStore
from .grid_live import LiveGridError, LiveGridRunner, grid_spec_from_row

logger = logging.getLogger(__name__)

ENV_FLAG = "KAISTRADE_DCA_LIVE"
DEMO_ENV_FLAG = "KAISTRADE_DCA_DEMO"
MAX_MARGIN_ENV = "KAISTRADE_DCA_LIVE_MAX_MARGIN"
DEFAULT_LIVE_MAX_MARGIN_USDT = 10.0
CONFIRM_START = "CANLI DCA BAŞLAT"
CONFIRM_DEMO_START = "DEMO DCA BAŞLAT"
CONFIRM_EMERGENCY = "DCA ACİL DURDUR"
MAX_BOTS_PER_USER = 3
PARAM_KEYS = ("base_margin_usdt", "safety_orders", "step_pct", "volume_multiplier", "take_profit_pct", "stop_gap_pct", "leverage")


def _truthy(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def live_enabled() -> bool:
    """Canlı DCA varsayılan AÇIK (başlatmak yine ARM, yazılı onay ve toplam marjin sınırı ister); kapatmak için KAISTRADE_DCA_LIVE=0."""
    return str(os.getenv(ENV_FLAG, "1")).strip().lower() not in {"0", "false", "no", "off"}


def demo_enabled() -> bool:
    """Demo DCA varsayılan AÇIK (gerçek para yok)."""
    return str(os.getenv(DEMO_ENV_FLAG, "1")).strip().lower() not in {"0", "false", "no", "off"}


def live_max_margin_usdt() -> float:
    try:
        value = float(os.getenv(MAX_MARGIN_ENV, DEFAULT_LIVE_MAX_MARGIN_USDT))
    except (TypeError, ValueError):
        return DEFAULT_LIVE_MAX_MARGIN_USDT
    return value if value > 0 else DEFAULT_LIVE_MAX_MARGIN_USDT


def require_enabled() -> None:
    if not live_enabled():
        raise HTTPException(403, f"Canlı DCA kapalı (sunucuda {ENV_FLAG}=0). Açmak için {ENV_FLAG} değerini kaldır ya da 1 yap (ilk testte toplam marjin en çok "
                                 f"{live_max_margin_usdt():g} USDT).")


def check_live_margin(plan: dict[str, Any]) -> None:
    limit = live_max_margin_usdt()
    if float(plan.get("total_margin_usdt") or 0) > limit + 1e-9:
        raise DcaPlanError(f"Canlı DCA şu an en çok {limit:g} USDT toplam marjinle çalışır (plan {plan['total_margin_usdt']} USDT); "
                           "marjini düşür ya da sunucu sınırını bilinçli olarak yükselt.", 422)


def http_error(exc: LiveGridError | DcaPlanError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def plan_params(body: Any) -> dict[str, Any]:
    """İstek gövdesinden yeni tur için saklanacak parametreler."""
    return {key: getattr(body, key) for key in PARAM_KEYS}


class DcaRunner(LiveGridRunner):
    kind_label = "dca"

    def __init__(self, store: GridStore, *, account: str = "LIVE") -> None:
        super().__init__(store, account=account)
        # Yeni tur planlayıcı: async (engine, state) -> plan (DcaPlanError fırlatabilir). Uç nokta katmanı atar; yoksa tur BİTER.
        self.cycle_planner: Any = None

    # ---- ayar / motor
    def _spec_for_plan(self, grid_id: str, symbol: str, plan: dict[str, Any]) -> GridSpec:
        spec = spec_from_plan(grid_id, symbol, plan, tick=float(plan["tick"]))
        spec.meta["params"] = dict(plan.get("params") or {})
        return spec

    def _start_note(self, symbol: str, plan: dict[str, Any]) -> str:
        return (f"{symbol} DCA botu başlatılıyor ({plan['direction']}, taban + {plan['safety_orders']} ek emir, "
                f"toplam marjin {plan['total_margin_usdt']} USDT{', tur açık' if plan.get('recycle') else ''}).")

    def _new_engine(self, spec: GridSpec, adapter: Any, state: dict[str, Any], *, user_id: str = "") -> DcaEngine:
        def risk_gate() -> tuple[bool, str]:
            if self.account == "DEMO":
                return True, ""
            if self.blocked_check(state):
                return False, "canlı yürütme kilitli"
            if state.get("real_trading_locked") is not False:
                return False, "gerçek işlem kilidi kapalı"
            return True, ""

        return DcaEngine(adapter, self.store, spec, risk_gate=risk_gate, sleep=time.sleep, user_id=user_id, account=self.account)

    def grid_payload(self, row: Any) -> dict[str, Any]:
        out = super().grid_payload(row)
        try:
            engine = DcaEngine(None, self.store, grid_spec_from_row(row), account=self.account)   # defter satırı var: yalnız okuma
            out["stats"] = engine.stats()
            out["position_qty"] = engine.grid_net()
        except Exception:  # noqa: BLE001 - gösterim hatası botu etkilemez
            logger.warning("DCA_STATS_FAILED grid=%s", row["grid_id"])
        out["kind"] = "DCA"
        out["params"] = (out.get("spec") or {}).get("meta", {}).get("params")
        return out

    # ---- tur döngüsü
    async def _after_tick(self, engine: Any, state: dict[str, Any], status: str) -> str:
        if status != CYCLE_DONE:
            return status
        return await self._next_cycle(engine, state)

    def _end(self, engine: Any, state: dict[str, Any], reason: str) -> str:
        self.store.set_grid(engine.gid, status=STOPPED, halt_reason=reason)
        self._record(state, "LIVE_GRID_STATUS", f"{engine.sym} DCA botu durdu: {reason}", grid_id=engine.gid, status=STOPPED)
        return STOPPED

    async def _next_cycle(self, engine: Any, state: dict[str, Any]) -> str:
        """Kâr hedefi doldu. Yeni tur yalnız tüm kapılar yeniden geçilirse açılır; aksi halde bot durur (açık pozisyon yoktur)."""
        if not self.entry_authority(state):
            return self._end(engine, state, "Kâr hedefi doldu; canlı giriş yetkisi (ARM) kapalı olduğu için yeni tur açılmadı")
        if self.blocked_check(state):
            return self._end(engine, state, "Kâr hedefi doldu; canlı yürütme kilitli olduğu için yeni tur açılmadı")
        limit = self.daily_limit_of(state)
        row = self.store.grid(engine.gid)
        if limit is not None and self.day_summary(str(row["user_id"] or ""), owner=False, limit=limit)["reached"]:
            return self._end(engine, state, "Kâr hedefi doldu; günlük zarar limitine ulaşıldığı için yeni tur açılmadı")
        planner = self.cycle_planner
        if planner is None:
            return self._end(engine, state, "Kâr hedefi doldu; yeni tur planlayıcısı hazır olmadığı için yeni tur açılmadı")
        try:
            plan = await planner(engine, state)
        except (DcaPlanError, LiveGridError, HTTPException) as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            return self._end(engine, state, f"Kâr hedefi doldu; yeni tur risk kapılarından geçmedi: {detail}")
        except Exception as exc:  # noqa: BLE001 - belirsizlikte yeni risk açılmaz
            logger.warning("DCA_REPLAN_FAILED grid=%s type=%s", engine.gid, type(exc).__name__)
            return self._end(engine, state, "Kâr hedefi doldu; yeni tur planı güvenle hesaplanamadı, yeni tur açılmadı")
        spec = self._spec_for_plan(engine.gid, engine.sym, plan)
        started = await asyncio.to_thread(engine.begin_cycle, spec)
        if not started:
            return self._end(engine, state, "Kâr hedefi doldu; borsada bu bota ait kalıntı emir/pozisyon göründüğü için yeni tur açılmadı")
        self._record(state, "LIVE_GRID_STATUS", f"{engine.sym} DCA yeni tur başladı (marjin {plan['total_margin_usdt']} USDT).",
                     grid_id=engine.gid, status=engine.status)
        return engine.status


def runner_for(app: Any) -> DcaRunner:
    existing = getattr(app.state, "live_dca_runner", None)
    if existing is not None:
        return existing
    from .local_storage import DATA_DIR
    runner = DcaRunner(GridStore(str(DATA_DIR / "dca_live.sqlite3"), check_same_thread=False))
    app.state.live_dca_runner = runner
    return runner


def demo_runner_for(app: Any) -> DcaRunner:
    existing = getattr(app.state, "demo_dca_runner", None)
    if existing is not None:
        return existing
    from .local_storage import DATA_DIR
    runner = DcaRunner(GridStore(str(DATA_DIR / "dca_demo.sqlite3"), check_same_thread=False), account="DEMO")
    app.state.demo_dca_runner = runner
    return runner
