"""C17 labels: +2 ATR before -2 ATR first touch on MARK high/low from the entry candle (C+1 open), STOP first when both fall in one candle.
Future data is used ONLY here, never in event detection. Reuses the C15/C14 label code."""
from __future__ import annotations

from typing import Any

import numpy as np

from app.research import c15_scoring as cs

BAR = cs.BAR
TAKER_FEE = 0.0005


def label_decision(mh: np.ndarray, ml: np.ndarray, mc: np.ndarray, entry_idx: int, entry: float, atr: float, fund_t: np.ndarray, fund_r: np.ndarray,
                   t0: int) -> dict[str, Any]:
    out = cs.label_row(mh, ml, mc, entry_idx, entry, atr, fund_t, fund_r, t0)
    for side in ("LONG", "SHORT"):
        key = side.lower()
        out[f"y1_{key}"] = cs.first_barrier(mh, ml, entry_idx, entry, atr, side, k=1.0)["result"]
        sgn = 1.0 if side == "LONG" else -1.0
        # net R: 5 bps taker fee per side on the entry notional and the actual funding, in units of R (= 2 ATR)
        fs = out.get(f"fund_sum_{key}")
        out[f"cost_r_{key}"] = None if fs is None else (2 * TAKER_FEE * entry + sgn * fs * entry) / (2 * atr)
    return out
