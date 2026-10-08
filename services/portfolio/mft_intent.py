"""Pure L2 portfolio-intent projection for the Wave 2 Testnet outbox."""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from quantos.contracts.hashing import stable_hash
from quantos.contracts.mft_execution import (
    TestnetExecutionIntent,
    create_testnet_execution_intent,
)
from quantos.contracts.portfolio import RebalancePlan, TargetPosition
from services.portfolio.portfolio_constraints import (
    PortfolioConstraintInputs,
    PortfolioConstraintViolation,
    enforce_portfolio_constraints,
)


_HEX64 = re.compile(r"[0-9a-f]{64}")
_EVIDENCE_KINDS = frozenset({"CAPACITY", "CORRELATION", "COST"})
_SCHEMA = "mft-portfolio-intent-projection.v1"
_EPS = 1e-9
_NO_ACTION_REASONS = frozenset({"NO_PAPER_SLEEVES", "NO_REBALANCE_REQUIRED"})


class MftIntentError(RuntimeError):
    """Portfolio intent evidence or persistence is invalid."""


class MftIntentBlocked(MftIntentError):
    """The projection is fail-closed and must not reach execution dispatch."""


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise MftIntentBlocked(f"{name} must be non-empty canonical text")
    return value


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise MftIntentBlocked(f"{name} must be a lowercase sha256 digest")
    return value


def _time(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MftIntentBlocked(f"{name} must be a non-negative integer")
    return value


def _weights(values: Mapping[str, float], name: str) -> dict[str, float]:
    if not isinstance(values, Mapping):
        raise MftIntentBlocked(f"{name} must be a mapping")
    output: dict[str, float] = {}
    for instrument, value in sorted(values.items()):
        instrument = _text(instrument, f"{name}.instrument")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise MftIntentBlocked(f"{name} contains a non-finite weight")
        output[instrument] = float(value)
    return output


def _digest_set(values: Iterable[str], name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise MftIntentBlocked(f"{name} must be a digest collection")
    materialized = tuple(_digest(item, name) for item in values)
    canonical = tuple(sorted(set(materialized)))
    if not canonical or len(canonical) != len(materialized):
        raise MftIntentBlocked(f"{name} must be non-empty and unique")
    return canonical


@dataclass(frozen=True, slots=True)
class MftSleeveRef:
    alpha_id: str
    trial_id: str
    workflow_state: str
    workflow_receipt_hash: str
    validation_artifact_hash: str
    forward_decision_hash: str
    panel_digest: str
    policy_digest: str
    decided_at_ms: int
    expires_at_ms: int

    def __post_init__(self) -> None:
        for name in (
            "alpha_id",
            "trial_id",
            "workflow_receipt_hash",
            "validation_artifact_hash",
            "forward_decision_hash",
            "panel_digest",
            "policy_digest",
        ):
            _digest(getattr(self, name), name)
        if self.workflow_state != "PAPER":
            raise MftIntentBlocked("sleeve workflow_state must be PAPER")
        _time(self.decided_at_ms, "decided_at_ms")
        _time(self.expires_at_ms, "expires_at_ms")
        if self.expires_at_ms <= self.decided_at_ms:
            raise MftIntentBlocked("sleeve expiry must follow its decision")

    def as_dict(self) -> dict[str, object]:
        return {
            "alpha_id": self.alpha_id,
            "trial_id": self.trial_id,
            "workflow_state": self.workflow_state,
            "workflow_receipt_hash": self.workflow_receipt_hash,
            "validation_artifact_hash": self.validation_artifact_hash,
            "forward_decision_hash": self.forward_decision_hash,
            "panel_digest": self.panel_digest,
            "policy_digest": self.policy_digest,
            "decided_at_ms": self.decided_at_ms,
            "expires_at_ms": self.expires_at_ms,
        }


@dataclass(frozen=True, slots=True)
class MftPortfolioEvidenceRef:
    kind: str
    status: str
    evidence_hash: str
    as_of_ms: int
    expires_at_ms: int

    def __post_init__(self) -> None:
        if self.kind not in _EVIDENCE_KINDS:
            raise MftIntentBlocked("unknown portfolio evidence kind")
        if self.status != "PASS":
            raise MftIntentBlocked(f"{self.kind} evidence is not PASS")
        _digest(self.evidence_hash, "evidence_hash")
        _time(self.as_of_ms, "as_of_ms")
        _time(self.expires_at_ms, "expires_at_ms")
        if self.expires_at_ms <= self.as_of_ms:
            raise MftIntentBlocked("portfolio evidence expiry is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "status": self.status,
            "evidence_hash": self.evidence_hash,
            "as_of_ms": self.as_of_ms,
            "expires_at_ms": self.expires_at_ms,
        }


def _projection_body(
    *,
    cycle_id: str,
    status: str,
    recorded_at_ms: int,
    reason_code: str,
    sleeve_refs: Sequence[MftSleeveRef],
    evidence_refs: Sequence[MftPortfolioEvidenceRef],
    plan: RebalancePlan | None,
    deltas: Sequence[TargetPosition],
    intent: TestnetExecutionIntent | None,
) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA,
        "cycle_id": cycle_id,
        "status": status,
        "recorded_at_ms": recorded_at_ms,
        "reason_code": reason_code,
        "sleeve_refs": [item.as_dict() for item in sleeve_refs],
        "evidence_refs": [item.as_dict() for item in evidence_refs],
        "plan": None if plan is None else plan.model_dump(mode="json"),
        "deltas": [item.model_dump(mode="json") for item in deltas],
        "intent": None if intent is None else intent.model_dump(mode="json"),
    }


@dataclass(frozen=True, slots=True)
class MftIntentProjection:
    cycle_id: str
    status: str
    recorded_at_ms: int
    reason_code: str
    sleeve_refs: tuple[MftSleeveRef, ...]
    evidence_refs: tuple[MftPortfolioEvidenceRef, ...]
    plan: RebalancePlan | None
    deltas: tuple[TargetPosition, ...]
    intent: TestnetExecutionIntent | None
    projection_hash: str

    def __post_init__(self) -> None:
        _text(self.cycle_id, "cycle_id")
        _time(self.recorded_at_ms, "recorded_at_ms")
        if self.status not in {"READY", "NO_ACTION"}:
            raise MftIntentBlocked("projection status is invalid")
        if self.status == "READY":
            if self.reason_code or self.plan is None or self.intent is None:
                raise MftIntentBlocked("READY projection is incomplete")
            if not self.sleeve_refs or not self.deltas:
                raise MftIntentBlocked("READY projection has no sleeves or deltas")
        else:
            if self.reason_code not in _NO_ACTION_REASONS:
                raise MftIntentBlocked("NO_ACTION reason is invalid")
            if self.deltas or self.intent is not None:
                raise MftIntentBlocked("NO_ACTION must not contain an order shell")
            if self.reason_code == "NO_PAPER_SLEEVES" and (
                self.sleeve_refs or self.evidence_refs or self.plan is not None
            ):
                raise MftIntentBlocked("no-passers receipt must be empty")
            if self.reason_code == "NO_REBALANCE_REQUIRED" and (
                not self.sleeve_refs or not self.evidence_refs or self.plan is None
            ):
                raise MftIntentBlocked("no-rebalance receipt must retain its evidence")
        _digest(self.projection_hash, "projection_hash")
        if self.projection_hash != stable_hash(self.body()):
            raise MftIntentBlocked("projection_hash does not bind projection")

    def body(self) -> dict[str, object]:
        return _projection_body(
            cycle_id=self.cycle_id,
            status=self.status,
            recorded_at_ms=self.recorded_at_ms,
            reason_code=self.reason_code,
            sleeve_refs=self.sleeve_refs,
            evidence_refs=self.evidence_refs,
            plan=self.plan,
            deltas=self.deltas,
            intent=self.intent,
        )

    def as_dict(self) -> dict[str, object]:
        return {**self.body(), "projection_hash": self.projection_hash}


class MftIntentOutbox(Protocol):
    def append(self, projection: MftIntentProjection) -> object: ...


def _projection(
    *,
    cycle_id: str,
    status: str,
    recorded_at_ms: int,
    reason_code: str,
    sleeve_refs: tuple[MftSleeveRef, ...],
    evidence_refs: tuple[MftPortfolioEvidenceRef, ...],
    plan: RebalancePlan | None,
    deltas: tuple[TargetPosition, ...],
    intent: TestnetExecutionIntent | None,
) -> MftIntentProjection:
    body = _projection_body(
        cycle_id=cycle_id,
        status=status,
        recorded_at_ms=recorded_at_ms,
        reason_code=reason_code,
        sleeve_refs=sleeve_refs,
        evidence_refs=evidence_refs,
        plan=plan,
        deltas=deltas,
        intent=intent,
    )
    return MftIntentProjection(
        cycle_id=cycle_id,
        status=status,
        recorded_at_ms=recorded_at_ms,
        reason_code=reason_code,
        sleeve_refs=sleeve_refs,
        evidence_refs=evidence_refs,
        plan=plan,
        deltas=deltas,
        intent=intent,
        projection_hash=stable_hash(body),
    )


def project_mft_portfolio_intent(
    outbox: MftIntentOutbox,
    *,
    cycle_id: str,
    portfolio_id: str,
    sleeves: Iterable[MftSleeveRef],
    plan: RebalancePlan | None,
    current_weights: Mapping[str, float],
    constraints: PortfolioConstraintInputs | None,
    evidence_refs: Iterable[MftPortfolioEvidenceRef],
    market_watermark_ms: int,
    market_snapshot_hash: str,
    decided_at_ms: int,
    recorded_at_ms: int,
    max_market_age_ms: int,
    policy_version: str,
    expected_workflow_receipt_hash: str | None = None,
    expected_validation_artifact_hashes: Iterable[str] | None = None,
    expected_forward_decision_hashes: Iterable[str] | None = None,
    expected_panel_digest: str | None = None,
) -> MftIntentProjection:
    """Validate one PAPER cycle, persist its outbox row, and return no dispatcher."""
    if not hasattr(outbox, "append"):
        raise TypeError("outbox must provide append")
    cycle_id = _text(cycle_id, "cycle_id")
    portfolio_id = _text(portfolio_id, "portfolio_id")
    decided_at_ms = _time(decided_at_ms, "decided_at_ms")
    recorded_at_ms = _time(recorded_at_ms, "recorded_at_ms")
    max_market_age_ms = _time(max_market_age_ms, "max_market_age_ms")
    if recorded_at_ms < decided_at_ms:
        raise MftIntentBlocked("recorded_at_ms predates decision")
    sleeve_refs = tuple(sleeves)
    if any(not isinstance(item, MftSleeveRef) for item in sleeve_refs):
        raise TypeError("sleeves must contain MftSleeveRef")
    sleeve_refs = tuple(sorted(sleeve_refs, key=lambda item: item.alpha_id))
    if not sleeve_refs:
        projection = _projection(
            cycle_id=cycle_id,
            status="NO_ACTION",
            recorded_at_ms=recorded_at_ms,
            reason_code="NO_PAPER_SLEEVES",
            sleeve_refs=(),
            evidence_refs=(),
            plan=None,
            deltas=(),
            intent=None,
        )
        outbox.append(projection)
        return projection
    if len({item.alpha_id for item in sleeve_refs}) != len(sleeve_refs):
        raise MftIntentBlocked("duplicate sleeve alpha_id")
    if len({item.trial_id for item in sleeve_refs}) != len(sleeve_refs):
        raise MftIntentBlocked("duplicate sleeve trial_id")
    if any(item.decided_at_ms != decided_at_ms for item in sleeve_refs):
        raise MftIntentBlocked("sleeve decision time mismatch")
    if any(recorded_at_ms >= item.expires_at_ms for item in sleeve_refs):
        raise MftIntentBlocked("sleeve is expired")
    workflow_hashes = {item.workflow_receipt_hash for item in sleeve_refs}
    panel_hashes = {item.panel_digest for item in sleeve_refs}
    policy_hashes = {item.policy_digest for item in sleeve_refs}
    if len(workflow_hashes) != 1 or len(panel_hashes) != 1 or len(policy_hashes) != 1:
        raise MftIntentBlocked("sleeve refs do not share one workflow context")
    workflow_hash = next(iter(workflow_hashes))
    validation_hashes = _digest_set(
        (item.validation_artifact_hash for item in sleeve_refs),
        "validation_artifact_hash",
    )
    forward_hashes = _digest_set(
        (item.forward_decision_hash for item in sleeve_refs),
        "forward_decision_hash",
    )
    if any(
        value is None
        for value in (
            expected_workflow_receipt_hash,
            expected_validation_artifact_hashes,
            expected_forward_decision_hashes,
            expected_panel_digest,
        )
    ):
        raise MftIntentBlocked("authoritative Wave 1 refs are required")
    if workflow_hash != _digest(
        expected_workflow_receipt_hash, "expected_workflow_receipt_hash"
    ):
        raise MftIntentBlocked("workflow receipt ref mismatch")
    expected_validation = _digest_set(
        expected_validation_artifact_hashes,
        "expected_validation_artifact_hash",
    )
    if validation_hashes != expected_validation:
        raise MftIntentBlocked("validation artifact refs mismatch")
    expected_forward = _digest_set(
        expected_forward_decision_hashes,
        "expected_forward_decision_hash",
    )
    if forward_hashes != expected_forward:
        raise MftIntentBlocked("forward decision refs mismatch")
    panel_hash = next(iter(panel_hashes))
    if panel_hash != _digest(expected_panel_digest, "expected_panel_digest"):
        raise MftIntentBlocked("panel digest ref mismatch")
    market_watermark_ms = _time(market_watermark_ms, "market_watermark_ms")
    _digest(market_snapshot_hash, "market_snapshot_hash")
    if (
        market_watermark_ms > decided_at_ms
        or decided_at_ms - market_watermark_ms > max_market_age_ms
    ):
        raise MftIntentBlocked("market watermark is stale or in the future")
    refs = tuple(evidence_refs)
    if any(not isinstance(item, MftPortfolioEvidenceRef) for item in refs):
        raise TypeError("evidence_refs must contain MftPortfolioEvidenceRef")
    refs = tuple(sorted(refs, key=lambda item: item.kind))
    if {item.kind for item in refs} != _EVIDENCE_KINDS or len(refs) != len(_EVIDENCE_KINDS):
        raise MftIntentBlocked("capacity/correlation/cost evidence set is incomplete")
    if any(
        item.as_of_ms > decided_at_ms
        or recorded_at_ms >= item.expires_at_ms
        or decided_at_ms - item.as_of_ms > max_market_age_ms
        for item in refs
    ):
        raise MftIntentBlocked("portfolio evidence is stale, future, or expired")
    if not isinstance(plan, RebalancePlan) or plan.portfolio_id != portfolio_id:
        raise MftIntentBlocked("RebalancePlan is missing or for another portfolio")
    target_map = _weights(
        {item.instrument_id: item.target_weight for item in plan.targets}, "plan.targets"
    )
    if not target_map or len(target_map) != len(plan.targets):
        raise MftIntentBlocked("RebalancePlan targets are empty or duplicated")
    plan = plan.model_copy(
        update={"targets": tuple(sorted(plan.targets, key=lambda item: item.instrument_id))}
    )
    if constraints is None:
        raise MftIntentBlocked("portfolio gate evidence is required")
    try:
        enforce_portfolio_constraints(target_map, constraints)
    except PortfolioConstraintViolation as exc:
        raise MftIntentBlocked("portfolio gate did not PASS") from exc
    current = _weights(current_weights, "current_weights")
    deltas = tuple(
        TargetPosition(
            instrument_id=instrument,
            target_weight=round(target_map.get(instrument, 0.0) - current.get(instrument, 0.0), 10),
        )
        for instrument in sorted(set(target_map) | set(current))
        if abs(target_map.get(instrument, 0.0) - current.get(instrument, 0.0)) > _EPS
    )
    expected_turnover = sum(abs(item.target_weight) for item in deltas)
    if abs(float(plan.turnover) - expected_turnover) > _EPS:
        raise MftIntentBlocked("RebalancePlan turnover does not match current-position delta")
    if not deltas:
        projection = _projection(
            cycle_id=cycle_id,
            status="NO_ACTION",
            recorded_at_ms=recorded_at_ms,
            reason_code="NO_REBALANCE_REQUIRED",
            sleeve_refs=sleeve_refs,
            evidence_refs=refs,
            plan=plan,
            deltas=(),
            intent=None,
        )
        outbox.append(projection)
        return projection
    policy_version = _text(policy_version, "policy_version")
    intent = create_testnet_execution_intent(
        portfolio_id=portfolio_id,
        alpha_ids=(item.alpha_id for item in sleeve_refs),
        decided_at_ms=decided_at_ms,
        expires_at_ms=min(item.expires_at_ms for item in sleeve_refs),
        targets=plan.targets,
        market_watermark_ms=market_watermark_ms,
        market_snapshot_hash=market_snapshot_hash,
        workflow_receipt_hash=workflow_hash,
        validation_artifact_hashes=validation_hashes,
        policy_version=policy_version,
        policy_hash=next(iter(policy_hashes)),
        testnet_submission_requested=False,
    )
    projection = _projection(
        cycle_id=cycle_id,
        status="READY",
        recorded_at_ms=recorded_at_ms,
        reason_code="",
        sleeve_refs=sleeve_refs,
        evidence_refs=refs,
        plan=plan,
        deltas=deltas,
        intent=intent,
    )
    outbox.append(projection)
    return projection


__all__ = [
    "MftIntentBlocked",
    "MftIntentError",
    "MftIntentProjection",
    "MftPortfolioEvidenceRef",
    "MftSleeveRef",
    "project_mft_portfolio_intent",
]
