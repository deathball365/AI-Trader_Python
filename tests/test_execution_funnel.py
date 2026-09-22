import unittest

from routes_accounts import _execution_funnel


class _Storage:
    def __init__(self, plans=None, audits=None, deployments=None,
                 filled_orders=0, live_filled=0, timeout_orders=0):
        self.plans = plans or {"plans": 12, "directions": 8}
        self.audits = audits or []
        self.deployments = deployments if deployments is not None else [
            {"symbol": "GOLD#", "strategy_id": "s1"},
        ]
        self.filled_orders = filled_orders
        self.live_filled = live_filled
        self.timeout_orders = timeout_orders

    def fetchone(self, sql, params=()):
        if "FROM structure_trade_plans" in sql:
            return dict(self.plans)
        if "FROM paper_orders" in sql and "status='filled'" in sql:
            return {"n": self.filled_orders}
        if "FROM paper_orders" in sql and "canceled" in sql:
            return {"n": self.timeout_orders}
        if "FROM trade_execution_reports" in sql:
            return {"n": self.live_filled}
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
        ], timeout_orders=2)

        funnel = _execution_funnel(storage, 1, 22)

        self.assertEqual(funnel["plans"], 12)
        self.assertEqual(funnel["directions"], 8)
        self.assertEqual(funnel["triggered"], 8)
        self.assertEqual(funnel["risk_passed"], 2)
        self.assertEqual(funnel["ordered"], 0)
        reasons = {item["reason_code"]: item["count"] for item in funnel["blocked_reasons"]}
        self.assertEqual(reasons["claim_conflict"], 5)
        self.assertEqual(reasons["entry_guard"], 1)
        self.assertEqual(reasons["timeout"], 2)

    def test_counts_filled_orders_not_timed_out_pending(self):
        storage = _Storage(
            audits=[{"status": "ordered", "reason_code": "eligible", "n": 2, "occurrences": 2}],
            filled_orders=3,
        )
        funnel = _execution_funnel(storage, 1, 22)
        self.assertEqual(funnel["risk_passed"], 2)
        self.assertEqual(funnel["ordered"], 3)

    def test_ignores_waiting_ticks_that_are_not_persisted_as_triggers(self):
        storage = _Storage(audits=[
            {"status": "no_action", "reason_code": "no_direction", "n": 9, "occurrences": 9},
        ])

        funnel = _execution_funnel(storage, 1, 21)

        self.assertEqual(funnel["triggered"], 0)
        self.assertEqual(funnel["ordered"], 0)
        self.assertEqual(funnel["blocked_reasons"], [])

    def test_counts_reused_plans_and_skips_observation_rows(self):
        storage = _Storage()
        captured = {}
        original = storage.fetchone

        def fetchone(sql, params=()):
            if "FROM structure_trade_plans" in sql:
                captured["sql"] = sql
                captured["params"] = params
            return original(sql, params)

        storage.fetchone = fetchone
        funnel = _execution_funnel(storage, 1, 22)
        self.assertEqual(funnel["plans"], 12)
        self.assertEqual(funnel["directions"], 8)
        self.assertIn("p.setup_type<>'no_trade'", captured["sql"])
        self.assertIn("p.created_at>=? OR p.updated_at>=?", captured["sql"])
        self.assertEqual(captured["params"][-1], captured["params"][-2])

    def test_empty_deployments_do_not_query_public_plans(self):
        storage = _Storage(deployments=[])
        funnel = _execution_funnel(storage, 1, 22)
        self.assertEqual(funnel["plans"], 0)
        self.assertEqual(funnel["directions"], 0)


if __name__ == "__main__":
    unittest.main()
