import unittest
from datetime import datetime

from market.models.trading_strategy import TradingStrategy
from market.services.tick_execution_context import TickExecutionContext
from paper_trading import PaperTradingService


class TradingStrategyDatetimeTests(unittest.TestCase):
    def test_unix_updated_at_survives_to_dict(self):
        strategy = TradingStrategy.from_dict({
            "symbol": "GOLD#",
            "strategy_id": "74a8b589",
            "strategy_name": "GOLD M5",
            "created_at": 1750000000,
            "updated_at": 1750000000,
        })
        payload = strategy.to_dict()
        self.assertIsInstance(strategy.updated_at, datetime)
        self.assertIsInstance(payload["updated_at"], str)
        self.assertTrue(payload["updated_at"])

    def test_isoformat_round_trip_still_works(self):
        original = datetime(2026, 9, 6, 21, 10, 7)
        strategy = TradingStrategy.from_dict({
            "symbol": "GOLD#",
            "created_at": original.isoformat(),
            "updated_at": original.isoformat(),
        })
        self.assertEqual(strategy.created_at, original)
        self.assertEqual(strategy.to_dict()["created_at"], original.isoformat())


class _Audits:
    def __init__(self):
        self.rows = []

    def record(self, **kwargs):
        self.rows.append(kwargs)


class PaperStrategyLoadIsolationTests(unittest.TestCase):
    def test_one_broken_strategy_does_not_abort_later_deployments(self):
        service = PaperTradingService.__new__(PaperTradingService)
        service._expire_deployments = lambda *args, **kwargs: None
        service.matching_engine = type(
            "_Engine",
            (),
            {"expire_stale_pending_orders": staticmethod(lambda *args, **kwargs: None)},
        )()
        service.execution_gate_audits = _Audits()
        loaded = []

        def _load(user_id, deployment):
            if deployment["strategy_id"] == "74a8b589":
                raise AttributeError("'int' object has no attribute 'isoformat'")
            loaded.append(deployment["strategy_id"])
            return {
                "strategy_id": deployment["strategy_id"],
                "symbol": "GOLD#",
                "strategy_name": "ok",
            }

        service._deployment_strategy = _load
        service._strategy_matches_quote = lambda *args, **kwargs: False
        service.storage = type("_Storage", (), {
            "fetchall": staticmethod(lambda *args, **kwargs: [
                {
                    "strategy_id": "74a8b589", "account_id": 22,
                    "deployment_id": "d-bad", "account_status": "active",
                    "account_enabled": 1, "account_trading_enabled": 1,
                    "account_auto_trading_enabled": 1,
                },
                {
                    "strategy_id": "f06120e2", "account_id": 22,
                    "deployment_id": "d-good", "account_status": "active",
                    "account_enabled": 1, "account_trading_enabled": 1,
                    "account_auto_trading_enabled": 1,
                },
            ]),
        })()
        context = TickExecutionContext.create(
            user_id=1, source_account_id=21, symbol="GOLD#",
            price=4300.0, captured_at=1.0, signals_by_strategy={},
        )

        created = service.process_strategy_signals(
            1, "GOLD#", 4300.0, strategy_service=None,
            quote_account_id=21, execution_context=context,
        )

        self.assertEqual(created, 0)
        self.assertEqual(loaded, ["f06120e2"])
        self.assertEqual(
            [row["reason_code"] for row in service.execution_gate_audits.rows],
            ["strategy_load_failed"],
        )
