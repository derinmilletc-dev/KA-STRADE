"""Analist V2 – 1 saatlik yön ve hareket analizi (yalnız analist ekranı; canlı işlemle bağlantısı yoktur).

Model: 2020-10…2026-04 verisiyle eğitilmiş, kalibre edilmiş çok sınıflı lojistik regresyon (LONG / SHORT / BEKLE),
yalnız kapanmış 15m mumlardan. Geçmiş veride 17 katlı walk-forward ile doğrulandı; ileriye dönük test yapılmadı.
Puan bir olasılık değildir: yön eğiliminin, geçmişteki tüm tahminler içindeki gücünü (yüzdelik) gösterir.
Geçmiş isabet oranları puanın yanında ayrıca verilir. Maliyet sonrası kâr avantajı gösterilmemiştir.

Saf Python'dur (numpy/pandas gerektirmez). Özellik hesapları `analyst_v2_features_ref.py` (pandas) ile birebir aynıdır;
eşitlik `tests/test_analyst_v2.py` ile doğrulanır. Eski alanlara (confidence, long_score, short_score,
final_decision_score, smart_score), `analysis.py`'ye, emir, stop/TP, pozisyon boyutu ve risk koduna dokunmaz.
"""

from __future__ import annotations

import bisect
import json
import math
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

MODEL_PATH = Path(__file__).with_name("analyst_v2_model.json")
FEATURES_15M = ["c_e20", "e20_e50", "e50_e200", "e20_slope", "e50_slope", "since_cross", "structure", "adx",
                "rsi", "rsi_chg", "macd", "macd_chg", "ret1", "ret4", "ret16", "accel", "divergence",
                "breakout", "false_breakout", "retest", "dist_hi", "dist_lo", "wick",
                "rvol", "vol_chg", "vol_dir_corr", "breakout_vol",
                "atr_rel", "atr_pct", "bb_pct", "expansion", "shock"]
PREF_BARS = 400
M15 = 900
DIRECTION_MIN_SCORE = 60       # bunun altında yön gösterilmez (BEKLE); geçmişte isabet rastgeleye yakın
STRONG_SCORE = 80
GROUP_LABELS = {"trend": "Trend", "momentum": "Momentum", "yapi": "Piyasa yapısı", "hacim": "Hacim", "volatilite": "Volatilite"}
NAN = float("nan")


# ------------------------------------------------------------------ model
@lru_cache(maxsize=1)
def load_model(path: str = str(MODEL_PATH)) -> dict[str, Any]:
    model = json.loads(Path(path).read_text(encoding="utf-8"))
    if model["features"] != FEATURES_15M or model["lr_classes"] != [0, 1, 2]:
        raise ValueError("Analist V2 model dosyası kodla uyuşmuyor")
    return model


def _softmax(z: list[float]) -> list[float]:
    m = max(z)
    e = [math.exp(v - m) for v in z]
    s = sum(e)
    return [v / s for v in e]


def _matvec(a: list[list[float]], x: list[float], b: list[float]) -> list[float]:
    return [sum(ai * xi for ai, xi in zip(row, x)) + bi for row, bi in zip(a, b)]


def _percentile(quantiles: list[float], value: float) -> int:
    return max(0, min(100, bisect.bisect_right(quantiles, value) - 1))


# ------------------------------------------------------------------ göstergeler (pandas referansıyla aynı tanımlar)
def _ewm(values: list[float], alpha: float) -> list[float]:
    """pandas ewm(adjust=False): ilk geçerli değerden başlar; öncesi NaN."""
    out, prev = [], None
    for v in values:
        if v != v:                       # NaN
            out.append(NAN if prev is None else prev)
            continue
        prev = v if prev is None else alpha * v + (1 - alpha) * prev
        out.append(prev)
    return out


def _ema(values: list[float], n: int) -> list[float]:
    return _ewm(values, 2 / (n + 1))


def _wilder(values: list[float], n: int = 14) -> list[float]:
    return _ewm(values, 1 / n)


def _sign(x: float) -> float:
    return 1.0 if x > 0 else -1.0 if x < 0 else 0.0


def _avg_ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def _spearman(a: list[float], b: list[float]) -> float:
    ra, rb = _avg_ranks(a), _avg_ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    da, db = [x - ma for x in ra], [x - mb for x in rb]
    den = math.sqrt(sum(x * x for x in da) * sum(x * x for x in db))
    return sum(x * y for x, y in zip(da, db)) / den if den > 0 else 0.0


def contiguous_closed(candles: list[dict], now: float) -> list[dict]:
    """Yalnız kapanmış mumlar; sondan geriye kesintisiz (900 sn aralıklı) kısım."""
    rows = sorted({int(c["time"]): c for c in candles if c["time"] + M15 <= now}.values(), key=lambda c: c["time"])
    start = len(rows) - 1
    while start > 0 and rows[start]["time"] - rows[start - 1]["time"] == M15:
        start -= 1
    return rows[start:] if rows else []


def features_last(rows: list[dict]) -> dict[str, float]:
    """Son (kapanmış) mum için 32 özellik. `rows` kesintisiz olmalıdır."""
    o = [float(r["open"]) for r in rows]
    h = [float(r["high"]) for r in rows]
    l = [float(r["low"]) for r in rows]
    c = [float(r["close"]) for r in rows]
    v = [float(r["volume"]) for r in rows]
    n = len(c)
    L = n - 1
    tr = [NAN] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, n)]
    atr = _wilder(tr)
    d = [NAN] + [c[i] - c[i - 1] for i in range(1, n)]
    gain = _wilder([NAN if x != x else max(x, 0.0) for x in d])
    loss = _wilder([NAN if x != x else max(-x, 0.0) for x in d])
    rsi = [NAN if (g != g or q != q) else (100.0 if q == 0 else 100 - 100 / (1 + g / q)) for g, q in zip(gain, loss)]
    e12, e26 = _ema(c, 12), _ema(c, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = _ema(macd, 9)
    hist = [a - b for a, b in zip(macd, sig)]
    pdm, mdm = [0.0], [0.0]
    for i in range(1, n):
        up, down = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm.append(up if (up > down and up > 0) else 0.0)
        mdm.append(down if (down > up and down > 0) else 0.0)
    wtr, wp, wm = _wilder(tr), _wilder(pdm), _wilder(mdm)
    dx = []
    for t, p, m in zip(wtr, wp, wm):
        if t != t or t == 0:
            dx.append(NAN)
            continue
        pdi, mdi = 100 * p / t, 100 * m / t
        dx.append(100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) else NAN)
    adx_win = dx[L - 13:L + 1]
    adx = sum(adx_win) / 14 if all(x == x for x in adx_win) else NAN
    e20, e50, e200 = _ema(c, 20), _ema(c, 50), _ema(c, 200)

    def pivots_until(last: int):
        tops, bottoms = [], []
        for i in range(3, last - 2):                     # onay i + 3 <= last
            if h[i] > max(h[i - 3:i]) and h[i] >= max(h[i + 1:i + 4]):
                tops.append(h[i])
            if l[i] < min(l[i - 3:i]) and l[i] <= min(l[i + 1:i + 4]):
                bottoms.append(l[i])
        return tops, bottoms

    tops, bottoms = pivots_until(L)
    if len(tops) >= 2 and len(bottoms) >= 2:
        structure = 0.5 * _sign(tops[-1] - tops[-2]) + 0.5 * _sign(bottoms[-1] - bottoms[-2])
    else:
        structure = 0.0
    a = atr[L]
    s1 = a / c[L]
    lr = [math.log(x) for x in c]
    f: dict[str, float] = {}
    f["c_e20"] = (c[L] - e20[L]) / a
    f["e20_e50"] = (e20[L] - e50[L]) / a
    f["e50_e200"] = (e50[L] - e200[L]) / a if n >= PREF_BARS else 0.0
    f["e20_slope"] = (e20[L] - e20[L - 4]) / a
    f["e50_slope"] = (e50[L] - e50[L - 4]) / a
    cross = [_sign(x - y) for x, y in zip(e20, e50)]
    last_change = next((j for j in range(L, 0, -1) if cross[j] != cross[j - 1]), None)
    f["since_cross"] = 96.0 if last_change is None else float(min(L - last_change, 96))
    f["structure"] = structure
    f["adx"] = adx
    f["rsi"] = rsi[L] - 50
    f["rsi_chg"] = rsi[L] - rsi[L - 4]
    f["macd"] = hist[L] / a
    f["macd_chg"] = (hist[L] - hist[L - 4]) / a
    for k in (1, 4, 16):
        f[f"ret{k}"] = (lr[L] - lr[L - k]) / s1
    f["accel"] = ((lr[L] - lr[L - 4]) - (lr[L - 4] - lr[L - 8])) / s1
    newhigh = h[L] >= max(h[L - 49:L + 1])
    newlow = l[L] <= min(l[L - 49:L + 1])
    rsi_prev = rsi[L - 49:L]
    if newhigh and rsi[L] < max(rsi_prev):
        f["divergence"] = -1.0
    elif newlow and rsi[L] > min(rsi_prev):
        f["divergence"] = 1.0
    else:
        f["divergence"] = 0.0

    def levels(j: int) -> tuple[float, float]:
        if j - 82 < 0:
            return NAN, NAN
        return max(h[j - 82:j - 2]), min(l[j - 82:j - 2])

    def brk_at(j: int) -> float:
        lh, ll = levels(j)
        if lh != lh:
            return NAN
        return 1.0 if c[j] > lh else -1.0 if c[j] < ll else 0.0

    f["breakout"] = brk_at(L)
    fb_up = fb_dn = 0.0
    for j in range(L - 3, L + 1):
        lh, ll = levels(j)
        if lh == lh and h[j] > lh and c[j] <= lh:
            fb_up = 1.0
        if ll == ll and l[j] < ll and c[j] >= ll:
            fb_dn = 1.0
    f["false_breakout"] = fb_dn - fb_up

    def ffill_level(j: int, want: float) -> float:
        for i in range(j, max(j - 16, 0) - 1, -1):
            if brk_at(i) == want:
                return levels(i)[0 if want == 1.0 else 1]
        return NAN

    near_up = near_dn = False
    for j in range(L - 15, L + 1):
        up_lvl, dn_lvl = ffill_level(j, 1.0), ffill_level(j, -1.0)
        if up_lvl == up_lvl and l[j] <= up_lvl + 0.25 * atr[j]:
            near_up = True
        if dn_lvl == dn_lvl and h[j] >= dn_lvl - 0.25 * atr[j]:
            near_dn = True
    up_now, dn_now = ffill_level(L, 1.0), ffill_level(L, -1.0)
    f["retest"] = (1.0 if near_up and up_now == up_now and c[L] > up_now else 0.0) - \
                  (1.0 if near_dn and dn_now == dn_now and c[L] < dn_now else 0.0)
    f["dist_hi"] = (max(h[L - 79:L + 1]) - c[L]) / a
    f["dist_lo"] = (c[L] - min(l[L - 79:L + 1])) / a
    wicks = []
    for j in range(L - 2, L + 1):
        rng = h[j] - l[j]
        wicks.append(0.0 if rng == 0 else ((h[j] - max(o[j], c[j])) - (min(o[j], c[j]) - l[j])) / rng)
    f["wick"] = sum(wicks) / 3
    win96 = sorted(v[L - 95:L + 1])
    med = (win96[47] + win96[48]) / 2
    f["rvol"] = v[L] / med if med > 0 else NAN
    m20, m100 = sum(v[L - 19:L + 1]) / 20, sum(v[L - 99:L + 1]) / 100
    f["vol_chg"] = math.log(m20 / m100) if m20 > 0 and m100 > 0 else NAN
    f["vol_dir_corr"] = _spearman([_sign(c[j] - o[j]) for j in range(L - 19, L + 1)], v[L - 19:L + 1])
    f["breakout_vol"] = f["breakout"] * (1.0 if (f["rvol"] == f["rvol"] and f["rvol"] >= 1.5) else 0.0)
    f["atr_rel"] = s1
    atr_win = atr[L - 299:L + 1]
    f["atr_pct"] = sum(1 for x in atr_win if x <= a) / 300 if all(x == x for x in atr_win) else NAN
    bbw = []
    for j in range(L - 299, L + 1):
        w = c[j - 19:j + 1]
        mu = sum(w) / 20
        sd = math.sqrt(sum((x - mu) ** 2 for x in w) / 20)
        bbw.append(4 * sd / mu)
    f["bb_pct"] = sum(1 for x in bbw if x <= bbw[-1]) / 300
    r = [hh - ll for hh, ll in zip(h, l)]
    f["expansion"] = (sum(r[L - 7:L + 1]) / 8) / (sum(r[L - 29:L - 7]) / 22)
    f["shock"] = abs(lr[L] - lr[L - 1]) / s1
    return f


# ------------------------------------------------------------------ açıklamalar
def _fmt(x: float, digits: int = 2) -> str:
    return f"{x:.{digits}f}".replace(".", ",")


def _details(f: dict[str, float]) -> dict[str, str]:
    trend = [f"Fiyat EMA20'nin {_fmt(abs(f['c_e20']))} ATR {'üstünde' if f['c_e20'] >= 0 else 'altında'}",
             "EMA20 > EMA50" if f["e20_e50"] >= 0 else "EMA20 < EMA50"]
    if f["e50_e200"] != 0:
        trend.append("EMA50 > EMA200" if f["e50_e200"] > 0 else "EMA50 < EMA200")
    trend.append({1.0: "yükselen tepe/dip yapısı", -1.0: "alçalan tepe/dip yapısı"}.get(f["structure"], "karışık tepe/dip yapısı"))
    trend.append(f"ADX {_fmt(f['adx'], 0)} ({'güçlü' if f['adx'] >= 25 else 'zayıf'} trend)")
    mom = [f"RSI {_fmt(f['rsi'] + 50, 0)}", f"MACD histogramı {'pozitif' if f['macd'] >= 0 else 'negatif'}",
           f"son 1 saat {_fmt(f['ret4'], 1)}σ", f"son 4 saat {_fmt(f['ret16'], 1)}σ"]
    if f["divergence"]:
        mom.append("yükseliş uyumsuzluğu" if f["divergence"] > 0 else "düşüş uyumsuzluğu")
    yapi = [f"son 20 saatin tepesine {_fmt(f['dist_hi'], 1)} ATR, dibine {_fmt(f['dist_lo'], 1)} ATR",
            {1.0: "yukarı kırılım", -1.0: "aşağı kırılım"}.get(f["breakout"], "kırılım yok")]
    if f["false_breakout"]:
        yapi.append("aşağıda sahte kırılım (toplama)" if f["false_breakout"] > 0 else "yukarıda sahte kırılım (reddedilme)")
    if f["retest"]:
        yapi.append("kırılan seviye üstten test edildi" if f["retest"] > 0 else "kırılan seviye alttan test edildi")
    if abs(f["wick"]) > 0.25:
        yapi.append("üst fitil baskısı" if f["wick"] > 0 else "alt fitil desteği")
    hacim = [f"son mum hacmi medyanın {_fmt(f['rvol'], 1)} katı",
             f"son 5 saatin hacmi, 25 saatlik ortalamanın {_fmt(math.exp(f['vol_chg']), 2)} katı",
             "hacim mum yönüyle uyumlu" if f["vol_dir_corr"] > 0.2 else
             "hacim mum yönüyle uyumsuz" if f["vol_dir_corr"] < -0.2 else "hacim–yön ilişkisi zayıf"]
    vol = [f"ATR / fiyat: %{_fmt(f['atr_rel'] * 100)}", f"ATR yüzdeliği (son 75 saat): %{_fmt(f['atr_pct'] * 100, 0)}",
           "volatilite genişliyor" if f["expansion"] > 1.2 else "volatilite daralıyor" if f["expansion"] < 0.8 else "volatilite dengeli"]
    return {"trend": " · ".join(trend), "momentum": " · ".join(mom), "yapi": " · ".join(yapi),
            "hacim": " · ".join(hacim), "volatilite": " · ".join(vol)}


# ------------------------------------------------------------------ değerlendirme
def evaluate(candles: list[dict], now: float | None = None, model: dict | None = None) -> dict[str, Any]:
    """Tek coin için analist sonucu. `candles` = Binance 15m mumları (açık mum otomatik atılır)."""
    model = model or load_model()
    now = time.time() if now is None else now
    base = {"model_version": model["version"], "horizon": model["horizon"],
            "note": "Puan olasılık değildir. Geçmiş isabet oranları gelecek için garanti değildir; işlem sinyali değildir."}
    rows = contiguous_closed(candles, now)
    if len(rows) < PREF_BARS:
        return {**base, "status": "VERI_YETERSIZ", "analyst_direction": "BEKLE", "analyst_score": None,
                "data_quality": {"closed_bars": len(rows), "required": PREF_BARS}}
    f = features_last(rows)
    x = [f[name] for name in FEATURES_15M]
    if not all(math.isfinite(val) for val in x):
        return {**base, "status": "VERI_YETERSIZ", "analyst_direction": "BEKLE", "analyst_score": None,
                "data_quality": {"closed_bars": len(rows), "missing": [n for n, val in zip(FEATURES_15M, x) if not math.isfinite(val)]}}
    z = [(val - mu) / sd for val, mu, sd in zip(x, model["scaler_mean"], model["scaler_scale"])]
    raw = _softmax(_matvec(model["coef"], z, model["intercept"]))
    prob = _softmax(_matvec(model["cal_coef"], [math.log(max(p, 1e-12)) for p in raw], model["cal_intercept"]))
    p_long, p_short, p_wait = prob
    strength = p_long - p_short
    score = _percentile(model["strength_quantiles"], abs(strength))
    move_pct = _percentile(model["move_quantiles"], 1 - p_wait)
    lean = "LONG" if strength > 0 else "SHORT"
    direction = lean if score >= DIRECTION_MIN_SCORE and strength != 0 else "BEKLE"
    strength_label = "GÜÇLÜ" if score >= STRONG_SCORE else "ORTA" if score >= DIRECTION_MIN_SCORE else "ZAYIF"
    band_lo = min(90, score // 10 * 10)
    band = f"{band_lo}-{100 if band_lo == 90 else band_lo + 9}"
    hist = next((row for row in model["band_table"] if row["band"] == band and row["direction"] == lean), None)
    move_level = "YUKSEK" if move_pct >= 67 else "DUSUK" if move_pct < 33 else "ORTA"
    move_hist = next((row for row in model["move_table"] if row["level"] == move_level), None)
    diff = [(cl - cs) * zi for cl, cs, zi in zip(model["coef"][0], model["coef"][1], z)]
    contrib = {g: sum(diff[FEATURES_15M.index(name)] for name in cols) for g, cols in model["groups"].items()}
    total = sum(abs(val) for val in contrib.values()) or 1.0
    details = _details(f)
    # Ortalamaya dönüş: bileşen, göstergenin ham yönünün tersine itiyorsa bunu açıkça yaz.
    raw_sign = {"trend": _sign(f["c_e20"]), "momentum": _sign(f["ret4"])}
    components = []
    for g, val in sorted(contrib.items(), key=lambda kv: -abs(kv[1])):
        push = "LONG" if val > 0.02 else "SHORT" if val < -0.02 else "NÖTR"
        detail = details[g]
        if push != "NÖTR" and g in raw_sign and raw_sign[g] and raw_sign[g] != (1 if push == "LONG" else -1):
            detail += (" → fiyat kısa vadede " + ("yukarı" if raw_sign[g] > 0 else "aşağı") +
                       " aşırı uzadı; geçmişte bu durum çoğunlukla geri dönüşle sonuçlandı")
        components.append({"group": g, "label": GROUP_LABELS[g], "push": push, "weight_pct": round(abs(val) / total * 100),
                           "contribution": round(val, 4), "detail": detail})
    move_text = {"YUKSEK": "yüksek", "ORTA": "orta", "DUSUK": "düşük"}[move_level]
    if direction == "BEKLE":
        summary = (f"Belirgin bir 1 saatlik yön eğilimi yok (puan {score}/100, hafif {lean} eğilimli). "
                   f"Hareket beklentisi {move_text}.")
    else:
        drivers = [comp["label"].lower() for comp in components if comp["push"] == direction][:2]
        summary = f"1 saatlik eğilim {direction} ({strength_label.lower()}, puan {score}/100)"
        summary += f"; öne çıkan: {', '.join(drivers)}." if drivers else "."
        if hist:
            up, down = ("yükseliş", "düşüş") if direction == "LONG" else ("düşüş", "yükseliş")
            summary += (f" Geçmişte bu güçteki {direction} eğilimlerinden 1 saat içinde: %{round(hist['hit'] * 100)} belirgin {up}, "
                        f"%{round(hist['opposite'] * 100)} belirgin {down}, kalanı yatay (rastgele %{round(hist['base'] * 100)} {up}).")
        summary += f" Hareket beklentisi {move_text}."
    return {
        **base, "status": "OK",
        "analyst_direction": direction, "analyst_lean": lean, "analyst_score": score, "analyst_strength": strength_label,
        "probabilities": {"LONG": round(p_long, 4), "SHORT": round(p_short, 4), "BEKLE": round(p_wait, 4)},
        "movement": {"level": move_level, "percentile": move_pct,
                     "historical_share_moved": round(move_hist["share_moved"], 4) if move_hist else None},
        "history": {"band": band, "direction": lean, "samples": hist["n"], "hit_rate": round(hist["hit"], 4),
                    "opposite_rate": round(hist["opposite"], 4), "base_rate": round(hist["base"], 4),
                    "avg_net_bp": round(hist["net_bp"], 1)} if hist else None,
        "components": components, "summary": summary,
        "data_quality": {"closed_bars": len(rows), "status": "IYI"},
    }
