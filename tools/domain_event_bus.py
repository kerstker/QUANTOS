"""Open the shared offline domain-event database used by scheduled tool boundaries."""
from __future__ import annotations

from services.execution.dry_run_store import (
    DryRunOutboxEvent,
    DryRunRun,
    DryRunStore,
)
from services.execution.events import dry_run_completed_event, dry_run_started_event
from services.monitoring.domain_event_runtime import (
    DOMAIN_EVENT_DB,
    DOMAIN_EVENT_PROJECTION_CONSUMER,
    DOMAIN_EVENT_PROJECTION_DB,
    open_domain_event_bus,
    project_pending_domain_events,
)
from services.monitoring.event_bus import DurableEventBus
from services.risk.research_risk.events import research_risk_evaluated_event


def dispatch_dry_run_outbox(
    store: DryRunStore,
    event_bus: DurableEventBus,
    *,
    at_ms: int,
    limit: int = 1_000,
) -> dict:
    """Idempotently transfer pending dry-run events into the shared event log."""
    pending = store.pending_outbox(limit=limit)
    dispatched = 0
    for record in pending:
        event = record["event"]
        event_bus.publish_once(
            event,
            correlation_id=record["correlation_id"],
            causation_id=record["causation_id"],
        )
        store.mark_outbox_dispatched(
            record["outbox_id"],
            dispatched_at=at_ms,
            event_repro_hash=event.reproducibility_hash,
        )
        dispatched += 1
    return {
        "dispatched": dispatched,
        "pending": len(store.pending_outbox(limit=limit)),
    }


def save_and_project_dry_run(
    store: DryRunStore,
    run: DryRunRun,
    event_bus: DurableEventBus,
    *,
    now_ms: int,
    projection_path: str = DOMAIN_EVENT_PROJECTION_DB,
    project: bool = True,
) -> dict:
    """Atomically retain a run+completion outbox and dispatch it.

    ``project=True`` keeps the historical one-shot behavior.  A controlled
    ``project=False`` mode is used by high-volume offline drivers: the run and
    its outbox are still committed and published atomically, while a verified
    full projection rebuild can catch up the append-only read model without
    replaying every event through the quadratic per-event runtime path.
    """
    if not isinstance(project, bool):
        raise TypeError("project must be a bool")
    mode = str(run.detail.get("mode") or "single_period")
    started = dry_run_started_event(
        run_id=run.run_id,
        strategy_id=run.strategy_id,
        timestamp_ms=int(run.timestamp),
        mode=mode,
    )
    completed = dry_run_completed_event(
        run_id=run.run_id,
        strategy_id=run.strategy_id,
        timestamp_ms=int(run.timestamp),
        n_intents=run.n_intents,
        n_approved=run.n_approved,
        n_rejected=run.n_rejected,
        n_fills=int(run.detail.get("n_fills") or 0),
        audit_root=run.audit_root,
        run_repro_hash=run.reproducibility_hash,
    )
    messages = []
    completion_causation_id = started.event_id
    risk = run.detail.get("research_risk")
    # Only evaluated engine sets have a canonical risk event.  Strict FULL
    # runs with missing evidence remain PENDING and must not be mislabeled as
    # subset/full evaluated evidence.
    if isinstance(risk, dict) and risk.get("engines_run"):
        risk_event = research_risk_evaluated_event(
            run_id=run.run_id,
            strategy_id=run.strategy_id,
            run_repro_hash=run.reproducibility_hash,
            timestamp_ms=int(run.timestamp),
            risk=risk,
        )
        messages.append(
            DryRunOutboxEvent(
                run_id=run.run_id,
                event=risk_event,
                correlation_id=run.run_id,
                causation_id=started.event_id,
            )
        )
        completion_causation_id = risk_event.event_id
    messages.append(
        DryRunOutboxEvent(
            run_id=run.run_id,
            event=completed,
            correlation_id=run.run_id,
            causation_id=completion_causation_id,
        )
    )
    store.save_run_with_outbox(
        run,
        messages,
        require_completion=True,
    )
    dispatch = dispatch_dry_run_outbox(store, event_bus, at_ms=now_ms)
    projection = (
        project_pending_domain_events(
            event_bus, now_ms=now_ms, projection_path=projection_path
        )
        if project
        else {
            "skipped": True,
            "reason": "OFFLINE_BATCH_PROJECTION_REBUILD_REQUIRED",
            "projection_path": projection_path,
        }
    )
    return {"dispatch": dispatch, "projection": projection}


__all__ = [
    "DOMAIN_EVENT_DB",
    "DOMAIN_EVENT_PROJECTION_CONSUMER",
    "DOMAIN_EVENT_PROJECTION_DB",
    "dispatch_dry_run_outbox",
    "open_domain_event_bus",
    "project_pending_domain_events",
    "save_and_project_dry_run",
]
