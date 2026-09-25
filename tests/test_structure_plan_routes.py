import unittest

from routes_structure_plans import assemble_structure_plan_execution


class StructurePlanRouteAssemblyTests(unittest.TestCase):
    def test_active_deployment_remains_visible_when_account_trading_is_disabled(self):
        plans = [{"plan_id": "plan-1"}]
        strategies = [{
            "strategy_id": "strategy-1",
            "strategy_name": "BTC M5",
            "period": "M5",
            "deployments": [],
        }]
        deployments = [{
            "deployment_id": "dep-1",
            "strategy_id": "strategy-1",
            "account_id": 22,
            "account_name": "ULTRAPAPER",
            "account_type": "paper",
            "execution_mode": "paper",
            "status": "active",
            "symbol": "BTCUSD#",
            "enabled": 1,
            "trading_enabled": 0,
            "auto_trading_enabled": 1,
        }]
        audits = [{
            "deployment_id": "dep-1",
            "plan_id": "plan-1",
            "status": "blocked",
            "reason_code": "trading_disabled",
            "tick_id": "tick-1",
            "updated_at": 100,
        }]

        result = assemble_structure_plan_execution(
            plans, strategies, deployments, [], audits, "BTCUSD#",
        )

        matrix = result[0]["execution_matrix"]
        self.assertEqual(len(matrix), 1)
        self.assertTrue(matrix[0]["active"])
        self.assertFalse(matrix[0]["account_execution_enabled"])
        self.assertEqual(matrix[0]["gate_reason_code"], "trading_disabled")
        self.assertEqual(result[0]["subscription_summary"]["expected_count"], 0)
        self.assertEqual(result[0]["subscription_summary"]["consumed_count"], 0)

    def test_expected_count_skips_accounts_with_trading_disabled(self):
        plans = [{"plan_id": "plan-1"}]
        strategies = [{
            "strategy_id": "strategy-1",
            "strategy_name": "AUDUSD M1",
            "period": "M1",
            "deployments": [],
        }]
        deployments = [
            {
                "deployment_id": "paper-1",
                "strategy_id": "strategy-1",
                "account_id": 22,
                "account_name": "ULTRAPAPER",
                "account_type": "paper",
                "execution_mode": "paper",
                "status": "active",
                "symbol": "AUDUSD#",
                "enabled": 1,
                "trading_enabled": 1,
                "auto_trading_enabled": 1,
            },
            {
                "deployment_id": "live-1",
                "strategy_id": "strategy-1",
                "account_id": 21,
                "account_name": "MT5",
                "account_type": "live",
                "execution_mode": "live",
                "status": "active",
                "symbol": "AUDUSD#",
                "enabled": 1,
                "trading_enabled": 1,
                "auto_trading_enabled": 0,
            },
        ]
        executions = [{
            "plan_id": "plan-1",
            "deployment_id": "paper-1",
            "status": "filled",
            "order_id": "ord-1",
            "reason": "",
            "updated_at": 100,
        }]
        result = assemble_structure_plan_execution(
            plans, strategies, deployments, executions, [], "AUDUSD#",
        )
        summary = result[0]["subscription_summary"]
        self.assertEqual(summary["expected_count"], 1)
        self.assertEqual(summary["consumed_count"], 1)
        self.assertEqual(summary["unconsumed_count"], 0)
        self.assertEqual(summary["deployment_count"], 2)


if __name__ == "__main__":
    unittest.main()
