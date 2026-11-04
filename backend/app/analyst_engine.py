"""Yeni Analist motoru – bağımsız değerlendirme katmanı (ARAŞTIRMA; henüz hiçbir API'ye bağlı değil).

Canlı sistemle ilişkisi:
- `analysis.py`, emir, stop/TP ve risk koduna dokunmaz; `analysis` içindeki gösterge fonksiyonlarını yalnız OKUR.
- Eski alanların (confidence, long_score, short_score, final_decision_score, smart_score) yerine geçmez; yalnız
  `analyst_*` ve `calibrat*` alanlarını üretir.

Puanlama (onaylı plan §1). Her bileşen −1 (tam SHORT) … +1 (tam LONG) arası yönlü bir değer verir:
  trend              15m: fiyat–EMA50, EMA20–EMA50, EMA50–EMA200 (ATR ile ölçekli, tanh)
  momentum           15m: RSI14, MACD histogramı, 16 mumluk (4 saat) değişim
  structure          15m: onaylı salınım tepeleri/dipleri (yükselen/alçalan yapı) + son salınımın kırılması
  volume_volatility  15m: son 20 mumun hacim ağırlıklı yön dengesi × ATR yüzdeliği aşırıysa 0,5
  mtf                1h ve 4h: fiyat–EMA50 ve EMA20–EMA50 (her biri yarı ağırlık)
  ham = Σ ağırlık × bileşen / 100  →  analyst_score = round(100 × |ham|), yön = işaret(ham), skor < 20 → BEKLE.
Skor bir OLASILIK DEĞİLDİR; bileşenlerin aynı yönde birleşme gücüdür. Olasılık ayrı kalibrasyonla üretilir.

Veri kalitesi (onaylı plan §2): yalnız kapanmış mumlar (açık mum süzülür); ≥400 iyi, 250–399 uyarı, <250 geçersiz.
15m geçersizse skor üretilmez. Eksik zaman dilimi/EMA200 katkısı 0 sayılır — ağırlıklar yeniden DAĞITILMAZ.
"""

from __future__ import annotations

import math
from typing import Any

from app.analysis import ema, rsi  # göstergeler analysis.py'den yalnız okunur

ENGINE_VERSION = "analyst-engine-0.1"
WEIGHTS: dict[str, dict[str, int]] = {
    "A": {"trend": 25, "momentum": 20, "structure": 20, "volume_volatility": 15, "mtf": 20},
    "B": {"trend": 25, "momentum": 15, "structure": 25, "volume_volatility": 15, "mtf": 20},
}
COMPONENTS = ("trend", "momentum", "structure", "volume_volatility", "mtf")
INTERVAL_SECONDS = {"15m": 900, "1h": 3600, "4h": 14400}
PREFERRED_BARS = 400
MIN_BARS = 250
DIRECTION_MIN_SCORE = 20
PIVOT_SPAN = 3            # salınım: iki yanında 3 mum; sağ taraf kapanmış olmalı (onaylı)
PIVOT_LOOKBACK = 200
FLOW_BARS = 20
MOMENTUM_BARS = 16
ATR_PCT_BARS = 300


def _clip(value: float) -> float:
    return max(-1.0, min(1.0, value))


def _sign(value: float) -> float:
    return 1.0 if value > 0 else -1.0 if value < 0 else 0.0


def quality(count: int) -> str:
    return "IYI" if count >= PREFERRED_BARS else "UYARI" if count >= MIN_BARS else "GECERSIZ"


def closed_only(candles: list[dict], interval: str, now: int | None) -> list[dict]:
    """Açık mum hiçbir koşulda hesaba girmez: açılış + süre > now olan mumlar atılır."""
    if now is None:
        return list(candles)
    step = INTERVAL_SECONDS[interval]
    return [c for c in candles if c["time"] + step <= now]


def atr_series(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    """analysis.atr ile aynı Wilder yumuşatması; son değer analysis.atr ile birebir aynıdır."""
    ranges = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])) for i in range(1, len(closes))]
    if len(ranges) < period:
        return []
    value = sum(ranges[:period]) / period
    out = [value]
    for item in ranges[period:]:
        value = (value * (period - 1) + item) / period
        out.append(value)
    return out


def _trend_parts(closes: list[float], atr_value: float, use_ema200: bool) -> list[float]:
    e20, e50 = ema(closes, 20)[-1], ema(closes, 50)[-1]
    parts = [math.tanh((closes[-1] - e50) / (2 * atr_value)), math.tanh((e20 - e50) / atr_value)]
    if use_ema200:
        parts.append(math.tanh((e50 - ema(closes, 200)[-1]) / (3 * atr_value)))
    return parts


def trend(closes: list[float], atr_value: float) -> float:
    # EMA200 en az 400 mumda (2 × periyot) ısınmış sayılır; değilse katkısı 0 (yeniden dağıtılmaz).
    parts = _trend_parts(closes, atr_value, len(closes) >= PREFERRED_BARS)
    return sum(parts) / 3


def momentum(closes: list[float], atr_value: float) -> float:
    fast, slow = ema(closes, 12), ema(closes, 26)
    line = [a - b for a, b in zip(fast, slow)]
    hist = line[-1] - ema(line, 9)[-1]
    m1 = _clip((rsi(closes) - 50) / 20)
    m2 = math.tanh(hist / (0.25 * atr_value))
    m3 = math.tanh((closes[-1] - closes[-1 - MOMENTUM_BARS]) / (2 * atr_value))
    return (m1 + m2 + m3) / 3


def pivots(highs: list[float], lows: list[float]) -> tuple[list[float], list[float]]:
    """Onaylı salınımlar: sol 3 mumdan kesin büyük/küçük, sağ 3 mumdan büyük-eşit/küçük-eşit. Sağdaki 3 mum kapanmış olmalı."""
    n = len(highs)
    start = max(PIVOT_SPAN, n - PIVOT_LOOKBACK)
    tops, bottoms = [], []
    for i in range(start, n - PIVOT_SPAN):
        left_h, right_h = highs[i - PIVOT_SPAN:i], highs[i + 1:i + 1 + PIVOT_SPAN]
        left_l, right_l = lows[i - PIVOT_SPAN:i], lows[i + 1:i + 1 + PIVOT_SPAN]
        if highs[i] > max(left_h) and highs[i] >= max(right_h):
            tops.append(highs[i])
        if lows[i] < min(left_l) and lows[i] <= min(right_l):
            bottoms.append(lows[i])
    return tops, bottoms


def structure(highs: list[float], lows: list[float], close: float) -> float:
    tops, bottoms = pivots(highs, lows)
    shape = 0.0
    if len(tops) >= 2 and len(bottoms) >= 2:
        shape = 0.5 * _sign(tops[-1] - tops[-2]) + 0.5 * _sign(bottoms[-1] - bottoms[-2])
    breakout = 0.0
    if tops and close > tops[-1]:
        breakout = 1.0
    elif bottoms and close < bottoms[-1]:
        breakout = -1.0
    return (shape + breakout) / 2


def atr_percentile(atrs: list[float]) -> float | None:
    history = atrs[-ATR_PCT_BARS:]
    if len(history) < 50:
        return None
    current = history[-1]
    return sum(1 for value in history if value <= current) / len(history)


def volume_volatility(candles: list[dict], pct: float | None) -> float:
    recent = candles[-FLOW_BARS:]
    total = sum(c["volume"] for c in recent)
    balance = sum(c["volume"] * _sign(c["close"] - c["open"]) for c in recent) / total if total > 0 else 0.0
    factor = 0.5 if pct is not None and (pct > 0.9 or pct < 0.1) else 1.0
    return _clip(2 * balance) * factor


def higher_timeframe(candles: list[dict]) -> float | None:
    if quality(len(candles)) == "GECERSIZ":
        return None
    closes = [c["close"] for c in candles]
    atrs = atr_series([c["high"] for c in candles], [c["low"] for c in candles], closes)
    if not atrs or atrs[-1] <= 0:
        return None
    parts = _trend_parts(closes, atrs[-1], False)
    return sum(parts) / 2


def risk_label(pct: float | None, extension_atr: float) -> str:
    if (pct is not None and pct >= 0.8) or extension_atr > 2.5:
        return "YUKSEK"
    if pct is not None and pct < 0.4 and extension_atr < 1.0:
        return "DUSUK"
    return "ORTA"


def components(c15: list[dict], c1h: list[dict], c4h: list[dict], *, now: int | None = None) -> dict[str, Any]:
    """Model ağırlıklarından bağımsız ham bileşenler (A ve B aynı bileşenleri farklı ağırlıklarla birleştirir)."""
    c15, c1h, c4h = closed_only(c15, "15m", now), closed_only(c1h, "1h", now), closed_only(c4h, "4h", now)
    q = {"15m": quality(len(c15)), "1h": quality(len(c1h)), "4h": quality(len(c4h))}
    warnings = [f"{tf}: {len(rows)} kapanmış mum (<{PREFERRED_BARS})" for tf, rows in (("15m", c15), ("1h", c1h), ("4h", c4h))
                if q[tf] != "IYI"]
    out: dict[str, Any] = {"data_quality": {"timeframes": q, "warnings": warnings,
                                            "overall": "GECERSIZ" if q["15m"] == "GECERSIZ" else
                                            "IYI" if all(v == "IYI" for v in q.values()) else "UYARI"},
                           "values": None, "risk": None}
    if q["15m"] == "GECERSIZ":
        return out
    closes = [c["close"] for c in c15]
    highs, lows = [c["high"] for c in c15], [c["low"] for c in c15]
    atrs = atr_series(highs, lows, closes)
    if not atrs or atrs[-1] <= 0:
        out["data_quality"]["overall"] = "GECERSIZ"
        out["data_quality"]["warnings"].append("15m: ATR sıfır")
        return out
    atr_value = atrs[-1]
    pct = atr_percentile(atrs)
    h1, h4 = higher_timeframe(c1h), higher_timeframe(c4h)
    mtf = 0.5 * (h1 or 0.0) + 0.5 * (h4 or 0.0)   # eksik zaman dilimi 0 katkı; yeniden dağıtım yok
    out["values"] = {
        "trend": trend(closes, atr_value),
        "momentum": momentum(closes, atr_value),
        "structure": structure(highs, lows, closes[-1]),
        "volume_volatility": volume_volatility(c15, pct),
        "mtf": mtf,
    }
    extension = abs(closes[-1] - ema(closes, 20)[-1]) / atr_value
    out["risk"] = risk_label(pct, extension)
    out["atr_percentile"] = pct
    return out


def combine(parts: dict[str, Any], model: str) -> dict[str, Any]:
    """Bileşenleri model ağırlığıyla birleştirir. Olasılık alanları kalibrasyon katmanı doldurana kadar None kalır."""
    result: dict[str, Any] = {
        "analyst_score": None, "analyst_direction": None, "analyst_risk": parts.get("risk"),
        "analyst_data_quality": parts["data_quality"], "analyst_raw": None, "analyst_model": model,
        "calibrated_success_probability": None, "calibration_sample_count": None,
        "calibration_confidence_interval": None, "calibration_version": None, "engine_version": ENGINE_VERSION,
    }
    values = parts.get("values")
    if values is None:
        return result
    weights = WEIGHTS[model]
    raw = sum(weights[name] * values[name] for name in COMPONENTS) / 100
    score = round(100 * abs(raw))
    result.update(analyst_raw=raw, analyst_score=score,
                  analyst_direction=("LONG" if raw > 0 else "SHORT") if score >= DIRECTION_MIN_SCORE and raw != 0 else "BEKLE",
                  analyst_components={name: {"value": values[name], "points": weights[name] * values[name]} for name in COMPONENTS})
    return result


def analyze_analyst(c15: list[dict], c1h: list[dict], c4h: list[dict], *, model: str = "A", now: int | None = None) -> dict[str, Any]:
    return combine(components(c15, c1h, c4h, now=now), model)
