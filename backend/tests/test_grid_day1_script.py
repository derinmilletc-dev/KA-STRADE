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
    def __init__(self, ex, *, dual=False, broken_info=False, percent_down=0.0):
        super().__init__(ex)
        self.dual, self.broken_info, self.percent_down = dual, broken_info, percent_down
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
                                                       {"filterType": "MIN_NOTIONAL", "notional": str(self.ex.min_notional)}] + ([{"filterType": "PERCENT_PRICE", "multiplierDown": str(self.percent_down)}] if self.percent_down else [])}
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
        http = Http(self.ex, **{k: v for k, v in kw.items() if k in ("dual", "broken_info", "percent_down")})
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

    def test_price_stays_inside_percent_price_filter(self):
        self.ex.percent_down = 0.95
        report, _ = self.go(percent_down=0.95)
        self.assertFalse(report.failed, [(r.name, r.detail) for r in report.results if r.status == day1.FAIL])
        placed = [o for o in self.ex.orders.values() if o.client_id.startswith("kg-d1")]
        self.assertEqual(len(placed), 1)
        self.assertGreaterEqual(placed[0].price, 100.0 * 0.95)
        self.assertLess(placed[0].price, 100.0)

    def test_unexpected_fill_is_reported_as_failure_and_position_change_too(self):
        done = []
        def fill_after_create(method, info):
            if method == "get_order" and not done:
                done.append(1)
                self.ex.set_price("BTCUSDT", 1.0)           # emir dolsun
        self.ex.hooks.append(fill_after_create)
        report, _ = self.go()
        self.assertTrue(report.failed)
        self.assertEqual(self.status(report, "Emir DOLDU"), [day1.FAIL])
        self.assertEqual(self.status(report, "Pozisyon değişmedi"), [day1.FAIL])

    def test_duplicate_id_open_and_after_cancel_reported_separately(self):
        self.ex.reuse_final_ids = True
        report, _ = self.go()
        self.assertEqual(self.status(report, "Kimlik tekrarı (emir açıkken)"), [day1.PASS])
        after = [r for r in report.results if r.name.startswith("Kimlik tekrarı (emir iptal edilmişken)")]
        self.assertEqual(len(after), 1)
        self.assertIn("YENİDEN", after[0].detail)
        self.assertEqual(self.ex.open_orders("BTCUSDT"), [])            # yeniden açılan da temizlendi
        self.assertFalse(report.failed)

    def test_final_state_read_from_exchange(self):
        report, http = self.go()
        names = [r.name for r in report.results]
        self.assertIn("Pozisyon değişmedi (borsadan)", names)
        self.assertIn("Temizlik (borsada test emri kalmadı)", names)

    def test_acceptance_table_never_counts_skips_as_pass(self):
        report, _ = self.go()
        js, md = report.write(self.dir)
        text = md.read_text()
        self.assertIn("## Kabul kriterleri", text)
        self.assertRegex(text, r"\| Yanıt kaybı \| ATLANDI \|")
        self.assertIn("ELLE doğrulanmalı", text)
        self.assertNotIn("**Genel: GEÇTİ**", text)           # atlananlar varken sade 'GEÇTİ' yazılmaz

    def test_script_runs_as_main_and_registers_module(self):
        import subprocess, sys
        env = {k: v for k, v in os.environ.items() if not k.startswith("BINANCE")}
        out = subprocess.run([sys.executable, "-B", str(Path(day1.__file__)), "--report-dir", str(self.dir)], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertIn("BINANCE_TESTNET_API_KEY", out.stderr)


class TestTestnetOnlyStatic(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def sources(self):
        files = list((self.ROOT / "app" / "grid_engine").glob("*.py")) + [self.ROOT / "scripts" / "grid_testnet_day1.py"]
        return {f.name: f.read_text() for f in files}

    def test_no_live_hosts_anywhere_in_grid_code(self):
        import re
        for name, text in self.sources().items():
            for pattern in (r"binance\.com", r"api\.binance", r"fapi\.binance", r"dapi\.", r"vision\.binance"):
                self.assertIsNone(re.search(pattern, text), f"{name}: canlı adres izi {pattern}")

    def test_script_has_no_direct_network_imports(self):
        text = self.sources()["grid_testnet_day1.py"]
        for word in ("import urllib", "import http", "import requests", "import httpx", "import socket", "from urllib", "from http"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
