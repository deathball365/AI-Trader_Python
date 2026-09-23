"""Unified entry-guard boundary shared by live and Paper execution paths."""


class EntryGuardService:
    def __init__(self, *, replay_guard, circuit_breaker, repeat_guard=None):
        self.replay_guard = replay_guard
        self.circuit_breaker = circuit_breaker
        self.repeat_guard = repeat_guard

    def check_live(self, user_id, account_id, strategy, signal, enabled=True, action=""):
        if not enabled:
            return {"allowed": True, "loss_streak": 0, "scope": "live"}
        replay = self.replay_guard.check_live(user_id, account_id, strategy, signal)
        if not replay.get("allowed", True):
            return replay
        if self.repeat_guard is not None:
            repeat = self.repeat_guard.check(
                user_id=user_id, account_id=account_id, strategy=strategy,
                signal=signal, execution_mode="live", action=action,
            )
            if not repeat.get("allowed", True):
                return repeat
        return self.circuit_breaker.check_live(user_id, account_id, strategy, signal)

    @staticmethod
    def check_paper(legacy_checker, *args, **kwargs):
        """Temporary adapter keeps Paper's persistence-specific checks intact."""
        return legacy_checker(*args, **kwargs)
