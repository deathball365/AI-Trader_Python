import unittest

from paper_trading import paper_account_cash, paper_required_margin


class PaperRequiredMarginTests(unittest.TestCase):
    def test_usdjpy_does_not_multiply_by_jpy_price(self):
        margin = paper_required_margin("USDJPY#", 158.29, 0.04, 100000.0, 100.0)
        self.assertAlmostEqual(margin, 40.0, places=6)

    def test_audusd_still_uses_quote_price(self):
        margin = paper_required_margin("AUDUSD#", 0.70, 0.04, 100000.0, 100.0)
        self.assertAlmostEqual(margin, 28.0, places=6)

    def test_gold_and_index_keep_price_times_contract(self):
        gold = paper_required_margin("GOLD#", 4283.0, 0.01, 100.0, 100.0)
        index = paper_required_margin("US500Cash#", 7700.0, 0.3, 1.0, 100.0)
        self.assertAlmostEqual(gold, 42.83, places=6)
        self.assertAlmostEqual(index, 23.1, places=6)

    def test_usdjpy_fits_2300_paper_account(self):
        margin = paper_required_margin("USDJPY#", 158.29, 0.04, 100000.0, 100.0)
        self.assertLess(margin, 2300.0)


class PaperAccountCashTests(unittest.TestCase):
    def test_usdjpy_stop_is_dollars_not_yen(self):
        cash = paper_account_cash(
            "USDJPY#", 158.165 - 158.173, 0.04, 100000.0, 158.173,
        )
        self.assertAlmostEqual(cash, -32.0 / 158.173, places=6)
        self.assertGreater(cash, -1.0)
        self.assertLess(cash, 0.0)

    def test_gold_one_dollar_move_is_one_dollar(self):
        cash = paper_account_cash("GOLD#", 1.00, 0.01, 100.0, 4300.0)
        self.assertAlmostEqual(cash, 1.00, places=6)

    def test_audusd_keeps_quote_times_contract(self):
        cash = paper_account_cash("AUDUSD#", 0.00100, 0.04, 100000.0, 0.71)
        self.assertAlmostEqual(cash, 4.00, places=6)
