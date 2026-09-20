"""Administrator routes for market-structure configuration."""
from __future__ import annotations

from typing import Dict
import json
import time
from collections import defaultdict

from fastapi import APIRouter, Depends

from auth import AuthUser, require_admin
from mysql_repositories import RuntimeStateRepository, get_storage
from llm_governance import AI_SIGNAL_ANALYSIS, STRUCTURE_ANALYSIS


def create_market_structure_config_routes(market_defaults: Dict, plan_defaults: Dict, engine_manager=None) -> APIRouter:
    router = APIRouter()
    allowed = {**market_defaults, **plan_defaults}
    # SETUP defaults live inside the public structure configuration.  They are
    # intentionally kept as a nested map so a setup can inherit the normal
    # structure defaults while still exposing a single public place to edit it.
    setup_default_key = "setup_defaults"
    integer_keys = {
        "pivot_legs", "medium_pivot_legs", "large_pivot_legs", "break_confirm_bars",
        "retest_bars", "range_min_touches", "range_min_bars", "min_segment_bars",
        "trendline_min_touches", "trendline_min_bars",
        "max_event_age_bars", "trend_max_event_age_bars_m1",
        "trend_max_event_age_bars_other", "trend_min_retest_bars",
        "trend_continuation_hold_bars",
        "pressure_plan_valid_bars", "pressure_min_event_confidence",
        "zone_lookback_bars", "zone_min_visits", "zone_min_consecutive_bars", "zone_consecutive_gap_bars", "zone_identity_max_gap_bars", "zone_target_count",
        "pressure_min_rejections", "pivot_zone_min_points", "pivot_zone_target_count",
        "confirmation_bars", "max_plan_lifetime_bars",
        "max_entries_per_opportunity", "cooldown_minutes",
    }
    list_keys = {"allowed_setups", "blocked_setups", "allowed_directions", "blocked_hours"}
    bool_keys = {
        "enabled", "require_reclaim", "enable_zone_pressure",
        "zone_pressure_enabled", "pivot_zone_enabled", "require_retest",
        "invalidate_on_zone_return",
    }
    string_keys = {"entry_mode"}
    inherit_empty_list_keys = {"allowed_setups", "blocked_setups", "allowed_directions", "blocked_hours"}

    ratio_keys = {"zone_min_close_ratio", "pressure_reclaim_ratio", "pressure_min_efficiency"}
    nonnegative_integer_keys = {"cooldown_minutes"}

    def migrate_legacy_config(storage, stored):
        """Materialize the legacy JSON config into normalized MySQL tables."""
        if not isinstance(stored, dict):
            return
        now = int(time.time())
        base = {k: v for k, v in stored.items() if k in allowed or k == setup_default_key}
        if isinstance(stored.get(setup_default_key), dict):
            base[setup_default_key] = stored[setup_default_key]
        storage.execute(
            "INSERT INTO structure_default_configs(user_id,version,config_json,updated_at) VALUES(0,1,?,?) "
            "ON DUPLICATE KEY UPDATE config_json=config_json",
            (json.dumps(base, ensure_ascii=False), now),
        )
        for item in stored.get("profiles") or []:
            if not isinstance(item, dict) or not item.get("symbol") or not item.get("period"):
                continue
            cfg = {k: v for k, v in item.items() if k not in {"symbol", "period", "setup_profiles", "profiles"}}
            storage.execute(
                "INSERT INTO structure_symbol_period_configs(user_id,symbol,period,config_json,updated_at) VALUES(0,?,?,?,?) "
                "ON DUPLICATE KEY UPDATE symbol=symbol",
                (str(item["symbol"]).upper(), str(item["period"]).upper(), json.dumps(cfg, ensure_ascii=False), now),
            )
        for item in stored.get("setup_profiles") or []:
            if not isinstance(item, dict) or not item.get("symbol") or not item.get("period") or not item.get("setup_type"):
                continue
            cfg = {k: v for k, v in item.items() if k not in {"symbol", "period", "setup_type"}}
            storage.execute(
                "INSERT INTO structure_setup_configs(user_id,symbol,period,setup_type,config_json,updated_at) VALUES(0,?,?,?,?,?) "
                "ON DUPLICATE KEY UPDATE setup_type=setup_type",
                (str(item["symbol"]).upper(), str(item["period"]).upper(), str(item["setup_type"]).lower(), json.dumps(cfg, ensure_ascii=False), now),
            )

    def read_normalized(storage):
        default = storage.fetchone("SELECT * FROM structure_default_configs WHERE user_id=0 AND status='active'") or {}
        profiles = storage.fetchall("SELECT * FROM structure_symbol_period_configs WHERE user_id=0 AND status='active' ORDER BY symbol,period")
        setups = storage.fetchall("SELECT * FROM structure_setup_configs WHERE user_id=0 AND status='active' ORDER BY symbol,period,setup_type")
        def decode(row):
            value = row.get("config_json") if row else {}
            if isinstance(value, str):
                try: value = json.loads(value)
                except (TypeError, ValueError): value = {}
            return value if isinstance(value, dict) else {}
        return default, profiles, setups, decode

    def migrate_zone_public_defaults(storage, row, decoded):
        """Tighten untouched legacy public zone defaults once.

        Existing administrators may have deliberately tuned these values, so
        only the original built-in tuple is migrated.  Symbol/period/setup
        rows are never changed; they continue to override the public layer.
        """
        if not row or not isinstance(decoded, dict):
            return decoded
        tightened = {
            "zone_bin_atr": 0.35, "zone_min_close_ratio": 0.15,
            "zone_min_visits": 4, "zone_min_consecutive_bars": 30,
            "zone_consecutive_gap_bars": 0, "zone_leave_atr": 0.7,
            "zone_max_width_atr": 1.2, "pressure_touch_atr": 0.3,
            "pressure_min_rejections": 4,
            "pressure_min_displacement_atr": 1.0,
            "pressure_min_efficiency": 0.6, "pivot_zone_merge_atr": 0.35,
            "pivot_zone_min_points": 3, "pivot_zone_target_count": 6,
        }
        legacy_tuples = (
            {"zone_bin_atr": 0.5, "zone_min_close_ratio": 0.20, "zone_min_visits": 3,
             "zone_leave_atr": 0.5, "zone_max_width_atr": 2.0, "pressure_touch_atr": 0.35,
             "pressure_min_rejections": 3, "pressure_min_displacement_atr": 0.8,
             "pressure_min_efficiency": 0.55, "pivot_zone_merge_atr": 0.45},
            {"zone_bin_atr": 0.4, "zone_min_close_ratio": 0.25, "zone_min_visits": 4,
             "zone_leave_atr": 0.7, "zone_max_width_atr": 1.5, "pressure_touch_atr": 0.3,
             "pressure_min_rejections": 4, "pressure_min_displacement_atr": 1.0,
             "pressure_min_efficiency": 0.6, "pivot_zone_merge_atr": 0.35},
        )
        def same(a, b):
            try: return abs(float(a) - float(b)) < 1e-9
            except (TypeError, ValueError): return a == b
        if not any(all(key in decoded and same(decoded[key], value) for key, value in old.items()) for old in legacy_tuples):
            # The previous public default used four Pivot points.  This is a
            # deliberate global default change (not a symbol-specific tuning),
            # so migrate that untouched value to the new minimum of three.
            if same(decoded.get("pivot_zone_min_points"), 4):
                updated = {**decoded, "pivot_zone_min_points": 3}
                storage.execute(
                    "UPDATE structure_default_configs SET config_json=?,version=version+1,updated_at=? "
                    "WHERE user_id=0 AND status='active' AND version=?",
                    (json.dumps(updated, ensure_ascii=False), int(time.time()), int(row.get("version") or 0)),
                )
                return updated
            return decoded
        updated = {**decoded, **tightened}
        storage.execute(
            "UPDATE structure_default_configs SET config_json=?,version=version+1,updated_at=? "
            "WHERE user_id=0 AND status='active' AND version=?",
            (json.dumps(updated, ensure_ascii=False), int(time.time()), int(row.get("version") or 0)),
        )
        return updated

    def persist_normalized(storage, cfg, profiles, setup_profiles, reason="手工保存结构分析配置"):
        now = int(time.time())
        old_default, old_profiles, old_setups, decode = read_normalized(storage)
        old_default_json = decode(old_default)
        default_version = int(old_default.get("version") or 0) + 1
        storage.execute(
            "INSERT INTO structure_default_configs(user_id,version,config_json,updated_by,updated_at) VALUES(0,?,?,0,?) "
            "ON DUPLICATE KEY UPDATE version=version+1,config_json=VALUES(config_json),updated_at=VALUES(updated_at)",
            (default_version, json.dumps({k: cfg.get(k) for k in allowed if k in cfg} | ({setup_default_key: cfg.get(setup_default_key, {})} if isinstance(cfg.get(setup_default_key), dict) else {}), ensure_ascii=False), now),
        )
        old_p = {(str(x.get('symbol')).upper(), str(x.get('period')).upper()): x for x in old_profiles}
        active_profiles = set()
        for item in profiles:
            symbol, period = str(item['symbol']).upper(), str(item['period']).upper()
            active_profiles.add((symbol, period))
            old = old_p.get((symbol, period), {})
            storage.execute(
                "INSERT INTO structure_symbol_period_configs(user_id,symbol,period,version,config_json,updated_by,updated_at) VALUES(0,?,?,?, ?,0,?) "
                "ON DUPLICATE KEY UPDATE version=version+1,config_json=VALUES(config_json),status='active',updated_at=VALUES(updated_at)",
                (symbol, period, int(old.get('version') or 0) + 1, json.dumps({k:v for k,v in item.items() if k not in {'symbol','period'}}, ensure_ascii=False), now),
            )
        old_s = {(str(x.get('symbol')).upper(), str(x.get('period')).upper(), str(x.get('setup_type')).lower()): x for x in old_setups}
        active_setups = set()
        for item in setup_profiles:
            symbol, period, setup = str(item['symbol']).upper(), str(item['period']).upper(), str(item['setup_type']).lower()
            active_setups.add((symbol, period, setup))
            old = old_s.get((symbol, period, setup), {})
            storage.execute(
                "INSERT INTO structure_setup_configs(user_id,symbol,period,setup_type,version,config_json,updated_by,updated_at) VALUES(0,?,?,?,?,?,0,?) "
                "ON DUPLICATE KEY UPDATE version=version+1,config_json=VALUES(config_json),status='active',updated_at=VALUES(updated_at)",
                (symbol, period, setup, int(old.get('version') or 0) + 1, json.dumps({k:v for k,v in item.items() if k not in {'symbol','period','setup_type'}}, ensure_ascii=False), now),
            )
        for key in set(old_p) - active_profiles:
            storage.execute(
                "UPDATE structure_symbol_period_configs SET status='inactive',updated_at=? WHERE user_id=0 AND symbol=? AND period=?",
                (now, key[0], key[1]),
            )
        for key in set(old_s) - active_setups:
            storage.execute(
                "UPDATE structure_setup_configs SET status='inactive',updated_at=? WHERE user_id=0 AND symbol=? AND period=? AND setup_type=?",
                (now, key[0], key[1], key[2]),
            )
        storage.execute(
            "INSERT INTO structure_config_change_logs(user_id,scope,before_json,after_json,source,reason,created_at) VALUES(0,'default',?,?, 'manual', ?, ?) ",
            (json.dumps(old_default_json, ensure_ascii=False), json.dumps({k: cfg.get(k) for k in allowed if k in cfg} | ({setup_default_key: cfg.get(setup_default_key, {})} if isinstance(cfg.get(setup_default_key), dict) else {}), ensure_ascii=False), reason, now),
        )

    def as_bool(value, default=False):
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off", ""}:
                return False
        if value is None:
            return default
        return bool(value)

    @router.get("/admin/market-structure/config", dependencies=[Depends(require_admin)])
    async def get_config(user: AuthUser = Depends(require_admin)):
        storage = get_storage()
        items = RuntimeStateRepository(0, 0).list_entities("market_structure_config")
        legacy = items[-1] if items and isinstance(items[-1], dict) else {}
        # Old installations kept the complete configuration in runtime state.
        # Materialize it once, then always read the normalized MySQL tables so
        # the UI sees exactly what the save endpoint persisted.
        migrate_legacy_config(storage, legacy)
        default_row, profile_rows, setup_rows, decode = read_normalized(storage)
        normalized_default = decode(default_row)
        normalized_default = migrate_zone_public_defaults(storage, default_row, normalized_default)
        config = {**allowed, **normalized_default}
        if not normalized_default:
            config = {**allowed, **{k: v for k, v in legacy.items() if k in allowed}}
        if not isinstance(config.get(setup_default_key), dict):
            config[setup_default_key] = legacy.get(setup_default_key, {}) if isinstance(legacy.get(setup_default_key), dict) else {}

        profiles = []
        for row in profile_rows:
            item = {"symbol": row.get("symbol"), "period": row.get("period"), **decode(row)}
            profiles.append(item)
        if not profiles and isinstance(legacy.get("profiles"), list):
            profiles = legacy.get("profiles", [])

        setup_profiles = []
        for row in setup_rows:
            item = {
                "symbol": row.get("symbol"),
                "period": row.get("period"),
                "setup_type": row.get("setup_type"),
                **decode(row),
            }
            setup_profiles.append(item)
        if not setup_profiles and isinstance(legacy.get("setup_profiles"), list):
            setup_profiles = legacy.get("setup_profiles", [])
        return {
            "status": "ok",
            "config": {k: v for k, v in config.items() if k in allowed or k == setup_default_key},
            "profiles": profiles,
            "setup_profiles": setup_profiles,
        }

    @router.get("/admin/market-structure/config/effective", dependencies=[Depends(require_admin)])
    async def get_effective_config(symbol: str, period: str, setup_type: str = "", user: AuthUser = Depends(require_admin)):
        from market.services.signal.structure_plan_signal import resolve_structure_plan_config
        effective = resolve_structure_plan_config(symbol, period, setup_type)
        source = {}
        storage = get_storage()
        default_row, profiles, setups, decode = read_normalized(storage)
        profile_row = next((x for x in profiles if str(x.get('symbol')).upper()==symbol.upper() and str(x.get('period')).upper()==period.upper()), None)
        setup_row = next((x for x in setups if str(x.get('symbol')).upper()==symbol.upper() and str(x.get('period')).upper()==period.upper() and str(x.get('setup_type')).lower()==setup_type.lower()), None)
        public_default = decode(default_row)
        setup_defaults = public_default.get("setup_defaults") if isinstance(public_default.get("setup_defaults"), dict) else {}
        setup_default = setup_defaults.get(setup_type, {}) if setup_type else {}
        profile, setup = decode(profile_row), decode(setup_row)
        def has_override(layer, key):
            if key not in layer:
                return False
            value = layer.get(key)
            return not (key in inherit_empty_list_keys and isinstance(value, list) and not value)
        for key in effective:
            source[key] = (
                "setup" if has_override(setup, key)
                else "symbol_period" if has_override(profile, key)
                else "setup_default" if has_override(setup_default, key)
                else "default"
            )
        return {"status": "ok", "symbol": symbol.upper(), "period": period.upper(), "setup_type": setup_type.lower(), "config": effective, "sources": source}

    @router.get("/admin/market-structure/config/overview", dependencies=[Depends(require_admin)])
    async def get_config_overview(user: AuthUser = Depends(require_admin)):
        storage = get_storage()
        default_row, profile_rows, setup_rows, decode = read_normalized(storage)
        profiles = [{"symbol": x.get("symbol"), "period": x.get("period"), "version": x.get("version"), "updated_at": x.get("updated_at"), **decode(x)} for x in profile_rows]
        setups = [{"symbol": x.get("symbol"), "period": x.get("period"), "setup_type": x.get("setup_type"), "version": x.get("version"), "updated_at": x.get("updated_at"), **decode(x)} for x in setup_rows]
        keys = {(str(x.get("symbol")).upper(), str(x.get("period")).upper()) for x in profiles if x.get("symbol") and x.get("period")}
        keys.update((str(x.get("symbol")).upper(), str(x.get("period")).upper()) for x in setups if x.get("symbol") and x.get("period"))
        rows = []
        for symbol, period in sorted(keys):
            local = [x for x in setups if str(x.get("symbol")).upper() == symbol and str(x.get("period")).upper() == period]
            rows.append({"symbol": symbol, "period": period, "has_profile": any(str(x.get("symbol")).upper() == symbol and str(x.get("period")).upper() == period for x in profiles), "setups": [{"setup_type": x.get("setup_type"), "enabled": x.get("enabled", True)} for x in local]})
        return {"status": "ok", "default_configured": bool(default_row), "default": {"version": default_row.get("version", 0), "updated_at": default_row.get("updated_at", 0)}, "items": rows, "profiles": profiles, "setup_profiles": setups}

    @router.delete("/admin/market-structure/config/profile/{symbol}/{period}", dependencies=[Depends(require_admin)])
    async def delete_config_profile(symbol: str, period: str, user: AuthUser = Depends(require_admin)):
        """Remove all symbol/period overrides and restore the public defaults.

        A matrix row represents both the symbol-period engine override and all
        SETUP overrides beneath it.  Keep the rows as inactive records instead
        of hard-deleting them so configuration history/audit remains intact.
        """
        symbol = str(symbol or "").strip().upper()
        period = str(period or "").strip().upper()
        if not symbol or not period:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="symbol 和 period 不能为空")

        storage = get_storage()
        default_row, profile_rows, setup_rows, decode = read_normalized(storage)
        matching_profile = next(
            (row for row in profile_rows
             if str(row.get("symbol") or "").upper() == symbol
             and str(row.get("period") or "").upper() == period),
            None,
        )
        matching_setups = [
            row for row in setup_rows
            if str(row.get("symbol") or "").upper() == symbol
            and str(row.get("period") or "").upper() == period
        ]
        if matching_profile is None and not matching_setups:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"未找到 {symbol} · {period} 的专项配置")

        cfg = decode(default_row)
        profiles = [
            {"symbol": row.get("symbol"), "period": row.get("period"), **decode(row)}
            for row in profile_rows
            if not (str(row.get("symbol") or "").upper() == symbol
                    and str(row.get("period") or "").upper() == period)
        ]
        setup_profiles = [
            {"symbol": row.get("symbol"), "period": row.get("period"),
             "setup_type": row.get("setup_type"), **decode(row)}
            for row in setup_rows
            if not (str(row.get("symbol") or "").upper() == symbol
                    and str(row.get("period") or "").upper() == period)
        ]
        reason = f"删除 {symbol} · {period} 品种/周期及全部 SETUP 专项配置，恢复公共默认"
        persist_normalized(storage, cfg, profiles, setup_profiles, reason)

        # Keep the legacy runtime snapshot aligned for older readers during the
        # migration period; normalized MySQL tables remain the source of truth.
        runtime = RuntimeStateRepository(0, 0)
        runtime_cfg = {k: v for k, v in cfg.items() if k in allowed or k == setup_default_key}
        runtime_cfg["profiles"] = profiles
        runtime_cfg["setup_profiles"] = setup_profiles
        runtime.upsert_entity("market_structure_config", "default", runtime_cfg, status="active")
        return {"status": "ok", "symbol": symbol, "period": period, "deleted": True,
                "message": f"{symbol} · {period} 专项配置已删除，已恢复公共默认"}

    @router.get("/admin/market-structure/config/history", dependencies=[Depends(require_admin)])
    async def get_config_history(limit: int = 50, user: AuthUser = Depends(require_admin)):
        limit = max(1, min(int(limit), 200))
        rows = get_storage().fetchall(
            f"SELECT id, user_id, scope, before_json, after_json, source, reason, created_at "
            f"FROM structure_config_change_logs WHERE user_id=0 ORDER BY created_at DESC, id DESC LIMIT {limit}"
        )
        for row in rows:
            for key in ("before_json", "after_json"):
                if isinstance(row.get(key), str):
                    try: row[key] = json.loads(row[key])
                    except (TypeError, ValueError): pass
        return {"status": "ok", "items": rows}

    @router.post("/admin/market-structure/config/generate", dependencies=[Depends(require_admin)])
    async def generate_config(payload: Dict, user: AuthUser = Depends(require_admin)):
        symbol = str(payload.get("symbol") or "").strip().upper()
        period = str(payload.get("period") or "M5").strip().upper()
        setup_type = str(payload.get("setup_type") or "").strip().lower()
        scope = str(payload.get("scope") or "symbol_period")
        if not symbol or not period or scope not in {"symbol_period", "setup"} or (scope == "setup" and not setup_type):
            return {"status": "failed", "reason": "symbol、period 必填；SETUP 配置还需要 setup_type"}
        storage = get_storage(); default_row, profile_rows, setup_rows, decode = read_normalized(storage)
        base = decode(default_row)
        profile = next((x for x in profile_rows if str(x.get("symbol")).upper()==symbol and str(x.get("period")).upper()==period), None)
        if profile: base = {**base, **decode(profile)}
        setup = next((x for x in setup_rows if str(x.get("symbol")).upper()==symbol and str(x.get("period")).upper()==period and str(x.get("setup_type")).lower()==setup_type), None)
        if scope == "setup" and setup: base = {**base, **decode(setup)}
        candidate = dict(base)
        changes = []
        overrides = payload.get("overrides") if isinstance(payload.get("overrides"), dict) else {}
        for key, value in overrides.items():
            if key not in allowed: continue
            if base.get(key) != value:
                changes.append({"field": key, "before": base.get(key), "after": value, "reason": payload.get("reason") or "特殊配置生成器"})
            candidate[key] = value
        candidate.update({"symbol": symbol, "period": period, **({"setup_type": setup_type} if scope == "setup" else {})})
        return {"status": "ok", "scope": scope, "base": base, "candidate": candidate, "changes": changes}

    @router.put("/admin/market-structure/config", dependencies=[Depends(require_admin)])
    async def put_config(payload: Dict, user: AuthUser = Depends(require_admin)):
        cfg = dict(allowed)
        def normalize_fields(item):
            """Normalize one configuration layer using the same rules.

            Public SETUP defaults and symbol/setup overrides must have identical
            types; otherwise a value saved from the editor can compare unequal
            to the resolver's value (for example ``"2"`` vs ``2``).
            """
            result = {}
            for key in allowed:
                if key in list_keys and key in item:
                    value = item.get(key)
                    if isinstance(value, str):
                        value = [part.strip() for part in value.split(",") if part.strip()]
                    if isinstance(value, list):
                        result[key] = value
                    continue
                if key in bool_keys and key in item:
                    result[key] = as_bool(item.get(key), key == "enabled")
                    continue
                if key in string_keys and key in item:
                    result[key] = str(item.get(key) or "").strip()
                    continue
                if key in item:
                    try:
                        value = float(item[key])
                        if key in ratio_keys:
                            result[key] = min(1.0, max(0.0, value))
                        else:
                            result[key] = (max(0, int(value)) if key in nonnegative_integer_keys
                                           else max(1, int(value)) if key in integer_keys
                                           else max(0.0, value))
                    except (TypeError, ValueError):
                        pass
            return result
        setup_defaults = payload.get(setup_default_key)
        if isinstance(setup_defaults, dict):
            cfg[setup_default_key] = {
                str(setup).strip().lower(): normalize_fields(value)
                for setup, value in setup_defaults.items()
                if isinstance(value, dict)
            }
        def normalize(item, *, setup=False):
            if not item.get("symbol") or not item.get("period") or (setup and not item.get("setup_type")):
                return None
            result = {"symbol": str(item["symbol"]).strip(), "period": str(item["period"]).upper()}
            if setup:
                result["setup_type"] = str(item["setup_type"]).strip().lower()
            result.update(normalize_fields(item))
            return result
        for key in allowed:
            if key in list_keys and key in payload:
                value = payload.get(key)
                if isinstance(value, str):
                    value = [part.strip() for part in value.split(",") if part.strip()]
                if isinstance(value, list):
                    cfg[key] = value
                continue
            if key in bool_keys and key in payload:
                cfg[key] = as_bool(payload.get(key), key == "enabled")
                continue
            if key in string_keys and key in payload:
                cfg[key] = str(payload.get(key) or "").strip()
                continue
            if key in payload:
                try:
                    value = float(payload[key])
                    if key in ratio_keys:
                        cfg[key] = min(1.0, max(0.0, value))
                    else:
                        cfg[key] = (max(0, int(value)) if key in nonnegative_integer_keys
                                    else max(1, int(value)) if key in integer_keys
                                    else max(0.0, value))
                except (TypeError, ValueError):
                    pass
        if not isinstance(cfg.get(setup_default_key), dict):
            cfg[setup_default_key] = {}
        profiles = [x for x in (normalize(item) for item in (payload.get("profiles") or []) if isinstance(item, dict)) if x]
        setup_profiles = []
        for item in (payload.get("setup_profiles") or []):
            if not isinstance(item, dict):
                continue
            normalized = normalize(item, setup=True)
            # A metadata-only row is not a real override.  Omitting it lets
            # persist_normalized mark a previously saved empty override inactive.
            if normalized and any(k not in {"symbol", "period", "setup_type"} for k in normalized):
                setup_profiles.append(normalized)
        cfg["profiles"] = profiles; cfg["setup_profiles"] = setup_profiles
        RuntimeStateRepository(0, 0).upsert_entity("market_structure_config", "default", cfg, status="active")
        persist_normalized(get_storage(), cfg, profiles, setup_profiles, str(payload.get("reason") or "手工保存结构分析配置"))
        return {"status": "ok", "config": {k: v for k, v in cfg.items() if k in allowed or k == setup_default_key}, "profiles": profiles, "setup_profiles": setup_profiles}

    @router.post("/admin/market-structure/optimize-setups", dependencies=[Depends(require_admin)])
    async def optimize_setups(payload: Dict | None = None, user: AuthUser = Depends(require_admin)):
        """Generate conservative symbol/period/setup overrides from recent PnL.

        This is intentionally deterministic and explainable: it aggregates closed
        positions (not partial exit legs), applies minimum sample counts, and only
        auto-disables consistently losing setups.  ``apply`` controls persistence.
        """
        payload = payload or {}
        now = int(time.time())
        start_at, end_at = payload.get("start_at"), payload.get("end_at")
        try:
            start = int(float(start_at)) if start_at is not None else now - max(7, min(int(payload.get("days") or 30), 90)) * 86400
            end = int(float(end_at)) if end_at is not None else now
        except (TypeError, ValueError):
            return {"status": "failed", "reason": "开始时间和结束时间必须是有效时间戳"}
        if start >= end or end > now or end - start > 365 * 86400:
            return {"status": "failed", "reason": "时间范围无效：开始时间必须早于结束时间，且范围不超过365天"}
        days = max(1, round((end - start) / 86400, 2))
        storage = get_storage()
        stored_items = RuntimeStateRepository(0, 0).list_entities("market_structure_config")
        stored_config = stored_items[-1] if stored_items and isinstance(stored_items[-1], dict) else {}
        existing_setup = {(str(item.get("symbol") or "").upper(), str(item.get("period") or "").upper(), str(item.get("setup_type") or "").lower()): item
                        for item in (stored_config.get("setup_profiles") or []) if isinstance(item, dict)}
        rows = storage.fetchall(
            "SELECT position_id, symbol, net_profit, closed_at, position_attribution_json "
            "FROM paper_trades WHERE closed_at>=? AND closed_at<=? ORDER BY closed_at",
            (start, end),
        )
        # Live MT5 deals use a different schema; normalize them to the same
        # position-level shape before aggregation.  Partial deals are merged
        # by mt5_position_id just like Paper partial exits.
        rows += storage.fetchall(
            "SELECT mt5_position_id AS position_id, symbol, "
            "(COALESCE(profit,0)+COALESCE(swap,0)+COALESCE(commission,0)) AS net_profit, "
            "deal_timestamp AS closed_at, position_attribution_json "
            "FROM live_trade_deals WHERE deal_timestamp>=? AND deal_timestamp<=? "
            "ORDER BY deal_timestamp",
            (start, end),
        )
        # Aggregate all partial exits into one position so split TP does not
        # overweight a setup's apparent win rate.
        positions = {}
        for row in rows:
            try:
                attr = row.get("position_attribution_json")
                attr = attr if isinstance(attr, dict) else json.loads(attr or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                attr = {}
            if attr.get("signal_source") != "structure_plan":
                continue
            setup = str(attr.get("setup_type") or attr.get("selected_setup_type") or "").strip().lower()
            if not setup:
                continue
            key = str(row.get("position_id") or f"{row.get('symbol')}:{row.get('closed_at')}:{setup}")
            item = positions.setdefault(key, {"symbol": str(row.get("symbol") or "").strip(),
                                               "period": str(attr.get("signal_source_period") or "M5").upper(),
                                               "setup_type": setup, "pnl": 0.0,
                                               "closed_at": int(row.get("closed_at") or 0)})
            item["pnl"] += float(row.get("net_profit") or 0)
        grouped = defaultdict(list)
        for item in positions.values():
            if item["symbol"]:
                grouped[(item["symbol"].upper(), item["period"], item["setup_type"])].append(item)
        proposals, diagnostics = [], []
        existing_profiles = {(str(item.get("symbol") or "").upper(), str(item.get("period") or "").upper()): item
                             for item in (stored_config.get("profiles") or []) if isinstance(item, dict)}
        profile_diagnostics, conflicts = [], []
        for (symbol, period, setup), pnls in sorted(grouped.items()):
            if len(pnls) < 3:
                continue
            values = [float(item["pnl"]) for item in pnls]
            net = sum(values); wins = sum(1 for value in values if value > 0)
            losses = sum(1 for value in values if value < 0)
            win_rate = wins / len(pnls)
            recent_values = [float(item["pnl"]) for item in pnls if item.get("closed_at", 0) >= now - 2 * 86400]
            recent_net = sum(recent_values)
            profile = {"symbol": symbol, "period": period, "setup_type": setup,
                       "enabled": True, "allowed_directions": ["buy", "sell"]}
            reasons = []
            # Losing setups get confirmation/reclaim gates; repeated weak
            # setups are disabled until the user explicitly re-enables them.
            # A setup that was weak over 30 days but profitable in the last
            # two days is considered recovered; keep it enabled and report
            # the positive recent evidence instead of tightening it again.
            recovered = setup == "trend_continuation" and recent_values and recent_net > 0
            if net < 0 and not recovered:
                profile.update({"require_reclaim": True, "confirmation_bars": 2,
                                "min_displacement_atr": 0.4, "min_real_risk_reward": 1.3})
                reasons.append("净亏损，增加回收确认、两根确认K线和最小位移过滤")
                if win_rate < 0.40 or losses >= wins * 2:
                    profile["enabled"] = False
                    reasons.append("胜率低于40%或亏损次数至少为盈利次数2倍，暂时停用")
            if "breakout" in setup or "triangle" in setup:
                profile.update({"entry_mode": "breakout_retest", "min_body_atr": 0.5})
                reasons.append("突破类要求实体和回踩确认")
            elif "location" in setup or "reversal" in setup or "sweep" in setup:
                profile.update({"entry_mode": "touch_and_reclaim", "require_reclaim": True})
                reasons.append("反转/位置类要求触碰后收盘回收")
            if net > 0:
                reasons.append("净盈利，保留当前参数；成功经验是该品种/周期下该 Setup 可继续交易")
            if recovered:
                reasons.append(f"趋势策略近2天净盈利 {recent_net:.2f}，视为调整后已改善，不再收紧")
            previous = existing_setup.get((symbol, period, setup), {})
            changes = []
            for field, value in profile.items():
                if previous.get(field) != value:
                    changes.append(f"{field}: {previous.get(field, '未配置')} → {value}")
            diagnostics.append({"symbol": symbol, "period": period, "setup_type": setup,
                               "orders": len(pnls), "net_pnl": round(net, 2),
                               "recent_orders": len(recent_values), "recent_net_pnl": round(recent_net, 2),
                               "win_rate": round(win_rate * 100, 2), "success_evidence": {
                                   "profitable": net > 0, "winning_orders": wins,
                                   "average_win": round(sum(v for v in values if v > 0) / wins, 2) if wins else 0,
                               }, "reasons": reasons})
            diagnostics[-1]["proposed_enabled"] = profile.get("enabled", True)
            diagnostics[-1]["changes"] = "；".join(changes) if changes else "保持现有配置"
            proposals.append(profile)
        # Generate independent symbol+period diagnostics. These profiles only
        # carry aggregate fields (currently the allowed SETUP whitelist).
        symbol_groups = defaultdict(list)
        setup_by_symbol_period = defaultdict(list)
        for (symbol, period, setup), pnls in grouped.items():
            symbol_groups[(symbol, period)].extend(pnls)
            setup_by_symbol_period[(symbol, period)].append(setup)
        symbol_profiles = []
        symbol_default_profiles, symbol_default_diagnostics = [], []
        for (symbol, period), pnls in sorted(symbol_groups.items()):
            if len(pnls) < 3:
                continue
            values = [float(item["pnl"]) for item in pnls]
            net = sum(values)
            observed = sorted(set(setup_by_symbol_period[(symbol, period)]))
            profile = {"symbol": symbol, "period": period}
            reasons = []
            setup_diags = [item for item in diagnostics if item["symbol"] == symbol and item["period"] == period]
            profitable = sorted({item["setup_type"] for item in setup_diags
                                 if item.get("net_pnl", 0) >= 0 and item.get("proposed_enabled", True)})
            if net > 0 and profitable:
                profile["allowed_setups"] = profitable
                reasons.append("按品种周期汇总，仅保留历史净盈亏不为负且满足样本条件的 SETUP")
            else:
                reasons.append("品种周期整体净亏损，不自动收紧允许 SETUP，避免把机会全部关闭")
            previous = existing_profiles.get((symbol, period), {})
            changes = [f"{field}: {previous.get(field, '未配置')} → {value}"
                       for field, value in profile.items() if field not in {"symbol", "period"}
                       and previous.get(field) != value]
            profile_diagnostics.append({
                "symbol": symbol, "period": period, "orders": len(pnls),
                "winning_orders": sum(1 for value in values if value > 0),
                "win_rate": round(sum(1 for value in values if value > 0) / len(values) * 100, 2),
                "net_pnl": round(net, 2), "observed_setups": observed,
                "changes": "；".join(changes) if changes else "保持现有配置",
                "reasons": reasons, "proposed": bool(changes),
            })
            if len(profile) > 2:
                symbol_profiles.append(profile)
        # Symbol-wide diagnostics are intentionally conservative: only emit a
        # proposal when the same setup is observed across at least two periods
        # and the sample is large enough.  A single weak timeframe must not
        # change every period of the instrument.
        symbol_all = defaultdict(list)
        symbol_setup_periods = defaultdict(set)
        for (symbol, period, setup), pnls in grouped.items():
            symbol_all[symbol].extend(pnls)
            symbol_setup_periods[(symbol, setup)].add(period)
        for symbol, pnls in sorted(symbol_all.items()):
            if len(pnls) < 10:
                continue
            values = [float(item["pnl"]) for item in pnls]
            setup_net = defaultdict(float)
            setup_count = defaultdict(int)
            for (item_symbol, _period, setup), setup_pnls in grouped.items():
                if item_symbol != symbol:
                    continue
                setup_net[setup] += sum(float(x["pnl"]) for x in setup_pnls)
                setup_count[setup] += len(setup_pnls)
            stable_losses = [setup for setup, count in setup_count.items()
                             if count >= 6 and len(symbol_setup_periods[(symbol, setup)]) >= 2
                             and setup_net[setup] < 0]
            profile = {"symbol": symbol, "period": "*"}
            reasons = []
            if stable_losses:
                profile["blocked_setups"] = sorted(stable_losses)
                reasons.append("该 SETUP 在至少两个周期持续净亏损，建议品种级暂时禁止")
            diagnostic = {
                "symbol": symbol, "period": "*", "orders": len(pnls),
                "winning_orders": sum(1 for value in values if value > 0),
                "win_rate": round(sum(1 for value in values if value > 0) / len(values) * 100, 2),
                "net_pnl": round(sum(values), 2),
                "stable_loss_setups": sorted(stable_losses),
                "changes": "；".join(f"禁止 {x}" for x in stable_losses) if stable_losses else "保持",
                "reasons": reasons or ["未发现跨周期一致的系统性亏损"],
                "proposed": bool(stable_losses),
            }
            symbol_default_diagnostics.append(diagnostic)
            if len(profile) > 2:
                symbol_default_profiles.append(profile)
        # Surface setup-level conflicts without merging them into the profile.
        conflict_fields = ("entry_mode", "confirmation_bars", "min_displacement_atr",
                           "require_reclaim", "min_real_risk_reward", "min_body_atr",
                           "entry_zone_atr", "stop_buffer_atr", "target_buffer_atr",
                           "max_plan_lifetime_bars", "require_retest", "retest_tolerance_atr")
        conflict_labels = {
            "entry_mode": "入场方式", "confirmation_bars": "确认K线数",
            "min_displacement_atr": "最小位移 ATR", "require_reclaim": "要求回收",
            "min_real_risk_reward": "最低真实盈亏比", "min_body_atr": "突破实体 ATR",
            "entry_zone_atr": "入场区域 ATR", "stop_buffer_atr": "止损缓冲 ATR",
            "target_buffer_atr": "止盈缓冲 ATR", "max_plan_lifetime_bars": "计划安全兜底K线",
            "require_retest": "要求回踩", "retest_tolerance_atr": "回踩容差 ATR",
        }
        for (symbol, period), _setups in sorted(setup_by_symbol_period.items()):
            setup_rows = [item for item in proposals if item["symbol"] == symbol and item["period"] == period]
            for field in conflict_fields:
                values = [{"setup_type": item["setup_type"], "value": item.get(field)}
                          for item in setup_rows if field in item]
                if len({json.dumps(item["value"], sort_keys=True, ensure_ascii=False) for item in values}) > 1:
                    conflicts.append({"symbol": symbol, "period": period, "field": field,
                                      "field_label": conflict_labels.get(field, field),
                                      "values": values,
                                      "reason": "不同 SETUP 的建议不同，只保留在 SETUP 专项层；请分别确认各 SETUP，不要合并到品种+周期层"})

        # Applying a reviewed preview must use exactly the rows the admin saw,
        # rather than silently recomputing them between preview and apply.
        if payload.get("apply") and isinstance(payload.get("proposals"), list):
            proposals = [item for item in payload["proposals"] if isinstance(item, dict)]
            if isinstance(payload.get("symbol_profiles"), list):
                symbol_profiles = [item for item in payload["symbol_profiles"] if isinstance(item, dict)]
            if isinstance(payload.get("symbol_default_profiles"), list):
                symbol_default_profiles = [item for item in payload["symbol_default_profiles"] if isinstance(item, dict)]
            else:
                symbol_profiles = []
        # Keep symbol/period recommendations independent from setup selections.
        # In particular, do not rebuild this list from ``proposals`` here:
        # otherwise selecting one setup would implicitly apply its whitelist.
        applied = False
        if bool(payload.get("apply")):
            current = RuntimeStateRepository(0, 0).list_entities("market_structure_config")
            stored = current[-1] if current and isinstance(current[-1], dict) else {}
            existing = [item for item in (stored.get("setup_profiles") or []) if isinstance(item, dict)]
            index = {(str(item.get("symbol")).upper(), str(item.get("period")).upper(), str(item.get("setup_type")).lower()): item for item in existing}
            for item in proposals:
                index[(item["symbol"], item["period"], item["setup_type"])] = item
            merged = list(index.values())
            cfg = {k: stored.get(k, value) for k, value in allowed.items()}
            profile_items = [item for item in (stored.get("profiles") or []) if isinstance(item, dict)]
            profile_index = {(str(item.get("symbol")).upper(), str(item.get("period")).upper()): item for item in profile_items}
            for item in symbol_profiles:
                key = (item["symbol"], item["period"])
                profile_index[key] = {**profile_index.get(key, {}), **item}
            for item in symbol_default_profiles:
                key = (item["symbol"], "*")
                profile_index[key] = {**profile_index.get(key, {}), **item}
            cfg["profiles"] = list(profile_index.values())
            cfg["setup_profiles"] = merged
            RuntimeStateRepository(0, 0).upsert_entity("market_structure_config", "default", cfg, status="active")
            # Keep the direct API apply path consistent with the normal save
            # path.  Without this write, a caller that applies the preview
            # outside the UI would only update the legacy runtime snapshot and
            # the normalized MySQL tables could immediately win on the next
            # reload.
            persist_normalized(storage, cfg, list(profile_index.values()), merged,
                               reason="应用结构配置历史优化建议")
            applied = True
        return {"status": "ok", "days": days, "start_at": start, "end_at": end, "applied": applied,
                "proposals": proposals, "symbol_profiles": symbol_profiles,
                "diagnostics": diagnostics, "profile_diagnostics": profile_diagnostics,
                "symbol_default_profiles": symbol_default_profiles,
                "symbol_default_diagnostics": symbol_default_diagnostics,
                "conflicts": conflicts}

    @router.post("/admin/market-structure/optimize-setups/review", dependencies=[Depends(require_admin)])
    async def review_setup_proposals(payload: Dict, user: AuthUser = Depends(require_admin)):
        """Have the LLM review deterministic proposals without changing config."""
        if engine_manager is None:
            return {"status": "unavailable", "reason": "未配置大模型引擎"}
        proposals = payload.get("proposals") or []
        diagnostics = payload.get("diagnostics") or []
        symbol_profiles = payload.get("symbol_profiles") or []
        profile_diagnostics = payload.get("profile_diagnostics") or []
        conflicts = payload.get("conflicts") or []
        if not proposals and not symbol_profiles:
            return {"status": "skipped", "reason": "没有可供复核的优化建议"}
        prompt = (
            "请审核以下由确定性规则生成的结构交易 SETUP 配置建议。只依据提供的历史统计，"
            "分别判断建议是否合理，指出应保留、调整或拒绝的建议。不得直接修改配置。"
            "严格返回 JSON：{\"summary\":\"\",\"recommendations\":[{\"symbol\":\"\",\"period\":\"\",\"setup_type\":\"\",\"decision\":\"apply|reject|review\",\"reason\":\"\",\"risk\":\"\"}],\"global_notes\":[\"\"]}。"
            "样本少于10笔只能 review，不得建议停用。\n\n"
            + json.dumps({"proposals": proposals, "diagnostics": diagnostics,
                          "symbol_profiles": symbol_profiles,
                          "profile_diagnostics": profile_diagnostics,
                          "conflicts": conflicts}, ensure_ascii=False, default=str)
        )
        try:
            engine = engine_manager.get_engine_for_user(user.user_id)
            review = engine.llm_service.call_llm(
                prompt, system_prompt="你是交易配置建议审核器，只做复核，不直接写入配置。",
                scene_code=STRUCTURE_ANALYSIS, object_type="structure_setup_optimizer_review",
                object_id=f"{int(time.time())}:{user.user_id}", max_tokens=3500,
            )
            return {"status": "ok", "review": review or {}}
        except Exception as exc:
            return {"status": "failed", "reason": str(exc)[:500]}

    return router
