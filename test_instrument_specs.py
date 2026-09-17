import unittest

from repositories.instrument_specs import normalize_price, normalize_volume


class InstrumentSpecTests(unittest.TestCase):
    def test_open_volume_uses_step_without_exceeding_risk(self):
        spec = {"min_volume": 0.01, "volume_step": 0.01, "max_volume": 10, "volume_digits": 2}
        self.assertEqual(0.01, normalize_volume(0.0147, spec, opening=True))

    def test_partial_volume_closes_remaining_when_level_is_dust(self):
        spec = {"min_volume": 0.01, "volume_step": 0.01, "max_volume": 10, "volume_digits": 2}
        self.assertEqual(0.02, normalize_volume(0.0063, spec, opening=False, current_volume=0.02))

    def test_fractional_symbol_is_supported(self):
        spec = {"min_volume": 0.001, "volume_step": 0.001, "max_volume": 100, "volume_digits": 3}
        self.assertEqual(0.014, normalize_volume(0.0147, spec, opening=True))

    def test_price_uses_account_symbol_tick_and_digits(self):
        spec = {"price_digits": 2, "tick_size": 0.01, "point_size": 0.01}
        self.assertEqual(4361.67, normalize_price(4361.674, spec))
        self.assertEqual(4361.68, normalize_price(4361.674, spec, direction="up"))
        self.assertEqual(4361.67, normalize_price(4361.679, spec, direction="down"))

    def test_price_supports_non_decimal_tick_size(self):
        spec = {"price_digits": 2, "tick_size": 0.25, "point_size": 0.01}
        self.assertEqual(100.25, normalize_price(100.13, spec))


if __name__ == "__main__":
    unittest.main()
