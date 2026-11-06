"""Kais Long gölge puanı: yalnız KAYDEDER, hiçbir kararı değiştirmez.

Eski puandan farkı: (1) fiyat>Bollinger orta bandı puanı kaldırıldı (fiyat>EMA20 ile aynı bilgiyi tekrar sayıyordu),
(2) aşırı uzamış fiyata (EMA20'den ATR cinsinden uzaklık) ve dirence yakınlığa ceza.
Karşılaştırma günlükteki KAIS_SHADOW_SCORE satırlarından yapılır."""
from __future__ import annotations

import math
from typing import Any

SHADOW_THRESHOLD = 86  # eski eşikle aynı; dağılım farkı günlükten okunur


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def shadow_score(analysis: dict[str, Any]) -> dict[str, Any] | None:
    """Eksik veri varsa None. LONG olmayan sonuç için de hesaplar (yön kontrolü çağırana ait)."""
    ema = analysis.get("ema") or {}
    price, e20, e50, e200 = (_num(analysis.get("entry")), _num(ema.get("ema20")), _num(ema.get("ema50")), _num(ema.get("ema200")))
    macd, rsi, adx, vol = (_num(analysis.get("macd")), _num(analysis.get("rsi")), _num(analysis.get("adx")), _num(analysis.get("volume_ratio")))
    atr, resistance = _num(analysis.get("atr")), _num(analysis.get("resistance"))
    if None in (price, e20, e50, e200, macd, rsi, adx, vol, atr, resistance) or atr <= 0:
        return None
    score = 25
    score += 10 if price > e20 else 0
    score += 12 if e20 > e50 else 0
    score += 14 if e50 > e200 else 0
    score += 10 if macd > 0 else 0
    score += 9 if 52 <= rsi <= 72 else 0
    score += 8 if adx >= 20 and e20 > e50 else 0
    score += 6 if vol >= 1.05 else 0
    extension = abs(price - e20) / atr
    penalty = 14 if extension > 3 else 8 if extension > 2 else 0
    penalty += 6 if (resistance - price) / atr < 1 else 0
    new = max(0, min(95, score - penalty))
    return {"score": new, "penalty": penalty, "extension_atr": round(extension, 2), "would_pass": new >= SHADOW_THRESHOLD}
