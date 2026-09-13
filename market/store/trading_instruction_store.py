#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易指令存储模块
"""

from typing import List, Dict, Optional
import threading
from collections import defaultdict
from datetime import datetime, timedelta

from ..models import TradingInstruction
from repositories.runtime import RuntimeStateRepository


class TradingInstructionStore:
    """交易指令存储（只负责数据CRUD）"""

    ENTITY_TYPE = "trading_instruction"
    DELIVERY_RETRY_DELAY = timedelta(seconds=15)
    MAX_DELIVERY_ATTEMPTS = 3
    # `sent` is retained temporarily for instructions created by the previous
    # implementation. Loading one converts it to the reliable `delivered`
    # state rather than silently stranding it after a deployment.
    ACTIVE_STATUSES = ("pending", "delivered", "sent")

    def __init__(self, user_id: int = None, account_id: int = None):
        # 按品种分类的指令: {symbol: [TradingInstruction, ...]}
        self._instructions_by_symbol: Dict[str, List[TradingInstruction]] = defaultdict(list)

        # 按ID索引
        self._instructions_by_id: Dict[str, TradingInstruction] = {}

        # 线程锁
        self._lock = threading.RLock()
        self._repository = (
            RuntimeStateRepository(user_id, account_id)
            if user_id is not None
            else None
        )
        if self._repository:
            for data in self._repository.list_entities(
                self.ENTITY_TYPE,
                statuses=list(self.ACTIVE_STATUSES),
            ):
                instruction = TradingInstruction.from_dict(data)
                symbol = instruction.symbol.upper()
                self._instructions_by_symbol[symbol].append(instruction)
                self._instructions_by_id[instruction.instruction_id] = instruction

        print("[TradingInstructionStore] 交易指令存储已初始化")

    # ==================== 添加指令 ====================

    def add_instruction(self, instruction: TradingInstruction) -> str:
        """
        添加交易指令

        Args:
            instruction: 交易指令对象

        Returns:
            指令ID
        """
        with self._lock:
            symbol = instruction.symbol.upper()

            # 存储到两个字典
            self._instructions_by_symbol[symbol].append(instruction)
            self._instructions_by_id[instruction.instruction_id] = instruction
            self._persist(instruction)

            print(f"[TradingInstructionStore] 添加指令: {instruction.instruction_id} {symbol} {instruction.action}")
            return instruction.instruction_id

    def add_instruction_from_dict(self, data: Dict) -> str:
        """从字典添加指令"""
        instruction = TradingInstruction.from_dict(data)
        return self.add_instruction(instruction)

    def add_instructions_batch(self, instructions: List[TradingInstruction]) -> int:
        """
        批量添加指令

        Args:
            instructions: 指令列表

        Returns:
            添加数量
        """
        count = 0
        for inst in instructions:
            self.add_instruction(inst)
            count += 1
        return count

    # ==================== 获取指令 ====================

    def get_instruction_by_id(self, instruction_id: str) -> Optional[TradingInstruction]:
        """根据ID获取指令"""
        with self._lock:
            return self._instructions_by_id.get(instruction_id)

    def get_instructions_by_symbol(self, symbol: str) -> List[TradingInstruction]:
        """获取指定品种的指令列表"""
        with self._lock:
            return list(self._instructions_by_symbol.get(symbol.upper(), []))

    def get_all_instructions(self) -> List[TradingInstruction]:
        """获取所有指令"""
        with self._lock:
            return list(self._instructions_by_id.values())

    def get_all_instructions_dict(self) -> Dict[str, List[Dict]]:
        """
        获取所有指令（按品种分类）

        Returns:
            {symbol: [instruction_dict, ...]}
        """
        with self._lock:
            self._reconcile_execution_reports()
            result = {}
            for symbol, instructions in self._instructions_by_symbol.items():
                # Dashboard and operator APIs need the persisted delivery
                # state; the EA polling endpoint uses ``to_dict`` separately.
                # Include the full record here so pending/delivered/attempt
                # details are not lost when the dashboard opens the drawer.
                result[symbol] = []
                for inst in instructions:
                    item = inst.to_full_dict()
                    item["mount"] = inst.mount
                    result[symbol].append(item)
            return result

    def _reconcile_execution_reports(self) -> None:
        """对账已落库的 EA 回执，避免旧 ``sent`` 状态长期滞留。

        旧版本在回执写入和指令状态更新之间可能因为进程重启、不同
        engine 实例或网络重试而留下状态分叉。读取操作顺手做一次按账户
        范围的批量对账，不依赖 EA 再次领取，也不会把 pending 回执误判为
        已完成。
        """
        if not self._repository:
            return
        try:
            rows = self._repository.storage.fetchall(
                "SELECT instruction_id, execution_status, success "
                "FROM trade_execution_reports "
                "WHERE user_id=? AND account_id=?",
                (self._repository.user_id, self._repository.account_id),
            )
        except Exception:
            return
        terminal = {"filled", "rejected", "timeout", "canceled", "cancelled"}
        reports = {}
        for row in rows:
            instruction_id = str(row.get("instruction_id") or "")
            if not instruction_id:
                continue
            status = str(row.get("execution_status") or "").lower()
            if status not in terminal:
                status = "filled" if bool(row.get("success")) else "rejected" if row.get("success") is not None else ""
            if status in terminal:
                reports[instruction_id] = status
        for instruction_id, status in reports.items():
            inst = self._instructions_by_id.get(instruction_id)
            if not inst or inst.status not in self.ACTIVE_STATUSES:
                continue
            inst.status = status
            inst.executed_at = datetime.now()
            self._persist(inst)
            symbol = inst.symbol.upper()
            self._instructions_by_symbol[symbol] = [
                item for item in self._instructions_by_symbol.get(symbol, [])
                if item.instruction_id != instruction_id
            ]
            self._instructions_by_id.pop(instruction_id, None)

    # ==================== 获取并发送指令（EA调用）====================

    def fetch_and_remove_by_symbol(self, symbol: str, current_price: float = None) -> List[Dict]:
        """
        获取满足条件的指令并标记已投递（EA轮询时调用）。

        指令不会在第一次 GET 时删除。只有 EA 的执行回执才能让它进入
        最终状态；否则网络响应丢失会把真实交易指令永久吞掉。投递超时后
        只重投递同一 instruction_id，EA 可据此安全去重。

        价格过滤逻辑：
        - 买入指令：指令价格 <= 当前价格 → 发送
        - 卖出指令：指令价格 >= 当前价格 → 发送

        Args:
            symbol: 品种
            current_price: 当前价格，None时不做价格过滤

        Returns:
            满足条件的指令列表（字典格式，用于返回给EA）
        """
        with self._lock:
            symbol = symbol.upper()
            instructions = self._instructions_by_symbol.get(symbol, [])

            if not instructions:
                return []

            result = []
            now = datetime.now()

            for inst in instructions:
                if inst.status not in self.ACTIVE_STATUSES:
                    continue

                if inst.status == "sent":
                    inst.status = "delivered"
                    inst.last_delivery_at = inst.sent_at
                    inst.delivery_attempts = max(1, int(inst.delivery_attempts or 0))
                    self._persist(inst)

                should_send = inst.status == "pending"

                if inst.status == "delivered":
                    last_delivery = inst.last_delivery_at or inst.sent_at
                    if last_delivery and now - last_delivery < self.DELIVERY_RETRY_DELAY:
                        continue
                    if int(inst.delivery_attempts or 0) >= self.MAX_DELIVERY_ATTEMPTS:
                        inst.status = "timeout"
                        self._persist(inst)
                        print(
                            "[TradingInstructionStore] 指令投递超时: "
                            f"{inst.instruction_id} {symbol}"
                        )
                        continue
                    # A previous response may have been lost. Re-send exactly
                    # the same instruction even if price has moved; the EA's
                    # idempotency key prevents a second market order.
                    should_send = True

                # 价格条件过滤
                if should_send and inst.status == "pending" and current_price is not None:
                    if inst.action.lower() == 'b':
                        # 买入：指令价格需要 <= 当前价格
                        if inst.price > current_price:
                            should_send = False
                    elif inst.action.lower() == 's':
                        # 卖出：指令价格需要 >= 当前价格
                        if inst.price < current_price:
                            should_send = False

                if should_send:
                    inst.status = "delivered"
                    inst.sent_at = now
                    inst.last_delivery_at = now
                    inst.delivery_attempts = int(inst.delivery_attempts or 0) + 1
                    result.append(inst.to_dict())  # 返回给EA的格式
                    self._persist(inst)

            if result:
                print(
                    f"[TradingInstructionStore] 投递指令给EA: {symbol} {len(result)}条 "
                    f"(当前价格: {current_price})"
                )

            return result

    def mark_execution_report(self, instruction_id: str, success: bool) -> bool:
        """Apply the EA's terminal receipt and stop future delivery."""
        with self._lock:
            inst = self._instructions_by_id.get(str(instruction_id or ""))
            if not inst:
                return False
            inst.status = "filled" if success else "rejected"
            inst.executed_at = datetime.now()
            self._persist(inst)
            symbol = inst.symbol.upper()
            self._instructions_by_symbol[symbol] = [
                item for item in self._instructions_by_symbol[symbol]
                if item.instruction_id != inst.instruction_id
            ]
            del self._instructions_by_id[inst.instruction_id]
            return True

    # ==================== 移除指令 ====================

    def remove_instruction(self, instruction_id: str) -> Optional[TradingInstruction]:
        """移除指定指令"""
        with self._lock:
            instruction = self._instructions_by_id.get(instruction_id)
            if not instruction:
                return None

            symbol = instruction.symbol.upper()
            self._instructions_by_symbol[symbol] = [
                i for i in self._instructions_by_symbol[symbol] if i.instruction_id != instruction_id
            ]
            del self._instructions_by_id[instruction_id]
            if self._repository:
                self._repository.delete_entity(self.ENTITY_TYPE, instruction_id)

            return instruction

    def clear_by_symbol(self, symbol: str) -> int:
        """清空指定品种的指令"""
        with self._lock:
            symbol = symbol.upper()
            instructions = self._instructions_by_symbol.get(symbol, [])
            count = len(instructions)

            for inst in instructions:
                if inst.instruction_id in self._instructions_by_id:
                    del self._instructions_by_id[inst.instruction_id]

            if symbol in self._instructions_by_symbol:
                del self._instructions_by_symbol[symbol]
            if self._repository:
                self._repository.delete_entities(self.ENTITY_TYPE, symbol)

            print(f"[TradingInstructionStore] 清空 {symbol} 指令: {count}条")
            return count

    def clear_all(self) -> int:
        """清空所有指令"""
        with self._lock:
            count = len(self._instructions_by_id)
            self._instructions_by_symbol.clear()
            self._instructions_by_id.clear()
            if self._repository:
                self._repository.delete_entities(self.ENTITY_TYPE)
            print(f"[TradingInstructionStore] 已清空所有指令: {count}条")
            return count

    # ==================== 统计 ====================

    def get_count_by_symbol(self, symbol: str) -> int:
        """获取指定品种的指令数量"""
        with self._lock:
            return len(self._instructions_by_symbol.get(symbol.upper(), []))

    def get_total_count(self) -> int:
        """获取总指令数量"""
        with self._lock:
            return len(self._instructions_by_id)

    def get_status(self) -> Dict:
        """获取存储状态"""
        with self._lock:
            symbols_count = {symbol: len(instructions)
                           for symbol, instructions in self._instructions_by_symbol.items()}
            return {
                "total_instructions": len(self._instructions_by_id),
                "symbols": symbols_count,
            }

    def set_scope(self, user_id: int, account_id: int) -> None:
        if self._repository:
            self._repository.migrate_scope(account_id)
            self._repository.set_scope(user_id, account_id)
        else:
            self._repository = RuntimeStateRepository(user_id, account_id)
        for instruction in self._instructions_by_id.values():
            self._persist(instruction)

    def _persist(self, instruction: TradingInstruction) -> None:
        if self._repository:
            self._repository.upsert_entity(
                self.ENTITY_TYPE,
                instruction.instruction_id,
                instruction.to_full_dict(),
                symbol=instruction.symbol.upper(),
                status=instruction.status,
            )
