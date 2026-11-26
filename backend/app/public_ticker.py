"""Giriş/kayıt ekranındaki akan fiyat şeridi için herkese açık, önbellekli fiyat okuması.

Tarayıcı Binance'e doğrudan ulaşamadığında (operatör engeli, uygulama içi tarayıcı) şerit boş kalmasın diye
sunucu üzerinden aynı veriyi verir. Yalnız okuma; semboller sıkı doğrulanır, sonuç kısa süre önbelleğe alınır.
"""
from __future__ import annotations

import json
import re
import time

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
BINANCE_SPOT = "https://api.binance.com"
SYMBOL = re.compile(r"[A-Z0-9]{5,12}")
MAX_SYMBOLS = 12
CACHE_SECONDS = 8
_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}


@router.get("/api/public/ticker")
async def public_ticker(symbols: str = Query(..., max_length=200)) -> list[dict[str, str]]:
    names = sorted({item.strip().upper() for item in symbols.split(",") if item.strip()})
    if not names or len(names) > MAX_SYMBOLS or not all(SYMBOL.fullmatch(item) for item in names):
        raise HTTPException(status_code=400, detail="Geçersiz sembol listesi.")
    key = ",".join(names)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=3.0)) as client:
            response = await client.get(f"{BINANCE_SPOT}/api/v3/ticker/24hr", params={"symbols": json.dumps(names, separators=(",", ":"))})
        response.raise_for_status()
        rows = [{"symbol": str(item["symbol"]), "lastPrice": str(item["lastPrice"]), "openPrice": str(item["openPrice"])} for item in response.json()]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        if hit:
            return hit[1]
        raise HTTPException(status_code=502, detail="Piyasa verisi şu an alınamıyor.")
    if len(_cache) > 32:
        _cache.clear()
    _cache[key] = (now, rows)
    return rows
