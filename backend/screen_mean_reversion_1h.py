"""Single-candidate exploratory screening of a range-market mean-reversion idea.

NOT the Engine/facade and NOT a gated account. It reuses the data loading, gap
inventory, funding and metric rules of ``screen_donchian_4h`` unchanged, so the
two screens are directly comparable. Every parameter below is FIXED before the
first run: one setting, one run, no variants, no re-run after seeing results.

Idea: when the market is NOT trending (ADX14 < 20 on 1h), a close outside the
Bollinger(20, 2) band with an extreme RSI is bet to fall back toward the band
middle. LONG below the lower band with RSI14 < 30; SHORT above the upper band
with RSI14 > 70. The signal is a CLOSED 1h candle; entry is the next 15m open
(= the 1h boundary), then adverse slippage is applied.

Exit (single exit, no partials, no break-even): take profit at the Bollinger
middle fixed at the decision, stop at 1.5 x ATR14 from the entry open, and a
24 hour time stop (market exit at the first 15m open at or after 24h). Stop is
checked first inside a 15m bar. A trade is skipped when its target is closer
than 3x the round-trip cost (0.16%), a cost rule rather than a tuned filter.

Indicators use the trailing window of up to 259 contiguous CLOSED 1h candles
(the Original's analysis window), resetting after gaps; warmup is 100 bars.
Each trade risks 3 USDT (1R); 1000 USDT is a reference only. Daily limits,
exposure, margin, stop caps, exchange filters and liquidation gates are NOT
applied. One position per symbol. Contract 15m OHLC drives exits, not native
MARK protection. No spread charge, bootstrap or forced final exit.

Future timestamp gaps are offline data-quality exclusions, NOT live signals
(72 hours before contract 1h/15m, mark 15m and native funding gaps). Unknown and
open trades never contribute to N or performance. Symbol/month outputs contain
COUNTS ONLY. Existing Original/LIVE/Demo and holdout protocols are untouched.

The TRAIN and VALIDATION windows were already seen by earlier work, so a
DEVAM result is only a reason to look at untouched data, never an edge proof.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any

import screen_donchian_4h as base
from app.analysis import adx, atr, rsi
from app.backtest_data import Dataset, Series

ScreeningError = base.ScreeningError
require = base.require
stamp, month = base.stamp, base.month
Phase, PHASES = base.Phase, base.PHASES
Gap, blackout = base.Gap, base.blackout
metrics, decision, digest = base.metrics, base.decision, base.digest
settle = base.settle
MIN_N, MIN_NET_R, MIN_PF = base.MIN_N, base.MIN_NET_R, base.MIN_PF
RISK, FEE, SLIP = base.RISK, base.FEE, base.SLIP
STEP, BLACKOUT = base.STEP, base.BLACKOUT
SYMBOLS, VIEW, METADATA_SHA = base.SYMBOLS, base.VIEW, base.METADATA_SHA

SIGNAL_STEP = 3600
HOLD = 24 * 3600
WINDOW, WARMUP = 259, 100
BB_N, BB_K = 20, 2.0
RSI_PERIOD, RSI_LOW, RSI_HIGH = 14, 30.0, 70.0
ADX_PERIOD, ADX_MAX = 14, 20.0
ATR_PERIOD, STOP_K = 14, 1.5
COST_MULTIPLE = 3
ROUND_TRIP_COST = 2 * (float(FEE) + float(SLIP))
MIN_TARGET_FRACTION = COST_MULTIPLE * ROUND_TRIP_COST
OUTPUT = Path(r"C:\Users\ahmtt\kaistrade-data\mean-reversion-screening-results")
SCHEMA = "mean-reversion-1h-independent-screen-v1"
DISCLAIMER = (
    "EXPLORATORY_NOT_ENGINE_OR_FACADE; INDEPENDENT_1R_TRADES; "
    "NO_ACCOUNT_DAILY_EXPOSURE_MARGIN_CAP_EXCHANGE_OR_LIQUIDATION_GATES; "
    "1000_USDT_REFERENCE_ONLY_NO_COMPOUNDING; CONTRACT_15M_PROTECTION; "
    "FUTURE_GAP_INFORMATION_NOT_LIVE; NO_BOOTSTRAP; SEEN_WINDOWS_DEVAM_IS_NOT_PROOF"
)
EXIT_REASONS = ("TARGET", "STOP", "TIME")


@dataclass(frozen=True)
class Observation:
    direction: str | None
    atr_value: float | None
    middle: float | None
    reason: str


def band_side(closes: list[float]) -> tuple[str | None, float | None]:
    """Cheap first gate: close outside Bollinger(20, 2). Returns (side, middle)."""
    window = closes[-BB_N:]
    middle, deviation = fmean(window), pstdev(window)
    if deviation <= 0:
        return None, None
    close = closes[-1]
    if close < middle - BB_K * deviation:
        return "LONG", middle
    if close > middle + BB_K * deviation:
        return "SHORT", middle
    return None, middle


def indicator_values(rows: list[dict[str, Any]]) -> dict[str, float]:
    highs, lows, closes = ([row[key] for row in rows] for key in ("high", "low", "close"))
    return {"adx": adx(highs, lows, closes, period=ADX_PERIOD),
            "rsi": rsi(closes, RSI_PERIOD),
            "atr": atr(highs, lows, closes, period=ATR_PERIOD)}


def observe(series: Series, at: int) -> Observation:
    require(series.interval == "1h" and at % SIGNAL_STEP == 0, "Invalid 1h decision clock")
    rows = series.closed(at, limit=WINDOW)
    if not rows or rows[-1]["time"] != at - SIGNAL_STEP:
        return Observation(None, None, None, "missing_closed_1h")
    low = len(rows) - 1
    while low and rows[low]["time"] - rows[low - 1]["time"] == SIGNAL_STEP:
        low -= 1
    rows = rows[low:]
    if len(rows) < WARMUP:
        return Observation(None, None, None, "warmup_1h")
    side, middle = band_side([row["close"] for row in rows])
    if middle is None:
        return Observation(None, None, None, "zero_width_band")
    if side is None:
        return Observation(None, None, None, "inside_band")
    values = indicator_values(rows)
    require(all(math.isfinite(value) for value in values.values()), "Invalid closed 1h indicator")
    if not values["adx"] < ADX_MAX:
        return Observation(None, None, None, "trending_adx")
    extreme = values["rsi"] < RSI_LOW if side == "LONG" else values["rsi"] > RSI_HIGH
    if not extreme:
        return Observation(None, None, None, "rsi_not_extreme")
    require(values["atr"] > 0, "Invalid closed 1h ATR")
    return Observation(side, values["atr"], middle, "signal")


def series_inventory(data: Dataset, symbol: str, phase: Phase) -> list[Gap]:
    return [
        *base.series_gaps(data.frames[symbol]["1h"], SIGNAL_STEP, phase, "contract_1h"),
        *base.series_gaps(data.frames[symbol]["15m"], STEP, phase, "contract_15m"),
        *base.series_gaps(data.marks[symbol], STEP, phase, "mark"),
        *base.funding_gaps(data, symbol, phase),
    ]


def missing(gaps: list[Gap], at: int, *, position: bool) -> list[str]:
    return sorted({gap.kind for gap in gaps if (not position or gap.kind != "contract_1h")
                   and gap.start < at + STEP and at < gap.end})


@dataclass
class MeanPosition:
    symbol: str
    direction: str
    opened_at: int
    entry: Decimal
    stop: Decimal
    target: Decimal
    quantity: Decimal = field(init=False)
    remaining: Decimal = field(init=False)
    actual_entry: Decimal = field(init=False)
    commission: Decimal = field(init=False)
    gross: Decimal = Decimal(0)
    frictionless: Decimal = Decimal(0)
    funding: Decimal = Decimal(0)
    status: str = "OPEN"
    closed_at: int | None = None
    uncertainty: list[str] = field(default_factory=list)
    exits: list[tuple[str, Decimal, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        require(self.direction in ("LONG", "SHORT"), "Invalid position direction")
        require(self.entry > 0 and self.stop > 0 and self.sign * (self.entry - self.stop) > 0,
                "Invalid independent Stop")
        require(self.target > 0 and self.sign * (self.target - self.entry) > 0, "Invalid independent target")
        self.quantity = self.remaining = RISK / abs(self.entry - self.stop)
        self.actual_entry = self.entry * (1 + self.sign * SLIP)
        self.commission = self.quantity * self.actual_entry * FEE

    @property
    def sign(self) -> int:
        return 1 if self.direction == "LONG" else -1

    @property
    def net(self) -> Decimal:
        return self.gross - self.commission + self.funding

    def fill(self, expected: Decimal, quantity: Decimal, at: int, reason: str) -> None:
        actual = expected * (1 - self.sign * SLIP)
        self.gross += quantity * self.sign * (actual - self.actual_entry)
        self.frictionless += quantity * self.sign * (expected - self.entry)
        self.commission += quantity * actual * FEE
        self.remaining -= quantity
        self.exits.append((reason, quantity, at))
        if self.remaining == 0:
            self.status, self.closed_at = "CLOSED", at

    def advance(self, contract: dict[str, Any], *, opening_only: bool) -> None:
        bar = int(contract["time"])
        if opening_only:
            opening = Decimal(str(contract["open"]))
            if self.sign * (opening - self.stop) <= 0:
                self.fill(opening, self.remaining, bar, "STOP")
            elif self.sign * (opening - self.target) >= 0:
                self.fill(opening, self.remaining, bar, "TARGET")
            elif bar >= self.opened_at + HOLD:
                self.fill(opening, self.remaining, bar, "TIME")
            return
        adverse = Decimal(str(contract["low"] if self.sign == 1 else contract["high"]))
        favorable = Decimal(str(contract["high"] if self.sign == 1 else contract["low"]))
        at = bar + STEP - 1
        if self.sign * (adverse - self.stop) <= 0:
            self.fill(self.stop, self.remaining, at, "STOP")
        elif self.sign * (favorable - self.target) >= 0:
            self.fill(self.target, self.remaining, at, "TARGET")


def create_position(symbol: str, at: int, opening: float,
                    observation: Observation) -> tuple[MeanPosition | None, str]:
    require(observation.direction is not None and observation.atr_value is not None
            and observation.middle is not None, "Missing signal levels")
    sign = 1 if observation.direction == "LONG" else -1
    stop = opening - sign * STOP_K * observation.atr_value
    if stop <= 0:
        return None, "invalid_positive_levels"
    distance = sign * (observation.middle - opening)
    if distance <= 0:
        return None, "target_not_ahead"
    if distance / opening < MIN_TARGET_FRACTION:
        return None, "target_too_close"
    return MeanPosition(symbol, observation.direction, at, Decimal(str(opening)), Decimal(str(stop)),
                        Decimal(str(observation.middle))), "accepted"


def pass_checks(value: dict[str, Any]) -> dict[str, bool]:
    pf_ok = value["usdt_pf"] is not None and value["usdt_pf"] > MIN_PF
    return {"n_at_least_min": value["N"] >= MIN_N,
            "mean_net_r_positive": value["mean_net_r"] is not None and value["mean_net_r"] > MIN_NET_R,
            "profit_factor_above_bar": pf_ok or value["pf_status"] == "NO_LOSSES"}


def run_phase(data: Dataset, phase: Phase) -> dict[str, Any]:
    require(phase.start < phase.end and phase.start % STEP == 0 and phase.end % STEP == 0, "Invalid phase")
    symbols = sorted(data.frames)
    gaps = {symbol: series_inventory(data, symbol, phase) for symbol in symbols}
    funding_times = {symbol: [event["time"] for event in data.funding[symbol]] for symbol in symbols}
    positions: dict[str, MeanPosition] = {}
    trades: list[MeanPosition] = []
    rejections: Counter[str] = Counter()
    signals = skipped = 0

    def finish(position: MeanPosition) -> None:
        if position.status == "CLOSED" and not data.funding_complete(position.symbol, position.opened_at, position.closed_at):
            position.status = "UNKNOWN"
            position.uncertainty = ["funding_incomplete"]
        if position.status != "OPEN":
            del positions[position.symbol]

    for at in range(phase.start, phase.end, STEP):
        for symbol in symbols:
            contract, mark = data.frames[symbol]["15m"].at(at), data.marks[symbol].at(at)
            unavailable = missing(gaps[symbol], at, position=True)
            position = positions.get(symbol)
            if position is not None:
                if contract is None or mark is None or unavailable:
                    position.status, position.closed_at = "UNKNOWN", at
                    position.uncertainty = unavailable or ["missing_15m"]
                    finish(position)
                else:
                    settle(data, position, at, funding_times[symbol], opening_only=True)
                    position.advance(contract, opening_only=True)
                    finish(position)
            if at % SIGNAL_STEP == 0:
                observation = observe(data.frames[symbol]["1h"], at)
                if observation.direction is not None:
                    signals += 1
                    if symbol in positions:
                        skipped += 1
                    elif blackout(gaps[symbol], at):
                        rejections["gap_blackout"] += 1
                    elif contract is None or mark is None or missing(gaps[symbol], at, position=False):
                        rejections["missing_entry_data"] += 1
                    else:
                        position, reason = create_position(symbol, at, contract["open"], observation)
                        if position is None:
                            rejections[reason] += 1
                        else:
                            positions[symbol] = position
                            trades.append(position)
                else:
                    rejections[observation.reason] += 1
            position = positions.get(symbol)
            if position is not None:
                settle(data, position, at, funding_times[symbol], opening_only=False)
                position.advance(contract, opening_only=False)
                finish(position)
    for position in positions.values():
        position.status = "OPEN_AT_END"
    counts = lambda rows: {
        "accepted": len(rows), "N": sum(trade.status == "CLOSED" for trade in rows),
        "uncertain": sum(trade.status == "UNKNOWN" for trade in rows),
        "open_at_end": sum(trade.status == "OPEN_AT_END" for trade in rows),
    }
    months = sorted({month(at) for at in range(phase.start, phase.end, 86400)})
    closed = [trade for trade in trades if trade.status == "CLOSED"]
    result = metrics(trades)
    return {
        "period": {"start": stamp(phase.start), "end_exclusive": stamp(phase.end)},
        "metrics": result, "pass_checks": pass_checks(result),
        "signals": signals, "overlap_skipped": skipped,
        "entry_rejections": dict(sorted(rejections.items())),
        "exit_reason_counts": {reason: sum(trade.exits[-1][0] == reason for trade in closed)
                               for reason in EXIT_REASONS},
        "direction_counts": {side: counts([trade for trade in trades if trade.direction == side])
                             for side in ("LONG", "SHORT")},
        "by_symbol_counts": {symbol: counts([trade for trade in trades if trade.symbol == symbol]) for symbol in symbols},
        "by_entry_month_counts": {key: counts([trade for trade in trades if month(trade.opened_at) == key]) for key in months},
        "uncertain_trades": [
            {"symbol": trade.symbol, "opened_at": stamp(trade.opened_at), "detected_at": stamp(trade.closed_at),
             "reasons": trade.uncertainty} for trade in trades if trade.status == "UNKNOWN"
        ],
        "open_trades": [{"symbol": trade.symbol, "opened_at": stamp(trade.opened_at)}
                        for trade in trades if trade.status == "OPEN_AT_END"],
    }


PHASE_KEYS = {"period", "metrics", "pass_checks", "signals", "overlap_skipped", "entry_rejections",
              "exit_reason_counts", "direction_counts", "by_symbol_counts", "by_entry_month_counts",
              "uncertain_trades", "open_trades"}


def validate_report(report: dict[str, Any]) -> None:
    require(set(report) == {"schema", "disclaimer", "candidate", "assumptions", "phases", "decision", "provenance"},
            "Unexpected top-level screening schema")
    require(report["schema"] == SCHEMA and report["disclaimer"] == DISCLAIMER, "Missing independent-screen declaration")
    require(report["decision"] == decision(report["phases"]), "Decision differs from fixed rule")
    for phase in report["phases"].values():
        require(set(phase) == PHASE_KEYS, "Unexpected phase schema")
        require(phase["pass_checks"] == pass_checks(phase["metrics"]), "Pass checks differ from fixed rule")
        require(set(phase["exit_reason_counts"]) == set(EXIT_REASONS), "Exit reason schema differs")
        require(sum(phase["exit_reason_counts"].values()) == phase["metrics"]["N"], "Exit counts do not match N")
        for key in ("by_symbol_counts", "by_entry_month_counts", "direction_counts"):
            for value in phase[key].values():
                require(set(value) == {"accepted", "N", "uncertain", "open_at_end"},
                        "Symbol/month/direction performance is forbidden")
        for row in phase["uncertain_trades"]:
            require(set(row) == {"symbol", "opened_at", "detected_at", "reasons"}, "Uncertain trade schema differs")
        for row in phase["open_trades"]:
            require(set(row) == {"symbol", "opened_at"}, "Open trade schema differs")
        value = phase["metrics"]
        require(value["accepted"] == value["N"] + value["uncertain"] + value["open_at_end"], "Count partition differs")


def screen(data: Dataset, provenance: dict[str, Any]) -> dict[str, Any]:
    report = {
        "schema": SCHEMA, "disclaimer": DISCLAIMER,
        "candidate": {"timeframe": "1h", "bollinger": {"period": BB_N, "k": BB_K},
                      "rsi": {"period": RSI_PERIOD, "long_below": RSI_LOW, "short_above": RSI_HIGH},
                      "regime": {"adx_period": ADX_PERIOD, "adx_below": ADX_MAX},
                      "atr_period": ATR_PERIOD, "stop_k": STOP_K, "target": "BOLLINGER_MIDDLE_AT_DECISION",
                      "time_stop_hours": HOLD // 3600, "window_bars": WINDOW, "warmup_bars": WARMUP,
                      "min_target_fraction": MIN_TARGET_FRACTION, "cost_multiple": COST_MULTIPLE,
                      "protection": "CONTRACT_15M_STOP_FIRST", "blackout_seconds": BLACKOUT,
                      "fee_bps_per_side": 5, "slip_bps_per_side": 3, "spread_applied": False,
                      "partials": False, "BE": False, "reentry_rule": "NONE_ONE_POSITION_PER_SYMBOL"},
        "assumptions": {"reference_capital_usdt": 1000, "risk_usdt_per_trade": 3,
                        "pf": "SUM_POSITIVE_NET_USDT_DIV_ABS_SUM_NEGATIVE_NET_USDT_SAME_FIXED_3_USDT_RISK",
                        "no_loss_pf": "NULL_WITH_NO_LOSSES_STATUS_UNBOUNDED_PASSES_THRESHOLD",
                        "zero_result_pf": "UNDEFINED_FAIL",
                        "decision_rule": {"both_phases": True, "N_ge": MIN_N, "net_r_gt": MIN_NET_R, "pf_gt": MIN_PF},
                        "one_setting_one_run": True,
                        "windows_already_seen": True},
        "phases": {phase.name: run_phase(data, phase) for phase in PHASES},
        "decision": "", "provenance": provenance,
    }
    report["decision"] = decision(report["phases"])
    validate_report(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True,
                        help="Existing hash-locked metadata COPY outside data blocks")
    args = parser.parse_args(argv)
    try:
        require(not OUTPUT.exists(), "mean-reversion-screening-results already exists; no overwrite/retry")
        metadata = args.metadata.resolve()
        require(metadata == Path.home().joinpath("kaistrade-data", "donchian-holdout-evidence",
                                                 "current-metadata.json").resolve(),
                "Only the previously verified external metadata COPY is allowed")
        require(digest(metadata) == METADATA_SHA, "Metadata fingerprint changed")
        OUTPUT.mkdir(parents=True, exist_ok=False)
        os.environ.update({"DATA_DIR": str(OUTPUT / "runtime"), "PROTREBOT_DATA_DIR": str(OUTPUT / "runtime"),
                           "DATABASE_URL": "", "PROTREBOT_DURABLE_AUTH_REQUIRED": "0", "ASSISTANT_LIVE_TESTS": "0"})
        from build_measurement_view import load_measurement_dataset
        from measure_donchian_counts import deny_network, quiet_native

        started = time.monotonic()
        with quiet_native(), deny_network():
            data = load_measurement_dataset(VIEW, metadata, PHASES[0].start, PHASES[-1].end)
            require(set(data.frames) == set(SYMBOLS) and set(data.marks) == set(SYMBOLS),
                    "Incomplete eight-symbol universe")
            require(not data.report["missing_archives"], "Missing measurement archives")
            for parts in data.report["symbols"].values():
                require(not any(part.get("duplicates", 0) for part in parts.values()),
                        "Duplicate measurement candles")
            require(all(set(frames) == {"15m", "1h", "4h"} for frames in data.frames.values()),
                    "Incomplete contract frames")
            result = screen(data, {"metadata_sha256": METADATA_SHA,
                                   "source_archives": data.report["sources"],
                                   "script_sha256": digest(Path(__file__)),
                                   "base_script_sha256": digest(Path(base.__file__))})
        require(digest(metadata) == METADATA_SHA, "Metadata changed during screening")
        result["provenance"]["elapsed_seconds"] = time.monotonic() - started
        summary = {"schema": SCHEMA, "disclaimer": DISCLAIMER, "decision": result["decision"],
                   "phases": {name: {"metrics": value["metrics"], "pass_checks": value["pass_checks"],
                                     "exit_reason_counts": value["exit_reason_counts"],
                                     "signals": value["signals"]}
                              for name, value in result["phases"].items()}}
        for name, value in (("screen.json", result), ("summary.json", summary)):
            path = OUTPUT / name
            with path.open("x", encoding="utf-8") as stream:
                json.dump(value, stream, indent=2, allow_nan=False)
                stream.write("\n")
            print(str(path), digest(path))
        print(json.dumps(summary, allow_nan=False))
        return 0
    except (ScreeningError, OSError, ValueError, KeyError, TypeError, RuntimeError, ArithmeticError) as error:
        print(f"SCREENING_FAILED {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
