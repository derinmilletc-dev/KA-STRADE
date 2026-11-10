"""KAIS FARMER marjin/slot koruması ve giriş denemesi (C6).

- Toplam marjin tavanını KULLANICI belirler (açarken zorunlu); kodda üst sınır `HARD_CAP_USDT` = en çok 5 pozisyon × 100 USDT marjin.
- Etkin kapasite = min(max_positions, floor(toplam_marjin_tavanı / pozisyon_marjini)). `max_positions` "N işlem aç" demek DEĞİLDİR.
- Slot = HELD + OPEN + CLOSING (flat doğrulanana kadar CLOSING slotu ve marjini tutar).
- Marjin rezervasyonu + mum giriş denemesi `FarmerStore.claim_entry` içinde TEK SQLite işlemidir.
- Hesap-geneli günlük zarar Farmer'a özgü hesaplanmaz; mevcut sistemden gelen karar `DailyLossPort` ile alınır.
- PnL doğrulaması slotu bloke etmez: yalnız `release_when_flat` flat doğrulamasına bağlıdır."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .confirm import ConfirmParams, EntryDecision, FarmerCandidate, RiskView, decide_entry
from .store import FarmerStore

HARD_CAP_USDT = 500.0
DAILY_LOSS_BLOCK = "DAILY_LOSS_LIMIT"


class DailyLossPort(Protocol):
    """Mevcut hesap-geneli günlük zarar kaydı (daily_ledger) üzerinden: engel varsa neden döner, yoksa boş.
    Okunamıyorsa fail-closed (engel) döndürmek adaptörün sorumluluğudur."""
    def __call__(self) -> tuple[str, ...]: ...


def effective_capacity(max_positions: int, total_margin_cap: float, position_margin: float) -> int:
    if position_margin <= 0 or total_margin_cap <= 0 or max_positions <= 0:
        return 0
    cap = min(float(total_margin_cap), HARD_CAP_USDT)
    return max(0, min(int(max_positions), math.floor(cap / position_margin + 1e-9)))


@dataclass(frozen=True)
class SlotView:
    capacity: int
    held: int
    open: int
    closing: int

    @property
    def used(self) -> int:
        return self.held + self.open + self.closing

    @property
    def free(self) -> int:
        return max(0, self.capacity - self.used)

    def as_dict(self) -> dict[str, int]:
        return {"capacity": self.capacity, "held": self.held, "open": self.open, "closing": self.closing, "free": self.free}


class MarginGuard:
    def __init__(self, store: FarmerStore, account_key: str, *, max_positions: int, total_margin_cap: float, position_margin: float,
                 daily_loss: DailyLossPort | None = None) -> None:
        self.store, self.account_key = store, account_key
        self.max_positions, self.position_margin = int(max_positions), float(position_margin)
        self.total_margin_cap = min(float(total_margin_cap), HARD_CAP_USDT)
        self.daily_loss = daily_loss

    @property
    def capacity(self) -> int:
        return effective_capacity(self.max_positions, self.total_margin_cap, self.position_margin)

    def slots(self) -> SlotView:
        c = self.store.slot_counts(self.account_key)
        return SlotView(self.capacity, c["HELD"], c["OPEN"], c["CLOSING"])

    def attempt_entry(self, candidate: FarmerCandidate, params: ConfirmParams, risk: RiskView, *, grid_id: str) -> EntryDecision:
        """Onay → kapılar → atomik (slot+marjin+mum kilidi). Yalnız `CONFIRMED` ise emir gönderilebilir.
        Günlük zarar engeli SAFETY_GATE olarak taşınır (kilit alınmaz)."""
        if abs(params.margin_usdt - self.position_margin) > 1e-9:
            raise ValueError("ConfirmParams.margin_usdt koruma marjininden farklı")
        blocked = tuple(self.daily_loss()) if self.daily_loss is not None else ()
        if blocked:
            risk = RiskView(risk.occupied, risk.arm_blocked, tuple(risk.safety_blocked) + blocked)
        return decide_entry(self.store, self.account_key, candidate, params, risk, grid_id=grid_id,
                            cap_usdt=self.total_margin_cap, max_slots=self.capacity)

    # ---------- yaşam döngüsü ----------
    def entry_filled(self, grid_id: str) -> None:
        self.store.mark_hold_open(grid_id)

    def entry_failed(self, grid_id: str, outcome: str = "REJECTED_TEMP") -> bool:
        """Emir reddi/başarısızlığı: marjin bırakılır, AMA mum satırı KALIR → aynı sembol+mumda yeniden deneme yok."""
        self.store.set_attempt_outcome(grid_id, outcome)
        return self.store.release_hold(grid_id)

    def closing_started(self, grid_id: str) -> bool:
        return self.store.mark_hold_closing(grid_id)

    def release_when_flat(self, grid_id: str, flat_verified: bool) -> bool:
        """Flat doğrulanmadan marjin/slot bırakılmaz. PnL durumu BURADA aranmaz."""
        return bool(flat_verified) and self.store.release_hold(grid_id)
