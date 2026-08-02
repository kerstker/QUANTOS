"""Reconcile all L2 Alpha stages into one append-only production workflow.

The tool reads existing immutable/attested research and paper artifacts.  It
does not recompute a verdict and cannot promote lifecycle state, access a
credential, connect to a broker, or submit an order.  Missing evidence remains
an explicit blocker; malformed existing evidence fails the reconciliation.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import time
from typing import Any

from quantos.contracts.hashing import stable_hash
from services.research import alpha_expression as ax
from services.research.alpha_production_workflow import (
    AlphaProductionWorkflow,
    CandidateIdentity,
    EvidenceGate,
    EvidenceStatus,
    GateEvidence,
    MaterialChangeClass,
    ProductionStage,
    derive_stage,
)
from services.research.paper_candidate_registry import PaperCandidateRegistry
from tools.cache_io import atomic_write_json


DEFAULT_DB = Path("data/cache/alpha_production_workflow.db")
DEFAULT_OUTPUT = Path("data/cache/alpha_production_workflow.json")
DEFAULT_CANDIDATE_REGISTRY = Path("data/cache/paper_candidate_registry.db")
DEFAULT_HISTORICAL_EVALUATIONS = Path("evidence/paper_alpha_evaluations.json")
DEFAULT_FORWARD_EVALUATIONS = Path("data/cache/forward_alpha_evaluations.json")
DEFAULT_FORWARD_STATUS = Path("data/cache/forward_track_status.json")
DEFAULT_RISK_BUNDLE = Path("data/cache/paper_research_risk_inputs.json")
DEFAULT_PAPER_REALTIME = Path("data/cache/paper_realtime_status.json")
DEFAULT_LIFECYCLE = Path("evidence/alpha_lifecycle_status.json")
REQUIRED_FORWARD_PERIODS = 8
LEGACY_DIAGNOSTIC_IDS = frozenset(
    {"carry_oi_majors", "crowd_contra", "top_contra"}
)


def _read_optional(path: str | Path) -> dict[str, Any]:
    resolved = Path(path)
    if not resolved.is_file():
        return {}
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"workflow evidence is unreadable: {resolved}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"workflow evidence is not an object: {resolved}")
    return value


def _timestamp_ms(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = int(value)
    if result < 0:
        return None
    if result < 10_000_000_000:
        result *= 1000
    return result


def _document_timestamp(document: Mapping[str, Any]) -> int | None:
    for name in (
        "generated_at_ms",
        "generated_at",
        "ran_at",
        "observed_at",
        "timestamp",
    ):
        result = _timestamp_ms(document.get(name))
        if result is not None:
            return result
    return None


def _artifact_hash(document: Mapping[str, Any], row: object) -> str:
    return stable_hash(
        {
            "schema_version": document.get("schema_version"),
            "dataset_version": document.get("dataset_version"),
            "config_hash": document.get("config_hash"),
            "generated_at": document.get("generated_at"),
            "ran_at": document.get("ran_at"),
            "row": row,
        }
    )


def _gate(
    gate: EvidenceGate,
    status: EvidenceStatus,
    *,
    path: str | Path,
    document: Mapping[str, Any],
    row: object,
) -> GateEvidence:
    present = bool(document)
    return GateEvidence(
        gate=gate,
        status=status,
        reference=str(path).replace("\\", "/") if present else None,
        content_hash=_artifact_hash(document, row) if present else None,
        observed_at_ms=_document_timestamp(document) if present else None,
    )


def _field_ids(expression: object) -> tuple[str, ...]:
    fields: set[str] = set()

    def visit(node: object) -> None:
        if isinstance(node, (tuple, list)):
            if (
                len(node) == 2
                and node[0] == "field"
                and isinstance(node[1], str)
            ):
                fields.add(node[1])
            for child in node[1:]:
                visit(child)

    visit(expression)
    return tuple(sorted(fields)) or ("expression_only",)


def _mechanism(alpha_id: str) -> tuple[str, str | None, tuple[str, ...]]:
    if alpha_id.startswith("carry"):
        return (
            "CARRY",
            "Cross-sectional funding and positioning dispersion should earn a net carry spread.",
            (
                "Held-out net carry spread is non-positive after fees and funding.",
                "The edge fails across the pre-registered stress and regime partitions.",
            ),
        )
    if alpha_id.startswith("crowd"):
        return (
            "CROWDING_REVERSAL",
            "Crowded positioning should mean-revert cross-sectionally after realistic costs.",
            (
                "Held-out net long-short spread is non-positive.",
                "Forward rank IC and risk-adjusted return fail the pre-registered gates.",
            ),
        )
    if alpha_id.startswith("top_contra"):
        return (
            "TOP_TRADER_CROWDING_REVERSAL",
            "Crowded top-trader positioning should mean-revert cross-sectionally.",
            (
                "Held-out net long-short spread is non-positive.",
                "The signal does not survive independent forward evidence.",
            ),
        )
    return (f"UNDECLARED:{alpha_id}", None, ())


def build_candidate_identities(
    *,
    candidate_registry_path: str | Path = DEFAULT_CANDIDATE_REGISTRY,
) -> tuple[CandidateIdentity, ...]:
    """Build the bootstrap population from static preregistration + verified admission."""
    from tools.forward_track_advance import _ALPHAS, _alpha_spec

    registered: dict[str, object] = dict(_ALPHAS)
    dynamic_ids: list[str] = []
    dynamic: dict[str, Any] = {}
    registry_path = Path(candidate_registry_path)
    if registry_path.is_file():
        with PaperCandidateRegistry(registry_path, create=False) as registry:
            if not registry.chain_valid():
                raise RuntimeError("paper candidate registry hash chain is invalid")
            dynamic = {item.alpha_id: item for item in registry.all()}
    for alpha_id, admitted in dynamic.items():
        existing = registered.get(alpha_id)
        if existing is not None:
            if ax.serialize(_alpha_spec(existing)["expr"]) != admitted.expression:
                raise RuntimeError(
                    f"paper candidate identity conflicts with static alpha: {alpha_id}"
                )
            continue
        registered[alpha_id] = {
            "expr": ax.deserialize(admitted.expression),
            "quantile": admitted.quantile,
            "cadence_days": admitted.cadence_days,
        }
        dynamic_ids.append(alpha_id)
    identities: list[CandidateIdentity] = []
    for alpha_id, entry in registered.items():
        spec = _alpha_spec(entry)
        expression_object = spec["expr"]
        expression = ax.serialize(expression_object)
        admitted = dynamic.get(alpha_id)
        family_id, hypothesis, falsification = _mechanism(str(alpha_id))
        source_payload = {
            "candidate_id": str(alpha_id),
            "expression": expression,
            "quantile": float(spec["quantile"]),
            "cadence_days": int(spec["cadence_days"]),
            "source": "tools.forward_track_advance._ALPHAS",
        }
        identities.append(
            CandidateIdentity(
                candidate_id=str(alpha_id),
                expression=expression,
                quantile=float(spec["quantile"]),
                cadence_days=int(spec["cadence_days"]),
                universe="cs_majors",
                family_id=family_id,
                source_kind=(
                    "VERIFIED_FACTORY_PASS"
                    if alpha_id in dynamic_ids
                    else "STATIC_PREREGISTERED"
                ),
                source_ref=(
                    f"experiment:{admitted.evaluation_run_id}"
                    if admitted is not None
                    else "tools.forward_track_advance._ALPHAS"
                ),
                source_definition_hash=(
                    admitted.definition_hash
                    if admitted is not None
                    else stable_hash(source_payload)
                ),
                required_datasets=_field_ids(expression_object),
                economic_hypothesis=hypothesis,
                falsification_criteria=falsification,
                created_at_ms=(admitted.admitted_at_ms if admitted is not None else 0),
                predecessor_id=None,
                change_class=MaterialChangeClass.NEW_CANDIDATE,
            )
        )
    return tuple(identities)


def _mapping_row(document: Mapping[str, Any], candidate_id: str) -> dict[str, Any]:
    rows = document.get("alphas")
    row = rows.get(candidate_id) if isinstance(rows, Mapping) else None
    return dict(row) if isinstance(row, Mapping) else {}


def _historical_gate(
    candidate_id: str,
    *,
    historical_document: Mapping[str, Any],
    historical_path: str | Path,
    forward_document: Mapping[str, Any],
    forward_path: str | Path,
) -> GateEvidence:
    row = _mapping_row(historical_document, candidate_id)
    document = historical_document
    path = historical_path
    if not row:
        row = _mapping_row(forward_document, candidate_id)
        document = forward_document
        path = forward_path
    if not row:
        return _gate(
            EvidenceGate.HISTORICAL_OOS,
            EvidenceStatus.NOT_AVAILABLE,
            path=path,
            document={},
            row={},
        )
    evaluation = row.get("evaluation")
    evaluation = evaluation if isinstance(evaluation, Mapping) else {}
    verified = row.get("status") == "VERIFIED" and evaluation.get("verified") is True
    if verified and evaluation.get("verdict") == "PASS":
        status = EvidenceStatus.PASS
    elif verified:
        status = EvidenceStatus.FAIL
    else:
        status = EvidenceStatus.PENDING
    return _gate(
        EvidenceGate.HISTORICAL_OOS,
        status,
        path=path,
        document=document,
        row=row,
    )


def _shadow_gate(
    candidate_id: str,
    *,
    document: Mapping[str, Any],
    path: str | Path,
) -> tuple[GateEvidence, int | None]:
    row = _mapping_row(document, candidate_id)
    if not row:
        return (
            _gate(
                EvidenceGate.SIGNAL_SHADOW,
                EvidenceStatus.NOT_AVAILABLE,
                path=path,
                document={},
                row={},
            ),
            None,
        )
    periods = row.get("n_forward_periods")
    observed = int(periods) if type(periods) is int and periods >= 0 else None
    if observed is not None and observed >= REQUIRED_FORWARD_PERIODS:
        status = EvidenceStatus.MATURE
    elif row.get("evidence_state") in {"FAILED", "BLOCKED"}:
        status = EvidenceStatus.BLOCKED
    else:
        status = EvidenceStatus.RUNNING
    return (
        _gate(
            EvidenceGate.SIGNAL_SHADOW,
            status,
            path=path,
            document=document,
            row=row,
        ),
        observed,
    )


def _risk_gate(
    candidate_id: str,
    *,
    document: Mapping[str, Any],
    path: str | Path,
) -> GateEvidence:
    row = _mapping_row(document, candidate_id)
    if not row:
        status = EvidenceStatus.NOT_AVAILABLE
    elif row.get("status") == "READY":
        status = EvidenceStatus.READY
    elif row.get("status") in {"FAIL", "FAILED", "BLOCKED"}:
        status = EvidenceStatus.FAIL
    else:
        status = EvidenceStatus.PENDING
    return _gate(
        EvidenceGate.RESEARCH_RISK,
        status,
        path=path,
        document=document if row else {},
        row=row,
    )


def _execution_gate(
    candidate_id: str,
    *,
    document: Mapping[str, Any],
    path: str | Path,
) -> tuple[GateEvidence, bool]:
    row = _mapping_row(document, candidate_id)
    marked = row.get("status") == "PAPER_MARKED"
    legacy = bool(marked and candidate_id in LEGACY_DIAGNOSTIC_IDS)
    if legacy:
        status = EvidenceStatus.LEGACY_DIAGNOSTIC
    elif marked:
        status = EvidenceStatus.PAPER_MARKED
    elif row:
        status = EvidenceStatus.PENDING
    else:
        status = EvidenceStatus.NOT_AVAILABLE
    return (
        _gate(
            EvidenceGate.EXECUTION_PAPER,
            status,
            path=path,
            document=document if row else {},
            row=row,
        ),
        legacy,
    )


def _lifecycle_gate(
    candidate_id: str,
    *,
    document: Mapping[str, Any],
    path: str | Path,
) -> GateEvidence:
    row = _mapping_row(document, candidate_id)
    states = " ".join(str(value) for value in row.values()).upper()
    if "RETIRED" in states:
        status = EvidenceStatus.RETIRED
    elif any(value in states for value in ("FAILED", "DEGRADED", "BLOCKED")):
        status = EvidenceStatus.BLOCKED
    elif row:
        status = EvidenceStatus.RUNNING
    else:
        status = EvidenceStatus.NOT_AVAILABLE
    return _gate(
        EvidenceGate.LIFECYCLE,
        status,
        path=path,
        document=document if row else {},
        row=row,
    )


def reconcile_workflow(
    *,
    workflow: AlphaProductionWorkflow,
    identities: Sequence[CandidateIdentity],
    historical_document: Mapping[str, Any],
    historical_path: str | Path,
    forward_evaluations_document: Mapping[str, Any],
    forward_evaluations_path: str | Path,
    forward_status_document: Mapping[str, Any],
    forward_status_path: str | Path,
    risk_document: Mapping[str, Any],
    risk_path: str | Path,
    realtime_document: Mapping[str, Any],
    realtime_path: str | Path,
    lifecycle_document: Mapping[str, Any],
    lifecycle_path: str | Path,
    release_commit: str | None,
) -> dict[str, Any]:
    inserted_candidates = 0
    inserted_events = 0
    source_documents = (
        historical_document,
        forward_evaluations_document,
        forward_status_document,
        risk_document,
        realtime_document,
        lifecycle_document,
    )
    source_times = [
        timestamp
        for document in source_documents
        if (timestamp := _document_timestamp(document)) is not None
    ]
    as_of_ms = max(source_times, default=0)
    for identity in identities:
        inserted_candidates += int(workflow.register_candidate(identity))
        historical = _historical_gate(
            identity.candidate_id,
            historical_document=historical_document,
            historical_path=historical_path,
            forward_document=forward_evaluations_document,
            forward_path=forward_evaluations_path,
        )
        shadow, periods = _shadow_gate(
            identity.candidate_id,
            document=forward_status_document,
            path=forward_status_path,
        )
        risk = _risk_gate(
            identity.candidate_id,
            document=risk_document,
            path=risk_path,
        )
        execution, legacy = _execution_gate(
            identity.candidate_id,
            document=realtime_document,
            path=realtime_path,
        )
        lifecycle = _lifecycle_gate(
            identity.candidate_id,
            document=lifecycle_document,
            path=lifecycle_path,
        )
        blockers: list[str] = []
        if identity.economic_hypothesis is None:
            blockers.append("ECONOMIC_HYPOTHESIS_MISSING")
        if not identity.falsification_criteria:
            blockers.append("FALSIFICATION_CRITERIA_MISSING")
        if historical.status is not EvidenceStatus.PASS:
            blockers.append("HISTORICAL_OOS_NOT_PASS")
        if shadow.status is not EvidenceStatus.MATURE:
            blockers.append("SIGNAL_SHADOW_NOT_MATURE")
        if risk.status is not EvidenceStatus.READY:
            blockers.append("RESEARCH_RISK_NOT_READY")
        if release_commit is None:
            blockers.append("RELEASE_IDENTITY_MISSING")
        if legacy:
            blockers.append("LEGACY_DIAGNOSTIC_NOT_DEPLOYMENT_ELIGIBLE")
        previous = workflow.latest_bundle(identity.candidate_id)
        bundle = derive_stage(
            gates=(historical, shadow, risk, execution, lifecycle),
            identity_hash=identity.identity_hash,
            candidate_id=identity.candidate_id,
            observed_forward_periods=periods,
            required_forward_periods=REQUIRED_FORWARD_PERIODS,
            blockers=tuple(sorted(set(blockers))),
            release_commit=release_commit,
            legacy_diagnostic=legacy,
            as_of_ms=as_of_ms,
            previous_stage=previous.stage if previous is not None else None,
        )
        inserted_events += int(workflow.record_evidence(bundle))
    snapshot = workflow.snapshot()
    snapshot.update(
        {
            "generated_at_ms": int(time.time_ns() // 1_000_000),
            "reconciliation": {
                "inserted_candidates": inserted_candidates,
                "inserted_events": inserted_events,
                "source_as_of_ms": as_of_ms,
                "release_commit": release_commit,
            },
            "source_hashes": {
                "historical_evaluations": stable_hash(historical_document),
                "forward_evaluations": stable_hash(forward_evaluations_document),
                "forward_status": stable_hash(forward_status_document),
                "research_risk": stable_hash(risk_document),
                "paper_realtime": stable_hash(realtime_document),
                "lifecycle": stable_hash(lifecycle_document),
            },
        }
    )
    return snapshot


def run(
    *,
    db_path: str | Path = DEFAULT_DB,
    output_path: str | Path = DEFAULT_OUTPUT,
    candidate_registry_path: str | Path = DEFAULT_CANDIDATE_REGISTRY,
    historical_evaluations_path: str | Path = DEFAULT_HISTORICAL_EVALUATIONS,
    forward_evaluations_path: str | Path = DEFAULT_FORWARD_EVALUATIONS,
    forward_status_path: str | Path = DEFAULT_FORWARD_STATUS,
    risk_path: str | Path = DEFAULT_RISK_BUNDLE,
    realtime_path: str | Path = DEFAULT_PAPER_REALTIME,
    lifecycle_path: str | Path = DEFAULT_LIFECYCLE,
    release_commit: str | None = None,
) -> dict[str, Any]:
    commit = release_commit or os.environ.get("QUANTOS_RELEASE_COMMIT")
    if commit is not None:
        commit = commit.strip().lower()
        if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
            raise RuntimeError("QUANTOS_RELEASE_COMMIT is invalid")
    identities = build_candidate_identities(
        candidate_registry_path=candidate_registry_path
    )
    with AlphaProductionWorkflow(db_path) as workflow:
        snapshot = reconcile_workflow(
            workflow=workflow,
            identities=identities,
            historical_document=_read_optional(historical_evaluations_path),
            historical_path=historical_evaluations_path,
            forward_evaluations_document=_read_optional(forward_evaluations_path),
            forward_evaluations_path=forward_evaluations_path,
            forward_status_document=_read_optional(forward_status_path),
            forward_status_path=forward_status_path,
            risk_document=_read_optional(risk_path),
            risk_path=risk_path,
            realtime_document=_read_optional(realtime_path),
            realtime_path=realtime_path,
            lifecycle_document=_read_optional(lifecycle_path),
            lifecycle_path=lifecycle_path,
            release_commit=commit,
        )
    atomic_write_json(str(output_path), snapshot, indent=2)
    return snapshot


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--candidate-registry", default=str(DEFAULT_CANDIDATE_REGISTRY))
    parser.add_argument(
        "--historical-evaluations", default=str(DEFAULT_HISTORICAL_EVALUATIONS)
    )
    parser.add_argument("--forward-evaluations", default=str(DEFAULT_FORWARD_EVALUATIONS))
    parser.add_argument("--forward-status", default=str(DEFAULT_FORWARD_STATUS))
    parser.add_argument("--risk-bundle", default=str(DEFAULT_RISK_BUNDLE))
    parser.add_argument("--paper-realtime", default=str(DEFAULT_PAPER_REALTIME))
    parser.add_argument("--lifecycle", default=str(DEFAULT_LIFECYCLE))
    parser.add_argument("--release-commit", default=None)
    args = parser.parse_args(argv)
    try:
        snapshot = run(
            db_path=args.db,
            output_path=args.output,
            candidate_registry_path=args.candidate_registry,
            historical_evaluations_path=args.historical_evaluations,
            forward_evaluations_path=args.forward_evaluations,
            forward_status_path=args.forward_status,
            risk_path=args.risk_bundle,
            realtime_path=args.paper_realtime,
            lifecycle_path=args.lifecycle,
            release_commit=args.release_commit,
        )
    except (OSError, RuntimeError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"alpha production workflow blocked: {exc}")
        return 2
    print(
        "alpha production workflow: "
        f"status={snapshot['status']} population={snapshot['population_count']} "
        f"eligible={len(snapshot['execution_paper_eligible_ids'])} "
        f"execution={len(snapshot['execution_paper_ids'])}"
    )
    return 0 if snapshot["status"] == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_DB",
    "DEFAULT_OUTPUT",
    "LEGACY_DIAGNOSTIC_IDS",
    "REQUIRED_FORWARD_PERIODS",
    "build_candidate_identities",
    "reconcile_workflow",
    "run",
]
