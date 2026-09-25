"""Normalize structural analysis into one auditable state vocabulary.

The structure engine intentionally exposes several independent views (major
state, range pattern, trend phase and events).  This module does not change
those calculations; it gives plans a stable semantic projection so that
selection and execution can later be separated from detection.
"""

from __future__ import annotations

from typing import Any, Mapping


def _latest_event(structure: Mapping[str, Any]) -> Mapping[str, Any]:
    events = list(structure.get("internal_events") or [])
    events += list(structure.get("external_events") or [])
    if not events:
        return {}
    return max(
        events,
        key=lambda item: (
            int(item.get("confirmed_at") or item.get("index") or 0),
            int(item.get("bar_time") or item.get("time") or 0),
        ),
    )


def _local_pattern(box: Mapping[str, Any]) -> str:
    pattern = str(box.get("pattern") or "").lower()
    if "triangle" in pattern or "converg" in pattern:
        return "triangle"
    if pattern in {"range", "box", "rectangle"} or box.get("active"):
        return "range"
    return "none"


def _phase(structure: Mapping[str, Any], event: Mapping[str, Any], local: str) -> str:
    box = structure.get("range") or {}
    status = str(box.get("status") or "").lower()
    if status == "breakout_confirmed":
        return "breakout_confirmed"
    if status == "failed_breakout":
        return "reversal_candidate"
    event_type = str(event.get("type") or "").lower()
    if event_type in {"bos", "choch", "breakout", "breakout_confirmed"}:
        return "breakout_confirmed"
    if event_type in {"false_breakout"}:
        return "reversal_candidate"
    trend_phase = str(structure.get("trend_phase") or "").lower()
    if trend_phase in {"pullback", "retest", "continuation"}:
        return "pullback"
    if trend_phase in {"expansion", "impulse", "trend"}:
        return "forming"
    if local in {"range", "triangle"}:
        return "forming"
    return "undetermined"


def _execution_setup(setup_type: str, entry_mode: str = "") -> str:
    setup = str(setup_type or "").lower()
    mode = str(entry_mode or "").lower()
    if setup == "no_trade":
        return "no_trade"
    if setup == "range_false_breakout":
        return "false_breakout_reclaim"
    if "triangle" in setup and "breakout" in setup:
        return "triangle_breakout_retest" if "retest" in mode else "triangle_breakout"
    if setup == "range_breakout":
        return "range_breakout_retest" if "retest" in mode else "range_breakout"
    if setup in {"structure_location_pullback", "trend_continuation"}:
        return "trend_pullback_reclaim"
    if setup == "structure_reversal":
        return "structure_reversal"
    return setup or "observation"


def derive_structure_state(
    structure: Mapping[str, Any] | None,
    *,
    setup_type: str = "",
    direction: str = "",
    entry_mode: str = "",
    event: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the normalized state without changing the source snapshot."""
    source = structure or {}
    box = source.get("range") or {}
    major = str(source.get("major_state") or "undetermined").lower()
    local = _local_pattern(box)
    latest = dict(event or _latest_event(source))
    event_type = str(latest.get("type") or "none").lower()
    if event_type == "zone_breakout_confirmed":
        event_type = "breakout"
    if major == "up":
        primary = "trend_up"
    elif major == "down":
        primary = "trend_down"
    elif local == "range":
        primary = "range"
    elif local == "triangle":
        primary = "transition"
    else:
        primary = "transition"
    if event_type == "none" and str(box.get("status") or "").lower() == "breakout_confirmed":
        event_type = "breakout"
    return {
        "primary_structure": primary,
        "local_pattern": local,
        "phase": _phase(source, latest, local),
        "event": event_type,
        "event_direction": str(latest.get("direction") or direction or "none"),
        "execution_setup": _execution_setup(setup_type, entry_mode),
        "major_state": major,
        "trend_phase": str(source.get("trend_phase") or "undetermined"),
        "trend_regime": str(source.get("trend_regime") or ""),
    }
