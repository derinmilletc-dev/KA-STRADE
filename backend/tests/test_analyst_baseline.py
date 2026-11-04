"""Analist başlangıç ölçüm aracı: sonuç kuralları, sızıntı/simetri kontrolleri ve uçtan uca sentetik çalıştırma."""
import io
import random
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import analyst_baseline_cli as tool  # noqa: E402

BAR = 900


def bar(t, o, h, l, c):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": 100.0}


def test_long_target_first_is_win_with_costs():
    bars = [bar(0, 100, 101, 99.5, 100.5), bar(BAR, 100.5, 102.5, 100, 102)]
    out = tool.simulate("LONG", 100, 98, bars + [bar(BAR * (i + 2), 102, 102, 102, 102) for i in range(100)], 5, 3)
    assert out["outcome"] == "KAZANC" and out["bars"] == 2
    assert abs(out["r"] - (1 - (100 + 102) * 0.0008 / 2)) < 1e-9


def test_same_bar_stop_and_target_is_loss():
    bars = [bar(0, 100, 103, 97, 100)] + [bar(BAR * (i + 1), 100, 100, 100, 100) for i in range(100)]
    assert tool.simulate("LONG", 100, 98, bars, 0, 0)["outcome"] == "KAYIP"


def test_short_is_mirror_of_long():
    bars = [bar(0, 100, 100.5, 97.5, 98)] + [bar(BAR * (i + 1), 98, 98, 98, 98) for i in range(100)]
    out = tool.simulate("SHORT", 100, 102, bars, 0, 0)
    assert out["outcome"] == "KAZANC" and abs(out["r"] - 1) < 1e-9


def test_timeout_is_reported_separately():
    bars = [bar(BAR * i, 100, 100.5, 99.5, 100.2) for i in range(tool.HORIZON)]
    out = tool.simulate("LONG", 100, 98, bars, 0, 0)
    assert out["outcome"] == "ZAMAN_ASIMI" and abs(out["r"] - 0.1) < 1e-9


def test_incomplete_horizon_is_not_counted():
    bars = [bar(BAR * i, 100, 100.5, 99.5, 100.2) for i in range(10)]
    assert tool.simulate("LONG", 100, 98, bars, 0, 0) is None


def test_invalid_stop_side_is_rejected():
    assert tool.simulate("LONG", 100, 101, [bar(0, 100, 100, 100, 100)] * 100, 0, 0) is None


def test_wilson_interval_and_bands():
    low, high = tool.wilson(54, 100)
    assert 0.44 < low < 0.45 and 0.63 < high < 0.64
    assert tool.band_of(35) == "35-49" and tool.band_of(95) == "90-100" and tool.band_of(20) is None
    assert tool.max_drawdown([1, -1, -1, 2, -3]) == -3


def test_closed_window_never_contains_future():
    rows = [bar(BAR * i, 1, 1, 1, 1) for i in range(1000)]
    window = tool.closed_window(rows, 700)
    assert window[-1]["time"] == rows[700]["time"] and len(window) == tool.WINDOW


def _synthetic(seed=3, count=4 * 2976):
    rng = random.Random(seed)
    price, rows, start = 100.0, [], tool.month_start("2026-07")
    for i in range(count):
        o = price
        price *= 1 + rng.gauss(0.0002 if (i // 700) % 2 else -0.0002, 0.004)
        h, l = max(o, price) * (1 + abs(rng.gauss(0, 0.002))), min(o, price) * (1 - abs(rng.gauss(0, 0.002)))
        rows.append(bar(start + i * BAR, o, h, l, price) | {"volume": rng.uniform(50, 150)})
    return rows


def _write_archives(root, symbol, interval, rows, step):
    for month in tool.month_list("2026-07", "2026-10"):
        lo, hi = tool.month_start(month), tool.month_end(month)
        chunk = [row for row in rows if lo <= row["time"] < hi]
        path = tool.archive_path(root, symbol, interval, month)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(f"{r['time'] * 1000},{r['open']},{r['high']},{r['low']},{r['close']},{r['volume']},{(r['time'] + step) * 1000 - 1},0,0,0,0,0\n" for r in chunk)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(path.name.replace(".zip", ".csv"), text)
        path.write_bytes(buffer.getvalue())


def test_end_to_end_on_synthetic_archives(tmp_path, monkeypatch):
    rows = _synthetic()
    four = []
    for i in range(0, len(rows) - 15, 16):
        chunk = rows[i:i + 16]
        four.append(bar(chunk[0]["time"], chunk[0]["open"], max(r["high"] for r in chunk), min(r["low"] for r in chunk), chunk[-1]["close"]))
    data = tmp_path / "data"
    _write_archives(data, "BTCUSDT", "15m", rows, BAR)
    _write_archives(data, "BTCUSDT", "4h", four, 14400)
    monkeypatch.setitem(tool.STAGES, "tiny", {"symbols": ("BTCUSDT",), "warmup": "2026-07", "start": "2026-08", "end": "2026-10",
                                              "periods": {"tum": ("2026-08", "2026-10")}})
    report = tool.run("tiny", data, tmp_path / "out", 1, 5, 3, True)
    assert report["analysis_unchanged"]
    assert report["checks"]["leak_failures"] == 0
    assert report["symbols"][0]["signals"] > 0
    signals = [line for line in (tmp_path / "out" / "signals.jsonl").read_text().splitlines() if line]
    import json
    parsed = [json.loads(line) for line in signals]
    # Bir sinyal sonuçlanmadan aynı coinde yenisi yok.
    for left, right in zip(parsed, parsed[1:]):
        assert right["time"] >= left["exit_time"]
    assert all(row["outcome"] in {"KAZANC", "KAYIP", "ZAMAN_ASIMI"} for row in parsed)
    assert (tmp_path / "out" / "report.md").read_text().startswith("# Eski analist sistemi")
