"""Hesap seviyesinde ortak risk ve coin rezervasyonu — manuel, otomatik ve Grid girişleri aynı defteri kullanır.

Sorun: Grid başlatma, plan hesaplandıktan sonra durum kilidini bırakır ve emirler `runner.start` içinde gider. Bu aralıkta
(a) ikinci bir Grid ya da manuel/otomatik giriş aynı günlük zarar bütçesini ikinci kez kullanabilir, (b) aynı coine ikinci bir
giriş açılabilir. Rezervasyon, "karar anı" ile "emir/defter kaydı" arasındaki boşluğu kapatır: kontrol + kayıt tek adımda
(await yok, kilitli) yapılır, iş bitince serbest bırakılır.

Defter durum sözlüğünde geçici alandadır (`_reservations`); kalıcı kayda yazılmaz. Çok üyeli modda her üyenin durumu ayrıdır.
Tek kullanıcı, tek giriş: davranış aynıdır (başkasının rezervasyonu yoksa bütçe kontrolü yapılmaz).
"""
from __future__ import annotations

import contextlib
import threading
import time
import uuid
from typing import Any, Iterator

KEY = "_reservations"
STALE_SECONDS = 900.0     # unutulmuş rezervasyon (ör. kesilen istek) bu süreden sonra düşer; coin sonsuza dek kilitlenmez
_LOCK = threading.Lock()


class ReservationConflict(RuntimeError):
    """Aynı coin başka bir giriş için ayrılmış ya da ortak risk bütçesi yetmiyor."""

    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind          # "symbol" | "risk"


def _book(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    book = state.get(KEY)
    if not isinstance(book, dict):
        book = {}
        state[KEY] = book
    cutoff = time.monotonic() - STALE_SECONDS
    for token in [t for t, r in book.items() if r["at"] < cutoff]:
        book.pop(token, None)
    return book


def others_worst_loss(state: dict[str, Any], exclude: str | None = None) -> float:
    with _LOCK:
        return round(sum(r["worst_loss"] for t, r in _book(state).items() if t != exclude), 8)


def snapshot(state: dict[str, Any]) -> list[dict[str, Any]]:
    with _LOCK:
        return [{"token": t, **{k: v for k, v in r.items() if k != "at"}} for t, r in _book(state).items()]


def reserve(state: dict[str, Any], kind: str, symbol: str, worst_loss: float = 0.0, *, budget_left: float | None = None,
            committed: float = 0.0) -> str:
    """Coini ve (varsa) en kötü zararı ayırır. budget_left: bugün kalan günlük zarar bütçesi; committed: çalışan Grid'lerin zaten
    ayırdığı en kötü zarar. Başkasının ayırdığı (eşzamanlı ya da Grid) risk varsa bütçe kontrolü yapılır; yoksa tek işlemin mevcut
    kapıları aynen geçerlidir."""
    symbol = str(symbol or "").upper()
    worst = max(0.0, float(worst_loss or 0.0))
    with _LOCK:
        book = _book(state)
        for other in book.values():
            if symbol and other["symbol"] == symbol:
                raise ReservationConflict(f"{symbol} için aynı anda başka bir {other['kind']} girişi hazırlanıyor; çift giriş engellendi.",
                                          kind="symbol")
        others = sum(r["worst_loss"] for r in book.values()) + max(0.0, float(committed or 0.0))
        if budget_left is not None and others > 0 and worst > float(budget_left) - others + 1e-9:
            raise ReservationConflict(
                f"Ortak günlük risk bütçesi yetmiyor: bu giriş {worst:.2f} USDT, kalan {max(float(budget_left) - others, 0):.2f} USDT "
                f"(eşzamanlı girişler {others:.2f} USDT ayırdı).", kind="risk")
        token = uuid.uuid4().hex[:12]
        book[token] = {"kind": kind, "symbol": symbol, "worst_loss": worst, "at": time.monotonic()}
        return token


def update(state: dict[str, Any], token: str, worst_loss: float) -> None:
    with _LOCK:
        record = _book(state).get(token)
        if record is not None:
            record["worst_loss"] = max(0.0, float(worst_loss or 0.0))


def release(state: dict[str, Any], token: str | None) -> None:
    if token:
        with _LOCK:
            _book(state).pop(token, None)


@contextlib.contextmanager
def hold(state: dict[str, Any], kind: str, symbol: str, worst_loss: float = 0.0, *, budget_left: float | None = None) -> Iterator[str]:
    token = reserve(state, kind, symbol, worst_loss, budget_left=budget_left)
    try:
        yield token
    finally:
        release(state, token)
