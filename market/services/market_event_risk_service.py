"""Deterministic market-event risk windows for structure trade plans.

The service deliberately has no dependency on a third-party economic calendar.
Regular market opens are calculated in their native time zones (and therefore
respect DST); dated macro events are stored in the public market configuration.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Dict, Iterable, Optional
from zoneinfo import ZoneInfo

from market_event_repository import MarketEventRepository
from runtime_cache import TTLCache, invalidate as invalidate_runtime_cache
from repositories.runtime import RuntimeStateRepository


REVERSAL_SETUPS = {
    "range_lower_reversal", "range_upper_reversal", "range_false_breakout",
    "structure_location_pullback", "liquidity_sweep_reclaim",
    "choch_reversal", "structure_reversal", "pressure_reversal",
    "pressure_zone_breakout",
}


DEFAULT_EVENT_RISK_RULES = [
    {"id": "tokyo_open", "label": "东京开盘", "event_type": "market_open", "level": "L1",
     "timezone": "Asia/Tokyo", "time": "09:00", "weekdays": [0, 1, 2, 3, 4],
     "before_minutes": 5, "after_minutes": 10},
    {"id": "shanghai_open", "label": "上海开盘", "event_type": "market_open", "level": "L1",
     "timezone": "Asia/Shanghai", "time": "09:30", "weekdays": [0, 1, 2, 3, 4],
     "before_minutes": 5, "after_minutes": 10},
    {"id": "shanghai_futures_afternoon_open", "label": "上海期货午盘", "event_type": "market_open", "level": "L1",
     "timezone": "Asia/Shanghai", "time": "13:30", "weekdays": [0, 1, 2, 3, 4],
     "before_minutes": 5, "after_minutes": 10},
    {"id": "london_open", "label": "伦敦开盘", "event_type": "market_open", "level": "L2",
     "timezone": "Europe/London", "time": "08:00", "weekdays": [0, 1, 2, 3, 4],
     "before_minutes": 5, "after_minutes": 10},
    {"id": "new_york_open", "label": "纽约开盘", "event_type": "market_open", "level": "L2",
     "timezone": "America/New_York", "time": "09:30", "weekdays": [0, 1, 2, 3, 4],
     "before_minutes": 5, "after_minutes": 10},
]

_calendar_cache = TTLCache(ttl_seconds=60, max_items=16)

# Calendar providers do not always assign a consistent importance score.  These
# two US releases therefore receive deterministic treatment even when a source
# labels them as medium impact or omits a country field.
NFP_KEYWORDS = (
    "nonfarm payroll", "non-farm payroll", "nonfarm employment",
    "nonfarm jobs", "非农", "美国就业报告",
)
FOMC_KEYWORDS = (
    "fomc", "federal reserve", "fed interest rate", "fed rate decision",
    "interest rate decision", "美联储", "联邦公开市场委员会", "利率决议",
)

# Market-level event taxonomy.  This is intentionally separate from strategy
# configuration: the same event-to-symbol relationship is shared by all users
# and all structure setups.
EVENT_IMPACT_RULES = (
    {"event_type": "fomc", "symbols": ("GOLD", "SILVER", "OIL", "US100", "US500", "BTCUSD", "AUDUSD"),
     "keywords": FOMC_KEYWORDS, "before_minutes": 5, "after_minutes": 15},
    {"event_type": "nfp", "symbols": ("GOLD", "SILVER", "US100", "US500", "BTCUSD", "AUDUSD"),
     "keywords": NFP_KEYWORDS, "before_minutes": 5, "after_minutes": 15},
    {"event_type": "us_inflation", "symbols": ("GOLD", "SILVER", "US100", "US500", "BTCUSD", "AUDUSD"),
     "keywords": ("cpi", "core cpi", "pce", "core pce", "美国通胀", "消费者物价", "个人消费支出"),
     "before_minutes": 5, "after_minutes": 15},
    {"event_type": "energy", "symbols": ("OIL",),
     "keywords": ("eia", "crude oil inventories", "原油库存", "opec", "欧佩克", "iea"),
     "before_minutes": 5, "after_minutes": 15},
    {"event_type": "rba", "symbols": ("AUDUSD",),
     "keywords": ("rba", "reserve bank of australia", "澳洲联储", "澳大利亚利率"),
     "before_minutes": 5, "after_minutes": 15},
    {"event_type": "china_macro", "symbols": ("AUDUSD", "OIL", "SILVER"),
     "keywords": ("china pmi", "中国pmi", "中国采购经理", "中国制造业"),
     "before_minutes": 5, "after_minutes": 15},
    {"event_type": "treasury_yield", "symbols": ("GOLD", "SILVER", "US100", "US500", "BTCUSD", "AUDUSD"),
     "keywords": ("treasury yield", "10-year yield", "2-year yield", "美债收益率", "国债收益率"),
     "before_minutes": 5, "after_minutes": 15},
)

# Unified market-event registry. Recurring session windows and dated calendar
# events share one schema while retaining their distinct time resolvers.
MARKET_EVENT_RULES = tuple(
    {**rule, "source_type": "recurring_time"} for rule in DEFAULT_EVENT_RISK_RULES
) + tuple(
    {**rule, "source_type": "calendar_event"} for rule in EVENT_IMPACT_RULES
)
_market_rules_cache = TTLCache(ttl_seconds=30, max_items=1)


def get_market_event_rules() -> list[Dict]:
    """Return the persisted global rules, falling back to built-in defaults."""
    cached = _market_rules_cache.get("default", "calendar")
    if cached is not None:
        return [dict(item) for item in cached]
    rules = [dict(item) for item in MARKET_EVENT_RULES]
    try:
        saved = RuntimeStateRepository(0, 0).get_entity("market_event_rules", "default")
        if isinstance(saved, dict) and isinstance(saved.get("rules"), list) and saved["rules"]:
            rules = [dict(item) for item in saved["rules"] if isinstance(item, dict)]
    except Exception as exc:
        print(f"[EventRisk] 市场事件规则读取失败，使用默认规则: {exc}")
    _market_rules_cache.set("default", rules, "calendar")
    return [dict(item) for item in rules]


def invalidate_market_event_rules() -> None:
    invalidate_runtime_cache({"calendar"})


def is_reversal_setup(setup_type: str) -> bool:
    return str(setup_type or "").strip().lower() in REVERSAL_SETUPS


def effective_event_risk_rules(config: Dict) -> list[Dict]:
    """Merge configured rules onto built-in market-open protection.

    A custom rollover rule supplements the standard Tokyo, Shanghai, London,
    and New York opening windows.  Matching IDs override their built-in rule,
    which also lets administrators disable or tune an individual market open.
    """
    configured = config.get("event_risk_rules")
    if not isinstance(configured, list) or not configured:
        return [dict(rule) for rule in MARKET_EVENT_RULES]
    merged = [dict(rule) for rule in get_market_event_rules()]
    indexes = {
        str(rule.get("id") or ""): index
        for index, rule in enumerate(merged)
        if str(rule.get("id") or "")
    }
    for raw in configured:
        if not isinstance(raw, dict):
            continue
        rule_id = str(raw.get("id") or "")
        if rule_id and rule_id in indexes:
            index = indexes[rule_id]
            merged[index] = {**merged[index], **raw}
            continue
        if rule_id:
            indexes[rule_id] = len(merged)
        merged.append(dict(raw))
    return merged


def _matches_scope(values: Iterable[str] | None, wanted: str) -> bool:
    choices = {str(item).strip().upper() for item in (values or []) if str(item).strip()}
    return not choices or "*" in choices or str(wanted or "").upper() in choices


def _event_at(rule: Dict, now: int) -> Optional[int]:
    """Return the nearest scheduled occurrence of a recurring or dated rule."""
    if rule.get("event_time"):
        try:
            return int(rule["event_time"])
        except (TypeError, ValueError):
            return None
    time_text = str(rule.get("time") or "")
    if not time_text or ":" not in time_text:
        return None
    try:
        hour, minute = (int(part) for part in time_text.split(":", 1))
        tz = ZoneInfo(str(rule.get("timezone") or "Asia/Shanghai"))
    except (TypeError, ValueError, KeyError):
        return None
    local_now = datetime.fromtimestamp(now, timezone.utc).astimezone(tz)
    weekdays = {int(day) for day in (rule.get("weekdays") or []) if str(day).isdigit()}
    # Search around today: an event that began before midnight can still be in
    # its after-window, and a pre-window can begin on the previous local day.
    candidates = []
    for offset in (-1, 0, 1):
        day = (local_now + timedelta(days=offset)).date()
        occurrence = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
        if weekdays and occurrence.weekday() not in weekdays:
            continue
        candidates.append(int(occurrence.timestamp()))
    return min(candidates, key=lambda item: abs(item - now)) if candidates else None


def _calendar_events(now: int) -> list[Dict]:
    """Load a narrow, shared calendar slice at most once a minute per worker."""
    beijing = datetime.fromtimestamp(now, timezone.utc).astimezone(ZoneInfo("Asia/Shanghai"))
    dates = [(beijing + timedelta(days=offset)).date().isoformat() for offset in (-1, 0, 1)]
    cache_key = tuple(dates)
    cached = _calendar_cache.get(cache_key, "calendar")
    if cached is not None:
        return cached
    events: list[Dict] = []
    try:
        repository = MarketEventRepository()
        for event_date in dates:
            events.extend(repository.list_calendar(event_date))
            events.extend(repository.list_key_events(event_date))
    except Exception as exc:
        # Risk protection must never take down Tick execution if the calendar
        # source is temporarily unavailable; deterministic opening rules remain.
        print(f"[EventRisk] 财经日历读取失败，继续使用开盘窗口: {exc}")
    _calendar_cache.set(cache_key, events, "calendar")
    return events


def _calendar_timestamp(event: Dict) -> int:
    for field in ("event_timestamp", "timestamp"):
        try:
            value = int(event.get(field) or 0)
            if value:
                return value // 1000 if value > 10**12 else value
        except (TypeError, ValueError):
            pass
    date_text, time_text = str(event.get("event_date") or ""), str(event.get("event_time") or "")
    if not date_text or not time_text:
        return 0
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return int(datetime.strptime(f"{date_text} {time_text}", fmt).replace(
                tzinfo=ZoneInfo("Asia/Shanghai")
            ).timestamp())
        except ValueError:
            continue
    return 0


def _major_us_event(event: Dict) -> Optional[str]:
    """Classify NFP/FOMC by normalized calendar fields, not source severity."""
    text = " ".join(
        str(event.get(field) or "")
        for field in ("name", "title", "event", "description", "country", "currency")
    ).lower()
    if any(keyword in text for keyword in NFP_KEYWORDS):
        return "nfp"
    if any(keyword in text for keyword in FOMC_KEYWORDS):
        return "fomc"
    return None


def _canonical_symbol(symbol: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(symbol or "").upper())


def _event_impact_rule(event: Dict, symbol: str) -> Optional[Dict]:
    text = " ".join(str(event.get(field) or "") for field in (
        "name", "title", "event", "description", "country", "currency",
    )).casefold()
    canonical = _canonical_symbol(symbol)
    for rule in get_market_event_rules():
        if rule.get("source_type") != "calendar_event":
            continue
        if canonical not in rule["symbols"]:
            continue
        if any(str(keyword).casefold() in text for keyword in rule["keywords"]):
            return rule
    return None


def _calendar_event(config: Dict, symbol: str, setup_type: str, now: int) -> Optional[Dict]:
    min_importance = max(1, min(3, int(config.get("event_risk_min_importance") or 3)))
    for event in _calendar_events(now):
        try:
            importance = int(event.get("importance") or 0)
        except (TypeError, ValueError):
            importance = 0
        major_type = _major_us_event(event)
        impact_rule = _event_impact_rule(event, symbol)
        if impact_rule and not major_type:
            major_type = impact_rule["event_type"]
        if not is_reversal_setup(setup_type) and not impact_rule:
            continue
        # NFP and FOMC must always be protected, even when a calendar source
        # has not yet normalized its impact level.
        if not major_type and importance < min_importance:
            continue
        symbols = event.get("symbols") or []
        if symbols and not _matches_scope(symbols, symbol):
            continue
        event_time = _calendar_timestamp(event)
        major = bool(major_type)
        if impact_rule:
            before_minutes = impact_rule["before_minutes"]
            after_minutes = impact_rule["after_minutes"]
        else:
            before_minutes = int(config.get(
                "event_risk_major_before_minutes" if major else "event_risk_calendar_before_minutes",
                45 if major else 30,
            ) or 0)
            after_minutes = int(config.get(
                "event_risk_major_after_minutes" if major else "event_risk_calendar_after_minutes",
                90 if major else 45,
            ) or 0)
        before = max(0, before_minutes) * 60
        after = max(0, after_minutes) * 60
        if not event_time or not event_time - before <= now < event_time + after:
            continue
        label = str(event.get("name") or event.get("title") or "财经日历高影响事件")
        major_label = {"nfp": "美国非农（NFP）", "fomc": "美联储议息/FOMC"}.get(major_type)
        return {
            "id": str(event.get("id") or f"calendar:{event_time}:{label}"),
            "label": label,
            "event_type": major_type or "economic_calendar",
            "level": "L4" if impact_rule or major or importance >= 3 else "L3",
            "event_time": event_time,
            "suppress_from": event_time - before,
            "resume_after": event_time + after,
            "reason": f"重大宏观事件：{major_label or impact_rule['event_type']}" if impact_rule or major else f"财经日历高影响事件：{label}",
            "importance": importance,
            "major_event": major,
        }
    return None


def active_event(config: Dict, symbol: str, period: str, setup_type: str, now: Optional[int] = None) -> Optional[Dict]:
    """Return the applicable event window, including the post-event recheck.

    Only reversal setups are suppressed by default. Rules can explicitly set
    ``affect_setups`` to override this behaviour for a particular event.
    """
    now = int(now or datetime.now(timezone.utc).timestamp())
    if not bool(config.get("event_risk_enabled", True)):
        return None
    setup = str(setup_type or "").strip().lower()
    calendar = _calendar_event(config, symbol, setup, now)
    if calendar:
        confirmation_bars = max(0, int(config.get("event_risk_resume_confirmation_bars") or 1))
        calendar["resume_confirmation_bars"] = confirmation_bars
        calendar["resume_after"] += confirmation_bars * {
            "M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400,
        }.get(str(period).upper(), 300)
        return calendar
    for raw in effective_event_risk_rules(config):
        if not isinstance(raw, dict) or raw.get("enabled", True) is False:
            continue
        if not _matches_scope(raw.get("symbol_scope"), symbol) or not _matches_scope(raw.get("period_scope"), period):
            continue
        affected = {str(value).strip().lower() for value in (raw.get("affect_setups") or []) if str(value).strip()}
        if affected:
            if setup not in affected:
                continue
        elif not is_reversal_setup(setup):
            continue
        event_time = _event_at(raw, now)
        if not event_time:
            continue
        before = max(0, int(raw.get("before_minutes") or 0)) * 60
        after = max(0, int(raw.get("after_minutes") or 0)) * 60
        suppress_from, suppress_until = event_time - before, event_time + after
        if not suppress_from <= now < suppress_until:
            continue
        result = {
            "id": str(raw.get("id") or raw.get("event_type") or "market_event"),
            "label": str(raw.get("label") or "市场事件"),
            "event_type": str(raw.get("event_type") or "market_event"),
            "level": str(raw.get("level") or "L2").upper(),
            "event_time": event_time,
            "suppress_from": suppress_from,
            "resume_after": suppress_until,
            "reason": f"{raw.get('label') or '市场事件'}风险窗口",
        }
        confirmation_bars = max(0, int(config.get("event_risk_resume_confirmation_bars") or 1))
        result["resume_confirmation_bars"] = confirmation_bars
        result["resume_after"] += confirmation_bars * {
            "M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400,
        }.get(str(period).upper(), 300)
        return result
    return None


def snapshot(config: Dict, symbol: str, period: str, setup_type: str, now: Optional[int] = None) -> Dict:
    event = active_event(config, symbol, period, setup_type, now)
    return {"event_risk": event} if event else {}
