import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from market.services.market_event_risk_service import (
    DEFAULT_EVENT_RISK_RULES,
    _event_at,
    active_event,
)
from market.services.major_us_calendar_collector import parse_bls_nfp_ics, parse_fomc_calendar
from market.services.signal.structure_plan_signal import STRUCTURE_PLAN_DEFAULT_CONFIG


class MarketEventRiskTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(STRUCTURE_PLAN_DEFAULT_CONFIG)

    def test_new_york_open_uses_native_dst_offset(self):
        rule = {
            "timezone": "America/New_York", "time": "09:30",
            "weekdays": [0, 1, 2, 3, 4],
        }
        winter_now = int(datetime(2026, 1, 5, 15, 30, tzinfo=timezone.utc).timestamp())
        summer_now = int(datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc).timestamp())
        self.assertEqual(
            _event_at(rule, winter_now),
            int(datetime(2026, 1, 5, 9, 30, tzinfo=ZoneInfo("America/New_York")).timestamp()),
        )
        self.assertEqual(
            _event_at(rule, summer_now),
            int(datetime(2026, 7, 6, 9, 30, tzinfo=ZoneInfo("America/New_York")).timestamp()),
        )

    @patch("market.services.market_event_risk_service._calendar_events", return_value=[])
    def test_shanghai_futures_afternoon_open_uses_beijing_time_and_pauses_reversals(self, _events):
        rule = next(
            item for item in DEFAULT_EVENT_RISK_RULES
            if item["id"] == "shanghai_futures_afternoon_open"
        )
        event_time = int(datetime(
            2026, 9, 7, 13, 30, tzinfo=ZoneInfo("Asia/Shanghai")
        ).timestamp())

        self.assertEqual(_event_at(rule, event_time), event_time)
        event = active_event(
            self.config, "GOLD#", "M5", "range_upper_reversal",
            event_time - 5 * 60,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["id"], "shanghai_futures_afternoon_open")
        self.assertEqual(event["label"], "上海期货午盘")
        self.assertEqual(event["suppress_from"], event_time - 5 * 60)
        self.assertEqual(event["resume_after"], event_time + 10 * 60 + 5 * 60)
        self.assertEqual(event["resume_confirmation_bars"], 1)

    @patch("market.services.market_event_risk_service._calendar_events", return_value=[])
    def test_market_open_pauses_pressure_reversal_and_pressure_breakout(self, _events):
        event_time = int(datetime(
            2026, 9, 7, 13, 30, tzinfo=ZoneInfo("Asia/Shanghai")
        ).timestamp())

        reversal = active_event(
            self.config, "GOLD#", "M5", "pressure_reversal", event_time,
        )
        self.assertIsNotNone(reversal)
        self.assertEqual(reversal["id"], "shanghai_futures_afternoon_open")
        breakout = active_event(
            self.config, "GOLD#", "M5", "pressure_zone_breakout", event_time,
        )
        self.assertIsNotNone(breakout)
        self.assertEqual(breakout["id"], "shanghai_futures_afternoon_open")

    @patch("market.services.market_event_risk_service._calendar_events", return_value=[])
    def test_custom_rollover_supplements_market_open_rules(self, _events):
        config = {
            **self.config,
            "event_risk_rules": [{
                "id": "beijing_daily_rollover",
                "label": "北京时间日切换",
                "event_type": "daily_rollover",
                "timezone": "Asia/Shanghai",
                "time": "06:00",
                "weekdays": list(range(7)),
                "before_minutes": 0,
                "after_minutes": 85,
                "affect_setups": ["pressure_reversal", "pressure_zone_breakout"],
            }],
        }
        rollover_time = int(datetime(
            2026, 9, 7, 6, 30, tzinfo=ZoneInfo("Asia/Shanghai")
        ).timestamp())
        rollover = active_event(
            config, "BTCUSD#", "M5", "pressure_zone_breakout", rollover_time,
        )
        self.assertIsNotNone(rollover)
        self.assertEqual(rollover["id"], "beijing_daily_rollover")

        new_york_open = int(datetime(
            2026, 9, 8, 9, 30, tzinfo=ZoneInfo("America/New_York")
        ).timestamp())
        opening = active_event(
            config, "BTCUSD#", "M5", "pressure_zone_breakout", new_york_open,
        )
        self.assertIsNotNone(opening)
        self.assertEqual(opening["id"], "new_york_open")

    @patch("market.services.market_event_risk_service._calendar_events")
    def test_nfp_is_l4_even_when_calendar_marks_medium_impact(self, events):
        at = int(datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc).timestamp())
        events.return_value = [{
            "id": "nfp", "name": "US Nonfarm Payrolls", "importance": 2,
            "event_timestamp": at,
        }]
        event = active_event(self.config, "BTCUSD", "M5", "range_upper_reversal", at - 3 * 60)
        self.assertIsNotNone(event)
        self.assertEqual(event["event_type"], "nfp")
        self.assertEqual(event["level"], "L4")
        self.assertEqual(event["suppress_from"], at - 5 * 60)
        self.assertIn("美国非农", event["reason"])

    @patch("market.services.market_event_risk_service._calendar_events")
    def test_fomc_is_l4_and_pauses_all_gold_entries(self, events):
        at = int(datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc).timestamp())
        events.return_value = [{
            "id": "fomc", "title": "FOMC Interest Rate Decision", "importance": 1,
            "event_timestamp": at,
        }]
        event = active_event(self.config, "GOLD_", "M1", "liquidity_sweep_reclaim", at)
        self.assertEqual(event["event_type"], "fomc")
        self.assertEqual(event["level"], "L4")
        trend_event = active_event(self.config, "GOLD_", "M1", "trend_continuation", at)
        self.assertIsNotNone(trend_event)
        self.assertEqual(trend_event["suppress_from"], at - 5 * 60)
        self.assertEqual(trend_event["resume_after"], at + 15 * 60 + 60)

    @patch("market.services.market_event_risk_service._calendar_events")
    def test_normal_high_impact_calendar_window_and_resume_bar(self, events):
        at = int(datetime(2026, 9, 4, 14, 0, tzinfo=timezone.utc).timestamp())
        events.return_value = [{"name": "US ISM", "importance": 3, "event_timestamp": at}]
        event = active_event(self.config, "BTCUSD", "M5", "range_lower_reversal", at)
        self.assertEqual(event["level"], "L4")
        self.assertEqual(event["resume_confirmation_bars"], 1)
        self.assertEqual(event["resume_after"], at + 45 * 60 + 5 * 60)

    @patch("market.services.market_event_risk_service._calendar_events")
    def test_energy_event_only_pauses_oil_all_entries(self, events):
        at = int(datetime(2026, 9, 9, 14, 30, tzinfo=timezone.utc).timestamp())
        events.return_value = [{
            "id": "eia", "name": "EIA Crude Oil Inventories", "importance": 2,
            "event_timestamp": at,
        }]
        oil = active_event(self.config, "OIL#", "M5", "trend_continuation", at)
        gold = active_event(self.config, "GOLD#", "M5", "trend_continuation", at)
        self.assertIsNotNone(oil)
        self.assertEqual(oil["event_type"], "energy")
        self.assertEqual(oil["suppress_from"], at - 5 * 60)
        self.assertEqual(oil["resume_after"], at + 15 * 60 + 5 * 60)
        self.assertIsNone(gold)

    def test_official_calendar_parsers_normalize_nfp_and_fomc(self):
        nfp = parse_bls_nfp_ics("""BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Employment Situation
DTSTART:20260904T123000Z
END:VEVENT
END:VCALENDAR""")
        self.assertEqual(nfp[0]["name"], "美国非农就业报告（NFP）")
        fomc = parse_fomc_calendar(
            "<h2>2026 FOMC Meetings</h2><p>January 27-28 March 17-18</p>"
            "<h2>2027 FOMC Meetings</h2>", 2026,
        )
        self.assertEqual(len(fomc), 2)
        self.assertTrue(all(item["name"] == "美联储议息决议（FOMC）" for item in fomc))


if __name__ == "__main__":
    unittest.main()
