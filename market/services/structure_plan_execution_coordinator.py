"""Structure Plan claim/record/release workflow for order execution."""
from __future__ import annotations



class StructurePlanExecutionCoordinator:
    def __init__(self, repository, execution_service):
        self.repository = repository
        self.execution_service = execution_service

    def validate_stage(
        self, decision, positions, *, user_id: int = 0, account_id: int = 0,
        deployment_id: str = "",
    ) -> dict:
        """Validate account-side prerequisites for a staged opportunity.

        A breakout is an add-on only when this exact structural opportunity has
        an initial-stage plan.  Opportunities that start directly with a
        breakout must remain independently executable.
        """
        summary = decision.signal_summary or {}
        stage = str(summary.get("selected_trade_opportunity_stage") or "")
        if stage != "breakout":
            return {"allowed": True, "stage": stage, "reason": "非突破加仓阶段"}
        opportunity_id = str(summary.get("selected_trade_opportunity_id") or "")
        initial_plans = []
        if self.repository is not None and opportunity_id and user_id:
            initial_plans = [
                plan for plan in self.repository.list_opportunity(
                    int(user_id), opportunity_id,
                    symbol=str(getattr(decision, "symbol", "") or ""),
                    period=str(summary.get("selected_signal_period") or ""),
                )
                if str(plan.get("opportunity_stage") or plan.get("event_stage") or "") == "initial"
            ]
        elif summary.get("requires_initial_fill") is True:
            initial_plans = [{"plan_id": str(summary.get("initial_plan_id") or "")}]

        if not initial_plans:
            return {
                "allowed": True, "stage": stage,
                "reason": "该机会没有首仓阶段，突破作为首次入场",
                "opportunity_id": opportunity_id,
                "entry_role": "direct_breakout",
            }

        initial_plan_ids = {
            str(plan.get("plan_id") or "") for plan in initial_plans
            if str(plan.get("plan_id") or "")
        }
        executions = []
        if self.repository is not None and initial_plan_ids and user_id:
            executions = [
                row for row in self.repository.list_executions(
                    int(user_id), list(initial_plan_ids),
                )
                if int(row.get("account_id") or 0) == int(account_id or 0)
                and str(row.get("deployment_id") or "") == str(deployment_id or "")
                and str(row.get("plan_id") or "") in initial_plan_ids
                and str(row.get("plan_stage") or "") == "initial"
                and str(row.get("direction") or "").lower() == str(decision.action or "").lower()
            ]
        filled = [
            row for row in executions
            if str(row.get("status") or "").lower() in {"filled", "partially_filled"}
        ]
        if self.repository is not None and initial_plan_ids and not filled:
            return {
                "allowed": False, "stage": stage,
                "reason": "突破阶段需要同一机会的首仓已成交",
                "opportunity_id": opportunity_id,
                "entry_role": "add_on",
            }
        direction = str(decision.action or "")
        filled_order_ids = {
            str(row.get("order_id") or "") for row in filled
            if str(row.get("order_id") or "")
        }
        filled_position_ids = set()
        if (
            self.repository is not None and filled_order_ids
            and hasattr(self.repository, "list_filled_position_ids")
        ):
            filled_position_ids = set(self.repository.list_filled_position_ids(
                int(user_id), int(account_id), list(filled_order_ids),
            ))
        matching = []
        for position in positions or []:
            item = position if isinstance(position, dict) else position.to_dict()
            item_direction = str(item.get("direction") or "").lower()
            if not item_direction:
                item_direction = "buy" if str(item.get("type") or "").upper() == "BUY" else "sell"
            position_id = int(item.get("ticket") or item.get("position_id") or 0)
            if (
                item_direction == direction
                and (not filled_position_ids or position_id in filled_position_ids)
            ):
                matching.append(item)
        if not matching:
            return {"allowed": False, "stage": stage, "reason": "同一机会首仓成交后尚未同步到当前持仓"}
        unprotected = [item for item in matching if float(item.get("sl") or 0) <= 0]
        if unprotected:
            return {"allowed": False, "stage": stage, "reason": "首仓保护止损尚未确认，禁止突破阶段加仓"}
        return {
            "allowed": True, "stage": stage,
            "reason": "同一机会首仓已成交且保护止损已确认",
            "position_count": len(matching), "opportunity_id": opportunity_id,
            "entry_role": "add_on",
        }

    def claim_for_decision(
        self, user_id: int, account_id: int, decision, *,
        deployment_id: str = "", execution_mode: str = "live",
        tick_id: str = "", gate_trace=None, account_snapshot=None,
    ):
        summary = decision.signal_summary or {}
        plan_id = str(summary.get("selected_trade_plan_id") or "")
        group_id = str(summary.get("selected_trade_plan_group_id") or "")
        if not plan_id:
            return {"plan_id": "", "group_id": "", "deployment": None, "claimed": False}
        deployment = {"deployment_id": str(deployment_id)} if deployment_id else None
        if deployment is None and self.repository is not None:
            deployment = self.repository.storage.fetchone(
                "SELECT deployment_id FROM strategy_deployments "
                "WHERE user_id=? AND account_id=? AND strategy_id=? "
                "AND execution_mode=? AND status='active' LIMIT 1",
                (int(user_id), int(account_id), str(decision.strategy_id),
                 str(execution_mode or "live")),
            )
        if not deployment:
            return {"plan_id": plan_id, "group_id": group_id, "deployment": None, "claimed": False}
        stage = str(summary.get("selected_trade_opportunity_stage") or "default")
        direction = str(decision.action or summary.get("direction") or "none").lower()
        plan = {
            **summary, "plan_id": plan_id, "plan_group_id": group_id,
            "plan_stage": stage, "direction": direction,
        }
        # The repository performs plan-status validation and the atomic
        # account/deployment/stage/direction claim in one operation.  Avoid a
        # stale pre-check here: an old signal may reference a superseded plan
        # and must return plan_inactive rather than a misleading conflict.
        claim_result = getattr(self.execution_service, "claim_result", None)
        if claim_result is not None:
            claim = claim_result(
                user_id=int(user_id), account_id=int(account_id),
                deployment_id=str(deployment["deployment_id"]),
                strategy_id=str(decision.strategy_id), plan=plan,
                reason=str(decision.decision_reason or ""),
                tick_id=str(tick_id or ""), execution_mode=str(execution_mode or ""),
                gate_trace=gate_trace, account_snapshot=account_snapshot,
            )
        else:
            claim = {"claimed": bool(self.execution_service.claim(
            user_id=int(user_id), account_id=int(account_id),
            deployment_id=str(deployment["deployment_id"]),
            strategy_id=str(decision.strategy_id), plan=plan,
            reason=str(decision.decision_reason or ""),
            tick_id=str(tick_id or ""), execution_mode=str(execution_mode or ""),
            gate_trace=gate_trace, account_snapshot=account_snapshot,
            )), "reason_code": "claimed", "reason": "", "details": {}}
        return {
            "plan_id": plan_id, "group_id": group_id,
            "deployment": deployment, "claimed": bool(claim.get("claimed")), "plan": plan,
            "reason_code": str(claim.get("reason_code") or ("claimed" if claim.get("claimed") else "claim_failed")),
            "reason": str(claim.get("reason") or ""),
            "details": dict(claim.get("details") or {}),
            "tick_id": str(tick_id or ""), "execution_mode": str(execution_mode or ""),
            "gate_trace": list(gate_trace or []),
            "account_snapshot": dict(account_snapshot or {}),
        }

    def record_order(self, user_id: int, account_id: int, decision, context, order_id: str) -> None:
        if not context.get("claimed") or not context.get("deployment"):
            return
        plan = context.get("plan") or {}
        self.execution_service.record_order(
            user_id=int(user_id), account_id=int(account_id),
            deployment_id=str(context["deployment"]["deployment_id"]),
            strategy_id=str(decision.strategy_id), plan=plan,
            order_id=order_id, reason=str(decision.decision_reason or ""),
            tick_id=str(context.get("tick_id") or ""),
            execution_mode=str(context.get("execution_mode") or ""),
            gate_trace=context.get("gate_trace") or [],
            account_snapshot=context.get("account_snapshot") or {},
        )

    def release(self, user_id: int, account_id: int, context, reason: str) -> None:
        if not context.get("claimed") or not context.get("deployment"):
            return
        self.execution_service.release(
            user_id=int(user_id), account_id=int(account_id),
            deployment_id=str(context["deployment"]["deployment_id"]),
            plan=context.get("plan") or {"plan_id": context.get("plan_id", "")},
            reason=reason,
        )
