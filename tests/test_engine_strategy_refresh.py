import unittest

from trading_engine_manager import EngineKey, EngineRuntime, TradingEngineManager


class _Store:
    def __init__(self):
        self.reload_count = 0

    def reload_from_storage(self):
        self.reload_count += 1


class _Engine:
    def __init__(self):
        self.strategy_service = type(
            "_StrategyService", (), {"strategy_store": _Store()}
        )()


def _runtime(engine):
    return EngineRuntime(
        engine=engine,
        last_active_at=0,
        next_pending_cleanup_at=0,
        next_signal_cleanup_at=0,
        next_llm_analysis_at=0,
    )


class _Storage:
    def __init__(self):
        self.calls = 0

    def fetchall(self, sql, params=()):
        self.calls += 1
        if "execution_mode = 'paper'" in sql or "d.execution_mode = 'paper'" in sql:
            return [{"account_id": 22, "strategy_id": "s1"}]
        return [{"account_id": 21}, {"account_id": 22}]


class EngineStrategyRefreshTests(unittest.TestCase):
    def test_refresh_user_strategies_can_target_one_account(self):
        manager = TradingEngineManager.__new__(TradingEngineManager)
        manager._lock = __import__("threading").RLock()
        manager._active_deployments = {7: {"account_ids": (21,), "paper_rows": ()}}
        manager._tick_strategy_cache = {(7, "GOLD#"): ("stale",)}
        first = _Engine()
        second = _Engine()
        manager._engines = {
            EngineKey(7, 21): _runtime(first),
            EngineKey(7, 22): _runtime(second),
            EngineKey(8, 21): _runtime(_Engine()),
        }

        manager.refresh_user_strategies(7, account_id=21)

        self.assertEqual(first.strategy_service.strategy_store.reload_count, 1)
        self.assertEqual(second.strategy_service.strategy_store.reload_count, 0)
        self.assertNotIn(7, manager._active_deployments)
        self.assertNotIn((7, "GOLD#"), manager._tick_strategy_cache)

    def test_tick_strategies_are_cached_until_strategy_refresh(self):
        manager = TradingEngineManager.__new__(TradingEngineManager)
        manager._lock = __import__("threading").RLock()
        manager._tick_strategy_cache = {}
        manager._engines = {}
        manager.paper_trading = object()
        builds = {"count": 0}

        class _Coordinator:
            def strategies_for_quote(self, user_id, account_id, symbol):
                builds["count"] += 1
                return [type("S", (), {"strategy_id": f"{account_id}-{symbol}"})()]

        class _Engine:
            def __init__(self):
                self.strategy_runtime_coordinator = _Coordinator()

        manager.get_engine = lambda user_id, account_id: _Engine()
        deployments = {"account_ids": (21,), "paper_rows": ()}
        first = manager._strategies_for_tick(7, "GOLD#", (21,), deployments)
        second = manager._strategies_for_tick(7, "GOLD#", (21,), deployments)
        self.assertIs(first, second)
        self.assertEqual(builds["count"], 1)

        manager._active_deployments = {7: deployments}
        manager.refresh_user_strategies(7)
        third = manager._strategies_for_tick(7, "GOLD#", (21,), deployments)
        self.assertIsNot(third, first)
        self.assertEqual(builds["count"], 2)

    def test_active_deployments_are_cached_until_strategy_refresh(self):
        manager = TradingEngineManager.__new__(TradingEngineManager)
        manager._lock = __import__("threading").RLock()
        manager._active_deployments = {}
        manager._tick_strategy_cache = {}
        storage = _Storage()
        manager.repositories = type("_Repos", (), {"storage": storage})()
        manager._engines = {}

        first = manager._active_deployments_for_user(7)
        second = manager._active_deployments_for_user(7)
        self.assertEqual(first["account_ids"], (21, 22))
        self.assertIs(first, second)
        self.assertEqual(storage.calls, 2)

        manager.refresh_user_strategies(7)
        third = manager._active_deployments_for_user(7)
        self.assertEqual(third["account_ids"], (21, 22))
        self.assertIsNot(third, first)
        self.assertEqual(storage.calls, 4)


if __name__ == "__main__":
    unittest.main()
