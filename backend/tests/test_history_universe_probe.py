"""Küçük ölçüm aracı: yalnız 1d mum indirir, kilitli ayı hiç istemez, SHA-256 doğrular, evren ve funding etkisini hesaplar."""
from __future__ import annotations

import hashlib
import io
import json
import sys
import urllib.parse
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import history_discovery as hd  # noqa: E402
import history_universe_probe as probe  # noqa: E402

HEADER = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"


def daily_zip(symbol: str, month: str, days: int, quote_volume: float) -> bytes:
    year, number = map(int, month.split("-"))
    rows = [HEADER]
    for day in range(1, days + 1):
        from datetime import datetime, timezone
        stamp = int(datetime(year, number, day, tzinfo=timezone.utc).timestamp() * 1000)
        rows.append(f"{stamp},1,1,1,1,1,{stamp + 86399999},{quote_volume},1,1,1,0\n")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{symbol}-1d-{month}.csv", "".join(rows))
    return buffer.getvalue()


def put(store: dict[str, bytes], symbol: str, month: str, days: int, volume: float, *, bad_checksum: bool = False) -> None:
    key = f"{hd.MONTHLY}klines/{symbol}/1d/{symbol}-1d-{month}.zip"
    data = daily_zip(symbol, month, days, volume)
    digest = hashlib.sha256(data).hexdigest()
    store[key] = data
    store[key + ".CHECKSUM"] = (("0" * 64 if bad_checksum else digest) + f"  {symbol}-1d-{month}.zip\n").encode()


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


def standard_store() -> dict[str, bytes]:
    store: dict[str, bytes] = {}
    for month in ("2024-05", "2024-06", "2024-07", "2024-08"):
        put(store, "BIGUSDT", month, 28, 9_000_000)
        put(store, "MIDUSDT", month, 28, 5_000_000)
        put(store, "SMALLUSDT", month, 28, 100_000)
    put(store, "NEWUSDT", "2024-07", 28, 99_000_000)   # önceki ayı (2024-06) yok → 2024-07 evrenine giremez
    put(store, "NEWUSDT", "2024-08", 28, 99_000_000)
    put(store, "BIGUSDT", "2026-06", 28, 9_000_000)    # kilitli ay arşivde var: ne listelenmeli ne istenmeli
    put(store, "BIGUSDT", "2026-07", 28, 9_000_000)
    return store


def make_report() -> dict:
    return {"version": "history-discovery-v1", "candidates": ["BIGUSDT", "MIDUSDT", "NEWUSDT", "SMALLUSDT"],
            "coins": {
                "BIGUSDT": {"testable_months": ["2024-07", "2024-08"], "missing_funding_months": [], "delisted": False},
                "MIDUSDT": {"testable_months": ["2024-07"], "missing_funding_months": ["2024-08"], "delisted": True,
                            "first_month": "2024-05", "last_month": "2024-08"},
                "NEWUSDT": {"testable_months": [], "missing_funding_months": ["2024-07", "2024-08"], "delisted": False,
                            "first_month": "2024-07", "last_month": "2024-08"},
                "SMALLUSDT": {"testable_months": [], "missing_funding_months": [], "delisted": False}}}


def fetch_plan(store: dict[str, bytes], tmp_path: Path):
    http = FakeHttp(store)
    archive = hd.Archive(http, base=hd.LISTING_BASES[0])
    plan = probe.plan_files(archive, ["BIGUSDT", "MIDUSDT", "NEWUSDT", "SMALLUSDT"])
    return http, archive, plan


def test_plan_has_only_1d_and_no_locked_months(tmp_path):
    http, _, plan = fetch_plan(standard_store(), tmp_path)
    assert plan and all(item["key"].count("/1d/") == 1 for item in plan)
    assert all(probe.allowed_month(item["month"]) for item in plan)
    assert not any(item["month"] >= hd.HOLDOUT_START for item in plan)
    probe.download_all(http, hd.LISTING_BASES[0], plan, tmp_path / "out")
    for url in http.urls:
        assert "2026-06" not in url and "2026-07" not in url, url
        assert "fundingRate" not in url and "15m" not in url


def test_locked_month_download_is_refused_even_if_planned(tmp_path):
    http = FakeHttp(standard_store())
    item = {"symbol": "BIGUSDT", "month": "2026-06", "size": 1,
            "key": f"{hd.MONTHLY}klines/BIGUSDT/1d/BIGUSDT-1d-2026-06.zip"}
    with pytest.raises(ValueError):
        probe.fetch_one(http, hd.LISTING_BASES[0], item, tmp_path)
    assert http.urls == []


def test_checksum_verified_and_bad_file_not_saved(tmp_path):
    store = standard_store()
    put(store, "BADUSDT", "2024-07", 28, 1.0, bad_checksum=True)
    http = FakeHttp(store)
    item = {"symbol": "BADUSDT", "month": "2024-07", "size": 1, "key": f"{hd.MONTHLY}klines/BADUSDT/1d/BADUSDT-1d-2024-07.zip"}
    log = probe.fetch_one(http, hd.LISTING_BASES[0], item, tmp_path)
    assert "UYUŞMADI" in log["status"]
    assert not list(tmp_path.rglob("*.zip"))
    good = {"symbol": "BIGUSDT", "month": "2024-07", "size": 1, "key": f"{hd.MONTHLY}klines/BIGUSDT/1d/BIGUSDT-1d-2024-07.zip"}
    log = probe.fetch_one(http, hd.LISTING_BASES[0], good, tmp_path)
    assert log["status"].startswith("indirildi") and len(log["sha256"]) == 64


def test_resume_reuses_verified_files_and_never_overwrites(tmp_path):
    http, _, plan = fetch_plan(standard_store(), tmp_path)
    first = probe.download_all(http, hd.LISTING_BASES[0], plan, tmp_path)
    assert all(log["status"].startswith("indirildi") for log in first)
    before = len(http.urls)
    second = probe.download_all(http, hd.LISTING_BASES[0], plan, tmp_path)
    assert all(log["status"].startswith("mevcut") for log in second)
    assert len(http.urls) == before  # ağa gitmedi
    target = next(tmp_path.rglob("BIGUSDT-1d-2024-07.zip"))
    target.write_bytes(b"bozuk")
    again = probe.fetch_one(http, hd.LISTING_BASES[0], next(i for i in plan if i["symbol"] == "BIGUSDT" and i["month"] == "2024-07"), tmp_path)
    assert "bozuk" in again["status"] and target.read_bytes() == b"bozuk"  # üzerine yazılmadı


def stats_from(store: dict[str, bytes]):
    stats: dict = {}
    for key, data in store.items():
        if key.endswith(".zip") and not hd.locked(hd.MONTH_RE.search(key).group(1)):
            symbol, month = key.split("/")[-3], hd.MONTH_RE.search(key).group(1)
            stats.setdefault(symbol, {})[month] = probe.month_stats(probe.parse_daily_zip(data), month)
    return stats


def test_universe_uses_previous_month_volume_and_requires_history():
    universe = probe.build_universe(stats_from(standard_store()), size=2)
    july = [s for s, _ in universe["2024-07"]]
    assert july == ["BIGUSDT", "MIDUSDT"]          # NEW'in önceki ayı yok; SMALL boyuta sığmadı
    assert [s for s, _ in universe["2024-08"]] == ["NEWUSDT", "BIGUSDT"]  # NEW'in önceki ayı artık var, hacmi en yüksek
    # önceki ayda 15 günden az veri → sıralamaya girmez
    store = standard_store()
    put(store, "FEWUSDT", "2024-06", 10, 999_000_000)
    put(store, "FEWUSDT", "2024-07", 28, 999_000_000)
    assert "FEWUSDT" not in [s for s, _ in probe.build_universe(stats_from(store))["2024-07"]]


def test_impact_counts_funding_and_other_exclusions():
    universe = probe.build_universe(stats_from(standard_store()), size=100)
    months_rows, detail = probe.impact(universe, make_report()["coins"])
    august = next(r for r in months_rows if r["month"] == "2024-08")
    assert august["funding_excluded"] >= 1 and august["testable"] >= 1
    assert august["delisted_in_universe"] >= 1
    mid = next(r for r in detail if r["month"] == "2024-08" and r["symbol"] == "MIDUSDT")
    assert mid["testable"] == "hayır" and mid["reason"] == "funding yok"
    small = next(r for r in detail if r["month"] == "2024-07" and r["symbol"] == "SMALLUSDT")
    assert small["reason"] == "başka veri eksik"


def test_funding_gap_check_classifies_live_and_dull_candles():
    store = standard_store()
    for month in ("2024-07", "2024-08"):
        put(store, "DULLUSDT", month, 28, 0.0)
    report = make_report()
    report["coins"]["DULLUSDT"] = {"testable_months": [], "missing_funding_months": ["2024-07", "2024-08"], "delisted": True,
                                   "first_month": "2024-07", "last_month": "2024-08"}
    stats = stats_from(store)
    universe = probe.build_universe(stats)
    _, detail = probe.impact(universe, report["coins"])
    rows = {r["symbol"]: r for r in probe.funding_gap_check(report["coins"], stats, detail)}
    assert "sönük" in rows["DULLUSDT"]["verdict"]
    assert "canlı" in rows["MIDUSDT"]["verdict"]
    assert rows["MIDUSDT"]["continuous_until_end"] == "evet"
    assert "BIGUSDT" not in rows  # funding eksiği yok


def test_check_output_resume_rules(tmp_path):
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "a.txt").write_text("x")
    with pytest.raises(FileExistsError):
        probe.check_output(busy, resume=True)       # bu aracın klasörü değil
    (busy / probe.MARKER).write_text("{}")
    with pytest.raises(FileExistsError):
        probe.check_output(busy, resume=False)
    assert probe.check_output(busy, resume=True) == busy.resolve()
    for forbidden in ("/opt/kaistrade/x", "/var/lib/kaistrade/r", "/etc/kaistrade"):
        with pytest.raises(ValueError):
            probe.check_output(Path(forbidden), resume=True)


def test_end_to_end_main_writes_reports_without_locked_months(tmp_path):
    report_path = tmp_path / "discovery-report.json"
    report_path.write_text(json.dumps(make_report()), encoding="utf-8")
    fake = FakeHttp(standard_store())
    original = probe.PoliteHttp
    probe.PoliteHttp = lambda rate=0: fake  # type: ignore[assignment]
    try:
        code = probe.main(["--report", str(report_path), "--output", str(tmp_path / "out")])
        plan_code = probe.main(["--report", str(report_path), "--plan-only"])
    finally:
        probe.PoliteHttp = original  # type: ignore[assignment]
    assert code == 0 and plan_code == 0
    reports = next((tmp_path / "out").glob("reports-*"))
    names = {p.name for p in reports.iterdir()}
    assert {"summary.md", "universe-by-month.csv", "universe-impact.csv", "funding-gap-check.csv", "download-log.csv"} <= names
    for path in reports.glob("*.csv"):
        text = path.read_text(encoding="utf-8")
        assert "2026-06" not in text and "2026-07" not in text
    summary_lines = [l for l in (reports / "summary.md").read_text(encoding="utf-8").splitlines() if "2026-06" in l]
    assert len(summary_lines) == 1 and "kilitli" in summary_lines[0].lower()  # yalnız kural cümlesi; veri yok
    assert "Funding yüzünden dışarıda" in (reports / "summary.md").read_text(encoding="utf-8")
    assert (tmp_path / "out" / probe.MARKER).is_file()


def test_standalone_and_no_credentials():
    source = Path(probe.__file__).read_text(encoding="utf-8")
    assert "from app" not in source and "import app" not in source
    assert "X-MBX-APIKEY" not in source and "signature" not in source.lower()
    assert "DATABASE_URL" not in source and "/etc/kaistrade.env" not in source
