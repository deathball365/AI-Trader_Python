import unittest

from paper_trading import market_spec


class MarketSpecTests(unittest.TestCase):
    def test_silver_is_not_treated_as_forex(self):
        self.assertEqual(market_spec("SILVER#"), (0.001, 5000.0))
        self.assertEqual(market_spec("XAGUSD"), (0.001, 5000.0))

    def test_broker_contract_size_overrides_fallback(self):
        point, contract = market_spec(
            "SILVER#",
            {"point_size": 0.001, "contract_size": 5000},
        )
        self.assertEqual((point, contract), (0.001, 5000.0))

    def test_zero_broker_contract_falls_back(self):
        self.assertEqual(
            market_spec("SILVER#", {"contract_size": 0, "point_size": 0}),
            (0.001, 5000.0),
        )

    def test_gold_and_btc_keep_existing_multipliers(self):
        self.assertEqual(market_spec("GOLD#"), (0.01, 100.0))
        self.assertEqual(market_spec("BTCUSD#"), (0.01, 1.0))

    def test_true_fx_pair_still_uses_standard_lot(self):
        self.assertEqual(market_spec("EURUSD"), (0.00001, 100000.0))
        self.assertEqual(market_spec("USDJPY"), (0.001, 100000.0))

    def test_oil_and_index_fallbacks(self):
        self.assertEqual(market_spec("OILCash#"), (0.01, 100.0))
        self.assertEqual(market_spec("US100Cash#"), (0.01, 1.0))

    def test_silver_pnl_matches_contract_size(self):
        _, contract = market_spec("SILVER#")
        pnl = (65.660 - 65.772) * 1 * 0.01 * contract
        self.assertAlmostEqual(pnl, -5.6, places=6)


if __name__ == "__main__":
    unittest.main()


class _Rows:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _Storage:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def fetchone(self, sql, params=()):
        self.calls.append((sql, params))
        return self.rows.pop(0) if self.rows else None


class MarketSpecLookupTests(unittest.TestCase):
    def test_paper_account_reuses_live_broker_contract_size(self):
        storage = _Storage([
            None,
            {
                "account_id": 21,
                "symbol": "SILVER#",
                "min_volume": 0.01,
                "volume_step": 0.01,
                "max_volume": 50,
                "volume_digits": 2,
                "contract_size": 5000,
                "price_digits": 3,
                "tick_size": 0.001,
                "point_size": 0.001,
                "source": "mt5",
                "updated_at": 1,
            },
        ])
        point, contract = market_spec("SILVER#", account_id=22, storage=storage)
        self.assertEqual((point, contract), (0.001, 5000.0))
        self.assertEqual(len(storage.calls), 2)


    def test_tick_value_overrides_contract_size(self):
        point, contract = market_spec(
            "SILVER#",
            {"tick_size": 0.001, "tick_value": 5.0, "contract_size": 1},
        )
        self.assertEqual(point, 0.001)
        self.assertAlmostEqual(contract, 5000.0)
