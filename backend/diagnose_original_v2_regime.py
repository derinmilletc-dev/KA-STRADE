"""Descriptive only: does a market-regime filter separate Original v2 winners?

Replays the unchanged gap18d profile and records, for every fully verified trade,
the trend-strength context at the decision bar (native ADX14 on the same closed
15m window) and whether the native EMA stack was aligned. No entry, exit, sizing
or risk rule is changed; nothing here touches LIVE, Demo or any order path.

The kept/dropped comparison and its pass bar are pre-registered in DECISION_RULE
below and evaluated by this script, so the bar cannot move after the numbers are
seen. Tertile cut points come from TRAIN only; VALIDATION reuses the same cuts.

BU BIR BACKTEST DEGILDIR. Groups are arithmetic over recorded trades: dropping a
group does not re-simulate the sequence, so gates that depend on order (daily
trades, consecutive losses, same-direction limit, accounting locks) would differ
in a real run. VALIDATION was already consumed by earlier attempts, so a GEC
verdict here is a reason to forward-test, never proof of an edge.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import math
import os
import time
from pathlib import Path
from statistics import fmean

import diagnose_original_v2_trades as diag
import prescreen_original_v2 as parent
from app.backtest_baseline import percentile
from app.strategies.original_offline_engine import fully_verified
from build_measurement_view import load_measurement_dataset, sha256_file
from measure_donchian_counts import (
    PHASES,
    VIEW,
    MeasurementError,
    emit,
    locked_plan,
    primary_config,
    validate_loaded_scope,
)
from measure_original_counts import METADATA, VIEW_SHA

OUTPUT = Path.home() / "kaistrade-data" / "original-v2-regime-diagnosis"
SCHEMA = "original-v2-regime-filter-diagnosis-v1"

# Locked features. Both are already produced by the native analysis; no new indicator.
TERTILE_FEATURE = "adx14_15m"
FLAG_FEATURES = ("ema_aligned_with_direction", "ema_aligned_any")

# Pre-registered candidates. Fixed before the run; no threshold search, no extra variants.
CANDIDATES = (
    {"id": "adx_high", "kind": "tertile", "keep": ("HIGH",),
     "intent": "Trade only the strongest trend third; drop the weakest two thirds."},
    {"id": "adx_middle_high", "kind": "tertile", "keep": ("MIDDLE", "HIGH"),
     "intent": "Drop only the weakest trend third."},
    {"id": "ema_aligned_with_direction", "kind": "flag", "flag": "ema_aligned_with_direction",
     "intent": "Trade only when the EMA stack is ordered in the trade's own direction."},
)

DECISION_RULE = {
    "pre_registered": True,
    "cut_points": "TERTILE_CUTS_FROM_TRAIN_ONLY_VALIDATION_REUSES_THEM_TIES_GO_LOWER",
    "kept_group_requirements_in_BOTH_phases": {
        "min_N": 100,
        "mean_net_r_strictly_greater_than": 0.0,
        "profit_factor_r_strictly_greater_than": 1.15,
    },
    "separation_requirement_in_BOTH_phases": "KEPT_MEAN_NET_R_STRICTLY_ABOVE_DROPPED_MEAN_NET_R",
    "verdict_values": ["GEC", "DUR"],
    "multiplicity": "THREE_PRE_REGISTERED_CANDIDATES_ONE_RUN_NO_RERUNS_NO_TUNING",
    "window_caveat": (
        "VALIDATION was already used by earlier Original v2 attempts (100-day trend filter, F2), "
        "so it is NOT a clean referee. GEC means forward-test on Demo, not a proven edge."
    ),
    "not_a_rule_change": "NOTHING_IN_THIS_REPORT_CHANGES_LIVE_DEMO_OR_OFFLINE_BEHAVIOUR",
}

DEFINITIONS = {
    "purpose": "DESCRIPTIVE_ONLY_NOT_A_DECISION_INPUT",
    "profile": "UNCHANGED_ORIGINAL_FIXED_CAP6_LEV3_GAP18D_V1_NO_TREND_FILTER",
    "protocol_relaxation": False,
    "experiment_attempt_used": False,
    "adx14_15m": "NATIVE_ADX14_ON_LAST_259_CLOSED_CONTRACT_15M_CANDLES_AT_DECISION",
    "ema_aligned_any": "NATIVE_EMA20_EMA50_EMA200_STRICTLY_ORDERED_IN_EITHER_DIRECTION",
    "ema_aligned_with_direction": "NATIVE_EMA_STACK_STRICTLY_ORDERED_IN_THE_TRADE_DIRECTION",
    "net_r": "NATIVE_NET_R_ONLY_FULLY_VERIFIED_COMPLETED",
    "profit_factor_r": "SUM_POSITIVE_NET_R_DIVIDED_BY_ABS_SUM_NEGATIVE_NET_R_NULL_WHEN_NO_LOSSES",
    "tp3_share_pct": "SHARE_OF_GROUP_WHOSE_FINAL_NATIVE_EXIT_REASON_IS_TP3",
    "stop_before_tp1_share_pct": "SHARE_OF_GROUP_STOPPED_WITHOUT_A_NATIVE_TP1_FILL",
    "reliability": "N_LT_30_GUVENILMEZ_ONLY_A_REPORT_LABEL_NOT_A_GATE",
    "grouping": (
        "BU BIR BACKTEST DEGILDIR, KURAL ONERISI DEGILDIR. Arithmetic over recorded trades only. "
        "Removing a group does not replay the remaining sequence, so order-dependent gates "
        "(daily_trades, consecutive_losses, same_direction_positions, accounting locks) and the "
        "resulting entry set would differ in any real run."
    ),
}


class RegimeEngine(diag.DiagnosisEngine):
    """Adds trend-strength context to the parent's recorded entry facts. Decisions unchanged."""

    async def _enter(self, symbol: str, signal: dict, at: int) -> None:
        before = len(self.trades)
        await super()._enter(symbol, signal, at)
        parent.require(len(self.trades) in (before, before + 1), "REGIME_UNEXPECTED_ENTRY_DELTA")
        if len(self.trades) != before + 1:
            return
        averages, strength, direction = signal["ema"], signal["adx"], signal["direction"]
        parent.require(direction in {"LONG", "SHORT"}, "REGIME_INVALID_DIRECTION")
        parent.require(math.isfinite(strength) and strength >= 0, "REGIME_INVALID_ADX")
        parent.require(all(math.isfinite(averages[key]) and averages[key] > 0
                           for key in ("ema20", "ema50", "ema200")), "REGIME_INVALID_EMA_STACK")
        rising = averages["ema20"] > averages["ema50"] > averages["ema200"]
        falling = averages["ema20"] < averages["ema50"] < averages["ema200"]
        parent.require(not (rising and falling), "REGIME_CONTRADICTORY_EMA_STACK")
        facts = self.entry_facts[id(self.trades[-1])]
        parent.require(not set(facts) & {TERTILE_FEATURE, *FLAG_FEATURES}, "REGIME_FACT_COLLISION")
        facts.update({
            TERTILE_FEATURE: float(strength),
            "ema50_15m": float(averages["ema50"]),
            "ema200_15m": float(averages["ema200"]),
            "ema_aligned_any": bool(rising or falling),
            "ema_aligned_with_direction": bool(rising if direction == "LONG" else falling),
        })


def profit_factor(values: list[float]) -> float | None:
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    return gains / losses if losses > 0 else None


def group_stats(rows: list[dict]) -> dict:
    count = len(rows)
    if count == 0:
        return {"N": 0, "reliability": "guvenilmez", "mean_net_r": None, "profit_factor_r": None,
                "tp3_share_pct": None, "stop_before_tp1_share_pct": None, "median_duration_hours": None}
    nets = [row["net_r"] for row in rows]
    durations = sorted(row["duration_seconds"] / 3600 for row in rows)
    return {
        "N": count,
        "reliability": "guvenilmez" if count < 30 else "descriptive_only",
        "mean_net_r": fmean(nets),
        "profit_factor_r": profit_factor(nets),
        "tp3_share_pct": sum(row["path"] == "TP3" for row in rows) / count * 100,
        "stop_before_tp1_share_pct": sum(row["path"] == "STOP_BEFORE_TP1" for row in rows) / count * 100,
        "median_duration_hours": percentile(durations, 0.5),
    }


def train_cuts(rows: list[dict]) -> dict:
    parent.require(bool(rows), "REGIME_TRAIN_CUTS_REQUIRE_VERIFIED_TRADES")
    values = [row[TERTILE_FEATURE] for row in rows]
    return {"source": "TRAIN", "N": len(rows),
            TERTILE_FEATURE: [percentile(values, fraction) for fraction in (1 / 3, 2 / 3)]}


def split(rows: list[dict], candidate: dict, cuts: dict) -> tuple[list[dict], list[dict]]:
    if candidate["kind"] == "tertile":
        keep = set(candidate["keep"])
        chosen = [row for row in rows
                  if diag.tertile(row[TERTILE_FEATURE], cuts[TERTILE_FEATURE]) in keep]
        dropped = [row for row in rows
                   if diag.tertile(row[TERTILE_FEATURE], cuts[TERTILE_FEATURE]) not in keep]
    else:
        flag = candidate["flag"]
        chosen = [row for row in rows if row[flag]]
        dropped = [row for row in rows if not row[flag]]
    parent.require(len(chosen) + len(dropped) == len(rows), f"REGIME_SPLIT_LOSS:{candidate['id']}")
    return chosen, dropped


def tables(rows: list[dict], cuts: dict) -> dict:
    by_tertile = {group: [row for row in rows
                          if diag.tertile(row[TERTILE_FEATURE], cuts[TERTILE_FEATURE]) == group]
                  for group in ("LOW", "MIDDLE", "HIGH")}
    return {
        "N": len(rows),
        "all": group_stats(rows),
        "adx_tertiles": {group: group_stats(selected) for group, selected in by_tertile.items()},
        "adx_tertiles_by_direction": {
            side: {group: group_stats([row for row in selected if row["direction"] == side])
                   for group, selected in by_tertile.items()}
            for side in ("LONG", "SHORT")
        },
        "flags": {
            flag: {"TRUE": group_stats([row for row in rows if row[flag]]),
                   "FALSE": group_stats([row for row in rows if not row[flag]])}
            for flag in FLAG_FEATURES
        },
        "adx_observed": {label: percentile(sorted(row[TERTILE_FEATURE] for row in rows), fraction)
                         for label, fraction in (("p05", .05), ("p25", .25), ("p50", .5),
                                                 ("p75", .75), ("p95", .95))} if rows else None,
        "candidates": {
            candidate["id"]: {
                "intent": candidate["intent"],
                "kept": group_stats(chosen),
                "dropped": group_stats(dropped),
            } for candidate in CANDIDATES
            for chosen, dropped in (split(rows, candidate, cuts),)
        },
    }


def verdicts(phases: dict) -> dict:
    bar = DECISION_RULE["kept_group_requirements_in_BOTH_phases"]
    results = {}
    for candidate in CANDIDATES:
        failures: list[str] = []
        for name in ("TRAIN", "VALIDATION"):
            entry = phases[name]["tables"]["candidates"][candidate["id"]]
            kept, dropped = entry["kept"], entry["dropped"]
            mean, factor = kept["mean_net_r"], kept["profit_factor_r"]
            if kept["N"] < bar["min_N"]:
                failures.append(f"{name}_N_BELOW_{bar['min_N']}")
            if mean is None or not mean > bar["mean_net_r_strictly_greater_than"]:
                failures.append(f"{name}_MEAN_NET_R_NOT_POSITIVE")
            if factor is None or not factor > bar["profit_factor_r_strictly_greater_than"]:
                failures.append(f"{name}_PROFIT_FACTOR_NOT_ABOVE_BAR")
            if mean is None or dropped["mean_net_r"] is None or not mean > dropped["mean_net_r"]:
                failures.append(f"{name}_NO_SEPARATION_FROM_DROPPED_GROUP")
        results[candidate["id"]] = {"verdict": "DUR" if failures else "GEC", "failed_checks": failures}
    return results


async def phase_result(data, plan, phase, reference, progress=None):
    parent.require(PHASES[0].start <= phase.start < phase.end <= PHASES[-1].end
                   and phase.start % 900 == phase.end % 900 == 0, "MEASUREMENT_SCOPE_DENIED")
    engine = RegimeEngine(data, primary_config(phase, plan))
    counts = await parent.gap.replay_counts(engine, phase, progress)
    parent.assert_count_parity(counts, reference, phase.name)
    rows = [diag.trade_record(engine, position) for position in engine.trades if fully_verified(position)]
    parent.require(len(rows) == counts["counts"]["fully_verified_completed"], "REGIME_VERIFIED_N_MISMATCH")
    return counts, rows


def validate_rows(rows: list[dict], name: str, counts: dict) -> None:
    diag.validate_rows(rows, name, counts)
    for row in rows:
        parent.require(type(row[TERTILE_FEATURE]) is float and math.isfinite(row[TERTILE_FEATURE])
                       and row[TERTILE_FEATURE] >= 0, f"REGIME_NONFINITE_ADX:{name}")
        parent.require(all(type(row[flag]) is bool for flag in FLAG_FEATURES),
                       f"REGIME_FLAG_NOT_BOOLEAN:{name}")
        parent.require(not row["ema_aligned_with_direction"] or row["ema_aligned_any"],
                       f"REGIME_FLAG_CONTRADICTION:{name}")


def slim(row: dict) -> dict:
    """Compact per-trade columns; the parent diagnosis already holds the full bar envelopes."""
    keys = ("symbol", "direction", "signal_id", "opened_at", "closed_at", "duration_seconds",
            "spec_entry_price", "initial_stop_price", "stop_distance_pct", "confidence",
            "ema_distance_atr", TERTILE_FEATURE, *FLAG_FEATURES, "exit_reason", "tp1_seen",
            "path", "net_r", "mfe_r", "mae_r")
    return {key: row[key] for key in keys}


def write_raw(output: Path, name: str, rows: list[dict]) -> dict:
    path = output / f"regime-trades-{name}.jsonl"
    compact = [slim(row) for row in rows]
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in compact:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False))
            handle.write("\n")
    loaded = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    parent.require(loaded == compact, f"REGIME_RAW_ROUNDTRIP:{name}")
    return {"file": path.name, "sha256": sha256_file(path), "N": len(compact)}


def validate_report(report: dict, rows: dict[str, list[dict]]) -> None:
    parent.require(set(report) == {"schema", "definitions", "decision_rule", "candidates", "profile",
                                   "provenance", "train_cutpoints", "phases", "verdicts",
                                   "elapsed_seconds"}
                   and report["schema"] == SCHEMA and report["definitions"] == DEFINITIONS
                   and report["decision_rule"] == DECISION_RULE
                   and report["candidates"] == list(CANDIDATES), "REGIME_FIXED_REGISTRATION")
    parent.require(report["profile"] == parent.asdict(parent.PROFILE)
                   and report["provenance"]["parent_prescreen_provenance"]["profile_hash"]
                   == parent.PROFILE_HASH, "REGIME_PROFILE_CHANGED")
    parent.require(set(report["phases"]) == set(rows) == {"TRAIN", "VALIDATION"}, "REGIME_PHASES")
    parent.require(report["train_cutpoints"] == train_cuts(rows["TRAIN"]), "REGIME_TRAIN_ONLY_CUTS")
    for name, phase in report["phases"].items():
        counts = phase["native_counts"]
        parent.gap.validate_report({
            "schema": parent.gap.SCHEMA, "exception_record": parent.gap.exception_record(),
            "profile": parent.asdict(parent.PROFILE),
            "provenance": report["provenance"]["parent_prescreen_provenance"]["run3_provenance"],
            "phases": {name: counts}, "elapsed_seconds": 0,
        }, expected_phases={name})
        validate_rows(rows[name], name, counts)
        parent.require(phase["tables"] == tables(rows[name], report["train_cutpoints"]),
                       f"REGIME_TABLES:{name}")
        parent.require(set(phase) == {"native_counts", "raw_trades", "tables"}, f"REGIME_PHASE_KEYS:{name}")
        raw = phase["raw_trades"]
        parent.require(set(raw) == {"file", "sha256", "N"}
                       and raw["file"] == f"regime-trades-{name}.jsonl"
                       and raw["N"] == len(rows[name]) and isinstance(raw["sha256"], str)
                       and len(raw["sha256"]) == 64
                       and all(char in "0123456789abcdef" for char in raw["sha256"]),
                       f"REGIME_RAW_MANIFEST:{name}")
    parent.require(report["verdicts"] == verdicts(report["phases"]), "REGIME_VERDICT_MISMATCH")
    parent.require(set(report["verdicts"]) == {candidate["id"] for candidate in CANDIDATES},
                   "REGIME_VERDICT_COVERAGE")
    parent.require(math.isfinite(report["elapsed_seconds"]) and report["elapsed_seconds"] >= 0,
                   "REGIME_ELAPSED")
    json.dumps(report, allow_nan=False)


def guarded_main(argv=None) -> int:
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", required=True, type=Path)
    args = parser.parse_args(argv)
    loop, stream = None, sys.stdout
    try:
        parent.require(not OUTPUT.exists(), "RESULTS_ALREADY_EXIST")
        parent.require(args.metadata.resolve() == METADATA.resolve(), "METADATA_COPY_REQUIRED")
        plan = locked_plan()
        parent.require(sha256_file(args.metadata) == plan["input_sha256"]["metadata"],
                       "LOCKED_METADATA_MISMATCH")
        parent.require(sha256_file(VIEW / "manifest.json") == VIEW_SHA, "LOCKED_VIEW_MISMATCH")
        references = parent.load_references()
        for name, expected in diag.EXPECTED.items():
            counts = references[name]["phases"][name]["counts"]
            parent.require(tuple(counts[key] for key in
                                 ("accepted_entries", "completed", "fully_verified_completed"))
                           == expected, f"REGIME_RUN3_PIN:{name}")
        OUTPUT.mkdir(parents=True, exist_ok=False)
        os.environ.update({"DATA_DIR": str(OUTPUT / "runtime"),
                           "PROTREBOT_DATA_DIR": str(OUTPUT / "runtime"),
                           "DATABASE_URL": "", "PROTREBOT_DURABLE_AUTH_REQUIRED": "0",
                           "ASSISTANT_LIVE_TESTS": "0"})
        loop = asyncio.new_event_loop()
        started = time.monotonic()
        with parent.quiet_native(), parent.deny_network():
            record = {
                "parent_prescreen_provenance": parent.provenance(plan, references),
                "regime_script_sha256": sha256_file(Path(__file__)),
                "parent_diagnosis_script_sha256": sha256_file(Path(diag.__file__)),
                "diagnosis_only": True, "protocol_relaxation": False, "experiment_attempt_used": False,
            }
            data = load_measurement_dataset(VIEW, args.metadata, PHASES[0].start, PHASES[-1].end)
            validate_loaded_scope(data, plan["symbols"])
            phases, raw_rows = {}, {}
            for phase in PHASES:
                counts, rows = loop.run_until_complete(phase_result(
                    data, plan, phase, references[phase.name]["phases"][phase.name],
                    progress=lambda value: emit(stream, value)))
                validate_rows(rows, phase.name, counts)
                phases[phase.name] = {"native_counts": counts}
                raw_rows[phase.name] = rows
                emit(stream, {"status": "phase_parity_verified", "phase": phase.name, "N": len(rows)})
        cuts = train_cuts(raw_rows["TRAIN"])
        for name, rows in raw_rows.items():
            phases[name].update(raw_trades=write_raw(OUTPUT, name, rows), tables=tables(rows, cuts))
        report = {
            "schema": SCHEMA, "definitions": copy.deepcopy(DEFINITIONS),
            "decision_rule": copy.deepcopy(DECISION_RULE), "candidates": [dict(c) for c in CANDIDATES],
            "profile": parent.asdict(parent.PROFILE), "provenance": record,
            "train_cutpoints": cuts, "phases": phases, "verdicts": {},
            "elapsed_seconds": time.monotonic() - started,
        }
        report["verdicts"] = verdicts(report["phases"])
        validate_report(report, raw_rows)
        path = OUTPUT / "regime-diagnosis.json"
        with path.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, allow_nan=False)
            handle.write("\n")
        emit(stream, {"status": "completed", "output": str(path), "sha256": sha256_file(path),
                      "train_cutpoints": cuts,
                      "verdicts": {key: value["verdict"] for key, value in report["verdicts"].items()},
                      "raw_trades": {name: phase["raw_trades"] for name, phase in phases.items()}})
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ArithmeticError) as exc:
        emit(stream, {"status": "failed",
                      "code": str(exc) if isinstance(exc, MeasurementError) else "NATIVE_FAILURE",
                      "error_class": type(exc).__name__, "detail": str(exc)})
        return 2
    finally:
        if loop is not None:
            loop.close()


def main(argv=None) -> int:
    with parent.gap.previous.research_path_guard():
        return guarded_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
