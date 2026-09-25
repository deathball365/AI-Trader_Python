"""Resolve the four-layer market-structure configuration model."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterable

_GROUPS = {"structure", "hierarchy", "structure_hierarchy", "primary_structure_rules",
           "pattern_rules", "event_rules", "execution", "plan", "runtime"}


def decode(row: Dict | None) -> Dict:
    if not row:
        return {}
    value = row.get("config_json")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return value if isinstance(value, dict) else {}


def _leaves(layer: Any) -> Iterable[tuple[str, Any]]:
    if not isinstance(layer, dict):
        return
    for key, value in layer.items():
        if key == "setup_defaults":
            yield key, value
        elif isinstance(value, dict):
            yield from _leaves(value)
        else:
            yield key, value


def _runtime_values(layer: Dict, allowed: set[str]) -> Dict:
    return {key: value for key, value in _leaves(layer) if key in allowed}


def _merge(target: Dict, layer: Dict, allowed: set[str], *, inherit_empty_lists=False) -> None:
    for key, value in _runtime_values(layer, allowed).items():
        if (inherit_empty_lists and key in {"allowed_directions", "blocked_hours", "blocked_setups"}
                and isinstance(value, list) and not value):
            continue
        target[key] = value


def _setup_defaults(base: Dict, setup: str) -> Dict:
    values = base.get("setup_defaults")
    if not isinstance(values, dict):
        return {}
    item = values.get(setup)
    return item if isinstance(item, dict) else {}


def _rows(storage, table: str, symbol: str, period: str, setup: str = ""):
    if table == "structure_symbol_period_configs":
        return storage.fetchall(
            "SELECT period, config_json FROM structure_symbol_period_configs "
            "WHERE user_id=0 AND symbol=? AND period IN (?, '*') AND status='active'",
            (symbol, period),
        )
    return storage.fetchall(
        "SELECT period, config_json FROM structure_setup_configs "
        "WHERE user_id=0 AND symbol=? AND period IN (?, '*') AND setup_type=? AND status='active'",
        (symbol, period, setup),
    )


def resolve(symbol: str, period: str, setup_type: str, defaults: Dict,
            repository_factory: Callable[[], object] | None = None) -> Dict:
    """Resolve public structure, symbol-period, public SETUP and SETUP layers.

    ``repository_factory`` is retained for caller compatibility, but legacy
    runtime-state configuration is deliberately no longer consulted.
    """
    config = dict(defaults)
    allowed = set(defaults)
    wanted_symbol = str(symbol or "").strip().upper()
    wanted_period = str(period or "").strip().upper()
    wanted_setup = str(setup_type or "").strip().lower()
    try:
        from mysql_repositories import get_storage
        storage = get_storage()
        default_row = storage.fetchone(
            "SELECT config_json FROM structure_default_configs WHERE user_id=0 AND status='active'"
        )
        symbol_rows = _rows(storage, "structure_symbol_period_configs", wanted_symbol, wanted_period)
        setup_rows = (_rows(storage, "structure_setup_configs", wanted_symbol, wanted_period, wanted_setup)
                      if wanted_setup and wanted_setup != "__builder__" else [])
        base = decode(default_row)
        symbol_default_row = next((row for row in symbol_rows if str(row.get("period") or "") == "*"), None)
        symbol_row = next((row for row in symbol_rows if str(row.get("period") or "").upper() == wanted_period), None)
        setup_symbol_row = next((row for row in setup_rows if str(row.get("period") or "") == "*"), None)
        setup_row = next((row for row in setup_rows if str(row.get("period") or "").upper() == wanted_period), None)
        symbol_default, profile = decode(symbol_default_row), decode(symbol_row)
        setup_symbol_profile, setup_profile = decode(setup_symbol_row), decode(setup_row)
        _merge(config, base, allowed)
        if wanted_setup and wanted_setup != "__builder__":
            _merge(config, _setup_defaults(base, wanted_setup), allowed, inherit_empty_lists=True)
        _merge(config, symbol_default, allowed, inherit_empty_lists=True)
        if wanted_setup and wanted_setup != "__builder__":
            _merge(config, setup_symbol_profile, allowed, inherit_empty_lists=True)
        _merge(config, profile, allowed, inherit_empty_lists=True)
        _merge(config, setup_profile, allowed, inherit_empty_lists=True)
        config["_structure_layers"] = {
            "default": base,
            "symbol_period": symbol_default | profile,
            "setup_default": _setup_defaults(base, wanted_setup),
            "setup": setup_symbol_profile | setup_profile,
        }
        if setup_type == "__builder__":
            rows = storage.fetchall(
                "SELECT setup_type, period, config_json FROM structure_setup_configs "
                "WHERE user_id=0 AND symbol=? AND period IN ('*',?) AND status='active' ORDER BY period DESC",
                (wanted_symbol, wanted_period),
            )
            config["_setup_profiles"] = [
                {"symbol": wanted_symbol, "period": str(row.get("period") or wanted_period).upper(),
                 "setup_type": str(row.get("setup_type") or "").lower(), **decode(row)}
                for row in rows
            ]
    except Exception as exc:
        print(f"[StructurePlan] 结构配置读取失败，使用公共默认值: {exc}")
        if setup_type == "__builder__":
            config["_setup_profiles"] = []
    return config
