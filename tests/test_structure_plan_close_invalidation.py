import unittest

from market.services.signal.structure_plan.lifecycle import (
    close_invalidate_reason,
    opportunity_still_valid,
)


class StructurePlanCloseInvalidationTests(unittest.TestCase):
    def test_wick_through_protection_does_not_invalidate_on_close_helper(self):
        plan = {
            "direction": "buy",
            "setup_type": "range_lower_reversal",
            "status": "active",
            "invalidation_price": 98.5,
            "entry_price": 100.0,
            "entry_zone": {"lower": 99.5, "upper": 100.5},
            "close_invalidation_rules": ["range_structure_break", "protected_level_break"],
            "structure_segment_id": "seg-1",
            "structure_metadata": {"segment_id": "seg-1", "range_top": 105.0, "range_bottom": 98.5},
        }
        structure = {
            "structure_segment_id": "seg-1",
            "atr": 1.0,
            "range": {"status": "active", "top": 105.0, "bottom": 98.5},
        }
        # Close still above protection: valid even if an intraday wick traded lower.
        self.assertEqual(close_invalidate_reason(plan, structure, 99.0, atr=1.0), "")
        self.assertTrue(opportunity_still_valid(plan, structure, 99.0, atr=1.0))

    def test_close_beyond_protection_invalidates_range_plan(self):
        plan = {
            "direction": "buy",
            "setup_type": "range_lower_reversal",
            "status": "active",
            "invalidation_price": 98.5,
            "entry_price": 100.0,
            "entry_zone": {"lower": 99.5, "upper": 100.5},
            "close_invalidation_rules": ["range_structure_break", "protected_level_break"],
            "structure_segment_id": "seg-1",
            "structure_metadata": {"segment_id": "seg-1", "range_top": 105.0, "range_bottom": 98.5},
        }
        structure = {
            "structure_segment_id": "seg-1",
            "atr": 1.0,
            "range": {
                "status": "breakout_confirmed",
                "breakout_direction": "down",
                "top": 105.0,
                "bottom": 98.5,
            },
        }
        reason = close_invalidate_reason(plan, structure, 98.3, atr=1.0)
        self.assertTrue(reason)
        self.assertFalse(opportunity_still_valid(plan, structure, 98.3, atr=1.0))

    def test_segment_change_invalidates_orphaned_waiter(self):
        plan = {
            "direction": "buy",
            "setup_type": "range_breakout",
            "status": "active",
            "invalidation_price": 98.5,
            "entry_price": 101.0,
            "entry_zone": {"lower": 100.5, "upper": 101.5},
            "close_invalidation_rules": ["same_structure_new_plan", "range_structure_break"],
            "structure_segment_id": "seg-old",
            "structure_metadata": {"segment_id": "seg-old", "range_top": 105.0, "range_bottom": 98.5},
        }
        structure = {
            "structure_segment_id": "seg-new",
            "atr": 1.0,
            "range": {"status": "active", "top": 110.0, "bottom": 100.0},
        }
        self.assertEqual(
            close_invalidate_reason(plan, structure, 104.0, atr=1.0),
            "结构段已切换，原交易机会失效",
        )

    def test_location_plan_dies_when_hl_closes_broken(self):
        plan = {
            "direction": "buy",
            "setup_type": "structure_location_pullback",
            "status": "active",
            "entry_price": 100.0,
            "validation_evidence": {"location_entry_level": 100.0},
            "close_invalidation_rules": [],
        }
        structure = {"structure_segment_id": "seg-1", "atr": 1.0}
        self.assertEqual(
            close_invalidate_reason(plan, structure, 99.9, atr=1.0),
            "HL 被收盘跌破，原回撤计划失效",
        )
        # A wick is not represented by close_price and therefore remains valid.
        self.assertEqual(close_invalidate_reason(plan, structure, 100.0, atr=1.0), "")

    def test_location_sell_plan_dies_when_lh_closes_broken(self):
        plan = {
            "direction": "sell",
            "setup_type": "structure_location_pullback",
            "status": "active",
            "entry_price": 100.0,
            "validation_evidence": {"location_entry_level": 100.0},
            "close_invalidation_rules": [],
        }
        structure = {"structure_segment_id": "seg-1", "atr": 1.0}
        self.assertEqual(
            close_invalidate_reason(plan, structure, 100.1, atr=1.0),
            "LH 被收盘突破，原回撤计划失效",
        )


if __name__ == "__main__":
    unittest.main()
