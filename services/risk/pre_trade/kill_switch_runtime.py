"""Transactional kill-switch commands over the authoritative state store."""
from __future__ import annotations

from typing import Optional

from quantos.contracts.alpha import Actor
from quantos.contracts.events import EventOutboxMessage
from quantos.contracts.killswitch import KillSwitchScope, KillSwitchState
from services.governance.approval import ApprovalRequest
from services.risk.pre_trade.events import (
    kill_switch_activated_event,
    kill_switch_activation_event_id,
    kill_switch_reset_event,
)
from services.risk.pre_trade.kill_switch import KillSwitch
from services.risk.pre_trade.kill_switch_store import (
    KillSwitchStateConflict,
    KillSwitchStateStore,
)


def arm_and_record(
    store: KillSwitchStateStore,
    actor: Actor,
    reason: str,
    *,
    at: int,
    scope: KillSwitchScope = KillSwitchScope.GLOBAL,
    scope_value: str = "",
    incident_id: str = "",
) -> KillSwitchState:
    """Latch state and its redacted activation event in one CAS transaction."""
    previous = store.load_state()
    if previous.tripped:
        raise KillSwitchStateConflict(
            "kill-switch is already latched; refusing to rewrite the incident"
        )
    machine = KillSwitch(previous)
    state = machine.arm(
        actor,
        reason,
        at=at,
        scope=scope,
        scope_value=scope_value,
        incident_id=incident_id,
    )
    event = kill_switch_activated_event(
        state,
        actor=actor,
        reason=reason,
        at_ms=at,
    )
    store.append_transition(
        expected_state=previous,
        state=state,
        action="ARM",
        recorded_at=at,
        event_outbox=EventOutboxMessage(
            event=event,
            correlation_id=state.incident_id,
        ),
    )
    return state


def reset_with_grant_and_record(
    store: KillSwitchStateStore,
    actor: Optional[Actor],
    grant: ApprovalRequest,
    *,
    at: int,
) -> KillSwitchState:
    """Verify a multi-party grant, then atomically clear state and stage its event."""
    previous = store.load_state()
    if not previous.tripped:
        return previous
    machine = KillSwitch(previous)
    state = machine.reset_with_grant(actor, grant, at=at)
    assert actor is not None
    event = kill_switch_reset_event(
        previous,
        actor=actor,
        reset_via="multi_party",
        at_ms=at,
    )
    store.append_transition(
        expected_state=previous,
        state=state,
        action="RESET",
        recorded_at=at,
        event_outbox=EventOutboxMessage(
            event=event,
            correlation_id=previous.incident_id,
            causation_id=kill_switch_activation_event_id(previous),
        ),
    )
    return state


__all__ = ["arm_and_record", "reset_with_grant_and_record"]
