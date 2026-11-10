"""C16 pipeline tests: entry on C+1, label isolation, TRAIN/VALID separation, OOS exclusion, score reproducibility, model freeze."""
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.research import c15_scoring as cs
from app.research.c16 import analysis as an
from app.research.c16 import pipeline as pl
from app.research.c16 import structure as st
from tests.test_c16_structure import candles

BAR = 900


def fake_market(n=700, seed=5):
    t, o, h, l, c, v = candles(n, seed)
    start = int(t[0])
    rows = [{"time": int(t[i]), "open": o[i], "high": h[i], "low": l[i], "close": c[i], "volume": v[i]} for i in range(n)]
    ds = SimpleNamespace(frames={"BTCUSDT": {"15m": SimpleNamespace(rows=rows)}})
    rng = np.random.default_rng(1)
    mk = SimpleNamespace(n=n, copen={"BTCUSDT": o}, mhigh={"BTCUSDT": h + rng.uniform(0, 0.1, n)}, mlow={"BTCUSDT": l - rng.uniform(0, 0.1, n)},
                         mclose={"BTCUSDT": c}, fund_t={"BTCUSDT": np.array([start + 8 * 3600 * k for k in range(1, 40)])},
                         fund_r={"BTCUSDT": np.full(39, 0.0001)})
    return ds, mk, start, start + n * BAR, (t, o, h, l, c, v)


def test_entry_next_candle_test():
    ds, mk, start, end, (t, o, *_r) = fake_market()
    rows = pl.block_rows(ds, mk, "BTCUSDT", start, end, start + 320 * BAR)
    assert rows
    for r in rows:
        j = (r["at"] - start) // BAR - 1
        assert r["at"] == t[j] + BAR                      # decision = close of candle j
        assert r["entry_price"] == o[j + 1]               # entry = OPEN of the next candle
        assert r["feature_cutoff_ts"] == r["at"] and r["candle_close_ts"] <= r["at"]


def test_label_no_leakage_test():
    ds, mk, start, end, _ = fake_market()
    base = pl.block_rows(ds, mk, "BTCUSDT", start, end, start + 320 * BAR)
    r0 = base[40]
    j = (r0["at"] - start) // BAR - 1
    # labels depend on data from the entry candle on; features never do. Corrupt mark candles BEFORE the entry: label unchanged.
    mk.mhigh["BTCUSDT"] = mk.mhigh["BTCUSDT"].copy()
    mk.mhigh["BTCUSDT"][:j + 1] += 1000
    same = pl.block_rows(ds, mk, "BTCUSDT", start, end, start + 320 * BAR)
    assert all(a["y"] == b["y"] for a, b in zip(base, same) if (a["at"] - start) // BAR > j)
    # corrupt the contract candles AFTER j: the features at j are unchanged
    ds2, mk2, *_ = fake_market()
    for r in ds2.frames["BTCUSDT"]["15m"].rows[j + 1:]:
        r["close"] *= 3
        r["high"] *= 3
    new = pl.block_rows(ds2, mk2, "BTCUSDT", start, end, start + 320 * BAR)
    for a, b in zip(base, new):
        if (a["at"] - start) // BAR - 1 <= j:
            assert all((a[k] == b[k]) or (np.isnan(a[k]) and np.isnan(b[k])) for k in st.ALL_FEATURES)


def test_train_valid_separation_test():
    tr = pd.DataFrame({"at": [cs.TRAIN_START + 10, cs.VALID_START - 900]})
    va = pd.DataFrame({"at": [cs.VALID_START, cs.OOS_START - 900]})
    pl.check_split(tr, "TRAIN")
    pl.check_split(va, "VALID")
    with pytest.raises(ValueError):
        pl.check_split(pd.DataFrame({"at": [cs.VALID_START]}), "TRAIN")
    with pytest.raises(ValueError):
        pl.check_split(pd.DataFrame({"at": [cs.VALID_START - 900, cs.VALID_START]}), "VALID")


def test_oos_exclusion_test(tmp_path):
    with pytest.raises(ValueError):
        pl.assert_not_oos(cs.OOS_START)
    with pytest.raises(ValueError):
        pl.assert_not_oos("daily/markPriceKlines/BTCUSDT/15m/BTCUSDT-15m-2026-06-29.zip")
    with pytest.raises(ValueError):
        pl.check_split(pd.DataFrame({"at": [cs.OOS_START]}), "VALID")
    pl.assert_not_oos(cs.OOS_START - 900)
    (tmp_path / "manifest.json").write_text(json.dumps({"archives": [{"path": "x-2026-06.zip", "month": "2026-06"}]}))
    with pytest.raises(SystemExit):
        pl.guard_manifest(tmp_path)


def _frame(n=3000, seed=2):
    rng = np.random.default_rng(seed)
    d = pd.DataFrame({"f1": rng.normal(size=n), "f2": rng.normal(size=n), "day": np.repeat(np.arange(n // 30), 30)})
    d["y"] = (rng.random(n) < 1 / (1 + np.exp(-0.3 * d["f1"]))).astype(int)
    return d


def test_score_reproducibility_test():
    d = _frame()
    m1, m2 = an.fit_ridge(d, ["f1", "f2"], 10.0), an.fit_ridge(d, ["f1", "f2"], 10.0)
    assert m1 == m2 and m1["coef"][0] > 0
    z = an.linear_predictor(m1, d)
    grid = an.percentile_map(z)
    s = an.to_score(grid, z)
    assert s.min() >= 0 and s.max() <= 100 and np.all(np.diff(s[np.argsort(z)]) >= -1e-9)      # monotone 0..100
    w1, w2 = an.walk_forward(d, ["f1", "f2"]), an.walk_forward(d, ["f1", "f2"])
    assert np.array_equal(w1["oof"], w2["oof"], equal_nan=True) and w1["lambda"] == w2["lambda"]
    # walk-forward: every test row is strictly after its training rows (embargo >= 2 days)
    for tr, te in an.wf_folds(d):
        assert d["day"][tr].max() + an.EMBARGO_DAYS <= d["day"][te].min()


def test_model_freeze_test():
    d = _frame()
    model = an.fit_ridge(d, ["f1", "f2"], 10.0)
    frozen = {"models": {"B": model}, "spec": {"weights_fitted_on": "TRAIN only"}}
    frozen["sha256"] = an.model_hash({k: frozen[k] for k in ("models", "spec")})
    assert an.verify_frozen(frozen)
    frozen["models"]["B"]["coef"][0] += 1e-9               # any change after the freeze is detected
    assert not an.verify_frozen(frozen)
