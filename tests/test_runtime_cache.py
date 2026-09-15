from runtime_cache import (
    cache_domain_for_sql,
    invalidate,
    reset_stats,
    sql_read_cache,
    stats,
)


def test_sql_cache_normalises_sql_and_params():
    reset_stats()
    sql_read_cache.set_sql("SELECT  *  FROM user_strategy_configs", [1, {"b": 2, "a": 1}], "strategies", {"ok": True})
    assert sql_read_cache.get_sql(" select from user_strategy_configs ".replace("select from", "SELECT  *  FROM"), (1, {"a": 1, "b": 2}), "strategies") == {"ok": True}
    assert stats()["hits"]["strategies"] == 1


def test_safe_and_unsafe_sql_domains():
    assert cache_domain_for_sql("SELECT * FROM structure_symbol_period_configs WHERE symbol=?") == "configs"
    assert cache_domain_for_sql("SELECT * FROM market_calendar_events WHERE event_date=?") == "calendar"
    assert cache_domain_for_sql("SELECT * FROM structure_trade_plans WHERE status='active'") is None
    assert cache_domain_for_sql("SELECT * FROM trading_accounts WHERE id=?") is None
    assert cache_domain_for_sql("SELECT * FROM strategy_deployments WHERE status='active'") is None
    assert cache_domain_for_sql("SELECT * FROM structure_symbol_period_configs FOR UPDATE") is None


def test_invalidation_bumps_generation_and_expires_entry():
    reset_stats()
    sql = "SELECT * FROM structure_default_configs"
    sql_read_cache.set_sql(sql, (), "configs", {"version": 1})
    invalidate({"configs"})
    assert sql_read_cache.get_sql(sql, (), "configs") is None
    assert stats()["invalidations"]["configs"] == 1
