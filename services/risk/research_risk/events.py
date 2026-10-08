"""Canonical events for honestly evaluated dry-run research risk."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping

from quantos.contracts.events import (
    EVENT_RESEARCH_RISK_EVALUATED,
    Event,
    event_timestamp_from_milliseconds,
)


_RESEARCH_RISK_PROVENANCE = frozenset(
    {
        "REAL_RESEARCH_RISK_SUBSET",
        "REAL_RESEARCH_RISK_FULL",
    }
)


def _event_id(*parts: object) -> str:
    digest = hashlib.sha256(
        "|".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()
    return f"research-risk-{digest[:32]}"


def research_risk_evaluated_event(
    *,
    run_id: str,
    strategy_id: str,
    run_repro_hash: str,
    timestamp_ms: int,
    risk: Mapping,
) -> Event:
    """Bind a subset risk decision to the exact immutable dry-run snapshot."""
    decision = str(risk.get("decision", ""))
    if decision not in {"RISK_APPROVED", "RISK_REJECTED"}:
        raise ValueError("risk decision must be RISK_APPROVED or RISK_REJECTED")
    engines_run_raw = risk.get("engines_run")
    if not isinstance(engines_run_raw, Mapping) or not engines_run_raw:
        raise ValueError("engines_run must be a non-empty mapping")
    engines_run = {
        str(name): value for name, value in sorted(engines_run_raw.items())
    }
    if any(not name or not isinstance(value, bool) for name, value in engines_run.items()):
        raise ValueError("engines_run must map non-empty names to bool values")
    gates_run = int(risk.get("gates_run", -1))
    gates_passed = int(risk.get("gates_passed", -1))
    observed_passes = sum(1 for passed in engines_run.values() if passed)
    if gates_run != len(engines_run):
        raise ValueError("gates_run must equal the number of engines_run")
    if gates_passed != observed_passes:
        raise ValueError("gates_passed must equal the number of passed engines")
    expected_decision = (
        "RISK_APPROVED" if observed_passes == gates_run else "RISK_REJECTED"
    )
    if decision != expected_decision:
        raise ValueError("risk decision does not match engine results")
    skipped_raw = risk.get("engines_skipped", {})
    if not isinstance(skipped_raw, Mapping):
        raise ValueError("engines_skipped must be a mapping")
    skipped = sorted(str(name) for name in skipped_raw)
    reasons_raw = risk.get("reasons", ())
    if not isinstance(reasons_raw, (list, tuple)):
        raise ValueError("risk reasons must be a list or tuple")
    reasons = [str(reason) for reason in reasons_raw]
    if any(not reason for reason in reasons):
        raise ValueError("risk reasons must be non-empty strings")

    provenance = str(risk.get("provenance", ""))
    if provenance not in _RESEARCH_RISK_PROVENANCE:
        raise ValueError(
            "risk provenance must be REAL_RESEARCH_RISK_SUBSET or "
            "REAL_RESEARCH_RISK_FULL"
        )
    config_provenance = str(risk.get("config_provenance", ""))
    series = str(risk.get("series", ""))
    if not config_provenance:
        raise ValueError("risk config_provenance must be non-empty")
    if not series:
        raise ValueError("risk series must be non-empty")

    return Event(
        event_id=_event_id(run_id, run_repro_hash, decision, timestamp_ms),
        event_type=EVENT_RESEARCH_RISK_EVALUATED,
        event_version="1.0.0",
        timestamp=event_timestamp_from_milliseconds(timestamp_ms),
        source="services.risk.research_risk.dry_run_guard",
        domain="risk",
        criticality="C2" if decision == "RISK_APPROVED" else "C1",
        payload={
            "config_provenance": config_provenance,
            "decision": decision,
            "engines_run": engines_run,
            "engines_skipped": skipped,
            "gates_passed": gates_passed,
            "gates_run": gates_run,
            "provenance": provenance,
            "reasons": reasons,
            "run_id": run_id,
            "run_repro_hash": run_repro_hash,
            "series": series,
            "strategy_id": strategy_id,
        },
    )


__all__ = ["research_risk_evaluated_event"]
