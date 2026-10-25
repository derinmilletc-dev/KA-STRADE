"""Testnet adaptörü testleri — AĞ YOK. Adresin yalnızca Testnet'e kilitli olduğu ve JSON eşlemesi doğrulanır."""
from __future__ import annotations

import hashlib
import hmac
import inspect
import unittest
import urllib.parse

from app.grid_engine import testnet_adapter as ta
from app.grid_engine.engine import RUNNING, GridEngine, GridLevelSpec, GridSpec, client_order_id
from app.grid_engine.fake_exchange import FakeExchange
from app.grid_engine.ports import (LONG, NEW, ExchangeRejected, ExchangeTimeout, ExchangeUnreachable)
from app.grid_engine.store import GridStore

SYM = "BTCUSDT"


class Recorder:
    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply or (lambda m, u, p, h: (200, {}))

    def __call__(self, method, url, params, headers):
        self.calls.append((method, url, dict(params), dict(headers)))
        return self.reply(method, url, params, headers)


class TestAddressLock(unittest.TestCase):
    def test_constant_is_testnet(self):
        self.assertEqual(ta.TESTNET_BASE_URL, "https://testnet.binancefuture.com")

    def test_no_url_parameter_exists(self):
        names = set(inspect.signature(ta.BinanceTestnetAdapter.__init__).parameters)
        self.assertFalse({n for n in names if "url" in n.lower() or "host" in n.lower() or "base" in n.lower()})
        with self.assertRaises(TypeError):
            ta.BinanceTestnetAdapter("k", "s", base_url="https://fapi.binance.com")  # type: ignore[call-arg]

    def test_environment_cannot_switch_to_live(self):
        env = {"BINANCE_BASE_URL": "https://fapi.binance.com", "BINANCE_API_URL": "https://fapi.binance.com",
               "FAPI_URL": "https://fapi.binance.com", "BINANCE_TESTNET_BASE_URL": "https://fapi.binance.com",
               ta.ENV_KEY: "k", ta.ENV_SECRET: "s"}
        rec = Recorder()
        adapter = ta.BinanceTestnetAdapter.from_environment(env, transport=rec)
        adapter._request("GET", "/fapi/v1/time", signed=False)
        self.assertEqual(urllib.parse.urlsplit(rec.calls[0][1]).hostname, ta.TESTNET_HOST)

    def test_live_key_variable_names_are_never_read(self):
        with self.assertRaises(ValueError):
            ta.BinanceTestnetAdapter.from_environment({"BINANCE_API_KEY": "live", "BINANCE_API_SECRET": "live"})

    def test_url_validator_rejects_everything_but_exact_testnet(self):
        bad = ["http://testnet.binancefuture.com/fapi", "https://fapi.binance.com/fapi/v1/order",
               "https://testnet.binancefuture.com.evil.com/x", "https://evil.com/testnet.binancefuture.com",
               "https://testnet.binancefuture.com@evil.com/x", "https://testnet.binancefuture.com:8443/x",
               "https://user:pw@testnet.binancefuture.com/x", "https://TESTNET.binancefuture.com.attacker.io/x", ""]
        for url in bad:
            with self.assertRaises(ta.TestnetOnlyError, msg=url):
                ta.assert_testnet_url(url)
        ta.assert_testnet_url("https://testnet.binancefuture.com/fapi/v1/order")
        ta.assert_testnet_url("https://testnet.binancefuture.com:443/fapi/v1/order")

    def test_every_request_goes_to_testnet_host_only(self):
        rec = Recorder(lambda m, u, p, h: (200, [] if "openOrders" in u or "positionRisk" in u else {"symbols": [], "dualSidePosition": False}))
        adapter = ta.BinanceTestnetAdapter("k", "s", transport=rec)
        adapter.open_orders(SYM); adapter.position(SYM); adapter.assert_one_way_mode()
        with self.assertRaises(ValueError):
            adapter.symbol_filters(SYM)
        self.assertTrue(rec.calls)
        for _, url, *_ in rec.calls:
            self.assertEqual(urllib.parse.urlsplit(url).hostname, ta.TESTNET_HOST)

    def test_redirect_response_not_followed(self):
        adapter = ta.BinanceTestnetAdapter("k", "s", transport=Recorder(lambda *a: (307, {})))
        with self.assertRaises(ta.TestnetOnlyError):
            adapter.open_orders(SYM)

    def test_default_transport_refuses_foreign_url_before_any_io(self):
        with self.assertRaises(ta.TestnetOnlyError):
            ta.default_transport("GET", "https://fapi.binance.com/fapi/v1/time", {}, {})


class TestSigningAndMapping(unittest.TestCase):
    def adapter(self, reply):
        self.rec = Recorder(reply)
        return ta.BinanceTestnetAdapter("KEY123", "SECRET456", transport=self.rec, clock=lambda: 1700000000.0)

    ORDER = {"clientOrderId": "kg-g1-L01E0", "symbol": SYM, "side": "BUY", "type": "LIMIT", "price": "98.0", "stopPrice": "0",
             "origQty": "0.5", "executedQty": "0.2", "status": "PARTIALLY_FILLED", "reduceOnly": False}

    def test_signature_and_secret_never_sent(self):
        a = self.adapter(lambda *x: (200, self.ORDER))
        a.place_order(client_id="kg-g1-L01E0", symbol=SYM, side="BUY", type="LIMIT", qty=0.5, price=98.0)
        method, url, params, headers = self.rec.calls[0]
        sig = params.pop("signature")
        expect = hmac.new(b"SECRET456", urllib.parse.urlencode(params).encode(), hashlib.sha256).hexdigest()
        self.assertEqual(sig, expect)
        self.assertEqual(headers, {"X-MBX-APIKEY": "KEY123"})
        self.assertNotIn("SECRET456", repr(a) + url + str(params))
        self.assertEqual(params["timestamp"], "1700000000000")

    def test_place_params(self):
        a = self.adapter(lambda *x: (200, self.ORDER))
        a.place_order(client_id="kg-g1-L01E0", symbol=SYM, side="BUY", type="LIMIT", qty=0.5, price=98.0)
        p = self.rec.calls[0][2]
        self.assertEqual((p["price"], p["quantity"], p["timeInForce"], p["newClientOrderId"]), ("98", "0.5", "GTC", "kg-g1-L01E0"))
        self.assertNotIn("reduceOnly", p)
        a.place_order(client_id="kg-g1-L00S0", symbol=SYM, side="SELL", type="STOP_MARKET", qty=0.2, stop_price=90.0, reduce_only=True)
        p = self.rec.calls[1][2]
        self.assertEqual((p["stopPrice"], p["reduceOnly"], p["workingType"]), ("90", "true", "MARK_PRICE"))
        self.assertNotIn("price", p)

    def test_order_mapping(self):
        a = self.adapter(lambda *x: (200, self.ORDER))
        info = a.place_order(client_id="x", symbol=SYM, side="BUY", type="LIMIT", qty=0.5, price=98.0)
        self.assertEqual((info.client_id, info.qty, info.filled, info.status), ("kg-g1-L01E0", 0.5, 0.2, "PARTIALLY_FILLED"))

    def test_error_mapping(self):
        for code, name in ((-2022, "reduce"), (-4116, "dup"), (-2021, "trigger")):
            a = self.adapter(lambda *x, c=code: (400, {"code": c, "msg": "m"}))
            with self.assertRaises(ExchangeRejected) as ctx:
                a.place_order(client_id="x", symbol=SYM, side="BUY", type="LIMIT", qty=1, price=1.0)
            self.assertEqual(ctx.exception.code, code, name)

    def test_unknown_outcomes_are_timeouts_not_rejections(self):
        for reply in (lambda *x: (503, {"code": -1007, "msg": "Timeout waiting for response from backend server. Send status unknown"}),
                      lambda *x: (200, {"code": -1007, "msg": "unknown"}), lambda *x: (502, None)):
            a = self.adapter(reply)
            with self.assertRaises(ExchangeTimeout):
                a.place_order(client_id="x", symbol=SYM, side="BUY", type="LIMIT", qty=1, price=1.0)

        def boom_timeout(*x): raise ta.TransportTimeout("t")
        def boom_connect(*x): raise ta.TransportConnectError("c")
        with self.assertRaises(ExchangeTimeout):
            self.adapter(boom_timeout).get_order(SYM, "x")
        with self.assertRaises(ExchangeUnreachable):
            self.adapter(boom_connect).get_order(SYM, "x")

    def test_get_order_not_found_is_none_and_cancel_unknown_maps(self):
        a = self.adapter(lambda *x: (400, {"code": -2013, "msg": "Order does not exist."}))
        self.assertIsNone(a.get_order(SYM, "x"))
        with self.assertRaises(ExchangeRejected) as ctx:
            a.cancel_order(SYM, "x")
        self.assertEqual(ctx.exception.code, -2011)

    def test_mark_price(self):
        a = self.adapter(lambda *x: (200, {"symbol": SYM, "price": "87000.10"}))
        self.assertEqual(a.mark_price(SYM), 87000.1)
        self.assertEqual(self.rec.calls[0][1], "https://testnet.binancefuture.com/fapi/v1/ticker/price")
        self.assertNotIn("signature", self.rec.calls[0][2])
        a = self.adapter(lambda *x: (200, {"symbol": SYM, "price": "0"}))
        with self.assertRaises(ValueError):
            a.mark_price(SYM)

    def test_position_one_way_only_and_filters(self):
        a = self.adapter(lambda *x: (200, [{"symbol": SYM, "positionAmt": "0.300", "positionSide": "BOTH"},
                                         {"symbol": "ETHUSDT", "positionAmt": "5", "positionSide": "BOTH"}]))
        self.assertEqual(a.position(SYM), 0.3)
        a = self.adapter(lambda *x: (200, {"symbols": [{"symbol": SYM, "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"}, {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "100"}]}]}))
        self.assertEqual(a.symbol_filters(SYM), {"tickSize": 0.1, "stepSize": 0.001, "minNotional": 100.0})
        a = self.adapter(lambda *x: (200, {"dualSidePosition": True}))
        with self.assertRaises(RuntimeError):
            a.assert_one_way_mode()


class FakeBinanceHttp:
    """Binance Futures REST biçiminde konuşan sahte sunucu (FakeExchange üzerinde). Eşlemeyi uçtan uca sınar."""
    def __init__(self, ex: FakeExchange):
        self.ex = ex

    @staticmethod
    def _json(o):
        return {"clientOrderId": o.client_id, "symbol": o.symbol, "side": o.side, "type": o.type, "price": str(o.price),
                "stopPrice": str(o.stop_price), "origQty": str(o.qty), "executedQty": str(o.filled), "status": o.status, "reduceOnly": o.reduce_only}

    def __call__(self, method, url, params, headers):
        path = urllib.parse.urlsplit(url).path
        try:
            if path == "/fapi/v1/order" and method == "POST":
                o = self.ex.place_order(client_id=params["newClientOrderId"], symbol=params["symbol"], side=params["side"], type=params["type"],
                                        qty=float(params["quantity"]), price=float(params.get("price", 0)), stop_price=float(params.get("stopPrice", 0)),
                                        reduce_only=params.get("reduceOnly") == "true")
                return 200, self._json(o)
            if path == "/fapi/v1/order" and method == "GET":
                o = self.ex.get_order(params["symbol"], params["origClientOrderId"])
                return (200, self._json(o)) if o else (400, {"code": -2013, "msg": "Order does not exist."})
            if path == "/fapi/v1/order" and method == "DELETE":
                return 200, self._json(self.ex.cancel_order(params["symbol"], params["origClientOrderId"]))
            if path == "/fapi/v1/openOrders":
                return 200, [self._json(o) for o in self.ex.open_orders(params["symbol"])]
            if path == "/fapi/v2/positionRisk":
                return 200, [{"symbol": params["symbol"], "positionAmt": str(self.ex.position(params["symbol"])), "positionSide": "BOTH"}]
        except ExchangeRejected as exc:
            return 400, {"code": exc.code, "msg": str(exc)}
        except ExchangeTimeout:
            return 503, {"code": -1007, "msg": "unknown"}
        except ExchangeUnreachable:
            raise ta.TransportConnectError("x")
        raise AssertionError(f"beklenmeyen istek {method} {path}")


class TestEngineThroughAdapter(unittest.TestCase):
    def setUp(self):
        self.ex = FakeExchange(); self.ex.prices[SYM] = 100.0
        self.adapter = ta.BinanceTestnetAdapter("k", "s", transport=FakeBinanceHttp(self.ex), clock=lambda: 1.0)
        levels = [GridLevelSpec(98.0, 99.0, 0.5), GridLevelSpec(96.0, 97.0, 0.5), GridLevelSpec(94.0, 95.0, 0.5)]
        self.eng = GridEngine(self.adapter, GridStore(), GridSpec("g1", SYM, LONG, levels, 90.0))

    def test_start_fill_protect_emergency_through_adapter(self):
        self.assertEqual(self.eng.start(), RUNNING)
        self.assertEqual(len(self.ex.open_orders(SYM)), 3)
        self.ex.partial_fill(client_order_id("g1", LONG, 1, "E"), 0.2)
        self.eng.tick()
        stops = [o for o in self.ex.open_orders(SYM) if o.type == "STOP_MARKET"]
        self.assertEqual((len(stops), round(stops[0].qty, 6)), (1, 0.2))
        self.assertEqual(self.eng.emergency_stop(), "EMERGENCY_CLOSED")
        self.assertAlmostEqual(self.ex.positions[SYM], 0.0)
        self.assertEqual(self.ex.open_orders(SYM), [])

    def test_lost_reply_through_adapter_single_order(self):
        self.ex.lose_response_for.add(client_order_id("g1", LONG, 1, "E"))
        self.eng.start()
        for _ in range(4):
            self.eng.tick()
        self.assertTrue(all(v == 1 for v in self.ex.placed_count.values()))


if __name__ == "__main__":
    unittest.main()
