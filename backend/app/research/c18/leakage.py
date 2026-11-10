"""C18 leakage audit (pre-registration section 5): poison every input that lies after a cutoff and require that decisions made
up to the cutoff keep exactly the same feature values."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.research.c18 import features as ft


def poison(m: pd.DataFrame, fts: np.ndarray, fr: np.ndarray, qv: pd.Series, cutoff: int) -> tuple[pd.DataFrame, np.ndarray, pd.Series]:
    """Replace metrics rows with create_time > cutoff, funding settlements with calc_time > cutoff and volume closing after cutoff + 300 by garbage."""
    m2 = m.copy()
    num = m2.select_dtypes(include="number").columns
    m2.loc[m2.index > cutoff, num] = 1.0e9
    fr2 = np.where(fts > cutoff, 1.0e9, fr)
    q2 = qv.copy()
    q2[q2.index > cutoff + ft.STEP] = 1.0e-3
    return m2, fr2, q2


def audit(m, fts, fr, qv, at: np.ndarray, atr_pct: np.ndarray, price_dir: np.ndarray, cutoff: int) -> dict:
    """Rows with at <= cutoff + 300 may only see data up to the cutoff: they must be bit-identical. Rows well after must differ (the poison is real)."""
    base = ft.compute(m, fts, fr, qv, at, atr_pct, price_dir)
    m2, fr2, q2 = poison(m, fts, fr, qv, cutoff)
    pois = ft.compute(m2, fts, fr2, q2, at, atr_pct, price_dir)
    cols = ft.G1 + ft.G2
    early = at <= cutoff + ft.STEP
    late = at >= cutoff + 3 * 3600
    same = np.isclose(base.loc[early, cols].to_numpy(float), pois.loc[early, cols].to_numpy(float), equal_nan=True, rtol=0, atol=0)
    moved = ~np.isclose(base.loc[late, cols].to_numpy(float), pois.loc[late, cols].to_numpy(float), equal_nan=True)
    return {"cutoff": int(cutoff), "early_rows": int(early.sum()), "early_value_mismatches": int((~same).sum()),
            "late_rows": int(late.sum()), "late_cells_changed_by_poison": int(moved.sum()),
            "passed": bool((~same).sum() == 0 and moved.sum() > 0)}
