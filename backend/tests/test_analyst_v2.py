"""Analist V2 canlı motoru: saf Python özellikleri = pandas referansı; kapanmış mum; veri yetersizliği; çıktı sözleşmesi."""
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import analyst_v2 as live  # noqa: E402

M15 = 900


def synthetic(n=520, seed=7, start=1_700_000_000 - 1_700_000_000 % M15):
    rng = random.Random(seed)
    price, rows = 100.0, []
    for i in range(n):
        o = price
        price *= math.exp(rng.gauss(0.0002 if (i // 120) % 2 else -0.0002, 0.004))
        hi = max(o, price) * (1 + abs(rng.gauss(0, 0.002)))
        lo = min(o, price) * (1 - abs(rng.gauss(0, 0.002)))
        if i % 97 == 0:                      # uç durumlar: sıfır aralıklı mum, eşit hacimler
            hi = lo = o = price
        rows.append({"time": start + i * M15, "open": o, "high": hi, "low": lo, "close": price,
                     "volume": 100.0 if i % 13 == 0 else rng.uniform(50, 150)})
    return rows


def test_pure_python_features_match_pandas_reference():
    import numpy as np
    import pandas as pd
    import analyst_v2_features_ref as ref
    for seed in (1, 2, 3):
        rows = synthetic(seed=seed)
        df = pd.DataFrame({"o": [r["open"] for r in rows], "h": [r["high"] for r in rows], "l": [r["low"] for r in rows],
                           "c": [r["close"] for r in rows], "v": [r["volume"] for r in rows]},
                          index=[r["time"] for r in rows])
        expected = ref.features_15m(ref.base_indicators(df), np.array([len(df) - 1])).iloc[0]
        got = live.features_last(rows)
        assert ref.FEATURES_15M == live.FEATURES_15M
        for name in live.FEATURES_15M:
            assert math.isclose(got[name], float(expected[name]), rel_tol=1e-9, abs_tol=1e-9), (seed, name, got[name], expected[name])


def test_open_candle_is_dropped_and_result_unchanged():
    rows = synthetic()
    closed_now = rows[-1]["time"] + M15
    base = live.evaluate(rows, now=closed_now)
    opened = rows + [{"time": rows[-1]["time"] + M15, "open": 1.0, "high": 999.0, "low": 0.1, "close": 500.0, "volume": 1e9}]
    again = live.evaluate(opened, now=closed_now + 60)
    assert base["probabilities"] == again["probabilities"] and base["analyst_score"] == again["analyst_score"]


def test_insufficient_data_returns_wait_without_score():
    rows = synthetic(n=300)
    out = live.evaluate(rows, now=rows[-1]["time"] + M15)
    assert out["status"] == "VERI_YETERSIZ" and out["analyst_direction"] == "BEKLE" and out["analyst_score"] is None


def test_gap_uses_only_contiguous_tail():
    rows = synthetic(n=900)
    del rows[300]                            # 300. mumdan sonra 599 kesintisiz mum kalır
    tail = live.contiguous_closed(rows, rows[-1]["time"] + M15)
    assert len(tail) == 599 and all(b["time"] - a["time"] == M15 for a, b in zip(tail, tail[1:]))


def test_output_contract_and_thresholds():
    for seed in range(5):
        rows = synthetic(seed=seed)
        out = live.evaluate(rows, now=rows[-1]["time"] + M15)
        assert out["status"] == "OK"
        p = out["probabilities"]
        assert abs(sum(p.values()) - 1) < 1e-3
        assert 0 <= out["analyst_score"] <= 100
        assert (out["analyst_direction"] == "BEKLE") == (out["analyst_score"] < live.DIRECTION_MIN_SCORE)
        assert out["analyst_direction"] in ("LONG", "SHORT", "BEKLE")
        assert {c["group"] for c in out["components"]} == {"trend", "momentum", "yapi", "hacim", "volatilite"}
        assert sum(c["weight_pct"] for c in out["components"]) in range(98, 103)
        assert out["movement"]["level"] in ("YUKSEK", "ORTA", "DUSUK")
        assert "olasılık değildir" in out["note"]


def test_model_file_matches_code():
    model = live.load_model()
    assert model["features"] == live.FEATURES_15M and len(model["strength_quantiles"]) == 101
    assert set(model["groups"]) == {"trend", "momentum", "yapi", "hacim", "volatilite"}


def test_live_module_has_no_numpy_or_pandas_dependency():
    source = Path(live.__file__).read_text(encoding="utf-8")
    assert "import numpy" not in source and "import pandas" not in source
