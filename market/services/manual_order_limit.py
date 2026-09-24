"""Close extra same-day manual MT5 entries once a daily quota is used."""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from market.risk_clock import risk_day_start_timestamp


def is_manual_broker_position(position: Dict) -> bool:
    """EA-tagged tickets use an AIT comment; empty comments are manual."""
    comment = str(position.get("comment") or "").strip()
    if comment.upper().startswith("AIT"):
        return False
    try:
        if int(position.get("magic") or 0) == 123456:
            return False
    except (TypeError, ValueError):
        pass
    return not comment


def _ticket(position: Dict) -> int:
    try:
        return int(position.get("ticket") or position.get("position_id") or 0)
    except (TypeError, ValueError):
        return 0


def _opened_at(position: Dict) -> int:
    for key in ("open_timestamp", "openTimestamp", "time"):
        try:
            value = int(position.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def _profit(position: Dict) -> float:
    try:
        return float(position.get("profit") or 0)
    except (TypeError, ValueError):
        return 0.0


def load_today_manual_entries(storage, account_id: int, started_at: int) -> List[Dict]:
    rows = storage.fetchall(
        """
        SELECT d.mt5_position_id AS position_id,
               MIN(d.deal_timestamp) AS opened_at,
               COALESCE(SUM(CASE WHEN x.entry_type = 1 THEN x.profit ELSE 0 END), 0)
                   AS closed_pnl
        FROM live_trade_deals d
        LEFT JOIN live_trade_deals x
          ON x.account_id = d.account_id
         AND x.mt5_position_id = d.mt5_position_id
         AND x.entry_type = 1
        WHERE d.account_id = ? AND d.entry_type = 0 AND d.mt5_position_id > 0
          AND d.deal_timestamp >= ?
          AND (d.comment IS NULL OR TRIM(d.comment) = '')
        GROUP BY d.mt5_position_id
        ORDER BY opened_at, d.mt5_position_id
        """,
        (int(account_id), int(started_at)),
    ) or []
    entries = []
    for row in rows:
        try:
            position_id = int(row.get("position_id") or 0)
            opened_at = int(row.get("opened_at") or 0)
            closed_pnl = float(row.get("closed_pnl") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if position_id > 0:
            entries.append({
                "position_id": position_id,
                "opened_at": opened_at,
                "closed_pnl": closed_pnl,
            })
    return entries


def apply_manual_order_daily_limit(
    trading_server,
    *,
    user_id: int,
    account_id: int,
    symbol: str,
    positions: Iterable[Dict],
    account_repository,
    event_repository,
    storage=None,
    now: Optional[int] = None,
) -> List[int]:
    account = account_repository.get_by_id(int(user_id), int(account_id))
    if account is None or not getattr(account, "manual_order_daily_limit_enabled", True):
        return []
    total_limit = int(getattr(account, "manual_order_daily_limit", 10) or 0)
    loss_limit = int(getattr(account, "manual_losing_order_daily_limit", 3) or 0)
    if total_limit <= 0 and loss_limit <= 0:
        return []
    started_at = risk_day_start_timestamp(now)
    seen = {}
    closed_pnl = {}
    db = storage or getattr(account_repository, "storage", None)
    if db is not None:
        for item in load_today_manual_entries(db, account_id, started_at):
            seen[item["position_id"]] = item["opened_at"]
            closed_pnl[item["position_id"]] = item["closed_pnl"]
    open_manuals = []
    for position in positions or []:
        if not is_manual_broker_position(position):
            continue
        ticket = _ticket(position)
        if ticket <= 0:
            continue
        opened_at = _opened_at(position)
        if opened_at >= started_at:
            seen.setdefault(ticket, opened_at)
        open_manuals.append(position)
    ordered_ids = [
        position_id for position_id, _opened in sorted(
            seen.items(), key=lambda item: (item[1], item[0])
        )
    ]
    losers = {}
    for position_id, opened_at in seen.items():
        if closed_pnl.get(position_id, 0) < 0:
            losers[position_id] = opened_at
    for position in open_manuals:
        ticket = _ticket(position)
        if _profit(position) < 0:
            losers.setdefault(ticket, _opened_at(position) or seen.get(ticket, 0))
    ordered_losers = [
        position_id for position_id, _opened in sorted(
            losers.items(), key=lambda item: (item[1], item[0])
        )
    ]
    total_exceeded = total_limit > 0 and len(ordered_ids) > total_limit
    loss_exceeded = loss_limit > 0 and len(ordered_losers) >= loss_limit
    if not total_exceeded and not loss_exceeded:
        return []
    allowed = set(ordered_ids[:total_limit] if total_limit > 0 else ordered_ids)
    if loss_exceeded:
        cutoff_id = ordered_losers[loss_limit - 1]
        cutoff_time = seen.get(cutoff_id, 0)
        allowed = {
            position_id
            for position_id in allowed
            if (seen.get(position_id, 0), position_id) <= (cutoff_time, cutoff_id)
        }
    triggered: List[int] = []
    for position in open_manuals:
        ticket = _ticket(position)
        opened_at = seen.get(ticket, _opened_at(position))
        if ticket in allowed or opened_at < started_at:
            continue
        pos_symbol = str(position.get("symbol") or symbol or "").strip() or str(symbol)
        instruction_id = f"manual-order-limit-{account_id}-{ticket}"
        runtime = getattr(trading_server, "_runtime_repository", None)
        prior = runtime.get_entity("close_instruction", instruction_id) if runtime else None
        trading_server.add_close_position_instruction(
            pos_symbol, ticket, instruction_id=instruction_id,
        )
        triggered.append(ticket)
        if prior is None:
            reasons = []
            if total_exceeded:
                reasons.append(f"今日手动开仓 {len(ordered_ids)} 笔，超过上限 {total_limit}")
            if loss_exceeded:
                reasons.append(f"今日手动亏损 {len(ordered_losers)} 笔，达到上限 {loss_limit}")
            event_repository.record(
                int(user_id), int(account_id), str(ticket),
                "manual_order_daily_limit",
                f"{'；'.join(reasons)}，持仓 {ticket} 无备注，已生成平仓指令",
                symbol=pos_symbol, ticket=ticket,
                rule_type="manual_order_daily_limit", status="triggered",
                price=float(position.get("priceCurrent") or position.get("price_open") or 0),
                stop_loss=float(position.get("sl") or 0),
                take_profit=float(position.get("tp") or 0),
                volume=float(position.get("volume") or 0),
                payload={
                    "source": "server_position_snapshot",
                    "limit": total_limit,
                    "losing_limit": loss_limit,
                    "today_manual_count": len(ordered_ids),
                    "today_manual_loss_count": len(ordered_losers),
                    "instruction_id": instruction_id,
                },
            )
    return triggered
