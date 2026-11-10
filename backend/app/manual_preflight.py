"""Manuel emir ön kontrolü: gerçek emirdeki hesap kapılarını EMİR GÖNDERMEDEN, okuyarak değerlendirir.

Neden: /order/test (dry-run) yalnız emir şemasını ve boyutlandırmayı doğrular; pozisyon sınırı, toplam maruziyet, aynı yön
sınırı, günlük risk bütçesi, teminat ve coin doluluğu gibi hesap kapıları yalnız gerçek gönderimde çalışıyordu. Bu yüzden
hata sihirbazın en sonunda çıkıyordu. Bu modül aynı kapıları (execution_core.evaluate_entry_gates ile aynı kural) önceden gösterir.

Saf hesap: borsa, state ve rezervasyon yazımı yok. Her başarısız madde için (varsa) bir "çözüm" önerisi döner; öneriler yalnız
ekranda gösterilir, uygulanması kullanıcının dokunuşuyla olur. Gerçek gönderimdeki kapılar değişmeden çalışmaya devam eder."""
from __future__ import annotations

import math
from typing import Any

from .execution_core import evaluate_entry_gates, position_notional_usdt

MIN_MARGIN_USDT = 5.0
# Önerilen miktarı kapı sınırının biraz altında tutar (yuvarlama/komisyon payı).
FIT_SAFETY = 0.98

FRIENDLY = {
    "positions": "Açık pozisyon sınırı dolu.",
    "duplicate": "Bu coinde zaten açık pozisyon ya da emir var.",
    "active_plan": "Bu coin ve yönde zaten aktif bir plan var.",
    "exposure": "Toplam işlem büyüklüğü sınırı aşılıyor.",
    "spread": "Alış-satış farkı şu an çok açık.",
    "daily_loss": "Günlük kayıp limiti doldu.",
    "open_loss": "Açık pozisyonların zararı limite ulaştı.",
    "pnl_verified": "Bazı kapanışların kâr/zararı henüz doğrulanmadı.",
    "same_direction_positions": "Aynı yönde açık işlem sınırı dolu.",
    "direction_exposure": "Bu yöndeki toplam büyüklük sınırı aşılıyor.",
    "long": "LONG işlemlere izin kapalı.",
    "short": "SHORT işlemlere izin kapalı.",
    "one_way": "Hesap Tek Yön (One-way) modunda olmalı.",
}


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def fit_margin(margin: float, loss: float, allowed: float) -> float | None:
    """Kayıp marjinle doğru orantılıdır: yeni marjin = marjin x izin_verilen / kayıp (küçük pay bırakılarak, 0,1 adıma aşağı).
    Sonuç marjinden küçük ve en az 5 USDT değilse öneri yoktur (None)."""
    if not (margin > 0 and loss > 0 and allowed > 0):
        return None
    fitted = math.floor(margin * allowed / loss * FIT_SAFETY * 10) / 10
    return fitted if MIN_MARGIN_USDT <= fitted < margin else None


def _check(key: str, passed: bool, detail: str, fix: dict[str, Any] | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"key": key, "passed": bool(passed), "detail": detail}
    if not passed and fix:
        item["fix"] = fix
    return item


def build_report(
    *,
    symbol: str,
    direction: str,
    margin_usdt: float,
    leverage: int,
    estimated_loss_usdt: float | None,
    snapshot: dict[str, Any],
    policy: dict[str, Any],
    daily: dict[str, Any],
    spread_bps: float,
    active_plans: list[dict[str, Any]],
    readiness_gates: list[dict[str, Any]] | None,
    budget_left: float | None,
    committed: float,
    symbol_occupied: bool | None,
    symbol_occupancy_detail: str,
    grid_busy: bool,
    isolated_error: str | None,
) -> dict[str, Any]:
    """Tüm kapıları değerlendirir; {"ok", "checks", "first_blocker"} döner. Başarısız maddelerde çözüm önerisi bulunabilir."""
    notional = margin_usdt * leverage
    checks: list[dict[str, Any]] = []

    failed_readiness = [g for g in (readiness_gates or []) if isinstance(g, dict) and g.get("passed") is not True]
    if failed_readiness:
        text = "; ".join(str(g.get("detail") or g.get("label") or g.get("key") or "") for g in failed_readiness[:3]).strip("; ")
        checks.append(_check("readiness", False, text or "Canlı işlem kapıları tamamlanmadı.", {"type": "permission"}))

    checks.append(_check("one_way", not snapshot.get("hedge_mode"), "Hesap Tek Yön modunda." if not snapshot.get("hedge_mode")
                         else "Binance hesabı Hedge modunda; Tek Yön (One-way) moduna alınmalı."))
    checks.append(_check("multi_assets", not snapshot.get("multi_assets_mode"), "Multi-Assets modu kapalı." if not snapshot.get("multi_assets_mode")
                         else "Multi-Assets modu açık; kapatılmadan yeni işlem açılamaz."))
    if isolated_error:
        checks.append(_check("isolated", False, isolated_error))

    # Manuel gönderimle aynı politika/sinyal: günlük işlem ve ardışık kayıp SAYISI sınırı yok, güven ve tuzak puanı sabit.
    entry_policy = {**policy, "daily_trade_limit": 0, "consecutive_loss_limit": 0}
    guard = evaluate_entry_gates(
        symbol=symbol, signal={"direction": direction, "confidence": 100, "radar": {"trap_score": 0}},
        snapshot=snapshot, policy=entry_policy, daily=daily, spread_bps=spread_bps, armed=True, allowed_symbols=[],
        active_plans=active_plans, candidate_notional_usdt=notional, cycle_candidates=None, enforce_spread=False,
    )
    max_exposure = _num(entry_policy.get("max_total_exposure_usdt"))
    existing_exposure = 0.0
    for item in snapshot.get("positions") or []:
        try:
            existing_exposure += position_notional_usdt(item)
        except (ValueError, TypeError, AttributeError):
            existing_exposure = float("inf")
            break
    for gate in guard.get("gates", []):
        key = str(gate.get("key") or "")
        if key in {"arm", "symbol", "direction", "confidence", "trap", "one_way", "daily_trades", "consecutive_losses"}:
            continue  # manuel yolda geçerli değil / ayrıca gösteriliyor
        passed = gate.get("passed") is True
        fix: dict[str, Any] | None = None
        if key in {"positions", "same_direction_positions"}:
            fix = {"type": "positions"}
        elif key in {"duplicate", "active_plan"}:
            fix = {"type": "other_coin"}
        elif key in {"exposure", "direction_exposure"} and max_exposure is not None and leverage > 0:
            room = max_exposure - existing_exposure if math.isfinite(existing_exposure) else 0.0
            fitted = math.floor(room / leverage * FIT_SAFETY * 10) / 10 if room > 0 else 0.0
            if MIN_MARGIN_USDT <= fitted < margin_usdt:
                fix = {"type": "reduce_margin", "margin_usdt": fitted}
            else:
                fix = {"type": "positions"}
        checks.append(_check(f"gate_{key}", passed, FRIENDLY.get(key, str(gate.get("detail") or key)) if not passed else str(gate.get("detail") or ""), fix))

    available = _num(snapshot.get("available_balance"))
    if available is not None:
        enough = available >= margin_usdt
        fitted_margin = math.floor(available * FIT_SAFETY * 10) / 10
        checks.append(_check("collateral", enough,
                             "Kullanılabilir bakiye yeterli." if enough else f"Kullanılabilir bakiye {available:.2f} USDT; seçilen marjin {margin_usdt:.2f} USDT.",
                             {"type": "reduce_margin", "margin_usdt": fitted_margin} if MIN_MARGIN_USDT <= fitted_margin < margin_usdt else {"type": "deposit"}))

    if symbol_occupied is not None:
        checks.append(_check("symbol_occupancy", not symbol_occupied, "Bu coinde açık pozisyon/emir yok." if not symbol_occupied
                             else (symbol_occupancy_detail or "Bu coinde zaten açık pozisyon ya da emir var."), {"type": "other_coin"}))
    if grid_busy:
        checks.append(_check("grid_occupancy", False, "Bu coinde çalışan bir grid var; aynı coinde ayrıca işlem açılamaz.", {"type": "other_coin"}))

    max_loss = _num(entry_policy.get("max_loss_per_trade"))
    if estimated_loss_usdt is not None and max_loss is not None and max_loss > 0:
        within = estimated_loss_usdt <= max_loss + 1e-6
        fitted = fit_margin(margin_usdt, estimated_loss_usdt, max_loss)
        checks.append(_check("max_loss", within,
                             f"Stopta olası kayıp {estimated_loss_usdt:.2f} USDT, işlem başına limit {max_loss:.2f} USDT." if not within
                             else f"Stopta olası kayıp {estimated_loss_usdt:.2f} / {max_loss:.2f} USDT.",
                             {"type": "reduce_margin", "margin_usdt": fitted} if fitted else {"type": "widen_or_edit_limits"}))
    if estimated_loss_usdt is not None and budget_left is not None:
        left = budget_left - max(0.0, committed)
        ok = estimated_loss_usdt <= left + 1e-9
        fitted = fit_margin(margin_usdt, estimated_loss_usdt, left) if left > 0 else None
        checks.append(_check("risk_budget", ok,
                             f"Günlük risk bütçesi yetmiyor: bu işlem {estimated_loss_usdt:.2f} USDT, kalan {max(left, 0.0):.2f} USDT." if not ok
                             else f"Günlük risk bütçesi yeterli (kalan {left:.2f} USDT).",
                             {"type": "reduce_margin", "margin_usdt": fitted} if fitted else {"type": "positions"}))

    failed = [item for item in checks if not item["passed"]]
    return {"ok": not failed, "checks": checks, "first_blocker": failed[0] if failed else None}
