---
name: market-event-48h-impact
description: Score unseen important Jin10 flashes vs live symbols.
---

# Market event impact

Do not crawl websites. Score only the unseen important Jin10 flashes in the prompt.
Write the result to the AI-Trader key-events calendar.

## Symbols in scope

- GOLD#
- SILVER#
- OILCash#
- US100Cash#
- US500Cash#
- BTCUSD#
- EURUSD#
- AUDUSD#
- USDJPY#

## Output contract

Return JSON only, then POST it.

```json
{
  "date": "YYYY-MM-DD",
  "source": "hermes_48h",
  "events": [
    {
      "title": "short headline",
      "event_time": "HH:MM",
      "importance": 3,
      "category": "HERMES 48小时评估",
      "summary": "what happened and why it matters",
      "symbols": ["GOLD#", "US100Cash#"],
      "impacts": [
        {"symbol": "GOLD#", "bias": "bearish", "severity": "high", "affected": true, "note": "实际利率预期上升"},
        {"symbol": "US100Cash#", "bias": "bearish", "severity": "high", "affected": true, "note": "风险资产承压"},
        {"symbol": "AUDUSD#", "bias": "neutral", "severity": "low", "affected": false, "note": "无直接冲击"}
      ]
    }
  ]
}
```

Rules:

- Score only items in the prompt. Never recrawl or reuse already-seen IDs.
- Every in-scope symbol must appear in `impacts`.
- `affected` is true only if the event can move that symbol.
- `severity` is `high`, `medium`, or `low`.
- A Fed hawkish hike speech is usually `high` for GOLD#, SILVER#, US100Cash#, US500Cash#.
- `bias` is `bullish`, `bearish`, `mixed`, or `neutral`.
- `importance`: 3 = market-moving, 2 = material.
- Keep 1 to 5 events. If none are material, POST `"events": []`.

## Posting

POST to `http://39.106.142.123/api/news/hermes/assessments`.
Read `X-Hermes-Token` from `/root/.hermes/.env`.
Do not print the token. After success, reply with count and titles only.
