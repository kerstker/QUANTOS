"""Canonical events for deployment-governance decisions."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable

from quantos.contracts.events import (
    EVENT_DEPLOYMENT_RECORDED,
    Event,
    event_timestamp_from_milliseconds,
)


def _event_id(*parts: object) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()
    return f"deployment-{digest[:32]}"


def deployment_recorded_event(
    *,
    environment: str,
    subject: str,
    actor_id: str,
    allowed: bool,
    reasons: Iterable[str],
    version_ids: Iterable[str],
    at_ms: int,
) -> Event:
    retained_reasons = tuple(reasons)
    retained_version_ids = tuple(version_ids)
    return Event(
        event_id=_event_id(
            "evaluated",
            environment,
            subject,
            actor_id,
            allowed,
            retained_reasons,
            retained_version_ids,
            at_ms,
        ),
        event_type=EVENT_DEPLOYMENT_RECORDED,
        event_version="1.1.0",
        timestamp=event_timestamp_from_milliseconds(at_ms),
        source="services.deployment.gate",
        domain="governance",
        criticality="C0" if environment in {"STAGING", "LIVE"} else "C1",
        payload={
            "actor_id": actor_id,
            "allowed": allowed,
            "environment": environment,
            "reasons": list(retained_reasons),
            "subject": subject,
            "version_ids": list(retained_version_ids),
        },
    )


__all__ = ["deployment_recorded_event"]
