from market.services.decision_brief import build_decision_brief


def test_liquidity_sweep_buy_brief_matches_gold_style_attribution():
    brief = build_decision_brief(
        attribution={
            "direction": "buy",
            "setup_type": "liquidity_sweep_reclaim",
            "strategy_name": "GOLD# · GOLD_ 结构信号 M15 · ULTRAPAPER#22",
            "signal_source_period": "M15",
            "entry_reason": "单一信号(structure_plan)建议buy | 综合判断: buy，一致率100% | 止损距离 0.19 小于 最小止损 1.5 ATR，已自动调整为 16.44",
            "initial_stop_loss": 4254.38,
            "initial_take_profit": 4304.57,
            "initial_volume": 0.01,
            "position_policy_name": "信号止盈止损分批止盈",
            "entry_mode": "touch_and_reclaim",
        },
        plan={
            "setup_type": "liquidity_sweep_reclaim",
            "direction": "buy",
            "period": "M15",
            "direction_layer": "swing",
            "entry_layer": "internal",
            "entry_price": 4270.92,
            "entry_mode": "touch_and_reclaim",
            "reason": "M15 扫过下方低点后回收，与上涨结构一致",
            "structure_snapshot": {
                "major_state": "up",
                "internal_state": "down",
                "external_state": "up",
                "structure_hierarchy": {
                    "swing": {"bias": "up"},
                    "internal": {
                        "bias": "down",
                        "event": {"type": "liquidity_sweep", "direction": "down", "level": 4270.92},
                    },
                    "external": {"bias": "up"},
                },
            },
        },
        position={"symbol": "GOLD#", "direction": "buy", "entry_price": 4271.36, "volume": 0.01, "opened_at": 1790347581},
    )
    assert brief["available"] is True
    assert brief["title"] == "买入 GOLD# · 流动性扫单回收"
    assert "扫过下方低点" in brief["summary"]
    assert "上涨" in brief["summary"]
    bodies = " ".join(item["body"] for item in brief["sections"])
    assert "方向看 Swing" in bodies
    assert "4,270.92" in bodies
    assert "最小止损" in bodies


def test_missing_attribution_is_manual():
    brief = build_decision_brief({}, {}, {})
    assert brief["available"] is False


def test_range_lower_reversal_describes_internal_box_not_uptrend():
    brief = build_decision_brief(
        attribution={
            "direction": "buy",
            "setup_type": "range_lower_reversal",
            "strategy_name": "EURUSD# · M5 结构信号策略",
            "signal_source_period": "M5",
            "entry_mode": "touch_or_near",
            "initial_stop_loss": 1.13899,
            "initial_take_profit": 1.14100,
        },
        plan={
            "setup_type": "range_lower_reversal",
            "direction": "buy",
            "period": "M5",
            "direction_layer": "swing",
            "entry_layer": "internal",
            "entry_mode": "touch_or_near",
            "reason": "M5 箱体下沿回收买入计划，上下沿确认 3/3 次",
            "structure_snapshot": {
                "major_state": "up",
                "internal_state": "up",
                "external_state": "up",
                "structure_hierarchy": {
                    "swing": {"bias": "up", "pattern": "trend"},
                    "internal": {
                        "bias": "up",
                        "pattern": "range",
                        "pattern_detail": {"top": 1.14034, "bottom": 1.13956, "high_touches": 3, "low_touches": 3},
                    },
                    "external": {"bias": "up", "pattern": "trend"},
                },
            },
        },
        position={"symbol": "EURUSD#", "direction": "buy", "entry_price": 1.13965, "volume": 0.05},
    )
    bodies = " ".join(item["body"] for item in brief["sections"])
    assert "Internal 箱体" in bodies
    assert "Internal 上涨趋势" not in bodies
    assert "箱沿附近" in bodies
