"""Evaluate and publish single-node domain-event consumer health."""
from __future__ import annotations

import os
import time

from services.monitoring.domain_event_runtime import (
    DOMAIN_EVENT_DB,
    read_domain_event_bus_health,
)
from services.monitoring.event_health import EventBusHealthPolicy
from tools.alert_sink import emit
from tools.cache_io import atomic_write_json


DOMAIN_EVENT_HEALTH_SNAPSHOT = os.path.join(
    "data", "cache", "domain_event_health.json"
)


def _alert_messages(health: dict) -> list[str]:
    messages = []
    for alert in health.get("alerts", ()):
        code = str(alert.get("code", "EVENT_HEALTH_UNKNOWN"))
        observed = alert.get("observed")
        threshold = alert.get("threshold")
        messages.append(
            f"domain event bus {code}: observed={observed} threshold={threshold}"
        )
    return messages


def run_event_bus_health(
    *,
    now_ms: int | None = None,
    event_path: str = DOMAIN_EVENT_DB,
    output_path: str = DOMAIN_EVENT_HEALTH_SNAPSHOT,
    policy: EventBusHealthPolicy | None = None,
    health_reader=None,
    alert_emitter=None,
) -> dict:
    """Persist one health snapshot and deliver its bounded alert codes."""
    resolved_now_ms = int(time.time() * 1_000) if now_ms is None else int(now_ms)
    resolved_reader = health_reader or read_domain_event_bus_health
    resolved_emitter = alert_emitter or emit
    reader_kwargs = {"now_ms": resolved_now_ms, "path": event_path}
    if policy is not None:
        reader_kwargs["policy"] = policy
    health = resolved_reader(**reader_kwargs)
    alerts = _alert_messages(health)
    alert_delivery = resolved_emitter(
        alerts,
        context="domain_event_bus",
        now_ms=resolved_now_ms,
    )
    document = {
        **health,
        "data_provenance": "VERIFIED_DOMAIN_EVENT_AUTHORITY",
        "generated_at_ms": resolved_now_ms,
        "alert_delivery": alert_delivery,
    }
    atomic_write_json(output_path, document, indent=2)
    return document


def main() -> int:
    document = run_event_bus_health()
    print(
        "domain event health: "
        f"{document['verdict']}; alerts={len(document.get('alerts', ())) }"
    )
    return 1 if document["verdict"] == "BREACH" else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DOMAIN_EVENT_HEALTH_SNAPSHOT", "run_event_bus_health"]
