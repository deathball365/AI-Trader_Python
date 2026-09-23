import time
import unittest
from types import SimpleNamespace

from market.services.same_setup_repeat_guard import (
    SameSetupRepeatGuard, should_block_repeat, progress_threshold,
)


class SameSetupRepeatGuardTests(unittest.TestCase):
    def test_tiny_price_gap_is_blocked_inside_the_window(self):
        now = int(time.time())
        previous = should_block_repeat(
            [{"setup_type": "key_level_reversal", "direction": "sell",
              "price": 4297.92, "time": now - 120}],
            setup_type="key_level_reversal", direction="sell",
            price=4297.04, now=now, atr=2.2,
        )
        self.assertIsNotNone(previous)
        self.assertLess(0.88, progress_threshold(4297.04, 2.2))

    def test_enough_price_progress_is_allowed(self):
        now = int(time.time())
        previous = should_block_repeat(
            [{"setup_type": "range_upper_reversal", "direction": "sell",
              "price": 4339.49, "time": now - 180}],
            setup_type="range_upper_reversal", direction="sell",
            price=4320.0, now=now, atr=2.2,
        )
        self.assertIsNone(previous)

    def test_different_setup_is_allowed(self):
        now = int(time.time())
        previous = should_block_repeat(
            [{"setup_type": "range_upper_reversal", "direction": "sell",
              "price": 157.976, "time": now - 60}],
            setup_type="range_false_breakout", direction="sell",
            price=157.964, now=now, atr=0.05,
        )
        self.assertIsNone(previous)

    def test_old_fill_outside_window_is_allowed(self):
        now = int(time.time())
        previous = should_block_repeat(
            [{"setup_type": "liquidity_sweep_reclaim", "direction": "buy",
              "price": 93.72, "time": now - 3600}],
            setup_type="liquidity_sweep_reclaim", direction="buy",
            price=93.69, now=now, atr=0.2,
        )
        self.assertIsNone(previous)

    def test_guard_reads_recent_live_reports(self):
        now = int(time.time())
        class _Storage:
            def fetchall(self, sql, params):
                return [{
                    "action": "s",
                    "executed_price": 4297.92,
                    "requested_price": 4297.92,
                    "reported_at": now - 115,
                    "position_attribution_json": (
                        '{"setup_type":"key_level_reversal","strategy_id":"1d5c16b8"}'
                    ),
                    "strategy_id": "1d5c16b8",
                }]

        signal = SimpleNamespace(
            setup_type="key_level_reversal", action="sell",
            suggested_entry=4297.04, trigger_price=4297.04, symbol="GOLD#",
        )
        strategy = SimpleNamespace(strategy_id="1d5c16b8")
        result = SameSetupRepeatGuard(_Storage()).check(
            user_id=1, account_id=21, strategy=strategy, signal=signal,
            execution_mode="live", action="sell", now=now,
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["scope"], "setup_repeat")


if __name__ == "__main__":
    unittest.main()
