"""KAIS FARMER entry research (C14). Research only: no network, no trading API, no repository output, no Farmer execution code.

  frequency --data-2025 D          setup COUNTS on the TRAIN period only (no outcome is computed)
  run       --data-2025 D --data-2026 D --output O --protocol-hash H    the one pre-registered test (TRAIN / VALID / OOS)

`run` refuses to start unless the protocol hash you pass equals the hash of the frozen protocol in the module.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app import backtest_data as bd
from app import farmer_entry_research as er

REPO = Path(__file__).resolve().parents[1]
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")


def day(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def outside_repo(path: Path) -> Path:
    path = path.resolve()
    if path.is_relative_to(REPO):
        raise SystemExit("Data and results must stay outside the repository")
    return path


def load_arrays(root: Path, start: int, end: int) -> dict:
    """Complete 15m grid for contract and mark candles, per symbol. Any gap stops the run (no fill, no interpolation)."""
    root = outside_repo(root)
    ds = bd.load_dataset(root, root / "current-metadata.json", start, end)
    n = (end - start) // er.BAR
    out: dict = {"start": start, "end": end, "n": n, "sym": {}}
    for sym in SYMBOLS:
        crow = [r for r in ds.frames[sym]["15m"].rows if start <= r["time"] < end]
        mrow = [r for r in ds.marks[sym].rows if start <= r["time"] < end]
        for rows, label in ((crow, "contract"), (mrow, "mark")):
            if len(rows) != n or any(r["time"] != start + er.BAR * i for i, r in enumerate(rows)):
                raise SystemExit(f"{sym} {label} 15m series is not complete on the grid")
        out["sym"][sym] = {"o": np.array([r["open"] for r in crow]), "h": np.array([r["high"] for r in crow]), "l": np.array([r["low"] for r in crow]),
                           "c": np.array([r["close"] for r in crow]), "mo": np.array([r["open"] for r in mrow]), "mh": np.array([r["high"] for r in mrow]),
                           "ml": np.array([r["low"] for r in mrow]),
                           "fund_t": np.array([e["time"] for e in ds.funding[sym]]), "fund_r": np.array([e["rate"] for e in ds.funding[sym]])}
    return out


def month(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m")


def frequency(a: argparse.Namespace) -> None:
    cfg = er.PROTOCOL
    lo, hi = day(cfg.train[0]), day(cfg.train[1])
    data = load_arrays(a.data_2025, day(a.load_start_2025), hi)
    funnel: dict = {}
    for sym, d in data["sym"].items():
        atr = er.wilder_atr(d["h"], d["l"], d["c"], cfg.atr_period)
        events = er.detect_setups(d["o"], d["h"], d["l"], d["c"], atr)
        c = Counter()
        for e in events:
            ts = data["start"] + e["breakout_idx"] * er.BAR
            if lo <= ts < hi:
                c["breakouts"] += 1
                c[e["state"]] += 1
                c[f"month_{month(ts)}_{e['state']}"] += 1
        funnel[sym] = dict(c)
    print(json.dumps({"period": [cfg.train[0], cfg.train[1]], "counts_only": True, "funnel": funnel}, indent=1))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["frequency", "run"])
    p.add_argument("--data-2025", type=Path, required=True)
    p.add_argument("--data-2026", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--protocol-hash")
    p.add_argument("--load-start-2025", default="2025-03-01")
    a = p.parse_args()
    if a.command == "frequency":
        frequency(a)
    else:
        raise SystemExit("run is added after the protocol is locked")


if __name__ == "__main__":
    sys.exit(main())
