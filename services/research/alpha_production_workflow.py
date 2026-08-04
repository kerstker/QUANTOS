"""Single append-only authority for the L2 Alpha production funnel.

The workflow deliberately stops at execution paper.  It owns candidate identity,
material-change lineage, and the evidence-backed progression from a frozen
candidate through parallel historical/forward validation to simulated execution.
It has no broker, credential, mandate, lifecycle-promotion, or live-order seam.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping

from quantos.contracts.hashing import stable_hash
from services.research import alpha_expression as ax


_SHA256 = re.compile(r"[0-9a-f]{64}")
_RELEASE_COMMIT = re.compile(r"[0-9a-f]{40}")
_CANDIDATE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}")


class AlphaProductionWorkflowError(ValueError):
    """A production-workflow invariant was violated."""


class ProductionStage(str, Enum):
    FROZEN = "FROZEN"
    PARALLEL_VALIDATION = "PARALLEL_VALIDATION"
    EXECUTION_PAPER_ELIGIBLE = "EXECUTION_PAPER_ELIGIBLE"
    EXECUTION_PAPER = "EXECUTION_PAPER"
    RETIRED = "RETIRED"


class MaterialChangeClass(str, Enum):
    NEW_CANDIDATE = "NEW_CANDIDATE"
    NO_CHANGE = "NO_CHANGE"
    NON_MATERIAL_EVIDENCE = "NON_MATERIAL_EVIDENCE"
    MATERIAL_SIGNAL = "MATERIAL_SIGNAL"
    MATERIAL_DATA = "MATERIAL_DATA"
    MATERIAL_GOVERNANCE = "MATERIAL_GOVERNANCE"
    MATERIAL_MULTIPLE = "MATERIAL_MULTIPLE"


class CandidateDefinitionKind(str, Enum):
    """Canonical executable-definition shape bound to a candidate identity."""

    ALPHA_EXPRESSION = "ALPHA_EXPRESSION"
    REALTIME_MECHANISM = "REALTIME_MECHANISM"


class EvidenceGate(str, Enum):
    HISTORICAL_OOS = "HISTORICAL_OOS"
    SIGNAL_SHADOW = "SIGNAL_SHADOW"
    RESEARCH_RISK = "RESEARCH_RISK"
    EXECUTION_PAPER = "EXECUTION_PAPER"
    LIFECYCLE = "LIFECYCLE"


class EvidenceStatus(str, Enum):
    NOT_AVAILABLE = "NOT_AVAILABLE"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    READY = "READY"
    MATURE = "MATURE"
    PAPER_MARKED = "PAPER_MARKED"
    LEGACY_DIAGNOSTIC = "LEGACY_DIAGNOSTIC"
    RETIRED = "RETIRED"


_STAGE_RANK = {
    ProductionStage.FROZEN: 0,
    ProductionStage.PARALLEL_VALIDATION: 1,
    ProductionStage.EXECUTION_PAPER_ELIGIBLE: 2,
    ProductionStage.EXECUTION_PAPER: 3,
    ProductionStage.RETIRED: 4,
}


@dataclass(frozen=True)
class CandidateIdentity:
    candidate_id: str
    expression: str
    quantile: float
    cadence_days: int
    universe: str
    family_id: str
    source_kind: str
    source_ref: str
    source_definition_hash: str
    required_datasets: tuple[str, ...]
    economic_hypothesis: str | None
    falsification_criteria: tuple[str, ...]
    created_at_ms: int
    predecessor_id: str | None = None
    change_class: MaterialChangeClass = MaterialChangeClass.NEW_CANDIDATE
    definition_kind: CandidateDefinitionKind = CandidateDefinitionKind.ALPHA_EXPRESSION
    signal_spec_json: str | None = None
    decision_interval_ms: int | None = None
    label_horizon_ms: int | None = None

    @property
    def definition_hash(self) -> str:
        if self.definition_kind is CandidateDefinitionKind.ALPHA_EXPRESSION:
            # Preserve the pre-handoff digest byte-for-byte.  Existing frozen
            # daily candidates must not reset their evidence clock merely
            # because the authority learned a second definition shape.
            return stable_hash(
                {
                    "expression": self.expression,
                    "quantile": float(self.quantile),
                    "cadence_days": self.cadence_days,
                    "universe": self.universe,
                    "required_datasets": self.required_datasets,
                }
            )
        return stable_hash(
            {
                "definition_kind": self.definition_kind.value,
                "signal_spec_json": self.signal_spec_json,
                "quantile": float(self.quantile),
                "decision_interval_ms": self.decision_interval_ms,
                "label_horizon_ms": self.label_horizon_ms,
                "universe": self.universe,
                "required_datasets": self.required_datasets,
            }
        )

    @property
    def identity_hash(self) -> str:
        return stable_hash(_identity_payload(self))


@dataclass(frozen=True)
class GateEvidence:
    gate: EvidenceGate
    status: EvidenceStatus
    reference: str | None = None
    content_hash: str | None = None
    observed_at_ms: int | None = None


@dataclass(frozen=True)
class CandidateEvidenceBundle:
    candidate_id: str
    identity_hash: str
    stage: ProductionStage
    gates: tuple[GateEvidence, ...]
    observed_forward_periods: int | None
    required_forward_periods: int
    blockers: tuple[str, ...]
    release_commit: str | None
    legacy_diagnostic: bool
    as_of_ms: int

    def gate(self, name: EvidenceGate) -> GateEvidence:
        for item in self.gates:
            if item.gate is name:
                return item
        raise AlphaProductionWorkflowError(f"missing evidence gate: {name.value}")

    @property
    def eligible_for_execution_paper(self) -> bool:
        return bool(
            not self.legacy_diagnostic
            and not self.blockers
            and self.release_commit is not None
            and _RELEASE_COMMIT.fullmatch(self.release_commit)
            and self.gate(EvidenceGate.HISTORICAL_OOS).status is EvidenceStatus.PASS
            and self.gate(EvidenceGate.SIGNAL_SHADOW).status is EvidenceStatus.MATURE
            and self.gate(EvidenceGate.RESEARCH_RISK).status is EvidenceStatus.READY
        )

    @property
    def bundle_hash(self) -> str:
        return stable_hash(_bundle_payload(self, include_derived=False))


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AlphaProductionWorkflowError(f"{name} is required")
    return value


def _require_sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value.lower()) is None:
        raise AlphaProductionWorkflowError(f"{name} must be a SHA-256 digest")
    return value.lower()


def _identity_payload(identity: CandidateIdentity) -> dict[str, Any]:
    payload = {
        "candidate_id": identity.candidate_id,
        "expression": identity.expression,
        "quantile": float(identity.quantile),
        "cadence_days": identity.cadence_days,
        "universe": identity.universe,
        "family_id": identity.family_id,
        "source_kind": identity.source_kind,
        "source_ref": identity.source_ref,
        "source_definition_hash": identity.source_definition_hash,
        "required_datasets": list(identity.required_datasets),
        "economic_hypothesis": identity.economic_hypothesis,
        "falsification_criteria": list(identity.falsification_criteria),
        "created_at_ms": identity.created_at_ms,
        "predecessor_id": identity.predecessor_id,
        "change_class": identity.change_class.value,
    }
    if identity.definition_kind is not CandidateDefinitionKind.ALPHA_EXPRESSION:
        payload.update(
            {
                "definition_kind": identity.definition_kind.value,
                "signal_spec_json": identity.signal_spec_json,
                "decision_interval_ms": identity.decision_interval_ms,
                "label_horizon_ms": identity.label_horizon_ms,
            }
        )
    return payload


def _validated_identity(identity: CandidateIdentity) -> CandidateIdentity:
    if not isinstance(identity, CandidateIdentity):
        raise TypeError("identity must be a CandidateIdentity")
    if _CANDIDATE_ID.fullmatch(identity.candidate_id) is None:
        raise AlphaProductionWorkflowError("candidate_id is invalid")
    if not isinstance(identity.definition_kind, CandidateDefinitionKind):
        raise AlphaProductionWorkflowError("definition_kind is invalid")
    if identity.definition_kind is CandidateDefinitionKind.ALPHA_EXPRESSION:
        try:
            canonical = ax.serialize(ax.deserialize(identity.expression))
        except (TypeError, ValueError) as exc:
            raise AlphaProductionWorkflowError("candidate expression is invalid") from exc
        if canonical != identity.expression:
            raise AlphaProductionWorkflowError("candidate expression is not canonical")
        if any(
            value is not None
            for value in (
                identity.signal_spec_json,
                identity.decision_interval_ms,
                identity.label_horizon_ms,
            )
        ):
            raise AlphaProductionWorkflowError(
                "expression candidate cannot carry a realtime signal spec"
            )
    else:
        if identity.expression != "":
            raise AlphaProductionWorkflowError(
                "realtime mechanism expression must be empty"
            )
        if not isinstance(identity.signal_spec_json, str):
            raise AlphaProductionWorkflowError("signal_spec_json is required")
        try:
            signal_spec = json.loads(identity.signal_spec_json)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AlphaProductionWorkflowError("signal_spec_json is invalid") from exc
        if not isinstance(signal_spec, dict):
            raise AlphaProductionWorkflowError("signal_spec_json must be an object")
        canonical_signal_spec = json.dumps(
            signal_spec,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        if canonical_signal_spec != identity.signal_spec_json:
            raise AlphaProductionWorkflowError("signal_spec_json is not canonical")
        for name in ("decision_interval_ms", "label_horizon_ms"):
            value = getattr(identity, name)
            if type(value) is not int or value <= 0:
                raise AlphaProductionWorkflowError(f"{name} is invalid")
    if (
        isinstance(identity.quantile, bool)
        or not isinstance(identity.quantile, (int, float))
        or not math.isfinite(float(identity.quantile))
        or not 0.0 < float(identity.quantile) <= 0.5
    ):
        raise AlphaProductionWorkflowError("candidate quantile is invalid")
    if identity.definition_kind is CandidateDefinitionKind.ALPHA_EXPRESSION:
        if type(identity.cadence_days) is not int or not 1 <= identity.cadence_days <= 30:
            raise AlphaProductionWorkflowError("candidate cadence_days is invalid")
    elif identity.cadence_days != 0:
        raise AlphaProductionWorkflowError(
            "realtime mechanism cadence_days must be zero"
        )
    for name in ("universe", "family_id", "source_kind", "source_ref"):
        _require_text(getattr(identity, name), name)
    _require_sha256(identity.source_definition_hash, "source_definition_hash")
    if identity.required_datasets != tuple(sorted(set(identity.required_datasets))):
        raise AlphaProductionWorkflowError(
            "required_datasets must be a sorted unique tuple"
        )
    for dataset in identity.required_datasets:
        _require_text(dataset, "required_dataset")
    if identity.economic_hypothesis is not None:
        _require_text(identity.economic_hypothesis, "economic_hypothesis")
    if identity.falsification_criteria != tuple(identity.falsification_criteria):
        raise AlphaProductionWorkflowError("falsification_criteria must be a tuple")
    for criterion in identity.falsification_criteria:
        _require_text(criterion, "falsification criterion")
    if type(identity.created_at_ms) is not int or identity.created_at_ms < 0:
        raise AlphaProductionWorkflowError("created_at_ms is invalid")
    if identity.predecessor_id is not None:
        if (
            _CANDIDATE_ID.fullmatch(identity.predecessor_id) is None
            or identity.predecessor_id == identity.candidate_id
        ):
            raise AlphaProductionWorkflowError("predecessor_id is invalid")
    if not isinstance(identity.change_class, MaterialChangeClass):
        raise AlphaProductionWorkflowError("change_class is invalid")
    if identity.predecessor_id is None:
        if identity.change_class is not MaterialChangeClass.NEW_CANDIDATE:
            raise AlphaProductionWorkflowError(
                "a root candidate must use NEW_CANDIDATE change_class"
            )
    elif identity.change_class in {
        MaterialChangeClass.NEW_CANDIDATE,
        MaterialChangeClass.NO_CHANGE,
        MaterialChangeClass.NON_MATERIAL_EVIDENCE,
    }:
        raise AlphaProductionWorkflowError(
            "a successor must declare a material change_class"
        )
    return identity


def classify_material_change(
    before: CandidateIdentity,
    after: CandidateIdentity,
) -> MaterialChangeClass:
    """Classify immutable candidate-definition changes without guessing intent."""
    previous = _validated_identity(before)
    current = _validated_identity(after)
    if _identity_payload(previous) == _identity_payload(current):
        return MaterialChangeClass.NO_CHANGE

    changed_classes: set[MaterialChangeClass] = set()
    if any(
        getattr(previous, field) != getattr(current, field)
        for field in (
            "expression",
            "quantile",
            "cadence_days",
            "definition_kind",
            "signal_spec_json",
            "decision_interval_ms",
            "label_horizon_ms",
        )
    ):
        changed_classes.add(MaterialChangeClass.MATERIAL_SIGNAL)
    if any(
        getattr(previous, field) != getattr(current, field)
        for field in ("universe", "required_datasets")
    ):
        changed_classes.add(MaterialChangeClass.MATERIAL_DATA)
    if any(
        getattr(previous, field) != getattr(current, field)
        for field in ("family_id", "economic_hypothesis", "falsification_criteria")
    ):
        changed_classes.add(MaterialChangeClass.MATERIAL_GOVERNANCE)
    if len(changed_classes) > 1:
        return MaterialChangeClass.MATERIAL_MULTIPLE
    if changed_classes:
        return next(iter(changed_classes))
    return MaterialChangeClass.NON_MATERIAL_EVIDENCE


def _gate_payload(item: GateEvidence) -> dict[str, Any]:
    return {
        "gate": item.gate.value,
        "status": item.status.value,
        "reference": item.reference,
        "content_hash": item.content_hash,
        "observed_at_ms": item.observed_at_ms,
    }


def _validated_gate(item: GateEvidence) -> GateEvidence:
    if not isinstance(item, GateEvidence):
        raise TypeError("gate evidence must be GateEvidence")
    if not isinstance(item.gate, EvidenceGate) or not isinstance(
        item.status, EvidenceStatus
    ):
        raise AlphaProductionWorkflowError("gate or evidence status is invalid")
    if item.reference is not None:
        _require_text(item.reference, "evidence reference")
    if item.content_hash is not None:
        _require_sha256(item.content_hash, "evidence content_hash")
        if item.reference is None:
            raise AlphaProductionWorkflowError(
                "hashed evidence must include a reference"
            )
    if item.observed_at_ms is not None and (
        type(item.observed_at_ms) is not int or item.observed_at_ms < 0
    ):
        raise AlphaProductionWorkflowError("evidence observed_at_ms is invalid")
    return item


def _bundle_payload(
    bundle: CandidateEvidenceBundle,
    *,
    include_derived: bool,
) -> dict[str, Any]:
    payload = {
        "candidate_id": bundle.candidate_id,
        "identity_hash": bundle.identity_hash,
        "stage": bundle.stage.value,
        "gates": [_gate_payload(item) for item in bundle.gates],
        "observed_forward_periods": bundle.observed_forward_periods,
        "required_forward_periods": bundle.required_forward_periods,
        "blockers": list(bundle.blockers),
        "release_commit": bundle.release_commit,
        "legacy_diagnostic": bundle.legacy_diagnostic,
        "as_of_ms": bundle.as_of_ms,
    }
    if include_derived:
        payload["eligible_for_execution_paper"] = (
            bundle.eligible_for_execution_paper
        )
        payload["bundle_hash"] = bundle.bundle_hash
    return payload


def _validated_bundle(bundle: CandidateEvidenceBundle) -> CandidateEvidenceBundle:
    if not isinstance(bundle, CandidateEvidenceBundle):
        raise TypeError("bundle must be a CandidateEvidenceBundle")
    if _CANDIDATE_ID.fullmatch(bundle.candidate_id) is None:
        raise AlphaProductionWorkflowError("bundle candidate_id is invalid")
    _require_sha256(bundle.identity_hash, "bundle identity_hash")
    if not isinstance(bundle.stage, ProductionStage):
        raise AlphaProductionWorkflowError("bundle stage is invalid")
    for item in bundle.gates:
        _validated_gate(item)
    expected_gates = tuple(EvidenceGate)
    actual_gates = tuple(item.gate for item in bundle.gates)
    if actual_gates != expected_gates:
        raise AlphaProductionWorkflowError(
            "bundle gates must contain each canonical gate in canonical order"
        )
    if bundle.observed_forward_periods is not None and (
        type(bundle.observed_forward_periods) is not int
        or bundle.observed_forward_periods < 0
    ):
        raise AlphaProductionWorkflowError("observed_forward_periods is invalid")
    if (
        type(bundle.required_forward_periods) is not int
        or bundle.required_forward_periods < 1
    ):
        raise AlphaProductionWorkflowError("required_forward_periods is invalid")
    if bundle.blockers != tuple(sorted(set(bundle.blockers))):
        raise AlphaProductionWorkflowError("blockers must be a sorted unique tuple")
    for blocker in bundle.blockers:
        _require_text(blocker, "blocker")
    if bundle.release_commit is not None and _RELEASE_COMMIT.fullmatch(
        bundle.release_commit
    ) is None:
        raise AlphaProductionWorkflowError("release_commit is invalid")
    if type(bundle.legacy_diagnostic) is not bool:
        raise AlphaProductionWorkflowError("legacy_diagnostic is invalid")
    if type(bundle.as_of_ms) is not int or bundle.as_of_ms < 0:
        raise AlphaProductionWorkflowError("as_of_ms is invalid")
    if (
        bundle.stage is ProductionStage.RETIRED
        and bundle.gate(EvidenceGate.LIFECYCLE).status is not EvidenceStatus.RETIRED
    ):
        raise AlphaProductionWorkflowError("retired stage requires lifecycle evidence")
    return bundle


def derive_stage(
    *,
    gates: tuple[GateEvidence, ...],
    identity_hash: str,
    candidate_id: str,
    observed_forward_periods: int | None,
    required_forward_periods: int,
    blockers: tuple[str, ...],
    release_commit: str | None,
    legacy_diagnostic: bool,
    as_of_ms: int,
    previous_stage: ProductionStage | None = None,
) -> CandidateEvidenceBundle:
    """Derive a monotonic stage while keeping current gate regressions visible."""
    lifecycle = next(item for item in gates if item.gate is EvidenceGate.LIFECYCLE)
    probe = CandidateEvidenceBundle(
        candidate_id=candidate_id,
        identity_hash=identity_hash,
        stage=ProductionStage.PARALLEL_VALIDATION,
        gates=gates,
        observed_forward_periods=observed_forward_periods,
        required_forward_periods=required_forward_periods,
        blockers=blockers,
        release_commit=release_commit,
        legacy_diagnostic=legacy_diagnostic,
        as_of_ms=as_of_ms,
    )
    if lifecycle.status is EvidenceStatus.RETIRED:
        stage = ProductionStage.RETIRED
    elif probe.eligible_for_execution_paper and probe.gate(
        EvidenceGate.EXECUTION_PAPER
    ).status is EvidenceStatus.PAPER_MARKED:
        stage = ProductionStage.EXECUTION_PAPER
    elif probe.eligible_for_execution_paper:
        stage = ProductionStage.EXECUTION_PAPER_ELIGIBLE
    elif all(item.status is EvidenceStatus.NOT_AVAILABLE for item in gates):
        stage = ProductionStage.FROZEN
    else:
        stage = ProductionStage.PARALLEL_VALIDATION
    if previous_stage is not None and _STAGE_RANK[previous_stage] > _STAGE_RANK[stage]:
        stage = previous_stage
    return _validated_bundle(
        CandidateEvidenceBundle(
            candidate_id=candidate_id,
            identity_hash=identity_hash,
            stage=stage,
            gates=gates,
            observed_forward_periods=observed_forward_periods,
            required_forward_periods=required_forward_periods,
            blockers=blockers,
            release_commit=release_commit,
            legacy_diagnostic=legacy_diagnostic,
            as_of_ms=as_of_ms,
        )
    )


def _identity_from_payload(payload: Mapping[str, Any]) -> CandidateIdentity:
    return _validated_identity(
        CandidateIdentity(
            candidate_id=str(payload["candidate_id"]),
            expression=str(payload["expression"]),
            quantile=float(payload["quantile"]),
            cadence_days=int(payload["cadence_days"]),
            universe=str(payload["universe"]),
            family_id=str(payload["family_id"]),
            source_kind=str(payload["source_kind"]),
            source_ref=str(payload["source_ref"]),
            source_definition_hash=str(payload["source_definition_hash"]),
            required_datasets=tuple(payload.get("required_datasets") or ()),
            economic_hypothesis=payload.get("economic_hypothesis"),
            falsification_criteria=tuple(payload.get("falsification_criteria") or ()),
            created_at_ms=int(payload["created_at_ms"]),
            predecessor_id=payload.get("predecessor_id"),
            change_class=MaterialChangeClass(str(payload["change_class"])),
            definition_kind=CandidateDefinitionKind(
                str(
                    payload.get(
                        "definition_kind",
                        CandidateDefinitionKind.ALPHA_EXPRESSION.value,
                    )
                )
            ),
            signal_spec_json=payload.get("signal_spec_json"),
            decision_interval_ms=payload.get("decision_interval_ms"),
            label_horizon_ms=payload.get("label_horizon_ms"),
        )
    )


def _bundle_from_payload(payload: Mapping[str, Any]) -> CandidateEvidenceBundle:
    gates = tuple(
        GateEvidence(
            gate=EvidenceGate(str(item["gate"])),
            status=EvidenceStatus(str(item["status"])),
            reference=item.get("reference"),
            content_hash=item.get("content_hash"),
            observed_at_ms=item.get("observed_at_ms"),
        )
        for item in payload.get("gates", ())
        if isinstance(item, Mapping)
    )
    bundle = _validated_bundle(
        CandidateEvidenceBundle(
            candidate_id=str(payload["candidate_id"]),
            identity_hash=str(payload["identity_hash"]),
            stage=ProductionStage(str(payload["stage"])),
            gates=gates,
            observed_forward_periods=payload.get("observed_forward_periods"),
            required_forward_periods=int(payload["required_forward_periods"]),
            blockers=tuple(payload.get("blockers") or ()),
            release_commit=payload.get("release_commit"),
            legacy_diagnostic=bool(payload.get("legacy_diagnostic", False)),
            as_of_ms=int(payload["as_of_ms"]),
        )
    )
    if "bundle_hash" in payload and payload["bundle_hash"] != bundle.bundle_hash:
        raise AlphaProductionWorkflowError("bundle hash is invalid")
    if (
        "eligible_for_execution_paper" in payload
        and bool(payload["eligible_for_execution_paper"])
        != bundle.eligible_for_execution_paper
    ):
        raise AlphaProductionWorkflowError("bundle eligibility is inconsistent")
    return bundle


class AlphaProductionWorkflow:
    """Append-only population registry plus hash-chained workflow evidence."""

    def __init__(self, path: str | Path, *, create: bool = True) -> None:
        resolved = Path(path)
        if create:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(str(resolved))
        else:
            self._connection = sqlite3.connect(
                f"file:{resolved.resolve()}?mode=ro", uri=True
            )
        self._connection.row_factory = sqlite3.Row
        if create:
            self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS alpha_population (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id TEXT NOT NULL UNIQUE,
                identity_json TEXT NOT NULL,
                identity_hash TEXT NOT NULL UNIQUE,
                definition_hash TEXT NOT NULL,
                predecessor_id TEXT,
                change_class TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                record_hash TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS alpha_workflow_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                candidate_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                bundle_json TEXT NOT NULL,
                bundle_hash TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                record_hash TEXT NOT NULL UNIQUE,
                FOREIGN KEY(candidate_id) REFERENCES alpha_population(candidate_id)
            );
            CREATE TRIGGER IF NOT EXISTS alpha_population_no_update
            BEFORE UPDATE ON alpha_population BEGIN
                SELECT RAISE(ABORT, 'alpha population registry is append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS alpha_population_no_delete
            BEFORE DELETE ON alpha_population BEGIN
                SELECT RAISE(ABORT, 'alpha population registry is append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS alpha_workflow_events_no_update
            BEFORE UPDATE ON alpha_workflow_events BEGIN
                SELECT RAISE(ABORT, 'alpha workflow event log is append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS alpha_workflow_events_no_delete
            BEFORE DELETE ON alpha_workflow_events BEGIN
                SELECT RAISE(ABORT, 'alpha workflow event log is append-only');
            END;
            """
        )
        self._connection.commit()

    @staticmethod
    def _json(payload: Mapping[str, Any]) -> str:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    def register_candidate(self, identity: CandidateIdentity) -> bool:
        item = _validated_identity(identity)
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT identity_json FROM alpha_population WHERE candidate_id=?",
                (item.candidate_id,),
            ).fetchone()
            if existing is not None:
                stored = _identity_from_payload(json.loads(str(existing[0])))
                if stored != item:
                    raise AlphaProductionWorkflowError(
                        "candidate_id already has a different immutable identity"
                    )
                connection.rollback()
                return False
            if item.predecessor_id is not None:
                predecessor_row = connection.execute(
                    "SELECT identity_json FROM alpha_population WHERE candidate_id=?",
                    (item.predecessor_id,),
                ).fetchone()
                if predecessor_row is None:
                    raise AlphaProductionWorkflowError("predecessor is not registered")
                predecessor = _identity_from_payload(
                    json.loads(str(predecessor_row[0]))
                )
                actual_change = classify_material_change(predecessor, item)
                if actual_change is not item.change_class:
                    raise AlphaProductionWorkflowError(
                        "declared material change_class does not match identity diff"
                    )
            previous = connection.execute(
                "SELECT record_hash FROM alpha_population ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            previous_hash = str(previous[0]) if previous is not None else "0" * 64
            payload = _identity_payload(item)
            record_hash = stable_hash(
                {"previous_hash": previous_hash, "identity": payload}
            )
            connection.execute(
                """
                INSERT INTO alpha_population (
                    candidate_id,identity_json,identity_hash,definition_hash,
                    predecessor_id,change_class,previous_hash,record_hash
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    item.candidate_id,
                    self._json(payload),
                    item.identity_hash,
                    item.definition_hash,
                    item.predecessor_id,
                    item.change_class.value,
                    previous_hash,
                    record_hash,
                ),
            )
            connection.commit()
            return True
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise

    def identity(self, candidate_id: str) -> CandidateIdentity | None:
        row = self._connection.execute(
            "SELECT identity_json FROM alpha_population WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        return _identity_from_payload(json.loads(str(row[0])))

    def all_identities(self) -> tuple[CandidateIdentity, ...]:
        return tuple(
            _identity_from_payload(json.loads(str(row[0])))
            for row in self._connection.execute(
                "SELECT identity_json FROM alpha_population ORDER BY seq"
            )
        )

    def latest_bundle(self, candidate_id: str) -> CandidateEvidenceBundle | None:
        row = self._connection.execute(
            """
            SELECT bundle_json FROM alpha_workflow_events
            WHERE candidate_id=? ORDER BY seq DESC LIMIT 1
            """,
            (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        return _bundle_from_payload(json.loads(str(row[0])))

    def record_evidence(self, bundle: CandidateEvidenceBundle) -> bool:
        item = _validated_bundle(bundle)
        identity = self.identity(item.candidate_id)
        if identity is None:
            raise AlphaProductionWorkflowError("candidate is not registered")
        if item.identity_hash != identity.identity_hash:
            raise AlphaProductionWorkflowError(
                "evidence bundle is not bound to candidate identity"
            )
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            previous_candidate = connection.execute(
                """
                SELECT stage,bundle_hash FROM alpha_workflow_events
                WHERE candidate_id=? ORDER BY seq DESC LIMIT 1
                """,
                (item.candidate_id,),
            ).fetchone()
            if previous_candidate is not None:
                previous_stage = ProductionStage(str(previous_candidate["stage"]))
                if str(previous_candidate["bundle_hash"]) == item.bundle_hash:
                    connection.rollback()
                    return False
                if previous_stage is ProductionStage.RETIRED:
                    raise AlphaProductionWorkflowError(
                        "retired candidate workflow is terminal"
                    )
                if _STAGE_RANK[item.stage] < _STAGE_RANK[previous_stage]:
                    raise AlphaProductionWorkflowError(
                        "candidate workflow stage cannot move backward"
                    )
            else:
                previous_stage = None
            crossing_eligibility = (
                item.stage is ProductionStage.EXECUTION_PAPER_ELIGIBLE
                and previous_stage not in {
                    ProductionStage.EXECUTION_PAPER_ELIGIBLE,
                    ProductionStage.EXECUTION_PAPER,
                }
            )
            crossing_execution = (
                item.stage is ProductionStage.EXECUTION_PAPER
                and previous_stage is not ProductionStage.EXECUTION_PAPER
            )
            if crossing_eligibility and not item.eligible_for_execution_paper:
                raise AlphaProductionWorkflowError(
                    "execution-paper eligibility transition requires every current gate"
                )
            if crossing_execution and (
                not item.eligible_for_execution_paper
                or item.gate(EvidenceGate.EXECUTION_PAPER).status
                is not EvidenceStatus.PAPER_MARKED
            ):
                raise AlphaProductionWorkflowError(
                    "execution-paper transition requires eligibility and a marked account"
                )
            previous = connection.execute(
                "SELECT record_hash FROM alpha_workflow_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            previous_hash = str(previous[0]) if previous is not None else "0" * 64
            payload = _bundle_payload(item, include_derived=True)
            event_id = stable_hash(
                {"candidate_id": item.candidate_id, "bundle_hash": item.bundle_hash}
            )
            record_hash = stable_hash(
                {"previous_hash": previous_hash, "bundle": payload}
            )
            connection.execute(
                """
                INSERT INTO alpha_workflow_events (
                    event_id,candidate_id,stage,bundle_json,bundle_hash,
                    previous_hash,record_hash
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    event_id,
                    item.candidate_id,
                    item.stage.value,
                    self._json(payload),
                    item.bundle_hash,
                    previous_hash,
                    record_hash,
                ),
            )
            connection.commit()
            return True
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise

    def chain_valid(self) -> bool:
        try:
            previous_hash = "0" * 64
            for row in self._connection.execute(
                "SELECT * FROM alpha_population ORDER BY seq"
            ):
                payload = json.loads(str(row["identity_json"]))
                identity = _identity_from_payload(payload)
                if str(row["identity_hash"]) != identity.identity_hash:
                    return False
                if str(row["definition_hash"]) != identity.definition_hash:
                    return False
                if str(row["previous_hash"]) != previous_hash:
                    return False
                expected = stable_hash(
                    {"previous_hash": previous_hash, "identity": payload}
                )
                if str(row["record_hash"]) != expected:
                    return False
                previous_hash = expected
            previous_hash = "0" * 64
            for row in self._connection.execute(
                "SELECT * FROM alpha_workflow_events ORDER BY seq"
            ):
                payload = json.loads(str(row["bundle_json"]))
                bundle = _bundle_from_payload(payload)
                if str(row["bundle_hash"]) != bundle.bundle_hash:
                    return False
                if str(row["previous_hash"]) != previous_hash:
                    return False
                expected = stable_hash(
                    {"previous_hash": previous_hash, "bundle": payload}
                )
                if str(row["record_hash"]) != expected:
                    return False
                previous_hash = expected
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False
        return True

    def snapshot(self) -> dict[str, Any]:
        identities = self.all_identities()
        rows: list[dict[str, Any]] = []
        active_ids: list[str] = []
        eligible_ids: list[str] = []
        execution_ids: list[str] = []
        for identity in identities:
            bundle = self.latest_bundle(identity.candidate_id)
            active = bundle is None or bundle.stage is not ProductionStage.RETIRED
            if active:
                active_ids.append(identity.candidate_id)
            if bundle is not None and bundle.eligible_for_execution_paper:
                eligible_ids.append(identity.candidate_id)
            if bundle is not None and bundle.stage is ProductionStage.EXECUTION_PAPER:
                execution_ids.append(identity.candidate_id)
            rows.append(
                {
                    "candidate_id": identity.candidate_id,
                    "identity": _identity_payload(identity),
                    "identity_hash": identity.identity_hash,
                    "definition_hash": identity.definition_hash,
                    "active": active,
                    "workflow": (
                        _bundle_payload(bundle, include_derived=True)
                        if bundle is not None
                        else None
                    ),
                }
            )
        event_count = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM alpha_workflow_events"
            ).fetchone()[0]
        )
        return {
            "schema_version": 1,
            "authority": "ALPHA_PRODUCTION_WORKFLOW",
            "status": "VERIFIED" if self.chain_valid() else "INVALID",
            "population_count": len(rows),
            "event_count": event_count,
            "active_candidate_ids": active_ids,
            "execution_paper_eligible_ids": eligible_ids,
            "execution_paper_ids": execution_ids,
            "candidates": rows,
            "safety": {
                "paper_only": True,
                "live_allowed": False,
                "order_submission_allowed": False,
                "lifecycle_promotion": False,
                "credentials_accessed": False,
                "broker_connectivity": False,
            },
        }

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "AlphaProductionWorkflow":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = [
    "AlphaProductionWorkflow",
    "AlphaProductionWorkflowError",
    "CandidateDefinitionKind",
    "CandidateEvidenceBundle",
    "CandidateIdentity",
    "EvidenceGate",
    "EvidenceStatus",
    "GateEvidence",
    "MaterialChangeClass",
    "ProductionStage",
    "classify_material_change",
    "derive_stage",
]
