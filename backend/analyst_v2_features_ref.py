"""Analist V2 – 15m özellik REFERANS hesapları (pandas; yalnız araştırma ve testler). Canlıda app/analyst_v2.py saf Python sürümü kullanılır.

Yalnız kapanmış mumlar verilmelidir. Fiyattan bağımsız, normalize özellikler üretir. Canlı işlem, emir,
stop/TP veya risk koduyla bağlantısı yoktur; `analysis.py`'ye dokunmaz.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

GROUPS_15M = {
    "trend": ["c_e20", "e20_e50", "e50_e200", "e20_slope", "e50_slope", "since_cross", "structure", "adx"],
    "momentum": ["rsi", "rsi_chg", "macd", "macd_chg", "ret1", "ret4", "ret16", "accel", "divergence"],
    "yapi": ["breakout", "false_breakout", "retest", "dist_hi", "dist_lo", "wick"],
    "hacim": ["rvol", "vol_chg", "vol_dir_corr", "breakout_vol"],
    "volatilite": ["atr_rel", "atr_pct", "bb_pct", "expansion", "shock"],
}
FEATURES_15M = [name for cols in GROUPS_15M.values() for name in cols]
MIN_BARS, PREF_BARS = 250, 400


def ema(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(span=n, adjust=False).mean()


def wilder(x: pd.Series, n: int = 14) -> pd.Series:
    return x.ewm(alpha=1 / n, adjust=False).mean()


def base_indicators(df: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c, v = (df[k] for k in "ohlcv")
    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1, skipna=False)
    atr = wilder(tr)
    d = c.diff()
    rs = wilder(d.clip(lower=0)) / wilder((-d).clip(lower=0))
    rsi = 100 - 100 / (1 + rs)
    e12, e26 = ema(c, 12), ema(c, 26)
    macd = e12 - e26
    hist = macd - ema(macd, 9)
    up, down = h.diff(), -l.diff()
    pdm = up.where((up > down) & (up > 0), 0.0)
    mdm = down.where((down > up) & (down > 0), 0.0)
    pdi, mdi = 100 * wilder(pdm) / wilder(tr), 100 * wilder(mdm) / wilder(tr)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    adx = dx.rolling(14).mean()
    e20, e50, e200 = ema(c, 20), ema(c, 50), ema(c, 200)
    left_h = pd.concat([h.shift(k) for k in (1, 2, 3)], axis=1).max(axis=1, skipna=False)
    right_h = pd.concat([h.shift(-k) for k in (1, 2, 3)], axis=1).max(axis=1, skipna=False)
    left_l = pd.concat([l.shift(k) for k in (1, 2, 3)], axis=1).min(axis=1, skipna=False)
    right_l = pd.concat([l.shift(-k) for k in (1, 2, 3)], axis=1).min(axis=1, skipna=False)
    ph = (h > left_h) & (h >= right_h)
    pl = (l < left_l) & (l <= right_l)
    ph_conf = h.where(ph).shift(3)       # sağdaki 3 mum kapandıktan sonra bilinir
    pl_conf = l.where(pl).shift(3)

    def last_two(conf: pd.Series) -> tuple[pd.Series, pd.Series]:
        s = conf.dropna()
        return s.reindex(conf.index).ffill(), s.shift(1).reindex(conf.index).ffill()

    h2, h1_ = last_two(ph_conf)
    l2, l1_ = last_two(pl_conf)
    structure = 0.5 * np.sign(h2 - h1_) + 0.5 * np.sign(l2 - l1_)
    return pd.DataFrame({"o": o, "h": h, "l": l, "c": c, "v": v, "atr": atr, "rsi": rsi, "hist": hist, "adx": adx,
                         "e20": e20, "e50": e50, "e200": e200, "structure": structure.fillna(0.0)})


def segment_age(df: pd.DataFrame) -> pd.Series:
    ok = df["c"].notna().astype(int)
    grp = (ok == 0).cumsum()
    return ok.groupby(grp).cumsum()


def window_rank(values: np.ndarray, idx: np.ndarray, w: int) -> np.ndarray:
    out = np.full(len(idx), np.nan)
    ok = idx >= w - 1
    sub = idx[ok]
    if not len(sub):
        return out
    win = np.lib.stride_tricks.sliding_window_view(values, w)[sub - w + 1]
    cur = values[sub][:, None]
    res = (win <= cur).mean(axis=1)
    res[np.isnan(win).any(axis=1) | np.isnan(values[sub])] = np.nan
    out[ok] = res
    return out


def window_spearman(a: np.ndarray, b: np.ndarray, idx: np.ndarray, w: int) -> np.ndarray:
    out = np.full(len(idx), np.nan)
    ok = idx >= w - 1
    sub = idx[ok]
    if not len(sub):
        return out
    wa = np.lib.stride_tricks.sliding_window_view(a, w)[sub - w + 1]
    wb = np.lib.stride_tricks.sliding_window_view(b, w)[sub - w + 1]
    ra = pd.DataFrame(wa.T).rank().to_numpy().T.copy()
    rb = pd.DataFrame(wb.T).rank().to_numpy().T.copy()
    ra -= ra.mean(axis=1, keepdims=True)
    rb -= rb.mean(axis=1, keepdims=True)
    den = np.sqrt((ra ** 2).sum(1) * (rb ** 2).sum(1))
    with np.errstate(invalid="ignore", divide="ignore"):
        res = (ra * rb).sum(1) / den
    res[~np.isfinite(res)] = 0.0
    res[np.isnan(wa).any(1) | np.isnan(wb).any(1)] = np.nan
    out[ok] = res
    return out


def features_15m(ind: pd.DataFrame, last: np.ndarray) -> pd.DataFrame:
    """`ind` = base_indicators çıktısı; `last` = özelliği hesaplanacak (kapanmış) mumların konumu."""
    age = segment_age(ind).to_numpy()
    c, h, l, o, v = (ind[k].to_numpy() for k in "chlov")
    atr = ind["atr"].to_numpy()
    s1 = atr[last] / c[last]
    lr = np.log(ind["c"]).to_numpy()
    f = {}
    a = atr[last]
    e20, e50, e200 = (ind[k].to_numpy() for k in ("e20", "e50", "e200"))
    f["c_e20"] = (c[last] - e20[last]) / a
    f["e20_e50"] = (e20[last] - e50[last]) / a
    f["e50_e200"] = np.where(age[last] >= PREF_BARS, (e50[last] - e200[last]) / a, 0.0)
    f["e20_slope"] = (e20[last] - e20[last - 4]) / a
    f["e50_slope"] = (e50[last] - e50[last - 4]) / a
    cross = np.sign(e20 - e50)
    change = np.r_[False, cross[1:] != cross[:-1]]
    pos = np.where(change, np.arange(len(cross)), np.nan)
    last_cross = pd.Series(pos).ffill().to_numpy()
    f["since_cross"] = np.minimum(np.nan_to_num(last - last_cross[last], nan=96.0), 96.0)
    f["structure"] = ind["structure"].to_numpy()[last]
    f["adx"] = ind["adx"].to_numpy()[last]
    rsi, hist = ind["rsi"].to_numpy(), ind["hist"].to_numpy()
    f["rsi"] = rsi[last] - 50
    f["rsi_chg"] = rsi[last] - rsi[last - 4]
    f["macd"] = hist[last] / a
    f["macd_chg"] = (hist[last] - hist[last - 4]) / a
    for n in (1, 4, 16):
        f[f"ret{n}"] = (lr[last] - lr[last - n]) / s1
    f["accel"] = ((lr[last] - lr[last - 4]) - (lr[last - 4] - lr[last - 8])) / s1
    hs, ls_ = ind["h"], ind["l"]
    newhigh = (hs >= hs.rolling(50).max()).to_numpy()
    newlow = (ls_ <= ls_.rolling(50).min()).to_numpy()
    rsi_s = ind["rsi"]
    rsi_hi_prev = rsi_s.shift(1).rolling(49).max().to_numpy()
    rsi_lo_prev = rsi_s.shift(1).rolling(49).min().to_numpy()
    f["divergence"] = np.where(newhigh[last] & (rsi[last] < rsi_hi_prev[last]), -1.0,
                               np.where(newlow[last] & (rsi[last] > rsi_lo_prev[last]), 1.0, 0.0))
    lvl_h = hs.shift(3).rolling(80).max()
    lvl_l = ls_.shift(3).rolling(80).min()
    close = ind["c"]
    brk = np.where(close > lvl_h, 1.0, np.where(close < lvl_l, -1.0, 0.0))
    brk[lvl_h.isna().to_numpy()] = np.nan
    f["breakout"] = brk[last]
    fb_up = ((hs > lvl_h) & (close <= lvl_h)).astype(float).rolling(4).max()
    fb_dn = ((ls_ < lvl_l) & (close >= lvl_l)).astype(float).rolling(4).max()
    f["false_breakout"] = (fb_dn - fb_up).to_numpy()[last]
    up_lvl = lvl_h.where(brk == 1).ffill(limit=16)
    dn_lvl = lvl_l.where(brk == -1).ffill(limit=16)
    atr_s = ind["atr"]
    near_up = (ls_ <= up_lvl + 0.25 * atr_s).astype(float).rolling(16, min_periods=1).max()
    near_dn = (hs >= dn_lvl - 0.25 * atr_s).astype(float).rolling(16, min_periods=1).max()
    retest = np.where((near_up > 0) & (close > up_lvl), 1.0, 0.0) - np.where((near_dn > 0) & (close < dn_lvl), 1.0, 0.0)
    f["retest"] = retest[last]
    hi80, lo80 = hs.rolling(80).max().to_numpy(), ls_.rolling(80).min().to_numpy()
    f["dist_hi"] = (hi80[last] - c[last]) / a
    f["dist_lo"] = (c[last] - lo80[last]) / a
    rng = (hs - ls_).replace(0, np.nan)
    wick = ((hs - ind[["o", "c"]].max(axis=1)) - (ind[["o", "c"]].min(axis=1) - ls_)) / rng
    f["wick"] = wick.fillna(0).rolling(3).mean().to_numpy()[last]
    vol = ind["v"]
    med96 = vol.rolling(96).median().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        rvol = v[last] / med96[last]
        f["rvol"] = np.where(np.isfinite(rvol), rvol, np.nan)
        f["vol_chg"] = np.log(vol.rolling(20).mean().to_numpy()[last] / vol.rolling(100).mean().to_numpy()[last])
    f["vol_dir_corr"] = window_spearman(np.sign(c - o), v, last, 20)
    f["breakout_vol"] = f["breakout"] * (f["rvol"] >= 1.5)
    f["atr_rel"] = s1
    f["atr_pct"] = window_rank(atr, last, 300)
    bbw = (4 * close.rolling(20).std(ddof=0) / close.rolling(20).mean()).to_numpy()
    f["bb_pct"] = window_rank(bbw, last, 300)
    r = (hs - ls_)
    f["expansion"] = (r.rolling(8).mean() / r.shift(8).rolling(22).mean()).to_numpy()[last]
    f["shock"] = np.abs(lr[last] - lr[last - 1]) / s1
    return pd.DataFrame(f)


def candles_to_frame(candles: list[dict], now: float) -> pd.DataFrame:
    """API mum listesi → 15m çerçeve; açık mum (açılış + 900 > now) atılır, eksik mumlar NaN olur."""
    rows = [c for c in candles if c["time"] + 900 <= now]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame({"t": [int(c["time"]) for c in rows], "o": [float(c["open"]) for c in rows],
                       "h": [float(c["high"]) for c in rows], "l": [float(c["low"]) for c in rows],
                       "c": [float(c["close"]) for c in rows], "v": [float(c["volume"]) for c in rows]})
    df = df.drop_duplicates("t").sort_values("t").set_index("t")
    full = np.arange(df.index[0], df.index[-1] + 900, 900)
    return df.reindex(full)
