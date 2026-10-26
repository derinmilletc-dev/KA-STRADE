"""Synthetic descriptive arithmetic for the regime diagnosis; no archive, network or rule changes."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

import diagnose_original_v2_regime as regime
import test_measure_original_counts as fixtures
import test_offline_strategy_facade as native_tests
from app.strategies.original_gap_engine import OriginalGapRiskEngine
from measure_donchian_counts import locked_plan, primary_config

offline_only = native_tests.offline_only

PASSING = {"N": 120, "reliability": "descriptive_only", "mean_net_r": .2, "profit_factor_r": 1.4,
           "tp3_share_pct": 35.0, "stop_before_tp1_share_pct": 45.0, "median_duration_hours": 30.0}
WEAKER = {**PASSING, "N": 200, "mean_net_r": -.1, "profit_factor_r": .8}


def compact_row(value, *, path="STOP_BEFORE_TP1", direction="LONG",
                aligned=True, aligned_direction=True, duration=1799):
    return {
        "adx14_15m": float(value), "net_r": value / 100, "path": path, "direction": direction,
        "exit_reason": "STOP" if path.startswith("STOP") else "TP3",
        "ema_aligned_any": aligned, "ema_aligned_with_direction": aligned_direction,
        "duration_seconds": duration,
    }


def phases_with(kept, dropped):
    candidates = {candidate["id"]: {"intent": candidate["intent"], "kept": dict(kept),
                                    "dropped": dict(dropped)} for candidate in regime.CANDIDATES}
    return {name: {"tables": {"candidates": copy.deepcopy(candidates)}}
            for name in ("TRAIN", "VALIDATION")}


def test_profit_factor_is_null_without_losses_and_never_divides_by_zero():
    assert regime.profit_factor([1.0, -0.5]) == 2
    assert regime.profit_factor([1.0, 2.0]) is None
    assert regime.profit_factor([]) is None
    assert regime.profit_factor([0.0, 0.0]) is None
    assert regime.profit_factor([-1.0, -1.0]) == 0


def test_empty_group_reports_no_mean_rather_than_zero_and_labels_small_samples():
    empty = regime.group_stats([])
    assert empty["N"] == 0 and empty["reliability"] == "guvenilmez"
    assert empty["mean_net_r"] is None and empty["profit_factor_r"] is None
    assert empty["tp3_share_pct"] is None and empty["median_duration_hours"] is None
    assert regime.group_stats([compact_row(1)] * 29)["reliability"] == "guvenilmez"
    assert regime.group_stats([compact_row(1)] * 30)["reliability"] == "descriptive_only"


def test_group_stats_shares_use_the_group_denominator_only():
    rows = [compact_row(10, path="TP3"), compact_row(20), compact_row(30, path="STOP_AFTER_TP1")]
    observed = regime.group_stats(rows)
    assert observed["N"] == 3
    assert observed["tp3_share_pct"] == pytest.approx(100 / 3)
    assert observed["stop_before_tp1_share_pct"] == pytest.approx(100 / 3)
    assert observed["mean_net_r"] == pytest.approx(.2)
    assert observed["median_duration_hours"] == pytest.approx(1799 / 3600)


def test_tertile_cuts_come_from_train_only_and_validation_reuses_them():
    train = [compact_row(value) for value in (0, 3, 6, 9)]
    cuts = regime.train_cuts(train)
    assert cuts == {"source": "TRAIN", "N": 4, "adx14_15m": [3, 6]}
    validation = [compact_row(100), compact_row(200)]
    observed = regime.tables(validation, cuts)["adx_tertiles"]
    assert observed["HIGH"]["N"] == 2 and observed["LOW"]["N"] == observed["MIDDLE"]["N"] == 0
    assert cuts["adx14_15m"] == [3, 6]
    with pytest.raises(regime.MeasurementError, match="TRAIN_CUTS_REQUIRE_VERIFIED"):
        regime.train_cuts([])


@pytest.mark.parametrize(("candidate_id", "expected_kept"), [
    ("adx_high", {9}), ("adx_middle_high", {6, 9}),
])
def test_tertile_candidates_keep_the_declared_groups_and_lose_no_rows(candidate_id, expected_kept):
    rows = [compact_row(value) for value in (0, 3, 6, 9)]
    cuts = regime.train_cuts(rows)
    candidate = next(item for item in regime.CANDIDATES if item["id"] == candidate_id)
    kept, dropped = regime.split(rows, candidate, cuts)
    assert {row["adx14_15m"] for row in kept} == {float(value) for value in expected_kept}
    assert len(kept) + len(dropped) == len(rows)
    assert not {id(row) for row in kept} & {id(row) for row in dropped}


def test_flag_candidate_splits_on_the_trade_direction_not_merely_any_alignment():
    rows = [compact_row(10, aligned=True, aligned_direction=True),
            compact_row(20, aligned=True, aligned_direction=False),
            compact_row(30, aligned=False, aligned_direction=False)]
    cuts = regime.train_cuts(rows)
    candidate = next(item for item in regime.CANDIDATES if item["kind"] == "flag")
    kept, dropped = regime.split(rows, candidate, cuts)
    assert [row["adx14_15m"] for row in kept] == [10]
    assert [row["adx14_15m"] for row in dropped] == [20, 30]
    flags = regime.tables(rows, cuts)["flags"]
    assert flags["ema_aligned_any"]["TRUE"]["N"] == 2
    assert flags["ema_aligned_with_direction"]["TRUE"]["N"] == 1


def test_tables_report_every_candidate_with_both_sides_and_direction_breakdown():
    rows = [compact_row(value, direction=side, path=path)
            for value, side, path in ((1, "LONG", "TP3"), (5, "SHORT", "STOP_BEFORE_TP1"),
                                      (9, "LONG", "TP3"), (12, "SHORT", "TP3"))]
    cuts = regime.train_cuts(rows)
    observed = regime.tables(rows, cuts)
    assert set(observed["candidates"]) == {candidate["id"] for candidate in regime.CANDIDATES}
    for entry in observed["candidates"].values():
        assert entry["kept"]["N"] + entry["dropped"]["N"] == 4
    assert set(observed["adx_tertiles_by_direction"]) == {"LONG", "SHORT"}
    assert observed["all"]["N"] == 4 and observed["adx_observed"]["p50"] == pytest.approx(7)


def test_verdict_is_gec_only_when_both_phases_clear_the_pre_registered_bar():
    assert all(value["verdict"] == "GEC"
               for value in regime.verdicts(phases_with(PASSING, WEAKER)).values())


@pytest.mark.parametrize(("kept", "reason"), [
    ({**PASSING, "N": 99}, "N_BELOW_100"),
    ({**PASSING, "mean_net_r": 0.0}, "MEAN_NET_R_NOT_POSITIVE"),
    ({**PASSING, "profit_factor_r": 1.15}, "PROFIT_FACTOR_NOT_ABOVE_BAR"),
    ({**PASSING, "profit_factor_r": None}, "PROFIT_FACTOR_NOT_ABOVE_BAR"),
])
def test_each_bar_failure_forces_dur_and_names_itself(kept, reason):
    observed = regime.verdicts(phases_with(kept, WEAKER))
    for value in observed.values():
        assert value["verdict"] == "DUR"
        assert any(check.endswith(reason) for check in value["failed_checks"])
        assert {check.split("_")[0] for check in value["failed_checks"]} <= {"TRAIN", "VALIDATION"}


def test_a_kept_group_no_better_than_the_dropped_group_is_dur_even_when_profitable():
    observed = regime.verdicts(phases_with(PASSING, {**WEAKER, "mean_net_r": .2}))
    for value in observed.values():
        assert value["verdict"] == "DUR"
        assert any("NO_SEPARATION" in check for check in value["failed_checks"])


def test_one_failing_phase_is_enough_to_stop_a_candidate():
    phases = phases_with(PASSING, WEAKER)
    for entry in phases["VALIDATION"]["tables"]["candidates"].values():
        entry["kept"]["mean_net_r"] = -.05
    observed = regime.verdicts(phases)
    for value in observed.values():
        assert value["verdict"] == "DUR"
        assert all(check.startswith("VALIDATION") for check in value["failed_checks"])


def phase_and_rows(loop, name="TRAIN"):
    data, plan, phase = fixtures.dataset(), locked_plan(), fixtures.phase(name=name)
    native = OriginalGapRiskEngine(data, primary_config(phase, plan))
    reference = loop.run_until_complete(regime.parent.gap.replay_counts(native, phase))
    counts, rows = loop.run_until_complete(regime.phase_result(data, plan, phase, reference))
    return counts, rows, reference, data, plan, phase


def test_capture_adds_regime_context_from_the_native_analysis_and_keeps_counts(offline_only):
    counts, rows, reference, data, plan, phase = phase_and_rows(offline_only)
    assert counts == reference and len(rows) == counts["counts"]["fully_verified_completed"] == 1
    row = rows[0]
    policy = regime.parent.PROFILE.policy(primary_config(phase, plan).policy)
    analysis = data.canonical(fixtures.BTC, fixtures.AT, policy)["analysis"]
    averages = analysis["ema"]
    assert row["adx14_15m"] == analysis["adx"]
    assert row["ema50_15m"] == averages["ema50"] and row["ema200_15m"] == averages["ema200"]
    rising = averages["ema20"] > averages["ema50"] > averages["ema200"]
    falling = averages["ema20"] < averages["ema50"] < averages["ema200"]
    assert row["ema_aligned_any"] is bool(rising or falling)
    assert row["ema_aligned_with_direction"] is bool(
        rising if analysis["direction"] == "LONG" else falling)
    assert row["confidence"] == analysis["confidence"]
    regime.validate_rows(rows, "TRAIN", counts)


def test_validation_rejects_non_boolean_flags_contradictions_and_bad_adx(offline_only):
    counts, rows, *_ = phase_and_rows(offline_only)
    for field, value, message in (
        ("ema_aligned_any", 1, "FLAG_NOT_BOOLEAN"),
        ("adx14_15m", -1.0, "NONFINITE_ADX"),
        ("adx14_15m", 5, "NONFINITE_ADX"),
    ):
        broken = copy.deepcopy(rows)
        broken[0][field] = value
        with pytest.raises(regime.MeasurementError, match=message):
            regime.validate_rows(broken, "TRAIN", counts)
    contradiction = copy.deepcopy(rows)
    contradiction[0].update(ema_aligned_any=False, ema_aligned_with_direction=True)
    with pytest.raises(regime.MeasurementError, match="FLAG_CONTRADICTION"):
        regime.validate_rows(contradiction, "TRAIN", counts)


def test_slim_rows_roundtrip_without_bar_envelopes_and_never_overwrite(tmp_path, offline_only):
    counts, rows, *_ = phase_and_rows(offline_only)
    regime.validate_rows(rows, "TRAIN", counts)
    manifest = regime.write_raw(tmp_path, "TRAIN", rows)
    path = tmp_path / "regime-trades-TRAIN.jsonl"
    assert manifest == {"file": path.name, "sha256": regime.sha256_file(path), "N": len(rows)}
    loaded = [json.loads(line) for line in path.read_text().splitlines()]
    assert loaded == [regime.slim(row) for row in rows]
    assert "bars_15m" not in loaded[0] and "native_row" not in loaded[0]
    assert loaded[0]["adx14_15m"] == rows[0]["adx14_15m"]
    with pytest.raises(FileExistsError):
        regime.write_raw(tmp_path, "TRAIN", rows)


def built_report(tmp_path, loop):
    plan = locked_plan()
    original = regime.parent.gap.provenance(plan)
    provenance = regime.parent.provenance(
        plan, {name: {"provenance": original} for name in ("TRAIN", "VALIDATION")})
    phases, raw = {}, {}
    for name in ("TRAIN", "VALIDATION"):
        counts, rows, *_ = phase_and_rows(loop, name)
        raw[name] = rows
        phases[name] = {"native_counts": counts, "raw_trades": regime.write_raw(tmp_path, name, rows)}
    cuts = regime.train_cuts(raw["TRAIN"])
    for name, value in phases.items():
        value["tables"] = regime.tables(raw[name], cuts)
    report = {
        "schema": regime.SCHEMA, "definitions": copy.deepcopy(regime.DEFINITIONS),
        "decision_rule": copy.deepcopy(regime.DECISION_RULE),
        "candidates": [dict(candidate) for candidate in regime.CANDIDATES],
        "profile": regime.parent.asdict(regime.parent.PROFILE),
        "provenance": {"parent_prescreen_provenance": provenance},
        "train_cutpoints": cuts, "phases": phases, "verdicts": {}, "elapsed_seconds": 0,
    }
    report["verdicts"] = regime.verdicts(report["phases"])
    return report, raw


def test_report_pins_registration_train_only_cuts_and_its_own_verdicts(tmp_path, offline_only):
    report, raw = built_report(tmp_path, offline_only)
    regime.validate_report(report, raw)
    moved = copy.deepcopy(report)
    moved["train_cutpoints"]["adx14_15m"][0] += 1
    with pytest.raises(regime.MeasurementError, match="TRAIN_ONLY_CUTS"):
        regime.validate_report(moved, raw)
    relaxed = copy.deepcopy(report)
    relaxed["decision_rule"]["kept_group_requirements_in_BOTH_phases"]["min_N"] = 1
    with pytest.raises(regime.MeasurementError, match="FIXED_REGISTRATION"):
        regime.validate_report(relaxed, raw)
    forced = copy.deepcopy(report)
    forced["verdicts"][regime.CANDIDATES[0]["id"]] = {"verdict": "GEC", "failed_checks": []}
    with pytest.raises(regime.MeasurementError, match="VERDICT_MISMATCH"):
        regime.validate_report(forced, raw)


def test_existing_output_stops_before_input_read(tmp_path):
    with patch.object(regime, "OUTPUT", tmp_path), \
         patch.object(regime, "locked_plan", side_effect=AssertionError("input read forbidden")):
        assert regime.main(["--metadata", str(regime.METADATA)]) == 2
