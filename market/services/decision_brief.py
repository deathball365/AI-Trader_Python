"""Build a readable opening-decision brief from position attribution and the structure plan."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional


SETUP_LABELS = {
    "liquidity_sweep_reclaim": "流动性扫单回收",
    "structure_location_pullback": "结构位置回撤",
    "range_breakout": "箱体突破",
    "range_breakout_watch": "箱体突破观察",
    "range_false_breakout": "箱体假突破",
    "range_lower_reversal": "箱体下沿反转",
    "range_upper_reversal": "箱体上沿反转",
    "triangle_breakout": "三角形突破",
    "triangle_breakout_watch": "三角形突破观察",
    "triangle_prebreakout_pullback": "三角形提前回撤",
    "trend_continuation": "趋势延续",
    "choch_reversal": "CHOCH 反转",
    "structure_reversal": "结构反转",
}

BIAS_LABELS = {"up": "上涨", "down": "下跌", "sideways": "震荡", "range": "箱体"}
DIRECTION_LABELS = {"buy": "买入", "sell": "卖出"}
LAYER_LABELS = {"internal": "Internal", "swing": "Swing", "external": "External"}
TZ_SHANGHAI = timezone(timedelta(hours=8))


def _text(value, default=""):
    return str(value or default).strip()


def _number(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _time(value) -> str:
    try:
        stamp = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if stamp <= 0:
        return ""
    if stamp > 10_000_000_000:
        stamp //= 1000
    return datetime.fromtimestamp(stamp, TZ_SHANGHAI).strftime("%m/%d %H:%M")


def _price(value) -> str:
    number = _number(value)
    if number <= 0:
        return "--"
    digits = 5 if number < 10 else 3 if number < 1000 else 2
    return f"{number:,.{digits}f}"


def _setup_label(setup: str) -> str:
    key = _text(setup).lower()
    return SETUP_LABELS.get(key, setup or "结构入场")


def _bias_label(value: str) -> str:
    key = _text(value).lower()
    return BIAS_LABELS.get(key, value or "未确认")


def _layer_bias(hierarchy: Dict, layer: str) -> str:
    payload = (hierarchy or {}).get(layer) or {}
    return _text(payload.get("bias") or payload.get("state"))


def _layer_event(hierarchy: Dict, layer: str) -> Dict:
    payload = (hierarchy or {}).get(layer) or {}
    event = payload.get("event") or payload.get("last_event") or {}
    return event if isinstance(event, dict) else {}


def _layer_describe(hierarchy: Dict, layer: str, fallback_bias: str = "") -> str:
    payload = (hierarchy or {}).get(layer) or {}
    pattern = _text(payload.get("pattern")).lower()
    bias = _text(payload.get("bias") or fallback_bias).lower()
    detail = payload.get("pattern_detail") or {}
    name = LAYER_LABELS.get(layer, layer)
    if "triangle" in pattern:
        return f"{name} 三角形"
    if pattern in {"range", "box", "rectangle", "sideways", "broadening"}:
        top = _number(detail.get("top") or detail.get("locked_top"))
        bottom = _number(detail.get("bottom") or detail.get("locked_bottom"))
        high_slope = _number(detail.get("high_slope"))
        low_slope = _number(detail.get("low_slope"))
        shape = "箱体"
        if high_slope < 0 and low_slope < 0:
            shape = "下降箱体"
        elif high_slope > 0 and low_slope > 0:
            shape = "上升箱体"
        edges = f"（下沿 {_price(bottom)} / 上沿 {_price(top)}）" if top and bottom else ""
        return f"{name} {shape}{edges}"
    if pattern in {"trend", "up", "down"} or bias in {"up", "down"}:
        return f"{name} {_bias_label(bias or pattern)}趋势"
    if bias:
        return f"{name} {_bias_label(bias)}"
    return ""


def build_decision_brief(
    attribution: Optional[Dict] = None,
    plan: Optional[Dict] = None,
    position: Optional[Dict] = None,
) -> Dict:
    attribution = attribution or {}
    plan = plan or {}
    position = position or {}
    snapshot = plan.get("structure_snapshot") or attribution.get("structure_snapshot") or {}
    hierarchy = snapshot.get("structure_hierarchy") or snapshot.get("structure_levels") or {}
    setup = _text(attribution.get("setup_type") or plan.get("setup_type"))
    direction = _text(
        attribution.get("direction") or plan.get("direction") or position.get("direction")
    ).lower()
    symbol = _text(position.get("symbol") or plan.get("symbol") or attribution.get("symbol"))
    period = _text(
        attribution.get("signal_source_period") or plan.get("period") or "结构"
    ).upper()
    strategy = _text(attribution.get("strategy_name") or position.get("strategy_id"))
    entry = _number(position.get("entry_price") or plan.get("entry_price"))
    planned_entry = _number(plan.get("entry_price") or attribution.get("planned_entry"))
    stop = _number(
        attribution.get("initial_stop_loss")
        or position.get("stop_loss")
        or plan.get("stop_loss")
    )
    take = _number(
        attribution.get("initial_take_profit")
        or position.get("take_profit")
        or plan.get("take_profit")
    )
    volume = _number(position.get("volume") or attribution.get("initial_volume") or 0.01)
    opened = _time(position.get("opened_at") or attribution.get("opened_at"))
    direction_layer = _text(plan.get("direction_layer") or "swing")
    entry_layer = _text(plan.get("entry_layer") or "internal")
    swing = _layer_bias(hierarchy, "swing") or _text(snapshot.get("major_state"))
    internal = _layer_bias(hierarchy, "internal") or _text(snapshot.get("internal_state"))
    external = _layer_bias(hierarchy, "external") or _text(snapshot.get("external_state"))
    event = _layer_event(hierarchy, entry_layer) or _layer_event(hierarchy, "internal")
    event_type = _text(event.get("type") or snapshot.get("event") or plan.get("bind_event"))
    event_direction = _text(event.get("direction") or "")
    event_level = _number(event.get("level") or planned_entry)
    plan_reason = _text(plan.get("reason")).split("·")[0].strip()
    entry_reason = _text(attribution.get("entry_reason"))

    if not setup and not strategy and not entry:
        return {
            "available": False,
            "title": "没有策略归因",
            "summary": "这笔持仓没有策略决策记录，更像手工单或未完成归因的成交。",
            "sections": [],
        }

    action = DIRECTION_LABELS.get(direction, "开仓")
    title = f"{action} {symbol or '品种'} · {_setup_label(setup)}"
    summary_parts = []
    if period:
        summary_parts.append(f"{period} 结构计划")
    if setup == "liquidity_sweep_reclaim":
        swept = "下方低点" if event_direction == "down" or direction == "buy" else "上方高点"
        summary_parts.append(f"扫过{swept} {_price(event_level)} 后回收")
        if swing:
            summary_parts.append(f"与{_bias_label(swing)}结构一致")
    elif plan_reason:
        summary_parts.append(plan_reason)
    elif setup:
        summary_parts.append(_setup_label(setup))
    summary = "，".join(part for part in summary_parts if part) or "结构信号触发开仓"

    sections: List[Dict[str, str]] = []
    sections.append({
        "title": "策略",
        "body": (
            f"{strategy or '结构策略'} 只听 {period or '结构'} 计划。"
            f"{' 唯一信号源，方向一致后开仓。' if '一致率' in entry_reason or '单一信号' in entry_reason else ''}"
        ).strip(),
    })
    structure_bits = [
        _layer_describe(hierarchy, "swing", swing),
        _layer_describe(hierarchy, "internal", internal),
        _layer_describe(hierarchy, "external", external),
    ]
    structure_text = "，".join(bit for bit in structure_bits if bit)
    if setup == "liquidity_sweep_reclaim":
        allow = "只许买" if swing == "up" else "只许卖" if swing == "down" else "按方向层决定"
        sweep_side = "低点" if direction == "buy" else "高点"
        structure_body = (
            f"这个 SETUP 方向看 {LAYER_LABELS.get(direction_layer, direction_layer)}，"
            f"入场看 {LAYER_LABELS.get(entry_layer, entry_layer)}。"
            f"{' 当时 ' + structure_text + '。' if structure_text else ' '}"
            f"大级别{allow}；{LAYER_LABELS.get(entry_layer, '入场层')} 扫了{sweep_side} "
            f"{_price(event_level)} 后又收回，所以做这笔 {action}。"
        )
    elif setup in {"range_lower_reversal", "range_upper_reversal", "range_false_breakout"}:
        edge = "下沿" if direction == "buy" else "上沿"
        structure_body = (
            f"这个 SETUP 方向看 {LAYER_LABELS.get(direction_layer, direction_layer)}，"
            f"入场看 {LAYER_LABELS.get(entry_layer, entry_layer)} 的箱体{edge}。"
            f"{' 当时 ' + structure_text + '。' if structure_text else ' '}"
            f"{LAYER_LABELS.get(direction_layer, '方向层')}{'上涨所以只买下沿' if direction=='buy' else '下跌所以只卖上沿'}。"
            f"{(' ' + plan_reason + '。') if plan_reason else ''}"
        )
    else:
        structure_body = (
            f"SETUP 是 {_setup_label(setup)}。"
            f"{' 当时 ' + structure_text + '。' if structure_text else ''}"
            f"{(' ' + plan_reason + '。') if plan_reason else ''}"
        )
    sections.append({"title": "结构判断", "body": structure_body.strip()})

    fill_note = ""
    if planned_entry and entry and abs(entry - planned_entry) >= 0.01:
        fill_note = f"计划入场 {_price(planned_entry)}，实际成交 {_price(entry)}。"
    elif entry:
        fill_note = f"成交价 {_price(entry)}。"
    time_note = f"{opened} 开仓。" if opened else ""
    sections.append({
        "title": "入场",
        "body": (
            f"{time_note}{fill_note}"
            f"手数 {volume:g}。"
            f"{' 入场方式是回到被扫结构位附近再进。' if _text(plan.get('entry_mode') or attribution.get('entry_mode')) == 'touch_and_reclaim' else ''}"
            f"{' 入场方式是价格回到箱沿附近即可。' if _text(plan.get('entry_mode') or attribution.get('entry_mode')) == 'touch_or_near' else ''}"
        ).strip(),
    })

    risk_body = (
        f"初始止损 {_price(stop)}，止盈 {_price(take)}。"
        f"持仓方案 { _text(attribution.get('position_policy_name') or attribution.get('setup_profile_name') or '默认方案') }。"
    )
    if "最小止损" in entry_reason or "自动调整" in entry_reason:
        extra = entry_reason.split("|")[-1].strip()
        if extra:
            risk_body += f" {extra}。"
    sections.append({"title": "风控", "body": risk_body.strip()})
    for item in sections:
        item["body"] = re.sub(r" +", " ", item["body"]).strip()

    return {
        "available": True,
        "title": title,
        "summary": summary,
        "setup_type": setup,
        "setup_label": _setup_label(setup),
        "direction": direction,
        "symbol": symbol,
        "period": period,
        "sections": sections,
    }
