"""KAIS FARMER — arayüz için SAF görünüm katmanı (C10). fastapi/ağ/emir YOK; yalnız mevcut durumu okur ve doğrular.

Kural: arayüz marjin, risk, PnL, ARM, günlük kayıp ve koruma durumunun KAYNAĞI değildir. Bu modül yalnız backend'in bildiği durumu
JSON'a çevirir; hiçbir motor/ledger/PnL davranışını değiştirmez.

ÖNERİLEN değerler Faz B (KAIS-FARMER-FAZ-B.md §6.1) varsayılanlarıdır ve C12 backtest sonucuna kadar GEÇİCİDİR (`provisional=True`).
`PRACTICALITY_*` eşikleri yalnız ayar ekranındaki "pratik mi?" uyarısı içindir; hiçbir işlem kararında kullanılmaz ve backtest edilmemiştir."""
from __future__ import annotations

import math
from collections import Counter
from typing import Any, Mapping

from .farmer_engine import DISPLAY_NAME
from .farmer_engine.capability import LIVE_ENV_FLAG, TP_MARKET_VERIFIED_ENV, live_enabled, tp_market_declared_verified
from .farmer_engine.confirm import CONFIDENCE, ConfirmParams, feasibility
from .farmer_engine.margin import HARD_CAP_USDT, effective_capacity
from .farmer_engine.orchestrator import (ENTRY_PAUSED_ARM, ENTRY_PAUSED_SAFETY, INTERVENTION, OFF, RUNNING, STOPPING, FarmerSettings)

RECOMMENDED: dict[str, Any] = {
    "min_score": 92, "margin_usdt": 50.0, "leverage": 5, "target_usdt": 1.0, "max_loss_usdt": 1.5,
    "dead_score": 75, "dead_confirmations": 2, "max_positions": 5, "total_margin_cap": HARD_CAP_USDT,
}
LIMITS: dict[str, Any] = {
    "margin_options": [25, 50, 75, 100], "leverage": {"min": 1, "max": 5}, "min_score": {"min": 80, "max": 100},
    "target_usdt": {"min": 0.1, "max": 50.0}, "max_loss_usdt": {"min": 0.1, "max": 100.0}, "dead_score": {"min": 50, "max": 95},
    "dead_confirmations": {"min": 1, "max": 5}, "max_positions": {"min": 1, "max": 5}, "total_margin_cap": {"min": 25.0, "max": HARD_CAP_USDT},
}
PRACTICALITY_TIGHT_COST_RATIO = 0.5       # tahmini tur maliyeti hedefin yarısını geçerse "TIGHT"
PRACTICALITY_TIGHT_STOP_PCT = 0.5         # stop mesafesi (%) bunun altındaysa "TIGHT"
PRACTICALITY_HIGH_LEVERAGE = 5
LEGACY_MAX_LEVERAGE = 10                  # eskiden izin verilen en yüksek kaldıraç; daha büyük değer hâlâ REDDEDİLİR


class SettingsError(ValueError):
    """`code` arayüzün müşteriye çevirdiği kısa kod; ham hata müşteriye gösterilmez."""

    def __init__(self, code: str, field: str) -> None:
        super().__init__(f"{field}:{code}")
        self.code, self.field = code, field


def _number(body: Mapping[str, Any], key: str, default: Any, *, integer: bool = False, nullable: bool = False) -> Any:
    value = body.get(key, default)
    if value is None and nullable:
        return None
    if isinstance(value, bool) or value is None:
        raise SettingsError("INVALID_NUMBER", key)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SettingsError("INVALID_NUMBER", key) from None
    if not math.isfinite(number):
        raise SettingsError("INVALID_NUMBER", key)
    if integer:
        if number != int(number):
            raise SettingsError("NOT_INTEGER", key)
        return int(number)
    return number


def _in_range(key: str, value: float) -> None:
    limit = LIMITS[key]
    if value < limit["min"] or value > limit["max"]:
        raise SettingsError("OUT_OF_RANGE", key)


USER_CHOICES = ("target_usdt", "max_loss_usdt", "total_margin_cap")    # kullanıcı açarken kendisi girer; varsayılan YOK


def parse_settings(body: Mapping[str, Any], base: Mapping[str, Any] | None = None, *, require_user: bool = False) -> FarmerSettings:
    """Gövdeyi doğrular ve `FarmerSettings` üretir. Eksik alan `base` (mevcut ayar) ya da ÖNERİLEN değerden gelir; geçersiz değer REDDEDİLİR
    (sessizce düzeltilmez). `require_user=True` iken kâr hedefi, en fazla kayıp ve toplam tavan gövdede zorunludur. Tavan HARD_CAP_USDT'yi aşamaz."""
    if require_user:                                                # kâr, kayıp, toplam tavan: gövdede açıkça gelmeli
        for key in USER_CHOICES:
            if key not in body or body[key] is None or body[key] == "":
                raise SettingsError("REQUIRED", key)
    ref = {**RECOMMENDED, **(dict(base) if base else {})}
    min_score = _number(body, "min_score", ref["min_score"], integer=True)
    margin = _number(body, "margin_usdt", ref["margin_usdt"])
    leverage = _number(body, "leverage", ref["leverage"], integer=True)
    if leverage is not None and LIMITS["leverage"]["max"] < leverage <= LEGACY_MAX_LEVERAGE:
        leverage = LIMITS["leverage"]["max"]            # tavan 5x: eski kayıtlı 6-10x ayar 5x'e iner, Farmer açılışta çökmez
    target = _number(body, "target_usdt", ref["target_usdt"])
    max_loss = _number(body, "max_loss_usdt", ref["max_loss_usdt"])
    dead_score = _number(body, "dead_score", ref.get("dead_score"), integer=True, nullable=True)
    dead_conf = _number(body, "dead_confirmations", ref["dead_confirmations"], integer=True)
    max_positions = _number(body, "max_positions", ref["max_positions"], integer=True)
    cap = _number(body, "total_margin_cap", ref["total_margin_cap"])
    _in_range("min_score", min_score)
    if margin not in LIMITS["margin_options"]:
        raise SettingsError("NOT_ALLOWED", "margin_usdt")
    _in_range("leverage", leverage)
    _in_range("target_usdt", target)
    _in_range("max_loss_usdt", max_loss)
    if dead_score is not None:
        _in_range("dead_score", dead_score)
    _in_range("dead_confirmations", dead_conf)
    _in_range("max_positions", max_positions)
    _in_range("total_margin_cap", cap)
    if cap > HARD_CAP_USDT:
        raise SettingsError("OUT_OF_RANGE", "total_margin_cap")
    if margin > cap:
        raise SettingsError("MARGIN_ABOVE_CAP", "margin_usdt")
    confirm = ConfirmParams(variant=str(ref.get("variant") or CONFIDENCE), min_score=min_score, margin_usdt=float(margin), leverage=float(leverage),
                            target_usdt=float(target), max_loss_usdt=float(max_loss))
    return FarmerSettings(confirm=confirm, max_positions=max_positions, total_margin_cap=float(cap), dead_score=dead_score, dead_confirmations=dead_conf)


def preview(settings: FarmerSettings) -> dict[str, Any]:
    """Risk önizlemesi: tüm sayılar backend'in mevcut `feasibility` ve `effective_capacity` hesabından gelir; arayüz hesap yapmaz."""
    c = settings.confirm
    f = feasibility({}, c)
    capacity = effective_capacity(settings.max_positions, settings.total_margin_cap, c.margin_usdt)
    warnings: list[str] = []
    impossible = f.notional <= 0 or f.stop_pct is None or f.stop_pct <= 0 or f.target_pct is None or f.target_pct <= 0 or capacity <= 0
    if f.cost_over_target is not None and f.cost_over_target >= PRACTICALITY_TIGHT_COST_RATIO:
        warnings.append("TARGET_TOO_SMALL")
    if f.stop_pct is not None and 0 < f.stop_pct < PRACTICALITY_TIGHT_STOP_PCT:
        warnings.append("STOP_TOO_TIGHT")
    if capacity < settings.max_positions:
        warnings.append("MARGIN_CAP_LIMITS")
    if c.leverage >= PRACTICALITY_HIGH_LEVERAGE:
        warnings.append("HIGH_LEVERAGE")
    verdict = "NOT_RECOMMENDED" if impossible else "TIGHT" if ({"TARGET_TOO_SMALL", "STOP_TOO_TIGHT"} & set(warnings)) else "GOOD"
    return {"margin_usdt": c.margin_usdt, "leverage": c.leverage, "notional_usdt": round(f.notional, 4), "target_usdt": c.target_usdt,
            "max_loss_usdt": c.max_loss_usdt, "est_cost_usdt": round(f.est_cost_usdt, 4), "target_pct": _round(f.target_pct),
            "stop_pct": _round(f.stop_pct), "cost_over_target": _round(f.cost_over_target), "effective_capacity": capacity,
            "max_positions": settings.max_positions, "total_margin_cap": settings.total_margin_cap, "verdict": verdict, "warnings": warnings,
            "verdict_basis": "PRACTICALITY_HEURISTIC"}


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def entry_blockers_public(env: Mapping[str, str] | None = None) -> list[str]:
    """Müşteriye/arayüze KOD olarak (ham operatör metni değil)."""
    codes: list[str] = []
    if not live_enabled(env):
        codes.append("LIVE_FLAG_OFF")
    if not tp_market_declared_verified(env):
        codes.append("EXCHANGE_BEHAVIOUR_NOT_DECLARED_VERIFIED")
    return codes


def why_no_position(view: Mapping[str, Any], scanner: list[Mapping[str, Any]], daily_loss: tuple[str, ...] = ()) -> dict[str, Any]:
    """Birincil engel + ayrıntı. YALNIZ backend'in gerçek durumundan; bilinmeyen neden uydurulmaz (`UNKNOWN` döner)."""
    state, slots, capacity = view.get("state"), view.get("slots") or {}, int(view.get("effective_capacity") or 0)
    details: list[dict[str, Any]] = []
    if state == INTERVENTION:
        primary = "INTERVENTION"
    elif state == ENTRY_PAUSED_ARM:
        primary = "ARM_EXPIRED"
    elif state == ENTRY_PAUSED_SAFETY:
        primary = "DAILY_LOSS" if daily_loss else "SAFETY_GATE"
        details = [{"code": code, "count": 1} for code in daily_loss]
    elif state == STOPPING:
        primary = "STOP_BOT"
    elif state == OFF:
        primary = "OFF"
    elif capacity <= 0:
        primary = "CAPACITY_ZERO"
    elif int(slots.get("free", 0)) <= 0:
        primary = "CAPACITY_FULL"
    elif not scanner:
        primary = "NO_CANDIDATES"
    else:
        counts = Counter((row.get("reason") or row["status"]) for row in scanner if row["status"] != "QUALIFIED")
        details = [{"code": code, "count": n} for code, n in counts.most_common()]
        if not counts:
            primary = "QUALIFIED_ONLY"
        elif all(row["status"] == "COOLING_DOWN" for row in scanner if row["status"] != "QUALIFIED"):
            primary = "COOLDOWN"
        elif any(row["status"] == "WAITING_CONFIRMATION" for row in scanner):
            primary = "WAITING_CONFIRMATION"
        else:
            primary = details[0]["code"] if details else "UNKNOWN"
    return {"code": primary, "details": details}


def _today_totals(results: list[Mapping[str, Any]], now: float) -> dict[str, Any]:
    start = now - (now % 86400)             # UTC gün başı
    verified = pending = unverified = 0
    net = 0.0
    for row in results:
        closed = float(row.get("closed_at") or 0)
        if closed <= 0 or closed < start:
            continue
        state = row.get("pnl_state")
        if state == "VERIFIED" and row.get("verified_net_pnl") is not None:
            verified += 1
            net += float(row["verified_net_pnl"])
        elif state == "UNVERIFIED":
            unverified += 1
        else:
            pending += 1
    return {"basis": "UTC_DAY", "verified_net_pnl": round(net, 6) if verified else None, "verified_count": verified,
            "pending_count": pending, "unverified_count": unverified}


def _scanned_count(orch: Any) -> int | None:
    """Son kapanmış mumda taranan (analizi çıkan) sembol sayısı; okunamazsa None (uydurma sayı yok)."""
    try:
        return len(orch.feed.cache.all())
    except Exception:                                   # noqa: BLE001
        return None


def _signal_seconds() -> int:
    from .farmer_engine.feed import INTERVAL_MS, SIGNAL_INTERVAL
    return INTERVAL_MS[SIGNAL_INTERVAL] // 1000


def build_status(runtime: Any | None, *, arm: Mapping[str, Any], env: Mapping[str, str] | None = None, now: float,
                 daily_loss: tuple[str, ...] = ()) -> dict[str, Any]:
    """`/farmer/status` gövdesi. Çalışma zamanı (runtime) kurulu değilse `available=False` + kapı kodları; asla uydurma sayı yok."""
    base: dict[str, Any] = {"ok": True, "name": DISPLAY_NAME, "available": runtime is not None, "blockers": entry_blockers_public(env),
                            "env_flags": {"live": LIVE_ENV_FLAG, "declared_verified": TP_MARKET_VERIFIED_ENV},
                            "recommended": dict(RECOMMENDED), "provisional": True, "limits": LIMITS, "arm": dict(arm), "hard_cap_usdt": HARD_CAP_USDT,
                            "signal_interval_s": _signal_seconds()}
    if runtime is None:
        return {**base, "state": OFF, "configured": False, "config": None, "preview": preview(parse_settings({})), "positions": [], "scanner": [],
                "slots": None, "effective_capacity": None, "today": None, "why": {"code": "UNAVAILABLE", "details": []}, "recovered": False}
    orch = runtime.orchestrator
    view = orch.ui_snapshot(int(now * 1000))
    scanner = orch.scanner_view(int(now * 1000))
    configured = bool(getattr(runtime, "configured", True))
    cfg = orch.settings
    return {**base, "state": view["state"], "reason": view.get("reason", ""), "configured": configured, "config": cfg.as_dict(), "preview": preview(cfg),
            "positions": view["positions"], "scanner": scanner, "slots": view["slots"], "effective_capacity": view["effective_capacity"],
            "max_positions": cfg.max_positions, "snapshot_ok": view["snapshot_ok"], "recovered": bool(orch.recovered),
            "today": _today_totals(orch.store.results(orch.account_key), now), "recent_results": view["recent_results"],
            "why": why_no_position(view, scanner, daily_loss), "has_held": bool(orch.engines),
            "scanned_count": _scanned_count(orch),
            "margin_used_usdt": round(float(orch.store.held_margin(orch.account_key)), 6),
            "available_margin_usdt": round(max(0.0, min(float(cfg.total_margin_cap), HARD_CAP_USDT) - float(orch.store.held_margin(orch.account_key))), 6)}
