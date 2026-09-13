import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
