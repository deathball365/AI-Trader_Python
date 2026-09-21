import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from market.services.signal.structure_plan.lifecycle import (
    close_invalidate_reason,
    distance_invalidate_reason,
    opportunity_still_valid,
    max_entry_zone_widths,
)
from market.services.signal.structure_plan_signal import StructurePlanSignalGenerator


class StructurePlanLifecycleHardeningTests(unittest.TestCase):
    def test_default_max_zone_widths_is_tightened(self):
        self.assertEqual(max_entry_zone_widths({}), 3.5)

    def test_distance_invalidates_before_old_eight_widths(self):
        plan = {
            "direction": "buy",
            "entry_price": 100.0,
            "entry_zone": {"lower": 99.5, "upper": 100.5},
        }
        # 4 widths away used to survive under the old 8x rule.
        reason = distance_invalidate_reason(plan, 104.1)
        self.assertTrue(reason)
        self.assertFalse(opportunity_still_valid(plan, {"structure_segment_id": "s1"}, 104.1, atr=1.0))

    def test_outside_zone_streak_invalidates_directional_waiter(self):
        plan = {
            "direction": "buy",
            "period": "M5",
            "entry_price": 100.0,
            "entry_zone": {"lower": 99.5, "upper": 100.5},
            "outside_zone_closes": 2,
            "structure_segment_id": "s1",
            "close_invalidation_rules": [],
        }
        structure = {"structure_segment_id": "s1", "atr": 1.0}
        reason = close_invalidate_reason(plan, structure, 101.0, atr=1.0)
        self.assertIn("连续", reason)

    def test_same_opportunity_still_runs_closed_bar_checks(self):
        repo = MagicMock()
        repo.list_current.return_value = [{
            "plan_id": "plan-1",
            "status": "active",
            "direction": "buy",
            "opportunity_id": "opp-1",
            "entry_price": 100.0,
            "entry_zone": {"lower": 99.5, "upper": 100.5},
            "structure_segment_id": "s1",
            "close_invalidation_rules": ["protected_level_break"],
            "invalidation_price": 98.5,
            "period": "M5",
        }]
        gen = StructurePlanSignalGenerator(repository=repo, user_id=1, account_id=0)
        dead = gen._invalidate_stale_closed_plans(
            "EURUSD#",
            "M5",
            "market-structure",
            {"structure_segment_id": "s1", "atr": 1.0},
            close_price=98.0,
            atr=1.0,
            incoming_plans=[{
                "status": "active",
                "direction": "buy",
                "opportunity_id": "opp-1",
                "entry_price": 100.0,
            }],
        )
        self.assertIn("opp-1", dead)
        repo.invalidate_plan.assert_called()

    def test_touch_and_reclaim_persists_touch_state(self):
        repo = MagicMock()
        gen = StructurePlanSignalGenerator(repository=repo, user_id=1, account_id=0)
        plan = {
            "plan_id": "plan-touch",
            "setup_type": "range_lower_reversal",
            "entry_mode": "touch_and_reclaim",
            "direction": "buy",
            "entry_price": 100.0,
            "entry_zone": {"lower": 99.0, "upper": 101.0},
            "status": "active",
        }
        self.assertFalse(gen._triggered(plan, 99.5))
        self.assertTrue(plan.get("touch_seen"))
        repo.update_payload.assert_called()
        self.assertTrue(gen._triggered(plan, 100.2))
        self.assertEqual(plan.get("touch_state"), "reclaimed")


if __name__ == "__main__":
    unittest.main()
