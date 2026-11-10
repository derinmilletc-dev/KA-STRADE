"""KAIS FARMER → paylaşımlı kline akışı sembol talebi (C4). SAF: ağ yok, akışa dokunmaz.

Akış tek ve paylaşımlıdır (`BinanceKlineStream.configure` sembol kümesini DEĞİŞTİRİR, en çok 100). Farmer talebi Original V2'nin
talebine EKLENİR: Original sembolleri önce ve değişmeden gelir; kalan yer Farmer'ındır. Sığmayan/geçersiz Farmer sembolleri sessizce
düşürülmez — `DemandResult.dropped` ile açıkça bildirilir ve Farmer bunları aday/giriş için kullanamaz.
Farmer talebi boşken `with_farmer_demand` AYNI liste nesnesini döndürür (Original V2 davranışı bit bit aynı)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

STREAM_SYMBOL_LIMIT = 100                 # BinanceKlineStream.configure sınırı
STATE_ATTR = "farmer_symbol_demand"       # application.state üzerinde Farmer talebi (boş/yok = Farmer kapalı)
INVALID, NO_ROOM = "INVALID_SYMBOL", "STREAM_LIMIT"


def valid_symbol(symbol: Any) -> bool:
    """BinanceKlineStream.configure ile aynı kural."""
    return isinstance(symbol, str) and symbol.isascii() and symbol.isalnum() and symbol.isupper() and symbol.endswith("USDT")


@dataclass(frozen=True)
class DemandResult:
    symbols: list[str]                 # akışa verilecek birleşik liste
    farmer_included: list[str]         # Farmer'dan akışa giren (zaten Original'de olanlar dahil)
    dropped: dict[str, str]            # sembol -> neden (INVALID_SYMBOL | STREAM_LIMIT)


def merge_demand(base: Sequence[str], farmer: Sequence[str], *, limit: int = STREAM_SYMBOL_LIMIT) -> DemandResult:
    union = list(base)
    seen = set(union)
    included: list[str] = []
    dropped: dict[str, str] = {}
    for symbol in farmer:
        if not valid_symbol(symbol):
            dropped[str(symbol)] = INVALID
        elif symbol in seen:
            if symbol not in included:
                included.append(symbol)
        elif len(union) >= limit:
            dropped[symbol] = NO_ROOM
        else:
            union.append(symbol)
            seen.add(symbol)
            included.append(symbol)
    return DemandResult(union, included, dropped)


def farmer_demand(application: Any) -> list[str]:
    state = getattr(application, "state", None)
    demand = getattr(state, STATE_ATTR, None)
    return list(demand) if isinstance(demand, (list, tuple)) and demand else []


def with_farmer_demand(application: Any, symbols: list[str]) -> list[str]:
    """Original V2 sembollerine Farmer talebini ekler. Farmer talebi yoksa GİRDİ LİSTESİ aynen döner."""
    demand = farmer_demand(application)
    if not demand:
        return symbols
    return merge_demand(symbols, demand).symbols
