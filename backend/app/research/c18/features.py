"""C18 derivative-data features. Pre-registration: backend/studies/c18-turev-veri-onkayit.md (sections 1, 4, 5).

Time rule (section 5): a decision at time T (= entry candle open = decision-candle close) may only use 5-minute `metrics` rows whose
create_time <= T - 300 s, and funding settlements with calc_time + 300 s <= T. Rolling statistics look only backwards. Invalid rows
(NaN, OI <= 0) become NaN and are NEVER filled; a feature that needs a NaN input is NaN, and the model uses complete rows only.
"""
from __future__ import annotations

import glob
import io
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

STEP = 300                      # metrics grid (s)
DAY = 86400
Z_WIN = 30 * DAY // STEP        # 8640 rows
Z_MIN = int(0.8 * Z_WIN)
FUND_Z = 90                     # 30 days of 8h settlements
LAG = {"1h": 12, "4h": 48, "24h": 288}
DAY_INVALID_MAX = 0.05          # section 1: a day with more than 5% invalid rows is excluded for the group

G1 = ["fund_level", "fund_change", "fund_z", "fund_streak", "oi_chg_1h", "oi_chg_4h", "oi_chg_24h", "oiv_z", "oi_price_div",
      "oi_vol", "fund_x_oi", "oi24_atr"]
G2 = ["ls_top_level", "ls_top_chg_4h", "ls_acc_level", "ls_acc_chg_4h", "taker_ratio"]   # liquidation: not in the archive (probe: HTTP 404)
BASE = ["atr_pct", "atr_percentile", "roc8_atr", "roc16_atr", "ema20_dist_atr", "ema200_dist_atr", "volume_ratio", "body_atr_signed",
        "range_atr", "adx"]
SIGNED_G = ["fund_level", "fund_change", "fund_z", "fund_streak", "oi_price_div", "fund_x_oi", "ls_top_level", "ls_top_chg_4h",
            "ls_acc_level", "ls_acc_chg_4h", "taker_ratio"]
SIGNED_BASE = ["roc8_atr", "roc16_atr", "ema20_dist_atr", "ema200_dist_atr", "body_atr_signed"]
USED = {"G1": ["sum_open_interest_value"], "G2": ["sum_toptrader_long_short_ratio", "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]}


def load_metrics(root: Path, symbol: str) -> pd.DataFrame:
    """5-minute metrics for one symbol on a gap-free grid (missing/invalid rows = NaN, never filled). Index = epoch seconds."""
    parts = []
    for f in sorted(glob.glob(str(root / "metrics" / symbol / "*.zip"))):
        with zipfile.ZipFile(f) as z:
            parts.append(pd.read_csv(io.BytesIO(z.read(z.namelist()[0]))))
    m = pd.concat(parts, ignore_index=True)
    ts = pd.to_datetime(m["create_time"], utc=True).astype("datetime64[s, UTC]").astype("int64")
    m = m.assign(t=ts.to_numpy()).drop_duplicates("t").set_index("t").sort_index()
    for c in ("sum_open_interest", "sum_open_interest_value"):
        m.loc[m[c] <= 0, c] = np.nan
    grid = np.arange(m.index.min(), m.index.max() + STEP, STEP, dtype=np.int64)
    return m.reindex(grid)


def load_funding(root: Path, symbol: str) -> tuple[np.ndarray, np.ndarray]:
    parts = []
    for f in sorted(glob.glob(str(root / "fundingRate" / symbol / "*.zip"))):
        with zipfile.ZipFile(f) as z:
            parts.append(pd.read_csv(io.BytesIO(z.read(z.namelist()[0]))))
    d = pd.concat(parts, ignore_index=True).drop_duplicates("calc_time").sort_values("calc_time")
    return (d["calc_time"].to_numpy() // 1000).astype(np.int64), d["last_funding_rate"].to_numpy(dtype=float)


def invalid_days(m: pd.DataFrame) -> dict[str, set[int]]:
    """Days (UTC day index) with more than 5% invalid rows in any field the group uses. Rows absent from the archive count as invalid."""
    day = (m.index.to_numpy() // DAY).astype(int)
    out: dict[str, set[int]] = {}
    for grp, cols in USED.items():
        bad = np.zeros(len(m), dtype=bool)
        for c in cols:
            bad |= ~np.isfinite(m[c].to_numpy(dtype=float))
        frac = pd.Series(bad.astype(float)).groupby(day).mean()
        out[grp] = {int(d) for d, v in frac.items() if v > DAY_INVALID_MAX}
    return out


def _lag(a: np.ndarray, i: np.ndarray, k: int) -> np.ndarray:
    j = i - k
    out = np.full(len(i), np.nan)
    ok = (j >= 0) & (i >= 0) & (i < len(a))
    out[ok] = a[j[ok]]
    return out


def compute(m: pd.DataFrame, ft: np.ndarray, fr: np.ndarray, quote_vol_24h: pd.Series, at: np.ndarray, atr_pct: np.ndarray,
            price_dir4h: np.ndarray) -> pd.DataFrame:
    """LONG-orientation G1/G2 features for decision times `at` (epoch s). `quote_vol_24h` is indexed by candle close time (known <= at)."""
    t0 = int(m.index[0])
    i = ((at - STEP - t0) // STEP).astype(int)          # last usable row: create_time <= T - 300
    oi = m["sum_open_interest"].to_numpy(dtype=float)
    oiv = m["sum_open_interest_value"].to_numpy(dtype=float)
    cur = np.where((i >= 0) & (i < len(oi)), oiv[np.clip(i, 0, len(oi) - 1)], np.nan)
    out: dict[str, np.ndarray] = {}
    # ---- G1: funding (8h settlements, section 5)
    k = np.searchsorted(ft + STEP, at, side="right") - 1            # last settlement with calc_time + 300 <= T
    ok = k >= 1
    kk = np.clip(k, 0, len(fr) - 1)
    out["fund_level"] = np.where(k >= 0, fr[kk], np.nan)
    out["fund_change"] = np.where(ok, fr[kk] - fr[np.clip(k - 1, 0, None)], np.nan)
    z = np.full(len(at), np.nan)
    sgn = np.sign(fr)
    streak = np.zeros(len(fr))
    for n in range(len(fr)):
        streak[n] = (streak[n - 1] + 1 if n and sgn[n] == sgn[n - 1] and sgn[n] != 0 else 1) * (sgn[n] if sgn[n] != 0 else 0)
    zz = pd.Series(fr).rolling(FUND_Z, min_periods=int(0.8 * FUND_Z)).agg(["mean", "std"])
    good = k >= 0
    sd = zz["std"].to_numpy()[kk]
    z[good] = ((fr[kk] - zz["mean"].to_numpy()[kk]) / np.where(sd > 0, sd, np.nan))[good]
    out["fund_z"] = z
    out["fund_streak"] = np.where(good, streak[kk], np.nan)
    # ---- G1: open interest
    for name, n in LAG.items():
        prev = _lag(oi, i, n)
        now = np.where((i >= 0) & (i < len(oi)), oi[np.clip(i, 0, len(oi) - 1)], np.nan)
        out[f"oi_chg_{name}"] = now / prev - 1.0
    roll = pd.Series(oiv).rolling(Z_WIN, min_periods=Z_MIN).agg(["mean", "std"])
    mu, sdv = roll["mean"].to_numpy(), roll["std"].to_numpy()
    ic = np.clip(i, 0, len(oiv) - 1)
    out["oiv_z"] = np.where((i >= 0) & (i < len(oiv)), (cur - mu[ic]) / np.where(sdv[ic] > 0, sdv[ic], np.nan), np.nan)
    out["oi_price_div"] = np.sign(out["oi_chg_4h"]) * np.sign(price_dir4h)
    qv = quote_vol_24h.reindex(at, method=None).to_numpy(dtype=float)
    out["oi_vol"] = cur / qv
    out["fund_x_oi"] = np.sign(out["fund_level"]) * out["oi_chg_4h"]
    out["oi24_atr"] = out["oi_chg_24h"] / np.where(atr_pct > 0, atr_pct, np.nan)
    # ---- G2: long/short ratios and taker ratio (log ratios; >0 = long-heavy)
    for nm, col in (("ls_top", "sum_toptrader_long_short_ratio"), ("ls_acc", "count_long_short_ratio")):
        lr = np.log(m[col].to_numpy(dtype=float))
        now = np.where((i >= 0) & (i < len(lr)), lr[ic], np.nan)
        out[f"{nm}_level"] = now
        out[f"{nm}_chg_4h"] = now - _lag(lr, i, LAG["4h"])
    tk = np.log(m["sum_taker_long_short_vol_ratio"].to_numpy(dtype=float))
    stack = np.vstack([_lag(tk, i, n) for n in (0, 1, 2)])           # last 3 rows (15 minutes); one NaN row => NaN feature
    out["taker_ratio"] = stack.mean(axis=0)
    df = pd.DataFrame(out)
    df["_oi_ok"] = np.isfinite(cur)
    return df


def orient(df: pd.DataFrame, side: np.ndarray) -> pd.DataFrame:
    """SHORT = mirrored data: every signed feature changes sign (LONG-oriented ratios become 'with the trade' values)."""
    s = np.where(side == "LONG", 1.0, -1.0)
    out = df.copy()
    for c in SIGNED_G:
        if c in out:
            out[c] = out[c].to_numpy(dtype=float) * s
    return out
