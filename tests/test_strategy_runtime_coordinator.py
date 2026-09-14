import unittest

from market.services.strategy_runtime_coordinator import StrategyRuntimeCoordinator


class _Strategy:
    def __init__(self, symbol):
        self.symbol = symbol


class _Store:
    def __init__(self, strategies):
        self.strategies = strategies

    def get_all_strategies(self):
        return list(self.strategies)


class _Accounts:
    def get_by_id(self, *_args):
        return None


class _Mappings:
    def __init__(self, compatible_result=False):
        self.compatible_result = compatible_result

    def source_server(self, *_args):
        return ""

    def compatible(self, *_args):
        return self.compatible_result


class StrategyRuntimeCoordinatorTests(unittest.TestCase):
    def test_broker_suffix_does_not_match_canonical_symbol(self):
        strategy = _Strategy("BTCUSD")
        coordinator = StrategyRuntimeCoordinator(
            _Store([strategy]), _Mappings(), _Accounts()
        )

        matched = coordinator.strategies_for_quote(1, 22, "BTCUSD#")

        self.assertEqual(matched, [])

    def test_unrelated_symbol_is_not_matched_by_suffix_fallback(self):
        strategy = _Strategy("GOLD#")
        coordinator = StrategyRuntimeCoordinator(
            _Store([strategy]), _Mappings(), _Accounts()
        )

        self.assertEqual(coordinator.strategies_for_quote(1, 22, "BTCUSD#"), [])

    def test_cross_broker_symbol_requires_explicit_mapping(self):
        strategy = _Strategy("BTCUSD")
        coordinator = StrategyRuntimeCoordinator(
            _Store([strategy]), _Mappings(compatible_result=True), _Accounts()
        )

        self.assertEqual(
            coordinator.strategies_for_quote(1, 22, "BTCUSD#"), [strategy]
        )


if __name__ == "__main__":
    unittest.main()
