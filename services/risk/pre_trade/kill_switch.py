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
from services.governance.approval import ApprovalRequest, verify_multi_party_grant


class UnauthorizedReset(PermissionError):
    pass


def _emit(sink: object, *, event_id: str, actor: Actor, action: str, at: int,
          target: str, metadata: dict) -> None:
    """Best-effort emit to a duck-typed audit sink (Rank #20). A None sink is a no-op, so the
    decision paths default to unchanged behaviour; any object exposing GlobalAuditLog/DurableAuditLog's
    append(event_id, actor, action, timestamp, target, metadata) is a valid sink."""
    if sink is None:
        return
    sink.append(event_id=event_id, actor=actor.actor_id if actor is not None else "unknown",
                action=action, timestamp=at, target=target, metadata=metadata)


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
            incident_id: str = "", audit_sink: object = None) -> KillSwitchState:
        if not isinstance(self._state, KillSwitchState):   # corrupt state -> still arm (fail-closed)
            self._state = KillSwitchState()
        n = self._state.trip_count + 1
        self._state = KillSwitchState(
            tripped=True, scope=scope, scope_value=scope_value,
            incident_id=incident_id or f"trip-{n}", armed_by=actor, reason=reason,
            armed_at=at, trip_count=n,
        )
        # Halt is the SAFE direction — the trip already stands; recording it is a side effect.
        _emit(audit_sink, event_id=f"ks.arm.{self._state.incident_id}.{at}", actor=actor,
              action="kill_switch.arm", at=at, target=self._state.incident_id,
              metadata={"reason": reason, "scope": scope.value if hasattr(scope, "value") else str(scope),
                        "scope_value": scope_value, "trip_count": str(n)})
        return self._state

    def reset(self, actor: Optional[Actor], approval: Optional[ApprovalRecord], *, at: int,
              audit_sink: object = None) -> KillSwitchState:
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
        # Reset RESUMES trading — record before clearing, so an audit failure keeps the halt (fail-closed).
        _emit(audit_sink, event_id=f"ks.reset.{s.incident_id}.{at}", actor=actor,
              action="kill_switch.reset", at=at, target=s.incident_id,
              metadata={"reset_via": "single"})
        self._state = s.model_copy(update={"tripped": False, "reset_by": actor, "reset_at": at})
        return self._state

    def reset_with_grant(self, actor: Optional[Actor], grant: ApprovalRequest, *, at: int,
                         audit_sink: object = None) -> KillSwitchState:
        """复位(多方路径):要 HUMAN actor + 绑定到本事件的、已 CONSUMED 的三方 grant。fail-closed。
        旧 reset(单 ApprovalRecord)不变;这是更严的加法入口。"""
        s = self._state
        if not s.tripped:
            return s
        if actor is None or not actor.is_human:
            raise UnauthorizedReset("kill-switch reset requires a HUMAN actor")
        if not verify_multi_party_grant(grant, s.incident_id):
            raise UnauthorizedReset("reset requires a consumed multi-party approval grant bound to this incident")
        _emit(audit_sink, event_id=f"ks.reset.{s.incident_id}.{at}", actor=actor,
              action="kill_switch.reset", at=at, target=s.incident_id,
              metadata={"reset_via": "multi_party"})
        self._state = s.model_copy(update={"tripped": False, "reset_by": actor, "reset_at": at})
        return self._state
