"""Borsa fiyat/miktar kurallarını exchangeInfo yanıtından çıkarır (saf fonksiyon; ağ yok)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


class GridFilterError(ValueError):
    pass


def _dec(value: Any, name: str) -> Decimal:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise GridFilterError(f"{name} okunamadı") from None
    if not out.is_finite() or out < 0:
        raise GridFilterError(f"{name} geçersiz")
    return out


def extract_symbol_filters(exchange_info: Any, symbol: str) -> dict[str, Any]:
    """tickSize, stepSize, minQty, minNotional. Eksik/bozuk veri → GridFilterError (sessizce varsayılan DEĞER UYDURMAZ)."""
    if not isinstance(exchange_info, dict) or not isinstance(exchange_info.get("symbols"), list):
        raise GridFilterError("exchangeInfo yanıtı geçersiz")
    symbol = str(symbol or "").upper()
    row = next((s for s in exchange_info["symbols"] if isinstance(s, dict) and str(s.get("symbol", "")).upper() == symbol), None)
    if row is None:
        raise GridFilterError(f"{symbol} bulunamadı")
    if row.get("status") != "TRADING":
        raise GridFilterError(f"{symbol} şu an işlem görmüyor")
    if row.get("contractType") != "PERPETUAL" or row.get("quoteAsset") != "USDT":
        raise GridFilterError(f"{symbol} USDT perpetual değil")
    filters = {f.get("filterType"): f for f in row.get("filters", []) if isinstance(f, dict)}
    for needed in ("PRICE_FILTER", "LOT_SIZE"):
        if needed not in filters:
            raise GridFilterError(f"{needed} bulunamadı")
    notional = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL")
    if notional is None:
        raise GridFilterError("en düşük emir değeri (MIN_NOTIONAL) bulunamadı")
    tick = _dec(filters["PRICE_FILTER"].get("tickSize"), "tickSize")
    step = _dec(filters["LOT_SIZE"].get("stepSize"), "stepSize")
    if tick <= 0 or step <= 0:
        raise GridFilterError("tickSize/stepSize sıfır olamaz")
    return {
        "symbol": symbol, "tickSize": float(tick), "stepSize": float(step),
        "minQty": float(_dec(filters["LOT_SIZE"].get("minQty", "0"), "minQty")),
        "minNotional": float(_dec(notional.get("notional", notional.get("minNotional")), "minNotional")),
    }
