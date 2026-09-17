import threading
import unittest
from collections import defaultdict
from types import SimpleNamespace

from routes_position import _apply_single_position_loss_limit
from server import TradingServer


class CloseInstructionDeliveryTestCase(unittest.TestCase):
    def _server(self):
        server = object.__new__(TradingServer)
        server.lock = threading.RLock()
        server._runtime_repository = None
        server._close_position_instructions = defaultdict(list)
        server._position_update_instructions = defaultdict(dict)
        server._position_partial_instructions = defaultdict(dict)
        server._live_entries_allowed = lambda: True
        server.trading_instruction_service = SimpleNamespace(
            fetch_instructions_for_ea=lambda _symbol, _price: [],
        )
        server.pending_order_service = SimpleNamespace(
            get_pending_orders_dict=lambda _symbol: [],
        )
        return server

    def test_legacy_integer_close_instruction_is_normalized(self):
        server = self._server()
        server._close_position_instructions["GOLD#"].append(1001)

        details = server.get_close_position_instruction_details("GOLD#")

        self.assertEqual(details, [{
            "symbol": "GOLD#",
            "ticket": 1001,
            "instruction_id": "position-close-1001",
            "run_id": "",
        }])

    def test_ea_response_returns_ticket_numbers_and_instruction_details(self):
        server = self._server()
        server._close_position_instructions["GOLD#"].append({
            "symbol": "GOLD#",
            "ticket": 1002,
            "instruction_id": "flatten-test-1002",
            "run_id": "run-test",
        })

        result = server.get_trades_by_symbol(
            "GOLD#", evaluate_price=False,
        )

        self.assertEqual(result["close_tickets"], [1002])
        self.assertEqual(result["close_instructions"][0]["ticket"], 1002)
        self.assertEqual(
            result["close_instructions"][0]["instruction_id"],
            "flatten-test-1002",
        )


class _RuntimeRepository:
    def __init__(self):
        self.entities = {}

    def get_entity(self, entity_type, entity_id):
        return self.entities.get((entity_type, entity_id))

    def upsert_entity(self, entity_type, entity_id, payload, **_kwargs):
        self.entities[(entity_type, entity_id)] = dict(payload)

    def list_entities(self, entity_type, statuses=None, limit=None):
        rows = [
            dict(payload) for (kind, _entity_id), payload in self.entities.items()
            if kind == entity_type
            and (not statuses or payload.get("status") in statuses)
        ]
        return rows[:limit] if limit else rows


class _AccountRepository:
    def __init__(self, enabled=True, amount=30):
        self.account = SimpleNamespace(
            single_position_loss_limit_enabled=enabled,
            single_position_loss_limit_amount=amount,
            currency="USD",
        )

    def get_by_id(self, _user_id, _account_id):
        return self.account


class _EventRepository:
    def __init__(self):
        self.events = []

    def record(self, *args, **kwargs):
        self.events.append((args, kwargs))


class _InstrumentSpecRepository:
    def __init__(self, spec=None):
        self.spec = spec or {}

    def get(self, _account_id, _symbol):
        return dict(self.spec)


class SinglePositionLossLimitTestCase(unittest.TestCase):
    def _server(self):
        server = object.__new__(TradingServer)
        server.lock = threading.RLock()
        server._runtime_repository = _RuntimeRepository()
        server._close_position_instructions = defaultdict(list)
        return server

    def test_limit_is_inclusive_and_deduplicates_repeated_snapshot(self):
        server = self._server()
        events = _EventRepository()
        positions = [{
            "ticket": 2001,
            "profit": -30,
            "volume": 0.01,
            "priceCurrent": 100,
        }]

        first = _apply_single_position_loss_limit(
            server, user_id=1, account_id=2, symbol="SILVER#",
            positions=positions,
            account_repository=_AccountRepository(),
            event_repository=events,
        )
        second = _apply_single_position_loss_limit(
            server, user_id=1, account_id=2, symbol="SILVER#",
            positions=positions,
            account_repository=_AccountRepository(),
            event_repository=events,
        )

        self.assertEqual(first, [2001])
        self.assertEqual(second, [2001])
        self.assertEqual(len(server._close_position_instructions["SILVER#"]), 1)
        self.assertEqual(len(events.events), 1)
        queued = server._close_position_instructions["SILVER#"][0]
        self.assertEqual(queued["instruction_id"], "position-loss-limit-2-2001")

    def test_below_threshold_or_disabled_does_not_queue_close(self):
        server = self._server()
        events = _EventRepository()

        near_limit = _apply_single_position_loss_limit(
            server, user_id=1, account_id=2, symbol="SILVER#",
            positions=[{"ticket": 2002, "profit": -29.99}],
            account_repository=_AccountRepository(),
            event_repository=events,
        )
        disabled = _apply_single_position_loss_limit(
            server, user_id=1, account_id=2, symbol="SILVER#",
            positions=[{"ticket": 2003, "profit": -100}],
            account_repository=_AccountRepository(enabled=False),
            event_repository=events,
        )

        self.assertEqual(near_limit, [])
        self.assertEqual(disabled, [])
        self.assertEqual(server._close_position_instructions["SILVER#"], [])
        self.assertEqual(events.events, [])


class PositionUpdateDeliveryTestCase(unittest.TestCase):
    def _server(self):
        server = object.__new__(TradingServer)
        server.user_id = 1
        server.account_id = 21
        server.lock = threading.RLock()
        server._runtime_repository = _RuntimeRepository()
        server._position_update_instructions = defaultdict(dict)
        server._managed_position_state = {}
        server._position_event_repository = _EventRepository()
        server.instrument_specs = _InstrumentSpecRepository({
            "price_digits": 2, "tick_size": 0.01, "point_size": 0.01,
        })
        return server

    def test_update_remains_deliverable_until_success_receipt(self):
        server = self._server()
        server._managed_position_state[2083797050] = {"direction": "buy"}
        instruction = server._queue_position_update_instruction(
            "GOLD#", {"ticket": 2083797050, "sl": 4361.674, "tp": 0,
                       "reason": "position_management"},
            events=[{"status": "triggered", "rule_type": "trailing_stop",
                     "candidate_stop_loss": 4361.674}],
        )

        first = server._deliver_position_update_instructions("GOLD#")
        persisted = server._runtime_repository.get_entity(
            "position_update_instruction", instruction["instruction_id"],
        )

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["sl"], 4361.68)
        self.assertEqual(first[0]["instruction_id"], instruction["instruction_id"])
        self.assertEqual(persisted["status"], "delivered")
        self.assertIn(2083797050, server._position_update_instructions["GOLD#"])

        persisted["last_delivered_at"] = 0
        server._runtime_repository.upsert_entity(
            "position_update_instruction", instruction["instruction_id"], persisted,
        )
        server._position_update_instructions["GOLD#"][2083797050] = persisted
        retried = server._deliver_position_update_instructions("GOLD#")
        self.assertEqual(len(retried), 1)
        self.assertEqual(retried[0]["instruction_id"], instruction["instruction_id"])

    def test_success_receipt_completes_and_removes_pending_update(self):
        server = self._server()
        server._managed_position_state[2083797050] = {"direction": "buy"}
        instruction = server._queue_position_update_instruction(
            "GOLD#", {"ticket": 2083797050, "sl": 4361.674, "tp": 0},
        )
        server._managed_position_state[2083797050].update({
            "pending_stop_loss": 4361.674,
            "pending_stop_instruction_id": instruction["instruction_id"],
            "stop_loss": 4355.01,
        })

        result = server.apply_position_update_execution_report({
            "instruction_id": instruction["instruction_id"],
            "action": "position_modify_sl", "success": True,
            "symbol": "GOLD#", "mt5_position_id": 2083797050,
            "executed_price": 4361.68, "retcode": 10009,
        })

        self.assertEqual(result["status"], "executed")
        self.assertNotIn(2083797050, server._position_update_instructions["GOLD#"])
        state = server._managed_position_state[2083797050]
        self.assertEqual(state["stop_loss"], 4361.68)
        self.assertEqual(state["pending_stop_loss"], 0)
        self.assertEqual(len(server._position_event_repository.events), 1)

    def test_sell_stop_uses_account_symbol_tick_and_price_digits(self):
        server = self._server()
        server._managed_position_state[2002] = {"direction": "sell"}

        instruction = server._queue_position_update_instruction(
            "GOLD#", {"ticket": 2002, "sl": 4361.679, "tp": 0},
        )

        self.assertEqual(instruction["sl"], 4361.67)
        self.assertIn("-4361.67000000-", instruction["instruction_id"])

    def test_invalid_stops_receipt_is_retried_instead_of_lost(self):
        server = self._server()
        server._managed_position_state[2003] = {
            "direction": "buy", "stop_loss": 4355.01,
            "pending_stop_loss": 4361.68,
        }
        instruction = server._queue_position_update_instruction(
            "GOLD#", {"ticket": 2003, "sl": 4361.674, "tp": 0},
        )

        result = server.apply_position_update_execution_report({
            "instruction_id": instruction["instruction_id"],
            "action": "position_modify_sl", "success": False,
            "symbol": "GOLD#", "mt5_position_id": 2003,
            "executed_price": 4355.01, "retcode": 10016,
            "error_message": "Invalid stops",
        })

        self.assertEqual(result["status"], "pending")
        self.assertIn(2003, server._position_update_instructions["GOLD#"])
        self.assertEqual(
            server._position_update_instructions["GOLD#"][2003]["last_delivered_at"], 0,
        )


if __name__ == "__main__":
    unittest.main()
