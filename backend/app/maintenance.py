"""Centralized maintenance-mode state used to gate brand-new trade entries.

Scope is intentionally narrow: this module only decides whether a *new*
position/automation entry may be created. It must never be consulted by, or
have any effect on, existing-position protection - monitoring, Stop-Loss,
Take-Profit, Risk Guard, trailing/risk-reduction, closes, or exchange
reconciliation all keep running regardless of maintenance mode.

State lives in ``app.state.maintenance`` (a plain dict, same pattern as the
other in-memory runtime state such as ``app.state.paper``) - no new database
table, cache layer, or background loop is introduced.
"""

from __future__ import annotations

import json
import os
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException

MaintenanceMode = Literal["NORMAL", "MAINTENANCE", "EMERGENCY"]
VALID_MODES: frozenset[str] = frozenset({"NORMAL", "MAINTENANCE", "EMERGENCY"})


def default_maintenance_state() -> dict[str, Any]:
    """Initial value for ``app.state.maintenance``. Default is always NORMAL."""
    return {"mode": "NORMAL", "reason": "", "updated_at": None, "updated_by": None,
            "scheduled_at": None, "scheduled_by": None, "scheduled_set_at": None}


# Zamanlı bakım: admin bir başlangıç anı seçer; o an gelince mod kendiliğinden MAINTENANCE olur.
# Arka plan döngüsü yok: süre dolduğunda ilk okuma modu uygular (apply_due_schedule).
SCHEDULE_MIN_SECONDS = 30
SCHEDULE_MAX_SECONDS = 30 * 24 * 3600


def get_maintenance_mode(state: dict[str, Any] | None) -> str:
    """Read the current mode. Fails closed to ``UNKNOWN`` if state is missing
    or holds an unrecognized value - callers must treat UNKNOWN like MAINTENANCE
    for new entries while never touching existing-position protection."""
    if not isinstance(state, dict):
        return "UNKNOWN"
    apply_due_schedule(state)
    mode = state.get("mode")
    return mode if mode in VALID_MODES else "UNKNOWN"


# Yönetici (OWNER) bakım modundan etkilenmez: kendi yeni girişleri ve otomasyonları açık kalır.
# Üyeler için kural aynen geçerli. İstek geldiğinde ``mark_staff`` ile işaretlenir; arka plan
# döngüleri (istek bağlamı olmayan) kullanıcı kimliğiyle bu listeye bakar.
STAFF_REQUEST: ContextVar[bool] = ContextVar("maintenance_staff_request", default=False)
STAFF_USER_IDS: set[str] = set()


def mark_staff(user_id: str | None) -> None:
    """Call from the request middleware when the caller is the admin/owner."""
    STAFF_REQUEST.set(True)
    if user_id:
        STAFF_USER_IDS.add(str(user_id))


def clear_staff() -> None:
    STAFF_REQUEST.set(False)


def _is_staff_context() -> bool:
    if STAFF_REQUEST.get():
        return True
    try:
        from .live_registry import CURRENT_USER
        current = CURRENT_USER.get()
    except Exception:
        return False
    return bool(current) and str(current) in STAFF_USER_IDS


def new_entry_blocked(state: dict[str, Any] | None) -> bool:
    """True whenever a brand-new position/automation entry must not be created.

    Never true for the admin/owner: maintenance only restricts regular members."""
    if _is_staff_context():
        return False
    return get_maintenance_mode(state) != "NORMAL"


def guard_new_entry(state: dict[str, Any] | None) -> None:
    """Raise 503 MAINTENANCE_MODE if new entries are currently blocked. No-op otherwise.

    Call this only at the start of functions that OPEN a brand-new position or
    START a brand-new automation session - never inside position monitoring,
    protection (SL/TP/Risk Guard), or close/cancel code paths.
    """
    if new_entry_blocked(state):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MAINTENANCE_MODE",
                "message": (
                    "Yeni işlem girişleri bakım modu nedeniyle geçici olarak durduruldu. "
                    "Açık pozisyonlar ve risk koruması etkilenmez."
                ),
                "mode": get_maintenance_mode(state),
            },
        )


MAINTENANCE_SKIP_REASON = "maintenance"


def maintenance_blocks_automation(state: dict[str, Any] | None) -> bool:
    """Pre-check for automation loops: skip a new-entry attempt up front.

    Only an explicitly present state is consulted here, so the pre-check never
    turns a missing attribute into a silent skip. ``guard_new_entry`` remains the
    authoritative, fail-closed gate inside the order functions."""
    return state is not None and new_entry_blocked(state)


def is_maintenance_rejection(exc: BaseException) -> bool:
    """True only for the exact rejection raised by ``guard_new_entry``.

    Automation loops use this to treat a maintenance-blocked new entry as an
    expected skip. Any other HTTP 503, or any other exception, returns False and
    must keep following the caller's existing failure handling."""
    if not isinstance(exc, HTTPException) or exc.status_code != 503:
        return False
    detail = exc.detail
    return isinstance(detail, dict) and detail.get("code") == "MAINTENANCE_MODE"


def set_maintenance_mode(
    state: dict[str, Any],
    mode: str,
    *,
    reason: str = "",
    updated_by: str | None = None,
) -> dict[str, Any]:
    """Admin-only mutation of the mode flag. Never starts/stops automation or
    touches open positions - it only changes whether new entries are allowed."""
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid maintenance mode: {mode!r}")
    state["mode"] = mode
    state["reason"] = reason
    state["updated_at"] = time.time()
    state["updated_by"] = updated_by
    # Elle verilen karar zamanlanmış planın önüne geçer.
    clear_schedule(state)
    return dict(state)


def _valid_epoch(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number == number and 0 < number < 4_102_444_800 else None


def apply_due_schedule(state: dict[str, Any] | None, now: float | None = None) -> bool:
    """Süresi dolmuş bir plan varsa modu MAINTENANCE yapar ve planı temizler.

    Yalnızca NORMAL moddayken uygular: zaten MAINTENANCE/EMERGENCY ise (admin elle
    geçtiyse) mevcut mod korunur, EMERGENCY asla düşürülmez."""
    if not isinstance(state, dict):
        return False
    due = _valid_epoch(state.get("scheduled_at"))
    if due is None:
        if state.get("scheduled_at") is not None:
            clear_schedule(state)  # bozuk değer: sessizce temizle, moda dokunma
        return False
    current = time.time() if now is None else now
    if current < due:
        return False
    if state.get("mode") == "NORMAL":
        state["mode"] = "MAINTENANCE"
        state["reason"] = str(state.get("reason") or "") or "scheduled"
        state["updated_at"] = due
        state["updated_by"] = "schedule"
    clear_schedule(state)
    return True


def clear_schedule(state: dict[str, Any]) -> None:
    state["scheduled_at"] = None
    state["scheduled_by"] = None
    state["scheduled_set_at"] = None


def schedule_maintenance(
    state: dict[str, Any],
    *,
    starts_at: float,
    updated_by: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Bakımı ileri bir ana planlar. Yalnızca NORMAL moddayken izin verilir."""
    current = time.time() if now is None else now
    moment = _valid_epoch(starts_at)
    if moment is None:
        raise ValueError("Geçersiz başlangıç zamanı.")
    if state.get("mode") != "NORMAL":
        raise ValueError("Sistem zaten bakımda veya acil durumda; önce Normal moda dönün.")
    delta = moment - current
    if delta < SCHEDULE_MIN_SECONDS:
        raise ValueError("Başlangıç zamanı en az 30 saniye sonra olmalı.")
    if delta > SCHEDULE_MAX_SECONDS:
        raise ValueError("Bakım en fazla 30 gün sonrasına planlanabilir.")
    state["scheduled_at"] = moment
    state["scheduled_by"] = updated_by
    state["scheduled_set_at"] = current
    return dict(state)


def public_maintenance_view(state: dict[str, Any] | None) -> dict[str, Any]:
    """Giriş ekranı ve site için herkese açık, sır içermeyen görünüm."""
    mode = get_maintenance_mode(state)
    scheduled = _valid_epoch(state.get("scheduled_at")) if isinstance(state, dict) and mode == "NORMAL" else None
    return {"mode": mode if mode in VALID_MODES else "MAINTENANCE", "scheduled_at": scheduled, "now": time.time()}


def admin_maintenance_view(state: dict[str, Any]) -> dict[str, Any]:
    get_maintenance_mode(state)
    return {**state, "now": time.time()}


def save_state(state: dict[str, Any], path: Path) -> None:
    """Planı ve modu yeniden başlatmalara karşı dosyaya yazar (atomik, hata yutmaz)."""
    payload = {key: state.get(key) for key in ("mode", "reason", "updated_at", "updated_by", "scheduled_at", "scheduled_by", "scheduled_set_at")}
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temp, path)


def load_state(path: Path) -> dict[str, Any]:
    """Dosyadan okur; dosya yok/bozuksa güvenli varsayılan (NORMAL) döner."""
    state = default_maintenance_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return state
    if not isinstance(raw, dict):
        return state
    if raw.get("mode") in VALID_MODES:
        state["mode"] = raw["mode"]
    for key in ("reason", "updated_by", "scheduled_by"):
        if isinstance(raw.get(key), str):
            state[key] = raw[key][:200]
    for key in ("updated_at", "scheduled_at", "scheduled_set_at"):
        state[key] = _valid_epoch(raw.get(key))
    apply_due_schedule(state)
    return state
