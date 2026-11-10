import unittest


class OccupancyDetailTests(unittest.TestCase):
    def test_detail_names_what_blocks_the_symbol(self):
        from app import v25_execution as live
        snap = {"positions": [], "open_orders": [], "open_algo_orders": [{"symbol": "BTCUSDT", "type": "STOP_MARKET"}, {"symbol": "BTCUSDT", "type": "TAKE_PROFIT_MARKET"}],
                "open_algo_orders_available": True}
        verified, occupied, detail = live.symbol_occupancy_status(snap, "BTCUSDT")
        self.assertTrue(verified and occupied)
        self.assertIn("2 koşullu emir", detail)
        self.assertIn("STOP_MARKET", detail)

    def test_clear_symbol_is_not_occupied(self):
        from app import v25_execution as live
        snap = {"positions": [], "open_orders": [], "open_algo_orders": [], "open_algo_orders_available": True}
        self.assertEqual(live.symbol_occupancy_status(snap, "BTCUSDT")[:2], (True, False))


if __name__ == "__main__":
    unittest.main()
