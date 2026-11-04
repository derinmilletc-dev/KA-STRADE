"""Analist V2 araştırma aracı: etiket kuralı, katlar, kalibrasyon ölçüleri, bootstrap, birleştirme."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

import analyst_v2_research as v  # noqa: E402


def test_label_rule_is_symmetric_and_cost_aware():
    df = pd.DataFrame({"r_4s": [0.01, -0.01, 0.001, np.nan], "sig_4s": [0.01, 0.01, 0.01, 0.01]})
    y = v.labels(df, "4s", 0.5)
    assert list(y) == [0, 1, 2, -1]
    # Maliyet eşiği: r = c + k·σ tam sınırda LONG
    df = pd.DataFrame({"r_4s": [v.COST + 0.005, -(v.COST + 0.005)], "sig_4s": [0.01, 0.01]})
    assert list(v.labels(df, "4s", 0.5)) == [0, 1]


def test_folds_cover_dev_period_without_overlap():
    f = v.folds()
    assert len(f) == 17 and f[0][0] == v.ts("2022-01-01") and f[-1] == (v.ts("2026-01-01"), v.DEV_END)
    assert all(a[1] == b[0] for a, b in zip(f, f[1:]))


def test_train_mask_purges_label_window():
    d = pd.DataFrame({"t": np.arange(0, 10 * 86400, 3600)})
    start = 5 * 86400
    m = v.train_mask(d, start, "24s")
    assert (d["t"][m] + 96 * v.M15 + 86400 <= start).all()
    assert not m[d["t"] >= start - 86400].any()


def test_ece_and_slope_on_calibrated_probs():
    rng = np.random.default_rng(0)
    p = rng.dirichlet([2, 2, 3], size=20000)
    y = np.array([rng.choice(3, p=row) for row in p])
    assert all(v.ece(p, y, k) < 0.02 for k in range(3))
    assert all(0.85 < v.cal_slope(p, y, k) < 1.15 for k in range(3))


def test_two_way_bootstrap_contains_mean():
    rng = np.random.default_rng(1)
    days = np.repeat(np.arange(60), 10)
    coins = np.tile(np.arange(10), 60)
    vals = rng.normal(0.1, 1, size=600)
    point, lo, hi, boot = v.TwoWay(days, coins, b=300).mean(vals)
    assert lo < point < hi and len(boot) == 300


def test_aggregate_only_complete_groups():
    idx = np.arange(0, 16 * v.M15, v.M15)
    df = pd.DataFrame({"o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 1.0}, index=idx)
    df.iloc[5] = np.nan
    agg = v.aggregate(df, v.H1)
    assert agg["c"].isna().tolist() == [False, True, False, False]


def test_window_rank_counts_current_value():
    x = np.arange(10, dtype=float)
    r = v.window_rank(x, np.array([9]), 5)
    assert r[0] == 1.0
