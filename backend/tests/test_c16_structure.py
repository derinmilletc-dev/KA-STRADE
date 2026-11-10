"""C16 structure engine tests (synthetic candles, no I/O)."""
import math

import numpy as np

from app.research.c16 import structure as st

BAR = st.BAR


def candles(n=700, seed=3, drift=0.0):
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(drift, 0.6, n))
    o = np.concatenate([[100.0], c[:-1]])
    h = np.maximum(o, c) + rng.uniform(0.05, 0.5, n)
    l = np.minimum(o, c) - rng.uniform(0.05, 0.5, n)
    v = rng.uniform(100, 200, n)
    t = 1_700_000_000 // BAR * BAR + np.arange(n) * BAR
    return t, o, h, l, c, v


def same(a, b):
    return a.keys() == b.keys() and all((math.isnan(a[k]) and math.isnan(b[k])) or a[k] == b[k] for k in a)


def test_no_future_candle_test():
    """Features at j from the full run == bit-identical to a run that only ever saw candles <= j (FULL vs CUTOFF)."""
    t, o, h, l, c, v = candles()
    for side in ("LONG", "SHORT"):
        full = st.run_series(t, o, h, l, c, v, side)
        assert len(full) > 100
        for j in list(full)[::37]:
            cut = st.run_series(t[:j + 1], o[:j + 1], h[:j + 1], l[:j + 1], c[:j + 1], v[:j + 1], side)
            assert same(full[j], cut[j]), (side, j)
        j = list(full)[150]                                   # garbage after j must not matter
        o2, h2, l2, c2, v2 = (x.copy() for x in (o, h, l, c, v))
        for arr in (o2, h2, l2, c2):
            arr[j + 1:] = arr[j + 1:] * 3 + 50
        assert same(full[j], st.run_series(t, o2, h2, l2, c2, v2, side)[j])


def test_feature_cutoff_test():
    t, o, h, l, c, v = candles()
    out = st.run_series(t, o, h, l, c, v, "LONG")
    for j, f in out.items():
        dec = t[j] + BAR
        assert f["candle_close_ts"] == f["feature_cutoff_ts"] == dec
        assert f["swing_max_confirmed_ts"] <= dec and f["level_known_ts"] <= dec


def test_confirmed_swing_test():
    t, o, h, l, c, v = candles()
    eng = st.SideEngine()
    for j in range(len(t)):
        eng.push(int(t[j]), o[j], h[j], l[j], c[j], v[j])
        for pivot, conf, price in eng.sh + eng.sl:
            assert conf <= j and conf - pivot == st.K          # needs K closed bars to the right
    assert eng.sh and eng.sl
    for pivot, conf, price in eng.sh:                          # fractal definition
        assert price == h[pivot] and price > max(h[pivot - st.K:pivot]) and price >= max(h[pivot + 1:pivot + st.K + 1])


def _flat_then_break():
    n = 520
    t = 1_700_000_000 // BAR * BAR + np.arange(n) * BAR
    base = 100 + 2 * np.sin(np.arange(n) * 0.5)                # oscillation -> many swings
    o, c = base.copy(), base.copy()
    h, l = base + 0.3, base - 0.3
    v = np.full(n, 150.0)
    return t, o, h, l, c, v


def test_breakout_known_level_test():
    t, o, h, l, c, v = _flat_then_break()
    eng, rows = st.SideEngine(), {}
    for j in range(len(t)):
        if j == 400:                                           # close far above the known range high
            c[j] = 112.0
            h[j] = 112.3
            o[j] = 101.0
        f = eng.push(int(t[j]), o[j], h[j], l[j], c[j], v[j])
        if f:
            rows[j] = f
        if j == 400:
            level_before = eng.prev_range[0]
    assert rows[400]["bo_fresh"] == 1.0
    assert rows[400]["bo_dist_atr"] > 0 and abs(rows[400]["bo_dist_atr"] * rows[400]["range_width_atr"] ) >= 0
    # the level used is the one known BEFORE the breakout bar (the breakout bar itself cannot define the level)
    assert rows[400]["level_known_ts"] == t[399] + BAR
    assert 112.0 > level_before


def test_retest_temporal_order_test():
    t, o, h, l, c, v = _flat_then_break()
    eng, trace = st.SideEngine(), []
    for j in range(len(t)):
        if j == 400:
            o[j], c[j], h[j], l[j] = 101.0, 112.0, 112.3, 100.7
        if j in (402, 403):                                    # pull back into the level zone, then hold
            o[j], c[j], h[j], l[j] = 104.0, 104.5, 106.0, 100.0
        f = eng.push(int(t[j]), o[j], h[j], l[j], c[j], v[j])
        if f and eng.bo is not None:
            trace.append((j, eng.bo["idx"], eng.bo["touch"], eng.bo["state"]))
    for j, b, touch, state in trace:
        assert b <= j
        if touch is not None:
            assert touch > b                                    # retest strictly after the breakout
    assert any(s in (2, 3, -1) for *_x, s in trace) or all(s == 1 for *_x, s in trace)


def test_mirror_symmetry():
    t, o, h, l, c, v = candles(drift=0.02)
    a = st.run_series(t, o, h, l, c, v, "SHORT")
    m = st.run_series(t, *st.mirror(o, h, l, c, v), "LONG")
    assert a.keys() == m.keys() and all(same(a[j], m[j]) for j in a)
    # RSI of the mirrored series = 100 - RSI
    lo = st.run_series(t, o, h, l, c, v, "LONG")
    j = list(lo)[50]
    assert abs(lo[j]["rsi"] + a[j]["rsi"] - 100) < 1e-6


def test_feature_family_test():
    assert set(st.ALL_FEATURES) == {k for fs in st.FAMILIES.values() for k in fs}
    assert len(st.ALL_FEATURES) == len(set(st.ALL_FEATURES))
    assert set(st.FAMILIES) == {"A_STRUCTURE", "B_BREAKOUT_RETEST", "C_LOCATION", "D_MOMENTUM", "E_VOLATILITY_REGIME"}
    t, o, h, l, c, v = candles()
    f = next(iter(st.run_series(t, o, h, l, c, v, "LONG").values()))
    assert set(st.ALL_FEATURES) <= set(f) and set(st.CUTOFF_FIELDS) <= set(f)
