"""Drain authoritative deployment-evaluation events into the local event runtime."""
from __future__ import annotations

import os

from services.monitoring.domain_event_runtime import (
    dispatch_deployment_event_outbox,
    open_domain_event_bus,
    project_pending_domain_events,
)


def dispatch_and_project_deployment_events(
    store,
    *,
    state_path: str,
    now: int,
    worker_id: str,
) -> dict:
    parent = os.path.dirname(state_path) or "."
    event_bus = open_domain_event_bus(os.path.join(parent, "domain_events.db"))
    try:
        dispatch = dispatch_deployment_event_outbox(store, event_bus, at_ms=now)
        projection = project_pending_domain_events(
            event_bus,
            now_ms=now,
            projection_path=os.path.join(parent, "domain_event_projection.db"),
            worker_id=worker_id,
        )
        return {"dispatch": dispatch, "projection": projection}
    finally:
        event_bus.close()


__all__ = ["dispatch_and_project_deployment_events"]
