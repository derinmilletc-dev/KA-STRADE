"""KAIS FARMER giriş onayı (C5). SAF fonksiyonlar: ağ yok, emir yok, yeni indikatör yok.

Veri kaynağı yalnız Original V2 `StrategyResult.legacy` çıktısıdır (`analysis` ve `mtf`); momentum/trap/breakout/MTF yeniden hesaplanmaz.

REPODAN alınan eşikler (kaynak):
  trap_score        <= 35  strategies/kais_original.py:26 (execution_core `max_trap_score` varsayılanı da 35)
  breakout_quality  >= 50  main.HISTORICAL_POLICY_DEFAULT["breakout_quality_threshold"] (main.py:394; fastapi gerektirdiği için burada kopya)
  taker ücreti      0.0005 dca_engine/plan.py:23
  MTF yön desteği   mtf["direction"] == aday yönü  (shared_mtf_decision onaylanmazsa "BEKLE" döner; `alignment` 0-100 ağırlıklı güven puanıdır, yön DEĞİL)

UNRESOLVED (repoda yok; uydurulmadı): cost/target, target/ATR, stop/ATR kabul eşikleri. `ConfirmParams`'ta None = uygulanmaz, yalnız raporlanır;
değerleri backtest (C12) belirler. Matematiksel imkânsızlıklar (stop mesafesi <= 0, notional <= 0, ATR yok) HER ZAMAN reddedilir."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..dca_engine.plan import TAKER_FEE
from .store import CLAIMED, DUPLICATE, OVER_CAP, FarmerStore

CONFIDENCE, STRATEGY_QUALITY = "CONFIDENCE", "STRATEGY_QUALITY"
VARIANTS = (CONFIDENCE, STRATEGY_QUALITY)
CONFIRMED, REJECTED = "CONFIRMED", "REJECTED"

REPO_MAX_TRAP_SCORE = 35
REPO_MIN_BREAKOUT_QUALITY = 50
DEFAULT_BUFFER_USDT = 0.05          # Faz B §6.3 maliyet formülündeki tampon

# red nedenleri
SCORE_TOO_LOW, INVALID_DIRECTION, MOMENTUM_MISMATCH = "SCORE_TOO_LOW", "INVALID_DIRECTION", "MOMENTUM_MISMATCH"
MTF_NOT_CONFIRMED, SHORT_FILTER_REJECTED = "MTF_NOT_CONFIRMED", "SHORT_FILTER_REJECTED"
TRAP_REJECTED, BREAKOUT_QUALITY_REJECTED = "TRAP_REJECTED", "BREAKOUT_QUALITY_REJECTED"
COST_INFEASIBLE, ATR_INFEASIBLE = "COST_INFEASIBLE", "ATR_INFEASIBLE"
MARGIN_CAP, OCCUPIED, ENTRY_LOCKED, SAFETY_GATE, ARM_BLOCKED = "MARGIN_CAP", "OCCUPIED", "ENTRY_LOCKED", "SAFETY_GATE", "ARM_BLOCKED"

_MOMENTUM_FOR = {"LONG": "Pozitif", "SHORT": "Negatif"}


@dataclass(frozen=True)
class ConfirmParams:
    variant: str
    min_score: int
    margin_usdt: float
    leverage: float
    target_usdt: float
    max_loss_usdt: float
    buffer_usdt: float = DEFAULT_BUFFER_USDT
    max_trap_score: float = REPO_MAX_TRAP_SCORE
    min_breakout_quality: float = REPO_MIN_BREAKOUT_QUALITY
    # UNRESOLVED — backtest parametreleri (None = uygulanmaz, yalnız raporlanır)
    max_cost_over_target: float | None = None
    max_target_over_atr: float | None = None
    min_stop_over_atr: float | None = None

    def __post_init__(self) -> None:
        if self.variant not in VARIANTS:
            raise ValueError(f"bilinmeyen varyant: {self.variant}")


@dataclass(frozen=True)
class FarmerCandidate:
    symbol: str
    interval: str
    candle_open_ms: int
    analysis: Mapping[str, Any]
    mtf: Mapping[str, Any]

    @classmethod
    def from_legacy(cls, symbol: str, interval: str, candle_open_ms: int, legacy: Mapping[str, Any]) -> "FarmerCandidate":
        return cls(symbol, interval, int(candle_open_ms), legacy.get("analysis") or {}, legacy.get("mtf") or {})

    @property
    def direction(self) -> str:
        return str(self.analysis.get("direction") or "")


@dataclass(frozen=True)
class Feasibility:
    notional: float
    est_cost_usdt: float
    target_pct: float | None
    stop_pct: float | None
    cost_over_target: float | None
    target_over_atr: float | None
    stop_over_atr: float | None
    atr_pct: float | None

    def as_dict(self) -> dict[str, Any]:
        return dict(vars(self))


def feasibility(analysis: Mapping[str, Any], p: ConfirmParams) -> Feasibility:
    """Faz B §6.3: maliyet = notional·taker·2 + tampon; hedef% = (hedef+maliyet)/notional; stop% = (maks zarar − maliyet)/notional."""
    notional = float(p.margin_usdt) * float(p.leverage)
    cost = notional * TAKER_FEE * 2 + float(p.buffer_usdt) if notional > 0 else 0.0
    target_pct = (p.target_usdt + cost) / notional * 100 if notional > 0 else None
    stop_pct = (p.max_loss_usdt - cost) / notional * 100 if notional > 0 else None
    cost_over_target = cost / p.target_usdt if p.target_usdt > 0 else None
    price, atr = _num(analysis.get("entry")), _num(analysis.get("atr"))
    atr_pct = atr / price * 100 if price and atr and price > 0 and atr > 0 else None
    t_atr = target_pct / atr_pct if atr_pct and target_pct is not None else None
    s_atr = stop_pct / atr_pct if atr_pct and stop_pct is not None else None
    return Feasibility(notional, cost, target_pct, stop_pct, cost_over_target, t_atr, s_atr, atr_pct)


def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ConfirmationResult:
    status: str
    reasons: tuple[str, ...]
    feasibility: Feasibility
    checks: dict[str, Any] = field(default_factory=dict)

    @property
    def confirmed(self) -> bool:
        return self.status == CONFIRMED

    @property
    def reject_reason(self) -> str:
        return self.reasons[0] if self.reasons else ""


def confirm(candidate: FarmerCandidate, p: ConfirmParams) -> ConfirmationResult:
    """Deterministik: aynı girdi → aynı sonuç. Tüm başarısız kontroller sırayla toplanır; `reject_reason` ilkidir."""
    a, mtf, direction = candidate.analysis, candidate.mtf, candidate.direction
    feas = feasibility(a, p)
    checks: dict[str, Any] = {}
    reasons: list[str] = []
    if direction not in ("LONG", "SHORT"):             # BEKLE ve bilinmeyen değerler giriş DEĞİLDİR
        checks["direction"] = {"ok": False, "value": direction}
        return ConfirmationResult(REJECTED, (INVALID_DIRECTION,), feas, checks)
    checks["direction"] = {"ok": True, "value": direction}

    confidence = _num(a.get("confidence"))
    ok = confidence is not None and confidence >= p.min_score
    checks["score"] = {"ok": ok, "value": confidence, "min": p.min_score}
    if not ok:
        reasons.append(SCORE_TOO_LOW)

    momentum = a.get("momentum")
    ok = momentum == _MOMENTUM_FOR[direction]
    checks["momentum"] = {"ok": ok, "value": momentum, "required": _MOMENTUM_FOR[direction]}
    if not ok:
        reasons.append(MOMENTUM_MISMATCH)

    permission, mtf_dir = mtf.get("entry_permission") is True, mtf.get("direction")
    ok = permission and mtf_dir == direction
    checks["mtf"] = {"ok": ok, "entry_permission": mtf.get("entry_permission"), "direction": mtf_dir, "alignment": mtf.get("alignment")}
    if not ok:
        reasons.append(MTF_NOT_CONFIRMED)

    if direction == "SHORT":                            # LONG'da short filtresi uygulanmaz
        sf = mtf.get("short_filter_v2")
        ok = isinstance(sf, Mapping) and sf.get("accepted") is True
        checks["short_filter"] = {"ok": ok, "present": isinstance(sf, Mapping), "reason": sf.get("reason") if isinstance(sf, Mapping) else None}
        if not ok:
            reasons.append(SHORT_FILTER_REJECTED)

    if p.variant == STRATEGY_QUALITY:
        radar = a.get("radar") if isinstance(a.get("radar"), Mapping) else {}
        trap, breakout = _num(radar.get("trap_score")), _num(radar.get("breakout_quality"))
        ok = trap is not None and trap <= p.max_trap_score             # eksik veri = kabul değil (execution_core de eksik trap'i 100 sayar)
        checks["trap"] = {"ok": ok, "value": trap, "max": p.max_trap_score}
        if not ok:
            reasons.append(TRAP_REJECTED)
        ok = breakout is not None and breakout >= p.min_breakout_quality
        checks["breakout"] = {"ok": ok, "value": breakout, "min": p.min_breakout_quality}
        if not ok:
            reasons.append(BREAKOUT_QUALITY_REJECTED)

    cost_ok = feas.notional > 0 and feas.stop_pct is not None and feas.stop_pct > 0 and p.target_usdt > 0
    if cost_ok and p.max_cost_over_target is not None:
        cost_ok = feas.cost_over_target is not None and feas.cost_over_target <= p.max_cost_over_target
    checks["cost"] = {"ok": cost_ok, "cost_over_target": feas.cost_over_target, "max": p.max_cost_over_target}
    if not cost_ok:
        reasons.append(COST_INFEASIBLE)

    atr_ok = feas.atr_pct is not None
    if atr_ok and p.max_target_over_atr is not None:
        atr_ok = feas.target_over_atr is not None and feas.target_over_atr <= p.max_target_over_atr
    if atr_ok and p.min_stop_over_atr is not None:
        atr_ok = feas.stop_over_atr is not None and feas.stop_over_atr >= p.min_stop_over_atr
    checks["atr"] = {"ok": atr_ok, "atr_pct": feas.atr_pct, "target_over_atr": feas.target_over_atr, "stop_over_atr": feas.stop_over_atr,
                     "max_target_over_atr": p.max_target_over_atr, "min_stop_over_atr": p.min_stop_over_atr}
    if not atr_ok:
        reasons.append(ATR_INFEASIBLE)

    return ConfirmationResult(REJECTED if reasons else CONFIRMED, tuple(reasons), feas, checks)


# ---------------------------------------------------------------- risk / doluluk + aynı-mum kilidi
@dataclass(frozen=True)
class RiskView:
    """Çağıran (orkestratör) mevcut güvenlik kapılarını DEĞERLENDİRİP buraya taşır; boş = engel yok.
    occupied      : sembolde mevcut Farmer / Original V2 / manuel / başka strateji pozisyonu ya da aktif Farmer motoru (OCCUPIED)
    arm_blocked   : ARM / canlı giriş yetkisi / gerçek-işlem kilidi (ARM_BLOCKED)
    safety_blocked: hesap rezervasyonu, account_fence, yürütme/hazırlık kapıları vb. (SAFETY_GATE)"""
    occupied: tuple[str, ...] = ()
    arm_blocked: tuple[str, ...] = ()
    safety_blocked: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntryDecision:
    status: str                     # CONFIRMED (emir gönderilebilir, kilit alındı) | REJECTED
    reason: str
    confirmation: ConfirmationResult
    grid_id: str = ""


def decide_entry(store: FarmerStore, account_key: str, candidate: FarmerCandidate, p: ConfirmParams, risk: RiskView, *,
                 grid_id: str, cap_usdt: float) -> EntryDecision:
    """Onay → risk/doluluk → atomik kilit+marjin. Emir yalnız `status == CONFIRMED` ise gönderilir; kilit EMİRDEN ÖNCE alınır.
    Reddedilen aday kilit tüketmez (yalnız gerçek deneme mumu kilitler)."""
    conf = confirm(candidate, p)
    snapshot = {"analysis": {k: candidate.analysis.get(k) for k in ("direction", "confidence", "momentum", "atr", "entry", "radar")},
                "mtf": {k: candidate.mtf.get(k) for k in ("direction", "alignment", "entry_permission", "verdict")},
                "variant": p.variant, "feasibility": conf.feasibility.as_dict(), "checks": conf.checks}
    score = float(_num(candidate.analysis.get("confidence")) or 0.0)

    def reject(reason: str) -> EntryDecision:
        store.save_candidate(account_key, candidate.symbol, candidate.candle_open_ms, candidate.direction or "BEKLE", score, snapshot,
                             status="REJECTED", reject_reason=reason)
        return EntryDecision(REJECTED, reason, conf, grid_id)

    if not conf.confirmed:
        return reject(conf.reject_reason)
    for reason, items in ((ARM_BLOCKED, risk.arm_blocked), (SAFETY_GATE, risk.safety_blocked), (OCCUPIED, risk.occupied)):
        if items:
            snapshot["blockers"] = list(items)
            return reject(reason)
    claim = store.claim_entry(account_key, candidate.symbol, candidate.interval, candidate.candle_open_ms, grid_id, p.margin_usdt, cap_usdt=cap_usdt)
    if claim == DUPLICATE:
        return reject(ENTRY_LOCKED)
    if claim == OVER_CAP:
        return reject(MARGIN_CAP)
    assert claim == CLAIMED
    store.save_candidate(account_key, candidate.symbol, candidate.candle_open_ms, candidate.direction, score, snapshot, status="CONFIRMING")
    return EntryDecision(CONFIRMED, "", conf, grid_id)
