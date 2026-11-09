"""C18 dataset builder: joins the C16 decision rows (price features + labels, unchanged) with G1/G2 derivative features."""
from __future__ import annotations

import glob
import io
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from app.research.c18 import features as ft

OOS_START = 1780272000      # 2026-06-01 00:00 UTC
MOVE_BARS = 16


def assert_no_oos(df: pd.DataFrame) -> None:
    if int(df["at"].max()) >= OOS_START:
        raise ValueError("OOS timestamp refused (C18)")


def quote_volume_24h(klines_root: Path, symbol: str) -> pd.Series:
    parts = []
    for f in sorted(glob.glob(str(klines_root / symbol / "15m" / "*.zip"))):
        with zipfile.ZipFile(f) as z:
            raw = z.read(z.namelist()[0]).decode("utf-8-sig")
        d = pd.read_csv(io.StringIO(raw), header=None, skiprows=1 if not raw.split(",")[0].isdigit() else 0)
        parts.append(d[[0, 7]])
    d = pd.concat(parts).drop_duplicates(0).sort_values(0)
    t = d[0].to_numpy(dtype=np.int64)
    t = t // 1000 if t.max() > 1e12 else t
    s = pd.Series(d[7].to_numpy(dtype=float), index=t + 900)       # indexed by candle close time
    full = s.reindex(np.arange(s.index.min(), s.index.max() + 900, 900))
    return full.rolling(96, min_periods=96).sum()                  # a gap => NaN, never filled


def build(split_csv: Path, derivative_root: Path, klines_root: Path) -> pd.DataFrame:
    base = pd.read_csv(split_csv)
    assert_no_oos(base)
    longs = base[base["side"] == "LONG"].set_index(["symbol", "at"])
    rows = []
    for sym, g in base.groupby("symbol"):
        m = ft.load_metrics(derivative_root, sym)
        fts, fr = ft.load_funding(derivative_root, sym)
        bad = ft.invalid_days(m)
        qv = quote_volume_24h(klines_root, sym)
        for side in ("LONG", "SHORT"):
            d = g[g["side"] == side].sort_values("at").reset_index(drop=True)
            at = d["at"].to_numpy(dtype=np.int64)
            lr = longs.loc[sym].reindex(at)
            f = ft.compute(m, fts, fr, qv, at, d["atr_pct"].to_numpy(dtype=float), lr["roc16_atr"].to_numpy(dtype=float))
            f = ft.orient(f, np.array([side] * len(d)))
            day = ((at - ft.STEP) // ft.DAY).astype(int)
            for grp, cols in (("G1", ft.G1), ("G2", ft.G2)):
                excl = np.isin(day, list(bad[grp]))
                f[f"{grp}_day_excluded"] = excl
                f.loc[excl, cols] = np.nan
            d = pd.concat([d, f.drop(columns=["_oi_ok"])], axis=1).copy()
            # movement label (direction-free): a +-2 ATR MARK touch within 16 bars of the entry (LONG-row `hold` <= 16)
            d = d.assign(y_move=((d["hold"] <= MOVE_BARS) & d["hold"].notna()).astype(int))
            rows.append(d)
    out = pd.concat(rows, ignore_index=True)
    assert_no_oos(out)
    return out
