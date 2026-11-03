"""Açık pozisyon kartı için coinin güncel güven puanı (yalnız okuma).

Botun giriş kararında kullandığı stratejinin aynısı, her kapanmış 15 dakikalık mumda bir kez, açık her canlı pozisyon
(bot veya manuel plan) için hesaplanır ve plana `live_score` olarak yazılır; durum sayfası planları olduğu gibi gönderdiği
için kart bu alanı okur. Emir göndermez, hesap verisi istemez; yalnız herkese açık mum verisi okunur. Akıllı Kâr Koruma'dan
bağımsızdır: o kapalıyken de çalışır ve onun hiçbir kaydına dokunmaz. Dış (botun açmadığı) pozisyonda plan olmadığı için hesaplanmaz.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

KEY = "live_score"
RETRY_SECONDS = 60


async def tick(state: dict[str, Any], snapshot: dict[str, Any], client: Any,
               kline_fetch: Callable[[str, str, int], Awaitable[Any]] | None = None, *, now: float | None = None) -> bool:
    """Puanı güncellenen plan varsa True döner (çağıran durumu kaydeder). Hata vermez."""
    from . import original_profit_guard as guard
    from . import v25_execution as live
    now = time.time() if now is None else now
    at = guard.candle_open(now)
    positions: dict[str, dict[str, Any]] = {}
    for row in snapshot.get("positions", []) or []:
        item = guard.view_of(row)
        if item is not None:
            positions[item["symbol"]] = item
    computed: dict[str, tuple[dict[str, Any] | None, str]] = {}
    changed = False
    for plan in list((state.get("plans") or {}).values()):
        if not isinstance(plan, dict):
            continue
        try:
            if not live.live_plan_is_active(plan):
                continue
            symbol = str(plan.get("symbol") or "").upper()
            view = positions.get(symbol)
            if view is None or view["direction"] != str(plan.get("direction") or "").upper():
                continue
            current = plan.get(KEY) if isinstance(plan.get(KEY), dict) else {}
            if current.get("candle") == at or now < float(current.get("retry_at") or 0):
                continue
            if symbol not in computed:
                computed[symbol] = await guard.fetch_analysis(client, symbol, at, kline_fetch)
            analysis, why = computed[symbol]
            if analysis is None:
                # Veri eksik/bayat: önceki puan korunur, kısa süre sonra yeniden denenir.
                plan[KEY] = {**current, "retry_at": now + RETRY_SECONDS, "last_failure": why}
            else:
                obs = guard.classify_observation(analysis, view["direction"], 0)
                plan[KEY] = {"score": obs["score"], "mismatch": bool(obs["mismatch"]),
                             "opposite_confidence": obs["opposite_confidence"], "note": obs["note"],
                             "candle": at, "checked_at": now, "basis": "15m_candle_close"}
            changed = True
        except Exception as exc:  # asla ana döngüyü düşürme
            logger.warning("LIVE_SCORE_FAILED symbol=%s error=%s", plan.get("symbol"), type(exc).__name__)
    return changed
