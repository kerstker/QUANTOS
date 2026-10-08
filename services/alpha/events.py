"""Canonical events produced by the alpha lifecycle boundary."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable

from quantos.contracts.events import (
    EVENT_ALPHA_PROMOTED,
    Event,
    event_timestamp_from_milliseconds,
)


def _event_id(*parts: object) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()
    return f"alpha-promotion-{digest[:32]}"


def alpha_promoted_event(
    *,
    request_id: str,
    alpha_id: str,
    src: str,
    dst: str,
    actor_id: str,
    grant_head_hash: str,
    eval_run_id: str,
    evidence_notes: Iterable[str],
    at_ms: int,
) -> Event:
    return Event(
        event_id=_event_id("promoted", request_id, alpha_id, dst),
        event_type=EVENT_ALPHA_PROMOTED,
        event_version="1.1.0",
        timestamp=event_timestamp_from_milliseconds(at_ms),
        source="services.alpha.promotion",
        domain="research",
        criticality="C1",
        payload={
            "actor_id": actor_id,
            "alpha_id": alpha_id,
            "dst": dst,
            "eval_run_id": eval_run_id,
            "evidence_notes": list(evidence_notes),
            "grant_head_hash": grant_head_hash,
            "request_id": request_id,
            "src": src,
        },
    )


__all__ = ["alpha_promoted_event"]
