"""Keşif aracı: yalnız liste okur, veri indirmez, kilitli dönemi raporlamaz, eksik funding'i doldurmaz."""
from __future__ import annotations

import csv
import json
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import history_discovery as hd  # noqa: E402

MONTHS = hd.months("2024-01", "2026-09")  # kilitli aylar da sahte arşivde var: rapora girmemeli


def keys_for(symbol: str, months: list[str], *, skip_funding: tuple[str, ...] = (), skip_mark: tuple[str, ...] = (),
             book_months: tuple[str, ...] = ()) -> dict[str, int]:
    store: dict[str, int] = {}
    for month in months:
        for kind, interval in hd.MONTHLY_KINDS:
            if kind == "fundingRate" and month in skip_funding:
                continue
            if kind == "markPriceKlines" and month in skip_mark:
                continue
            prefix = f"{hd.MONTHLY}{kind}/{symbol}/" + (f"{interval}/" if interval else "")
            name = f"{symbol}-{interval or kind}-{month}.zip"
            store[prefix + name] = 1000
            store[prefix + name + ".CHECKSUM"] = 90
    for month in book_months:
        for day in ("01", "02"):
            for kind in hd.ORDERBOOK_KINDS:
                key = f"{hd.DAILY}{kind}/{symbol}/{symbol}-{kind}-{month}-{day}.zip"
                store[key] = 500
    return store


class FakeHttp(hd.Http):
    def __init__(self, store: dict[str, int], page: int = 7):
        super().__init__(rate_per_second=1000)
        self.store, self.page, self.urls = store, page, []

    def get(self, url: str) -> bytes:
        self.urls.append(url)
        self.requests += 1
        parsed = urllib.parse.urlparse(url)
        assert parsed.hostname in hd.ALLOWED_HOSTS
        if url == hd.EXCHANGE_INFO:
            return b"{}"
        query = dict(urllib.parse.parse_qsl(parsed.query))
        prefix, delimiter, marker = query.get("prefix", ""), query.get("delimiter"), query.get("marker", "")
        names = sorted(k for k in self.store if k.startswith(prefix) and k > marker)
        contents, prefixes = [], []
        for key in names:
            rest = key[len(prefix):]
            if delimiter and delimiter in rest:
                common = prefix + rest.split(delimiter)[0] + delimiter
                if common not in prefixes and common > marker:
                    prefixes.append(common)
            else:
                contents.append(key)
        items = [("c", k) for k in contents] + [("p", p) for p in prefixes]
        items.sort(key=lambda item: item[1])
        chunk, truncated = items[: self.page], len(items) > self.page
        body = "".join(f"<Contents><Key>{k}</Key><Size>{self.store[k]}</Size></Contents>" if t == "c"
                       else f"<CommonPrefixes><Prefix>{k}</Prefix></CommonPrefixes>" for t, k in chunk)
        next_marker = f"<NextMarker>{chunk[-1][1]}</NextMarker>" if truncated and chunk else ""
        xml = (f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>{str(truncated).lower()}'
               f"</IsTruncated>{next_marker}{body}</ListBucketResult>")
        return xml.encode()


def run(store: dict[str, int], **kwargs):
    http = FakeHttp(store)
    report = hd.discover(hd.Archive(http, base=hd.LISTING_BASES[0]), exchange_info=kwargs.pop("info", None), **kwargs)
    return report, http


def full_store() -> dict[str, int]:
    store = {}
    store.update(keys_for("BTCUSDT", MONTHS, book_months=("2024-07",)))
    store.update(keys_for("DEADUSDT", hd.months("2024-01", "2025-03")))                         # delist edilmiş
    store.update(keys_for("GAPUSDT", MONTHS, skip_funding=("2025-02",), skip_mark=("2025-04",)))  # funding ve mark eksik
    store.update(keys_for("BTCUSDT_250328", hd.months("2025-01", "2025-03")))                     # vadeli kontrat
    store.update(keys_for("ETHUSDC", MONTHS))                                                       # USDT değil
    return store


def test_only_listing_requests_never_data_files():
    _, http = run(full_store())
    assert http.urls
    for url in http.urls:
        assert "prefix=" in url, url  # her istek bir liste sorgusu
        assert not urllib.parse.urlparse(url).path.endswith((".zip", ".CHECKSUM")), url


def test_locked_months_never_appear_anywhere():
    report, _ = run(full_store())
    text = json.dumps({key: report[key] for key in ("coins", "candidates", "excluded", "errors")})
    assert report["holdout"].startswith(hd.HOLDOUT_START)  # yalnız kural cümlesi; veri değil
    for month in hd.months("2026-06", "2026-09"):
        assert month not in text


def test_candidates_and_exclusions():
    report, _ = run(full_store())
    assert report["candidates"] == ["BTCUSDT", "DEADUSDT", "GAPUSDT"]
    excluded = {row["symbol"] for row in report["excluded"]}
    assert {"BTCUSDT_250328", "ETHUSDC"} <= excluded


def test_delist_and_partial_months_reported_not_counted_as_gaps():
    report, _ = run(full_store())
    dead = report["coins"]["DEADUSDT"]
    assert dead["delisted"] is True and dead["last_month"] == "2025-03"
    info_rows = [g for g in dead["gaps"] if g["action"] == "bilgi; eksik sayılmaz"]
    assert any("delist" in g["reason"] for g in info_rows)
    assert "2025-03" in dead["testable_months"]
    assert report["coins"]["BTCUSDT"]["delisted"] is False


def test_missing_funding_is_excluded_not_filled():
    report, _ = run(full_store())
    gap = report["coins"]["GAPUSDT"]
    assert gap["missing_funding_months"] == ["2025-02"]
    assert "2025-02" not in gap["testable_months"] and "2025-04" not in gap["testable_months"]
    funding_rows = [g for g in gap["gaps"] if g["kind"] == "fundingRate"]
    assert funding_rows and "tahminle doldurulmaz" in funding_rows[0]["action"]
    assert gap["funding_coverage"] < 1


def test_orderbook_availability_is_counted():
    report, _ = run(full_store())
    assert report["coins"]["BTCUSDT"]["orderbook_months"] == {"bookTicker": 1, "bookDepth": 1}
    assert report["coins"]["GAPUSDT"]["orderbook_months"] == {"bookTicker": 0, "bookDepth": 0}


def test_reports_written_and_scenarios_separate(tmp_path):
    report, _ = run(full_store())
    out = tmp_path / "rapor"
    files = {p.name for p in hd.write_reports(out, report)}
    assert {"discovery-report.json", "coin-availability.csv", "monthly-gaps.csv", "funding-coverage.csv",
            "cost-scenario-iyimser.csv", "cost-scenario-temel.csv", "cost-scenario-kotumser.csv", "summary.md"} <= files
    summary = (out / "summary.md").read_text(encoding="utf-8")
    for heading in ("Coin bazında bulunabilirlik", "Aylık eksik veri", "Funding kapsamı", "Depolama tahmini",
                    "Veri indirme riskleri", "İndirme öncesi son kontrol", "simülasyon"):
        assert heading in summary
    rows = {name: list(csv.DictReader((out / f"cost-scenario-{name}.csv").open(encoding="utf-8"))) for name in hd.SCENARIOS}
    first = {name: float(r[0]["spread_kayma_bp"]) for name, r in rows.items()}
    assert first["iyimser"] < first["temel"] < first["kotumser"]
    # likidite ve oynaklık maliyeti değiştirir
    temel = rows["temel"]
    assert float(temel[0]["spread_kayma_bp"]) < float(temel[-1]["spread_kayma_bp"])
    with pytest.raises(FileExistsError):
        (out / "summary.md").open("x")


def test_output_guards(tmp_path):
    for forbidden in ("/opt/kaistrade/x", "/var/lib/kaistrade/rapor", "/etc/kaistrade"):
        with pytest.raises(ValueError):
            hd.check_output(Path(forbidden))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(ValueError):
        hd.check_output(repo / "out")
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "a.txt").write_text("x")
    with pytest.raises(FileExistsError):
        hd.check_output(busy)
    assert hd.check_output(tmp_path / "new") == (tmp_path / "new").resolve()


def test_http_refuses_other_hosts_and_sends_no_credentials():
    http = hd.Http()
    with pytest.raises(ValueError):
        http.get("https://fapi.binance.com.evil.example/x")
    with pytest.raises(ValueError):
        http.get("http://data.binance.vision/?prefix=x")
    source = Path(hd.__file__).read_text(encoding="utf-8")
    assert "X-MBX-APIKEY" not in source and "signature" not in source.lower()
    assert "/etc/kaistrade.env" not in source and "DATABASE_URL" not in source


def test_tool_is_standalone():
    source = Path(hd.__file__).read_text(encoding="utf-8")
    assert "from app" not in source and "import app" not in source
