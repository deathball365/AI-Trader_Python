"""Configurable SETUP binding: pattern + layer event, with direction/entry roles."""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional


LAYERS = ("internal", "swing", "external")
PATTERNS = ("range", "triangle", "trend")
EVENTS = (
    "bos", "choch", "retest", "reclaim", "false_breakout",
    "liquidity_sweep", "breakout_confirmed",
)

# Existing SETUP names keep working.  Each one now names the geometry it
# watches, which layer decides direction, and which layer times entry.
DEFAULT_BINDINGS: Dict[str, Dict[str, str]] = {
    "range_breakout": {
        "bind_pattern": "range", "bind_event": "breakout_confirmed",
        "direction_layer": "swing", "entry_layer": "swing",
    },
    "range_breakout_watch": {
        "bind_pattern": "range", "bind_event": "breakout_confirmed",
        "direction_layer": "swing", "entry_layer": "swing",
    },
    "range_false_breakout": {
        "bind_pattern": "range", "bind_event": "false_breakout",
        "direction_layer": "swing", "entry_layer": "internal",
    },
    "range_lower_reversal": {
        "bind_pattern": "range", "bind_event": "reclaim",
        "direction_layer": "swing", "entry_layer": "swing",
    },
    "range_upper_reversal": {
        "bind_pattern": "range", "bind_event": "reclaim",
        "direction_layer": "swing", "entry_layer": "swing",
    },
    "triangle_breakout": {
        "bind_pattern": "triangle", "bind_event": "breakout_confirmed",
        "direction_layer": "swing", "entry_layer": "swing",
    },
    "triangle_breakout_watch": {
        "bind_pattern": "triangle", "bind_event": "breakout_confirmed",
        "direction_layer": "swing", "entry_layer": "swing",
    },
    "triangle_prebreakout_pullback": {
        "bind_pattern": "triangle", "bind_event": "retest",
        "direction_layer": "swing", "entry_layer": "internal",
    },
    "structure_location_pullback": {
        "bind_pattern": "trend", "bind_event": "retest",
        "direction_layer": "swing", "entry_layer": "internal",
    },
    "trend_continuation": {
        "bind_pattern": "trend", "bind_event": "bos",
        "direction_layer": "swing", "entry_layer": "internal",
    },
    "structure_reversal": {
        "bind_pattern": "trend", "bind_event": "choch",
        "direction_layer": "swing", "entry_layer": "swing",
    },
    "choch_reversal": {
        "bind_pattern": "trend", "bind_event": "choch",
        "direction_layer": "swing", "entry_layer": "swing",
    },
    "liquidity_sweep_reclaim": {
        "bind_pattern": "trend", "bind_event": "liquidity_sweep",
        "direction_layer": "swing", "entry_layer": "internal",
    },
}


def normalize_layer(value: str, default: str = "swing") -> str:
    name = str(value or "").strip().lower()
    aliases = {"small": "internal", "medium": "swing", "major": "swing", "large": "external"}
    name = aliases.get(name, name)
    return name if name in LAYERS else default


def normalize_pattern(value: str) -> str:
    name = str(value or "").strip().lower()
    if "triangle" in name or name in {"ascending_triangle", "descending_triangle", "broadening", "converging_triangle"}:
        return "triangle"
    if name in {"range", "box", "rectangle", "sideways"}:
        return "range"
    if name in {"trend", "up", "down", "trend_up", "trend_down"}:
        return "trend"
    return "none"


def normalize_event(value) -> str:
    if isinstance(value, dict):
        value = value.get("type") or value.get("event_type") or ""
    name = str(value or "").strip().lower()
    aliases = {
        "breakout": "breakout_confirmed",
        "range_breakout_confirmed": "breakout_confirmed",
        "failed_breakout": "false_breakout",
        "range_failed_breakout": "false_breakout",
        "pullback": "retest",
        "retest_confirmed": "retest",
        "reclaim_confirmed": "reclaim",
    }
    name = aliases.get(name, name)
    return name if name in EVENTS else (name or "none")


def layer_state(structure: Dict, layer: str) -> Dict:
    hierarchy = (structure or {}).get("structure_hierarchy") or {}
    return dict(hierarchy.get(normalize_layer(layer)) or {})


def layer_events(structure: Dict, layer: str) -> List[Dict]:
    layer = normalize_layer(layer)
    mapping = {
        "internal": (structure or {}).get("internal_events") or [],
        "swing": (structure or {}).get("major_events") or [],
        "external": (structure or {}).get("external_events") or [],
    }
    return list(mapping.get(layer) or [])


def layer_pattern(structure: Dict, layer: str) -> str:
    state = layer_state(structure, layer)
    return normalize_pattern(state.get("pattern"))


def layer_event(structure: Dict, layer: str) -> str:
    state = layer_state(structure, layer)
    current = normalize_event(state.get("event"))
    if current and current != "none":
        return current
    events = layer_events(structure, layer)
    if not events:
        return "none"
    return normalize_event(events[-1])


def resolve_binding(setup_type: str, config: Optional[Dict] = None) -> Dict[str, str]:
    setup = str(setup_type or "").strip().lower()
    base = dict(DEFAULT_BINDINGS.get(setup) or {
        "bind_pattern": "trend", "bind_event": "bos",
        "direction_layer": "swing", "entry_layer": "swing",
    })
    cfg = config or {}
    for key in ("bind_pattern", "bind_event", "direction_layer", "entry_layer"):
        if cfg.get(key):
            base[key] = cfg[key]
    base["bind_pattern"] = normalize_pattern(base.get("bind_pattern"))
    if base["bind_pattern"] == "none":
        base["bind_pattern"] = "trend"
    base["bind_event"] = normalize_event(base.get("bind_event"))
    base["direction_layer"] = normalize_layer(base.get("direction_layer"))
    base["entry_layer"] = normalize_layer(base.get("entry_layer"), base["direction_layer"])
    base["setup_type"] = setup
    return base


def binding_matches(structure: Dict, binding: Dict) -> bool:
    wanted_pattern = normalize_pattern(binding.get("bind_pattern"))
    wanted_event = normalize_event(binding.get("bind_event"))
    direction_layer = normalize_layer(binding.get("direction_layer"))
    entry_layer = normalize_layer(binding.get("entry_layer"), direction_layer)
    if layer_pattern(structure, direction_layer) != wanted_pattern:
        return False
    observed = {layer_event(structure, direction_layer), layer_event(structure, entry_layer)}
    if wanted_event in {"retest", "reclaim"}:
        return wanted_event in observed or "bos" in observed or "breakout_confirmed" in observed
    return wanted_event in observed


def iter_enabled_bindings(config: Dict, setup_types: Iterable[str]) -> List[Dict]:
    bindings = []
    for setup in setup_types:
        item = resolve_binding(setup, config)
        item["setup_type"] = setup
        bindings.append(item)
    return bindings
