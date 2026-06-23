"""Kill-switch engine (Phase 8) — the one mutable, latched object.

Arm: ANY actor may halt trading (easy to stop). Reset: HUMAN only, bound to the
specific incident by a granted ApprovalRecord (review P0-4) — agents/SYSTEM can
never clear it. is_active() FAILS CLOSED: missing/unreadable state reads as
tripped (corrupt safety state must never mean "safe"). No auto-reset, ever.
"""
from __future__ import annotations

from typing import Optional

from quantos.contracts.alpha import Actor
from quantos.contracts.governance import ApprovalRecord
from quantos.contracts.killswitch import KillSwitchScope, KillSwitchState


class UnauthorizedReset(PermissionError):
    pass


class KillSwitch:
    def __init__(self, state: Optional[KillSwitchState] = None):
        self._state = state if state is not None else KillSwitchState()

    @property
    def state(self) -> KillSwitchState:
        return self._state

    def is_active(self, scope_value: str = "") -> bool:
        s = self._state
        if not isinstance(s, KillSwitchState):
            return True                                  # fail-closed on corrupt state
        if not s.tripped:
            return False
        if s.scope == KillSwitchScope.GLOBAL:
            return True
        return s.scope_value == scope_value              # narrowed scope matches the order

    def arm(self, actor: Actor, reason: str, *, at: int,
            scope: KillSwitchScope = KillSwitchScope.GLOBAL, scope_value: str = "",
            incident_id: str = "") -> KillSwitchState:
        if not isinstance(self._state, KillSwitchState):   # corrupt state -> still arm (fail-closed)
            self._state = KillSwitchState()
        n = self._state.trip_count + 1
        self._state = KillSwitchState(
            tripped=True, scope=scope, scope_value=scope_value,
            incident_id=incident_id or f"trip-{n}", armed_by=actor, reason=reason,
            armed_at=at, trip_count=n,
        )
        return self._state

    def reset(self, actor: Optional[Actor], approval: Optional[ApprovalRecord], *, at: int) -> KillSwitchState:
        s = self._state
        if not s.tripped:
            return s
        if actor is None or not actor.is_human:
            raise UnauthorizedReset("kill-switch reset requires a HUMAN actor")
        if approval is None or not approval.approved:
            raise UnauthorizedReset("reset requires a granted human ApprovalRecord")
        if approval.subject_id != s.incident_id:
            raise UnauthorizedReset("approval is not bound to this kill-switch incident")
        if approval.approver != actor.actor_id:
            raise UnauthorizedReset("the granted approval must name the resetting human")
        self._state = s.model_copy(update={"tripped": False, "reset_by": actor, "reset_at": at})
        return self._state
