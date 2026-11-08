"""KAIS FARMER offline backtest simulator (C12).

Real Farmer pure functions are imported, not re-implemented: `confirm` (entry confirmation), `farmer_spec` / `levels_from_avg`
(sizing, target and stop from the REAL average fill), `effective_capacity` (slot capacity, 100 USDT hard cap) and `extract_symbol_filters`
(tick / step / min notional). Candidates come from the unmodified Original V2 evaluator via `FarmerFeed` rules (closed 15m candle, 259
closed candles per interval, no look-ahead). The glue below only replays closed candles: it has no network client and never touches
a trading API or any production module.

Execution assumptions (all explicit, see `ASSUMPTIONS`): market entry at the NEXT 15m contract open plus adverse slippage; stop and
take-profit trigger on MARK price OHLC; both in one bar -> STOP first; gap beyond a trigger at bar open -> contract open plus slippage;
taker fee 5 bps per side (`TAKER_FEE`); funding = -side * qty * mark_open(settlement bar) * rate for settlements after entry up to the exit;
net = gross - commission + funding.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .dca_engine.plan import TAKER_FEE
from .farmer_engine.confirm import CONFIDENCE, ConfirmParams, FarmerCandidate, confirm
from .farmer_engine.engine import farmer_spec, levels_from_avg
from .farmer_engine.margin import HARD_CAP_USDT, effective_capacity
from .grid_engine.filters import extract_symbol_filters

BAR = 900
DEAD_CONFIRMATIONS = 2          # FarmerSettings.dead_confirmations default (Faz B 6.4)
SAFETY_MARGIN_USDT = 0.05       # FarmerSettings.safety_margin_usdt default
BUFFER_USDT = 0.05              # confirm.DEFAULT_BUFFER_USDT

ASSUMPTIONS = [
    "Candidates: unmodified Original V2 live evaluator on CLOSED 15m/1h/4h candles (259 each, gap-free); decision time = close of the 15m bar.",
    "Entry: MARKET at the next 15m contract OPEN plus adverse slippage; quantity from the planned price (candidate entry) like farmer_spec.",
    "Target/stop: recomputed from the real fill average with the real levels_from_avg (stop first, then target; reduce-only quantity).",
    "Triggers on MARK price OHLC. Both in one bar -> STOP first. Gap at bar open beyond a trigger -> contract open plus adverse slippage.",
    "TAKE_PROFIT_MARKET / priceProtect / already-crossed error behaviours are NOT VERIFIED on Binance; modelled as trigger-then-market fill.",
    "Fees: 5 bps taker per side (TAKER_FEE). No maker fills. Funding: -side*qty*mark_open(settlement bar)*rate, settlements after entry up to exit.",
    "Net = gross - commission + funding. Unknown funding is never zero-filled (none missing in this dataset).",
    ("Dead close: 2 consecutive closed candles adverse (confidence<dead score, opposite direction AND momentum), only if estimated net > 0.05 USDT; "
     "never closes at a loss. Executes as a market close at the next bar open plus slippage."),
    "Slot lifecycle: a slot and its symbol stay occupied until flat plus cooldown (default 900 s). Range exits are assumed flat at bar end.",
    "Same symbol+candle lock: one attempt per symbol per candle. Rejected candidates consume no slot.",
    "Exchange metadata (tick, step, min qty, min notional) is TODAY's public exchangeInfo, NOT historical (conditional).",
    "Positions still open at the end of the data are excluded from net statistics and counted separately.",
]


@dataclass
class Market:
    """15m arrays aligned on one grid (index i opens at t0 + 900*i); contract klines, mark klines, funding, filters."""
    t0: int
    n: int
    symbols: list[str]
    copen: dict[str, np.ndarray] = field(default_factory=dict)
    mopen: dict[str, np.ndarray] = field(default_factory=dict)
    mhigh: dict[str, np.ndarray] = field(default_factory=dict)
    mlow: dict[str, np.ndarray] = field(default_factory=dict)
    mclose: dict[str, np.ndarray] = field(default_factory=dict)
    fund_t: dict[str, np.ndarray] = field(default_factory=dict)
    fund_r: dict[str, np.ndarray] = field(default_factory=dict)
    rules: dict[str, dict[str, Any]] = field(default_factory=dict)


def build_market(dataset: Any, start: int, end: int) -> Market:
    """From a validated `backtest_data.Dataset`. Refuses any gap: arrays must be complete on the grid (no fill, no interpolation)."""
    n = (end - start) // BAR
    mk = Market(start, n, sorted(dataset.frames))
    for sym in mk.symbols:
        contract, mark = dataset.frames[sym]["15m"], dataset.marks[sym]
        for series, label in ((contract, "contract"), (mark, "mark")):
            rows = [r for r in series.rows if start <= r["time"] < end]
            if len(rows) != n or any(r["time"] != start + BAR * i for i, r in enumerate(rows)):
                raise ValueError(f"{sym} {label} 15m series is not complete on the grid")
        crows = [r for r in contract.rows if start <= r["time"] < end]
        mrows = [r for r in mark.rows if start <= r["time"] < end]
        mk.copen[sym] = np.array([r["open"] for r in crows])
        mk.mopen[sym] = np.array([r["open"] for r in mrows])
        mk.mhigh[sym] = np.array([r["high"] for r in mrows])
        mk.mlow[sym] = np.array([r["low"] for r in mrows])
        mk.mclose[sym] = np.array([r["close"] for r in mrows])
        events = dataset.funding[sym]
        mk.fund_t[sym] = np.array([e["time"] for e in events])
        mk.fund_r[sym] = np.array([e["rate"] for e in events])
        mk.rules[sym] = extract_symbol_filters(dataset.metadata["exchange_info"], sym)
    return mk


@dataclass
class Candidates:
    """Decision-time candidate arrays, index d = (at - t0)/900 where `at` is the close of the decision bar (entry bar = d)."""
    rows: dict[str, dict[int, dict[str, Any]]]
    n: int
    # adverse[(dead_score, side)][symbol] -> bool array over closed-bar index k (= d - 1)
    adverse: dict[tuple, dict[str, np.ndarray]] = field(default_factory=dict)


def build_candidates(cache: dict[str, dict], t0: int, n: int, dead_scores: tuple) -> Candidates:
    rows: dict[str, dict[int, dict[str, Any]]] = {}
    for sym, payload in cache.items():
        rows[sym] = {(r["at"] - t0) // BAR: r for r in payload["rows"]}
    cand = Candidates(rows, n)
    for dead in dead_scores:
        if dead is None:
            continue
        for side, opp_dir, opp_mom in (("LONG", "SHORT", "Negatif"), ("SHORT", "LONG", "Pozitif")):
            per_symbol = {}
            for sym, table in rows.items():
                flags = np.zeros(n + 1, dtype=bool)
                for d, r in table.items():
                    # same rule as FarmerEngine.is_adverse: confidence < dead AND opposite direction AND opposite momentum
                    c = r["confidence"]
                    if c is not None and c < dead and r["direction"] == opp_dir and r["momentum"] == opp_mom and 1 <= d <= n:
                        flags[d - 1] = True                       # closed bar k = d - 1
                per_symbol[sym] = flags
            cand.adverse[(dead, side)] = per_symbol
    return cand


def farmer_candidate(sym: str, row: dict[str, Any]) -> FarmerCandidate:
    analysis = {"direction": row["direction"], "confidence": row["confidence"], "momentum": row["momentum"], "atr": row["atr"],
                "entry": row["entry"], "radar": {"trap_score": row["trap"], "breakout_quality": row["breakout"]}}
    mtf = {"entry_permission": row["mtf_permission"], "direction": row["mtf_direction"], "alignment": row["mtf_alignment"],
           "short_filter_v2": {"accepted": row["sf_accepted"]} if row["sf_present"] else None}
    return FarmerCandidate(sym, "15m", (row["at"] - BAR) * 1000, analysis, mtf)


def confirmed_table(cand: Candidates, margin: float, leverage: float, target: float, max_loss: float, min_score: int,
                    variant: str = CONFIDENCE) -> list[tuple[int, float, str]]:
    """Real `confirm()` over every tradable candidate, scored >= min_score. Returns [(d, -confidence, symbol)] sorted like
    `CandidateCache.entry_candidates` (confidence desc, then symbol) within each decision bar."""
    out: list[tuple[int, float, str]] = []
    params = ConfirmParams(variant, int(min_score), float(margin), float(leverage), float(target), float(max_loss))
    for sym, table in cand.rows.items():
        for d, r in table.items():
            if r["direction"] not in ("LONG", "SHORT") or r["confidence"] is None or r["confidence"] < min_score:
                continue
            if confirm(farmer_candidate(sym, r), params).confirmed:
                out.append((d, -float(r["confidence"]), sym))
    out.sort()
    return out


def simulate(mk: Market, cand: Candidates, confirmed: list[tuple[int, float, str]], *, margin: float, leverage: float, target: float,
             max_loss: float, max_positions: int, dead_score: float | None, slippage_bps: float, cooldown_s: int = 900,
             total_cap: float = HARD_CAP_USDT, start_d: int = 0, end_d: int | None = None,
             intrabar: str = "STOP_FIRST") -> dict[str, Any]:
    """One continuous replay. Returns trades (closed), open_at_end, rejection counters."""
    slip = slippage_bps / 10_000.0
    capacity = effective_capacity(max_positions, min(total_cap, HARD_CAP_USDT), margin)
    if total_cap > HARD_CAP_USDT:                                    # RESEARCH ONLY: lifted cap (margin 150); never a production setting
        capacity = max(0, min(int(max_positions), math.floor(total_cap / margin + 1e-9)))
    end_d = mk.n if end_d is None else end_d
    trades: list[dict[str, Any]] = []
    open_at_end: list[dict[str, Any]] = []
    rejects = {"NO_SLOT": 0, "OCCUPIED": 0, "PREPARE_FAILED": 0, "NO_ENTRY_BAR": 0}
    active: list[tuple[int, str, int]] = []                          # (release_ts, symbol, entry_ts)
    for d, _neg, sym in confirmed:
        if d < start_d or d >= end_d:
            continue
        at = mk.t0 + d * BAR
        active = [a for a in active if a[0] > at]
        if len(active) >= capacity:
            rejects["NO_SLOT"] += 1
            continue
        if any(a[1] == sym for a in active):
            rejects["OCCUPIED"] += 1
            continue
        if d >= mk.n:
            rejects["NO_ENTRY_BAR"] += 1
            continue
        row = cand.rows[sym][d]
        side = row["direction"]
        sign = 1 if side == "LONG" else -1
        rules = mk.rules[sym]
        try:
            spec = farmer_spec("bt", sym, side, float(row["entry"]), margin=margin, leverage=leverage, target_usdt=target,
                               max_loss_usdt=max_loss, tick=rules["tickSize"], step=rules["stepSize"], buffer_usdt=BUFFER_USDT)
        except ValueError:
            rejects["PREPARE_FAILED"] += 1
            continue
        qty = spec.levels[0].qty
        if qty < rules["minQty"] or qty * float(row["entry"]) < rules["minNotional"]:
            rejects["PREPARE_FAILED"] += 1
            continue
        fill = float(mk.copen[sym][d]) * (1 + sign * slip)
        tp, stop = levels_from_avg(side, fill, qty, target_usdt=target, max_loss_usdt=max_loss, tick=rules["tickSize"], buffer_usdt=BUFFER_USDT)
        entry_fee = qty * fill * TAKER_FEE
        meta = spec.meta
        atr, planned = row.get("atr"), float(row["entry"])
        atr_pct = atr / planned * 100 if atr and atr > 0 and planned > 0 else None
        pos = {"symbol": sym, "side": side, "d": d, "entry_ts": at, "qty": qty, "entry_fill": fill, "tp": tp, "stop": stop,
               "margin": margin, "entry_fee": entry_fee, "ambiguous": 0, "confidence": row["confidence"],
               "cost_over_target": meta["est_cost_usdt"] / target if target > 0 else None,
               "target_over_atr": meta["target_pct"] / atr_pct if atr_pct else None,
               "stop_over_atr": meta["stop_pct"] / atr_pct if atr_pct else None}
        exit_info = _find_exit(mk, cand, pos, sign, slip, dead_score, intrabar)
        if exit_info is None:
            pos["status"] = "OPEN"
            open_at_end.append(pos)
            active.append((10**12, sym, at))
            continue
        kind, exit_ts, exit_fill, flat_ts = exit_info["kind"], exit_info["ts"], exit_info["fill"], exit_info["flat_ts"]
        gross = sign * (exit_fill - fill) * qty
        commission = entry_fee + qty * exit_fill * TAKER_FEE
        funding = _funding(mk, sym, sign, qty, at, exit_ts)
        pos.update(status="CLOSED", reason=kind, exit_ts=exit_ts, exit_fill=exit_fill, gross=gross, commission=commission, funding=funding,
                   net=gross - commission + funding, ambiguous=exit_info["ambiguous"], hold_bars=max(1, (exit_ts - at) // BAR))
        trades.append(pos)
        active.append((flat_ts + cooldown_s, sym, at))
    return {"trades": trades, "open": open_at_end, "rejects": rejects, "capacity": capacity}


def _funding(mk: Market, sym: str, sign: int, qty: float, entry_ts: int, exit_ts: int) -> float:
    times = mk.fund_t[sym]
    lo = bisect.bisect_right(times, entry_ts)                          # settlement strictly after entry
    hi = bisect.bisect_right(times, exit_ts)
    total = 0.0
    for i in range(lo, hi):
        bar = int(times[i] - mk.t0) // BAR
        total += -sign * qty * float(mk.mopen[sym][bar]) * float(mk.fund_r[sym][i])
    return total


def _find_exit(mk: Market, cand: Candidates, pos: dict[str, Any], sign: int, slip: float, dead_score: float | None,
               intrabar: str = "STOP_FIRST") -> dict[str, Any] | None:
    sym, j, qty, fill = pos["symbol"], pos["d"], pos["qty"], pos["entry_fill"]
    tp, stop = pos["tp"], pos["stop"]
    n = mk.n
    if stop is None:                                                   # cost eats max loss: engine force-closes immediately at market
        px = float(mk.copen[sym][j]) * (1 - sign * slip)
        return {"kind": "FORCED_COST", "ts": mk.t0 + j * BAR, "fill": px, "flat_ts": mk.t0 + j * BAR, "ambiguous": 0}
    mo, mh, ml = mk.mopen[sym][j:], mk.mhigh[sym][j:], mk.mlow[sym][j:]
    if sign == 1:
        stop_hit, tp_hit = ml <= stop, mh >= tp
        stop_gap, tp_gap = mo <= stop, mo >= tp
    else:
        stop_hit, tp_hit = mh >= stop, ml <= tp
        stop_gap, tp_gap = mo >= stop, mo <= tp
    hit = stop_hit | tp_hit
    e_rel = int(np.argmax(hit)) if hit.any() else None
    e = None if e_rel is None else j + e_rel
    range_exit = None
    if e is not None:
        bar_start = mk.t0 + e * BAR
        copen = float(mk.copen[sym][e])
        ambiguous = int(stop_hit[e_rel] and tp_hit[e_rel])
        if stop_gap[e_rel]:
            range_exit = ("STOP", bar_start, copen * (1 - sign * slip), bar_start, ambiguous, True)
        elif tp_gap[e_rel] and not stop_hit[e_rel]:
            range_exit = ("TARGET", bar_start, copen * (1 - sign * slip), bar_start, ambiguous, True)
        elif stop_hit[e_rel] and not (intrabar == "TP_FIRST" and tp_hit[e_rel]):   # STOP first (default) when both are possible in one bar
            range_exit = ("STOP", bar_start + BAR - 1, stop * (1 - sign * slip), bar_start + BAR, ambiguous, False)
        else:
            range_exit = ("TARGET", bar_start + BAR - 1, tp * (1 - sign * slip), bar_start + BAR, ambiguous, False)
    dead_exit = None
    if dead_score is not None:
        flags = cand.adverse[(dead_score, pos["side"])][sym]
        last = (e if e is not None else n) - 1                          # closed bars j .. last may trigger a boundary check
        if last >= j:
            f = flags[j:last + 1]
            c = np.cumsum(f)
            reset = np.maximum.accumulate(np.where(~f, c, 0))
            run = c - reset
            price = mk.mclose[sym][j:last + 1]
            est = sign * (price - fill) * qty - (fill * qty + price * qty) * TAKER_FEE
            ok = (run >= DEAD_CONFIRMATIONS) & (est > SAFETY_MARGIN_USDT)
            if ok.any():
                k = j + int(np.argmax(ok))
                d_exit = k + 1
                gap_wins = range_exit is not None and range_exit[5] and e == d_exit
                if d_exit < n and not gap_wins:
                    ts = mk.t0 + d_exit * BAR
                    dead_exit = ("DEAD_CLOSE", ts, float(mk.copen[sym][d_exit]) * (1 - sign * slip), ts, 0, True)
    chosen = dead_exit or range_exit
    if chosen is None:
        return None
    kind, ts, px, flat_ts, ambiguous, _gap = chosen
    return {"kind": kind, "ts": ts, "fill": px, "flat_ts": flat_ts, "ambiguous": ambiguous}


# ---------------------------------------------------------------------------------------------- metrics
def trade_metrics(trades: list[dict[str, Any]], segments: dict[str, tuple[int, int]], months: list[tuple[str, int, int]],
                  max_loss: float) -> dict[str, Any]:
    """Closed-trade statistics per segment (by EXIT time) and per calendar month. Unknown PnL is never counted as zero (all trades here are
    closed with complete funding); open positions are excluded and reported by the caller."""
    ordered = sorted(trades, key=lambda p: p["exit_ts"])
    t_exit = np.array([p["exit_ts"] for p in ordered], dtype=float)
    net = np.array([p["net"] for p in ordered], dtype=float)
    margin_seconds = np.array([p["margin"] * (p["exit_ts"] - p["entry_ts"]) for p in ordered], dtype=float)
    out: dict[str, Any] = {}
    if ordered:
        out.update(gross=float(sum(p["gross"] for p in ordered)), comm=float(sum(p["commission"] for p in ordered)),
                   fund=float(sum(p["funding"] for p in ordered)), amb=int(sum(p["ambiguous"] for p in ordered)),
                   dead_n=sum(p["reason"] == "DEAD_CLOSE" for p in ordered), stop_n=sum(p["reason"] == "STOP" for p in ordered),
                   tgt_n=sum(p["reason"] == "TARGET" for p in ordered))
    else:
        out.update(gross=0.0, comm=0.0, fund=0.0, amb=0, dead_n=0, stop_n=0, tgt_n=0)
    for name, (lo, hi) in segments.items():
        mask = (t_exit >= lo) & (t_exit < hi)
        values, count = net[mask], int(mask.sum())
        if count == 0:
            out.update({f"{name}_n": 0, f"{name}_net": 0.0, f"{name}_wins": 0, f"{name}_pf": float("nan"), f"{name}_exp_r": float("nan"),
                        f"{name}_dd": 0.0, f"{name}_avgm": 0.0})
            continue
        curve = np.concatenate([[0.0], np.cumsum(values)])
        gains, losses = values[values > 0].sum(), -values[values < 0].sum()
        out.update({f"{name}_n": count, f"{name}_net": float(values.sum()), f"{name}_wins": int((values > 0).sum()),
                    f"{name}_pf": float(gains / losses) if losses > 0 else float("inf"), f"{name}_exp_r": float(values.mean() / max_loss),
                    f"{name}_dd": float((np.maximum.accumulate(curve) - curve).max()), f"{name}_avgm": float(margin_seconds[mask].sum() / (hi - lo))})
    for label, lo, hi in months:
        out[f"M_{label}"] = float(net[(t_exit >= lo) & (t_exit < hi)].sum()) if ordered else 0.0
    return out
