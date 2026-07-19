"""Deterministic health policy for the single-node canonical event authority."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

from services.monitoring.consumer_group import (
    PROCESS_DECLARED_WORKER_ASSURANCE,
)


@dataclass(frozen=True)
class EventBusHealthPolicy:
    max_pending_events: int = 1_000
    max_oldest_pending_age_ms: int = 900_000
    max_dead_letters: int = 0
    max_event_records: int = 1_000_000
    max_consumer_journal_records: int = 5_000_000
    max_coordination_journal_records: int = 1_000_000
    max_allocated_bytes: int = 2_147_483_648
    warning_fraction: float = 0.8

    def __post_init__(self) -> None:
        for name in (
            "max_pending_events",
            "max_oldest_pending_age_ms",
            "max_dead_letters",
            "max_event_records",
            "max_consumer_journal_records",
            "max_coordination_journal_records",
            "max_allocated_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not 0.0 < self.warning_fraction <= 1.0:
            raise ValueError("warning_fraction must be in (0, 1]")


def _alert(
    code: str,
    severity: str,
    *,
    observed: int | None,
    threshold: int | None,
) -> dict:
    return {
        "code": code,
        "observed": observed,
        "severity": severity,
        "threshold": threshold,
    }


def evaluate_event_bus_health(
    snapshot: Mapping,
    policy: EventBusHealthPolicy | None = None,
) -> dict:
    """Grade a verified operational snapshot without mutating retained history."""
    if not isinstance(snapshot, Mapping):
        raise TypeError("snapshot must be a mapping")
    policy = policy or EventBusHealthPolicy()
    if not isinstance(policy, EventBusHealthPolicy):
        raise TypeError("policy must be an EventBusHealthPolicy")
    integrity = dict(snapshot.get("integrity", {}))
    group = dict(snapshot.get("group", {}))
    totals = dict(snapshot.get("totals", {}))
    partitions = tuple(snapshot.get("partitions", ()))
    alerts: list[dict] = []

    for key, code in (
        ("event_chain_valid", "EVENT_CHAIN_INVALID"),
        ("consumer_chain_valid", "CONSUMER_CHAIN_INVALID"),
        ("coordination_chain_valid", "COORDINATION_CHAIN_INVALID"),
    ):
        if integrity.get(key) is not True:
            alerts.append(_alert(code, "CRITICAL", observed=None, threshold=None))

    pending = int(totals.get("pending_events", 0))
    pending_ages = [
        int(item["oldest_pending_age_ms"])
        for item in partitions
        if item.get("oldest_pending_age_ms") is not None
    ]
    oldest_pending_age = max(pending_ages, default=None)
    if pending > policy.max_pending_events:
        alerts.append(
            _alert(
                "CONSUMER_LAG_EVENTS_EXCEEDED",
                "CRITICAL",
                observed=pending,
                threshold=policy.max_pending_events,
            )
        )
    if (
        oldest_pending_age is not None
        and oldest_pending_age > policy.max_oldest_pending_age_ms
    ):
        alerts.append(
            _alert(
                "CONSUMER_LAG_AGE_EXCEEDED",
                "CRITICAL",
                observed=oldest_pending_age,
                threshold=policy.max_oldest_pending_age_ms,
            )
        )
    if pending and oldest_pending_age is None:
        alerts.append(
            _alert(
                "CONSUMER_LAG_AGE_UNAVAILABLE",
                "WARNING",
                observed=pending,
                threshold=None,
            )
        )

    dead_letters = int(totals.get("dead_letters", 0))
    if dead_letters > policy.max_dead_letters:
        alerts.append(
            _alert(
                "DEAD_LETTERS_PRESENT",
                "CRITICAL",
                observed=dead_letters,
                threshold=policy.max_dead_letters,
            )
        )

    retention_metrics = (
        (
            "event_records",
            policy.max_event_records,
            "RETENTION_EVENT_ROWS",
        ),
        (
            "consumer_journal_records",
            policy.max_consumer_journal_records,
            "RETENTION_CONSUMER_JOURNAL",
        ),
        (
            "coordination_journal_records",
            policy.max_coordination_journal_records,
            "RETENTION_COORDINATION_JOURNAL",
        ),
        (
            "allocated_bytes",
            policy.max_allocated_bytes,
            "RETENTION_STORAGE",
        ),
    )
    for key, limit, prefix in retention_metrics:
        observed = int(totals.get(key, 0))
        if observed > limit:
            alerts.append(
                _alert(
                    f"{prefix}_LIMIT_EXCEEDED",
                    "CRITICAL",
                    observed=observed,
                    threshold=limit,
                )
            )
        elif observed >= limit * policy.warning_fraction:
            alerts.append(
                _alert(
                    f"{prefix}_NEAR_LIMIT",
                    "WARNING",
                    observed=observed,
                    threshold=limit,
                )
            )

    verdict = "PASS"
    if any(alert["severity"] == "CRITICAL" for alert in alerts):
        verdict = "BREACH"
    elif alerts:
        verdict = "WARN"
    return {
        "schema_version": 1,
        "verdict": verdict,
        "checked_at_ms": int(snapshot.get("checked_at_ms", 0)),
        "group_id": str(group.get("group_id", "")),
        "consumer_mode": "SINGLE_NODE_LEASED_PARTITIONS",
        "integrity": integrity,
        "active_workers": list(group.get("active_workers", ())),
        "active_worker_identities": list(
            group.get("active_worker_identities", ())
        ),
        "worker_identity_policy": str(
            group.get(
                "worker_identity_policy",
                PROCESS_DECLARED_WORKER_ASSURANCE,
            )
        ),
        "partition_count": int(group.get("partition_count", 0)),
        "pending_events": pending,
        "oldest_pending_age_ms": oldest_pending_age,
        "dead_letter_count": dead_letters,
        "event_records": int(totals.get("event_records", 0)),
        "consumer_journal_records": int(
            totals.get("consumer_journal_records", 0)
        ),
        "coordination_journal_records": int(
            totals.get("coordination_journal_records", 0)
        ),
        "allocated_bytes": int(totals.get("allocated_bytes", 0)),
        "retention_mode": "APPEND_ONLY_ALERT_ONLY",
        "thresholds": asdict(policy),
        "alerts": alerts,
    }


__all__ = ["EventBusHealthPolicy", "evaluate_event_bus_health"]
