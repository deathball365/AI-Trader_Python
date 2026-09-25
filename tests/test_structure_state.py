from market.services.signal.structure_state import derive_structure_state


def test_trend_triangle_breakout_maps_to_explicit_execution_setup():
    state = derive_structure_state(
        {
            "major_state": "up",
            "range": {"active": True, "pattern": "triangle", "status": "breakout_confirmed"},
        },
        setup_type="triangle_breakout",
        entry_mode="breakout_retest",
    )

    assert state["primary_structure"] == "trend_up"
    assert state["local_pattern"] == "triangle"
    assert state["phase"] == "breakout_confirmed"
    assert state["event"] == "breakout"
    assert state["execution_setup"] == "triangle_breakout_retest"


def test_failed_range_breakout_maps_to_reclaim_setup():
    state = derive_structure_state(
        {
            "major_state": "undetermined",
            "range": {"active": True, "pattern": "range", "status": "failed_breakout"},
        },
        setup_type="range_false_breakout",
        entry_mode="touch_and_reclaim",
    )

    assert state["primary_structure"] == "range"
    assert state["local_pattern"] == "range"
    assert state["phase"] == "reversal_candidate"
    assert state["execution_setup"] == "false_breakout_reclaim"


def test_latest_event_is_normalized_without_mutating_source():
    structure = {
        "major_state": "down",
        "range": {},
        "internal_events": [{"type": "bos", "confirmed_at": 10, "direction": "sell"}],
    }
    state = derive_structure_state(structure, setup_type="structure_location_pullback")

    assert state["primary_structure"] == "trend_down"
    assert state["event"] == "bos"
    assert state["event_direction"] == "sell"
    assert state["execution_setup"] == "trend_pullback_reclaim"
    assert "structure_state" not in structure

