import unittest

from market.services.signal.structure_plan_signal import StructurePlanBuilder


def snapshot(segment_id, top, bottom, anchor_note=None):
    return {
        "structure_segment_id": segment_id,
        "major_state": "down",
        "current_state": "down",
        "current_pattern": "range",
        "range": {
            "pattern": "range",
            "top": top,
            "bottom": bottom,
        },
        "note": anchor_note,
    }


class StructurePlanOpportunityIdentityTests(unittest.TestCase):
    def setUp(self):
        self.builder = StructurePlanBuilder()

    def _plan(self, *, anchor, segment_id, top=75850, bottom=75350):
        return self.builder._plan(
            source_id="structure", symbol="BTCUSD#", period="M5",
            anchor=anchor, setup_type="range_breakout", direction="sell",
            entry_mode="breakout_retest", status="active",
            entry=75780, zone_lower=75770, zone_upper=75790,
            stop_loss=75830, take_profit=75355,
            reason="M5 range收盘确认向下突破，等待回踩结构边界",
            valid_from=anchor, expires_at=anchor + 18000,
            structure_snapshot=snapshot(segment_id, top, bottom, anchor),
        )

    def test_rolling_anchor_keeps_the_same_opportunity(self):
        first = self._plan(anchor=1_789_567_200, segment_id="seg-range-down")
        second = self._plan(anchor=1_789_567_500, segment_id="seg-range-down")

        self.assertEqual(first["opportunity_id"], second["opportunity_id"])
        self.assertEqual(first["plan_id"], second["plan_id"])
        self.assertEqual(first["structure_segment_id"], "seg-range-down")
        self.assertNotEqual(first["structure_anchor_time"], second["structure_anchor_time"])

    def test_new_segment_creates_a_new_opportunity(self):
        first = self._plan(anchor=1_789_567_200, segment_id="seg-range-down")
        second = self._plan(anchor=1_789_567_500, segment_id="seg-range-up")

        self.assertNotEqual(first["opportunity_id"], second["opportunity_id"])
        self.assertNotEqual(first["plan_id"], second["plan_id"])


if __name__ == "__main__":
    unittest.main()
