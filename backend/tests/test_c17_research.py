"""C17 tests: look-ahead, temporal ordering, one entry per event, label isolation, splits, OOS exclusion, freeze, reproducibility."""
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.research import c15_scoring as cs
from app.research.c16 import structure as st
from app.research.c17 import analysis as an
from app.research.c17 import hypotheses as hy
from app.research.c17 import leakage as lk
from app.research.c17 import pipeline as pl
from app.research.c17 import walk_forward as wf

BAR = 900


def gen(n=9000, seed=1):
    rng = np.random.default_rng(seed)
    vol = np.where((np.arange(n) // 150) % 3 == 0, 0.15, 0.9)
    c = 100 + np.cumsum(rng.normal(0, 1, n) * vol)
    o = np.concatenate([[100.0], c[:-1]])
    h = np.maximum(o, c) + rng.uniform(0.02, 0.6, n) * vol
    low = np.minimum(o, c) - rng.uniform(0.02, 0.6, n) * vol
    v = rng.uniform(100, 200, n)
    t = 1_700_000_000 // BAR * BAR + np.arange(n) * BAR
    return t, o, h, low, c, v


def all_setups(arr, until=None):
    out = []
    for side in pl.SIDES:
        s, *_ = pl.detect(*arr, side, until=until)
        out += s
    return out


def test_no_future_candle():
    arr = gen()
    full = all_setups(arr)
    assert {s["hyp"][:2] for s in full} == {"H1", "H2", "H3"}
    for j in sorted({s["confirm_idx"] for s in full})[::40][:6]:
        cut = all_setups(tuple(x[:j + 1] for x in arr), until=None)       # dataset cut AT the decision candle
        assert lk.keyed(lk.upto(full, j)) == lk.keyed(cut)


def test_future_data_poisoning():
    arr = gen(seed=2)
    full = all_setups(arr)
    rng = np.random.default_rng(0)
    for j in sorted({s["confirm_idx"] for s in full})[::50][:5]:
        o2, h2, l2, c2, v2 = lk.poison(*arr, j, rng)
        poisoned = all_setups((arr[0], o2, h2, l2, c2, v2))
        assert lk.keyed(lk.upto(full, j)) == lk.keyed(lk.upto(poisoned, j))


def test_confirmed_swing():
    arr = gen(seed=3)
    for side in pl.SIDES:
        setups, _inv, _a, eng = pl.detect(*arr, side)
        for pivot, conf, _price in eng.sh + eng.sl:
            assert conf - pivot == st.K
        assert all(s["swing_max_confirmed_idx"] <= s["confirm_idx"] for s in setups)


def test_level_known_before_decision():
    for s in all_setups(gen(seed=4)):
        assert s["level_known_idx"] < s["setup_idx"] <= s["confirm_idx"]
        assert s["level_known_idx"] <= s["confirm_idx"]


def test_breakout_before_retest():
    h1 = [s for s in all_setups(gen(seed=5)) if s["hyp"].startswith("H1")]
    assert h1 and all(s["setup_idx"] < s["retest_idx"] for s in h1)


def test_retest_before_confirmation():
    h1 = [s for s in all_setups(gen(seed=6)) if s["hyp"].startswith("H1")]
    assert all(s["retest_idx"] < s["confirm_idx"] and s["confirm_idx"] == s["retest_idx"] + 1 for s in h1)
    assert all(s["retest_idx"] - s["setup_idx"] <= hy.CONFIG["H1_BREAKOUT_RETEST_RECLAIM"]["max_retest_bars"] for s in h1)


def fake_market(n=9000, seed=7):
    t, o, h, low, c, v = gen(n, seed)
    start = int(t[0])
    rows = [{"time": int(t[i]), "open": o[i], "high": h[i], "low": low[i], "close": c[i], "volume": v[i]} for i in range(n)]
    ds = SimpleNamespace(frames={"BTCUSDT": {"15m": SimpleNamespace(rows=rows)}})
    mk = SimpleNamespace(n=n, copen={"BTCUSDT": o}, mhigh={"BTCUSDT": h + 0.05}, mlow={"BTCUSDT": low - 0.05}, mclose={"BTCUSDT": c},
                         fund_t={"BTCUSDT": np.array([start + 8 * 3600 * k for k in range(1, 400)])}, fund_r={"BTCUSDT": np.full(399, 0.0001)})
    return ds, mk, start, start + n * BAR, (t, o, h, low, c, v)


def test_confirmation_before_entry():
    ds, mk, start, end, (_t, o, *_r) = fake_market()
    setups, _uni, _inv = pl.run_block(ds, mk, "BTCUSDT", start, end, start + 500 * BAR, end)
    assert len(setups) > 20
    assert (setups["entry_ts"] == setups["confirmation_ts"] + BAR).all() and (setups["decision_ts"] == setups["entry_ts"]).all()
    idx = ((setups["entry_ts"] - start) // BAR).astype(int).to_numpy()
    assert (setups["entry_price"].to_numpy() == o[idx]).all()               # entry = OPEN of the candle after the confirmation candle
    assert not any(lk.timing_violations(setups).values())


def test_one_entry_per_event():
    ds, mk, start, end, _ = fake_market(seed=8)
    setups, *_ = pl.run_block(ds, mk, "BTCUSDT", start, end, start + 500 * BAR, end)
    assert not setups.duplicated(["symbol", "hyp", "side", "pivot"]).any()
    assert not setups.duplicated(["symbol", "hyp", "side", "setup_ts"]).any()


def test_label_independent_from_features():
    ds, mk, start, end, _ = fake_market(seed=9)
    base, *_ = pl.run_block(ds, mk, "BTCUSDT", start, end, start + 500 * BAR, end)
    mk.mhigh["BTCUSDT"] = mk.mhigh["BTCUSDT"] + 7.0       # corrupt the MARK series (label source): detection must not change
    mk.mlow["BTCUSDT"] = mk.mlow["BTCUSDT"] - 7.0
    new, *_ = pl.run_block(ds, mk, "BTCUSDT", start, end, start + 500 * BAR, end)
    cols = ["hyp", "side", "level", "level_known_ts", "setup_ts", "retest_ts", "confirmation_ts", "entry_ts", "pivot"]
    pd.testing.assert_frame_equal(base[cols].reset_index(drop=True), new[cols].reset_index(drop=True))
    assert not base["y"].equals(new["y"])                  # labels do react to the mark series


def test_train_valid_separation():
    tr, va, oos = (an.WINDOWS[k] for k in ("TRAIN", "VALID", "OOS"))
    assert tr[1] <= va[0] and va[1] <= oos[0] and oos[0] == cs.OOS_START
    an.guard_decisions(pd.DataFrame({"decision_ts": [tr[0], tr[1] - 900]}), "TRAIN")
    with pytest.raises(ValueError):
        an.guard_decisions(pd.DataFrame({"decision_ts": [va[0]]}), "TRAIN")
    with pytest.raises(ValueError):
        an.guard_decisions(pd.DataFrame({"decision_ts": [tr[0]]}), "VALID")
    fs = wf.folds(tr[0] // 86400, tr[1] // 86400 - 1)
    assert [(f["train_days"], f["test_days"]) for f in fs] == [(68, 28), (98, 33), (133, 34)]
    assert all(f["test_last_day"] < va[0] // 86400 for f in fs)


def test_oos_exclusion():
    with pytest.raises(ValueError):
        an.guard_decisions(pd.DataFrame({"decision_ts": [cs.OOS_START]}), "VALID")
    with pytest.raises(ValueError):
        an.guard_decisions(pd.DataFrame({"decision_ts": [cs.OOS_START + 900]}), "TRAIN")
    an.guard_decisions(pd.DataFrame({"decision_ts": [cs.OOS_START + 900]}), "OOS")


def test_model_freeze(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x=1")
    h1 = an.file_hash([f])
    assert h1 == an.file_hash([f])
    f.write_text("x=2")
    assert an.file_hash([f]) != h1
    d1 = hy.definition_hash()
    saved = json.dumps(hy.CONFIG, sort_keys=True)
    hy.CONFIG["H1_BREAKOUT_RETEST_RECLAIM"]["breakout_body_atr"] = 0.30
    try:
        assert hy.definition_hash() != d1
    finally:
        hy.CONFIG.clear()
        hy.CONFIG.update(json.loads(saved))
    assert hy.definition_hash() == d1


def test_reproducibility():
    arr = gen(seed=11)
    assert lk.keyed(all_setups(arr)) == lk.keyed(all_setups(arr))
    ds, mk, start, end, _ = fake_market(seed=12)
    a, *_ = pl.run_block(ds, mk, "BTCUSDT", start, end, start + 500 * BAR, end)
    b, *_ = pl.run_block(ds, mk, "BTCUSDT", start, end, start + 500 * BAR, end)
    pd.testing.assert_frame_equal(a, b)
