"""Asistan için dar, SALT-OKUNUR Demo veri arayüzü (incelenmiş sınır).

Asistan modülleri `binance_demo` / `v21_demo` alanına doğrudan bağlanamaz (bkz. tests/test_assistant_contracts.py).
Gerekli okumalar yalnızca bu modüldeki üç fonksiyonla yapılır. Bu modül emir, iptal, kapatma, ARM veya consent
fonksiyonlarına HİÇ erişmez; bu, aynı test dosyasında AST ile denetlenir. Kimlik/abonelik/OWNER kararları çağıran araçtadır
(assistant_tools); burada yalnızca isteğin kendi üye oturumuna bağlı veri okunur (başka kullanıcının verisi okunamaz)."""
from __future__ import annotations

import asyncio
import math
from typing import Any

from fastapi import Request

from . import binance_demo, v21_demo

# Projenin kendi sinyal tazelik sınırı (v21_demo.MAX_SIGNAL_AGE_SECONDS): yeni, keyfi bir eşik tanımlanmaz.
MAX_SIGNAL_AGE_SECONDS = v21_demo.MAX_SIGNAL_AGE_SECONDS
MIN_MOVER_VOLUME_USDT = 5_000_000


async def read_demo_positions(request: Request, timeout: float) -> list[dict[str, Any]]:
    """İsteği yapan üyenin KENDİ Demo hesabındaki açık pozisyonlar (client_for üye oturumuna bağlıdır). Seviye alanı yok."""
    snapshot = await asyncio.wait_for(binance_demo.account_snapshot(binance_demo.client_for(request)), timeout=timeout)
    return [
        {key: row.get(key) for key in ("symbol", "direction", "quantity", "unrealized_pnl", "leverage")}
        for row in snapshot.get("positions", []) if row.get("quantity")
    ]


def read_scanner_candidates(request: Request) -> dict[str, Any]:
    """Üyenin kendi tarayıcı durumundan, projenin mevcut uygunluk kuralını (candidate_is_tradeable) geçen adaylar.
    Seviye alanları (entry/stop/tp) döndürülmez. Yön anahtarları (allow_long/allow_short) kalite kuralı değildir; açık tutulur."""
    member = getattr(getattr(request, "state", None), "member", None) or {}
    if not str(member.get("id") or "").strip():
        # Üye kimliği yoksa state_for ortak (global) duruma düşebilir: başka verisi okunmasın diye reddedilir.
        raise PermissionError("member session required")
    state = v21_demo.state_for(request)
    scanner = state.get("scanner") or {}
    settings = {**(state.get("settings") or {}), "allow_long": True, "allow_short": True}
    candidates = [row for row in scanner.get("all_candidates") or [] if isinstance(row, dict) and row.get("symbol")]
    eligible = [
        {"symbol": row.get("symbol"), "direction": str(row.get("direction") or "").upper(),
         "opportunity_score": row.get("opportunity_score"), "analysis_score": row.get("analysis_score"),
         "confidence_value": row.get("confidence_value"), "confidence_label": row.get("confidence")}
        for row in candidates if v21_demo.candidate_is_tradeable(row, settings, None)
    ]
    # scored: taramadaki TÜM adayların yalnız skorları (kurala uymayanlar dahil; coin karşılaştırması için). Seviye alanı yok.
    scored = [{"symbol": row.get("symbol"), "direction": str(row.get("direction") or "").upper(),
               "opportunity_score": row.get("opportunity_score"), "analysis_score": row.get("analysis_score"),
               "confidence_value": row.get("confidence_value"), "confidence_label": row.get("confidence")} for row in candidates]
    return {"last_scan_at": scanner.get("last_scan_at"), "timeframe": scanner.get("last_scan_timeframe"),
            "coins_scanned": scanner.get("coins_scanned"), "candidate_count": len(candidates), "eligible": eligible, "scored": scored}


async def read_market_movers(request: Request, timeout: float) -> list[dict[str, Any]]:
    """24 saatlik değişim satırları (canlı genel borsa verisi; her çağrıda taze). Yalnızca sonlu sayılar ve yeterli hacim."""
    rows = await asyncio.wait_for(v21_demo.auto_trade_market_universe(request.app), timeout=timeout)
    return [
        {"symbol": row["symbol"], "change": float(row["change"]), "price": float(row["price"])}
        for row in rows
        if isinstance(row, dict) and all(isinstance(row.get(key), (int, float)) and math.isfinite(row[key]) for key in ("change", "price", "volume"))
        and row["volume"] >= MIN_MOVER_VOLUME_USDT
    ]
