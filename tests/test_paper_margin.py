import unittest

from paper_trading import paper_required_margin


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
