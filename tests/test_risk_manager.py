import unittest

from market.models.trading_strategy import TradingStrategy
from market.services.strategy.risk_manager import RiskManager


class RiskManagerVolumeTest(unittest.TestCase):
    def test_fixed_volume_is_not_blocked_by_legacy_max_risk_points(self):
        manager = RiskManager()
        strategy = TradingStrategy(
            strategy_id="btc-live",
            strategy_name="BTC",
            symbol="BTCUSDm",
            fixed_volume=0.01,
            volume_mode="fixed",
        )

        # BTC 按持仓管理方案的 0.1% 最小止损约为 77 点。策略层旧的
        # 50 点限制不得再使实盘手数静默变成 0。
        self.assertEqual(
            manager.calculate_volume("BTCUSDm", 77.7, strategy),
            0.01,
        )

    def test_account_daily_risk_limit_overrides_default(self):
        manager = RiskManager()
        manager.set_account_limits(
            max_positions=10,
            max_single_volume=10,
            daily_loss_limit=5,
            daily_order_limit=100,
            daily_risk_limit=12.5,
        )
        manager.update_account_info(1000, 1000, 1000)
        result = manager.check_risk("X", 1, 100)
        self.assertEqual(result["daily_risk_limit"], 12.5)

    def test_daily_risk_limit_defaults_to_disabled(self):
        manager = RiskManager()
        self.assertEqual(manager.get_status()["daily_risk_limit"], 0.0)

    def test_raising_daily_loss_limit_releases_stale_breaker(self):
        manager = RiskManager()
        manager.update_account_info(163.6, 163.6, 163.6)
        manager._daily_realized_pnl = -77.18
        manager.set_account_limits(
            max_positions=10,
            max_single_volume=10,
            daily_loss_limit=20,
            daily_order_limit=100,
            daily_risk_limit=0,
        )
        self.assertTrue(manager._circuit_breaker)
        manager.set_account_limits(
            max_positions=10,
            max_single_volume=10,
            daily_loss_limit=58.7,
            daily_order_limit=100,
            daily_risk_limit=0,
        )
        self.assertFalse(manager._circuit_breaker)
        result = manager.check_risk("X", 0.01, 1)
        self.assertNotIn("账户已熔断", " ".join(result["warnings"]))

    def test_single_order_risk_limit_defaults_to_15(self):
        manager = RiskManager()
        manager.update_account_info(1000, 1000, 1000)
        blocked = manager.check_risk("X", 1, 160)
        self.assertFalse(blocked["allowed"])
        self.assertTrue(any("超过15.00%" in item for item in blocked["warnings"]))
        allowed = manager.check_risk("X", 1, 140)
        self.assertTrue(allowed["allowed"])

    def test_single_order_risk_limit_is_configurable(self):
        manager = RiskManager()
        manager.update_account_info(1000, 1000, 1000)
        manager.set_account_limits(
            max_positions=10,
            max_single_volume=10,
            daily_loss_limit=5,
            daily_order_limit=100,
            daily_risk_limit=0,
            single_order_risk_limit=20,
        )
        result = manager.check_risk("X", 1, 160)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["single_order_risk_limit"], 20)

    def test_disabled_daily_risk_limit_does_not_block(self):
        manager = RiskManager()
        manager.update_account_info(1000, 1000, 1000)
        manager._daily_risk_used = 80
        result = manager.check_risk("X", 1, 100)
        self.assertTrue(result["allowed"] or "将超过每日风险限制" not in " ".join(result["warnings"]))
        self.assertNotIn("将超过每日风险限制", " ".join(result["warnings"]))



    def test_zero_statistics_do_not_uninitialize_account(self):
        class _Stats:
            def get_account_info(self):
                return {"balance": 0, "equity": 0, "free_margin": 0}

        manager = RiskManager()
        manager.set_statistics_service(_Stats())
        manager.update_account_info(192.4, 188.1, 120.0)
        result = manager.check_risk("GOLD#", 0.01, 1)
        self.assertTrue(result["account_initialized"])
        self.assertNotIn("账户信息未初始化，禁止自动交易", result["warnings"])
        self.assertEqual(manager.get_account_balance(), 192.4)

if __name__ == "__main__":
    unittest.main()
