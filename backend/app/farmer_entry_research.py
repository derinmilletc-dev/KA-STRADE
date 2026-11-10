"""KAIS FARMER entry research (C14): CLOSED-CANDLE BREAKOUT + RETEST CONFIRMATION. Research only; nothing here is imported by the app.

Layers: DATA (numpy arrays) -> SIGNAL GENERATION -> EVENT DETECTION -> ENTRY LABEL -> OUTCOME -> STATISTICS. No network, no order, no Farmer
execution code. Every decision uses only candles that are CLOSED at the decision time (tests check that a prefix of the data gives the same
signals as the full data).

All numbers live in `PROTOCOL` (frozen). Interpretation decisions the protocol text left open are written down in `INTERPRETATION` and are
locked together with the numbers before any outcome is computed.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

BAR = 900


@dataclass(frozen=True)
class Protocol:
    range_window: int = 20
    compression_max_atr: float = 4.0
    breakout_body_atr: float = 0.35
    long_close_location: float = 0.65
    short_close_location: float = 0.35
    retest_window: int = 3
    retest_tolerance_atr: float = 0.20
    confirmation_body_atr: float = 0.20
    target_atr: float = 2.0
    stop_atr: float = 2.0
    primary_success_rate: float = 0.55
    confidence_level: float = 0.95
    minimum_oos_trades: int = 100
    minimum_expectancy_r: float = 0.05
    minimum_profit_factor: float = 1.10
    atr_period: int = 14
    atr_warmup_bars: int = 100               # detection starts after this many candles so the recursive ATR has converged
    # data split (UTC, [start, end)). Locked before any outcome was computed.
    # Written by the user as TRAIN 2025-04-01..2025-12-31, VALID 2026-01-01..2026-05-31, OOS 2026-06-01..2026-09-30 (inclusive days) = [start, end) below.
    train: tuple[str, str] = ("2025-04-01", "2026-01-01")
    valid: tuple[str, str] = ("2026-01-01", "2026-06-01")
    oos: tuple[str, str] = ("2026-06-01", "2026-10-01")


PROTOCOL = Protocol()

INTERPRETATION = [
    "Candles: Binance USD-M CONTRACT 15m klines for signals and entry; MARK price 15m high/low for barrier hits (same basis as the Farmer trigger).",
    ("ATR14 = Wilder recursive ATR over the whole series (causal). Setup ATR = ATR14 through the candle BEFORE the breakout candle; it is used for the "
     "compression, breakout body, retest tolerance and confirmation body. Entry ATR = ATR14 through the confirmation candle; it sets the +/-2 ATR barriers."),
    "Range = previous 20 closed candles (excluding the breakout candle). Breakout level = range_high (LONG) / range_low (SHORT).",
    ("Retest candle j (first candle in the 3-candle window): LONG low <= level + 0.20*ATR and close >= level (SHORT mirrored). A candle in the window that "
     "closes beyond the level against the breakout INVALIDATES the setup. No retest in 3 candles = EXPIRED."),
    "Confirmation = the single candle right after the retest candle; if it fails the setup is FAILED_CONFIRMATION and is not retried.",
    "Body test is abs(close-open)/ATR as written in the protocol (direction of the body is not required).",
    "Entry = CONTRACT open of the candle after the confirmation candle. Barriers = entry +/- 2.0*entry-ATR, STOP checked first in a candle that touches both.",
    ("Independence: after a breakout candle k, no new breakout (either side) on that symbol before candle k+21, so the 20-candle range window of a new "
     "setup contains no candle of the previous setup (= new compression)."),
    "Signals whose barriers are not decided before the end of the data are excluded and counted. A signal belongs to the period of its entry time.",
    "Success: LONG = +2 ATR first, SHORT = -2 ATR first. Gross R = +1 / -1. Costs: 5 bps taker per side, 2 or 5 bps adverse slippage per side, real funding.",
    "Pre-registered CI = Wilson 95% on the success rate (day-block bootstrap is reported as a robustness view only).",
    ("DATA COVERAGE: the available Binance archives are 2025-03-01..2025-09-30 and 2026-03-01..2026-09-30. TRAIN therefore effectively covers 2025-04-01.."
     "2025-09-30 and VALID 2026-03-01..2026-05-31 (the first ~100 candles of each block are ATR warm-up). The locked date ranges are NOT changed."),
    "Signals are detected on each continuous data block separately (2025 block, 2026 block). A signal belongs to the period of its ENTRY time.",
    "PASS needs ALL of: OOS N >= 100; OOS win rate >= 55%; Wilson 95% lower bound > 50%; OOS expectancy > +0.05R; OOS profit factor >= 1.10. N < 100 = INCONCLUSIVE.",
    ("Stability (secondary): gross expectancy > 0 in at least two of TRAIN / VALID / OOS. Failure 6 (small-subgroup dependence), made operational BEFORE the run: "
     "OOS win rate <= 50% for LONG or for SHORT, or fewer than two symbols with OOS win rate > 50%."),
    ("Baselines on the same signals: A random direction = exact expectation (mean of LONG and SHORT outcome per signal); B = current Farmer entry (confirmed, "
     "min score 90) labelled with the same +/-2 ATR rule, one open signal per symbol; C = reverse direction (diagnostic only)."),
]


def protocol_hash() -> str:
    blob = json.dumps({"protocol": asdict(PROTOCOL), "interpretation": INTERPRETATION}, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


# ------------------------------------------------------------------------------------------------ indicators
def wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Causal Wilder ATR. atr[i] uses candles <= i only; the first `period` values are NaN."""
    n = len(close)
    out = np.full(n, np.nan)
    if n <= period:
        return out
    prev = close[:-1]
    tr = np.maximum.reduce([high[1:] - low[1:], np.abs(high[1:] - prev), np.abs(low[1:] - prev)])    # tr[i-1] belongs to candle i
    value = float(tr[:period].mean())
    out[period] = value
    for i in range(period + 1, n):
        value = (value * (period - 1) + tr[i - 1]) / period
        out[i] = value
    return out


# ------------------------------------------------------------------------------------------------ event detection
ENTRY, EXPIRED, INVALIDATED, FAILED_CONFIRMATION = "ENTRY", "EXPIRED", "INVALIDATED", "FAILED_CONFIRMATION"


def detect_setups(o: np.ndarray, h: np.ndarray, low: np.ndarray, c: np.ndarray, atr: np.ndarray, cfg: Protocol = PROTOCOL) -> list[dict[str, Any]]:
    """Every breakout structure with its fate. Index i = candle open order; a decision at the close of candle m may use candles <= m only."""
    n, w = len(c), cfg.range_window
    first = max(cfg.atr_warmup_bars, w + cfg.atr_period + 1)
    events: list[dict[str, Any]] = []
    next_allowed = first
    k = first
    while k < n:
        if k < next_allowed or not np.isfinite(atr[k - 1]) or atr[k - 1] <= 0:
            k += 1
            continue
        a = float(atr[k - 1])
        range_high, range_low = float(h[k - w:k].max()), float(low[k - w:k].min())
        if (range_high - range_low) / a > cfg.compression_max_atr:
            k += 1
            continue
        span = float(h[k] - low[k])
        loc = (c[k] - low[k]) / span if span > 0 else 0.5
        side = None
        if c[k] > range_high and (c[k] - o[k]) / a >= cfg.breakout_body_atr and loc >= cfg.long_close_location:
            side, level = "LONG", range_high
        elif c[k] < range_low and (o[k] - c[k]) / a >= cfg.breakout_body_atr and loc <= cfg.short_close_location:
            side, level = "SHORT", range_low
        if side is None:
            k += 1
            continue
        event = {"breakout_idx": k, "side": side, "level": level, "setup_atr": a, "range_width_atr": (range_high - range_low) / a, "state": EXPIRED}
        next_allowed = k + w + 1
        retest = None
        for j in range(k + 1, min(k + cfg.retest_window, n - 1) + 1):
            against = c[j] < level if side == "LONG" else c[j] > level
            if against:
                event["state"] = INVALIDATED
                break
            touched = low[j] <= level + cfg.retest_tolerance_atr * a if side == "LONG" else h[j] >= level - cfg.retest_tolerance_atr * a
            if touched:
                retest = j
                break
        if retest is not None:
            event["retest_idx"] = retest
            m = retest + 1
            if m >= n:
                event["state"] = "PENDING_END_OF_DATA"
            else:
                body = abs(c[m] - o[m]) / a
                beyond = c[m] > h[retest] if side == "LONG" else c[m] < low[retest]
                if beyond and body >= cfg.confirmation_body_atr:
                    event.update(state=ENTRY, confirm_idx=m, entry_idx=m + 1, entry_atr=float(atr[m]))
                else:
                    event["state"] = FAILED_CONFIRMATION
        events.append(event)
        k += 1
    return events


# ------------------------------------------------------------------------------------------------ outcome
def label_outcome(mark_high: np.ndarray, mark_low: np.ndarray, entry_idx: int, entry_price: float, atr_entry: float, side: str,
                  target_atr: float = PROTOCOL.target_atr, stop_atr: float = PROTOCOL.stop_atr) -> dict[str, Any]:
    """First barrier hit from the entry candle on. Both touched in one candle -> STOP. Not decided before the data ends -> UNRESOLVED."""
    up, down = entry_price + target_atr * atr_entry, entry_price - stop_atr * atr_entry
    if side == "LONG":
        target_hit, stop_hit = mark_high[entry_idx:] >= up, mark_low[entry_idx:] <= down
    else:
        target_hit, stop_hit = mark_low[entry_idx:] <= entry_price - target_atr * atr_entry, mark_high[entry_idx:] >= entry_price + stop_atr * atr_entry
    hit = target_hit | stop_hit
    if not hit.any():
        return {"result": "UNRESOLVED", "exit_idx": None, "ambiguous": False}
    i = int(np.argmax(hit))
    stop_first = bool(stop_hit[i])
    return {"result": "STOP" if stop_first else "TARGET", "exit_idx": entry_idx + i, "ambiguous": bool(stop_hit[i] and target_hit[i])}


def cost_r(entry_price: float, atr_entry: float, side: str, funding_sum_rate: float, slippage_bps: float, taker_fee: float = 0.0005,
           stop_atr: float = PROTOCOL.stop_atr) -> dict[str, float]:
    """Round-trip cost expressed in R (1R = stop distance). funding_sum_rate = sum of settlement rates inside the holding window (positive rate
    costs a LONG, pays a SHORT)."""
    r_fraction = stop_atr * atr_entry / entry_price
    sign = 1 if side == "LONG" else -1
    return {"commission_r": 2 * taker_fee / r_fraction, "slippage_r": 2 * slippage_bps / 10_000 / r_fraction, "funding_r": sign * funding_sum_rate / r_fraction}


# ------------------------------------------------------------------------------------------------ statistics
def wilson(successes: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    if n == 0:
        return float("nan"), float("nan")
    z = _z(confidence)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


def _z(confidence: float) -> float:
    return {0.90: 1.6448536269514722, 0.95: 1.959963984540054, 0.99: 2.5758293035489004}[round(confidence, 2)]


def binom_z(successes: int, n: int, p0: float = 0.5) -> dict[str, float]:
    if n == 0:
        return {"z": float("nan"), "p_two_sided": float("nan")}
    z = (successes - n * p0) / math.sqrt(n * p0 * (1 - p0))
    return {"z": z, "p_two_sided": math.erfc(abs(z) / math.sqrt(2))}


def profit_factor(wins: int, losses: int) -> float:
    return float("inf") if losses == 0 and wins > 0 else (wins / losses if losses else float("nan"))


def max_drawdown_r(results_in_exit_order: list[float]) -> float:
    curve, peak, worst = 0.0, 0.0, 0.0
    for x in results_in_exit_order:
        curve += x
        peak = max(peak, curve)
        worst = max(worst, peak - curve)
    return worst


def edge_stats(outcomes: list[bool], confidence: float = 0.95) -> dict[str, Any]:
    n, k = len(outcomes), int(sum(outcomes))
    lo, hi = wilson(k, n, confidence)
    out = {"n": n, "wins": k, "losses": n - k, "win_rate": k / n if n else float("nan"), "ci_low": lo, "ci_high": hi,
           "expectancy_r": (2 * k - n) / n if n else float("nan"), "profit_factor": profit_factor(k, n - k)}
    out.update(binom_z(k, n))
    return out


def day_bootstrap_ci(outcomes: list[bool], days: list[int], rng: np.random.Generator, reps: int = 4000, confidence: float = 0.95) -> tuple[float, float]:
    if len(outcomes) < 2:
        return float("nan"), float("nan")
    ids, inv = np.unique(np.array(days), return_inverse=True)
    wins = np.bincount(inv, weights=np.array(outcomes, dtype=float), minlength=len(ids))
    cnt = np.bincount(inv, minlength=len(ids)).astype(float)
    pick = rng.integers(0, len(ids), size=(reps, len(ids)))
    rate = wins[pick].sum(1) / cnt[pick].sum(1)
    a = (1 - confidence) / 2
    return float(np.quantile(rate, a)), float(np.quantile(rate, 1 - a))


# ------------------------------------------------------------------------------------------------ independent reference checks
# Written straight from the protocol text with a different structure than `detect_setups` / `label_outcome`. They only verify the main code.
def reference_detect(o: np.ndarray, h: np.ndarray, low: np.ndarray, c: np.ndarray, atr: np.ndarray, cfg: Protocol = PROTOCOL,
                     suppressed: list[int] | None = None) -> list[dict[str, Any]]:
    w, n = cfg.range_window, len(c)
    first = max(cfg.atr_warmup_bars, w + cfg.atr_period + 1)

    def breakout_at(k: int) -> tuple[str, float, float] | None:
        a = atr[k - 1]
        if not np.isfinite(a) or a <= 0:
            return None
        prior_high, prior_low = max(h[i] for i in range(k - w, k)), min(low[i] for i in range(k - w, k))
        if (prior_high - prior_low) / a > cfg.compression_max_atr:
            return None
        rng_ = h[k] - low[k]
        close_loc = (c[k] - low[k]) / rng_ if rng_ > 0 else 0.5
        if c[k] > prior_high and (c[k] - o[k]) / a >= cfg.breakout_body_atr and close_loc >= cfg.long_close_location:
            return "LONG", prior_high, a
        if c[k] < prior_low and (o[k] - c[k]) / a >= cfg.breakout_body_atr and close_loc <= cfg.short_close_location:
            return "SHORT", prior_low, a
        return None

    candidates = [k for k in range(first, n) if breakout_at(k)]
    kept: list[int] = []
    for k in candidates:                                   # independence: a new structure needs k >= previous + range_window + 1
        if not kept or k >= kept[-1] + w + 1:
            kept.append(k)
    if suppressed is not None:
        suppressed.append(len(candidates) - len(kept))
    out = []
    for k in kept:
        side, level, a = breakout_at(k)                    # type: ignore[misc]
        tol = cfg.retest_tolerance_atr * a
        state, retest = EXPIRED, None
        for j in range(k + 1, k + cfg.retest_window + 1):
            if j >= n:
                break
            if (side == "LONG" and c[j] < level) or (side == "SHORT" and c[j] > level):
                state = INVALIDATED
                break
            near = (low[j] <= level + tol) if side == "LONG" else (h[j] >= level - tol)
            if near:
                retest = j
                break
        item = {"breakout_idx": k, "side": side, "state": state}
        if retest is not None:
            m = retest + 1
            if m >= n:
                item["state"] = "PENDING_END_OF_DATA"
            else:
                ok_close = c[m] > h[retest] if side == "LONG" else c[m] < low[retest]
                ok_body = abs(c[m] - o[m]) >= cfg.confirmation_body_atr * a
                item.update(state=ENTRY if (ok_close and ok_body) else FAILED_CONFIRMATION, retest_idx=retest)
                if item["state"] == ENTRY:
                    item.update(confirm_idx=m, entry_idx=m + 1)
        out.append(item)
    return out


def verify_entry_event(e: dict[str, Any], o: np.ndarray, h: np.ndarray, low: np.ndarray, c: np.ndarray, atr: np.ndarray, cfg: Protocol = PROTOCOL) -> list[str]:
    """Re-derive every rule of one ENTRY event from the raw candles. Returns the list of violated rules (empty = correct)."""
    bad: list[str] = []
    k, j, m, i, side = e["breakout_idx"], e["retest_idx"], e["confirm_idx"], e["entry_idx"], e["side"]
    w, a = cfg.range_window, float(atr[k - 1])
    hi, lo = float(h[k - w:k].max()), float(low[k - w:k].min())
    if abs(a - e["setup_atr"]) > 1e-12:
        bad.append("setup_atr is not ATR of the candle before the breakout")
    if (hi - lo) / a > cfg.compression_max_atr:
        bad.append("not compressed")
    loc = (c[k] - low[k]) / (h[k] - low[k])
    if side == "LONG":
        if not (c[k] > hi and (c[k] - o[k]) / a >= cfg.breakout_body_atr and loc >= cfg.long_close_location):
            bad.append("long breakout rule")
        if not (low[j] <= hi + cfg.retest_tolerance_atr * a and c[j] >= hi):
            bad.append("long retest rule")
        if not (c[m] > h[j]):
            bad.append("long confirmation close")
        if any(c[x] < hi for x in range(k + 1, j + 1)):
            bad.append("close back inside before retest")
    else:
        if not (c[k] < lo and (o[k] - c[k]) / a >= cfg.breakout_body_atr and loc <= cfg.short_close_location):
            bad.append("short breakout rule")
        if not (h[j] >= lo - cfg.retest_tolerance_atr * a and c[j] <= lo):
            bad.append("short retest rule")
        if not (c[m] < low[j]):
            bad.append("short confirmation close")
        if any(c[x] > lo for x in range(k + 1, j + 1)):
            bad.append("close back inside before retest")
    if not (k < j <= k + cfg.retest_window):
        bad.append("retest outside window")
    if abs(c[m] - o[m]) / a < cfg.confirmation_body_atr:
        bad.append("confirmation body too small")
    if m != j + 1:
        bad.append("confirmation is not the candle right after the retest")
    if i != m + 1:
        bad.append("entry is not the candle after confirmation")
    if abs(float(atr[m]) - e["entry_atr"]) > 1e-12:
        bad.append("entry ATR is not ATR through the confirmation candle")
    return bad


def reference_label(mark_high: np.ndarray, mark_low: np.ndarray, entry_idx: int, entry: float, atr_entry: float, side: str,
                    target_atr: float = PROTOCOL.target_atr, stop_atr: float = PROTOCOL.stop_atr) -> dict[str, Any]:
    sign = 1 if side == "LONG" else -1
    target, stop = entry + sign * target_atr * atr_entry, entry - sign * stop_atr * atr_entry
    for i in range(entry_idx, len(mark_high)):
        hit_target = mark_high[i] >= target if sign == 1 else mark_low[i] <= target
        hit_stop = mark_low[i] <= stop if sign == 1 else mark_high[i] >= stop
        if hit_stop:
            return {"result": "STOP", "exit_idx": i, "ambiguous": bool(hit_target)}
        if hit_target:
            return {"result": "TARGET", "exit_idx": i, "ambiguous": False}
    return {"result": "UNRESOLVED", "exit_idx": None, "ambiguous": False}
