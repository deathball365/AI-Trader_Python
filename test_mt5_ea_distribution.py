import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
EA_SOURCE = PROJECT_ROOT / "mt5TerminalEA.mq5"
EA_ARTIFACT = PROJECT_ROOT / "dist" / "mt5TerminalEA.ex5"


class MT5EADistributionTest(unittest.TestCase):
    def test_ea_defaults_to_public_api(self):
        source = EA_SOURCE.read_text(encoding="utf-8")

        self.assertIn(
            'input string InpServerUrl = "http://39.106.142.123/api"',
            source,
        )
        self.assertNotIn(
            'input string InpServerUrl = "http://127.0.0.1',
            source,
        )

    def test_compiled_artifact_is_available(self):
        self.assertTrue(EA_ARTIFACT.is_file())
        self.assertGreater(EA_ARTIFACT.stat().st_size, 100_000)

    def test_ea_supports_historical_dataset_tasks(self):
        source = EA_SOURCE.read_text(encoding="utf-8")

        self.assertIn('#property version   "2.12"', source)
        self.assertIn('#define EA_API_VERSION "2.1.2"', source)
        self.assertIn('X-EA-Version: " + EA_API_VERSION', source)
        self.assertIn("SYMBOL_TRADE_TICK_SIZE", source)
        self.assertIn("SYMBOL_TRADE_TICK_VALUE", source)
        self.assertIn("ClaimAccountTaskOwner", source)
        self.assertIn("SendMinuteStatistics(bool includeAccountSnapshot)", source)
        self.assertIn("SendMinuteStatistics(accountOwner)", source)
        self.assertIn("SyncEconomicCalendar();", source)
        self.assertIn("accountOwner && (g_lastCalendarSyncTime == 0", source)
        self.assertIn("NormalizeTradePrice(_Symbol, sl, stopRounding)", source)
        self.assertIn("SYMBOL_TRADE_STOPS_LEVEL", source)
        self.assertIn("NormalizeTradePrice(updateSymbol, sl, stopRounding)", source)
        self.assertIn("CheckHistoricalDataTask();", source)
        self.assertIn("SYMBOL_SWAP_LONG", source)
        self.assertIn("tick_value", source)
        self.assertIn("stops_level", source)
        self.assertIn("CopyRates(\n      _Symbol, PERIOD_M1", source)
        self.assertIn("/ea/backtest-data/tasks/next?symbol=", source)
        self.assertIn("/chunks", source)

    def test_ea_does_not_auto_close_local_risk_positions(self):
        source = EA_SOURCE.read_text(encoding="utf-8")

        self.assertNotIn("CheckAndCloseRiskyPositions", source)
        self.assertNotIn("g_riskLimitPercent", source)
        self.assertNotIn("Risk limit exceeded!", source)


if __name__ == "__main__":
    unittest.main()
