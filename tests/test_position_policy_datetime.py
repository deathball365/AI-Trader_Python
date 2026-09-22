import unittest
from datetime import datetime

from mysql_repositories import PositionManagementPolicyRepository


class PositionPolicyDatetimeTests(unittest.TestCase):
    def test_millisecond_timestamps_do_not_overflow(self):
        value = PositionManagementPolicyRepository._policy_datetime(1789919003000)
        self.assertEqual(value.year, 2026)
        self.assertEqual(value.month, 9)

    def test_second_timestamps_pass_through(self):
        value = PositionManagementPolicyRepository._policy_datetime(1789919003)
        self.assertEqual(value.year, 2026)
        self.assertEqual(value.month, 9)

    def test_datetime_objects_pass_through(self):
        now = datetime(2026, 9, 22, 12, 0, 0)
        self.assertIs(PositionManagementPolicyRepository._policy_datetime(now), now)

    def test_overflow_falls_back_to_now(self):
        value = PositionManagementPolicyRepository._policy_datetime(10 ** 20)
        self.assertIsInstance(value, datetime)
        self.assertGreaterEqual(value.year, 2026)


if __name__ == "__main__":
    unittest.main()
