"""C17 look-ahead checks (used by the tests and by the independent audit)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.research.c17 import pipeline as pl

BAR = pl.BAR
KEYS = ("hyp", "side", "level_known_idx", "setup_idx", "retest_idx", "confirm_idx", "level", "pivot", "swing_max_confirmed_idx")


def keyed(setups: list[dict]) -> list[tuple]:
    return sorted((tuple(s.get(k) for k in KEYS) for s in setups), key=str)


def upto(setups: list[dict], j: int) -> list[dict]:
    return [s for s in setups if s["confirm_idx"] <= j]


def poison(t, o, h, l, c, v, j: int, rng: np.random.Generator):
    """Replace every OHLCV value AFTER candle j by random garbage."""
    o2, h2, l2, c2, v2 = (x.copy() for x in (o, h, l, c, v))
    n = len(t) - j - 1
    if n > 0:
        base = rng.uniform(50, 200, n)
        o2[j + 1:], c2[j + 1:] = base, base * rng.uniform(0.9, 1.1, n)
        h2[j + 1:] = np.maximum(o2[j + 1:], c2[j + 1:]) * rng.uniform(1.0, 1.2, n)
        l2[j + 1:] = np.minimum(o2[j + 1:], c2[j + 1:]) * rng.uniform(0.8, 1.0, n)
        v2[j + 1:] = rng.uniform(1, 500, n)
    return o2, h2, l2, c2, v2


def timing_violations(df: pd.DataFrame) -> dict[str, int]:
    """Row-level checks on a setup table (all timestamps in epoch seconds)."""
    r = df["retest_ts"]
    has_rt = r.notna()
    return {"level_known_after_decision": int((df["level_known_ts"] > df["decision_ts"]).sum()),
            "setup_after_decision": int((df["setup_ts"] > df["decision_ts"]).sum()),
            "confirmation_after_decision": int((df["confirmation_ts"] > df["decision_ts"]).sum()),
            "swing_confirmed_after_decision": int((df["swing_confirmed_ts"] > df["decision_ts"]).sum()),
            "entry_not_confirmation_plus_15m": int((df["entry_ts"] != df["confirmation_ts"] + BAR).sum()),
            "decision_not_entry": int((df["decision_ts"] != df["entry_ts"]).sum()),
            "breakout_not_before_retest": int((df.loc[has_rt, "setup_ts"] >= r[has_rt]).sum()),
            "retest_not_before_confirmation": int((r[has_rt] >= df.loc[has_rt, "confirmation_ts"]).sum()),
            "setup_not_before_confirmation": int((df["setup_ts"] >= df["confirmation_ts"]).sum()),
            "duplicate_event": int(df.duplicated(["symbol", "hyp", "side", "pivot"]).sum())}
