from types import SimpleNamespace
from unittest.mock import patch

from paper_trading import PaperTradingService, market_spec


class _Storage:
    def __init__(self, balance=2205.66, max_single_volume=10.0):
        self.balance = balance
        self.max_single_volume = max_single_volume

    def fetchone(self, sql, params=()):
        return {
            "balance": self.balance,
            "max_single_volume": self.max_single_volume,
        }


def _service():
    service = PaperTradingService.__new__(PaperTradingService)
    service.storage = _Storage()
    return service


def test_fixed_mode_uses_strategy_volume_even_when_risk_percent_is_set():
    strategy = SimpleNamespace(
        fixed_volume=0.01, risk_percent=1.0, volume_mode="fixed",
    )
    service = _service()
    with patch(
        "paper_trading.market_spec",
        side_effect=lambda symbol, spec=None, account_id=None, storage=None: market_spec(symbol),
    ):
        assert service._paper_volume(22, "GOLD#", 2.88, strategy, 4277.48) == 0.01


def test_risk_percent_mode_can_size_from_account_risk():
    strategy = SimpleNamespace(
        fixed_volume=0.01, risk_percent=1.0, volume_mode="risk_percent",
    )
    service = _service()
    with patch(
        "paper_trading.market_spec",
        side_effect=lambda symbol, spec=None, account_id=None, storage=None: market_spec(symbol),
    ):
        assert service._paper_volume(22, "GOLD#", 4.52, strategy, 4290.0) > 0.01
