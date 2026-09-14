from datetime import datetime, timedelta
import unittest

from market.models.trading_instruction import TradingInstruction
from market.store.trading_instruction_store import TradingInstructionStore
from mysql_repositories import TradeExecutionRepository


def _instruction(instruction_id="inst-1"):
    return TradingInstruction(
        instruction_id=instruction_id,
        symbol="BTCUSD#",
        action="b",
        price=100.0,
        mount=0.01,
    )


class TradingInstructionDeliveryTest(unittest.TestCase):
    def test_delivery_retains_same_instruction_until_execution_receipt(self):
        store = TradingInstructionStore()
        instruction = _instruction()
        store.add_instruction(instruction)

        first = store.fetch_and_remove_by_symbol("BTCUSD#", 100.0)

        self.assertEqual([item["instruction_id"] for item in first], ["inst-1"])
        self.assertEqual(store.get_instruction_by_id("inst-1").status, "delivered")
        self.assertEqual(store.fetch_and_remove_by_symbol("BTCUSD#", 100.0), [])

        instruction.last_delivery_at = datetime.now() - timedelta(seconds=16)
        retry = store.fetch_and_remove_by_symbol("BTCUSD#", 95.0)

        self.assertEqual([item["instruction_id"] for item in retry], ["inst-1"])
        self.assertEqual(instruction.delivery_attempts, 2)

        self.assertTrue(store.mark_execution_report("inst-1", True))
        self.assertIsNone(store.get_instruction_by_id("inst-1"))
        self.assertEqual(store.fetch_and_remove_by_symbol("BTCUSD#", 110.0), [])


    def test_delivery_times_out_after_bounded_retries(self):
        store = TradingInstructionStore()
        instruction = _instruction("inst-timeout")
        instruction.status = "delivered"
        instruction.delivery_attempts = store.MAX_DELIVERY_ATTEMPTS
        instruction.last_delivery_at = datetime.now() - timedelta(seconds=16)
        store.add_instruction(instruction)

        self.assertEqual(store.fetch_and_remove_by_symbol("BTCUSD#", 100.0), [])
        self.assertEqual(store.get_instruction_by_id("inst-timeout").status, "timeout")


    def test_delivery_metadata_survives_model_round_trip(self):
        instruction = _instruction()
        instruction.status = "delivered"
        instruction.delivery_attempts = 2
        instruction.last_delivery_at = datetime.now()

        restored = TradingInstruction.from_dict(instruction.to_full_dict())

        self.assertEqual(restored.status, "delivered")
        self.assertEqual(restored.delivery_attempts, 2)
        self.assertIsNotNone(restored.last_delivery_at)


class _ExistingExecutionStorage:
    def __init__(self):
        self.execute_called = False

    def fetchone(self, _sql, _params=()):
        if "FROM trade_execution_reports" not in _sql:
            return None
        return {
            "instruction_id": "inst-duplicate",
            "execution_status": "filled",
            "success": 1,
            "payload_json": "{}",
            "position_attribution_json": "{}",
        }

    def execute(self, _sql, _params=()):
        self.execute_called = True


class _PendingExecutionStorage(_ExistingExecutionStorage):
    def __init__(self):
        super().__init__()
        self.row = {
            "instruction_id": "paper:o1",
            "execution_status": "pending",
            "success": 0,
            "requested_price": 100.0,
            "payload_json": "{}",
            "position_attribution_json": "{}",
        }

    def fetchone(self, sql, params=()):
        if "SELECT * FROM trade_execution_reports" in sql:
            return self.row
        return None

    def execute(self, sql, params=()):
        self.execute_called = True
        if "UPDATE trade_execution_reports" in sql:
            self.row.update({
                "execution_status": params[1],
                "success": params[0],
                "executed_price": params[2],
                "executed_volume": params[3],
                "payload_json": params[11],
            })


class TradeExecutionReceiptIdempotencyTest(unittest.TestCase):
    def test_duplicate_receipt_does_not_write_a_second_execution(self):
        storage = _ExistingExecutionStorage()
        result = TradeExecutionRepository(storage).record(
            1, 2, {"instruction_id": "inst-duplicate", "success": True}
        )

        self.assertTrue(result["duplicate"])
        self.assertEqual(result["status"], "filled")
        self.assertFalse(storage.execute_called)

    def test_pending_receipt_is_upgraded_by_paper_fill(self):
        storage = _PendingExecutionStorage()
        result = TradeExecutionRepository(storage).record(
            1, 2, {
                "instruction_id": "paper:o1", "order_id": "o1",
                "symbol": "BTCUSD", "action": "buy", "success": True,
                "status": "filled", "requested_price": 100,
                "executed_price": 101, "requested_volume": 0.1,
                "executed_volume": 0.1, "transport": "paper",
            },
        )

        self.assertTrue(storage.execute_called)
        self.assertTrue(result["upgraded"])
        self.assertEqual(result["status"], "filled")
        self.assertEqual(storage.row["execution_status"], "filled")
