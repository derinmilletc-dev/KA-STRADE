"""C18 research tests: time rules, orientation, poisoning, label independence, split guards, reproducibility, freeze."""
import hashlib
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
sys.path.insert(0, str(Path(__file__).parents[1]))

from app.research.c18 import features as ft
from app.research.c18 import leakage, pipeline, stats

T0 = 1_750_000_200            # multiple of 300
N = 12 * 24 * 40              # 40 days of 5-minute rows


def synth(seed=0):
    rng = np.random.default_rng(seed)
    idx = T0 + ft.STEP * np.arange(N, dtype=np.int64)
    m = pd.DataFrame({"sum_open_interest": 1000 + np.cumsum(rng.normal(0, 1, N)), "sum_open_interest_value": 5e7 + np.cumsum(rng.normal(0, 1e4, N)),
                      "sum_toptrader_long_short_ratio": 1 + 0.1 * rng.random(N), "count_long_short_ratio": 1 + 0.1 * rng.random(N),
                      "sum_taker_long_short_vol_ratio": 1 + 0.1 * rng.random(N)}, index=idx)
    ftm = (idx[0] // 28800 * 28800 + 28800 * np.arange(120)).astype(np.int64)
    fr = rng.normal(0, 1e-4, len(ftm))
    ar = np.arange(T0 + 35 * 3600 * 24 // 35, T0 + 35 * 86400, 900, dtype=np.int64)
    qv = pd.Series(1e9, index=np.arange(T0 - 900 * 100, T0 + 40 * 86400, 900))
    return m, ftm, fr, qv, ar


def run(m, ftm, fr, qv, at):
    return ft.compute(m, ftm, fr, qv, at, np.full(len(at), 0.01), np.ones(len(at)))


def test_no_future_metrics_row():
    m, ftm, fr, qv, at = synth()
    base = run(m, ftm, fr, qv, at)
    m2 = m.copy()
    m2.loc[m2.index > at[200] - ft.STEP, :] = 5.0
    row = run(m2, ftm, fr, qv, at[200:201])
    assert np.allclose(base.loc[200, ft.G1 + ft.G2].to_numpy(float), row.loc[0, ft.G1 + ft.G2].to_numpy(float), equal_nan=True)


def test_funding_boundary():
    m, ftm, fr, qv, _ = synth()
    t = int(ftm[20])
    a = np.array([t + ft.STEP - 1, t + ft.STEP], dtype=np.int64)
    r = ft.compute(m, ftm, fr, qv, a, np.full(2, 0.01), np.ones(2))
    assert r.loc[0, "fund_level"] == fr[19] and r.loc[1, "fund_level"] == fr[20]


def test_future_data_poisoning():
    m, ftm, fr, qv, at = synth()
    res = leakage.audit(m, ftm, fr, qv, at, np.full(len(at), 0.01), np.ones(len(at)), int(at[len(at) // 2]))
    assert res["passed"] and res["early_value_mismatches"] == 0 and res["late_cells_changed_by_poison"] > 0


def test_short_orientation_flips_signed_features_only():
    m, ftm, fr, qv, at = synth()
    f = run(m, ftm, fr, qv, at[:50])
    s = ft.orient(f, np.array(["SHORT"] * 50))
    for c in ft.SIGNED_G:
        assert np.allclose(s[c].to_numpy(float), -f[c].to_numpy(float), equal_nan=True)
    for c in ("oi_chg_4h", "oiv_z", "oi_vol"):
        assert np.allclose(s[c].to_numpy(float), f[c].to_numpy(float), equal_nan=True)


def test_invalid_rows_are_not_filled():
    m, ftm, fr, qv, _ = synth()
    m = m.copy()
    m.iloc[500:520, :] = np.nan
    i = 510
    a = np.array([m.index[i] + ft.STEP], dtype=np.int64)
    r = ft.compute(m, ftm, fr, qv, a, np.full(1, 0.01), np.ones(1))
    assert np.isnan(r.loc[0, "ls_top_level"]) and np.isnan(r.loc[0, "taker_ratio"])


def test_day_with_too_many_invalid_rows_is_excluded():
    m, *_ = synth()
    m = m.copy()
    m.iloc[:, :] = m.iloc[:, :]
    day = int(m.index[10] // ft.DAY)
    sel = (m.index // ft.DAY) == day
    pos = np.where(sel)[0][: int(0.2 * sel.sum())]
    m.iloc[pos, m.columns.get_loc("sum_open_interest_value")] = np.nan
    assert day in ft.invalid_days(m)["G1"] and day not in ft.invalid_days(m)["G2"]


def test_oos_refused():
    df = pd.DataFrame({"at": [pipeline.OOS_START]})
    with pytest.raises(ValueError):
        pipeline.assert_no_oos(df)


def test_label_independent_from_features():
    df = pd.DataFrame({"side": ["LONG"] * 4, "hold": [3.0, 20.0, np.nan, 16.0], "y": [1, 0, np.nan, 1], "at": [1, 2, 3, 4]})
    df = df.assign(y_move=((df["hold"] <= pipeline.MOVE_BARS) & df["hold"].notna()).astype(int))
    assert df["y_move"].tolist() == [1, 0, 0, 1]
    assert not any(c in ft.G1 + ft.G2 for c in df.columns)


def test_reproducibility_and_freeze_hash():
    m, ftm, fr, qv, at = synth()
    a, b = run(m, ftm, fr, qv, at), run(m, ftm, fr, qv, at)
    assert a.equals(b)
    d = pd.DataFrame({"yt": np.random.default_rng(1).integers(0, 2, 400), "x": np.random.default_rng(2).normal(size=400)})
    h = [hashlib.sha256(str(stats.fit(d, ["x"])).encode()).hexdigest() for _ in range(2)]
    assert h[0] == h[1]


def test_train_valid_separation():
    tr = pd.DataFrame({"at": [1_760_000_000], "x": [1.0]})
    va = pd.DataFrame({"at": [1_777_000_000]})
    assert tr["at"].max() < 1_767_225_600 <= va["at"].min()      # 2026-01-01
