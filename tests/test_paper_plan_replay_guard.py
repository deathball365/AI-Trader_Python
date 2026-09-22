import unittest
from types import SimpleNamespace

from paper_trading import PaperTradingService


class _Storage:
    def __init__(self, rows):
        self.rows = rows
        self.seen_sql = []

    def fetchall(self, sql, params):
        self.seen_sql.append(sql)
        if "FROM paper_orders" in sql:
            return list(self.rows)
        return []


def _signal(**overrides):
    payload = {
        "setup_type": "range_upper_reversal",
        "setup_family": "range",
        "source": "structure_plan",
        "trade_plan_id": "plan-1",
        "trade_plan_valid_from": 1000,
        "trade_plan_group_id": "group-1",
        "ai_plan_id": "",
        "ai_plan_valid_from": 0,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _guard(rows):
    service = PaperTradingService.__new__(PaperTradingService)
    service.storage = _Storage(rows)
    return service


class PaperPlanReplayGuardTests(unittest.TestCase):
    def test_canceled_timeout_order_does_not_consume_structure_plan(self):
        service = _guard([])
        result = service._paper_loss_streak_guard(
            1, 22, {"deployment_id": "dep-1"}, "AUDUSD#",
            SimpleNamespace(), "sell", _signal(),
            {"loss_streak_circuit_breaker_enabled": False},
        )
        self.assertTrue(result["allowed"])
        self.assertIn(
            "AND status IN ('pending', 'filled')",
            service.storage.seen_sql[0],
        )

    def test_pending_or_filled_order_still_blocks_same_structure_plan(self):
        service = _guard([
            {
                "position_attribution_json": (
                    '{"trade_plan_instance_id":"plan-1:1000",'
                    '"trade_plan_group_id":"group-1"}'
                ),
            }
        ])
        result = service._paper_loss_streak_guard(
            1, 22, {"deployment_id": "dep-1"}, "AUDUSD#",
            SimpleNamespace(), "sell", _signal(),
            {"loss_streak_circuit_breaker_enabled": False},
        )
        self.assertFalse(result["allowed"])
        self.assertIn("已经触发过", result["reason"])


if __name__ == "__main__":
    unittest.main()
