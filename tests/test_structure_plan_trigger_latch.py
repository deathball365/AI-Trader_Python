import unittest

from market.services.signal.structure_plan_signal import StructurePlanSignalGenerator


class _Repo:
    def __init__(self):
        self.payloads = []
        self.invalidated = []

    def update_payload(self, plan_id, payload):
        self.payloads.append((plan_id, dict(payload)))

    def invalidate_plan(self, plan_id, reason):
        self.invalidated.append((plan_id, reason))


class StructurePlanTriggerLatchTests(unittest.TestCase):
    def setUp(self):
        self.repo = _Repo()
        self.gen = StructurePlanSignalGenerator.__new__(StructurePlanSignalGenerator)
        self.gen.repository = self.repo
        self.gen._tick_state = {}
        self.gen._param = lambda key, default=None: {
            "max_entry_distance_pct": 0.8,
        }.get(key, default)

    def test_range_touch_plan_keeps_triggering_after_boundary_marked(self):
        plan = {
            "plan_id": "gold-m5",
            "setup_type": "range_lower_reversal",
            "direction": "buy",
            "entry_mode": "touch_or_near",
            "entry_price": 4341.9,
            "entry_zone": {"lower": 4339.2, "upper": 4344.5},
            "boundary_state": "left_boundary",
        }
        self.assertTrue(self.gen._triggered(plan, 4341.9))
        self.assertEqual(plan["boundary_state"], "triggered")
        # A later tick in the same zone must still be eligible for claim.
        self.assertTrue(self.gen._triggered(plan, 4342.1))

    def test_stale_far_plan_is_invalidated_by_distance(self):
        plan = {
            "plan_id": "us100-m1",
            "setup_type": "range_lower_reversal",
            "direction": "buy",
            "entry_price": 29927.7,
            "entry_zone": {"lower": 29924.4, "upper": 29931.0},
            "validation_evidence": {},
        }
        reason = self.gen._event_invalidated(plan, 30143.28)
        self.assertIn("倍入场区宽度", reason)

    def test_stale_far_plan_with_zone_is_invalidated_by_zone_widths(self):
        plan = {
            "plan_id": "btc-m5",
            "setup_type": "range_breakout",
            "direction": "buy",
            "entry_price": 82144.46,
            "entry_zone": {"lower": 82017.56, "upper": 82271.37},
            "validation_evidence": {},
        }
        reason = self.gen._event_invalidated(plan, 85451.35)
        self.assertIn("倍入场区宽度", reason)

    def test_reclaimed_plan_still_triggers_just_outside_zone(self):
        plan = {
            "plan_id": "oil-m1",
            "setup_type": "range_false_breakout",
            "direction": "buy",
            "entry_mode": "touch_and_reclaim",
            "entry_price": 94.1417,
            "entry_zone": {"lower": 94.1068, "upper": 94.1767},
            "touch_seen": True,
            "touch_state": "reclaimed",
            "boundary_state": "triggered",
        }
        self.assertTrue(self.gen._triggered(plan, 94.22))
        self.assertEqual(plan["touch_state"], "reclaimed")

    def test_reclaimed_plan_resets_when_price_is_too_far(self):
        plan = {
            "plan_id": "oil-m1-far",
            "setup_type": "range_false_breakout",
            "direction": "buy",
            "entry_mode": "touch_and_reclaim",
            "entry_price": 94.1417,
            "entry_zone": {"lower": 94.1068, "upper": 94.1767},
            "touch_seen": True,
            "touch_state": "reclaimed",
            "boundary_state": "triggered",
        }
        self.assertFalse(self.gen._triggered(plan, 95.5))
        self.assertEqual(plan["touch_state"], "unvisited")


if __name__ == "__main__":
    unittest.main()
