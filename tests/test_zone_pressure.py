import copy
import unittest

from market.services.zone_pressure import (
    DEFAULT_CONFIG, PERIOD_DENSITY_DEFAULTS, _update_zone_states,
    advance, visit, momentum,
)


def bar(t, c, h=None, l=None):
    return dict(timestamp=t, open=c, close=c, high=h or c + .2, low=l or c - .2)


class ZonePressureTests(unittest.TestCase):
    def test_public_defaults_require_tighter_density(self):
        self.assertEqual(DEFAULT_CONFIG["zone_bin_atr"], 0.35)
        self.assertEqual(DEFAULT_CONFIG["zone_min_close_ratio"], 0.15)
        self.assertEqual(DEFAULT_CONFIG["zone_min_visits"], 4)
        self.assertEqual(DEFAULT_CONFIG["zone_min_consecutive_bars"], 30)
        self.assertEqual(DEFAULT_CONFIG["pressure_min_rejections"], 4)

    def test_period_defaults_use_different_continuous_density_requirements(self):
        self.assertEqual(PERIOD_DENSITY_DEFAULTS["M1"], 45)
        self.assertEqual(PERIOD_DENSITY_DEFAULTS["M5"], 30)
        self.assertEqual(PERIOD_DENSITY_DEFAULTS["M15"], 20)

    def test_continuous_density_can_qualify_below_window_ratio(self):
        rows = [bar(60 * i, 100.0) for i in range(30)]
        rows.extend(bar(60 * i, 110.0 + i) for i in range(30, 160))
        result = advance("X", "M5", rows, config={"zone_min_close_ratio": 0.30})
        dense = [z for z in result["zones"] if z.get("aggregation_mode") == "continuous"]
        self.assertTrue(dense)
        self.assertGreaterEqual(dense[0]["consecutive_count"], 30)

    def test_structure_engine_snapshot_exposes_zone_pressure_context(self):
        from market.services.market_structure_engine_v2 import analyze
        rows = [bar(60 * i, 100 + (i % 5) * 0.2) for i in range(80)]
        result = analyze("X", "M1", rows)
        self.assertIn("zone_pressure", result)
        self.assertIn("zones", result["zone_pressure"])

    def test_density_context_does_not_create_execution_setup(self):
        from market.services.signal.structure_plan_signal import StructurePlanBuilder
        rows = [bar(60 * i, 100.5) for i in range(20)]
        structure = {
            "atr": 1.0, "major_state": "down", "internal_state": "down",
            "external_state": "down", "structure_segment_id": "segment-a",
            "zone_pressure": {"zones": [{"zone_id": "z1", "lower": 99.0, "upper": 101.0}],
                "events": [{"event_id": "e1", "zone_id": "z1",
                    "type": "pressure_reversal_confirmed", "direction": "sell",
                    "level": 100.5, "confirmed_at": 60 * 19}]},
        }
        plans = StructurePlanBuilder({"min_real_risk_reward": 1.0}).build(
            "market-structure", "X", "M1", rows, structure,
        )
        self.assertTrue(plans)
        self.assertTrue(all(item.get("setup_type") not in {
            "pressure_reversal", "pressure_zone_breakout"
        } for item in plans))

    def test_visit_requires_independent_departure(self):
        zone = dict(lower=99, upper=101, atr=2, visits=[], current_visit=None)
        for row in [bar(1, 100), bar(2, 100), bar(3, 100)]:
            visit(zone, row, .5)
        self.assertEqual(len(zone["visits"]), 0)
        visit(zone, bar(4, 103), .5)
        self.assertEqual(len(zone["visits"]), 1)

    def test_stream_restart_equals_prefix_replay_and_no_mutation(self):
        rows = [bar(60 * i + 60, 100 + (i % 6) * .2) for i in range(80)]
        profile = {"zone_min_close_ratio": 0.05, "zone_min_visits": 2}
        full = advance("X", "M1", rows, config=profile)
        prefix = advance("X", "M1", rows[:65], config=profile)
        saved = copy.deepcopy(prefix)
        resumed = advance("X", "M1", rows, previous=prefix, config=profile)
        self.assertEqual(full, resumed)
        self.assertEqual(prefix, saved)

    def test_momentum_is_direction_symmetric(self):
        rows = [bar(i, c) for i, c in enumerate([100, 99, 98, 96])]
        down = momentum(rows, 2)
        up = momentum([dict(r, close=200-r["close"], high=200-r["low"], low=200-r["high"]) for r in rows], 2)
        self.assertEqual(down["direction"], "sell")
        self.assertEqual(up["direction"], "buy")
        self.assertEqual(down["efficiency"], up["efficiency"])

    def test_zone_lifecycle_states_are_deterministic(self):
        rows = [bar(1, 100.0), bar(2, 100.0), bar(3, 100.0)]
        zone = {"zone_id": "z1", "lower": 99.0, "upper": 101.0,
                "visits": [], "current_visit": None}
        _update_zone_states([zone], [], rows, 1.0, {"zone_leave_atr": .5})
        self.assertEqual(zone["status"], "candidate")


if __name__ == "__main__":
    unittest.main()
