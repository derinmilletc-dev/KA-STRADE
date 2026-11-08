"""KAIS FARMER C16 pipeline helpers (research only). Builds side-aware rows from the structure engine, labels them with the C14/C15
label (+2 ATR before -2 ATR on MARK, entry = open of C+1) and exposes the OOS guard. Nothing here touches production code."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import numpy as np

from app.research import c15_scoring as cs
from app.research.c16 import structure as st

OOS_START = cs.OOS_START
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
SIDES = ("LONG", "SHORT")
BAR = st.BAR


def assert_not_oos(path_or_ts: Any) -> None:
    """Refuses any OOS timestamp or any path that names a month >= 2026-06 (research code must not read the OOS path)."""
    if isinstance(path_or_ts, (int, float)):
        if path_or_ts >= OOS_START:
            raise ValueError("OOS timestamp refused (C16)")
        return
    text = str(path_or_ts)
    for month in ("2026-06", "2026-07", "2026-08", "2026-09"):
        if month in text:
            raise ValueError(f"OOS path refused (C16): {text}")


def guard_manifest(root: Path) -> None:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    late = [a["path"] for a in manifest["archives"] if str(a["month"]) >= "2026-06"]
    if late:
        raise SystemExit(f"OOS archives present in the C16 data root ({len(late)}): refusing")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def archive_range(path: Path) -> tuple[int | None, int | None]:
    """min/max timestamp (epoch s) of the first column of the single CSV inside a zip."""
    assert_not_oos(path)
    with zipfile.ZipFile(path) as z:
        lines = z.read(z.namelist()[0]).decode("utf-8-sig").splitlines()
    vals = []
    for ln in (lines[0], lines[1], lines[-1]):
        head = ln.split(",")[0]
        if head.replace(".", "", 1).isdigit():
            v = float(head)
            vals.append(int(v / 1000) if v >= 1e12 else int(v))
    return (min(vals), max(vals)) if vals else (None, None)


def manifest_entry(root: Path, a: dict[str, Any], split: str) -> dict[str, Any]:
    p = root / a["path"]
    lo, hi = archive_range(p)
    return {"split": split, "symbol": a["symbol"], "kind": a["kind"], "timeframe": a.get("interval"), "month": a["month"], "path": a["path"],
            "sha256": sha256_file(p), "sha256_matches_download_manifest": sha256_file(p) == a.get("sha256"), "min_ts": lo, "max_ts": hi}


# ------------------------------------------------------------------------------------------------ rows
def block_rows(ds: Any, mk: Any, symbol: str, start: int, end: int, first: int, limit: int = 0) -> list[dict[str, Any]]:
    """Both-side rows for every decision of one (block, symbol). `ds.frames[symbol]['15m']` is the contract-price series."""
    assert_not_oos(end - BAR)
    rows15 = [r for r in ds.frames[symbol]["15m"].rows if start <= r["time"] < end]
    t = np.array([r["time"] for r in rows15], dtype=np.int64)
    o, h, l, c, v = (np.array([r[k] for r in rows15], dtype=float) for k in ("open", "high", "low", "close", "volume"))
    mh, ml, mc = mk.mhigh[symbol], mk.mlow[symbol], mk.mclose[symbol]
    feats = {side: st.run_series(t, o, h, l, c, v, side) for side in SIDES}
    out: list[dict[str, Any]] = []
    for at in range(first, end - BAR + 1, BAR):
        d = (at - start) // BAR                  # entry candle = C+1; decision candle C has index d-1
        j = d - 1
        if j not in feats["LONG"] or j not in feats["SHORT"] or d >= mk.n:
            continue
        if limit and len(out) >= 2 * limit:
            break
        atr = feats["LONG"][j]["atr_abs"]
        lab = cs.label_row(mh, ml, mc, d, float(mk.copen[symbol][d]), atr, mk.fund_t[symbol], mk.fund_r[symbol], start)
        for side in SIDES:
            f = feats[side][j]
            key, sgn = side.lower(), (1.0 if side == "LONG" else -1.0)
            row = {"symbol": symbol, "at": at, "side": side, "entry_price": float(mk.copen[symbol][d]), "y": lab.get(f"y_{key}"),
                   "hold": lab.get(f"hold_{key}"), "fund_sum": lab.get(f"fund_sum_{key}"), "exit_idx": lab.get(f"exit_idx_{key}"), "amb": lab.get(f"amb_{key}")}
            for k in (1, 2, 4, 8):
                x = lab.get(f"ret_{k}_atr")
                row[f"ret{k}_d"] = None if x is None else sgn * x
            row.update({k: f[k] for k in st.ALL_FEATURES})
            row.update({k: f[k] for k in st.CUTOFF_FIELDS})
            row["atr_abs"] = f["atr_abs"]
            row["source_first_ts"] = int(t[max(0, j - st.PCT_WIN + 1)])
            out.append(row)
    return out


def check_split(df: Any, split: str) -> None:
    """TRAIN rows must lie before 2026-01-01, VALID rows in [2026-01-01, 2026-06-01); anything else (and any OOS time) is refused."""
    lo, hi = int(df["at"].min()), int(df["at"].max())
    assert_not_oos(hi)
    if split == "TRAIN" and hi >= cs.VALID_START:
        raise ValueError("TRAIN contains VALID/OOS rows")
    if split == "VALID" and lo < cs.VALID_START:
        raise ValueError("VALID contains TRAIN rows")
