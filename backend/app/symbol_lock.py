"""Sembol bilinçli durum kilidi.

Eskiden tüm canlı/demo işlemleri tek bir asyncio.Lock'u paylaşıyordu: bir coine emir açılırken (borsa çağrıları
dahil yarım dakikadan uzun sürebilir) başka bir coini kapatmak, stop taşımak ya da hedef değiştirmek de beklerdi.

Bu kilit asyncio.Lock gibi davranır (acquire/release/locked/async with), ek olarak iki şey bilir:

* ``declare_entry(sembol)``: yeni giriş emri açan görev kendini bu sembolün girişi olarak bildirir. Bildirilmiş
  giriş yine tek başına çalışır (başka giriş, uzlaştırma/koruma döngüsü, acil durdurma onunla yan yana çalışmaz);
  yalnız FARKLI semboldeki pozisyon yönetimi (``symbol(sembol)``) onunla birlikte ilerleyebilir.
* ``symbol(sembol)``: kapat / kısmi kapat / stop-hedef / iz süren gibi tek pozisyonu yöneten işlemler. Aynı
  sembolde yalnız biri çalışır; bildirilmemiş (genel) kilit sahipleri ve aynı sembolün girişi bunu bekler.

Genel (bildirimsiz) ``acquire`` eskisi gibi her şeyi dışlar: bu yüzden döngüler ve acil durdurma davranışı değişmez.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator, Iterator

_DECLARED: ContextVar[tuple[str, Any] | None] = ContextVar("symbol_lock_declared_entry", default=None)


@contextmanager
def declare_entry(symbol: str | None) -> Iterator[None]:
    """Bu görevin, sonraki ``async with lock`` ile alacağı kilidin ``symbol`` girişi olduğunu bildirir."""
    value = (str(symbol).upper(), asyncio.current_task()) if symbol else None
    token = _DECLARED.set(value)
    try:
        yield
    finally:
        _DECLARED.reset(token)


def _declared_symbol() -> str | None:
    value = _DECLARED.get()
    if value is None or value[1] is not asyncio.current_task():
        return None  # bildirim başka göreve (ör. create_task kopyası) geçmez
    return value[0]


class SymbolAwareLock(asyncio.Lock):
    def __init__(self) -> None:
        super().__init__()
        self._exclusive = False
        self._exclusive_symbol: str | None = None
        self._symbols: set[str] = set()
        self._wakeups: list[asyncio.Future[None]] = []

    def _wake(self) -> None:
        for future in self._wakeups:
            if not future.done():
                future.set_result(None)

    async def _until(self, ready: Any) -> None:
        while not ready():
            future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._wakeups.append(future)
            try:
                await future
            finally:
                if future in self._wakeups:
                    self._wakeups.remove(future)

    def locked(self) -> bool:
        return self._exclusive or bool(self._symbols)

    async def acquire(self) -> bool:
        declared = _declared_symbol()
        await self._until(lambda: not self._exclusive and (declared not in self._symbols if declared else not self._symbols))
        self._exclusive = True
        self._exclusive_symbol = declared
        return True

    def release(self) -> None:
        if not self._exclusive:
            raise RuntimeError("Lock is not acquired.")
        self._exclusive = False
        self._exclusive_symbol = None
        self._wake()

    async def __aenter__(self) -> None:
        await self.acquire()

    async def __aexit__(self, *_exc: object) -> None:
        self.release()

    @asynccontextmanager
    async def symbol(self, symbol: str) -> AsyncIterator[None]:
        name = str(symbol).upper()
        await self._until(
            lambda: name not in self._symbols
            and (not self._exclusive or (self._exclusive_symbol is not None and self._exclusive_symbol != name))
        )
        self._symbols.add(name)
        try:
            yield
        finally:
            self._symbols.discard(name)
            self._wake()


def position_guard(state: dict[str, Any], symbol: str | None) -> Any:
    """Tek pozisyonu yöneten işlem için kilit: sembol biliniyorsa yalnız o sembolü dışlar, değilse eskisi gibi tümünü."""
    lock = state["lock"]
    if symbol and isinstance(lock, SymbolAwareLock):
        return lock.symbol(symbol)
    return lock


@asynccontextmanager
async def entry_guard(state: dict[str, Any], symbol: str | None) -> AsyncIterator[None]:
    """Giriş emri için kilit: kendini ``symbol`` girişi olarak bildirir ve kilidi alır (kilit sembol bilinçli değilse düz kilit)."""
    with declare_entry(symbol):
        async with state["lock"]:
            yield
