from market.services.signal.structure_plan.setup_binding import (
    binding_matches, layer_pattern, resolve_binding,
)


def _structure(swing_pattern="trend", internal_pattern="range", swing_event="bos", internal_event="reclaim"):
    return {
        "structure_hierarchy": {
            "swing": {"pattern": swing_pattern, "event": {"type": swing_event}},
            "internal": {"pattern": internal_pattern, "event": {"type": internal_event}},
            "external": {"pattern": "trend", "event": {"type": "bos"}},
        },
        "major_events": [{"type": swing_event, "direction": "up"}],
        "internal_events": [{"type": internal_event, "direction": "up"}],
        "external_events": [{"type": "bos", "direction": "up"}],
    }


def test_default_location_pullback_uses_swing_direction_and_internal_entry():
    binding = resolve_binding("structure_location_pullback")
    assert binding["bind_pattern"] == "trend"
    assert binding["direction_layer"] == "swing"
    assert binding["entry_layer"] == "internal"
    structure = _structure()
    assert layer_pattern(structure, "internal") == "range"
    assert binding_matches(structure, binding)


def test_range_breakout_does_not_match_internal_box_when_swing_is_trend():
    binding = resolve_binding("range_breakout")
    structure = _structure(swing_pattern="trend", internal_pattern="range", swing_event="bos")
    assert binding_matches(structure, binding) is False
    structure["structure_hierarchy"]["swing"]["pattern"] = "range"
    structure["structure_hierarchy"]["swing"]["event"] = {"type": "breakout_confirmed"}
    assert binding_matches(structure, binding) is True


def test_setup_override_can_use_internal_as_direction_layer():
    binding = resolve_binding("range_false_breakout", {
        "direction_layer": "internal", "entry_layer": "internal",
        "bind_pattern": "range", "bind_event": "false_breakout",
    })
    structure = _structure(swing_pattern="trend", internal_pattern="range", internal_event="false_breakout")
    assert binding["direction_layer"] == "internal"
    assert binding_matches(structure, binding) is True
