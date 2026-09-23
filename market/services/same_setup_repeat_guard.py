"""Block same-setup, same-direction entries that have not moved."""
from __future__ import annotations

import json
import time
from typing import Dict, Iterable, Optional


REPEAT_WINDOW_SECONDS = 30 * 60
MIN_PROGRESS_ATR = 0.8
MIN_PROGRESS_PCT = 0.0005


def _number(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default


def _direction(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"b", "buy"}:
        return "buy"
    if text in {"s", "sell"}:
        return "sell"
    return text


def _atr(rows: Iterable[Dict], period: int = 14) -> float:
    true_ranges = []
    previous_close = None
    items = list(rows or [])[-max(2, int(period) + 1):]
    for row in items:
        high = _number(row.get("high", row.get("high_price")))
        low = _number(row.get("low", row.get("low_price")))
        close = _number(row.get("close", row.get("close_price")))
        if high <= 0 or low <= 0 or close <= 0 or high < low:
            continue
        if previous_close is None:
            true_ranges.append(high - low)
        else:
            true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        previous_close = close
    if len(true_ranges) < 2:
        return 0.0
    return sum(true_ranges[-period:]) / min(period, len(true_ranges))


def progress_threshold(price: float, atr: float = 0.0) -> float:
    price = abs(_number(price))
    atr = max(0.0, _number(atr))
    if atr > 0:
        return max(atr * MIN_PROGRESS_ATR, price * MIN_PROGRESS_PCT)
    return max(price * MIN_PROGRESS_PCT, 1e-8)


def should_block_repeat(entries: Iterable[Dict], *, setup_type: str, direction: str,
                        price: float, now: int, atr: float = 0.0) -> Optional[Dict]:
    setup = str(setup_type or "generic_entry")
    direction = _direction(direction)
    price = _number(price)
    if price <= 0 or direction not in {"buy", "sell"}:
        return None
    threshold = progress_threshold(price, atr)
    for item in entries or []:
        if str(item.get("setup_type") or "generic_entry") != setup:
            continue
        if _direction(item.get("direction")) != direction:
            continue
        age = int(now) - int(item.get("time") or 0)
        if age < 0 or age > REPEAT_WINDOW_SECONDS:
            continue
        previous = _number(item.get("price"))
        if previous <= 0:
            continue
        if abs(price - previous) < threshold:
            return item
    return None


class SameSetupRepeatGuard:
    def __init__(self, storage=None, kline_store=None):
        self.storage = storage
        self.kline_store = kline_store

    def check(self, *, user_id: int, account_id: int, strategy, signal,
              execution_mode: str = "live", action: str = "",
              now: Optional[int] = None) -> Dict:
        now = int(now or time.time())
        setup_type = str(getattr(signal, "setup_type", "") or "generic_entry")
        direction = _direction(
            action or getattr(signal, "action", "") or getattr(signal, "market_direction", "")
        )
        if direction == "up":
            direction = "buy"
        elif direction == "down":
            direction = "sell"
        price = _number(
            getattr(signal, "suggested_entry", 0)
            or getattr(signal, "trigger_price", 0)
        )
        symbol = str(getattr(signal, "symbol", "") or "")
        strategy_id = str(getattr(strategy, "strategy_id", "") or "")
        atr = self._atr_for(symbol)
        entries = self._recent_entries(
            user_id, account_id, symbol, strategy_id, execution_mode, now,
        )
        previous = should_block_repeat(
            entries, setup_type=setup_type, direction=direction,
            price=price, now=now, atr=atr,
        )
        if previous is None:
            return {"allowed": True, "scope": "setup_repeat"}
        previous_price = _number(previous.get("price"))
        return {
            "allowed": False,
            "scope": "setup_repeat",
            "setup_type": setup_type,
            "direction": direction,
            "reason": (
                f"同一 SETUP {setup_type} 刚在 {previous_price:g} 做过同向单，"
                f"价格只移动了 {abs(price - previous_price):g}，"
                f"{REPEAT_WINDOW_SECONDS // 60} 分钟内不再重复开仓"
            ),
        }

    def _atr_for(self, symbol: str) -> float:
        if not self.kline_store or not symbol:
            return 0.0
        try:
            rows = self.kline_store.get_all_klines(symbol, "M1") or []
        except Exception:
            return 0.0
        return _atr(rows)

    def _recent_entries(self, user_id, account_id, symbol, strategy_id,
                        execution_mode: str, now: int):
        if self.storage is None:
            return []
        since = int(now) - REPEAT_WINDOW_SECONDS
        if execution_mode == "paper":
            rows = self.storage.fetchall(
                "SELECT action, requested_price, filled_price, requested_at, "
                "position_attribution_json FROM paper_orders "
                "WHERE user_id=? AND account_id=? AND symbol=? "
                "AND status IN ('pending','filled') AND requested_at>=? "
                "ORDER BY requested_at DESC LIMIT 80",
                (int(user_id), int(account_id), str(symbol), since),
            ) or []
            time_key, price_keys = "requested_at", ("filled_price", "requested_price")
        else:
            rows = self.storage.fetchall(
                "SELECT action, executed_price, requested_price, reported_at, "
                "position_attribution_json, strategy_id FROM trade_execution_reports "
                "WHERE user_id=? AND account_id=? AND symbol=? AND success=1 "
                "AND LOWER(action) IN ('b','s','buy','sell') AND reported_at>=? "
                "ORDER BY reported_at DESC LIMIT 80",
                (int(user_id), int(account_id), str(symbol), since),
            ) or []
            time_key, price_keys = "reported_at", ("executed_price", "requested_price")
        entries = []
        for row in rows:
            try:
                attribution = json.loads(row.get("position_attribution_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                attribution = {}
            row_strategy = str(row.get("strategy_id") or attribution.get("strategy_id") or "")
            if strategy_id and row_strategy and row_strategy != strategy_id:
                continue
            price = next((_number(row.get(key)) for key in price_keys if _number(row.get(key)) > 0), 0.0)
            entries.append({
                "setup_type": str(attribution.get("setup_type") or "generic_entry"),
                "direction": _direction(row.get("action") or attribution.get("direction")),
                "price": price,
                "time": int(row.get(time_key) or 0),
            })
        return entries
