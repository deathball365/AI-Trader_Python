import threading
import unittest
from collections import defaultdict
from types import SimpleNamespace

from market.risk_clock import risk_day_start_timestamp
from market.services.manual_order_limit import (
    apply_manual_order_daily_limit,
    is_manual_broker_position,
)
from server import TradingServer


class _RuntimeRepository:
    def __init__(self):
        self.entities = {}

    def get_entity(self, entity_type, entity_id):
        return self.entities.get((entity_type, entity_id))

    def upsert_entity(self, entity_type, entity_id, payload, **_kwargs):
        self.entities[(entity_type, entity_id)] = dict(payload)


class _AccountRepository:
    def __init__(self, enabled=True, limit=10):
        self.storage = None
        self.account = SimpleNamespace(
            manual_order_daily_limit_enabled=enabled,
            manual_order_daily_limit=limit,
            currency="USD",
        )

    def get_by_id(self, _user_id, _account_id):
        return self.account


class _EventRepository:
    def __init__(self):
        self.events = []

    def record(self, *args, **kwargs):
        self.events.append((args, kwargs))


class _Storage:
    def __init__(self, rows=None):
        self.rows = rows or []

    def fetchall(self, sql, params=()):
        return list(self.rows)


class ManualOrderLimitTests(unittest.TestCase):
    def _server(self):
        server = object.__new__(TradingServer)
        server.lock = threading.RLock()
        server._runtime_repository = _RuntimeRepository()
        server._close_position_instructions = defaultdict(list)
        return server

    def test_empty_comment_is_manual_ait_is_not(self):
        self.assertTrue(is_manual_broker_position({"comment": ""}))
        self.assertTrue(is_manual_broker_position({"comment": "  "}))
        self.assertFalse(is_manual_broker_position({"comment": "AIT|4a926e4f|abc"}))
        self.assertFalse(is_manual_broker_position({"comment": "", "magic": 123456}))

    def test_eleventh_manual_position_is_closed(self):
        started = risk_day_start_timestamp()
        history = [
            {"position_id": index, "opened_at": started + index}
            for index in range(1, 11)
        ]
        server = self._server()
        events = _EventRepository()
        tickets = apply_manual_order_daily_limit(
            server, user_id=1, account_id=21, symbol="GOLD#",
            positions=[{
                "ticket": 99, "symbol": "US100Cash#", "comment": "",
                "open_timestamp": started + 20, "volume": 0.1, "profit": -1,
            }],
            account_repository=_AccountRepository(),
            event_repository=events,
            storage=_Storage(history),
        )
        self.assertEqual(tickets, [99])
        queued = server._close_position_instructions["US100Cash#"][0]
        self.assertEqual(queued["instruction_id"], "manual-order-limit-21-99")
        self.assertEqual(len(events.events), 1)

    def test_first_ten_manual_positions_are_kept(self):
        started = risk_day_start_timestamp()
        history = [
            {"position_id": 99, "opened_at": started + 1},
        ]
        server = self._server()
        events = _EventRepository()
        tickets = apply_manual_order_daily_limit(
            server, user_id=1, account_id=21, symbol="GOLD#",
            positions=[{
                "ticket": 99, "symbol": "GOLD#", "comment": "",
                "open_timestamp": started + 1,
            }],
            account_repository=_AccountRepository(),
            event_repository=events,
            storage=_Storage(history),
        )
        self.assertEqual(tickets, [])
        self.assertEqual(events.events, [])

    def test_strategy_comment_is_ignored(self):
        started = risk_day_start_timestamp()
        history = [
            {"position_id": index, "opened_at": started + index}
            for index in range(1, 12)
        ]
        server = self._server()
        tickets = apply_manual_order_daily_limit(
            server, user_id=1, account_id=21, symbol="GOLD#",
            positions=[{
                "ticket": 77, "symbol": "GOLD#",
                "comment": "AIT|4a926e4f|abc",
                "open_timestamp": started + 30,
            }],
            account_repository=_AccountRepository(),
            event_repository=_EventRepository(),
            storage=_Storage(history),
        )
        self.assertEqual(tickets, [])

    def test_disabled_limit_does_not_close(self):
        started = risk_day_start_timestamp()
        history = [
            {"position_id": index, "opened_at": started + index}
            for index in range(1, 12)
        ]
        server = self._server()
        tickets = apply_manual_order_daily_limit(
            server, user_id=1, account_id=21, symbol="GOLD#",
            positions=[{
                "ticket": 99, "symbol": "GOLD#", "comment": "",
                "open_timestamp": started + 20,
            }],
            account_repository=_AccountRepository(enabled=False),
            event_repository=_EventRepository(),
            storage=_Storage(history),
        )
        self.assertEqual(tickets, [])
