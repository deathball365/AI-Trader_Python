"""User-level live market-source arbitration.

One broker publishes a user's canonical symbol once. Additional accounts from
the same broker reuse that feed; a different broker may publish only symbols
that are not already owned inside the user market domain.

Account pages must summarize symbol-level roles. Whole-account "reuse" based
only on peer activation order is obsolete and misleading after failover.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Dict, List

from mysql_repositories import (
    TradingAccountRepository,
    get_storage,
)
from repositories.platform import PlatformInstrumentMappingRepository
from runtime_cache import TTLCache


class MarketDataSourcePolicy:
    CACHE_SECONDS = 30
    # A market source must keep sending EA heartbeats. Once it is stale, a
    # healthy same-broker account may take over the shared quote stream.
    SOURCE_HEARTBEAT_TTL = 180

    def __init__(self):
        self.storage = get_storage()
        self.accounts = TradingAccountRepository(self.storage)
        self.mappings = PlatformInstrumentMappingRepository(self.storage)
        self._cache: Dict[tuple, tuple] = {}
        self._lock = threading.RLock()
        self._mapping_cache = TTLCache(ttl_seconds=60, max_items=4096)

    def canonical_symbol(self, broker_name: str, symbol: str) -> str:
        native = self.mappings._normalize(symbol)
        key = (str(broker_name or "").casefold(), native)
        cached = self._mapping_cache.get(key, "mappings")
        if cached is not None:
            return str(cached)
        row = self.storage.fetchone(
            """
            SELECT mapping_group FROM platform_instrument_mappings
            WHERE enabled = 1 AND native_symbol = ?
              AND COALESCE(NULLIF(broker_name, ''), broker_server) = ?
            ORDER BY updated_at DESC LIMIT 1
            """,
            (native, broker_name),
        )
        canonical = str(row["mapping_group"] or native).upper() if row else native
        self._mapping_cache.set(key, canonical, "mappings")
        return canonical

    def resolve(self, user_id: int, account_id: int, symbol: str) -> Dict:
        account = self.accounts.get_by_id(int(user_id), int(account_id))
        if account is None or account.account_type not in {"mt5", "ibkr"}:
            return {"mode": "blocked", "message": "实盘账户不存在"}
        broker_name = (
            "ibkr" if account.account_type == "ibkr" else
            self.mappings.broker_name_from_server(account.mt5_server or "").casefold()
        )
        canonical = self.canonical_symbol(broker_name, symbol)
        cache_key = (int(user_id), int(account_id), canonical)
        now = time.time()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and now - cached[0] < self.CACHE_SECONDS:
                return dict(cached[1])
        result = self._resolve_uncached(account, broker_name, symbol, canonical)
        with self._lock:
            self._cache[cache_key] = (now, dict(result))
        return result

    def _resolve_uncached(
        self, account, broker_name: str, native_symbol: str, canonical: str,
    ) -> Dict:
        blocked = self.storage.fetchone(
            "SELECT * FROM market_data_symbol_policies "
            "WHERE user_id = ? AND account_id = ? AND canonical_symbol = ? AND mode = 'blocked'",
            (account.user_id, account.account_id, canonical),
        )
        if blocked:
            return self._policy_payload(blocked, canonical)

        # 主行情源按“用户 + 标准品种”竞争，而不是按整个账户竞争。
        # 同一账户可以负责 BTCUSD，另一个同交易商账户负责 GOLD。
        source = self._claim_source(
            account.user_id, canonical, account.account_id,
            broker_name, native_symbol,
        )
        source_account_id = int(source.get("primary_account_id") or 0)
        source_broker = str(source.get("broker_name") or "").casefold()
        if source_broker and source_broker != broker_name:
            return self._block(account, broker_name, canonical, source_account_id)
        if source_account_id and source_account_id != account.account_id:
            primary = self.accounts.get_by_id(account.user_id, source_account_id)
            return self._save_policy(
                account, broker_name, "reuse", source_account_id, [],
                f"复用同品种主行情账户「{primary.account_name if primary else source_account_id}」的行情和策略触发 Tick",
                canonical,
            )
        return self._save_policy(
            account, broker_name, "primary", account.account_id, [],
            "该账户负责此品种的用户共享行情和策略触发 Tick", canonical,
        )

    def _claim_source(
        self, user_id: int, canonical: str, primary_account_id: int,
        broker_name: str, native_symbol: str,
    ) -> Dict:
        now = int(time.time())
        self.storage.execute(
            """
            INSERT INTO market_data_sources(
                user_id, canonical_symbol, primary_account_id, broker_name,
                native_symbol, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, canonical_symbol) DO NOTHING
            """,
            (user_id, canonical, primary_account_id, broker_name,
             str(native_symbol).upper(), now, now),
        )
        row = self.storage.fetchone(
            "SELECT * FROM market_data_sources "
            "WHERE user_id = ? AND canonical_symbol = ?",
            (user_id, canonical),
        )
        if row:
            old_primary_id = int(row.get("primary_account_id") or 0)
            current_source = self.accounts.get_by_id(user_id, old_primary_id)
            source_last_seen = int(getattr(current_source, "last_seen_at", 0) or 0)
            current_last_seen = int(getattr(
                self.accounts.get_by_id(user_id, primary_account_id),
                "last_seen_at", 0,
            ) or 0)
            source_is_stale = (
                current_source is None
                or current_source.status != "active"
                or not current_source.enabled
                or source_last_seen <= 0
                or now - source_last_seen > self.SOURCE_HEARTBEAT_TTL
            )
            current_is_healthy = (
                current_last_seen > 0
                and now - current_last_seen <= self.SOURCE_HEARTBEAT_TTL
            )
            if source_is_stale and current_is_healthy and old_primary_id != primary_account_id:
                self.storage.execute(
                    "UPDATE market_data_sources SET primary_account_id=?, "
                    "broker_name=?, native_symbol=?, updated_at=? "
                    "WHERE user_id=? AND canonical_symbol=?",
                    (primary_account_id, broker_name, str(native_symbol).upper(),
                     now, user_id, canonical),
                )
                row = self.storage.fetchone(
                    "SELECT * FROM market_data_sources "
                    "WHERE user_id = ? AND canonical_symbol = ?",
                    (user_id, canonical),
                )
                # Failover used to update only market_data_sources. Account pages
                # and leftover account-level rows could keep showing the old reuse
                # relationship until every symbol was re-resolved.
                self._demote_symbol_primary(
                    user_id=user_id,
                    canonical=canonical,
                    old_primary_id=old_primary_id,
                    new_primary_id=primary_account_id,
                    broker_name=broker_name,
                )
        return dict(row) if row else {}

    def _demote_symbol_primary(
        self,
        *,
        user_id: int,
        canonical: str,
        old_primary_id: int,
        new_primary_id: int,
        broker_name: str,
    ) -> None:
        old_account = self.accounts.get_by_id(user_id, int(old_primary_id or 0))
        new_account = self.accounts.get_by_id(user_id, int(new_primary_id or 0))
        if new_account is not None:
            self._save_policy(
                new_account,
                broker_name,
                "primary",
                int(new_primary_id),
                [],
                "该账户负责此品种的用户共享行情和策略触发 Tick",
                canonical,
                sync_account_summary=False,
            )
        if old_account is not None and int(old_primary_id) != int(new_primary_id):
            new_name = new_account.account_name if new_account else str(new_primary_id)
            self._save_policy(
                old_account,
                broker_name,
                "reuse",
                int(new_primary_id),
                [],
                f"复用同品种主行情账户「{new_name}」的行情和策略触发 Tick",
                canonical,
                sync_account_summary=False,
            )
        self._invalidate_cache(user_id, int(old_primary_id or 0), canonical)
        self._invalidate_cache(user_id, int(new_primary_id or 0), canonical)
        if old_account is not None:
            self._sync_account_summary(user_id, int(old_primary_id))
        if new_account is not None:
            self._sync_account_summary(user_id, int(new_primary_id))

    def _block(
        self, account, broker_name: str, canonical: str,
        primary_account_id: int,
    ) -> Dict:
        primary = self.accounts.get_by_id(account.user_id, primary_account_id)
        primary_name = primary.account_name if primary else str(primary_account_id)
        message = (
            f"标准品种 {canonical} 已由不同交易商账户「{primary_name}」提供行情，"
            "该实盘账户已禁止策略开仓"
        )
        return self._save_policy(
            account, broker_name, "blocked", primary_account_id,
            [canonical], message, canonical,
        )

    def _save_policy(
        self, account, broker_name: str, mode: str, primary_account_id: int,
        conflicts, message: str, canonical: str,
        *, sync_account_summary: bool = True,
    ) -> Dict:
        now = int(time.time())
        self.storage.execute(
            """
            INSERT INTO market_data_symbol_policies(
                user_id, account_id, canonical_symbol, broker_name, mode,
                primary_account_id, conflict_symbols_json, message, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, account_id, canonical_symbol) DO UPDATE SET
                broker_name = excluded.broker_name, mode = excluded.mode,
                primary_account_id = excluded.primary_account_id,
                conflict_symbols_json = excluded.conflict_symbols_json,
                message = excluded.message, updated_at = excluded.updated_at
            """,
            (account.user_id, account.account_id, canonical, broker_name, mode,
             primary_account_id, json.dumps(conflicts, ensure_ascii=False),
             message, now, now),
        )
        payload = {
            "mode": mode, "broker_name": broker_name,
            "canonical_symbol": canonical,
            "primary_account_id": primary_account_id,
            "conflict_symbols": list(conflicts), "message": message,
            "is_market_primary": mode == "primary",
            "can_open_trade": mode != "blocked",
        }
        if sync_account_summary:
            self._sync_account_summary(account.user_id, account.account_id)
        return payload

    def _invalidate_cache(self, user_id: int, account_id: int, canonical: str = "") -> None:
        with self._lock:
            if not account_id:
                return
            if canonical:
                self._cache.pop((int(user_id), int(account_id), str(canonical)), None)
                return
            for key in list(self._cache):
                if key[0] == int(user_id) and key[1] == int(account_id):
                    self._cache.pop(key, None)

    def _sync_account_summary(self, user_id: int, account_id: int) -> Dict:
        """Keep legacy account-level rows aligned with symbol-level truth."""
        summary = self.summarize_account(user_id, account_id)
        account = self.accounts.get_by_id(int(user_id), int(account_id))
        broker_name = ""
        if account is not None:
            broker_name = (
                "ibkr" if account.account_type == "ibkr" else
                self.mappings.broker_name_from_server(account.mt5_server or "").casefold()
            )
        now = int(time.time())
        self.storage.execute(
            """
            INSERT INTO market_data_account_policies(
                user_id, account_id, broker_name, mode, primary_account_id,
                conflict_symbols_json, message, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, account_id) DO UPDATE SET
                broker_name = excluded.broker_name,
                mode = excluded.mode,
                primary_account_id = excluded.primary_account_id,
                conflict_symbols_json = excluded.conflict_symbols_json,
                message = excluded.message,
                updated_at = excluded.updated_at
            """,
            (
                int(user_id),
                int(account_id),
                broker_name or str(summary.get("broker_name") or ""),
                str(summary.get("mode") or "pending"),
                int(summary.get("primary_account_id") or account_id or 0),
                json.dumps(summary.get("conflict_symbols") or [], ensure_ascii=False),
                str(summary.get("message") or ""),
                now,
                now,
            ),
        )
        return summary

    @staticmethod
    def _policy_payload(row, canonical: str = "") -> Dict:
        item = dict(row)
        try:
            conflicts = json.loads(item.get("conflict_symbols_json") or "[]")
        except (TypeError, ValueError):
            conflicts = []
        return {
            "mode": item.get("mode") or "unknown",
            "broker_name": item.get("broker_name") or "",
            "canonical_symbol": canonical or item.get("canonical_symbol") or "",
            "primary_account_id": int(item.get("primary_account_id") or 0),
            "conflict_symbols": conflicts,
            "message": item.get("message") or "",
            "is_market_primary": item.get("mode") == "primary",
            "can_open_trade": item.get("mode") != "blocked",
            "primary_symbol_count": int(item.get("primary_symbol_count") or 0),
            "reuse_symbol_count": int(item.get("reuse_symbol_count") or 0),
            "blocked_symbol_count": int(item.get("blocked_symbol_count") or 0),
        }

    def summarize_account(self, user_id: int, account_id: int) -> Dict:
        """Aggregate symbol roles for account cards and activation notices."""
        user_id = int(user_id)
        account_id = int(account_id)
        rows = self.storage.fetchall(
            """
            SELECT * FROM market_data_symbol_policies
            WHERE user_id = ? AND account_id = ?
            ORDER BY updated_at DESC
            """,
            (user_id, account_id),
        )
        source_rows = self.storage.fetchall(
            """
            SELECT * FROM market_data_sources
            WHERE user_id = ? AND primary_account_id = ?
            """,
            (user_id, account_id),
        )

        primary_symbols = {
            str(row.get("canonical_symbol") or "").upper()
            for row in rows
            if str(row.get("mode") or "") == "primary" and row.get("canonical_symbol")
        }
        primary_symbols.update(
            str(row.get("canonical_symbol") or "").upper()
            for row in source_rows
            if row.get("canonical_symbol")
        )
        reuse_symbols = {
            str(row.get("canonical_symbol") or "").upper()
            for row in rows
            if str(row.get("mode") or "") == "reuse" and row.get("canonical_symbol")
        } - primary_symbols
        blocked_symbols = {
            str(row.get("canonical_symbol") or "").upper()
            for row in rows
            if str(row.get("mode") or "") == "blocked" and row.get("canonical_symbol")
        }
        conflicts: List[str] = []
        for row in rows:
            if str(row.get("mode") or "") != "blocked":
                continue
            try:
                conflicts.extend(json.loads(row.get("conflict_symbols_json") or "[]"))
            except (TypeError, ValueError):
                pass
        conflicts = sorted({str(item) for item in conflicts if str(item).strip()})

        broker_name = ""
        primary_account_id = account_id
        if rows:
            broker_name = str(rows[0].get("broker_name") or "")
            primary_account_id = int(rows[0].get("primary_account_id") or account_id)
        elif source_rows:
            broker_name = str(source_rows[0].get("broker_name") or "")

        if blocked_symbols and not primary_symbols and not reuse_symbols:
            mode = "blocked"
            message = (
                f"有 {len(blocked_symbols)} 个品种因跨交易商行情冲突被禁止开仓"
            )
            primary_account_id = int(rows[0].get("primary_account_id") or 0) if rows else 0
        elif primary_symbols:
            mode = "primary"
            sample = "、".join(sorted(primary_symbols)[:4])
            more = len(primary_symbols) - min(len(primary_symbols), 4)
            suffix = f" 等{more}个" if more > 0 else ""
            message = (
                f"该账户是 {len(primary_symbols)} 个品种的行情主源"
                + (f"：{sample}{suffix}" if sample else "")
            )
            primary_account_id = account_id
        elif reuse_symbols:
            mode = "reuse"
            # Prefer the live source owner for the newest reuse row.
            primary_account_id = int(rows[0].get("primary_account_id") or 0) if rows else 0
            owner = self.accounts.get_by_id(user_id, primary_account_id)
            owner_name = owner.account_name if owner else str(primary_account_id or "未知账户")
            message = (
                f"该账户复用「{owner_name}」的 {len(reuse_symbols)} 个品种行情和策略触发 Tick"
            )
        else:
            mode = "pending"
            message = "等待 EA 上报品种后确认行情来源"
            primary_account_id = account_id

        return {
            "mode": mode,
            "broker_name": broker_name,
            "canonical_symbol": "",
            "primary_account_id": int(primary_account_id or 0),
            "conflict_symbols": conflicts or sorted(blocked_symbols),
            "message": message,
            "is_market_primary": mode == "primary",
            "can_open_trade": mode != "blocked",
            "primary_symbol_count": len(primary_symbols),
            "reuse_symbol_count": len(reuse_symbols),
            "blocked_symbol_count": len(blocked_symbols),
            "primary_symbols": sorted(primary_symbols),
            "reuse_symbols": sorted(reuse_symbols),
            "blocked_symbols": sorted(blocked_symbols),
        }

    def account_status(self, user_id: int, account_id: int, symbol: str = "") -> Dict:
        if symbol:
            account = self.accounts.get_by_id(int(user_id), int(account_id))
            if account:
                broker = (
                    "ibkr" if account.account_type == "ibkr"
                    else self.mappings.broker_name_from_server(account.mt5_server or "").casefold()
                )
                canonical = self.canonical_symbol(broker, symbol)
                row = self.storage.fetchone(
                    "SELECT * FROM market_data_symbol_policies "
                    "WHERE user_id = ? AND account_id = ? AND canonical_symbol = ? "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (int(user_id), int(account_id), canonical),
                )
                if row:
                    return self._policy_payload(row, canonical)
                source = self.storage.fetchone(
                    "SELECT * FROM market_data_sources "
                    "WHERE user_id = ? AND canonical_symbol = ?",
                    (int(user_id), canonical),
                )
                if source and int(source.get("primary_account_id") or 0) == int(account_id):
                    return {
                        "mode": "primary",
                        "broker_name": str(source.get("broker_name") or ""),
                        "canonical_symbol": canonical,
                        "primary_account_id": int(account_id),
                        "conflict_symbols": [],
                        "message": "该账户负责此品种的用户共享行情和策略触发 Tick",
                        "is_market_primary": True,
                        "can_open_trade": True,
                        "primary_symbol_count": 1,
                        "reuse_symbol_count": 0,
                        "blocked_symbol_count": 0,
                    }
                return {
                    "mode": "pending",
                    "message": "等待 EA 上报品种后确认行情来源",
                    "primary_account_id": 0,
                    "conflict_symbols": [],
                    "is_market_primary": False,
                    "can_open_trade": True,
                    "canonical_symbol": canonical,
                    "primary_symbol_count": 0,
                    "reuse_symbol_count": 0,
                    "blocked_symbol_count": 0,
                }
        return self.summarize_account(user_id, account_id)

    def execution_account_ids(
        self, user_id: int, broker_name: str, symbol: str = "",
    ) -> list[int]:
        """All active same-broker accounts driven by the primary market Tick."""
        result = []
        for account in self.accounts.list_for_user(int(user_id)):
            if (
                account.account_type != "mt5" or account.status != "active"
                or not account.enabled
            ):
                continue
            account_broker = self.mappings.broker_name_from_server(
                account.mt5_server or ""
            ).casefold()
            if account_broker != str(broker_name or "").casefold():
                continue
            status = self.account_status(user_id, account.account_id, symbol)
            if status.get("mode") != "blocked":
                result.append(account.account_id)
        return result

    def _account_heartbeat_fresh(self, account, now: int | None = None) -> bool:
        now = int(now or time.time())
        last_seen = int(getattr(account, "last_seen_at", 0) or 0)
        return last_seen > 0 and now - last_seen <= self.SOURCE_HEARTBEAT_TTL

    def activation_notice(self, user_id: int, account_id: int) -> Dict:
        account = self.accounts.get_by_id(int(user_id), int(account_id))
        if account is None:
            return {"mode": "pending", "message": "等待账户识别"}

        summary = self.summarize_account(user_id, account_id)
        if summary.get("mode") in {"primary", "reuse", "blocked"}:
            return summary

        broker_name = self.mappings.broker_name_from_server(
            account.mt5_server or ""
        ).casefold()
        now = int(time.time())
        peers = [
            item for item in self.accounts.list_for_user(int(user_id))
            if item.account_type == "mt5" and item.status == "active"
            and item.account_id != account.account_id
            and self.mappings.broker_name_from_server(
                item.mt5_server or ""
            ).casefold() == broker_name
        ]
        healthy_peers = [item for item in peers if self._account_heartbeat_fresh(item, now)]
        if healthy_peers:
            names = "、".join(item.account_name for item in healthy_peers[:2])
            return {
                "mode": "pending",
                "primary_account_id": account.account_id,
                "message": (
                    f"检测到同交易商在线账户（{names}）。行情主源按品种竞争："
                    "本账户上报的品种可成为主源，也可在其他账户掉线后接管对应品种；"
                    "订单和持仓管理始终由本账户独立执行"
                ),
                "is_market_primary": False,
                "can_open_trade": True,
                "primary_symbol_count": 0,
                "reuse_symbol_count": 0,
                "blocked_symbol_count": 0,
            }
        if peers:
            return {
                "mode": "pending",
                "primary_account_id": account.account_id,
                "message": (
                    "检测到同交易商账户但当前均已掉线；本账户上报品种后将成为对应品种行情主源"
                ),
                "is_market_primary": False,
                "can_open_trade": True,
                "primary_symbol_count": 0,
                "reuse_symbol_count": 0,
                "blocked_symbol_count": 0,
            }
        return {
            "mode": "pending",
            "primary_account_id": account.account_id,
            "message": "账户已激活；首次上报品种时将检查跨交易商行情冲突",
            "is_market_primary": False,
            "can_open_trade": True,
            "primary_symbol_count": 0,
            "reuse_symbol_count": 0,
            "blocked_symbol_count": 0,
        }
