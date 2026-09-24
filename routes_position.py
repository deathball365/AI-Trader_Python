#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仓位管理相关的接口路由
"""

from fastapi import APIRouter, Depends, Query, Request
from typing import Dict, Optional
import logging

from auth import AuthUser, require_auth
from ea_auth import EAIdentity, require_ea_auth
from trading_engine_manager import TradingEngineManager
from web_account_context import resolve_web_engine
from repositories.accounts import TradingAccountRepository
from repositories.trading import PositionManagementEventRepository
from repositories.instrument_specs import InstrumentSpecRepository
from market.store.structure_plan_store import StructureTradePlanRepository
from market.services.manual_order_limit import apply_manual_order_daily_limit

logger = logging.getLogger(__name__)


def _apply_single_position_loss_limit(
    trading_server,
    *,
    user_id: int,
    account_id: int,
    symbol: str,
    positions,
    account_repository: TradingAccountRepository,
    event_repository: PositionManagementEventRepository,
) -> list[int]:
    """Queue server-side close commands for positions beyond the account limit.

    MT5's reported ``profit`` is already expressed in the account currency.  A
    deterministic instruction id plus the server's ticket-level queue
    de-duplication makes repeated position heartbeats safe while still allowing
    a failed/delayed close to be delivered again on the next poll cycle.
    """
    account = account_repository.get_by_id(int(user_id), int(account_id))
    if account is None or not account.single_position_loss_limit_enabled:
        return []
    limit = float(account.single_position_loss_limit_amount or 0)
    if limit <= 0:
        return []

    triggered: list[int] = []
    for position in positions or []:
        try:
            ticket = int(position.get("ticket") or position.get("position_id") or 0)
            profit = float(position.get("profit") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if ticket <= 0 or profit > -limit:
            continue
        pos_symbol = str(position.get("symbol") or symbol or "").strip() or str(symbol)
        instruction_id = f"position-loss-limit-{account_id}-{ticket}"
        prior = None
        runtime = getattr(trading_server, "_runtime_repository", None)
        if runtime is not None:
            prior = runtime.get_entity("close_instruction", instruction_id)
        trading_server.add_close_position_instruction(
            pos_symbol, ticket, instruction_id=instruction_id,
        )
        triggered.append(ticket)
        if prior is None:
            event_repository.record(
                int(user_id), int(account_id), str(ticket),
                "single_position_loss_limit",
                (
                    f"单笔持仓浮亏 {profit:.2f} {account.currency} 已达到上限 "
                    f"{limit:.2f} {account.currency}，已生成平仓指令"
                ),
                symbol=pos_symbol, ticket=ticket,
                rule_type="single_position_loss_limit", status="triggered",
                price=float(position.get("priceCurrent") or 0),
                stop_loss=float(position.get("sl") or 0),
                take_profit=float(position.get("tp") or 0),
                volume=float(position.get("volume") or 0),
                payload={
                    "source": "server_position_snapshot",
                    "profit": profit,
                    "limit": limit,
                    "currency": account.currency,
                    "instruction_id": instruction_id,
                },
            )
    return triggered


def create_position_routes(engine_manager: TradingEngineManager) -> APIRouter:
    """
    创建仓位管理路由

    Args:
        engine_manager: 多账户交易引擎管理器
    """
    router = APIRouter()
    protected_router = APIRouter()
    repositories = engine_manager.repositories

    @router.post("/ea/instrument_specs")
    async def receive_instrument_specs(
        request: Request,
        identity: EAIdentity = Depends(require_ea_auth),
    ) -> Dict:
        """Persist broker-provided volume rules for one account and symbol."""
        try:
            payload = await request.json()
            symbol = str(payload.get("symbol") or "").strip()
            if not symbol:
                return {"status": "error", "message": "缺少品种"}
            spec = InstrumentSpecRepository().upsert(
                identity.account_id,
                symbol,
                payload,
            )
            return {"status": "ok", "account_id": identity.account_id, "spec": spec}
        except (TypeError, ValueError) as exc:
            return {"status": "error", "message": str(exc)}

    @router.post("/ea/positions")
    async def receive_positions(
        request: Request,
        identity: EAIdentity = Depends(require_ea_auth),
    ) -> Dict:
        """
        EA推送持仓数据

        请求体:
        ```json
        {
            "symbol": "BTCUSD#",
            "positions": [
                {
                    "ticket": 123456,
                    "volume": 0.01,
                    "priceOpen": 70000.00,
                    "type": "BUY",
                    "profit": 100.50,
                    "distanceSL": 50.0,
                    "distanceTP": 100.0
                }
            ]
        }
        ```
        """
        try:
            data = await request.json()
            symbol = data.get('symbol', '')
            positions = data.get('positions', [])
            full_account_snapshot = bool(data.get('full_account_snapshot', False))

            if not symbol:
                return {"status": "error", "message": "缺少品种信息"}

            trading_server = engine_manager.get_engine_for_ea(identity)
            if full_account_snapshot:
                # The account-owner EA sends every open position across all
                # symbols. Replace the complete snapshot so closed symbols
                # are removed instead of lingering in the runtime cache.
                result = trading_server.position_service.replace_all_positions(positions)
            else:
                result = trading_server.position_service.update_positions(symbol, positions)
            symbol_positions = [
                item for item in positions
                if str(item.get("symbol") or symbol) == str(symbol)
            ]
            # The account-owner chart reports every open position.  Loss-limit
            # closes must scan that full snapshot, not only the owner symbol,
            # otherwise a GOLD chart would never flatten a losing US100 ticket.
            loss_limit_positions = (
                positions if full_account_snapshot else symbol_positions
            )
            loss_limit_tickets = _apply_single_position_loss_limit(
                trading_server,
                user_id=identity.user_id,
                account_id=identity.account_id,
                symbol=symbol,
                positions=loss_limit_positions,
                account_repository=TradingAccountRepository(repositories.storage),
                event_repository=repositories.position_events,
            )
            manual_limit_tickets = apply_manual_order_daily_limit(
                trading_server,
                user_id=identity.user_id,
                account_id=identity.account_id,
                symbol=symbol,
                positions=loss_limit_positions,
                account_repository=TradingAccountRepository(repositories.storage),
                event_repository=repositories.position_events,
                storage=repositories.storage,
            )
            if isinstance(result, dict):
                result["loss_limit_close_count"] = len(loss_limit_tickets)
                result["loss_limit_close_tickets"] = loss_limit_tickets
                result["manual_order_limit_close_count"] = len(manual_limit_tickets)
                result["manual_order_limit_close_tickets"] = manual_limit_tickets
            try:
                StructureTradePlanRepository().confirm_protection_for_account(
                    identity.user_id, identity.account_id, symbol, symbol_positions,
                )
            except Exception as exc:
                logger.warning("结构计划保护止损确认失败: %s", exc)

            # 持仓快照是高频同步数据，不写入交易日志，避免每次 EA 心跳
            # 都产生一条无业务意义的记录。持仓当前状态仍由 position_store
            # 持久化，真实的成交、平仓、止损修改等事件继续写入审计日志。

            return result

        except Exception as e:
            print(f"[PositionAPI] 接收持仓数据异常: {e}")
            return {"status": "error", "message": str(e)}

    @protected_router.get("/positions")
    async def get_positions(
        symbol: Optional[str] = None,
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """
        获取持仓数据

        参数:
        - symbol: 可选，指定品种；不提供则返回所有
        """
        _, trading_server = resolve_web_engine(engine_manager, user, account_id)
        positions = trading_server.position_service.get_positions(symbol)
        return {
            "status": "ok",
            "count": len(positions),
            "positions": positions
        }

    @protected_router.get("/positions/summary")
    async def get_positions_summary(
        symbol: Optional[str] = None,
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """
        获取持仓汇总

        参数:
        - symbol: 可选，指定品种；不提供则返回所有
        """
        _, trading_server = resolve_web_engine(engine_manager, user, account_id)
        summary = trading_server.position_service.get_summary(symbol)
        return {
            "status": "ok",
            **summary
        }

    @protected_router.get("/positions/{symbol}/{ticket}")
    async def get_position(
        symbol: str,
        ticket: int,
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """
        获取单个持仓详情
        """
        _, trading_server = resolve_web_engine(engine_manager, user, account_id)
        position = trading_server.position_service.get_position(symbol, ticket)
        if not position:
            return {"status": "error", "message": "持仓不存在"}
        return {
            "status": "ok",
            "position": position
        }

    @protected_router.get("/positions/{symbol}/{ticket}/management-events")
    async def get_position_management_events(
        symbol: str,
        ticket: int,
        account_id: Optional[int] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(30, ge=1, le=200),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        account, _ = resolve_web_engine(engine_manager, user, account_id)
        events = repositories.position_events.list_for_position(
            user.user_id, account.account_id, str(ticket),
            limit=page_size + 1, offset=(page - 1) * page_size,
        )
        items = events[:page_size]
        return {"status": "ok", "events": items, "page": page,
                "page_size": page_size, "has_more": len(events) > page_size}

    # ==================== 交易历史接口 ====================

    @router.post("/ea/trade_history")
    async def receive_trade_history(
        request: Request,
        identity: EAIdentity = Depends(require_ea_auth),
    ) -> Dict:
        """
        EA推送交易历史数据

        请求体:
        ```json
        {
            "deals": [
                {
                    "ticket": 123456,
                    "order": 789012,
                    "symbol": "GOLD#",
                    "type": 0,
                    "entry": 0,
                    "volume": 0.1,
                    "price": 2050.50,
                    "profit": 0,
                    "swap": 0,
                    "commission": -5.0,
                    "time": "2026.03.16 15:30:00",
                    "comment": ""
                }
            ]
        }
        ```
        """
        try:
            data = await request.json()
            deals = data.get('deals', [])

            if not deals:
                return {"status": "ok", "message": "无数据", "count": 0}

            # 使用新的交易历史服务
            trading_server = engine_manager.get_engine_for_ea(identity)
            new_count = trading_server.trade_history_service.process_deals(deals)

            # 记录日志
            system_log = trading_server.system_log
            system_log.add_log(
                "trade_history_update",
                {
                    "deals_received": len(deals),
                    "deals_new": new_count,
                    "total_deals": len(trading_server.trade_history_store.get())
                },
                message=f"交易历史上报: 收到{len(deals)}条, 新增{new_count}条"
            )

            return {
                "status": "ok",
                "message": "交易历史已更新",
                "count": new_count
            }

        except Exception as e:
            print(f"[PositionAPI] 接收交易历史异常: {e}")
            return {"status": "error", "message": str(e)}

    @protected_router.get("/trade_history")
    async def get_trade_history(
        symbol: Optional[str] = None,
        account_id: Optional[int] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(30, ge=1, le=200),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """
        获取交易历史数据

        参数:
        - symbol: 可选，指定品种
        """
        _, trading_server = resolve_web_engine(engine_manager, user, account_id)
        # 读取一页加一条探测记录，避免把账户全部成交复制到响应内存。
        all_deals = trading_server.trade_history_service.get_deals(
            symbol, offset=(page - 1) * page_size, limit=page_size + 1,
        )
        start = (page - 1) * page_size
        deals = all_deals[:page_size]
        statistics = trading_server.trade_history_service.get_statistics(symbol)

        return {
            "status": "ok",
            "deals": deals,
            "page": page, "page_size": page_size,
            "has_more": len(all_deals) > page_size,
            "statistics": statistics
        }

    @protected_router.get("/trade_history/statistics")
    async def get_trade_history_statistics(
        symbol: Optional[str] = None,
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """
        获取交易历史统计

        参数:
        - symbol: 可选，指定品种
        """
        _, trading_server = resolve_web_engine(engine_manager, user, account_id)
        statistics = trading_server.trade_history_service.get_statistics(symbol)

        return {
            "status": "ok",
            **statistics
        }

    router.include_router(protected_router)
    return router
