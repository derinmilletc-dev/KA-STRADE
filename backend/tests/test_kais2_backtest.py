import csv
import io
import math
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kais2_backtest as bt  # noqa: E402

START = 1719792000  # 2024-07-01 00:00 UTC


def candle(time, o, h, l, c):
    return {"time": time, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}


def test_stop_first_when_stop_and_target_in_same_candle():
    bars = [candle(START, 100, 103.5, 94, 100)]
    out = bt.simulate_exit("LONG", START, 100, 95, 101, 103, bars)
    assert [leg["reason"] for leg in out["legs"]] == ["STOP"]
    assert out["ambiguous"] == 1


def test_tp1_then_tp3_long_and_short():
    bars = [candle(START, 100, 101.5, 99.5, 101), candle(START + 900, 101, 103.2, 100.5, 103)]
    legs = bt.simulate_exit("LONG", START, 100, 95, 101, 103, bars)["legs"]
    assert [leg["reason"] for leg in legs] == ["TP1", "TP3"]
    assert math.isclose(sum(leg["fraction"] for leg in legs), 1.0)
    short = [candle(START, 100, 100.5, 98.5, 99), candle(START + 900, 99, 99.5, 96.8, 97)]
    legs = bt.simulate_exit("SHORT", START, 100, 105, 99, 97, short)["legs"]
    assert [leg["reason"] for leg in legs] == ["TP1", "TP3"]


def test_data_gap_closes_at_last_close_without_guessing():
    bars = [candle(START, 100, 100.5, 99.5, 100.2), candle(START + 1800, 100, 101, 99, 100)]
    legs = bt.simulate_exit("LONG", START, 100, 95, 105, 110, bars)["legs"]
    assert legs[-1]["reason"] == "VERI_BOSLUGU" and legs[-1]["price"] == 100.2


def test_costs_reduce_result_and_scale():
    legs = [{"time": START, "fraction": 1.0, "price": 101.0, "reason": "TP3"}]
    gross = 100.0 / 100 * 1 * 1.0  # 1 USDT brüt (notional 100, +%1)
    base = bt.price_net("LONG", 100.0, legs, 100.0, 0.0, 1.0)
    double = bt.price_net("LONG", 100.0, legs, 100.0, 0.0, 2.0)
    assert 0 < double < base < gross
    assert math.isclose(bt.price_net("LONG", 100.0, legs, 100.0, 0.5, 1.0), base - 0.5)


def test_funding_sign_long_pays_positive_rate():
    legs = [{"time": START + 20000, "fraction": 1.0, "price": 100.0, "reason": "TP3"}]
    funding = [(START + 100, 0.0001)]
    long_cost = bt.funding_cost("LONG", START, 1000.0, 100.0, legs, funding, lambda _: 100.0)
    short_cost = bt.funding_cost("SHORT", START, 1000.0, 100.0, legs, funding, lambda _: 100.0)
    assert math.isclose(long_cost, 0.1) and math.isclose(short_cost, -0.1)


def test_aggregate_skips_incomplete_hours():
    full = [candle(START + i * 900, 1, 2, 0.5, 1.5) for i in range(4)]
    partial = [candle(START + 3600 + i * 900, 1, 2, 0.5, 1.5) for i in range(3)]
    bars = bt.aggregate(full + partial, 3600)
    assert len(bars) == 1 and bars[0]["time"] == START


def test_locked_months_never_loaded(tmp_path):
    folder = tmp_path / "raw" / "klines_15m" / "AAAUSDT"
    folder.mkdir(parents=True)
    for month in ("2026-05", "2026-06"):
        with zipfile.ZipFile(folder / f"AAAUSDT-15m-{month}.zip", "w") as archive:
            archive.writestr("x.csv", f"{START * 1000},1,2,0.5,1.5,10,0,0,0,0,0,0\n")
    candles = bt.load_candles(tmp_path, "AAAUSDT")
    assert len(candles) == 1  # yalnız 2026-05
    assert bt.locked("2026-06") and not bt.locked("2026-05")


def test_live_mtf_gate_is_the_real_function():
    gate = bt.load_live_mtf()
    result = gate(symbol="X", entry_direction="LONG", confidence_15m=85.0,
                  timeframe_results={"1h": {"direction": "LONG", "confidence": 80}, "4h": {"direction": "SHORT", "confidence": 80}})
    assert result["entry_permission"] is False


def make_data(root: Path, symbol: str, months: list[str]):
    raw = root / "raw"
    (raw / "klines_15m" / symbol).mkdir(parents=True)
    (raw / "fundingRate" / symbol).mkdir(parents=True)
    price, stamp = 100.0, START - 61 * 86400
    rows: dict[str, list[str]] = {}
    for step in range(61 * 96 + 31 * 96 * 2):
        drift = 0.0006 if (step // 700) % 2 == 0 else -0.0005
        wave = math.sin(step / 9) * 0.002
        close = price * (1 + drift + wave)
        high, low = max(price, close) * 1.002, min(price, close) * 0.998
        month = bt.month_str(stamp)
        rows.setdefault(month, []).append(f"{stamp * 1000},{price},{high},{low},{close},{50 + step % 7},0,0,0,0,0,0")
        price, stamp = close, stamp + 900
    for month, lines in rows.items():
        with zipfile.ZipFile(raw / "klines_15m" / symbol / f"{symbol}-15m-{month}.zip", "w") as archive:
            archive.writestr("k.csv", "\n".join(lines) + "\n")
        with zipfile.ZipFile(raw / "fundingRate" / symbol / f"{symbol}-fundingRate-{month}.zip", "w") as archive:
            archive.writestr("f.csv", "calc_time,funding_interval_hours,last_funding_rate\n"
                             f"{int(bt.datetime.strptime(month, '%Y-%m').replace(tzinfo=bt.timezone.utc).timestamp()) * 1000 + 3600000},8,0.0001\n")
    reports = root / "reports2-test"
    reports.mkdir()
    with (reports / "validation-coin-month.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["month", "symbol", "status", "missing_kinds", "candle_gap_total", "duplicate_rows", "unsorted_kinds"])
        for month in months:
            writer.writerow([month, symbol, "tam", "", 0, 0, ""])
        writer.writerow(["2024-09", symbol, "eksik: fundingRate", "fundingRate", 0, 0, ""])
    stage1 = root / "reports-stage1"
    stage1.mkdir()
    with (stage1 / "universe-by-month.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["month", "rank", "symbol", "avg_daily_usdt_prev_month", "testable", "reason", "delisted"])
        for month in months + ["2024-09"]:
            writer.writerow([month, 1, symbol, 1e9, "True", "", "False"])


def test_end_to_end_runs_reports_and_excludes_missing(tmp_path):
    root = tmp_path / "data"
    make_data(root, "TESTUSDT", ["2024-07", "2024-08"])
    out = tmp_path / "out"
    code = bt.main(["--data", str(root), "--out", str(out), "--workers", "1"])
    assert code == 0
    names = {path.name for path in out.iterdir()}
    assert {"summary.md", "trades.csv", "by-month.csv", "by-coin.csv", "excluded-coin-months.csv"} <= names
    summary = (out / "summary.md").read_text(encoding="utf-8")
    assert "Kilitli dönem" in summary and "okunmadı" in summary
    excluded = (out / "excluded-coin-months.csv").read_text(encoding="utf-8")
    assert "2024-09" in excluded
    assert bt.main(["--data", str(root), "--out", str(out)]) == 2  # dolu klasör ezilmez


def test_end_to_end_with_trades(tmp_path):
    root = tmp_path / "data"
    make_data(root, "TESTUSDT", ["2024-07", "2024-08"])
    original = (bt.core.configured_min_confidence, bt.core.risk_sized_order)
    bt.core.configured_min_confidence = lambda: 0
    bt.core.risk_sized_order = lambda entry, stop, policy, atr=None: {"notional_usdt": 300.0, "estimated_stop_loss_usdt": 3.0}
    try:
        out = tmp_path / "out"
        assert bt.main(["--data", str(root), "--out", str(out), "--workers", "1"]) == 0
    finally:
        bt.core.configured_min_confidence, bt.core.risk_sized_order = original
    rows = list(csv.DictReader((out / "trades.csv").open(encoding="utf-8")))
    assert rows and all(row["exit_time"] >= row["entry_time"] for row in rows)
    assert "Maliyet duyarlılığı" in (out / "summary.md").read_text(encoding="utf-8")


def test_only_side_filters_other_direction(tmp_path):
    root = tmp_path / "data"
    make_data(root, "TESTUSDT", ["2024-07", "2024-08"])
    original = (bt.core.configured_min_confidence, bt.core.risk_sized_order)
    bt.core.configured_min_confidence = lambda: 0
    bt.core.risk_sized_order = lambda entry, stop, policy, atr=None: {"notional_usdt": 300.0, "estimated_stop_loss_usdt": 3.0}
    try:
        for side, other in (("LONG", "SHORT"), ("SHORT", "LONG")):
            out = tmp_path / f"out-{side}"
            assert bt.main(["--data", str(root), "--out", str(out), "--workers", "1", "--only-side", side]) == 0
            rows = list(csv.DictReader((out / "trades.csv").open(encoding="utf-8")))
            assert all(row["direction"] == side for row in rows)
            assert all(row["direction"] != other for row in rows)
    finally:
        bt.core.configured_min_confidence, bt.core.risk_sized_order = original
