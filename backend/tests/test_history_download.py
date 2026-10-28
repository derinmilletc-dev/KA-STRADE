"""Ana indirme: aşama 2 yalnız --confirm ile iner; kilitli ay istenmez; SHA-256; eksik/boşluk raporlanır, doldurulmaz."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import sys
import urllib.parse
from collections import namedtuple
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import history_discovery as hd  # noqa: E402
import history_download as dl  # noqa: E402
import history_universe_probe as probe  # noqa: E402

DAILY_HEADER = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"


def start_ms(month: str) -> int:
    year, number = map(int, month.split("-"))
    return int(datetime(year, number, 1, tzinfo=timezone.utc).timestamp() * 1000)


def make_zip(name: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


def series(symbol: str, label: str, month: str, count: int = 10, skip: tuple[int, ...] = ()) -> bytes:
    step = {"klines_15m": 900, "klines_1h": 3600, "klines_4h": 14400, "markPriceKlines_15m": 900, "fundingRate": 28800}[label] * 1000
    base = start_ms(month)
    if label == "fundingRate":
        text = "calcTime,fundingIntervalHours,lastFundingRate\n" + "".join(
            f"{base + i * step},8,0.0001\n" for i in range(count) if i not in skip)
    else:
        text = DAILY_HEADER + "".join(
            f"{base + i * step},1,1,1,1,1,{base + i * step + step - 1},1,1,1,1,0\n" for i in range(count) if i not in skip)
    return make_zip(f"{symbol}-{label}-{month}.csv", text)


def daily(symbol: str, month: str, volume: float) -> bytes:
    base = start_ms(month)
    text = DAILY_HEADER + "".join(f"{base + d * 86400000},1,1,1,1,1,0,{volume},1,1,1,0\n" for d in range(28))
    return make_zip(f"{symbol}-1d-{month}.csv", text)


def put(store: dict[str, bytes], symbol: str, label: str, month: str, data: bytes) -> None:
    kind, _, interval = label.partition("_")
    prefix = f"{hd.MONTHLY}{kind}/{symbol}/" + (f"{interval}/" if interval else "")
    name = f"{symbol}-{interval or kind}-{month}.zip"
    store[prefix + name] = data
    store[prefix + name + ".CHECKSUM"] = (hashlib.sha256(data).hexdigest() + f"  {name}\n").encode()


class FakeHttp(probe.PoliteHttp):
    def __init__(self, store: dict[str, bytes]):
        super().__init__(rate=1000.0)
        self.store, self.urls = store, []

    def get(self, url: str) -> bytes:
        self.urls.append(url)
        parsed = urllib.parse.urlparse(url)
        assert parsed.hostname in hd.ALLOWED_HOSTS
        query = dict(urllib.parse.parse_qsl(parsed.query))
        if "prefix" in query:
            names = sorted(k for k in self.store if k.startswith(query["prefix"]) and k > query.get("marker", ""))
            body = "".join(f"<Contents><Key>{k}</Key><Size>{len(self.store[k])}</Size></Contents>" for k in names)
            return (f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated>{body}'
                    "</ListBucketResult>").encode()
        key = parsed.path.split("/data.binance.vision/", 1)[1]
        if key not in self.store:
            raise ValueError("404")
        return self.store[key]


def build_store() -> dict[str, bytes]:
    store: dict[str, bytes] = {}
    volumes = {"BIGUSDT": 9e6, "MIDUSDT": 5e6, "SMALLUSDT": 1e5}
    for symbol, volume in volumes.items():
        for month in ("2024-05", "2024-06", "2024-07", "2024-08"):
            put(store, symbol, "klines_1d", month, daily(symbol, month, volume))
    for month in ("2024-07", "2024-08"):
        put(store, "NEWUSDT", "klines_1d", month, daily("NEWUSDT", month, 9e7))
    labels = ("klines_15m", "klines_1h", "klines_4h", "markPriceKlines_15m", "fundingRate")
    for symbol in ("BIGUSDT", "MIDUSDT", "SMALLUSDT", "NEWUSDT"):
        for month in ("2024-07", "2024-08"):
            for label in labels:
                if symbol == "MIDUSDT" and month == "2024-08" and label == "fundingRate":
                    continue                                          # funding eksik: doldurulmaz
                skip = (3,) if (symbol == "BIGUSDT" and month == "2024-07" and label == "klines_15m") else ()
                put(store, symbol, label, month, series(symbol, label, month, skip=skip))
    for label in labels:                                              # kilitli ay arşivde var: istenmemeli
        put(store, "BIGUSDT", label, "2026-06", series("BIGUSDT", label, "2026-06"))
    return store


def report_file(tmp_path: Path) -> Path:
    path = tmp_path / "discovery-report.json"
    path.write_text(json.dumps({"version": "history-discovery-v1", "candidates": ["BIGUSDT", "MIDUSDT", "NEWUSDT", "SMALLUSDT"],
                                "coins": {s: {"testable_months": [], "missing_funding_months": []}
                                          for s in ("BIGUSDT", "MIDUSDT", "NEWUSDT", "SMALLUSDT")}}), encoding="utf-8")
    return path


Usage = namedtuple("Usage", "total used free")


def run_with(store: dict[str, bytes], argv: list[str], free: int = 10 ** 12) -> tuple[int, FakeHttp]:
    """Testler çalıştıran makinenin boş diskine bağlı olmasın: boş alan sabitlenir."""
    fake = FakeHttp(store)
    original, original_disk = probe.PoliteHttp, shutil.disk_usage
    probe.PoliteHttp = lambda rate=0: fake  # type: ignore[assignment]
    shutil.disk_usage = lambda path: Usage(free * 2, free, free)  # type: ignore[assignment]
    try:
        return dl.main(argv), fake
    finally:
        probe.PoliteHttp = original  # type: ignore[assignment]
        shutil.disk_usage = original_disk  # type: ignore[assignment]


def stage1(tmp_path: Path, store: dict[str, bytes]) -> Path:
    out = tmp_path / "out"
    code, _ = run_with(store, ["stage1", "--report", str(report_file(tmp_path)), "--output", str(out)])
    assert code == 0
    return out


def test_stage2_without_confirm_only_plans(tmp_path):
    store = build_store()
    out = stage1(tmp_path, store)
    code, http = run_with(store, ["stage2", "--report", str(report_file(tmp_path)), "--output", str(out)])
    assert code == 0
    assert not any(out.glob("raw/klines_15m")) and not (out / dl.STAGE2_MARKER).exists()
    assert not any(url.endswith((".zip", ".CHECKSUM")) for url in http.urls)  # plan: yalnız liste istekleri


def test_stage2_confirm_downloads_validates_and_never_touches_locked_months(tmp_path):
    store = build_store()
    out = stage1(tmp_path, store)
    code, http = run_with(store, ["stage2", "--report", str(report_file(tmp_path)), "--output", str(out), "--confirm"])
    assert code == 0
    for url in http.urls:
        assert "2026-06" not in url, url
    assert (out / dl.STAGE2_MARKER).is_file()
    reports = next(out.glob("reports2-*"))
    rows = list(csv.DictReader((reports / "validation-coin-month.csv").open(encoding="utf-8")))
    status = {(r["symbol"], r["month"]): r for r in rows}
    assert status[("SMALLUSDT", "2024-07")]["status"] == "tam"
    assert status[("BIGUSDT", "2024-07")]["status"].startswith("tam (")
    assert status[("BIGUSDT", "2024-07")]["candle_gap_total"] == "1"          # boşluk sayıldı, doldurulmadı
    assert status[("MIDUSDT", "2024-08")]["status"] == "eksik: fundingRate"   # funding tahminle doldurulmadı
    assert (out / "raw" / "fundingRate" / "MIDUSDT" / "MIDUSDT-fundingRate-2024-08.zip").exists() is False
    text = (reports / "validation-summary.md").read_text(encoding="utf-8")
    assert "Kilitli dönem" in text and "tahminle doldurulmadı" in text
    for csv_file in reports.glob("*.csv"):
        content = csv_file.read_text(encoding="utf-8")
        assert "2026-06" not in content


def test_resume_reuses_everything(tmp_path):
    store = build_store()
    out = stage1(tmp_path, store)
    args = ["stage2", "--report", str(report_file(tmp_path)), "--output", str(out), "--confirm"]
    assert run_with(store, args)[0] == 0
    code, http = run_with(store, args + ["--resume"])
    assert code == 0
    assert not any(url.endswith(".zip") for url in http.urls)                 # tekrar indirilmedi
    log = next(sorted(out.glob("reports2-*"))[-1].glob("download-log.csv"))
    assert all(r["status"].startswith("mevcut") for r in csv.DictReader(log.open(encoding="utf-8")))


def test_stage2_refuses_folder_without_stage1(tmp_path):
    empty = tmp_path / "bos"
    code, http = run_with(build_store(), ["stage2", "--report", str(report_file(tmp_path)), "--output", str(empty), "--confirm"])
    assert code == 2 and http.urls == []


def test_universe_file_with_locked_month_is_rejected(tmp_path):
    folder = tmp_path / "reports-x"
    folder.mkdir()
    (folder / "universe-by-month.csv").write_text("month,rank,symbol\n2026-06,1,BIGUSDT\n", encoding="utf-8")
    with pytest.raises(ValueError):
        dl.load_universe(tmp_path)


def test_disk_guard_blocks_download(tmp_path):
    store = build_store()
    out = stage1(tmp_path, store)
    code, http = run_with(store, ["stage2", "--report", str(report_file(tmp_path)), "--output", str(out), "--confirm"],
                          free=5 * 10 ** 9)  # 10 GB eşiğinin altı
    assert code == 2
    assert not any(url.endswith(".zip") for url in http.urls)


def test_inspect_zip_counts_gaps_and_duplicates_without_filling():
    base = start_ms("2024-07")
    rows = [base, base + 900_000, base + 3 * 900_000, base + 3 * 900_000]     # 1 boşluk, 1 yinelenen
    text = dl_header() + "".join(f"{t},1,1,1,1,1,0,1,1,1,1,0\n" for t in rows)
    info = dl.inspect_zip(make_zip("x.csv", text), "klines_15m")
    assert info["rows"] == 4 and info["duplicates"] == 1 and info["internal_missing"] == 1
    funding = dl.inspect_zip(series("X", "fundingRate", "2024-07", count=3), "fundingRate")
    assert funding["rows"] == 3 and funding["internal_missing"] == 0
    with pytest.raises(ValueError):
        dl.inspect_zip(make_zip("x.csv", "open_time,open\n"), "klines_15m")


def dl_header() -> str:
    return DAILY_HEADER


def test_no_credentials_and_standalone():
    source = Path(dl.__file__).read_text(encoding="utf-8")
    assert "from app" not in source and "import app" not in source
    assert "X-MBX-APIKEY" not in source and "signature" not in source.lower()
    assert "DATABASE_URL" not in source and "/etc/kaistrade.env" not in source
