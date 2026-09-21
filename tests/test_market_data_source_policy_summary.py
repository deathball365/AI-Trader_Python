import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from market_data_source_policy import MarketDataSourcePolicy


class MarketDataSourcePolicySummaryTests(unittest.TestCase):
    def setUp(self):
        self.policy = MarketDataSourcePolicy.__new__(MarketDataSourcePolicy)
        self.policy.CACHE_SECONDS = 30
        self.policy.SOURCE_HEARTBEAT_TTL = 180
        self.policy.storage = MagicMock()
        self.policy.accounts = MagicMock()
        self.policy.mappings = MagicMock()
        self.policy.mappings.broker_name_from_server.return_value = "xmglobal"
        self.policy._cache = {}
        self.policy._lock = __import__("threading").RLock()
        self.policy._mapping_cache = MagicMock()

    def test_summarize_account_prefers_primary_over_stale_reuse_sources(self):
        self.policy.storage.fetchall.side_effect = [
            [
                {
                    "canonical_symbol": "BTCUSD#",
                    "mode": "primary",
                    "broker_name": "xmglobal",
                    "primary_account_id": 21,
                    "conflict_symbols_json": "[]",
                    "updated_at": 200,
                },
                {
                    "canonical_symbol": "GOLD#",
                    "mode": "reuse",
                    "broker_name": "xmglobal",
                    "primary_account_id": 1,
                    "conflict_symbols_json": "[]",
                    "updated_at": 100,
                },
            ],
            [
                {"canonical_symbol": "BTCUSD#", "broker_name": "xmglobal"},
                {"canonical_symbol": "SILVER#", "broker_name": "xmglobal"},
            ],
        ]
        summary = self.policy.summarize_account(1, 21)
        self.assertEqual(summary["mode"], "primary")
        self.assertEqual(summary["primary_symbol_count"], 2)
        self.assertIn("SILVER#", summary["primary_symbols"])
        self.assertTrue(summary["is_market_primary"])

    def test_activation_notice_does_not_force_account_level_reuse(self):
        now = int(time.time())
        account = SimpleNamespace(
            account_id=21,
            account_name="MT5 68906652",
            account_type="mt5",
            status="active",
            enabled=True,
            mt5_server="XMGlobal-MT5 5",
            last_seen_at=now,
            activated_at=now,
            created_at=now,
        )
        stale_peer = SimpleNamespace(
            account_id=1,
            account_name="MT5 68806776",
            account_type="mt5",
            status="active",
            enabled=True,
            mt5_server="XMGlobal-MT5 5",
            last_seen_at=now - 10_000,
            activated_at=now - 20_000,
            created_at=now - 20_000,
        )
        self.policy.accounts.get_by_id.return_value = account
        self.policy.accounts.list_for_user.return_value = [account, stale_peer]
        self.policy.storage.fetchall.side_effect = [[], []]

        notice = self.policy.activation_notice(1, 21)
        self.assertEqual(notice["mode"], "pending")
        self.assertNotEqual(notice["mode"], "reuse")
        self.assertIn("掉线", notice["message"])

    def test_activation_notice_uses_symbol_summary_when_primary(self):
        now = int(time.time())
        account = SimpleNamespace(
            account_id=21,
            account_name="MT5 68906652",
            account_type="mt5",
            status="active",
            enabled=True,
            mt5_server="XMGlobal-MT5 5",
            last_seen_at=now,
            activated_at=now,
            created_at=now,
        )
        self.policy.accounts.get_by_id.return_value = account
        self.policy.storage.fetchall.side_effect = [
            [
                {
                    "canonical_symbol": "BTCUSD#",
                    "mode": "primary",
                    "broker_name": "xmglobal",
                    "primary_account_id": 21,
                    "conflict_symbols_json": "[]",
                    "updated_at": 200,
                }
            ],
            [{"canonical_symbol": "BTCUSD#", "broker_name": "xmglobal"}],
        ]
        notice = self.policy.activation_notice(1, 21)
        self.assertEqual(notice["mode"], "primary")
        self.assertEqual(notice["primary_symbol_count"], 1)


if __name__ == "__main__":
    unittest.main()
