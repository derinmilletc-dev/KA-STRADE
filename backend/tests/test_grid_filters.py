from __future__ import annotations

import unittest

from app.grid_engine.filters import GridFilterError, extract_symbol_filters


def info(**over):
    row = {"symbol": "BTCUSDT", "status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT", "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.10"}, {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
        {"filterType": "MIN_NOTIONAL", "notional": "100"}]}
    row.update(over)
    return {"symbols": [row]}


class TestFilters(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(extract_symbol_filters(info(), "btcusdt"),
                         {"symbol": "BTCUSDT", "tickSize": 0.1, "stepSize": 0.001, "minQty": 0.001, "minNotional": 100.0})

    def test_missing_or_bad_data_never_invents_defaults(self):
        bad = [({"symbols": []}, "BTCUSDT"), (None, "BTCUSDT"), ({}, "BTCUSDT"), (info(), "ETHUSDT"), (info(status="BREAK"), "BTCUSDT"),
               (info(contractType="CURRENT_QUARTER"), "BTCUSDT"), (info(quoteAsset="BUSD"), "BTCUSDT"),
               (info(filters=[{"filterType": "LOT_SIZE", "stepSize": "0.001"}, {"filterType": "MIN_NOTIONAL", "notional": "5"}]), "BTCUSDT"),
               (info(filters=[{"filterType": "PRICE_FILTER", "tickSize": "0.1"}, {"filterType": "LOT_SIZE", "stepSize": "0.001"}]), "BTCUSDT"),
               (info(filters=[{"filterType": "PRICE_FILTER", "tickSize": "0"}, {"filterType": "LOT_SIZE", "stepSize": "0.001"}, {"filterType": "MIN_NOTIONAL", "notional": "5"}]), "BTCUSDT"),
               (info(filters=[{"filterType": "PRICE_FILTER", "tickSize": "abc"}, {"filterType": "LOT_SIZE", "stepSize": "0.001"}, {"filterType": "MIN_NOTIONAL", "notional": "5"}]), "BTCUSDT")]
        for payload, symbol in bad:
            with self.assertRaises(GridFilterError, msg=str(payload)[:60]):
                extract_symbol_filters(payload, symbol)

    def test_notional_filter_alternative_name(self):
        data = info(filters=[{"filterType": "PRICE_FILTER", "tickSize": "0.1"}, {"filterType": "LOT_SIZE", "stepSize": "0.001"}, {"filterType": "NOTIONAL", "minNotional": "5"}])
        self.assertEqual(extract_symbol_filters(data, "BTCUSDT")["minNotional"], 5.0)


if __name__ == "__main__":
    unittest.main()
