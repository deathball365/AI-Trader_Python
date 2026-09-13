import unittest
from types import SimpleNamespace

from market.services.structure_plan_execution_coordinator import StructurePlanExecutionCoordinator


class StagedExecutionTests(unittest.TestCase):
    def decision(self, stage="breakout", action="buy"):
        return SimpleNamespace(action=action, symbol="BTCUSD#", signal_summary={
            "selected_trade_opportunity_stage": stage,
            "selected_trade_opportunity_id": "opportunity-1",
            "selected_signal_period": "M15",
        })

    @staticmethod
    def coordinator(plans=None, executions=None, position_ids=None):
        class _Repository:
            def __init__(self):
                self.opportunity_calls = []

            def list_opportunity(self, *args, **kwargs):
                self.opportunity_calls.append((args, kwargs))
                return list(plans or [])

            def list_executions(self, *_args, **_kwargs):
                return list(executions or [])

            def list_filled_position_ids(self, *_args, **_kwargs):
                return list(position_ids or [])

        return StructurePlanExecutionCoordinator(_Repository(), None)

    def test_breakout_without_initial_plan_is_direct_entry(self):
        decision = self.decision()
        result = self.coordinator().validate_stage(
            decision, [], user_id=7, account_id=22, deployment_id="dep-1",
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(result["entry_role"], "direct_breakout")

    def test_breakout_waits_for_same_opportunity_initial_receipt(self):
        coordinator = self.coordinator(plans=[{
            "plan_id": "initial-1", "opportunity_stage": "initial",
        }])
        result = coordinator.validate_stage(
            self.decision(), [{"direction": "buy", "sl": 99.0}],
            user_id=7, account_id=22, deployment_id="dep-1",
        )
        self.assertFalse(result["allowed"])
        self.assertIn("同一机会", result["reason"])

    def test_other_opportunity_position_cannot_unlock_breakout(self):
        coordinator = self.coordinator(
            plans=[{"plan_id": "initial-1", "opportunity_stage": "initial"}],
            executions=[{
                "plan_id": "other-initial", "account_id": 22,
                "deployment_id": "dep-1", "plan_stage": "initial",
                "direction": "buy", "status": "filled",
            }],
        )
        result = coordinator.validate_stage(
            self.decision(), [{"direction": "buy", "sl": 99.0}],
            user_id=7, account_id=22, deployment_id="dep-1",
        )
        self.assertFalse(result["allowed"])

    def test_matching_initial_fill_and_protection_unlocks_breakout(self):
        execution = {
            "plan_id": "initial-1", "account_id": 22,
            "deployment_id": "dep-1", "plan_stage": "initial",
            "direction": "buy", "status": "filled", "order_id": "order-1",
        }
        coordinator = self.coordinator(
            plans=[{"plan_id": "initial-1", "opportunity_stage": "initial"}],
            executions=[execution], position_ids=[101],
        )
        unprotected = coordinator.validate_stage(
            self.decision(), [{"ticket": 101, "direction": "buy", "sl": 0}],
            user_id=7, account_id=22, deployment_id="dep-1",
        )
        self.assertFalse(unprotected["allowed"])
        result = coordinator.validate_stage(
            self.decision(), [{"ticket": 101, "direction": "buy", "sl": 99.0}],
            user_id=7, account_id=22, deployment_id="dep-1",
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(result["entry_role"], "add_on")

    def test_unrelated_same_direction_position_cannot_unlock_breakout(self):
        coordinator = self.coordinator(
            plans=[{"plan_id": "initial-1", "opportunity_stage": "initial"}],
            executions=[{
                "plan_id": "initial-1", "account_id": 22,
                "deployment_id": "dep-1", "plan_stage": "initial",
                "direction": "buy", "status": "filled", "order_id": "order-1",
            }],
            position_ids=[101],
        )
        result = coordinator.validate_stage(
            self.decision(), [{"ticket": 202, "direction": "buy", "sl": 99.0}],
            user_id=7, account_id=22, deployment_id="dep-1",
        )
        self.assertFalse(result["allowed"])

    def test_opportunity_lookup_is_scoped_to_symbol_and_period(self):
        coordinator = self.coordinator()
        coordinator.validate_stage(
            self.decision(), [], user_id=7, account_id=22, deployment_id="dep-1",
        )
        _, kwargs = coordinator.repository.opportunity_calls[0]
        self.assertEqual(kwargs, {"symbol": "BTCUSD#", "period": "M15"})

    def test_initial_stage_does_not_require_existing_position(self):
        result = self.coordinator().validate_stage(self.decision("initial"), [])
        self.assertTrue(result["allowed"])

    def test_claim_uses_explicit_deployment_mode_stage_and_direction(self):
        calls = []

        class _ExecutionService:
            def claim(self, **kwargs):
                calls.append(kwargs)
                return True

        coordinator = StructurePlanExecutionCoordinator(None, _ExecutionService())
        decision = SimpleNamespace(
            strategy_id="strategy-1", action="buy", decision_reason="triggered",
            signal_summary={
                "selected_trade_plan_id": "plan-1",
                "selected_trade_plan_group_id": "group-1",
                "selected_trade_opportunity_stage": "breakout",
            },
        )
        context = coordinator.claim_for_decision(
            7, 22, decision, deployment_id="deployment-1",
            execution_mode="paper", tick_id="tick-1",
        )

        self.assertTrue(context["claimed"])
        self.assertEqual(calls[0]["deployment_id"], "deployment-1")
        self.assertEqual(calls[0]["execution_mode"], "paper")
        self.assertEqual(calls[0]["tick_id"], "tick-1")
        self.assertEqual(calls[0]["plan"]["plan_stage"], "breakout")
        self.assertEqual(calls[0]["plan"]["direction"], "buy")

if __name__ == "__main__":
    unittest.main()
