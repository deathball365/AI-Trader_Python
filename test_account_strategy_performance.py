import unittest

from market.services.account_strategy_performance import _summarize, build_live_performance


class AccountStrategyPerformanceTests(unittest.TestCase):
    def test_summarizes_completed_positions_and_risk_metrics(self):
        deployment = {
            "deployment_id": "dep-1", "strategy_id": "strategy-1",
            "strategy_name": "Gold M5", "symbol": "GOLD_",
            "execution_mode": "paper", "status": "active",
            "created_at": 100, "updated_at": 200,
        }
        result = _summarize(deployment, [
            {"position_id": "p1", "net_profit": 20, "commission": 2, "closed_at": 110},
            {"position_id": "p2", "net_profit": -10, "commission": 1, "closed_at": 120},
            {"position_id": "p3", "net_profit": -5, "commission": 1, "closed_at": 130},
            {"position_id": "p4", "net_profit": 0, "commission": 1, "closed_at": 140},
        ], filled_order_count=5, open_position_count=1, unrealized_profit=3.5)

        self.assertEqual(result["closed_position_count"], 4)
        self.assertEqual(result["win_count"], 1)
        self.assertEqual(result["loss_count"], 2)
        self.assertEqual(result["breakeven_count"], 1)
        self.assertEqual(result["win_rate"], 25)
        self.assertEqual(result["gross_profit"], 20)
        self.assertEqual(result["gross_loss"], 15)
        self.assertEqual(result["net_profit"], 5)
        self.assertEqual(result["profit_factor"], 1.33)
        self.assertEqual(result["max_drawdown"], 15)
        self.assertEqual(result["max_consecutive_losses"], 2)
        self.assertEqual(result["open_position_count"], 1)
        self.assertEqual(result["unrealized_profit"], 3.5)

    def test_live_performance_filters_by_strategy_id_column(self):
        queries = []

        class _Storage:
            def fetchall(self, sql, params=()):
                queries.append((sql, params))
                if "FROM strategy_deployments" in sql:
                    return [{
                        "deployment_id": "dep-1", "strategy_id": "74a8b589",
                        "status": "active", "created_at": 1, "updated_at": 1,
                        "execution_mode": "live", "symbol": "GOLD#",
                        "config_json": '{"strategy_name":"GOLD M5"}',
                    }]
                if "FROM live_trade_deals" in sql:
                    return []
                if "FROM trade_execution_reports" in sql:
                    return []
                raise AssertionError(sql)

        build_live_performance(_Storage(), 1, 21, [])
        deal_sql = next(sql for sql, _ in queries if "FROM live_trade_deals" in sql)
        report_sql = next(sql for sql, _ in queries if "FROM trade_execution_reports" in sql)
        self.assertIn("strategy_id IN", deal_sql)
        self.assertNotIn("json_extract", deal_sql.lower())
        self.assertIn("strategy_id IN", report_sql)
        self.assertNotIn("json_extract", report_sql.lower())


if __name__ == "__main__":
    unittest.main()
