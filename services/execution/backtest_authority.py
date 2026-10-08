"""Fail-closed authority boundary for backtest evidence.

The historical ``run_multi_period_dry_run`` records predate a complete input
manifest and use non-causal/spot-approximation semantics.  They remain useful
as immutable diagnostics, but they are not eligible for selection, promotion,
paper readiness, or live-readiness decisions.

``RunManifestV2`` is deliberately separate from the legacy store schema.  Its
identity binds every selection-bearing input and the causal decision/execution
timeline.  A new engine may only claim authority when the manifest and all
load-bearing semantic attestations are present and self-consistent.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Keep the original import surface and exact object identity for existing
# producers while allowing research verifiers to use the neutral contract.
from quantos.contracts.backtest_identity import (
    RUN_MANIFEST_SCHEMA_VERSION,
    CANONICAL_ENGINE_SEMANTICS,
    RunManifestV2,
    build_run_manifest_v2,
    exact_identity_hash,
    _canonical_identity,
    _authority_hash,
    _digest,
    _event_time,
)

from services.validation.research_risk_authority import (
    full_research_risk_approved,
)

AUTHORITATIVE_EVIDENCE_STATUS = "AUTHORITATIVE"
LEGACY_INVALIDATION_CODE = "INVALIDATED_PERP_LEDGER_V1"

# A self-described JSON document is not an authority boundary.  The deployed
# receipt-authority registry intentionally has no active keys, so no record may
# be admitted until a separately controlled signer and pinned verification path
# bind both the manifest and immutable result hash.  This flag must not be
# enabled as a configuration toggle; replacing it requires a reviewed verifier
# integration and adversarial tests.
_EXTERNAL_SIGNED_AUTHORITY_VERIFIER_INTEGRATED = False

_REQUIRED_ATTESTATIONS = frozenset(
    {
        "causal_available_time",
        "strict_next_event_execution",
        "decimal_contracts",
        "settlement_event_funding",
        "historical_venue_rules",
        "margin_liquidation_state_machine",
        "complete_cashflow_identity",
        "deployment_equivalent_risk",
        "capacity_is_load_bearing",
        "full_research_risk_9_of_9",
        "risk_history_ledger_replayed",
    }
)
_REQUIRED_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "reconciliation",
        "ending_state",
        "ledger_events",
        "liquidations",
        "decision_contexts",
        "research_risk_results",
        "execution_results",
    }
)


class BacktestIntegrityBlocked(RuntimeError):
    """Raised when a quarantined historical engine is invoked as authority."""


def is_authoritative_backtest_record(record: Mapping[str, Any]) -> bool:
    """Fail closed until an external signed authority verifier is integrated.

    Internal hashes below are valuable tamper evidence, but they cannot prove
    who produced a record.  In particular, a caller that can rewrite the JSON
    can recompute every unkeyed digest and assert every boolean attestation.
    Therefore the public admission predicate remains unconditionally closed
    while the governed receipt-authority registry has no independent trust
    root.  The structural checks are retained as defense in depth for the
    future signed-verifier integration.
    """

    if not _EXTERNAL_SIGNED_AUTHORITY_VERIFIER_INTEGRATED:
        return False

    try:
        if not isinstance(record, Mapping):
            return False
        detail = record.get("detail")
        if not isinstance(detail, Mapping):
            return False
        manifest = RunManifestV2.from_dict(detail.get("run_manifest_v2"))
        if detail.get("manifest_hash") != manifest.manifest_hash:
            return False
        if record.get("run_id") != manifest.run_id:
            return False
        if detail.get("engine_semantics") != CANONICAL_ENGINE_SEMANTICS:
            return False
        if detail.get("evidence_status") != AUTHORITATIVE_EVIDENCE_STATUS:
            return False
        attestations = detail.get("engine_attestations")
        if not isinstance(attestations, Mapping):
            return False
        if not all(attestations.get(name) is True for name in _REQUIRED_ATTESTATIONS):
            return False

        result_payload = detail.get("result_hash_payload")
        if not isinstance(result_payload, Mapping):
            return False
        if set(result_payload) != _REQUIRED_RESULT_FIELDS:
            return False
        if result_payload.get("schema_version") != 1:
            return False
        if result_payload.get("run_id") != manifest.run_id:
            return False
        if detail.get("result_hash") != exact_identity_hash(
            result_payload, name="authoritative_backtest_result"
        ):
            return False

        reconciliation = result_payload.get("reconciliation")
        ending_state = result_payload.get("ending_state")
        if not isinstance(reconciliation, Mapping) or not isinstance(ending_state, Mapping):
            return False
        if reconciliation.get("residual") != "0":
            return False
        if ending_state.get("cashflow_identity") != reconciliation:
            return False

        ledger_events = result_payload.get("ledger_events")
        decision_contexts = result_payload.get("decision_contexts")
        risk_history = result_payload.get("research_risk_results")
        execution_results = result_payload.get("execution_results")
        if not all(
            isinstance(value, (list, tuple)) and bool(value)
            for value in (
                ledger_events,
                decision_contexts,
                risk_history,
                execution_results,
            )
        ):
            return False

        decision_ids = {
            item.get("decision_id")
            for item in decision_contexts
            if isinstance(item, Mapping)
        }
        risk_ids = {
            item.get("decision_id")
            for item in risk_history
            if isinstance(item, Mapping)
        }
        if (
            len(decision_ids) != len(decision_contexts)
            or len(risk_ids) != len(risk_history)
            or decision_ids != risk_ids
            or None in decision_ids
        ):
            return False
        for item in risk_history:
            if not isinstance(item, Mapping):
                return False
            risk = item.get("result")
            if not isinstance(risk, Mapping) or not full_research_risk_approved(risk):
                return False
        if detail.get("research_risk") != risk_history[-1].get("result"):
            return False
        if detail.get("research_risk_history") != list(risk_history):
            return False
        return True
    except (TypeError, ValueError):
        return False


def quarantine_legacy_backtest(component: str) -> None:
    """Unconditionally stop a legacy engine from producing authoritative evidence."""

    name = str(component).strip() or "unknown"
    raise BacktestIntegrityBlocked(
        f"{LEGACY_INVALIDATION_CODE}: {name} uses legacy timing/perpetual-ledger semantics; "
        "raw collection and read-only forward observation may continue, but historical "
        "selection, reuse, persistence, and promotion are blocked"
    )


__all__ = [
    "AUTHORITATIVE_EVIDENCE_STATUS",
    "BacktestIntegrityBlocked",
    "CANONICAL_ENGINE_SEMANTICS",
    "LEGACY_INVALIDATION_CODE",
    "RUN_MANIFEST_SCHEMA_VERSION",
    "RunManifestV2",
    "build_run_manifest_v2",
    "exact_identity_hash",
    "is_authoritative_backtest_record",
    "quarantine_legacy_backtest",
]
