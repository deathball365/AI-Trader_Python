import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from auth import require_hermes_ingest
from routes_news import _normalize_key_events


class HermesIngestTests(unittest.TestCase):
    def test_key_event_keeps_symbol_impacts(self):
        events = _normalize_key_events("2026-09-25", [{
            "title": "美联储官员偏鹰讲话",
            "event_time": "21:33",
            "importance": 3,
            "symbols": ["GOLD#", "US100Cash#"],
            "impacts": [
                {"symbol": "GOLD#", "bias": "bearish", "note": "实际利率预期上升"},
                {"symbol": "US100Cash#", "bias": "bearish", "note": "风险资产承压"},
            ],
            "summary": "讲话强化继续限制性政策预期。",
        }])
        self.assertEqual(events[0]["title"], "美联储官员偏鹰讲话")
        self.assertEqual(events[0]["symbols"], ["GOLD#", "US100Cash#"])
        self.assertEqual(events[0]["impacts"][0]["bias"], "bearish")

    def test_hermes_token_is_required(self):
        request = type("Req", (), {"method": "POST"})()
        with patch.dict(os.environ, {"HERMES_INGEST_TOKEN": ""}, clear=False):
            with self.assertRaises(HTTPException) as raised:
                require_hermes_ingest(request, authorization=None, x_hermes_token=None)
            self.assertEqual(raised.exception.status_code, 503)

    def test_hermes_token_accepts_header(self):
        request = type("Req", (), {"method": "POST"})()
        with patch.dict(os.environ, {"HERMES_INGEST_TOKEN": "secret-token"}, clear=False):
            user = require_hermes_ingest(
                request, authorization=None, x_hermes_token="secret-token",
            )
        self.assertEqual(user.username, "hermes")
        self.assertEqual(user.role, "admin")


if __name__ == "__main__":
    unittest.main()
