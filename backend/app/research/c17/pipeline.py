"""C17 pipeline: run the three detectors on a block of 15m candles, label every decision, build the universe table used by the baselines."""
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
import pandas as pd

from app.research import c15_scoring as cs
from app.research.c16 import structure as st
from app.research.c17 import events as ev
from app.research.c17 import labels as lb

BAR = cs.BAR
SIDES = ("LONG", "SHORT")
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
HYPS = ("H1_BREAKOUT_RETEST_RECLAIM", "H2_LIQUIDITY_SWEEP_REVERSAL", "H3_COMPRESSION_DISPLACEMENT_CONTINUATION")


def detect(t: np.ndarray, o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, v: np.ndarray, side: str, until: int | None = None):
    """All setups of one orientation. `until` = last candle index fed (everything after it is never seen). Returns (setups, inventory, atr list, swings)."""
    if side == "SHORT":
        o, h, l, c, v = st.mirror(o, h, l, c, v)
    eng, dets, setups = st.SideEngine(), [d() for d in ev.DETECTORS], []
    atr_at: dict[int, float] = {}
    end = len(t) if until is None else until + 1
    for j in range(end):
        feat = eng.push(int(t[j]), float(o[j]), float(h[j]), float(l[j]), float(c[j]), float(v[j]))
        if eng.atr is not None:
            atr_at[j] = eng.atr
        for d in dets:
            if eng.atr is None:
                continue
            for s in d.step(eng):
                s["side"] = side
                s["vol_tercile"] = feat["vol_tercile"] if feat else float("nan")      # descriptive regimes only (never a filter)
                s["trend_regime"] = feat["trend_regime"] if feat else float("nan")
                s["swing_max_confirmed_idx"] = max([x[1] for x in eng.sh + eng.sl if x[1] <= j] or [-1])
                setups.append(s)
    inv = {d.name: dict(d.inv) for d in dets}
    return setups, inv, atr_at, eng


def arrays_from(ds: Any, symbol: str, start: int, end: int):
    rows = [r for r in ds.frames[symbol]["15m"].rows if start <= r["time"] < end]
    t = np.array([r["time"] for r in rows], dtype=np.int64)
    return (t, *(np.array([r[k] for r in rows], dtype=float) for k in ("open", "high", "low", "close", "volume")))


def run_block(ds: Any, mk: Any, symbol: str, start: int, end: int, dec_from: int, dec_to: int) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Returns (setups, universe, inventory). Decisions are kept only for dec_from <= decision_ts <= dec_to."""
    t, o, h, l, c, v = arrays_from(ds, symbol, start, end)
    mh, ml, mc = mk.mhigh[symbol], mk.mlow[symbol], mk.mclose[symbol]
    setups, inv, atr_at = [], {}, {}
    for side in SIDES:
        s, i, a, _eng = detect(t, o, h, l, c, v, side)
        setups += s
        inv[side] = i
        atr_at = a if side == "LONG" else atr_at
    uni_rows = []
    lab_cache: dict[int, dict] = {}
    for j, atr in atr_at.items():
        at = int(t[j]) + BAR
        d = j + 1
        if not (dec_from <= at <= dec_to) or d >= mk.n:
            continue
        lab = lb.label_decision(mh, ml, mc, d, float(mk.copen[symbol][d]), atr, mk.fund_t[symbol], mk.fund_r[symbol], start)
        lab_cache[j] = lab | {"entry_price": float(mk.copen[symbol][d]), "atr": atr}
        uni_rows.append({"symbol": symbol, "at": at, "j": j, **{k: lab_cache[j].get(k) for k in ("y_long", "y_short", "entry_price", "atr")}})
    uni = pd.DataFrame(uni_rows)
    rows = []
    for s in setups:
        j = s["confirm_idx"]
        if j not in lab_cache:
            continue
        lab, side, key = lab_cache[j], s["side"], s["side"].lower()
        at = int(t[j]) + BAR
        rows.append({"symbol": symbol, "hyp": s["hyp"], "side": side, "level": s["level"], "level_known_ts": int(t[s["level_known_idx"]]) + BAR,
                     "setup_ts": int(t[s["setup_idx"]]), "retest_ts": None if s["retest_idx"] is None else int(t[s["retest_idx"]]), "confirmation_ts": int(t[j]),
                     "entry_ts": int(t[j]) + BAR, "decision_ts": at, "swing_confirmed_ts": int(t[s["swing_max_confirmed_idx"]]) + BAR, "pivot": s["pivot"],
                     "entry_price": lab["entry_price"], "atr": lab["atr"], "y": lab.get(f"y_{key}"), "hold": lab.get(f"hold_{key}"), "y1": lab.get(f"y1_{key}"),
                     "mfe_r": lab.get(f"mfe_{key}_r"), "mae_r": lab.get(f"mae_{key}_r"), "cost_r": lab.get(f"cost_r_{key}"), "amb": lab.get(f"amb_{key}"),
                     "y_reverse": lab.get("y_short" if side == "LONG" else "y_long"), "vol_tercile": s["vol_tercile"], "trend_regime": s["trend_regime"]})
    inv_total = Counter()
    for side in SIDES:
        for hyp, dct in inv[side].items():
            for k, n in dct.items():
                inv_total[f"{hyp}|{side}|{k}"] += n
    return pd.DataFrame(rows), uni, dict(inv_total)
