"""Canonical events for audited non-human alpha maintenance transitions."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from quantos.contracts.events import (
    EVENT_ALPHA_MAINTENANCE_TRANSITION,
    Event,
    event_timestamp_from_milliseconds,
)


_ACTIONS = frozenset({"DEGRADED", "PAUSED", "RETIRED"})


def _event_id(*parts: object) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()
    return f"alpha-maint-{digest[:32]}"


def _json_safe(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=True, sort_keys=True, default=str))


def maintenance_transition_events(
    report: object, *, actor_id: str, timestamp_ms: int
) -> tuple[Event, ...]:
    if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int) or timestamp_ms < 0:
        raise ValueError("timestamp_ms must be a non-negative integer")
    if not isinstance(report, Mapping):
        return ()

    events = []
    for alpha_id in sorted(report):
        entry = report[alpha_id]
        if not isinstance(entry, Mapping):
            continue
        action = entry.get("action")
        if action not in _ACTIONS:
            continue
        reasons = [
            _json_safe(reason)
            for reason in (entry.get("reasons") or entry.get("alerts") or ())
        ]
        events.append(
            Event(
                event_id=_event_id(timestamp_ms, alpha_id, action),
                event_type=EVENT_ALPHA_MAINTENANCE_TRANSITION,
                timestamp=event_timestamp_from_milliseconds(timestamp_ms),
                source="services.alpha.maintenance",
                domain="research",
                criticality="C1" if action in {"PAUSED", "RETIRED"} else "C2",
                payload={
                    "action": action,
                    "actor_id": actor_id,
                    "alpha_id": str(alpha_id),
                    "reasons": reasons,
                },
            )
        )
    return tuple(events)
