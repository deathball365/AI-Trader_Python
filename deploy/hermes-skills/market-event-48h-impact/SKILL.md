---
name: market-event-48h-impact
description: Assess last 48h macro news vs live symbols and post to calendar.
---

# 48-hour market event impact

Search public news from the last 48 hours. Score only events that can move the symbols we currently trade. Write the result to the AI-Trader key-events calendar.

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

## What to collect

Look for central-bank speeches, rates/inflation/employment prints, energy supply headlines, geopolitics, and equity-index risk events. Ignore routine, unscheduled chatter unless price impact is already visible.

Use public web search. Prefer primary or high-quality sources: Fed, BLS, EIA, Reuters, Bloomberg, CNBC, Jin10, official government releases.

## Output contract

Return JSON only, no markdown. Then POST it.

```json
{
  "date": "YYYY-MM-DD",
  "source": "hermes_48h",
  "events": [
    {
      "title": "short headline",
      "event_time": "HH:MM",
      "importance": 2,
      "category": "HERMES 48小时评估",
      "summary": "what happened and why it matters",
      "symbols": ["GOLD#"],
      "impacts": [
        {"symbol": "GOLD#", "bias": "bearish", "note": "one-line reason"}
      ]
    }
  ]
}
```

Rules:

- `date` is Beijing date for the assessment, usually today.
- `event_time` is Beijing `HH:MM`. If unknown, use the search timestamp converted to Beijing time.
- `importance`: 3 = market-moving, 2 = material, never below 2.
- `bias` is one of `bullish`, `bearish`, `mixed`, `neutral`.
- Keep 3 to 8 events. Skip noise.
- Every event must name at least one in-scope symbol.

## Posting

POST the JSON to the AI-Trader backend:

- URL: `http://39.106.142.123/api/news/hermes/assessments`
- Header: `X-Hermes-Token: $HERMES_INGEST_TOKEN`
- Header: `Content-Type: application/json`

Read `HERMES_INGEST_TOKEN` from `/root/.hermes/.env`. Do not print it.

Example:

```bash
curl -sS -X POST "$AI_TRADER_NEWS_URL/news/hermes/assessments" \
  -H "Content-Type: application/json" \
  -H "X-Hermes-Token: $HERMES_INGEST_TOKEN" \
  -d @payload.json
```

Do not print the token. After a successful POST, reply with the count and the event titles only.
