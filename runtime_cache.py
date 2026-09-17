"""Small process-local cache with table-aware invalidation generations."""

from __future__ import annotations

import re
import threading
import time
from copy import deepcopy
from typing import Any, Optional


_lock = threading.RLock()
_generations = {
    "accounts": 0, "mappings": 0, "strategies": 0, "deployments": 0,
    "configs": 0, "plans": 0, "calendar": 0, "users": 0,
}
_stats = {
    "hits": {},
    "misses": {},
    "expired": {},
    "invalidations": {},
}


def _bump_stat(kind: str, domain: str) -> None:
    with _lock:
        bucket = _stats.setdefault(kind, {})
        bucket[domain] = int(bucket.get(domain, 0)) + 1


def stats() -> dict[str, dict[str, int]]:
    """Return a snapshot of cache counters for operational diagnostics."""
    with _lock:
        return {
            kind: dict(values)
            for kind, values in _stats.items()
        }


def reset_stats() -> None:
    with _lock:
        for values in _stats.values():
            values.clear()


def domains_for_sql(sql: str) -> set[str]:
    text = str(sql or "").lower()
    domains = set()
    if re.search(r"\b(trading_accounts|mt5_account_connections)\b", text):
        domains.add("accounts")
    if "platform_instrument_mappings" in text:
        domains.add("mappings")
    if "user_strategy_configs" in text:
        domains.add("strategies")
    if "strategy_deployments" in text:
        domains.add("deployments")
    if re.search(
        r"\b(structure_default_configs|structure_symbol_period_configs|"
        r"structure_setup_configs|position_management_policies)\b", text,
    ):
        domains.add("configs")
    if re.search(r"\b(market_data_sources|market_data_symbol_policies)\b", text):
        domains.add("mappings")
    if "structure_trade_plans" in text:
        domains.add("plans")
    if re.search(r"\b(market_calendar_events|market_key_events|market_flash_news)\b", text):
        domains.add("calendar")
    return domains


def invalidate(domains: set[str]) -> None:
    if not domains:
        return
    with _lock:
        for domain in domains:
            _generations[domain] = _generations.get(domain, 0) + 1
            _bump_stat("invalidations", domain)


def generation(domain: str) -> int:
    with _lock:
        return _generations.get(domain, 0)


class TTLCache:
    def __init__(self, ttl_seconds: float = 15.0, max_items: int = 2048):
        self.ttl_seconds = max(0.1, float(ttl_seconds))
        self.max_items = max(16, int(max_items))
        self._items: dict[Any, tuple[float, int, Any]] = {}
        self._lock = threading.RLock()

    def get(self, key: Any, domain: str) -> Optional[Any]:
        now = time.monotonic()
        current_generation = generation(domain)
        with self._lock:
            item = self._items.get(key)
            if item is None:
                _bump_stat("misses", domain)
                return None
            if item[0] <= now or item[1] != current_generation:
                self._items.pop(key, None)
                _bump_stat("expired", domain)
                _bump_stat("misses", domain)
                return None
            _bump_stat("hits", domain)
            return deepcopy(item[2])

    def set(self, key: Any, value: Any, domain: str, ttl_seconds: Optional[float] = None) -> None:
        with self._lock:
            if len(self._items) >= self.max_items:
                self._items.pop(next(iter(self._items)))
            self._items[key] = (
                time.monotonic() + (ttl_seconds or self.ttl_seconds),
                generation(domain),
                deepcopy(value),
            )


class SQLReadCache(TTLCache):
    """Shared cache for explicitly approved low-churn SQL read domains."""

    TTL_BY_DOMAIN = {
        "mappings": 60,
        "strategies": 60,
        "configs": 180,
        # Broker/server identity and membership flags are stable across Ticks.
        # Keep this shorter than config cache so a reconnect still shows up
        # quickly, but long enough to collapse the per-Tick fan-out.
        "accounts": 15,
        "users": 30,
        # Calendar/key-event feeds are refreshed periodically, but a request
        # fan-out can read the same day many times. Keep this short enough for
        # an upstream refresh to become visible without hitting MySQL per call.
        "calendar": 120,
    }

    def get_sql(self, sql: str, params: Any, domain: str) -> Optional[Any]:
        return self.get((_normalise_sql(sql), _normalise_params(params)), domain)

    def set_sql(self, sql: str, params: Any, domain: str, value: Any) -> None:
        self.set(
            (_normalise_sql(sql), _normalise_params(params)), value, domain,
            self.TTL_BY_DOMAIN.get(domain, 15),
        )


sql_read_cache = SQLReadCache(ttl_seconds=60, max_items=8192)


def _normalise_sql(sql: str) -> str:
    """Collapse harmless whitespace so equivalent hand-written SQL can share a key."""
    return re.sub(r"\s+", " ", str(sql or "")).strip().lower()


def _normalise_params(params: Any) -> Any:
    if isinstance(params, (list, tuple)):
        return tuple(_normalise_params(item) for item in params)
    if isinstance(params, dict):
        return tuple(sorted((str(key), _normalise_params(value)) for key, value in params.items()))
    return params


def cache_domain_for_sql(sql: str) -> Optional[str]:
    """Return a cache namespace only for low-churn, safe-to-cache reads."""
    text = str(sql or "").lower()
    if " from " not in f" {text} ":
        return None
    # Account enablement, balances and deployments directly gate order
    # generation.  Only cache the stable broker/server identity lookups.
    if re.search(r"\bstrategy_deployments\b", text):
        return None
    if re.search(r"\b(trading_accounts|mt5_account_connections)\b", text):
        if "mt5_server" in text and not re.search(
            r"\b(enabled|status|trading_enabled|auto_trading_enabled|balance|equity)\b",
            text,
        ):
            return "accounts"
        return None
    if re.search(r"\busers\b", text) and "membership_level" in text:
        return "users"
    # Locking reads must always hit the database and cannot be cached safely.
    if re.search(r"\bfor\s+update\b", text):
        return None
    if re.search(r"\b(platform_instrument_mappings|market_data_sources|market_data_symbol_policies)\b", text):
        return "mappings"
    if "user_strategy_configs" in text:
        return "strategies"
    if "strategy_deployments" in text:
        return "deployments"
    if re.search(r"\b(structure_default_configs|structure_symbol_period_configs|structure_setup_configs|position_management_policies)\b", text):
        return "configs"
    if "structure_trade_plans" in text:
        # Plan rows participate in claiming/expiry and must not be cached by
        # the generic SQL layer. Repositories use their own short-lived,
        # explicitly scoped cache only for safe snapshots.
        return None
    if re.search(r"\b(market_calendar_events|market_key_events|market_flash_news)\b", text):
        return "calendar"
    return None
