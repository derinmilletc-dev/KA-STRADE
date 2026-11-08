"""C15 research tests. Fixtures are synthetic and used for correctness checks only, never for results."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import c15_scoring_cli as cli
from app import analysis
from app.research import c15_analysis as ca
from app.research import c15_scoring as cs

RNG = np.random.default_rng(11)


def candles(n: int = 259, seed: int = 1, drift: float = 0.0) -> list[dict]:
    r = np.random.default_rng(seed)
    close = 100 + np.cumsum(r.normal(drift, 0.4, n))
    out = []
    for i in range(n):
        o = close[i - 1] if i else close[0]
        c = close[i]
        out.append({"time": 1_700_000_000 + 900 * i, "open": o, "high": max(o, c) + abs(r.normal(0, 0.2)), "low": min(o, c) - abs(r.normal(0, 0.2)),
                    "close": c, "volume": float(1000 + r.integers(0, 500))})
    return out


def legacy_for(c: list[dict]) -> dict:
    a = analysis.analyze(c)
    return {"analysis": a, "mtf": {"alignment": 80, "entry_permission": True, "direction": a["direction"], "verdict": "GÜÇLÜ ONAY",
                                   "timeframes": {"15m": {"direction": a["direction"], "confidence": a["confidence"]},
                                                  "1h": {"direction": a["direction"], "confidence": 80}, "4h": {"direction": a["direction"], "confidence": 85}}}}


# ------------------------------------------------------------------ score reproducibility and decomposition
@pytest.mark.parametrize("seed,drift", [(1, 0.0), (2, 0.15), (3, -0.15), (4, 0.05), (5, -0.05), (6, 0.3), (7, -0.3), (8, 0.0)])
def test_reconstruct_score_matches_analysis(seed: int, drift: float) -> None:
    a = analysis.analyze(candles(seed=seed, drift=drift))
    rec = cs.reconstruct_score(a)
    assert rec["direction"] == a["direction"]
    assert rec["confidence"] == a["confidence"]


def test_same_input_same_score_and_row() -> None:
    c = candles(seed=3, drift=0.2)
    assert cs.snapshot_row("BTCUSDT", 1_750_000_000, legacy_for(c)) == cs.snapshot_row("BTCUSDT", 1_750_000_000, legacy_for(c))


def test_features_do_not_see_future_candles() -> None:
    """The snapshot of a decision must not change when later candles exist (the evaluator only receives closed candles up to the decision)."""
    full = candles(n=320, seed=9, drift=0.1)
    a = cs.snapshot_row("ETHUSDT", 1_750_000_000, legacy_for(full[:259]))
    b = cs.snapshot_row("ETHUSDT", 1_750_000_000, legacy_for(full[:259] + []))
    assert a == b
    tampered = [dict(x) for x in full]
    for x in tampered[259:]:
        x["close"] *= 3
    assert cs.snapshot_row("ETHUSDT", 1_750_000_000, legacy_for(tampered[:259])) == a


def test_missing_features_stay_none_not_invented() -> None:
    row = cs.snapshot_row("BTCUSDT", 1_750_000_000, {"analysis": {"direction": "LONG", "confidence": 90, "radar": {}}, "mtf": {}})
    assert row["rsi"] is None and row["trap_score"] is None and row["atr"] is None
    assert row["gates_ex_score"] is False and row["farmer_confirmed"] is False


def test_farmer_variants_follow_confirm_py() -> None:
    legacy = legacy_for(candles(seed=2, drift=0.2))
    a = legacy["analysis"]
    a.update(direction="LONG", confidence=94, momentum="Pozitif")
    a["radar"].update(trap_score=50, breakout_quality=70)
    legacy["mtf"].update(entry_permission=True, direction="LONG")
    row = cs.snapshot_row("SOLUSDT", 1_750_000_000, legacy)
    assert row["farmer_confirmed"] is True                  # CONFIDENCE variant ignores trap/breakout
    assert row["farmer_confirmed_quality"] is False         # STRATEGY_QUALITY needs trap <= 35


# ------------------------------------------------------------------ OOS protection and split
def test_oos_timestamps_are_refused() -> None:
    with pytest.raises(ValueError):
        cs.assert_not_oos(cs.OOS_START)
    with pytest.raises(ValueError):
        cs.snapshot_row("BTCUSDT", cs.OOS_START + 900, legacy_for(candles()))
    assert cs.period_of(cs.VALID_START - 900) == "TRAIN" and cs.period_of(cs.VALID_START) == "VALID" and cs.period_of(cs.OOS_START - 900) == "VALID"


def test_split_matches_c14_lock() -> None:
    from app import farmer_entry_research as er
    assert er.protocol_hash() == "c29ad788bd4c7a8c4400b14b4cb593c17dd33597404c469e6839f44a8e271cf6"
    assert (cs.TRAIN_START, cs.VALID_START, cs.OOS_START) == (cli.utc("2025-04-01"), cli.utc("2026-01-01"), cli.utc("2026-06-01"))


def test_manifest_with_oos_archives_is_refused(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps({"archives": [{"month": "2026-05", "path": "a"}, {"month": "2026-06", "path": "b"}]}))
    with pytest.raises(SystemExit):
        cli.guard_manifest(tmp_path)
    (tmp_path / "manifest.json").write_text(json.dumps({"archives": [{"month": "2026-05", "path": "a"}]}))
    cli.guard_manifest(tmp_path)


def test_outputs_must_stay_outside_repo() -> None:
    with pytest.raises(SystemExit):
        cli.outside_repo(Path(cli.__file__).resolve().parent / "x")


# ------------------------------------------------------------------ labels and candle boundary
def test_first_barrier_rules() -> None:
    hi = np.array([100.0, 101.0, 104.5, 100.0])
    lo = np.array([100.0, 99.5, 99.0, 90.0])
    assert cs.first_barrier(hi, lo, 0, 100.0, 2.0, "LONG")["result"] == 1        # +4 reached on bar 2 (low 99 > 96)
    assert cs.first_barrier(hi, lo, 0, 100.0, 2.0, "SHORT")["result"] == 0       # +4 above hit first
    both = cs.first_barrier(np.array([105.0]), np.array([95.0]), 0, 100.0, 2.0, "LONG")
    assert both["result"] == 0 and both["ambiguous"] is True                      # both in one candle -> STOP
    assert cs.first_barrier(np.array([101.0, 101.0]), np.array([99.0, 99.0]), 0, 100.0, 2.0, "LONG")["result"] is None


def test_label_uses_only_candles_from_entry_on() -> None:
    n, t0 = 60, 1_700_000_000
    hi, lo, cl = np.linspace(101, 130, n), np.linspace(99, 128, n), np.linspace(100, 129, n)
    base = cs.label_row(hi, lo, cl, 10, 100.0, 1.0, np.array([]), np.array([]), t0)
    past = hi.copy()
    past[:10] = 1e9
    lo2 = lo.copy()
    lo2[:10] = -1e9
    assert cs.label_row(past, lo2, cl, 10, 100.0, 1.0, np.array([]), np.array([]), t0) == base
    assert base["y_long"] == 1 and base["exit_idx_long"] >= 10


def test_label_funding_window_and_returns() -> None:
    n, t0 = 40, 1_700_000_000
    hi, lo, cl = np.full(n, 101.0), np.full(n, 99.0), np.full(n, 100.0)
    hi[5] = 103.0
    ft, fr = np.array([t0 + 900 * 3, t0 + 900 * 6, t0 + 900 * 7, t0 + 900 * 30]), np.array([0.001, 0.002, 0.25, 0.5])
    lab = cs.label_row(hi, lo, cl, 2, 100.0, 1.0, ft, fr, t0)
    assert lab["y_long"] == 1 and lab["exit_idx_long"] == 5
    assert lab["fund_sum_long"] == pytest.approx(0.003)           # settlements in (entry, exit-candle end]; one exactly at the end counts (C12 bisect_right rule)
    assert lab["ret_1_atr"] == 0.0 and lab["hold_long"] == 4


# ------------------------------------------------------------------ statistics
def test_wilson_and_summary() -> None:
    lo, hi = cs.wilson(57, 100)
    assert 0.47 < lo < 0.57 < hi < 0.67
    s = cs.summarize_outcomes(np.array([1, 1, 0, 1]))
    assert s["expectancy_r"] == 0.5 and s["profit_factor"] == 3.0 and s["n"] == 4
    assert cs.summarize_outcomes(np.array([], dtype=int)) == {"n": 0}


def test_cluster_spearman_detects_signal_and_null() -> None:
    n = 4000
    days = np.repeat(np.arange(80), n // 80)
    x = RNG.normal(size=n)
    y = (x + RNG.normal(size=n) * 2 > 0).astype(float)
    sig = ca.cluster_spearman(x, y, days, np.random.default_rng(1))
    assert sig["rho"] > 0.2 and sig["ci_low"] > 0
    null = ca.cluster_spearman(RNG.normal(size=n), y, days, np.random.default_rng(2))
    assert null["ci_low"] < 0 < null["ci_high"]


def test_holm_is_monotone_and_capped() -> None:
    adj = ca.holm([0.01, 0.04, 0.03, 0.5])
    assert adj[0] == pytest.approx(0.04) and max(adj) <= 1.0 and adj[3] >= adj[1] >= adj[2] >= 0.0


def test_max_drawdown_and_pf() -> None:
    assert cs.max_drawdown_r(np.array([1, 0, 0, 1, 0, 0, 0])) == 4.0
    assert math.isinf(cs.pf(3, 0))


# ------------------------------------------------------------------ prepare, normalisation, models
def frame(n: int = 600) -> pd.DataFrame:
    r = np.random.default_rng(5)
    rows = []
    for i in range(n):
        side = "LONG" if i % 2 else "SHORT"
        rows.append({"symbol": "BTCUSDT" if i % 3 else "ETHUSDT", "at": 1_744_516_800 + 900 * i, "direction": side, "confidence": float(r.choice([84, 90, 92, 94, 95])),
                     "ema20_dist_atr": r.normal(), "ema20_50_gap_atr": r.normal(), "ema50_200_gap_atr": r.normal(), "macd_hist_atr": r.normal(),
                     "rsi": float(r.uniform(30, 70)), "bb_pos": r.normal(), "range_pos": r.uniform(), "resistance_dist_atr": r.uniform(0, 8), "support_dist_atr": r.uniform(0, 8),
                     "c_price_gt_ema20": int(r.integers(0, 2)), "c_ema20_gt_ema50": int(r.integers(0, 2)), "c_ema50_gt_ema200": int(r.integers(0, 2)),
                     "dir_1h": side, "dir_4h": side if i % 5 else "BEKLE", "conf_1h": 80.0, "conf_4h": 85.0, "long_score": 80, "short_score": 55,
                     "adx": float(r.uniform(10, 40)), "atr_pct": float(r.uniform(0.2, 0.8)), "bb_width_pct": float(r.uniform(1, 4)), "volume_ratio": float(r.uniform(0.5, 2.5)),
                     "trap_score": float(r.uniform(10, 60)), "breakout_quality": float(r.uniform(30, 90)), "mtf_alignment": 80.0, "pts_trend": 30.0, "pts_momentum": 20.0,
                     "pts_volume": 6.0, "gates_ex_score": True, "farmer_confirmed": True, "momentum": "Pozitif" if side == "LONG" else "Negatif",
                     "y_long": float(r.integers(0, 2)), "y_short": float(r.integers(0, 2)), "ret_1_atr": 0.1, "ret_2_atr": 0.1, "ret_4_atr": 0.1, "ret_8_atr": 0.1,
                     "hold_long": 5, "hold_short": 5})
    rows[0]["y_long"] = rows[0]["y_short"] = np.nan                      # unresolved label must be dropped
    return pd.DataFrame(rows)


def test_prepare_alignment_and_unresolved_drop() -> None:
    raw = frame()
    d = ca.prepare(raw)
    assert len(d) == len(raw) - 1
    longs, shorts = d[d["direction"] == "LONG"], d[d["direction"] == "SHORT"]
    assert (longs["macd_d"] == longs["macd_hist_atr"]).all() and (shorts["macd_d"] == -shorts["macd_hist_atr"]).all()
    assert np.allclose(shorts["rsi_d"], 100 - shorts["rsi"]) and np.allclose(shorts["room_atr"], shorts["support_dist_atr"])
    assert set(d["y"].unique()) <= {0.0, 1.0}


def test_models_deterministic_serializable_and_missing_safe() -> None:
    d = ca.prepare(frame())
    quality = {k: {"auc": 0.5 + 0.01 * i, "p_cluster": 0.5, "spearman_y": 0.0, "p_holm": 1.0} for i, k in enumerate(ca.FEATURES)}
    feats = ca.prune_for_c(d, quality)
    assert 1 <= len(feats) <= ca.C_MAX_FEATURES
    mc = ca.fit_c(d, feats)
    mb = ca.fit_b(d, [{"feature": "adx", "sign": 1}, {"feature": "atr_pct", "sign": -1}])
    mc2, mb2 = json.loads(json.dumps(mc)), json.loads(json.dumps(mb))
    assert np.array_equal(ca.score_c(mc, d), ca.score_c(mc2, d)) and np.array_equal(ca.score_b(mb, d), ca.score_b(mb2, d))
    holes = d.copy()
    holes.loc[holes.index[:5], "adx"] = np.nan
    holes.loc[holes.index[:5], feats[0]] = np.nan
    assert np.isfinite(ca.score_c(mc, holes)).all() and np.isfinite(ca.score_b(mb, holes)).all()
    assert ca.score_b({"features": []}, d).tolist() == [50.0] * len(d)
    p = ca.score_c(mc, d)
    assert ((p > 0) & (p < 1)).all()


def test_top_fraction_ties_are_outcome_blind_and_stable() -> None:
    s = np.array([5.0] * 50 + [1.0] * 50)
    m1, m2 = ca.top_fraction(s, 0.2), ca.top_fraction(s, 0.2)
    assert m1.sum() == 20 and (m1 == m2).all() and m1[:50].sum() == 20
    assert ca.top_fraction(s, 0.2, bottom=True)[50:].sum() == 20


def test_thin_by_hold_removes_overlap() -> None:
    d = pd.DataFrame({"symbol": ["A"] * 6, "at": [0, 900, 1800, 2700, 3600, 4500], "direction": ["LONG"] * 6, "hold_long": [3, 3, 3, 3, 3, 3], "hold_short": [3] * 6})
    kept = ca.thin_by_hold(d)
    assert kept["at"].tolist() == [0, 2700]


def test_classification_rule() -> None:
    def rep(rho: float, lo: float, mono: float) -> dict:
        return {"spearman_score_y": {"rho": rho, "ci_low": lo}, "quintile_monotonic_rho": mono}
    flat = rep(0.0, -0.02, 0.0)
    good = rep(0.05, 0.02, 1.0)
    assert ca.classify({"A": flat, "B": flat, "C": flat}, {"A": flat, "B": flat, "C": flat})["decision"].startswith("B")
    assert ca.classify({"A": flat, "B": good, "C": flat}, {"A": flat, "B": flat, "C": flat})["decision"].startswith("C")
    assert ca.classify({"A": flat, "B": good, "C": flat}, {"A": flat, "B": good, "C": flat})["decision"].startswith("A")


def test_research_package_is_isolated_from_production() -> None:
    root = Path(__file__).resolve().parents[1] / "app"
    banned = ("import_module(\"app.research", "from .research", "from app.research", "app.research.")
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not any(b in text for b in banned), f"{path.name} imports research code"
    for path in (root / "research").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("binance_demo", "execution_core", "exchange_connections", "requests.", "httpx", "websocket"):
            assert forbidden not in text, f"{path.name} touches {forbidden}"
