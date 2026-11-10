"""C17 TRAIN walk-forward windows (expanding, 2-day embargo, same arithmetic as C16: train 68/98/133 days -> test 28/33/34 days). VALID is never inside."""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from app.research.c17 import analysis as an

EMBARGO_DAYS = 2


def folds(first_day: int, last_day: int) -> list[dict[str, int]]:
    days = np.arange(first_day, last_day + 1)
    n = len(days)
    cuts = [round(n * 0.41), round(n * 0.585), round(n * 0.79), n]
    out = []
    for a, b in itertools.pairwise(cuts):
        out.append({"train_first_day": int(days[0]), "train_last_day": int(days[a - EMBARGO_DAYS - 1]), "train_days": a - EMBARGO_DAYS,
                    "test_first_day": int(days[a + EMBARGO_DAYS]), "test_last_day": int(days[b - 1]), "test_days": b - (a + EMBARGO_DAYS)})
    return out


def fold_report(setups: pd.DataFrame, fs: list[dict[str, int]]) -> list[dict]:
    day = (setups["decision_ts"] // an.DAY).astype(int)
    rows = []
    for i, f in enumerate(fs, 1):
        tr = setups[(day >= f["train_first_day"]) & (day <= f["train_last_day"])]
        te = setups[(day >= f["test_first_day"]) & (day <= f["test_last_day"])]
        s_te = an.summarize(te)
        rows.append({"fold": i, **f, "past_only_train": an.summarize(tr), "test": s_te,
                     "test_expectancy_positive": bool(s_te.get("resolved", 0) >= 10 and s_te.get("expectancy_r", 0) > 0)})
    return rows


def stable(rows: list[dict]) -> bool:
    return sum(r["test_expectancy_positive"] for r in rows) >= 2
