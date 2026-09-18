import unittest

from paper_trading import PaperTradingService


class _FakeRuntimeStorage:
    def __init__(self):
        self.queries = []

    def fetchall(self, sql, params=()):
        self.queries.append((sql, tuple(params)))
        if "entity_id IN" in sql:
            return [{
                "entity_id": "d1",
                "payload_json": '{"decision_reason":"箱体突破"}',
            }]
        return []


class RuntimePageQueryTests(unittest.TestCase):
    def test_paper_detail_looks_up_only_page_decision_ids(self):
        captured = {}

        class _Runtime:
            def __init__(self, *args, **kwargs):
                captured["args"] = args

            def list_entities_by_ids(self, entity_type, entity_ids):
                captured["entity_type"] = entity_type
                captured["entity_ids"] = list(entity_ids)
                return [{
                    "decision_id": "dec-1",
                    "decision_reason": "结构突破卖出",
                }]

            def list_entities(self, *args, **kwargs):
                raise AssertionError("paper detail must not scan all decisions")

        class _Service(PaperTradingService):
            def __init__(self):
                self.storage = object()
                self.position_events = type("E", (), {
                    "list_for_positions": staticmethod(lambda *a, **k: {}),
                })()

            def _paper_account(self, user_id, account_id):
                return type("A", (), {
                    "account_id": account_id, "user_id": user_id,
                    "account_name": "ULTRAPAPER", "account_type": "paper",
                    "status": "active", "currency": "USD",
                })()

            def _settings(self, account_id):
                return {}

            def _account_dict(self, account):
                return {"account_id": account.account_id}

        service = _Service()
        module = __import__("paper_trading")
        original = module.RuntimeStateRepository
        module.RuntimeStateRepository = _Runtime
        try:
            def fetchall(sql, params=()):
                if "FROM strategy_deployments" in sql:
                    return []
                if "FROM paper_positions" in sql:
                    return []
                if "FROM paper_orders" in sql:
                    return [{
                        "order_id": "o1", "decision_id": "dec-1",
                        "status": "filled", "rejection_reason": "",
                        "position_attribution_json": "{}",
                        "linked_position_id": "", "stop_loss": 0,
                        "take_profit": 0,
                    }]
                if "FROM paper_trades" in sql:
                    return [{
                        "trade_id": "t1", "open_decision_id": "dec-2",
                        "position_attribution_json": "{}",
                        "exit_reason": "stop_loss",
                        "initial_stop_loss": 0, "initial_take_profit": 0,
                    }]
                if "FROM paper_runtime_logs" in sql:
                    raise AssertionError("paper detail must not load runtime logs")
                raise AssertionError(sql)
            service.storage = type("S", (), {"fetchall": staticmethod(fetchall)})()
            detail = service.get_account_detail(1, 22, page=1, page_size=30)
        finally:
            module.RuntimeStateRepository = original

        self.assertEqual(captured["entity_type"], "strategy_decision")
        self.assertEqual(captured["entity_ids"], ["dec-1", "dec-2"])
        self.assertEqual(detail["orders"][0]["open_reason"], "结构突破卖出")
        self.assertEqual(detail["trades"][0]["open_reason"], "策略信号触发开仓")
        self.assertNotIn("today_trade_stats", detail)
        self.assertNotIn("strategy_performance", detail)
        self.assertNotIn("execution_funnel", detail)
        self.assertNotIn("runtime_logs", detail)


if __name__ == "__main__":
    unittest.main()
