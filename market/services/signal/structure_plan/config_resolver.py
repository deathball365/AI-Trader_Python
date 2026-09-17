"""Resolve market-layer structure-plan configuration."""
from __future__ import annotations

import json
from typing import Callable, Dict


def resolve(
    symbol: str,
    period: str,
    setup_type: str,
    defaults: Dict,
    repository_factory: Callable[[], object],
) -> Dict:
    """Apply defaults, symbol/period overrides, then setup override.

    Storage is injected to keep this resolver deterministic in tests and free
    of a hard dependency on the runtime repository implementation.
    """
    config = dict(defaults)
    try:
        # Normalized MySQL configuration is authoritative.  Keep the legacy
        # runtime entity as a fallback for old installations/tests.
        normalized = None
        try:
            from mysql_repositories import get_storage
            storage = get_storage()
            wanted_symbol = str(symbol or "").upper()
            wanted_period = str(period or "").upper()
            wanted_setup = str(setup_type or "").strip().lower()
            default_row = storage.fetchone(
                "SELECT config_json FROM structure_default_configs WHERE user_id=0 AND status='active'"
            )
            symbol_rows = storage.fetchall(
                "SELECT period, config_json FROM structure_symbol_period_configs WHERE user_id=0 AND symbol=? AND period IN (?, '*') AND status='active'",
                (wanted_symbol, wanted_period),
            )
            symbol_row = next((row for row in symbol_rows if str(row.get("period") or "").upper() == wanted_period), None)
            symbol_default_row = next((row for row in symbol_rows if str(row.get("period") or "") == "*"), None)
            setup_row = None
            setup_symbol_row = None
            if wanted_setup and wanted_setup != "__builder__":
                setup_rows = storage.fetchall(
                    "SELECT period, config_json FROM structure_setup_configs WHERE user_id=0 AND symbol=? AND period IN (?, '*') AND setup_type=? AND status='active'",
                    (wanted_symbol, wanted_period, wanted_setup),
                )
                setup_row = next((row for row in setup_rows if str(row.get("period") or "").upper() == wanted_period), None)
                setup_symbol_row = next((row for row in setup_rows if str(row.get("period") or "") == "*"), None)
            def decode(row):
                if not row:
                    return {}
                value = row.get("config_json")
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except (TypeError, ValueError):
                        return {}
                return value if isinstance(value, dict) else {}
            if default_row or symbol_default_row or symbol_row or setup_symbol_row or setup_row:
                normalized = (decode(default_row), decode(symbol_default_row), decode(symbol_row), decode(setup_symbol_row), decode(setup_row))
        except Exception as exc:
            print(f"[StructurePlan] 规范化配置读取失败，回退旧配置: {exc}")
        if normalized is not None:
            base, symbol_default, profile, setup_symbol_profile, setup_profile = normalized
            allowed = set(defaults)
            setup_defaults = base.get("setup_defaults") if isinstance(base.get("setup_defaults"), dict) else {}
            list_inherit = {"allowed_setups", "allowed_directions", "blocked_hours"}
            def merge_layer(target, layer, inherit_empty_lists=False):
                for key, value in layer.items():
                    if key not in allowed:
                        continue
                    if inherit_empty_lists and key in list_inherit and isinstance(value, list) and not value:
                        continue
                    target[key] = value
            # setup_defaults is a nested public layer, not an engine option.
            # Apply it between public structure defaults and symbol/period.
            merge_layer(config, base)
            if wanted_setup and wanted_setup != "__builder__":
                merge_layer(config, setup_defaults.get(wanted_setup, {}), inherit_empty_lists=True)
            merge_layer(config, symbol_default, inherit_empty_lists=True)
            if wanted_setup and wanted_setup != "__builder__":
                merge_layer(config, setup_symbol_profile, inherit_empty_lists=True)
            merge_layer(config, profile, inherit_empty_lists=True)
            merge_layer(config, setup_profile, inherit_empty_lists=True)
            # A scalar supplied by a symbol/period (or its setup) is an
            # explicit override.  The public default remains only a fallback;
            # the zone engine uses its hidden per-period runtime default when
            # this marker is absent.
            config["_zone_lookback_override"] = (
                "zone_lookback_bars" in symbol_default or "zone_lookback_bars" in profile or "zone_lookback_bars" in setup_symbol_profile or "zone_lookback_bars" in setup_profile
            )
            config["_zone_min_consecutive_override"] = (
                "zone_min_consecutive_bars" in symbol_default or "zone_min_consecutive_bars" in profile or "zone_min_consecutive_bars" in setup_symbol_profile or "zone_min_consecutive_bars" in setup_profile
            )
            if setup_type == "__builder__":
                try:
                    rows = get_storage().fetchall(
                        "SELECT setup_type, period, config_json FROM structure_setup_configs WHERE user_id=0 AND symbol=? AND period IN ('*',?) AND status='active' ORDER BY period DESC",
                        (str(symbol or '').upper(), str(period or '').upper()),
                    )
                    config["_setup_profiles"] = [
                        {"symbol": str(symbol or '').upper(), "period": str(row.get("period") or period).upper(),
                         "setup_type": str(row.get("setup_type") or "").lower(), **(
                             json.loads(row.get("config_json")) if isinstance(row.get("config_json"), str) else (row.get("config_json") or {})
                         )} for row in rows
                    ]
                except Exception:
                    config["_setup_profiles"] = []
            return config

        stored_items = repository_factory().list_entities("market_structure_config")
        stored = stored_items[-1] if stored_items else {}
        allowed = set(defaults)
        if not isinstance(stored, dict):
            stored = {}
        list_inherit = {"allowed_setups", "allowed_directions", "blocked_hours"}
        def merge_layer(target, layer, inherit_empty_lists=False):
            for key, value in layer.items():
                if key not in allowed:
                    continue
                if inherit_empty_lists and key in list_inherit and isinstance(value, list) and not value:
                    continue
                target[key] = value
        merge_layer(config, stored)
        setup_defaults = stored.get("setup_defaults") if isinstance(stored.get("setup_defaults"), dict) else {}
        wanted_symbol = str(symbol or "").upper()
        wanted_period = str(period or "").upper()
        profiles = stored.get("profiles") or []
        for profile in profiles:
            if (str(profile.get("symbol") or "").upper() == wanted_symbol
                    and str(profile.get("period") or "").upper() == wanted_period):
                merge_layer(config, profile, inherit_empty_lists=True)
                config["_zone_lookback_override"] = "zone_lookback_bars" in profile
                config["_zone_min_consecutive_override"] = "zone_min_consecutive_bars" in profile
                break
        matching = [
            profile for profile in (stored.get("setup_profiles") or [])
            if (str(profile.get("symbol") or "").upper() == wanted_symbol
                and str(profile.get("period") or "").upper() == wanted_period)
        ]
        wanted_setup = str(setup_type or "").strip().lower()
        if wanted_setup and wanted_setup != "__builder__":
            merge_layer(config, setup_defaults.get(wanted_setup, {}), inherit_empty_lists=True)
        if wanted_setup:
            for profile in matching:
                if str(profile.get("setup_type") or "").strip().lower() == wanted_setup:
                    merge_layer(config, profile, inherit_empty_lists=True)
                    break
        if setup_type == "__builder__":
            config["_setup_profiles"] = matching
    except Exception as exc:
        print(f"[StructurePlan] 公共计划配置读取失败，使用默认值: {exc}")
    return config
