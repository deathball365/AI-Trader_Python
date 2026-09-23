import unittest
from datetime import datetime
from unittest.mock import patch

from market.models.statistics import StatisticsData
from market.store.statistics_store import StatisticsStore


class _RuntimeRepository:
    snapshots = {}

    def __init__(self, user_id, account_id):
        self.user_id = int(user_id or 0)
        self.account_id = int(account_id or 0)

    def migrate_scope(self, account_id):
        self.account_id = int(account_id or 0)

    def set_scope(self, user_id, account_id):
        self.user_id = int(user_id or 0)
        self.account_id = int(account_id or 0)

    def get_entity(self, entity_type, entity_id):
        return self.snapshots.get((self.user_id, self.account_id, entity_type, entity_id))

    def list_entities(self, entity_type):
        item = self.get_entity(entity_type, "latest")
        return [item] if item else []


class StatisticsStoreScopeTests(unittest.TestCase):
    def test_set_scope_restores_durable_account_snapshot(self):
        snapshot = {
            "balance": 518.73, "equity": 522.30,
            "free_margin": 522.30, "margin": 0.0,
            "margin_level": 0.0, "updated_at": "2026-09-13T00:00:00+00:00",
        }
        _RuntimeRepository.snapshots[(7, 21, "account_snapshot", "latest")] = snapshot
        with patch("market.store.statistics_store.RuntimeStateRepository", _RuntimeRepository):
            store = StatisticsStore()
            store.set_scope(7, 21)
        self.assertEqual(store.get_account_info(), snapshot)

    def test_get_spread_uses_per_symbol_mt5_report(self):
        store = StatisticsStore(max_per_symbol=10, max_total=100)
        store.add(StatisticsData(
            symbol="GOLD#", timestamp=datetime.now(),
            bid_price=2650.10, ask_price=2650.28, spread=0.18, spread_points=18,
            balance=1000, equity=1000, margin_level=0,
        ))
        store.add(StatisticsData(
            symbol="BTCUSD#", timestamp=datetime.now(),
            bid_price=80000.0, ask_price=80012.0, spread=12.0, spread_points=12,
            balance=1000, equity=1000, margin_level=0,
        ))
        self.assertEqual(store.get_spread("gold#"), 0.18)
        self.assertEqual(store.get_spread("BTCUSD#"), 12.0)


    def test_spread_only_heartbeat_does_not_erase_account_snapshot(self):
        store = StatisticsStore()
        owner = StatisticsData.from_ea_data({
            "symbol": "USDJPY#",
            "timestamp": "2026-09-23 22:00:00",
            "bidPrice": 157.9,
            "askPrice": 157.91,
            "balance": 192.4,
            "equity": 188.1,
            "freeMargin": 120.0,
            "margin": 68.1,
            "marginLevel": 276.2,
        })
        store.add(owner)
        child = StatisticsData.from_ea_data({
            "symbol": "GOLD#",
            "timestamp": "2026-09-23 22:00:30",
            "bidPrice": 4301.1,
            "askPrice": 4301.4,
            "tickCount": 80,
            "spread": 0.3,
            "spreadPoints": 30,
        })
        store.add(child)
        info = store.get_account_info()
        self.assertFalse(child.has_account_snapshot)
        self.assertEqual(info["balance"], 192.4)
        self.assertEqual(info["equity"], 188.1)
        self.assertEqual(info["free_margin"], 120.0)
        self.assertEqual(store.get_spread("GOLD#"), 0.3)

    def test_get_spread_falls_back_to_ask_minus_bid(self):
        store = StatisticsStore()
        store.add(StatisticsData(
            symbol="AUDUSD#", timestamp=datetime.now(),
            bid_price=0.71120, ask_price=0.71138, spread=0.0, spread_points=0,
            balance=1000, equity=1000, margin_level=0,
        ))
        self.assertAlmostEqual(store.get_spread("AUDUSD#"), 0.00018, places=8)


if __name__ == "__main__":
    unittest.main()
