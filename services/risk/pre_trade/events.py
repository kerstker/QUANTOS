"""Canonical domain events emitted by the pre-trade safety boundary."""
from __future__ import annotations

import hashlib

from quantos.contracts.alpha import Actor
from quantos.contracts.events import (
    EVENT_KILL_SWITCH_ACTIVATED,
    EVENT_KILL_SWITCH_RESET,
    Event,
    event_timestamp_from_milliseconds,
)
from quantos.contracts.killswitch import KillSwitchState


def _event_id(*parts: object) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()
    return f"killswitch-{digest[:32]}"


def kill_switch_activated_event(
    state: KillSwitchState,
    *,
    actor: Actor,
    reason: str,
    at_ms: int,
    event_version: str = "1.1.0",
) -> Event:
    """Describe a halt without retaining potentially sensitive free-form reason text."""
    reason_fingerprint = hashlib.sha256(
        reason.encode("utf-8", errors="replace")
    ).hexdigest()
    if event_version == "1.0.0":
        timestamp = at_ms
    elif event_version == "1.1.0":
        timestamp = event_timestamp_from_milliseconds(at_ms)
    else:
        raise ValueError("unsupported kill-switch event version")
    return Event(
        event_id=_event_id("activated", state.incident_id, state.trip_count, at_ms),
        event_type=EVENT_KILL_SWITCH_ACTIVATED,
        event_version=event_version,
        timestamp=timestamp,
        source="services.risk.pre_trade.kill_switch",
        domain="risk",
        criticality="C0",
        payload={
            "actor_id": actor.actor_id,
            "incident_id": state.incident_id,
            "reason_fingerprint": reason_fingerprint,
            "scope": state.scope.value,
            "scope_value": state.scope_value,
            "trip_count": state.trip_count,
        },
    )


def kill_switch_reset_event(
    state: KillSwitchState,
    *,
    actor: Actor,
    reset_via: str,
    at_ms: int,
    event_version: str = "1.1.0",
) -> Event:
    if event_version == "1.0.0":
        timestamp = at_ms
    elif event_version == "1.1.0":
        timestamp = event_timestamp_from_milliseconds(at_ms)
    else:
        raise ValueError("unsupported kill-switch event version")
    return Event(
        event_id=_event_id(
            "reset",
            state.incident_id,
            state.trip_count,
            reset_via,
            at_ms,
        ),
        event_type=EVENT_KILL_SWITCH_RESET,
        event_version=event_version,
        timestamp=timestamp,
        source="services.risk.pre_trade.kill_switch",
        domain="risk",
        criticality="C0",
        payload={
            "actor_id": actor.actor_id,
            "incident_id": state.incident_id,
            "reset_via": reset_via,
            "trip_count": state.trip_count,
        },
    )


def kill_switch_activation_event_id(state: KillSwitchState) -> str:
    return _event_id(
        "activated",
        state.incident_id,
        state.trip_count,
        state.armed_at,
    )


__all__ = [
    "kill_switch_activated_event",
    "kill_switch_activation_event_id",
    "kill_switch_reset_event",
]
