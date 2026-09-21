import unittest
from types import SimpleNamespace

from routes_accounts import _execution_funnel


class _Storage:
    def __init__(self, plans=None, audits=None, deployments=None):
        self.plans = plans or {"plans": 12, "directions": 8}
        self.audits = audits or []
        self.deployments = deployments if deployments is not None else [
            {"symbol": "GOLD#", "strategy_id": "s1"},
        ]

    def fetchone(self, sql, params=()):
        if "FROM structure_trade_plans" in sql:
            return dict(self.plans)
        raise AssertionError(sql)

    def fetchall(self, sql, params=()):
        if "FROM strategy_deployments" in sql:
            return list(self.deployments)
        if "FROM execution_gate_audits" in sql and "GROUP BY status, reason_code" in sql:
            return list(self.audits)
        raise AssertionError(sql)


class ExecutionFunnelTests(unittest.TestCase):
    def test_counts_actionable_audits_even_when_execution_table_is_empty(self):
        storage = _Storage(audits=[
            {"status": "ordered", "reason_code": "eligible", "n": 2, "occurrences": 2},
            {"status": "blocked", "reason_code": "claim_conflict", "n": 5, "occurrences": 5},
            {"status": "no_action", "reason_code": "entry_guard", "n": 1, "occurrences": 1},
            {"status": "no_action", "reason_code": "no_new_trigger", "n": 40, "occurrences": 40},
        ])

        funnel = _execution_funnel(storage, 1, 22)

        self.assertEqual(funnel["plans"], 12)
        self.assertEqual(funnel["directions"], 8)
        self.assertEqual(funnel["triggered"], 8)
        self.assertEqual(funnel["risk_passed"], 2)
        self.assertEqual(funnel["ordered"], 2)
        self.assertEqual(funnel["blocked_reasons"][0]["reason_code"], "claim_conflict")
        self.assertEqual(funnel["blocked_reasons"][0]["count"], 5)

    def test_ignores_waiting_ticks_that_are_not_persisted_as_triggers(self):
        storage = _Storage(audits=[
            {"status": "no_action", "reason_code": "no_direction", "n": 9, "occurrences": 9},
        ])

        funnel = _execution_funnel(storage, 1, 21)

        self.assertEqual(funnel["triggered"], 0)
        self.assertEqual(funnel["ordered"], 0)
        self.assertEqual(funnel["blocked_reasons"], [])


if __name__ == "__main__":
    unittest.main()
