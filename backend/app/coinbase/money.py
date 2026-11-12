"""Para/miktar hesabı: yalnızca Decimal (float yok). Coinbase metin olarak verdiği sayıları buradan geçirir."""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from .errors import CoinbaseError

ROUND_UP = ROUND_CEILING
ROUND_DOWN = ROUND_FLOOR
ROUND_NEAREST = ROUND_HALF_UP


def to_decimal(value: Any, what: str = "değer") -> Decimal:
    """Metni/sayıyı Decimal yap. Boş, okunamayan, NaN veya sonsuz ise açık hata (sessizce 0 yapılmaz)."""
    if isinstance(value, bool) or value is None:
        raise CoinbaseError("BAD_RESPONSE", f"Coinbase cevabı beklenen biçimde değil: {what} yok.")
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise CoinbaseError("BAD_RESPONSE", f"Coinbase cevabı beklenen biçimde değil: {what} sayı değil.") from None
    if not number.is_finite():
        raise CoinbaseError("BAD_RESPONSE", f"Coinbase cevabı beklenen biçimde değil: {what} geçersiz.")
    return number


def round_to_increment(value: Decimal, increment: Decimal, mode: str = ROUND_NEAREST) -> Decimal:
    if increment <= 0:
        raise ValueError("adım pozitif olmalı")
    return (value / increment).to_integral_value(rounding=mode) * increment


def is_multiple(value: Decimal, increment: Decimal) -> bool:
    if increment <= 0:
        return False
    return (value % increment) == 0


def fmt(value: Decimal) -> str:
    """Coinbase'e gönderilecek düz metin (bilimsel gösterim yok): Decimal('1E+2') -> '100'."""
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text
