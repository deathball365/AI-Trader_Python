import unittest

from market.services.tick_execution_core import TickExecutionCore, TickQuote


class PaperPendingSameQuoteFillTests(unittest.TestCase):
    def test_core_still_waits_on_identical_timestamp(self):
        quote = TickQuote.create(100.0, 100.2, 1_000)
        state = TickExecutionCore.pending_state(1_000, quote, timeout_seconds=180)
        self.assertEqual(state.status, "wait")

    def test_one_second_forward_quote_is_eligible(self):
        # Paper matching advances pending with now+1 so the decision quote can fill.
        quote = TickQuote.create(100.0, 100.2, 1_001)
        state = TickExecutionCore.pending_state(1_000, quote, timeout_seconds=180)
        self.assertEqual(state.status, "eligible")


if __name__ == "__main__":
    unittest.main()
