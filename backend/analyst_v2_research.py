"""Analist V2 – yön tahmini araştırması (onkayit-v2, yalnız geliştirme aşaması).

Kullanıcı kararı (2026-11-04 14:06): ileriye dönük test ve VPS yapılmayacak. Bu araç yalnız ön kaydın geliştirme
bölümlerini (4, 5, 6, 8.2, 8.3, 9-S1/S2) geçmiş veriyle uygular. Sonuçlar "geçmiş veride test edildi" niteliğindedir;
ön kaydın tanımladığı bağımsız (ileriye dönük) test yapılmamıştır.

Canlı sistemle ilişkisi yoktur: analysis.py, emir, stop/TP, risk koduna dokunmaz; hiçbir API'ye bağlanmaz.
2026-05-01 ve sonrasına ait hiçbir arşiv okunmaz (K2).

Kullanım:
  python analyst_v2_research.py integrity --data ~/kais-data/analyst --out ~/kais-data/v2
  python analyst_v2_research.py build     --data ~/kais-data/analyst --out ~/kais-data/v2
  python analyst_v2_research.py kfix      --out ~/kais-data/v2
  python analyst_v2_research.py scan      --out ~/kais-data/v2
  python analyst_v2_research.py models    --out ~/kais-data/v2
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

M15, H1, H4 = 900, 3600, 14400
SYMBOLS = ("BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT DOGEUSDT ADAUSDT AVAXUSDT LINKUSDT LTCUSDT DOTUSDT TRXUSDT BCHUSDT "
           "NEARUSDT SUIUSDT APTUSDT ARBUSDT OPUSDT FILUSDT ATOMUSDT ETCUSDT UNIUSDT AAVEUSDT INJUSDT TIAUSDT SEIUSDT "
           "WLDUSDT 1000PEPEUSDT ENAUSDT HBARUSDT").split()
LOAD_FIRST, LOAD_LAST = "2020-01", "2026-04"           # 2026-05 ve sonrası asla okunmaz
DEV_START = 1601510400                                  # 2020-10-01 00:00 UTC
DEV_END = 1777593600                                    # 2026-05-01 00:00 UTC (hariç; etiket ufku da bunu geçemez)
K_WINDOW_END = 1640995200                               # 2022-01-01
HORIZONS = {"1s": 4, "4s": 16, "24s": 96}               # 15m mum sayısı
COST = 0.0016
K_QUANTILE = 0.40
MIN_BARS, PREF_BARS = 250, 400
UNIVERSE_4H = 400
BOOT_B, BOOT_BLOCK, BOOT_SEED = 2000, 7, 20261105
C_GRID = (0.01, 0.1, 1.0)
CLASSES = ("LONG", "SHORT", "BEKLE")


def month_list(first: str, last: str) -> list[str]:
    y, m = map(int, first.split("-"))
    ly, lm = map(int, last.split("-"))
    out = []
    while (y, m) <= (ly, lm):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def ts(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


# ------------------------------------------------------------------ veri ve bütünlük
def read_archive(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as zf:
        raw = zf.read(zf.namelist()[0]).decode()
    df = pd.read_csv(io.StringIO(raw), header=None, usecols=range(6), names=["t", "o", "h", "l", "c", "v"], dtype=str)
    df = df[pd.to_numeric(df["t"], errors="coerce").notna()].astype(float)
    t = df["t"].to_numpy()
    t = np.where(t >= 1e15, t / 1e6, np.where(t >= 1e12, t / 1e3, t))
    df["t"] = t.astype(np.int64)
    return df


def load_coin(root: Path, symbol: str) -> tuple[pd.DataFrame, dict]:
    files, parts = [], []
    for month in month_list(LOAD_FIRST, LOAD_LAST):
        path = root / "klines" / symbol / "15m" / f"{symbol}-15m-{month}.zip"
        if path.exists():
            files.append({"month": month, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            parts.append(read_archive(path))
    report = {"symbol": symbol, "archives": files}
    if not parts:
        return pd.DataFrame(), report
    df = pd.concat(parts, ignore_index=True)
    assert df["t"].max() < DEV_END, "2026-05 sonrası veri yüklenmemeli"
    exact_dup = int(df.duplicated().sum())
    df = df.drop_duplicates()
    conflict = int(df["t"].duplicated().sum())
    if conflict:
        raise ValueError(f"{symbol}: aynı zamanlı çelişkili {conflict} mum")
    df = df.sort_values("t").set_index("t")
    bad_ohlc = int(((df["l"] > df[["o", "c"]].min(axis=1)) | (df[["o", "c"]].max(axis=1) > df["h"]) | (df["v"] < 0)).sum())
    nonfinite = int((~np.isfinite(df.to_numpy())).sum())
    off_grid = int((df.index % M15 != 0).sum())
    full = np.arange(df.index[0], df.index[-1] + M15, M15)
    missing = np.setdiff1d(full, df.index.to_numpy())
    gaps = []
    if len(missing):
        breaks = np.flatnonzero(np.diff(missing) != M15)
        starts = np.r_[missing[0], missing[breaks + 1]]
        ends = np.r_[missing[breaks], missing[-1]]
        gaps = [{"from": int(s), "to": int(e), "bars": int((e - s) // M15 + 1)} for s, e in zip(starts, ends)]
    logret = np.log(df["c"]).diff()
    sd = logret.rolling(2000, min_periods=200).std()
    report.update(bars=int(len(df)), first=int(df.index[0]), last=int(df.index[-1]), exact_duplicates=exact_dup,
                  bad_ohlc=bad_ohlc, nonfinite=nonfinite, off_grid=off_grid, gaps=gaps, missing_bars=int(len(missing)),
                  zero_volume=int((df["v"] == 0).sum()), extreme_20sigma=int((logret.abs() > 20 * sd).sum()))
    return df.reindex(full), report


def aggregate(df: pd.DataFrame, step: int) -> pd.DataFrame:
    """Yalnız tam ve kesintisiz gruplar; eksik grup NaN kalır."""
    size = step // M15
    key = (df.index // step) * step
    g = df.groupby(key)
    out = pd.DataFrame({"o": g["o"].first(), "h": g["h"].max(), "l": g["l"].min(), "c": g["c"].last(), "v": g["v"].sum(),
                        "n": g["c"].count()})
    out.loc[out["n"] != size, ["o", "h", "l", "c", "v"]] = np.nan
    full = np.arange(out.index[0], out.index[-1] + step, step)
    return out.reindex(full).drop(columns="n")


# ------------------------------------------------------------------ göstergeler
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
    # Onay: sağdaki 3 mum kapandıktan sonra (i+3) bilinir.
    ph_conf = h.where(ph).shift(3)
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
    """Kesintisiz kapanmış mum sayısı (boşluktan sonra sıfırlanır)."""
    ok = df["c"].notna().astype(int)
    grp = (ok == 0).cumsum()
    return ok.groupby(grp).cumsum()


def window_rank(values: np.ndarray, idx: np.ndarray, w: int) -> np.ndarray:
    """values[idx] değerinin, kendisi dahil son w değer içindeki ≤ payı (yüzdelik). NaN pencere → NaN."""
    out = np.full(len(idx), np.nan)
    ok = idx >= w - 1
    sub = idx[ok]
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


def tf_features(ind: pd.DataFrame, at: np.ndarray, prefix: str) -> pd.DataFrame:
    """Çoklu zaman dilimi (1h/4h) özellikleri; at = kullanılacak mumların konum indeksi."""
    a = ind["atr"].to_numpy()[at]
    age = segment_age(ind).to_numpy()[at]
    e200ok = age >= PREF_BARS
    out = pd.DataFrame({
        f"{prefix}_c_e20": (ind["c"].to_numpy()[at] - ind["e20"].to_numpy()[at]) / a,
        f"{prefix}_e20_e50": (ind["e20"].to_numpy()[at] - ind["e50"].to_numpy()[at]) / a,
        f"{prefix}_e50_e200": np.where(e200ok, (ind["e50"].to_numpy()[at] - ind["e200"].to_numpy()[at]) / a, 0.0),
        f"{prefix}_rsi": ind["rsi"].to_numpy()[at] - 50,
        f"{prefix}_macd": ind["hist"].to_numpy()[at] / a,
        f"{prefix}_structure": ind["structure"].to_numpy()[at],
        f"{prefix}_atr_pct": window_rank(ind["atr"].to_numpy(), at, 300),
    })
    missing = (age < MIN_BARS) | out.isna().any(axis=1).to_numpy()
    out.loc[missing, :] = 0.0
    out[f"{prefix}_eksik"] = missing.astype(float)
    return out


def coin_table(df15: pd.DataFrame, btc: pd.DataFrame | None) -> pd.DataFrame:
    ind = base_indicators(df15)
    times = ind.index.to_numpy()
    age = segment_age(ind).to_numpy()
    c, h, l, o, v = (ind[k].to_numpy() for k in "chlov")
    atr = ind["atr"].to_numpy()
    # Karar anları: saat başı t; son kapanmış mum açılışı t-900.
    last = np.flatnonzero((times % H1) == H1 - M15)
    t = times[last] + M15
    keep = (t >= DEV_START) & (t < DEV_END) & (age[last] >= MIN_BARS) & np.isfinite(c[last])
    last, t = last[keep], t[keep]
    s1 = atr[last] / c[last]                                    # σ₁ = ATR / kapanış
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
    lvl_h = hs.shift(3).rolling(80).max()                        # önceki 80 mum (son 3 hariç)
    lvl_l = ls_.shift(3).rolling(80).min()
    close = ind["c"]
    brk = np.where(close > lvl_h, 1.0, np.where(close < lvl_l, -1.0, 0.0))
    brk[lvl_h.isna().to_numpy()] = np.nan
    f["breakout"] = brk[last]
    fb_up = ((hs > lvl_h) & (close <= lvl_h)).astype(float).rolling(4).max()
    fb_dn = ((ls_ < lvl_l) & (close >= lvl_l)).astype(float).rolling(4).max()
    f["false_breakout"] = (fb_dn - fb_up).to_numpy()[last]
    # Retest: son 16 mumda kırılım; kırılan seviyeye 0,25 ATR yaklaşıp kapanışla tutunma.
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
    feat = pd.DataFrame(f)
    # Çoklu zaman dilimi
    for step, prefix in ((H1, "h1"), (H4, "h4")):
        agg = aggregate(df15, step)
        aind = base_indicators(agg)
        at_open = ((t // step) - 1) * step                      # t'de kapanmış son büyük mum
        posn = np.searchsorted(aind.index.to_numpy(), at_open)
        posn = np.clip(posn, 0, len(aind) - 1)
        valid = aind.index.to_numpy()[posn] == at_open
        tff = tf_features(aind, posn, prefix)
        tff.loc[~valid, :] = 0.0
        tff.loc[~valid, f"{prefix}_eksik"] = 1.0
        feat = pd.concat([feat, tff.reset_index(drop=True)], axis=1)
        if prefix == "h4":
            cnt4 = aind["c"].notna().cumsum().to_numpy()[posn]
            universe = valid & (cnt4 >= UNIVERSE_4H)
    # BTC piyasa bağlamı
    if btc is not None:
        feat = pd.concat([feat, btc.reindex(t).reset_index(drop=True)], axis=1)
    else:
        for name in BTC_COLS:
            feat[name] = 0.0
    # Etiketler
    tpos = last + 1                                             # açılışı t olan mum
    okmask = ind["c"].notna().to_numpy().astype(np.int64)
    cum = np.r_[0, np.cumsum(okmask)]
    out = pd.DataFrame({"t": t})
    for name, n in HORIZONS.items():
        end = tpos + n - 1
        inside = end < len(c)
        endc = np.clip(end, 0, len(c) - 1)
        full = inside & ((cum[np.clip(end + 1, 0, len(cum) - 1)] - cum[tpos]) == n) & (times[endc] + M15 <= DEV_END)
        p0 = np.where(inside, o[np.clip(tpos, 0, len(o) - 1)], np.nan)
        ph = np.where(full, c[endc], np.nan)
        out[f"r_{name}"] = np.log(ph / p0)
        out[f"sig_{name}"] = s1 * math.sqrt(n)
        past = np.clip(last - n, 0, None)
        out[f"past_{name}"] = np.where(last - n >= 0, lr[last] - lr[past], np.nan)
    feat = pd.concat([out, feat], axis=1)
    feat["universe"] = universe
    return feat


BTC_COLS = ["btc_c_e20", "btc_e20_e50", "btc_rsi", "btc_ret16", "btc_atr_pct"]


def btc_context(df15: pd.DataFrame) -> pd.DataFrame:
    ind = base_indicators(df15)
    times = ind.index.to_numpy()
    last = np.flatnonzero((times % H1) == H1 - M15)
    t = times[last] + M15
    c, a = ind["c"].to_numpy(), ind["atr"].to_numpy()
    lr = np.log(ind["c"]).to_numpy()
    out = pd.DataFrame({
        "btc_c_e20": (c[last] - ind["e20"].to_numpy()[last]) / a[last],
        "btc_e20_e50": (ind["e20"].to_numpy()[last] - ind["e50"].to_numpy()[last]) / a[last],
        "btc_rsi": ind["rsi"].to_numpy()[last] - 50,
        "btc_ret16": (lr[last] - lr[last - 16]) / (a[last] / c[last]),
        "btc_atr_pct": window_rank(a, last, 300),
    }, index=t)
    return out


GROUPS = {
    "trend": ["c_e20", "e20_e50", "e50_e200", "e20_slope", "e50_slope", "since_cross", "structure", "adx"],
    "momentum": ["rsi", "rsi_chg", "macd", "macd_chg", "ret1", "ret4", "ret16", "accel", "divergence"],
    "yapi": ["breakout", "false_breakout", "retest", "dist_hi", "dist_lo", "wick"],
    "hacim": ["rvol", "vol_chg", "vol_dir_corr", "breakout_vol"],
    "volatilite": ["atr_rel", "atr_pct", "bb_pct", "expansion", "shock"],
    "mtf": [f"{p}_{k}" for p in ("h1", "h4") for k in ("c_e20", "e20_e50", "e50_e200", "rsi", "macd", "structure", "atr_pct", "eksik")],
    "btc": BTC_COLS,
}
FEATURES = [f for g in GROUPS.values() for f in g]


# ------------------------------------------------------------------ komutlar
def cmd_integrity(data: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    reports = []
    for sym in SYMBOLS:
        _, rep = load_coin(data, sym)
        reports.append(rep)
        print(sym, rep.get("bars"), "gaps", len(rep.get("gaps", [])), "bad", rep.get("bad_ohlc"), flush=True)
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "load": [LOAD_FIRST, LOAD_LAST],
                "symbols": reports}
    text = json.dumps(manifest, indent=1)
    (out / "integrity.json").write_text(text)
    (out / "integrity.sha256").write_text(hashlib.sha256(text.encode()).hexdigest() + "\n")


def cmd_build(data: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    btc_df, _ = load_coin(data, "BTCUSDT")
    btc = btc_context(btc_df)
    tables = []
    for sym in SYMBOLS:
        started = time.perf_counter()
        df, _ = load_coin(data, sym)
        if df.empty:
            continue
        tab = coin_table(df, None if sym == "BTCUSDT" else btc)
        tab.insert(0, "symbol", sym)
        tables.append(tab)
        print(sym, len(tab), f"{time.perf_counter() - started:.1f}s", flush=True)
    full = pd.concat(tables, ignore_index=True)
    full.to_parquet(out / "table.parquet") if _has_parquet() else full.to_pickle(out / "table.pkl")
    print("rows", len(full))


def _has_parquet() -> bool:
    try:
        import pyarrow  # noqa: F401
        return True
    except ImportError:
        return False


def load_full(out: Path) -> pd.DataFrame:
    p = out / "table.parquet"
    return pd.read_parquet(p) if p.exists() else pd.read_pickle(out / "table.pkl")


def labels(df: pd.DataFrame, name: str, k: float) -> np.ndarray:
    r, sig = df[f"r_{name}"].to_numpy(), df[f"sig_{name}"].to_numpy()
    y = np.full(len(df), 2)
    y[(r - COST) >= k * sig] = 0
    y[(-r - COST) >= k * sig] = 1
    y[~np.isfinite(r)] = -1
    return y


def cmd_kfix(out: Path) -> None:
    df = load_full(out)
    df = df[df["universe"]]
    res = {}
    for name in HORIZONS:
        m = (df["t"] >= DEV_START) & (df["t"] + HORIZONS[name] * M15 <= K_WINDOW_END) & df[f"r_{name}"].notna()
        sub = df[m]
        u = (np.abs(sub[f"r_{name}"]) - COST) / sub[f"sig_{name}"]
        k = float(np.quantile(u, K_QUANTILE))
        res[name] = {"k": round(max(k, 0.0), 4), "raw": k, "n": int(len(sub)), "coins": int(sub["symbol"].nunique())}
    res["computed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (out / "k_fixed.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


# ------------------------------------------------------------------ değerlendirme altyapısı
def folds() -> list[tuple[int, int]]:
    out = []
    y, m = 2022, 1
    while (y, m) <= (2025, 10):
        start = ts(f"{y:04d}-{m:02d}-01")
        ny, nm = (y + (m + 2) // 12, (m + 2) % 12 + 1)
        out.append((start, ts(f"{ny:04d}-{nm:02d}-01")))
        y, m = ny, nm
    out.append((ts("2026-01-01"), DEV_END))
    assert len(out) == 17
    return out


def logloss_rows(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    return -np.log(np.clip(p[np.arange(len(y)), y], 1e-15, 1 - 1e-15))


class TwoWay:
    """İki yönlü küme bootstrap: 7 günlük dairesel bloklar × coinler (bağımsız, iadeli)."""

    def __init__(self, days: np.ndarray, coins: np.ndarray, seed: int = BOOT_SEED, b: int = BOOT_B):
        self.day_keys, self.day_id = np.unique(days, return_inverse=True)
        self.coin_keys, self.coin_id = np.unique(coins, return_inverse=True)
        rng = np.random.default_rng(seed)
        nd, nc = len(self.day_keys), len(self.coin_keys)
        k = math.ceil(nd / BOOT_BLOCK)
        starts = rng.integers(0, nd, size=(b, k))
        idx = ((starts[:, :, None] + np.arange(BOOT_BLOCK)) % nd).reshape(b, -1)[:, :nd]
        self.wd = np.zeros((b, nd))
        np.add.at(self.wd, (np.repeat(np.arange(b), idx.shape[1]), idx.ravel()), 1)
        self.wc = rng.multinomial(nc, np.full(nc, 1 / nc), size=b).astype(float)

    def mean(self, values: np.ndarray) -> tuple[float, float, float, np.ndarray]:
        nd, nc = len(self.day_keys), len(self.coin_keys)
        s = np.zeros((nd, nc)); n = np.zeros((nd, nc))
        np.add.at(s, (self.day_id, self.coin_id), values)
        np.add.at(n, (self.day_id, self.coin_id), 1)
        num = ((self.wd @ s) * self.wc).sum(1)
        den = ((self.wd @ n) * self.wc).sum(1)
        boot = num / den
        point = values.mean()
        lo, hi = np.percentile(boot, [2.5, 97.5])
        return float(point), float(lo), float(hi), boot


def ece(p: np.ndarray, y: np.ndarray, k: int) -> float:
    order = np.argsort(p[:, k], kind="stable")
    n = len(order)
    b = (np.arange(n) * 10) // n
    pk, yk = p[order, k], (y[order] == k).astype(float)
    tot = 0.0
    for i in range(10):
        m = b == i
        tot += m.sum() / n * abs(yk[m].mean() - pk[m].mean())
    return float(tot)


def cal_slope(p: np.ndarray, y: np.ndarray, k: int) -> float:
    from sklearn.linear_model import LogisticRegression
    pk = np.clip(p[:, k], 1e-6, 1 - 1e-6)
    x = np.log(pk / (1 - pk))[:, None]
    m = LogisticRegression(penalty=None, max_iter=1000).fit(x, (y == k).astype(int))
    return float(m.coef_[0, 0])


def auc_ls(p: np.ndarray, y: np.ndarray) -> float:
    from sklearn.metrics import roc_auc_score
    m = y < 2
    if m.sum() < 10 or len(np.unique(y[m])) < 2:
        return float("nan")
    return float(roc_auc_score((y[m] == 0).astype(int), p[m, 0] - p[m, 1]))


def horizon_frame(df: pd.DataFrame, name: str, k: float) -> pd.DataFrame:
    d = df[df["universe"]].copy()
    d["y"] = labels(d, name, k)
    d = d[(d["y"] >= 0) & (d["t"] + HORIZONS[name] * M15 <= DEV_END)]
    d = d[np.isfinite(d[FEATURES].to_numpy()).all(axis=1)]
    d["day"] = (d["t"] // 86400).astype(np.int64)
    return d.reset_index(drop=True)


def train_mask(d: pd.DataFrame, start: int, name: str) -> np.ndarray:
    return (d["t"] + HORIZONS[name] * M15 + 86400 <= start).to_numpy()


def class_freq(y: np.ndarray) -> np.ndarray:
    return np.bincount(y, minlength=3) / len(y)


def rule_class(d: pd.DataFrame, which: str, name: str, k: float) -> np.ndarray:
    if which == "B1":
        r = d[f"past_{name}"].to_numpy(); sig = d[f"sig_{name}"].to_numpy()
        cls = np.full(len(d), 2); cls[(r - COST) >= k * sig] = 0; cls[(-r - COST) >= k * sig] = 1
        return cls
    s = np.sign(d["e20_e50"]) + np.sign(d["macd"]) + np.sign(d["rsi"])
    return np.where(s >= 2, 0, np.where(s <= -2, 1, 2))


def conditional_probs(train_cls, train_y, test_cls) -> np.ndarray:
    base = class_freq(train_y)
    table = np.tile(base, (3, 1))
    for c in range(3):
        m = train_cls == c
        if m.sum() >= 50:
            table[c] = class_freq(train_y[m])
    return table[test_cls]


def fit_lr(x, y, c):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(StandardScaler(), LogisticRegression(C=c, max_iter=300, tol=1e-4)).fit(x, y)


def vector_scaling(p_cal, y_cal):
    from sklearn.linear_model import LogisticRegression
    return LogisticRegression(C=1e6, max_iter=1000).fit(np.log(np.clip(p_cal, 1e-12, 1)), y_cal)


def split_slices(tr_idx: np.ndarray, d: pd.DataFrame, name: str):
    t = d["t"].to_numpy()[tr_idx]
    order = tr_idx[np.argsort(t, kind="stable")]
    tt = d["t"].to_numpy()[order]
    a, b = np.quantile(tt, [0.70, 0.85])
    gap = HORIZONS[name] * M15 + 86400
    fit = order[tt + gap <= a]
    hp = order[(tt >= a) & (tt + gap <= b)]
    cal = order[tt >= b]
    return fit, hp, cal


def run_m1(d, tr_idx, te_idx, name, cols):
    fit, hp, cal = split_slices(tr_idx, d, name)
    x, y = d[cols].to_numpy(np.float64), d["y"].to_numpy()
    best = None
    for c in C_GRID:
        m = fit_lr(x[fit], y[fit], c)
        ll = logloss_rows(m.predict_proba(x[hp]), y[hp]).mean()
        if best is None or ll < best[0]:
            best = (ll, c, m)
    cal_model = vector_scaling(best[2].predict_proba(x[cal]), y[cal])
    p = cal_model.predict_proba(np.log(np.clip(best[2].predict_proba(x[te_idx]), 1e-12, 1)))
    return p, {"C": best[1]}, best[2]


def run_m2(d, tr_idx, te_idx, name, cols):
    from sklearn.ensemble import HistGradientBoostingClassifier
    fit, hp, cal = split_slices(tr_idx, d, name)
    x, y = d[cols].to_numpy(np.float64), d["y"].to_numpy()
    m = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=1.0,
                                       early_stopping=False, max_iter=25, warm_start=True, random_state=0)
    best_ll, best_iter, worse = np.inf, 25, 0
    while m.max_iter <= 600:
        m.fit(x[fit], y[fit])
        ll = logloss_rows(m.predict_proba(x[hp]), y[hp]).mean()
        if ll < best_ll - 1e-6:
            best_ll, best_iter, worse = ll, m.max_iter, 0
        else:
            worse += 1
            if worse >= 2:
                break
        m.max_iter += 25
    final = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=1.0,
                                           early_stopping=False, max_iter=best_iter, random_state=0).fit(x[fit], y[fit])
    cal_model = vector_scaling(final.predict_proba(x[cal]), y[cal])
    p = cal_model.predict_proba(np.log(np.clip(final.predict_proba(x[te_idx]), 1e-12, 1)))
    return p, {"iter": best_iter}, final


def oos(d: pd.DataFrame, name: str, k: float, which: str, cols=None, log=None) -> tuple[np.ndarray, list]:
    """Tüm katlar için dış-örneklem olasılıkları (test katlarında olmayan satırlar NaN)."""
    p_all = np.full((len(d), 3), np.nan)
    info = []
    x = d[cols].to_numpy(np.float64) if cols else None
    y = d["y"].to_numpy()
    t = d["t"].to_numpy()
    for start, end in folds():
        te = np.flatnonzero((t >= start) & (t < end))
        tr = np.flatnonzero(train_mask(d, start, name))
        if not len(te):
            continue
        started = time.perf_counter()
        if which == "B0":
            p = np.tile(class_freq(y[tr]), (len(te), 1)); meta = {}
        elif which in ("B1", "B2"):
            cls = rule_class(d, which, name, k)
            p = conditional_probs(cls[tr], y[tr], cls[te]); meta = {}
        elif which == "S1":
            m = fit_lr(x[tr], y[tr], 0.1)
            p = m.predict_proba(x[te]); meta = {}
        elif which == "M1":
            p, meta, _ = run_m1(d, tr, te, name, cols)
        elif which == "M2":
            p, meta, _ = run_m2(d, tr, te, name, cols)
        p_all[te] = p
        info.append({"start": start, "n_train": int(len(tr)), "n_test": int(len(te)), **meta,
                     "sec": round(time.perf_counter() - started, 1)})
        if log:
            log(f"  {which} {name} fold {datetime.fromtimestamp(start, timezone.utc):%Y-%m} {info[-1]}")
    return p_all, info


def per_fold_ll(d, p, folds_):
    y, t = d["y"].to_numpy(), d["t"].to_numpy()
    res = []
    for start, end in folds_:
        m = (t >= start) & (t < end)
        res.append(float(logloss_rows(p[m], y[m]).mean()) if m.any() else float("nan"))
    return np.array(res)


def cmd_scan(out: Path) -> None:
    df = load_full(out)
    kfix = json.loads((out / "k_fixed.json").read_text())
    log = lambda s: print(s, flush=True)  # noqa: E731
    report = {}
    for name in HORIZONS:
        k = kfix[name]["k"]
        d = horizon_frame(df, name, k)
        test = (d["t"] >= folds()[0][0]).to_numpy()
        boot = TwoWay(d.loc[test, "day"].to_numpy(), d.loc[test, "symbol"].to_numpy())
        y = d["y"].to_numpy()
        p0, _ = oos(d, name, k, "B0")
        ll0 = logloss_rows(p0[test], y[test])
        rows = {}
        for g, cols in GROUPS.items():
            pg, _ = oos(d, name, k, "S1", cols)
            llg = logloss_rows(pg[test], y[test])
            point, lo, hi, _ = boot.mean(ll0 - llg)
            rows[g] = {"delta_ll": point, "ci": [lo, hi], "signal": lo > 0, "auc_ls": auc_ls(pg[test], y[test])}
            log(f"{name} {g}: ΔLL {point:+.5f} [{lo:+.5f}, {hi:+.5f}] AUC_LS {rows[g]['auc_ls']:.4f}")
        report[name] = {"k": k, "n_test": int(test.sum()), "class_share_test": class_freq(y[test]).tolist(),
                        "groups": rows, "S1_stop": not any(r["signal"] for r in rows.values())}
    (out / "scan.json").write_text(json.dumps(report, indent=1))


def cmd_models(out: Path) -> None:
    df = load_full(out)
    kfix = json.loads((out / "k_fixed.json").read_text())
    scan = json.loads((out / "scan.json").read_text())
    log = lambda s: print(s, flush=True)  # noqa: E731
    report = {}
    for name in HORIZONS:
        if scan[name]["S1_stop"]:
            report[name] = {"status": "S1: OHLCV ile tahmin edilebilir yön bulunamadı; modellenmedi"}
            log(f"{name}: S1 durdurdu")
            continue
        k = kfix[name]["k"]
        d = horizon_frame(df, name, k)
        test = (d["t"] >= folds()[0][0]).to_numpy()
        y = d["y"].to_numpy()
        boot = TwoWay(d.loc[test, "day"].to_numpy(), d.loc[test, "symbol"].to_numpy())
        probs, infos = {}, {}
        for which in ("B0", "B1", "B2", "M1", "M2"):
            probs[which], infos[which] = oos(d, name, k, which, FEATURES if which in ("M1", "M2") else None, log)
        ll = {w: logloss_rows(p[test], y[test]) for w, p in probs.items()}
        res = {"k": k, "n_test": int(test.sum()), "days": int(d.loc[test, "day"].nunique()),
               "class_share_test": class_freq(y[test]).tolist(), "models": {}}
        for w in probs:
            p = probs[w][test]
            pred = p.argmax(1)
            prec = {c: float((y[pred == i] == i).mean()) if (pred == i).any() else None for i, c in enumerate(CLASSES)}
            rec = {c: float((pred[y == i] == i).mean()) for i, c in enumerate(CLASSES)}
            from sklearn.metrics import balanced_accuracy_score, f1_score
            fold_ll = per_fold_ll(d, probs[w], folds())
            fold_b0 = per_fold_ll(d, probs["B0"], folds())
            res["models"][w] = {
                "logloss": float(ll[w].mean()),
                "brier": float(((p - np.eye(3)[y[test]]) ** 2).sum(1).mean()),
                "auc_ls": auc_ls(p, y[test]), "precision": prec, "recall": rec,
                "macro_f1": float(f1_score(y[test], pred, average="macro")),
                "balanced_acc": float(balanced_accuracy_score(y[test], pred)),
                "ece": [ece(p, y[test], i) for i in range(3)], "slope": [cal_slope(p, y[test], i) for i in range(3)],
                "pred_share": np.bincount(pred, minlength=3).tolist(),
                "folds_better_than_B0": int((fold_ll < fold_b0 - 1e-12).sum()),
                "info": infos[w],
            }
        for w in ("B1", "B2", "M1", "M2"):
            for base in ("B0", "B1"):
                if w == base:
                    continue
                point, lo, hi, _ = boot.mean(ll[base] - ll[w])
                res["models"][w][f"dLL_vs_{base}"] = [point, lo, hi]
        point, lo, hi, _ = boot.mean(ll["M1"] - ll["M2"])
        res["M2_vs_M1"] = [point, lo, hi]
        cands = [w for w in ("M1", "M2") if res["models"][w]["dLL_vs_B0"][1] > 0 and res["models"][w]["dLL_vs_B1"][1] > 0]
        sel = None
        if "M1" in cands and "M2" in cands:
            sel = "M2" if lo > 0 else "M1"
        elif cands:
            sel = cands[0]
        if sel and res["models"][sel]["folds_better_than_B0"] < 12:
            res["selection_note"] = f"{sel} P6'yı sağlamadı ({res['models'][sel]['folds_better_than_B0']}/17)"
            sel = None
        res["selected"] = sel
        # Ekonomik (betimleyici): seçilen veya en iyi model; LONG/SHORT tahminlerinin en güvenli %20'si
        best = sel or min(("M1", "M2"), key=lambda w: res["models"][w]["logloss"])
        p = probs[best][test]
        pred = p.argmax(1)
        conf = p[np.arange(len(p)), pred]
        dirm = pred < 2
        if dirm.any():
            tau = float(np.quantile(conf[dirm], 0.80))
            take = dirm & (conf >= tau)
            r = d.loc[test, f"r_{name}"].to_numpy()
            net = np.where(pred == 0, r, -r) - COST
            sig = d.loc[test, f"sig_{name}"].to_numpy()
            b2 = TwoWay(d.loc[test, "day"].to_numpy()[take], d.loc[test, "symbol"].to_numpy()[take])
            pt, lo2, hi2, _ = b2.mean(net[take] * 1e4)
            res["economic"] = {"model": best, "tau": tau, "n": int(take.sum()), "mean_bp": pt, "ci_bp": [lo2, hi2],
                               "mean_sigma": float((net[take] / sig[take]).mean()), "edge": bool(pt > 0 and lo2 > 0)}
        # Grup ablation ve MTF merdiveni (betimleyici). Hesap süresi nedeniyle S1 düzeni kullanılır:
        # LR, C = 0,1, tüm eğitim penceresi, kalibrasyonsuz; tam küme de aynı düzenle ölçülür (karşılaştırılabilir).
        p_all, _ = oos(d, name, k, "S1", FEATURES)
        ll_all = logloss_rows(p_all[test], y[test])
        abl = {}
        for g in GROUPS:
            cols = [c for c in FEATURES if c not in GROUPS[g]]
            pg, _ = oos(d, name, k, "S1", cols)
            point, lo, hi, _ = boot.mean(logloss_rows(pg[test], y[test]) - ll_all)
            abl[f"-{g}"] = [point, lo, hi]
            log(f"{name} ablation -{g}: {point:+.5f} [{lo:+.5f}, {hi:+.5f}]")
        base15 = [c for c in FEATURES if c not in GROUPS["mtf"]]
        ladder = {}
        for label, cols in (("15m", base15), ("15m+1h", base15 + [c for c in GROUPS["mtf"] if c.startswith("h1")])):
            pg, _ = oos(d, name, k, "S1", cols)
            point, lo, hi, _ = boot.mean(ll["B0"] - logloss_rows(pg[test], y[test]))
            ladder[label] = [point, lo, hi]
        point, lo, hi, _ = boot.mean(ll["B0"] - ll_all)
        ladder["15m+1h+4h"] = [point, lo, hi]
        res["ablation"] = abl
        res["mtf_ladder"] = ladder
        report[name] = res
        (out / "models.json").write_text(json.dumps(report, indent=1, default=float))
        log(f"{name}: seçilen {sel}")
    (out / "models.json").write_text(json.dumps(report, indent=1, default=float))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["integrity", "build", "kfix", "scan", "models"])
    ap.add_argument("--data", type=Path, default=Path.home() / "kais-data" / "analyst")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    repo = Path(__file__).resolve().parents[1]
    if a.out.resolve().is_relative_to(repo):
        ap.error("Çıktı klasörü repo dışında olmalı.")
    {"integrity": lambda: cmd_integrity(a.data, a.out), "build": lambda: cmd_build(a.data, a.out),
     "kfix": lambda: cmd_kfix(a.out), "scan": lambda: cmd_scan(a.out), "models": lambda: cmd_models(a.out)}[a.command]()


if __name__ == "__main__":
    main()
