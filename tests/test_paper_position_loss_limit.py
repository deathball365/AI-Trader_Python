import sqlite3
import unittest
from types import SimpleNamespace

from market.services.paper_position_service import PaperPositionService


class _PositionEvents:
    def __init__(self):
        self.events = []

    def record(self, *args, **kwargs):
        self.events.append((args, kwargs))


class PaperPositionLossLimitTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = lambda cursor, row: {
            description[0]: row[index]
            for index, description in enumerate(cursor.description)
        }
        self.conn.executescript(
            """
            CREATE TABLE trading_accounts(
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                currency TEXT NOT NULL,
                single_position_loss_limit_enabled INTEGER NOT NULL,
                single_position_loss_limit_amount REAL NOT NULL
            );
            CREATE TABLE paper_positions(
                position_id TEXT PRIMARY KEY,
                user_id INTEGER,
                account_id INTEGER,
                order_id TEXT,
                deployment_id TEXT,
                strategy_id TEXT,
                symbol TEXT,
                direction TEXT,
                volume REAL,
                remaining_volume REAL,
                entry_price REAL,
                current_price REAL,
                stop_loss REAL,
                take_profit REAL,
                initial_risk REAL,
                favorable_price REAL,
                open_commission REAL,
                unrealized_profit REAL,
                net_profit REAL,
                exit_mode TEXT,
                close_reason TEXT,
                position_policy_snapshot_json TEXT,
                partial_levels_done_json TEXT,
                position_attribution_json TEXT,
                status TEXT,
                opened_at INTEGER,
                closed_at INTEGER,
                close_price REAL,
                updated_at INTEGER
            );
            CREATE TABLE paper_trades(
                trade_id TEXT PRIMARY KEY,
                user_id INTEGER,
                account_id INTEGER,
                order_id TEXT,
                position_id TEXT,
                deployment_id TEXT,
                strategy_id TEXT,
                symbol TEXT,
                direction TEXT,
                volume REAL,
                entry_price REAL,
                exit_price REAL,
                gross_profit REAL,
                commission REAL,
                net_profit REAL,
                exit_reason TEXT,
                opened_at INTEGER,
                closed_at INTEGER,
                created_at INTEGER,
                position_attribution_json TEXT
            );
            """
        )
        self.conn.execute(
            "INSERT INTO trading_accounts VALUES(1, 7, 'USD', 1, 30.0)"
        )
        self.events = _PositionEvents()
        paper_service = SimpleNamespace(
            position_events=self.events,
            structure_plans=SimpleNamespace(
                update_execution_status=lambda *_args, **_kwargs: None
            ),
        )
        self.service = PaperPositionService(paper_service)

    def tearDown(self):
        self.conn.close()

    def _insert_position(self, position_id, entry_price):
        self.conn.execute(
            """
            INSERT INTO paper_positions(
                position_id, user_id, account_id, order_id, deployment_id,
                strategy_id, symbol, direction, volume, remaining_volume,
                entry_price, current_price, stop_loss, take_profit,
                initial_risk, favorable_price, open_commission,
                unrealized_profit, net_profit, exit_mode, close_reason,
                position_policy_snapshot_json, partial_levels_done_json,
                position_attribution_json, status, opened_at, updated_at
            ) VALUES(?, 7, 1, 'order-1', 'deployment-1', 'strategy-1',
                     'SILVER#', 'buy', 1, 1, ?, ?, 0, 0, 1, ?, 1,
                     0, 0, 'signal', '', '{}', '[]', '{}', 'open', 100, 100)
            """,
            (position_id, entry_price, entry_price, entry_price),
        )

    def _manage(self, bid):
        result = {"closed": 0}
        balance = self.service.manage(
            self.conn, 7, 1, "SILVER#", bid, bid + 0.1, 200,
            {"commission_per_lot": 1}, [], {}, result, 1000,
            contract_size=1, slippage=0,
        )
        return result, balance

    def test_tick_closes_position_when_estimated_net_loss_reaches_limit(self):
        # Gross -28 plus open/close commission -2 equals the configured -30.
        self._insert_position("position-loss", 100)

        result, balance = self._manage(72)

        position = self.conn.execute(
            "SELECT * FROM paper_positions WHERE position_id='position-loss'"
        ).fetchone()
        trade = self.conn.execute(
            "SELECT * FROM paper_trades WHERE position_id='position-loss'"
        ).fetchone()
        self.assertEqual(result["closed"], 1)
        self.assertEqual(position["status"], "closed")
        self.assertEqual(position["close_reason"], "single_position_loss_limit")
        self.assertEqual(trade["exit_reason"], "single_position_loss_limit")
        self.assertEqual(trade["net_profit"], -30)
        self.assertEqual(balance, 971)
        self.assertEqual(len(self.events.events), 1)

    def test_tick_keeps_position_open_before_limit(self):
        self._insert_position("position-open", 100)

        result, balance = self._manage(72.01)

        position = self.conn.execute(
            "SELECT * FROM paper_positions WHERE position_id='position-open'"
        ).fetchone()
        self.assertEqual(result["closed"], 0)
        self.assertEqual(position["status"], "open")
        self.assertEqual(balance, 1000)
        self.assertEqual(self.events.events, [])


if __name__ == "__main__":
    unittest.main()
