"""Yeni Analist motoru (araştırma): kapanmış mum, veri kalitesi, simetri, ağırlık dağıtmama, A/B sonuç kuralları."""
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import analyst_ab_cli as ab  # noqa: E402
from app import analysis, analyst_engine as engine  # noqa: E402

M15 = 900


def series(count, step=M15, seed=5, drift=0.0003):
    rng = random.Random(seed)
    price, out = 100.0, []
    for i in range(count):
        o = price
        price *= 1 + rng.gauss(drift if (i // 300) % 2 else -drift, 0.004)
        out.append({"time": i * step, "open": o, "high": max(o, price) * 1.001, "low": min(o, price) * 0.999,
                    "close": price, "volume": rng.uniform(50, 150)})
    return out


def frames(count15=500):
    c15 = series(count15)
    return c15, ab.aggregate(c15, 3600), ab.aggregate(c15, 14400)


def test_weights_match_plan():
    assert engine.WEIGHTS["A"] == {"trend": 25, "momentum": 20, "structure": 20, "volume_volatility": 15, "mtf": 20}
    assert engine.WEIGHTS["B"] == {"trend": 25, "momentum": 15, "structure": 25, "volume_volatility": 15, "mtf": 20}
    assert all(sum(w.values()) == 100 for w in engine.WEIGHTS.values())


def test_quality_thresholds_and_no_score_below_250():
    assert [engine.quality(n) for n in (400, 399, 250, 249)] == ["IYI", "UYARI", "UYARI", "GECERSIZ"]
    c15 = series(249)
    out = engine.analyze_analyst(c15, [], [])
    assert out["analyst_score"] is None and out["analyst_direction"] is None
    assert out["calibrated_success_probability"] is None and out["analyst_data_quality"]["overall"] == "GECERSIZ"


def test_open_candle_is_ignored():
    c15 = series(9000)
    h1, h4 = ab.aggregate(c15, 3600), ab.aggregate(c15, 14400)
    now = c15[-1]["time"]                      # son 15m mum henüz açık
    w1 = [r for r in h1 if r["time"] + 3600 <= now][-500:]
    w4 = [r for r in h4 if r["time"] + 14400 <= now][-500:]
    closed = engine.analyze_analyst(c15[-501:-1], w1, w4, now=now)
    open1, open4 = dict(w1[-1], time=now - 1800, close=w1[-1]["close"] * 3), dict(w4[-1], time=now - 900, close=0.01)
    with_open = engine.analyze_analyst(c15[-501:], w1 + [open1], w4 + [open4], now=now)
    assert closed["analyst_raw"] == with_open["analyst_raw"]


def test_mirror_symmetry_gives_opposite_direction_same_score():
    c15, h1, h4 = frames(9000)
    c15, h1, h4 = c15[-500:], h1[-500:], h4[-500:]
    pivot = 3 * max(r["high"] for r in c15 + h1 + h4)
    ref = engine.analyze_analyst(c15, h1, h4)
    mir = engine.analyze_analyst(ab.mirror(c15, pivot), ab.mirror(h1, pivot), ab.mirror(h4, pivot))
    assert abs(ref["analyst_raw"] + mir["analyst_raw"]) < 1e-9
    assert ref["analyst_score"] == mir["analyst_score"]
    if ref["analyst_direction"] != "BEKLE":
        assert {ref["analyst_direction"], mir["analyst_direction"]} == {"LONG", "SHORT"}


def test_missing_timeframe_is_not_redistributed():
    c15, h1, h4 = frames(9000)
    c15, h1, h4 = c15[-500:], h1[-500:], h4[-500:]
    full = engine.components(c15, h1, h4)
    no4h = engine.components(c15, h1, h4[:100])
    assert no4h["data_quality"]["timeframes"]["4h"] == "GECERSIZ"
    assert math.isclose(no4h["values"]["mtf"], 0.5 * engine.higher_timeframe(h1))
    assert abs(no4h["values"]["mtf"]) <= 0.5 + 1e-12          # tek zaman dilimi en fazla yarım katkı
    assert {k: v for k, v in no4h["values"].items() if k != "mtf"} == {k: v for k, v in full["values"].items() if k != "mtf"}


def test_ema200_not_used_before_400_bars():
    c15 = series(9000)[-399:]
    atr_value = engine.atr_series([r["high"] for r in c15], [r["low"] for r in c15], [r["close"] for r in c15])[-1]
    closes = [r["close"] for r in c15]
    assert math.isclose(engine.trend(closes, atr_value), sum(engine._trend_parts(closes, atr_value, False)) / 3)


def test_atr_series_matches_analysis_atr():
    c15 = series(500)
    h, l, c = [r["high"] for r in c15], [r["low"] for r in c15], [r["close"] for r in c15]
    assert math.isclose(engine.atr_series(h, l, c)[-1], analysis.atr(h, l, c))


def test_score_bounds_and_direction_threshold():
    c15, h1, h4 = frames(9000)
    for end in range(600, 9000, 700):
        window = c15[end - 500:end]
        now = window[-1]["time"] + M15
        w1 = [r for r in h1 if r["time"] + 3600 <= now][-500:]
        w4 = [r for r in h4 if r["time"] + 14400 <= now][-500:]
        for model in ("A", "B"):
            out = engine.analyze_analyst(window, w1, w4, model=model, now=now)
            assert 0 <= out["analyst_score"] <= 100
            assert (out["analyst_direction"] == "BEKLE") == (out["analyst_score"] < engine.DIRECTION_MIN_SCORE or out["analyst_raw"] == 0)
            assert all(-1 <= c["value"] <= 1 for c in out["analyst_components"].values())


def test_aggregate_uses_only_complete_groups():
    rows = series(64)
    del rows[5]                                 # ikinci saatlik grupta (3600) eksik 15m mum
    hours = ab.aggregate(rows, 3600)
    assert [h["time"] for h in hours[:2]] == [0, 7200] and len(hours) == 15


def bar(t, o, h, l, c):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}


def test_ab_simulate_rules():
    flat = [bar(i * M15, 100, 100.5, 99.5, 100.2) for i in range(300)]
    out = ab.simulate("LONG", 100, 98, flat, 0, 0, 0)
    assert out[96]["o"] == "ZAMAN_ASIMI" and abs(out[96]["r"] - 0.1) < 1e-9 and out[288]["o"] == "ZAMAN_ASIMI"
    both = [bar(0, 100, 103, 97, 100)] + flat[1:]
    assert ab.simulate("LONG", 100, 98, both, 0, 0, 0)[96]["o"] == "KAYIP"
    win = flat[:50] + [bar(50 * M15, 100, 102.5, 99.5, 102)] + flat[51:]
    res = ab.simulate("SHORT", 100, 102, [dict(b, high=200 - b["low"], low=200 - b["high"]) for b in win], 0, 0, 0)
    assert res[48]["o"] == "ZAMAN_ASIMI" and res[96]["o"] == "KAZANC"
    gap = flat[:40] + [dict(b, time=b["time"] + 24 * 3600) for b in flat[40:]]
    assert ab.simulate("LONG", 100, 98, gap, 0, 0, 0)[96] is None


def test_block_bootstrap_is_deterministic_and_contains_point():
    import numpy as np
    days = [f"2026-01-{d:02d}" for d in range(1, 29)]
    first, second = ab.Blocks(days, b=200), ab.Blocks(days, b=200)
    assert (first.idx == second.idx).all() and first.idx.shape == (200, 28)
    ids = np.repeat(np.arange(28), 5)
    point, lo, hi, _ = first.ratio(ids, np.sin(np.arange(140)))
    assert lo <= point <= hi
