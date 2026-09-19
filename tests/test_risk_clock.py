import unittest
from datetime import datetime

from market.risk_clock import BEIJING, risk_day_key, risk_day_start_timestamp


class RiskClockTest(unittest.TestCase):
    def test_business_day_changes_at_beijing_midnight(self):
        before = datetime(2026, 8, 30, 0, 0, 0, tzinfo=BEIJING).timestamp() - 1
        midnight = datetime(2026, 8, 30, 0, 0, 0, tzinfo=BEIJING).timestamp()
        after = datetime(2026, 8, 30, 0, 1, 0, tzinfo=BEIJING).timestamp()
        late = datetime(2026, 8, 30, 23, 59, 59, tzinfo=BEIJING).timestamp()

        self.assertEqual(risk_day_key(before), "2026-08-29")
        self.assertEqual(risk_day_key(midnight), "2026-08-30")
        self.assertEqual(risk_day_key(after), "2026-08-30")
        self.assertEqual(risk_day_key(late), "2026-08-30")
        self.assertEqual(
            risk_day_start_timestamp(before),
            int(datetime(2026, 8, 29, 0, 0, tzinfo=BEIJING).timestamp()),
        )
        self.assertEqual(risk_day_start_timestamp(midnight), int(midnight))
        self.assertEqual(risk_day_start_timestamp(after), int(midnight))
        self.assertEqual(risk_day_start_timestamp(late), int(midnight))


if __name__ == "__main__":
    unittest.main()
