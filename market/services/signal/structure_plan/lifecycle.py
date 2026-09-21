"""Pure lifecycle and conflict rules for structure trade plans."""
from __future__ import annotations

from typing import Dict, List, Optional


def invalidate_reason(plan: Dict, price: float) -> str:
    """Return the event that invalidates a plan at Tick time, if any."""
    rules = set(plan.get("tick_invalidation_rules") or [])
    metadata = plan.get("structure_metadata") or {}
    top = float(metadata.get("range_top") or 0)
    bottom = float(metadata.get("range_bottom") or 0)
    setup = str(plan.get("setup_type") or "")
    direction = str(plan.get("direction") or "")
    evidence = plan.get("validation_evidence") or {}
    zone_lower = float(evidence.get("zone_lower") or 0)
    zone_upper = float(evidence.get("zone_upper") or 0)
    zone_buffer = float(evidence.get("zone_invalidation_buffer") or 0)
    if "pressure_zone_return_inside" in rules and zone_upper > zone_lower > 0:
        if zone_lower < price < zone_upper:
            return "pressure_zone_returned_inside"
    if "pressure_protected_level_break" in rules and zone_upper > zone_lower > 0:
        invalid = (zone_lower - zone_buffer) if direction == "buy" else (zone_upper + zone_buffer)
        if (direction == "buy" and price <= invalid) or (direction == "sell" and price >= invalid):
            return "pressure_protected_zone_broken"
    if "close_return_to_invalid_boundary" in rules and top > bottom > 0:
        if bottom < price < top:
            return "range_returned_inside"
    if "protected_level_break" in rules:
        invalid = float(plan.get("invalidation_price") or 0)
        if invalid and ((direction == "buy" and price <= invalid) or (direction == "sell" and price >= invalid)):
            return "protected_level_broken"
    if "triangle_pattern_break" in rules and setup.startswith("triangle_") and top > bottom > 0:
        if (direction == "buy" and price < bottom) or (direction == "sell" and price > top):
            return "triangle_pattern_broken"
    return ""


def _as_float(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if number == number else float(default)


def close_invalidate_reason(
    plan: Dict,
    structure: Optional[Dict],
    close_price: float,
    atr: float = 0.0,
) -> str:
    """Return why a live plan should die on a closed bar, if it should.

    Destruction is close-confirmed. Wicks alone do not kill a waiting plan.
    """
    direction = str(plan.get("direction") or "")
    if direction not in {"buy", "sell"}:
        return ""
    close_price = _as_float(close_price)
    if close_price <= 0:
        return ""
    rules = set(plan.get("close_invalidation_rules") or plan.get("invalidation_rules") or [])
    atr = max(0.0, _as_float(atr))
    buffer = atr * 0.1
    invalid = _as_float(plan.get("invalidation_price") or plan.get("stop_loss"))
    metadata = plan.get("structure_metadata") or {}
    top = _as_float(metadata.get("range_top"))
    bottom = _as_float(metadata.get("range_bottom"))
    setup = str(plan.get("setup_type") or "")
    structure = structure or {}
    current_segment = str(structure.get("structure_segment_id") or "")
    plan_segment = str(
        plan.get("structure_segment_id")
        or metadata.get("segment_id")
        or ""
    )

    if "protected_level_break" in rules and invalid > 0:
        if direction == "buy" and close_price <= invalid - buffer:
            return "保护低点被收盘破坏"
        if direction == "sell" and close_price >= invalid + buffer:
            return "保护高点被收盘破坏"

    if "range_structure_break" in rules:
        box = structure.get("range") or {}
        box_status = str(box.get("status") or "")
        broken = False
        if invalid > 0:
            broken = (
                (direction == "buy" and close_price <= invalid - buffer)
                or (direction == "sell" and close_price >= invalid + buffer)
            )
        elif top > bottom > 0:
            broken = (
                (direction == "buy" and close_price < bottom - buffer)
                or (direction == "sell" and close_price > top + buffer)
            )
        if broken or box_status in {"breakout_confirmed", "failed"} and (
            (direction == "buy" and str(box.get("breakout_direction") or "") == "down")
            or (direction == "sell" and str(box.get("breakout_direction") or "") == "up")
        ):
            return "区间结构被收盘破坏"

    if "triangle_pattern_break" in rules and "triangle" in setup and top > bottom > 0:
        if direction == "buy" and close_price < bottom - buffer:
            return "三角形结构被收盘破坏"
        if direction == "sell" and close_price > top + buffer:
            return "三角形结构被收盘破坏"

    if plan_segment and current_segment and plan_segment != current_segment:
        # Segment change means the original trade thesis belongs to a finished
        # structure. Keep only if a newer active opportunity replaces it via
        # supersede; otherwise retire the orphaned waiter.
        if str(plan.get("status") or "") in {"active", "event_suppressed"}:
            return "结构段已切换，原交易机会失效"

    return ""


def opportunity_still_valid(
    plan: Dict,
    structure: Optional[Dict],
    close_price: float,
    atr: float = 0.0,
) -> bool:
    """Whether a waiting plan's entry thesis is still worth keeping."""
    if close_invalidate_reason(plan, structure, close_price, atr):
        return False
    direction = str(plan.get("direction") or "")
    if direction not in {"buy", "sell"}:
        return False
    entry = _as_float(plan.get("entry_price"))
    close_price = _as_float(close_price)
    if entry <= 0 or close_price <= 0:
        return False
    zone = plan.get("entry_zone") or {}
    lower = _as_float(zone.get("lower"))
    upper = _as_float(zone.get("upper"))
    zone_width = abs(upper - lower) if upper > lower > 0 else 0.0
    if zone_width > 0:
        if abs(close_price - entry) / zone_width > 8.0:
            return False
    else:
        if abs(close_price - entry) / entry * 100.0 > 0.8:
            return False
    structure = structure or {}
    plan_segment = str(
        plan.get("structure_segment_id")
        or (plan.get("structure_metadata") or {}).get("segment_id")
        or ""
    )
    current_segment = str(structure.get("structure_segment_id") or "")
    if plan_segment and current_segment and plan_segment != current_segment:
        return False
    return True


def resolve_conflicts(plans: List[Dict]) -> List[Dict]:
    """Keep the strongest direction when active plans conflict."""
    actionable = [p for p in plans if str(p.get("direction") or "") in {"buy", "sell"}]
    buys = [p for p in actionable if p.get("direction") == "buy"]
    sells = [p for p in actionable if p.get("direction") == "sell"]
    if not buys or not sells:
        return plans
    def score(plan: Dict):
        try:
            rr = float(plan.get("risk_reward_ratio") or 0)
        except (TypeError, ValueError):
            rr = 0.0
        try:
            distance = abs(float(plan.get("entry_price") or 0) - float(plan.get("trigger_price") or 0))
        except (TypeError, ValueError):
            distance = 0.0
        return (int(plan.get("confidence") or 0), rr, -distance)
    winner = max(actionable, key=score)
    return [p for p in plans if p not in actionable or p is winner]


def stage_for(status: str, entry_mode: str) -> str:
    if status == "watching":
        return "candidate"
    if entry_mode in {"breakout_retest", "touch_and_reclaim"}:
        return "confirmed"
    return "active"
