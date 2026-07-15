"""Canonical deterministic events emitted by the dry-run execution boundary."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable

from quantos.contracts.events import (
    EVENT_DRY_RUN_COMPLETED,
    EVENT_DRY_RUN_STARTED,
    EVENT_PRETRADE_APPROVED,
    EVENT_PRETRADE_REJECTED,
    Event,
    event_timestamp_from_milliseconds,
)


def _event_id(*parts: object) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()
    return f"dryrun-{digest[:32]}"


def dry_run_started_event(
    *, run_id: str, strategy_id: str, timestamp_ms: int, mode: str
) -> Event:
    return Event(
        event_id=_event_id("started", run_id),
        event_type=EVENT_DRY_RUN_STARTED,
        timestamp=event_timestamp_from_milliseconds(timestamp_ms),
        source="services.execution.dry_run_pipeline",
        domain="execution",
        criticality="C2",
        payload={"mode": mode, "run_id": run_id, "strategy_id": strategy_id},
    )


def pretrade_decision_event(
    *,
    run_id: str,
    strategy_id: str,
    timestamp_ms: int,
    scope: str,
    instrument_id: str,
    side: str,
    quantity: int,
    approved: bool,
    reasons: Iterable[object] = (),
) -> Event:
    reason_list = [str(reason) for reason in reasons]
    outcome = "approved" if approved else "rejected"
    return Event(
        event_id=_event_id(
            "pretrade", run_id, scope, instrument_id, side, quantity, outcome
        ),
        event_type=EVENT_PRETRADE_APPROVED if approved else EVENT_PRETRADE_REJECTED,
        timestamp=event_timestamp_from_milliseconds(timestamp_ms),
        source="services.execution.dry_run_pipeline",
        domain="risk",
        criticality="C2" if approved else "C1",
        payload={
            "approved": approved,
            "instrument_id": instrument_id,
            "quantity": int(quantity),
            "reasons": reason_list,
            "run_id": run_id,
            "side": side,
            "strategy_id": strategy_id,
        },
    )


def dry_run_completed_event(
    *,
    run_id: str,
    strategy_id: str,
    timestamp_ms: int,
    n_intents: int,
    n_approved: int,
    n_rejected: int,
    n_fills: int,
    audit_root: str,
    run_repro_hash: str,
) -> Event:
    return Event(
        event_id=_event_id("completed", run_id),
        event_type=EVENT_DRY_RUN_COMPLETED,
        event_version="1.1.0",
        timestamp=event_timestamp_from_milliseconds(timestamp_ms),
        source="services.execution.dry_run_pipeline",
        domain="execution",
        criticality="C2",
        payload={
            "audit_root": audit_root,
            "n_approved": int(n_approved),
            "n_fills": int(n_fills),
            "n_intents": int(n_intents),
            "n_rejected": int(n_rejected),
            "run_id": run_id,
            "run_repro_hash": run_repro_hash,
            "strategy_id": strategy_id,
        },
    )
