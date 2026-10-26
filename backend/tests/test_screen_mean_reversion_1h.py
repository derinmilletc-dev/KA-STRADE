"""Synthetic only: no historical dataset, results directory or archive access."""

from __future__ import annotations

import copy
import json
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

import screen_donchian_4h as base
import screen_mean_reversion_1h as screen
from app.analysis import atr
from app.backtest_data import Dataset, Series

AT, BTC, ETH = 1601510400, "BTCUSDT", "ETHUSDT"
START = AT + 120 * screen.SIGNAL_STEP
D = Decimal


def candle(at, opening=100.0, high=101.0, low=99.0, close=100.0):
    return {"time": at, "open": opening, "high": high, "low": low, "close": close,
            "volume": 100.0, "quote_volume": 20000000.0}


def h1_rows(bars, *, side="LONG"):
    end = START + bars * screen.STEP
    count = 120 + (end - START) // screen.SIGNAL_STEP + 2
    rows = [candle(AT + index * screen.SIGNAL_STEP) for index in range(count)]
    # Last closed bar at START: a sharp move outside the Bollinger band.
    rows[119] = (candle(rows[119]["time"], 100.0, 100.0, 95.5, 96.0) if side == "LONG"
                 else candle(rows[119]["time"], 100.0, 104.5, 100.0, 104.0))
    return rows


def data_for(*, bars=16, symbols=(BTC,), side="LONG", entry_open=None, flat=None):
    end = START + bars * screen.STEP
    opening = entry_open if entry_open is not None else (96.0 if side == "LONG" else 104.0)
    frames, marks, funding, months = {}, {}, {}, {}
    for symbol in symbols:
        m15 = [candle(at, opening, opening + 1.0, opening - 0.5, opening) for at in range(AT, end, screen.STEP)]
        if flat is not None:
            m15 = [candle(at, *flat) for at in range(AT, end, screen.STEP)]
        frames[symbol] = {"1h": Series("1h", h1_rows(bars, side=side)), "15m": Series("15m", m15)}
        marks[symbol] = Series("15m", copy.deepcopy(m15))
        funding[symbol] = [{"time": at, "rate": 0.0001, "interval_hours": 8.0}
                           for at in range(AT - 8 * 3600, end + 8 * 3600, 8 * 3600)]
        months[symbol] = {screen.month(event["time"]) for event in funding[symbol]}
    return Dataset(frames, marks, funding, {}, {"missing_archives": []}, months)


def phase(bars=16, name="TRAIN"):
    return screen.Phase(name, START, START + bars * screen.STEP)


def position(direction="LONG", opened=START):
    sign = 1 if direction == "LONG" else -1
    return screen.MeanPosition(BTC, direction, opened, D("100"), D(100 - 2 * sign), D(100 + 3 * sign))


def gate(monkeypatch, *, adx=10.0, rsi=None, side="LONG"):
    real = screen.indicator_values
    level = rsi if rsi is not None else (20.0 if side == "LONG" else 80.0)
    monkeypatch.setattr(screen, "indicator_values", lambda rows: {**real(rows), "adx": adx, "rsi": level})


def remove(series, at):
    return Series(series.interval, [row for row in series.rows if row["time"] != at])


def test_pre_registered_parameters_cannot_drift_silently():
    assert (screen.BB_N, screen.BB_K) == (20, 2.0)
    assert (screen.RSI_PERIOD, screen.RSI_LOW, screen.RSI_HIGH) == (14, 30.0, 70.0)
    assert (screen.ADX_PERIOD, screen.ADX_MAX) == (14, 20.0)
    assert (screen.ATR_PERIOD, screen.STOP_K) == (14, 1.5)
    assert screen.HOLD == 24 * 3600 and screen.SIGNAL_STEP == 3600
    assert (screen.WINDOW, screen.WARMUP) == (259, 100)
    assert screen.COST_MULTIPLE == 3
    assert screen.MIN_TARGET_FRACTION == pytest.approx(0.0048)
    assert (screen.MIN_N, screen.MIN_NET_R, screen.MIN_PF) == (150, 0.0, 1.15)
    assert screen.PHASES == base.PHASES
    assert (screen.FEE, screen.SLIP, screen.RISK) == (D("0.0005"), D("0.0003"), D(3))


def test_band_side_is_strictly_outside_and_zero_width_is_unusable():
    flat = [100.0] * 19
    assert screen.band_side(flat + [90.0])[0] == "LONG"
    assert screen.band_side(flat + [110.0])[0] == "SHORT"
    alternating = [99.0, 101.0] * 9 + [99.0, 100.5]
    assert screen.band_side(alternating)[0] is None
    assert screen.band_side([100.0] * 20) == (None, None)
    side, middle = screen.band_side(flat + [90.0])
    assert middle == pytest.approx(99.5)


def test_decision_uses_only_closed_bars_and_real_atr(monkeypatch):
    gate(monkeypatch)
    series = data_for().frames[BTC]["1h"]
    result = screen.observe(series, START)
    assert result.direction == "LONG" and result.reason == "signal"
    rows = series.closed(START, limit=screen.WINDOW)
    assert rows[-1]["time"] == START - screen.SIGNAL_STEP
    assert result.atr_value == atr([row["high"] for row in rows], [row["low"] for row in rows],
                                   [row["close"] for row in rows], period=14)
    assert result.middle == pytest.approx(sum(row["close"] for row in rows[-20:]) / 20)
    series.rows[120].update(high=999999.0, low=1.0, close=999998.0)
    assert screen.observe(series, START) == result
    series.rows[119]["close"] = 100.0
    assert screen.observe(series, START).direction is None


def test_indicator_window_is_the_last_259_contiguous_closed_bars(monkeypatch):
    gate(monkeypatch)
    rows = [candle(AT + index * 3600) for index in range(400)]
    rows[-1] = candle(rows[-1]["time"], 100.0, 100.0, 95.5, 96.0)
    series = Series("1h", rows)
    at = AT + 400 * 3600
    seen = {}
    real = screen.indicator_values
    monkeypatch.setattr(screen, "indicator_values",
                        lambda window: seen.setdefault("n", len(window)) and real(window) | {"adx": 5.0, "rsi": 10.0})
    assert screen.observe(series, at).direction == "LONG"
    assert seen["n"] == screen.WINDOW


def test_gap_resets_the_window_and_requires_100_contiguous_bars(monkeypatch):
    gate(monkeypatch)
    series = remove(data_for().frames[BTC]["1h"], AT + 40 * screen.SIGNAL_STEP)
    assert screen.observe(series, START).reason == "warmup_1h"
    long_enough = remove(data_for().frames[BTC]["1h"], AT + 10 * screen.SIGNAL_STEP)
    assert screen.observe(long_enough, START).direction == "LONG"


@pytest.mark.parametrize(("strength", "expected"), [(19.99, "signal"), (20.0, "trending_adx"), (35.0, "trending_adx")])
def test_adx_regime_gate_is_strictly_below_20(monkeypatch, strength, expected):
    gate(monkeypatch, adx=strength)
    assert screen.observe(data_for().frames[BTC]["1h"], START).reason == expected


@pytest.mark.parametrize(("side", "level", "expected"), [
    ("LONG", 29.99, "signal"), ("LONG", 30.0, "rsi_not_extreme"), ("LONG", 55.0, "rsi_not_extreme"),
    ("SHORT", 70.01, "signal"), ("SHORT", 70.0, "rsi_not_extreme"), ("SHORT", 45.0, "rsi_not_extreme"),
])
def test_rsi_extreme_gate_is_strict_and_direction_specific(monkeypatch, side, level, expected):
    gate(monkeypatch, rsi=level, side=side)
    result = screen.observe(data_for(side=side).frames[BTC]["1h"], START)
    assert result.reason == expected
    if expected == "signal":
        assert result.direction == side


def test_inside_band_never_computes_the_expensive_indicators(monkeypatch):
    monkeypatch.setattr(screen, "indicator_values", lambda rows: pytest.fail("indicators must be skipped"))
    series = data_for().frames[BTC]["1h"]
    for index in range(100, 119):
        series.rows[index]["close"] = 99.0 if index % 2 else 101.0
    series.rows[119]["close"] = 100.5
    assert screen.observe(series, START).reason == "inside_band"
    flat = data_for().frames[BTC]["1h"]
    flat.rows[119]["close"] = 100.0
    assert screen.observe(flat, START).reason == "zero_width_band"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_entry_is_next_15m_open_with_one_r_risk_and_middle_target(monkeypatch, side):
    gate(monkeypatch, side=side)
    observation = screen.observe(data_for(side=side).frames[BTC]["1h"], START)
    opening = 96.0 if side == "LONG" else 104.0
    value, reason = screen.create_position(BTC, START, opening, observation)
    sign = 1 if side == "LONG" else -1
    assert reason == "accepted" and value.opened_at == START
    assert value.entry == D(str(opening)) and value.target == D(str(observation.middle))
    assert value.stop == D(str(opening - sign * 1.5 * observation.atr_value))
    assert value.actual_entry == value.entry * (1 + sign * screen.SLIP)
    assert value.quantity * abs(value.entry - value.stop) == screen.RISK


def test_cost_rule_and_target_direction_reject_without_tuning(monkeypatch):
    gate(monkeypatch)
    observation = screen.observe(data_for().frames[BTC]["1h"], START)
    middle = observation.middle
    assert screen.create_position(BTC, START, middle + 0.5, observation) == (None, "target_not_ahead")
    assert screen.create_position(BTC, START, middle, observation) == (None, "target_not_ahead")
    near = middle * (1 - 0.0047)
    assert screen.create_position(BTC, START, near, observation) == (None, "target_too_close")
    far = middle * (1 - 0.0050)
    assert screen.create_position(BTC, START, far, observation)[1] == "accepted"
    tiny = screen.Observation("LONG", 1000.0, 100.0, "signal")
    assert screen.create_position(BTC, START, 10.0, tiny) == (None, "invalid_positive_levels")


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_stop_wins_when_stop_and_target_touch_in_one_15m_bar(side):
    value = position(side)
    value.advance(candle(START, 100.0, 104.0, 96.0), opening_only=False)
    assert value.status == "CLOSED"
    assert value.exits == [("STOP", value.quantity, START + 899)]
    assert value.frictionless / screen.RISK == -1


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_target_fills_at_the_middle_with_exact_costs(side):
    value = position(side)
    sign = value.sign
    row = candle(START, 100.0, 103.5 if sign == 1 else 100.5, 99.5 if sign == 1 else 96.5)
    value.advance(row, opening_only=False)
    assert value.exits == [("TARGET", value.quantity, START + 899)]
    assert value.frictionless / screen.RISK == D("1.5")
    expected_exit = D("103") * (1 - sign * screen.SLIP) if sign == 1 else D("97") * (1 + screen.SLIP)
    assert value.gross == value.quantity * sign * (expected_exit - value.actual_entry)
    costs = value.commission + value.frictionless - value.gross - value.funding
    assert value.frictionless - costs == value.net
    assert value.net < value.frictionless


def test_gap_through_stop_or_target_fills_at_the_open_not_the_level():
    stopped = position()
    stopped.advance(candle(START + 900, 97.0, 97.5, 96.5, 97.0), opening_only=True)
    assert stopped.exits[0][0] == "STOP" and stopped.closed_at == START + 900
    assert stopped.frictionless / screen.RISK == -1.5
    won = position()
    won.advance(candle(START + 900, 104.0, 104.5, 103.5, 104.0), opening_only=True)
    assert won.exits[0][0] == "TARGET" and won.frictionless / screen.RISK == D("2")


def test_time_stop_exits_at_the_first_open_at_or_after_24_hours():
    value = position()
    value.advance(candle(START + screen.HOLD - 900, 101.0, 101.5, 100.5), opening_only=True)
    assert value.status == "OPEN"
    value.advance(candle(START + screen.HOLD, 101.0, 101.5, 100.5), opening_only=True)
    assert value.exits == [("TIME", value.quantity, START + screen.HOLD)]
    assert value.frictionless / screen.RISK == D("0.5")
    late = position()
    late.advance(candle(START + screen.HOLD + 900, 99.0, 99.5, 98.5), opening_only=True)
    assert late.exits[0][0] == "TIME"


def test_stop_beats_time_and_target_beats_time_at_the_open():
    both = position()
    both.advance(candle(START + screen.HOLD, 97.0, 97.5, 96.5), opening_only=True)
    assert both.exits[0][0] == "STOP"
    won = position()
    won.advance(candle(START + screen.HOLD, 104.0, 104.5, 103.5), opening_only=True)
    assert won.exits[0][0] == "TARGET"


def test_invalid_positions_are_rejected_explicitly():
    with pytest.raises(screen.ScreeningError, match="Stop"):
        screen.MeanPosition(BTC, "LONG", START, D("100"), D("101"), D("103"))
    with pytest.raises(screen.ScreeningError, match="target"):
        screen.MeanPosition(BTC, "LONG", START, D("100"), D("98"), D("99"))
    with pytest.raises(screen.ScreeningError, match="direction"):
        screen.MeanPosition(BTC, "FLAT", START, D("100"), D("98"), D("103"))


def test_run_phase_takes_the_signal_and_exits_at_the_target(monkeypatch):
    gate(monkeypatch)
    data = data_for(bars=2)
    data.frames[BTC]["15m"].at(START + 900).update(high=100.5)
    report = screen.run_phase(data, phase(2))
    value = report["metrics"]
    assert value["accepted"] == value["N"] == 1 and value["uncertain"] == value["open_at_end"] == 0
    assert report["exit_reason_counts"] == {"TARGET": 1, "STOP": 0, "TIME": 0}
    assert report["direction_counts"]["LONG"]["accepted"] == 1
    assert report["direction_counts"]["SHORT"]["accepted"] == 0
    assert value["mean_gross_r"] > 0 and value["mean_net_r"] < value["mean_gross_r"]
    assert report["pass_checks"] == screen.pass_checks(value)


def test_run_phase_cost_filter_is_counted_and_blocks_the_entry(monkeypatch):
    gate(monkeypatch)
    data = data_for(bars=2, entry_open=99.7)
    report = screen.run_phase(data, phase(2))
    assert report["metrics"]["accepted"] == 0
    assert report["entry_rejections"]["target_too_close"] == 1


def test_overlap_skips_a_new_signal_while_a_position_is_open(monkeypatch):
    real = screen.observe
    gate(monkeypatch)
    monkeypatch.setattr(screen, "observe", lambda series, at: real(series, START))
    data = data_for(bars=5, flat=(96.0, 97.0, 95.5, 96.0))
    report = screen.run_phase(data, phase(5))
    assert report["signals"] == 2 and report["overlap_skipped"] == 1
    assert report["metrics"]["accepted"] == 1 and report["metrics"]["open_at_end"] == 1
    assert report["metrics"]["N"] == 0 and report["metrics"]["mean_net_r"] is None


def test_open_position_is_not_verified_n_and_exit_counts_ignore_it(monkeypatch):
    gate(monkeypatch)
    report = screen.run_phase(data_for(bars=1, flat=(96.0, 97.0, 95.5, 96.0)), phase(1))
    assert report["metrics"]["open_at_end"] == 1 and report["metrics"]["N"] == 0
    assert sum(report["exit_reason_counts"].values()) == 0
    assert report["open_trades"] == [{"symbol": BTC, "opened_at": screen.stamp(START)}]


@pytest.mark.parametrize("kind", ["contract", "mark", "funding"])
def test_new_entries_fail_closed_on_missing_current_data(monkeypatch, kind):
    gate(monkeypatch)
    data = data_for(bars=2)
    if kind == "contract":
        data.frames[BTC]["15m"] = remove(data.frames[BTC]["15m"], START)
    elif kind == "mark":
        data.marks[BTC] = remove(data.marks[BTC], START)
    else:
        data.funding[BTC] = []
    report = screen.run_phase(data, phase(2))
    assert report["metrics"]["accepted"] == 0
    assert report["entry_rejections"]["missing_entry_data"] == 1


def test_blackout_is_symbol_local_and_counted(monkeypatch):
    gate(monkeypatch)
    bars = screen.BLACKOUT // screen.STEP + 1
    data = data_for(bars=bars, symbols=(BTC, ETH))
    gap_at = START + screen.BLACKOUT
    data.marks[BTC] = remove(data.marks[BTC], gap_at)
    report = screen.run_phase(data, phase(bars))
    assert report["by_symbol_counts"][BTC]["accepted"] == 0
    assert report["by_symbol_counts"][ETH]["accepted"] == 1
    assert report["entry_rejections"]["gap_blackout"] == 1


def test_closed_trade_with_incomplete_funding_is_unknown_and_excluded(monkeypatch):
    gate(monkeypatch)
    data = data_for(bars=2)
    data.frames[BTC]["15m"].at(START + 900).update(high=100.5)
    calls = []

    def incomplete(self, symbol, opened, closed):
        calls.append((symbol, opened, closed))
        return False

    monkeypatch.setattr(Dataset, "funding_complete", incomplete)
    report = screen.run_phase(data, phase(2))
    assert len(calls) == 1 and calls[0][:2] == (BTC, START)
    assert report["metrics"]["accepted"] == report["metrics"]["uncertain"] == 1
    assert report["metrics"]["N"] == 0 and report["metrics"]["mean_net_r"] is None
    assert sum(report["exit_reason_counts"].values()) == 0
    assert report["uncertain_trades"][0]["reasons"] == ["funding_incomplete"]


@pytest.mark.parametrize("kind", ["contract", "mark"])
def test_existing_position_becomes_unknown_when_current_data_vanishes(monkeypatch, kind):
    gate(monkeypatch)
    data = data_for(bars=3, flat=(96.0, 97.0, 95.5, 96.0))
    if kind == "contract":
        data.frames[BTC]["15m"] = remove(data.frames[BTC]["15m"], START + 900)
    else:
        data.marks[BTC] = remove(data.marks[BTC], START + 900)
    with patch.object(screen, "blackout", return_value=False):
        report = screen.run_phase(data, phase(3))
    assert report["metrics"]["uncertain"] == 1
    assert report["metrics"]["N"] == 0 and report["metrics"]["open_at_end"] == 0
    assert report["uncertain_trades"][0]["detected_at"] == screen.stamp(START + 900)


@pytest.mark.parametrize(("n", "net", "pf", "status", "expected"), [
    (150, 0.01, 1.15000001, "DEFINED", True),
    (149, 0.01, 1.2, "DEFINED", False),
    (150, 0.0, 1.2, "DEFINED", False),
    (150, 0.01, 1.15, "DEFINED", False),
    (150, 0.01, None, "UNDEFINED", False),
    (150, 0.01, None, "NO_LOSSES", True),
])
def test_pass_checks_follow_the_fixed_bar_and_agree_with_the_decision(n, net, pf, status, expected):
    value = {"N": n, "mean_net_r": net, "usdt_pf": pf, "pf_status": status}
    assert all(screen.pass_checks(value).values()) is expected
    phases = {"TRAIN": {"metrics": copy.deepcopy(value)}, "VALIDATION": {"metrics": value}}
    assert screen.decision(phases) == ("DEVAM" if expected else "BIRAK")


def test_each_failed_check_is_named_independently():
    value = {"N": 10, "mean_net_r": -0.1, "usdt_pf": 0.5, "pf_status": "DEFINED"}
    assert screen.pass_checks(value) == {"n_at_least_min": False, "mean_net_r_positive": False,
                                         "profit_factor_above_bar": False}


def test_schema_has_counts_only_and_rejects_tampering(monkeypatch):
    gate(monkeypatch)
    monkeypatch.setattr(screen, "PHASES", (phase(1), screen.Phase("VALIDATION", START + 900, START + 1800)))
    report = screen.screen(data_for(bars=2), {"synthetic": True})
    screen.validate_report(report)
    json.dumps(report, allow_nan=False)
    assert report["disclaimer"] == screen.DISCLAIMER and "SEEN_WINDOWS" in report["disclaimer"]
    assert report["assumptions"]["one_setting_one_run"] and report["assumptions"]["windows_already_seen"]
    for value in report["phases"].values():
        for key in ("by_symbol_counts", "by_entry_month_counts", "direction_counts"):
            assert all(set(cell) == {"accepted", "N", "uncertain", "open_at_end"} for cell in value[key].values())
    for mutate, message in (
        (lambda r: r["phases"]["TRAIN"]["by_symbol_counts"][BTC].update(mean_net_r=1), "performance"),
        (lambda r: r["phases"]["TRAIN"]["direction_counts"]["LONG"].update(mean_net_r=1), "performance"),
        (lambda r: r["phases"]["TRAIN"]["pass_checks"].update(n_at_least_min=True), "Pass checks"),
        (lambda r: r.update(decision="DEVAM"), "Decision differs"),
        (lambda r: r["phases"]["TRAIN"]["exit_reason_counts"].update(TARGET=99), "Exit counts"),
        (lambda r: r.update(disclaimer="x"), "declaration"),
    ):
        broken = copy.deepcopy(report)
        mutate(broken)
        with pytest.raises(screen.ScreeningError, match=message):
            screen.validate_report(broken)


def test_existing_results_stop_before_any_metadata_or_dataset_read(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(screen, "OUTPUT", tmp_path)
    monkeypatch.setattr(screen, "digest", lambda _path: pytest.fail("must stop before reading input"))
    assert screen.main(["--metadata", str(tmp_path / "missing.json")]) == 2
    assert "already exists" in capsys.readouterr().err
