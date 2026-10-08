"""Run the L2-only domain-event recovery and health cycle as one scheduled job."""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping

from services.monitoring.domain_event_runtime import (
    DOMAIN_EVENT_DB,
    DOMAIN_EVENT_PROJECTION_DB,
)
from tools.alert_sink import emit
from tools.cache_io import atomic_write_json
from tools.domain_event_health import (
    DOMAIN_EVENT_HEALTH_SNAPSHOT,
    run_event_bus_health,
)
from tools.domain_event_outbox_drain import (
    DOMAIN_EVENT_DRAIN_SNAPSHOT,
    drain_domain_event_outboxes,
)


DOMAIN_EVENT_OPS_STATUS = os.path.join(
    "data", "cache", "domain_event_ops_status.json"
)

_ALLOWED_VERDICTS = frozenset(
    {"PASS", "WARN", "PARTIAL", "UNAVAILABLE", "BREACH"}
)
_VERDICT_SEVERITY = {
    "PASS": 0,
    "WARN": 1,
    "PARTIAL": 2,
    "UNAVAILABLE": 3,
    "BREACH": 4,
}


def _validated_now_ms(now_ms: int | None) -> int:
    if now_ms is None:
        return int(time.time() * 1_000)
    if isinstance(now_ms, bool) or not isinstance(now_ms, int):
        raise TypeError("now_ms must be an integer")
    if now_ms < 0:
        raise ValueError("now_ms must be non-negative")
    return now_ms


def _validated_result(value: object) -> dict:
    if not isinstance(value, Mapping):
        raise TypeError("component result must be a mapping")
    result = dict(value)
    verdict = result.get("verdict")
    if verdict not in _ALLOWED_VERDICTS:
        raise ValueError("component result has an unknown verdict")
    return result


def _failure(exc: Exception) -> dict:
    return {
        "verdict": "BREACH",
        "status": "EXECUTION_FAILED",
        "error_type": type(exc).__name__,
    }


def _overall_verdict(*results: Mapping[str, object]) -> str:
    return max(
        (str(result["verdict"]) for result in results),
        key=_VERDICT_SEVERITY.__getitem__,
    )


def exit_code_for_verdict(verdict: str) -> int:
    if verdict not in _ALLOWED_VERDICTS:
        raise ValueError(f"unknown domain event ops verdict: {verdict}")
    if verdict == "PASS":
        return 0
    if verdict == "BREACH":
        return 1
    return 2


def run_domain_event_ops(
    *,
    now_ms: int | None = None,
    event_path: str = DOMAIN_EVENT_DB,
    projection_path: str = DOMAIN_EVENT_PROJECTION_DB,
    drain_output_path: str = DOMAIN_EVENT_DRAIN_SNAPSHOT,
    health_output_path: str = DOMAIN_EVENT_HEALTH_SNAPSHOT,
    output_path: str = DOMAIN_EVENT_OPS_STATUS,
    drain_runner: Callable[..., object] = drain_domain_event_outboxes,
    health_runner: Callable[..., object] = run_event_bus_health,
    alert_emitter: Callable[..., object] = emit,
) -> dict:
    """Recover pending events, then evaluate health even if recovery fails."""
    resolved_now_ms = _validated_now_ms(now_ms)
    ops_alerts: list[str] = []

    try:
        drain = _validated_result(
            drain_runner(
                now_ms=resolved_now_ms,
                event_path=event_path,
                projection_path=projection_path,
                output_path=drain_output_path,
            )
        )
    except Exception as exc:
        drain = _failure(exc)
        ops_alerts.append(
            "domain event recovery "
            f"DOMAIN_EVENT_DRAIN_EXCEPTION:{type(exc).__name__}"
        )
    else:
        if drain["verdict"] != "PASS":
            ops_alerts.append(
                "domain event recovery "
                f"DOMAIN_EVENT_DRAIN_{drain['verdict']}"
            )

    try:
        health = _validated_result(
            health_runner(
                now_ms=resolved_now_ms,
                event_path=event_path,
                output_path=health_output_path,
            )
        )
    except Exception as exc:
        health = _failure(exc)
        ops_alerts.append(
            "domain event health "
            f"DOMAIN_EVENT_HEALTH_EXCEPTION:{type(exc).__name__}"
        )
    else:
        if health["verdict"] != "PASS":
            ops_alerts.append(
                "domain event health "
                f"DOMAIN_EVENT_HEALTH_{health['verdict']}"
            )

    verdict = _overall_verdict(drain, health)
    if ops_alerts:
        try:
            delivery = alert_emitter(
                ops_alerts,
                context="domain_event_ops",
                now_ms=resolved_now_ms,
            )
            if not isinstance(delivery, Mapping):
                raise TypeError("alert emitter result must be a mapping")
            alert_delivery = dict(delivery)
        except Exception as exc:
            alert_delivery = {
                "status": "BREACH",
                "error_type": type(exc).__name__,
            }
            verdict = "BREACH"
    else:
        alert_delivery = {"n": 0, "pushed": False}

    document = {
        "schema_version": 1,
        "mode": "L2_DRY_RUN_EVENT_OPERATIONS",
        "readiness": "L2_DRY_RUN_ONLY",
        "data_provenance": "VERIFIED_LOCAL_EVENT_AUTHORITIES",
        "generated_at_ms": resolved_now_ms,
        "verdict": verdict,
        "drain": drain,
        "health": health,
        "alert_delivery": alert_delivery,
        "safety": {
            "domain_state_mutation": False,
            "order_submission": False,
            "broker_connectivity": False,
            "credential_access": False,
            "retention_mutation": False,
        },
    }
    atomic_write_json(output_path, document, indent=2)
    return document


def main() -> int:
    document = run_domain_event_ops()
    print(
        "domain event operations: "
        f"{document['verdict']}; "
        f"drain={document['drain']['verdict']}; "
        f"health={document['health']['verdict']}"
    )
    return exit_code_for_verdict(document["verdict"])


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DOMAIN_EVENT_OPS_STATUS",
    "exit_code_for_verdict",
    "run_domain_event_ops",
]
