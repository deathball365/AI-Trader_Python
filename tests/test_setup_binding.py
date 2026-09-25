from market.services.signal.structure_plan.setup_binding import (
    binding_matches, layer_pattern, resolve_binding, setup_box,
)
from market.services.signal.structure_plan_signal import (
    STRUCTURE_PLAN_DEFAULT_CONFIG,
    StructurePlanBuilder,
)


def _structure(swing_pattern="trend", internal_pattern="range", swing_event="bos",
               internal_event="reclaim", internal_status="confirmed",
               swing_status=""):
    internal_detail = {
        "pattern": internal_pattern, "status": internal_status,
        "top": 100, "bottom": 90, "breakout_direction": "down",
        "active": True, "score": 80, "start_index": 0,
        "low_touches": 3, "high_touches": 3,
    } if internal_pattern in {"range", "triangle"} else {}
    swing_detail = {
        "pattern": swing_pattern, "status": swing_status or "confirmed",
        "top": 110, "bottom": 80, "breakout_direction": "up",
        "active": True, "score": 80, "start_index": 0,
        "low_touches": 3, "high_touches": 3,
    } if swing_pattern in {"range", "triangle"} else {}
    return {
        "atr": 1,
        "major_state": "up",
        "internal_state": "sideways" if internal_pattern == "range" else "up",
        "external_state": "up",
        "structure_hierarchy": {
            "swing": {
                "bias": "up", "pattern": swing_pattern,
                "event": {"type": swing_event},
                "pattern_detail": swing_detail,
            },
            "internal": {
                "bias": "sideways" if internal_pattern == "range" else "up",
                "pattern": internal_pattern,
                "event": {"type": internal_event},
                "pattern_detail": internal_detail,
            },
            "external": {"bias": "up", "pattern": "trend", "event": {"type": "bos"}},
        },
        "major_events": [{"type": swing_event, "direction": "up", "index": 8, "confirmed_at": 8}],
        "internal_events": [{"type": internal_event, "direction": "up", "index": 8, "confirmed_at": 8}],
        "external_events": [{"type": "bos", "direction": "up", "index": 8, "confirmed_at": 8}],
        "range": {},
    }


def test_default_location_pullback_uses_swing_direction_and_internal_entry():
    binding = resolve_binding("structure_location_pullback")
    assert binding["bind_pattern"] == "trend"
    assert binding["direction_layer"] == "swing"
    assert binding["entry_layer"] == "internal"


def test_location_pullback_does_not_match_internal_range():
    binding = resolve_binding("structure_location_pullback")
    structure = _structure(swing_pattern="trend", internal_pattern="range")
    assert layer_pattern(structure, "internal") == "range"
    assert binding_matches(structure, binding) is False
    structure = _structure(
        swing_pattern="trend", internal_pattern="trend", internal_event="retest",
    )
    assert binding_matches(structure, binding) is True


def test_false_breakout_matches_internal_range_when_swing_is_trend():
    binding = resolve_binding("range_false_breakout")
    structure = _structure(
        swing_pattern="trend", internal_pattern="range",
        internal_event="false_breakout", internal_status="failed_breakout",
    )
    assert binding["entry_layer"] == "internal"
    assert binding_matches(structure, binding) is True
    box, layer = setup_box(structure, binding)
    assert layer == "internal"
    assert box["status"] == "failed_breakout"


def test_range_breakout_does_not_match_internal_box_when_swing_is_trend():
    binding = resolve_binding("range_breakout")
    structure = _structure(swing_pattern="trend", internal_pattern="range", swing_event="bos")
    assert binding_matches(structure, binding) is False
    structure["structure_hierarchy"]["swing"]["pattern"] = "range"
    structure["structure_hierarchy"]["swing"]["pattern_detail"] = {
        "pattern": "range", "status": "breakout_confirmed",
        "top": 110, "bottom": 80, "breakout_direction": "up",
        "active": True, "score": 80, "start_index": 0,
    }
    structure["structure_hierarchy"]["swing"]["event"] = {"type": "breakout_confirmed"}
    assert binding_matches(structure, binding) is True


def test_setup_override_can_use_internal_as_direction_layer():
    binding = resolve_binding("range_false_breakout", {
        "direction_layer": "internal", "entry_layer": "internal",
        "bind_pattern": "range", "bind_event": "false_breakout",
    })
    structure = _structure(
        swing_pattern="trend", internal_pattern="range",
        internal_event="false_breakout", internal_status="failed_breakout",
    )
    assert binding["direction_layer"] == "internal"
    assert binding_matches(structure, binding) is True


def _rows():
    return [
        {"timestamp": 1_000 + i, "open": 95, "high": 96, "low": 94, "close": 95}
        for i in range(10)
    ]


def test_builder_uses_internal_false_breakout_when_swing_is_trend():
    structure = _structure(
        swing_pattern="trend", internal_pattern="range",
        internal_event="false_breakout", internal_status="failed_breakout",
    )
    builder = StructurePlanBuilder(STRUCTURE_PLAN_DEFAULT_CONFIG)
    snapshot = {
        "atr": 1, "major_state": "up", "internal_state": "sideways",
        "external_state": "up", "range": {},
        "structure_hierarchy": structure["structure_hierarchy"],
    }
    plans = builder._range_plans(
        "market-structure", "GOLD#", "M5", _rows(), structure, snapshot, 1009, 300,
    )
    assert plans
    assert plans[0]["setup_type"] == "range_false_breakout"
    assert plans[0]["direction"] == "buy"
    assert plans[0]["entry_layer"] == "internal"
    assert plans[0]["direction_layer"] == "swing"


def test_builder_skips_location_pullback_when_internal_is_range():
    structure = _structure(swing_pattern="trend", internal_pattern="range")
    builder = StructurePlanBuilder(STRUCTURE_PLAN_DEFAULT_CONFIG)
    snapshot = {
        "atr": 1, "major_state": "up", "internal_state": "sideways",
        "external_state": "up", "range": {},
        "structure_hierarchy": structure["structure_hierarchy"],
    }
    plans = builder._location_plans(
        "market-structure", "GOLD#", "M5", _rows(), structure, snapshot, 1009, 300,
    )
    assert plans == []


def test_watch_range_plan_does_not_block_location_pullback():
    builder = StructurePlanBuilder(STRUCTURE_PLAN_DEFAULT_CONFIG)
    selected = builder._select_structure_plans(
        [{
            "setup_type": "range_breakout_watch", "direction": "buy",
            "status": "watching", "entry_price": 0,
        }],
        [],
        [{
            "setup_type": "structure_location_pullback", "direction": "buy",
            "status": "active", "entry_price": 100,
        }],
    )
    assert [plan["setup_type"] for plan in selected] == ["structure_location_pullback"]


def test_active_event_and_location_plans_can_coexist():
    builder = StructurePlanBuilder(STRUCTURE_PLAN_DEFAULT_CONFIG)
    selected = builder._select_structure_plans(
        [],
        [{
            "setup_type": "choch_reversal", "direction": "sell",
            "status": "active", "entry_price": 110,
        }],
        [{
            "setup_type": "structure_location_pullback", "direction": "buy",
            "status": "active", "entry_price": 100,
        }],
    )
    assert {plan["setup_type"] for plan in selected} == {
        "choch_reversal", "structure_location_pullback",
    }


def test_range_reversal_does_not_use_swing_box_when_internal_is_trend():
    binding = resolve_binding("range_lower_reversal")
    structure = _structure(swing_pattern="range", internal_pattern="trend", swing_status="confirmed")
    assert binding["direction_layer"] == "swing"
    assert binding["entry_layer"] == "internal"
    assert binding_matches(structure, binding) is False
    box, layer = setup_box(structure, binding)
    assert box == {}
    assert layer == ""



def test_require_external_alignment_defaults():
    assert resolve_binding("range_lower_reversal")["require_external_alignment"] is False
    assert resolve_binding("range_false_breakout")["require_external_alignment"] is False
    assert resolve_binding("trend_continuation")["require_external_alignment"] is False
    assert resolve_binding("structure_location_pullback")["require_external_alignment"] is True
    assert resolve_binding("triangle_breakout")["require_external_alignment"] is True
    assert resolve_binding("triangle_breakout_watch")["require_external_alignment"] is True
    assert resolve_binding("choch_reversal")["require_external_alignment"] is True


def test_global_structure_config_does_not_force_external_alignment():
    binding = resolve_binding("range_lower_reversal", STRUCTURE_PLAN_DEFAULT_CONFIG)
    assert binding["require_external_alignment"] is False
    assert binding["entry_layer"] == "internal"
    breakout = resolve_binding("range_breakout", STRUCTURE_PLAN_DEFAULT_CONFIG)
    assert breakout["entry_layer"] == "swing"


def test_setup_overlay_can_toggle_external_alignment():
    assert resolve_binding("structure_location_pullback", {
        "require_external_alignment": False,
    })["require_external_alignment"] is False
    assert resolve_binding("range_lower_reversal", {
        "setup_type": "range_lower_reversal",
        "require_external_alignment": True,
    })["require_external_alignment"] is True


def test_builder_honors_external_alignment_switch():
    structure = _structure()
    structure["structure_hierarchy"]["external"]["bias"] = "down"
    structure["external_state"] = "down"

    blocked = StructurePlanBuilder(STRUCTURE_PLAN_DEFAULT_CONFIG)
    blocked._activate_setup("structure_location_pullback")
    assert blocked._setup_binding()["require_external_alignment"] is True
    assert blocked._external_allows(structure, "up") is False

    allowed = StructurePlanBuilder(
        STRUCTURE_PLAN_DEFAULT_CONFIG,
        setup_profiles=[{
            "setup_type": "structure_location_pullback",
            "require_external_alignment": False,
        }],
    )
    allowed._activate_setup("structure_location_pullback")
    assert allowed._setup_binding()["require_external_alignment"] is False
    assert allowed._external_allows(structure, "up") is True

    reversal = StructurePlanBuilder(STRUCTURE_PLAN_DEFAULT_CONFIG)
    reversal._activate_setup("range_lower_reversal")
    assert reversal._setup_binding()["require_external_alignment"] is False
    assert reversal._external_allows(structure, "up") is True
