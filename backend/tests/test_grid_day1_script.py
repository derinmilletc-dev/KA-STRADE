"""Gün 1 betiği testleri — AĞ YOK (Binance biçimli sahte sunucu üzerinde)."""
from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from app.grid_engine import testnet_adapter as ta
from app.grid_engine.fake_exchange import FakeExchange
from tests.test_grid_testnet_adapter import FakeBinanceHttp

SPEC = importlib.util.spec_from_file_location("day1", Path(__file__).resolve().parents[1] / "scripts" / "grid_testnet_day1.py")
day1 = importlib.util.module_from_spec(SPEC)
import sys as _sys
_sys.modules["day1"] = day1
SPEC.loader.exec_module(day1)  # type: ignore[union-attr]


class Http(FakeBinanceHttp):
    def __init__(self, ex, *, dual=False, broken_info=False):
        super().__init__(ex)
        self.dual, self.broken_info = dual, broken_info
        self.paths: list[str] = []

    def __call__(self, method, url, params, headers):
        path = urllib.parse.urlsplit(url).path
        self.paths.append(f"{method} {path}")
        if path == "/fapi/v1/positionSide/dual":
            return 200, {"dualSidePosition": self.dual}
        if path == "/fapi/v1/exchangeInfo":
            if self.broken_info:
                return 503, None
            sym = lambda n: {"symbol": n, "filters": [{"filterType": "PRICE_FILTER", "tickSize": str(self.ex.tick)},
                                                       {"filterType": "LOT_SIZE", "stepSize": str(self.ex.step)},
                                                       {"filterType": "MIN_NOTIONAL", "notional": str(self.ex.min_notional)}]}
            return 200, {"symbols": [sym("BTCUSDT"), sym("ETHUSDT")]}
        if path == "/fapi/v1/ticker/price":
            return 200, {"symbol": params["symbol"], "price": str(self.ex.prices[params["symbol"]])}
        return super().__call__(method, url, params, headers)


class TestDay1(unittest.TestCase):
    def setUp(self):
        self.ex = FakeExchange()
        self.ex.prices.update({"BTCUSDT": 100.0, "ETHUSDT": 50.0})
        self.dir = Path(tempfile.mkdtemp())

    def go(self, **kw):
        http = Http(self.ex, **{k: v for k, v in kw.items() if k in ("dual", "broken_info")})
        adapter = ta.BinanceTestnetAdapter("KEYKEYKEY1", "SECRETSECRET2", transport=http, clock=lambda: 1.0)
        report = day1.Report(secrets=("KEYKEYKEY1", "SECRETSECRET2"))
        day1.run(adapter, report)
        return report, http

    def status(self, report, name):
        return [r.status for r in report.results if r.name.startswith(name)]

    def test_happy_path_passes_and_cleans_up(self):
        report, http = self.go()
        self.assertFalse(report.failed, [(r.name, r.status, r.detail) for r in report.results if r.status == day1.FAIL])
        self.assertEqual(self.ex.open_orders("BTCUSDT"), [])
        self.assertEqual(self.status(report, "Kimlik tekrarı (emir açıkken)"), [day1.PASS])
        self.assertEqual(self.status(report, "Emir iptali"), [day1.PASS])
        for host in {urllib.parse.urlsplit(u).hostname for u in []}:
            pass

    def test_first_api_check_failure_means_no_orders(self):
        report, http = self.go(broken_info=True)
        self.assertTrue(report.failed)
        self.assertFalse([p for p in http.paths if p.startswith("POST")], "emir gitmemeli")
        self.assertEqual(self.status(report, "Emir testleri"), [day1.SKIP])

    def test_hedge_mode_fails_and_blocks_orders(self):
        report, http = self.go(dual=True)
        self.assertTrue(report.failed)
        self.assertFalse([p for p in http.paths if p.startswith("POST")])

    def test_exchange_accepting_duplicate_is_reported_as_failure_not_hidden(self):
        self.ex.dup_never_rejected = True
        report, _ = self.go()
        self.assertEqual(self.status(report, "Kimlik tekrarı (emir açıkken)"), [day1.FAIL])
        self.assertTrue(report.failed)
        self.assertEqual(self.ex.open_orders("BTCUSDT"), [])       # yine de temizlendi

    def test_lost_reply_never_resends(self):
        self.ex.lose_response_always = True
        report, _ = self.go()
        self.assertTrue(all(v <= 1 for v in self.ex.placed_count.values()), self.ex.placed_count)
        self.assertEqual(self.ex.open_orders("BTCUSDT"), [])

    def test_report_written_without_secrets(self):
        report, _ = self.go()
        js, md = report.write(self.dir)
        text = js.read_text() + md.read_text()
        self.assertNotIn("KEYKEYKEY1", text); self.assertNotIn("SECRETSECRET2", text)
        self.assertIn("BAŞARISIZ", md.read_text()) if report.failed else self.assertIn("GEÇTİ", md.read_text())

    def test_secret_redacted_even_if_in_error_text(self):
        r = day1.Report(secrets=("SECRETSECRET2",))
        out = r.add("x", day1.FAIL, "hata SECRETSECRET2 içeriyor")
        self.assertNotIn("SECRETSECRET2", out.detail)

    def test_stop_and_endpoint_checks_are_explicitly_skipped_not_faked(self):
        report, _ = self.go()
        self.assertEqual(self.status(report, "Stop verisi"), [day1.SKIP])
        self.assertEqual(self.status(report, "/api/v25/grid/filters"), [day1.SKIP])

    def test_main_requires_testnet_env_and_ignores_live_names(self):
        with mock.patch.dict(os.environ, {"BINANCE_API_KEY": "live", "BINANCE_API_SECRET": "live"}, clear=True):
            self.assertEqual(day1.main(["--report-dir", str(self.dir)]), 2)


if __name__ == "__main__":
    unittest.main()
