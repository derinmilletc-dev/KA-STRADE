import asyncio
import unittest

from app import v25_execution as live


class FakeClient:
    """Binance'i taklit eder. limit_fills: limit emir kaç sorguda dolsun (None = hiç dolmasın)."""

    def __init__(self, position="2", limit_fills=None, limit_reject=False, partial=None):
        self.position = position
        self.limit_fills = limit_fills
        self.limit_reject = limit_reject
        self.partial = partial
        self.calls = []
        self.limit_order = None
        self.polls = 0

    async def public_get(self, path, params=None):
        return {"symbols": [{"symbol": "ABCUSDT", "status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT",
                              "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                                          {"filterType": "LOT_SIZE", "stepSize": "0.1", "minQty": "0.1", "maxQty": "1000"}]}]}

    async def signed(self, method, path, params=None):
        params = dict(params or {})
        self.calls.append((method, path, params))
        if path == "/fapi/v3/positionRisk":
            return [{"symbol": "ABCUSDT", "positionAmt": self.position}]
        if method == "POST" and path == "/fapi/v1/order":
            if params["type"] == "LIMIT":
                if self.limit_reject:
                    raise live.LiveExchangeError("fiyat aralığı", exchange_code=-4014)
                self.limit_order = {"orderId": 11, "clientOrderId": params["newClientOrderId"], "status": "NEW",
                                    "executedQty": "0", "avgPrice": "0", "price": params["price"], "origQty": params["quantity"]}
                return dict(self.limit_order)
            self.position = "0"
            return {"orderId": 22, "status": "FILLED", "executedQty": params["quantity"], "avgPrice": "9.50"}
        if method == "GET" and path == "/fapi/v1/order":
            if self.limit_order and params.get("origClientOrderId") == self.limit_order["clientOrderId"]:
                self.polls += 1
                if self.limit_fills is not None and self.polls >= self.limit_fills:
                    self.limit_order.update(status="FILLED", executedQty=self.limit_order["origQty"], avgPrice=self.limit_order["price"])
                    self.position = "0"
                elif self.partial and self.polls >= 2 and self.limit_order["status"] != "CANCELED":
                    self.limit_order.update(status="PARTIALLY_FILLED", executedQty=self.partial, avgPrice=self.limit_order["price"])
                    self.position = str(float(self.position) - float(self.partial)) if self.position == "2" else self.position
                return dict(self.limit_order)
            raise live.LiveExchangeError("yok", exchange_code=-2013)
        if method == "DELETE":
            self.limit_order["status"] = "CANCELED"
            return {}
        raise AssertionError((method, path))


def run(client, price=10.004):
    return asyncio.run(live.close_symbol_limit_then_market(client, "ABCUSDT", "t", price, wait_seconds=0.3, poll_seconds=0.02))


class ManualCloseLimitTests(unittest.TestCase):
    def test_limit_fills_in_time_no_market(self):
        client = FakeClient(limit_fills=2)
        result = run(client)
        self.assertEqual(result["close_mode"], "LIMIT")
        self.assertEqual(live.close_fill_price(result), 10.0)  # fiyat tick'e yuvarlandı
        posts = [c for c in client.calls if c[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][2]["reduceOnly"], "true")
        self.assertEqual(posts[0][2]["side"], "SELL")
        self.assertFalse(any(c[0] == "DELETE" for c in client.calls))

    def test_not_filled_cancels_then_market(self):
        client = FakeClient(limit_fills=None)
        result = run(client)
        self.assertEqual(result["close_mode"], "LIMIT_SONRA_PIYASA")
        self.assertTrue(any(c[0] == "DELETE" for c in client.calls))
        market = [c for c in client.calls if c[0] == "POST" and c[2]["type"] == "MARKET"]
        self.assertEqual(len(market), 1)
        self.assertEqual(market[0][2]["reduceOnly"], "true")
        self.assertEqual(client.position, "0")

    def test_limit_rejected_goes_market(self):
        client = FakeClient(limit_reject=True)
        result = run(client)
        self.assertEqual(result["close_mode"], "PIYASA")
        self.assertFalse(any(c[2].get("type") == "LIMIT" and c[0] == "DELETE" for c in client.calls))

    def test_no_position_returns_none(self):
        self.assertIsNone(run(FakeClient(position="0")))

    def test_short_position_buys_back(self):
        client = FakeClient(position="-2", limit_fills=1)
        run(client)
        post = next(c for c in client.calls if c[0] == "POST")
        self.assertEqual(post[2]["side"], "BUY")

    def test_without_limit_price_uses_plain_market(self):
        client = FakeClient()
        result = asyncio.run(live.close_manual_symbol(client, "ABCUSDT", "t", None))
        self.assertNotIn("close_mode", result)
        self.assertEqual([c[2]["type"] for c in client.calls if c[0] == "POST"], ["MARKET"])


if __name__ == "__main__":
    unittest.main()
