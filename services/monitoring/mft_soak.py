"""Fail-closed Task 39 Testnet soak policy and evidence evaluation.

The module is pure: it owns no clock, filesystem, network, credential, venue,
mandate, broker, or submission path.  It validates pre-existing operator and
cycle receipts and can award only Testnet engineering evidence.  A passing
soak never grants Micro-Live or live-order authority.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from quantos.contracts.hashing import stable_hash


POLICY_KIND = "MFT_TESTNET_SOAK_POLICY"
START_KIND = "MFT_TESTNET_SOAK_START"
CYCLE_KIND = "MFT_TESTNET_SOAK_CYCLE"
VENUE = "BINANCE_FUTURES_TESTNET"
MODE = "TESTNET_ONLY"
AUTHORIZATION_CLASS = "OPERATOR_ASSERTED_TESTNET_ONLY"
COMPLETION_EFFECT = "TESTNET_ENGINEERING_ONLY_NO_MICRO_LIVE_AUTHORITY"
CALIBRATION = "PREREGISTERED_UNCALIBRATED_ENGINEERING_LIMITS"
REQUIRED_EVIDENCE_KINDS = (
    "market_continuity",
    "scheduler_lease",
    "intent",
    "oms",
    "fill",
    "reconciliation",
    "risk",
    "tca",
    "parity",
    "recovery_drill",
)
SAFETY_FLAGS = {
    "LIVE_TRADING_ENABLED": False,
    "REAL_BROKER_ENABLED": False,
    "ALLOW_ORDER_SUBMISSION": False,
    "ALLOW_AI_DIRECT_TRADING": False,
    "REQUIRE_HUMAN_APPROVAL": True,
}

_SHA256 = re.compile(r"[0-9a-f]{64}")
_ARTIFACT_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_POLICY_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "policy_id",
        "policy_version",
        "venue",
        "mode",
        "cadence_ms",
        "minimum_duration_ms",
        "minimum_completed_cycles",
        "maximum_cycle_gap_ms",
        "thresholds",
        "required_evidence_kinds",
        "safety_flags",
        "calibration",
        "completion_effect",
    }
)
_THRESHOLD_KEYS = frozenset(
    {
        "max_market_gap_ms",
        "max_scheduler_lag_ms",
        "max_reconciliation_abs_error",
        "max_abs_tca_slippage_bps",
        "max_parity_position_drift",
        "max_oms_unknown_count",
        "max_risk_breach_count",
        "max_failed_cycle_count",
    }
)
_START_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "run_id",
        "operator_start_id",
        "started_at_ms",
        "policy_hash",
        "venue",
        "mode",
        "authorization_class",
        "human_authorization_receipt_hash",
        "testnet_mandate_hash",
        "credential_reference_hash",
        "safety_flags",
    }
)
_CYCLE_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "run_id",
        "operator_start_id",
        "cycle_id",
        "cycle_index",
        "started_at_ms",
        "completed_at_ms",
        "policy_hash",
        "venue",
        "mode",
        "evidence",
        "metrics",
        "safety_flags",
    }
)
_METRIC_KEYS = frozenset(
    {
        "market_gap_ms",
        "scheduler_lag_ms",
        "intent_count",
        "oms_unknown_count",
        "fill_count",
        "reconciliation_abs_error",
        "risk_breach_count",
        "tca_abs_slippage_bps",
        "parity_position_drift",
        "recovery_drill_passed",
        "testnet_submission_count",
        "mainnet_submission_count",
    }
)
_EVIDENCE_KEYS = frozenset({"artifact", "artifact_hash", "observed_at_ms", "status"})


class MftSoakError(RuntimeError):
    """A policy or receipt cannot be trusted as Task 39 evidence."""


def _mapping(value: object, *, name: str, keys: frozenset[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MftSoakError(f"{name} must be an object")
    result = dict(value)
    if keys is not None and set(result) != keys:
        raise MftSoakError(f"{name} fields are invalid")
    return result


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise MftSoakError(f"{name} must be non-empty canonical text")
    return value


def _integer(value: object, *, name: str, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MftSoakError(f"{name} must be an integer")
    if value < (1 if positive else 0):
        qualifier = "positive" if positive else "non-negative"
        raise MftSoakError(f"{name} must be {qualifier}")
    return value


def _finite(value: object, *, name: str, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MftSoakError(f"{name} must be finite")
    result = float(value)
    if not math.isfinite(result) or (nonnegative and result < 0):
        raise MftSoakError(f"{name} must be finite and non-negative")
    return result


def _hash(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise MftSoakError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _artifact(value: object, *, name: str) -> str:
    text = _text(value, name=name)
    relative = PurePosixPath(text)
    if (
        "\\" in text
        or relative.is_absolute()
        or not relative.parts
        or _ARTIFACT_PATH.fullmatch(text) is None
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.as_posix() != text
    ):
        raise MftSoakError(f"{name} is unsafe or non-canonical")
    return text


def _safety(value: object) -> dict[str, bool]:
    result = _mapping(value, name="safety_flags")
    if result != SAFETY_FLAGS:
        raise MftSoakError("safety_flags do not preserve the L2 boundary")
    return dict(SAFETY_FLAGS)


@dataclass(frozen=True, slots=True)
class MftSoakPolicy:
    policy_id: str
    policy_version: str
    cadence_ms: int
    minimum_duration_ms: int
    minimum_completed_cycles: int
    maximum_cycle_gap_ms: int
    thresholds: Mapping[str, int | float]
    required_evidence_kinds: tuple[str, ...]
    calibration: str = CALIBRATION
    completion_effect: str = COMPLETION_EFFECT

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": POLICY_KIND,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "venue": VENUE,
            "mode": MODE,
            "cadence_ms": self.cadence_ms,
            "minimum_duration_ms": self.minimum_duration_ms,
            "minimum_completed_cycles": self.minimum_completed_cycles,
            "maximum_cycle_gap_ms": self.maximum_cycle_gap_ms,
            "thresholds": dict(self.thresholds),
            "required_evidence_kinds": list(self.required_evidence_kinds),
            "safety_flags": dict(SAFETY_FLAGS),
            "calibration": self.calibration,
            "completion_effect": self.completion_effect,
        }

    @property
    def policy_hash(self) -> str:
        return stable_hash(self.payload())


@dataclass(frozen=True, slots=True)
class CycleEvaluation:
    cycle_id: str
    cycle_index: int
    started_at_ms: int
    completed_at_ms: int
    status: str
    reason_codes: tuple[str, ...]
    micro_live_authority: bool = False


def parse_soak_policy(document: Mapping[str, Any]) -> MftSoakPolicy:
    body = _mapping(document, name="policy", keys=_POLICY_KEYS)
    if body["schema_version"] != 1 or body["kind"] != POLICY_KIND:
        raise MftSoakError("policy schema or kind is invalid")
    if body["venue"] != VENUE:
        raise MftSoakError("policy venue must be Binance Futures Testnet")
    if body["mode"] != MODE:
        raise MftSoakError("policy mode must be TESTNET_ONLY")
    if body["calibration"] != CALIBRATION:
        raise MftSoakError("policy calibration must remain preregistered and uncalibrated")
    if body["completion_effect"] != COMPLETION_EFFECT:
        raise MftSoakError("policy completion_effect cannot grant Micro-Live authority")
    _safety(body["safety_flags"])
    required = body["required_evidence_kinds"]
    if not isinstance(required, list) or tuple(required) != REQUIRED_EVIDENCE_KINDS:
        raise MftSoakError("required_evidence_kinds must match the complete Task 39 contract")
    raw_thresholds = _mapping(body["thresholds"], name="thresholds", keys=_THRESHOLD_KEYS)
    thresholds: dict[str, int | float] = {}
    for name in (
        "max_market_gap_ms",
        "max_scheduler_lag_ms",
        "max_oms_unknown_count",
        "max_risk_breach_count",
        "max_failed_cycle_count",
    ):
        thresholds[name] = _integer(raw_thresholds[name], name=name)
    for name in (
        "max_reconciliation_abs_error",
        "max_abs_tca_slippage_bps",
        "max_parity_position_drift",
    ):
        thresholds[name] = _finite(raw_thresholds[name], name=name, nonnegative=True)
    cadence_ms = _integer(body["cadence_ms"], name="cadence_ms", positive=True)
    minimum_duration_ms = _integer(
        body["minimum_duration_ms"], name="minimum_duration_ms", positive=True
    )
    minimum_completed_cycles = _integer(
        body["minimum_completed_cycles"], name="minimum_completed_cycles", positive=True
    )
    maximum_cycle_gap_ms = _integer(
        body["maximum_cycle_gap_ms"], name="maximum_cycle_gap_ms", positive=True
    )
    if maximum_cycle_gap_ms < cadence_ms:
        raise MftSoakError("maximum_cycle_gap_ms must not be shorter than cadence_ms")
    if minimum_duration_ms < cadence_ms * (minimum_completed_cycles - 1):
        raise MftSoakError("minimum duration is shorter than the required cycle schedule")
    return MftSoakPolicy(
        policy_id=_text(body["policy_id"], name="policy_id"),
        policy_version=_text(body["policy_version"], name="policy_version"),
        cadence_ms=cadence_ms,
        minimum_duration_ms=minimum_duration_ms,
        minimum_completed_cycles=minimum_completed_cycles,
        maximum_cycle_gap_ms=maximum_cycle_gap_ms,
        thresholds=dict(sorted(thresholds.items())),
        required_evidence_kinds=REQUIRED_EVIDENCE_KINDS,
    )


def validate_start_receipt(
    document: Mapping[str, Any], policy: MftSoakPolicy
) -> dict[str, Any]:
    if not isinstance(policy, MftSoakPolicy):
        raise TypeError("policy must be an MftSoakPolicy")
    body = _mapping(document, name="start receipt", keys=_START_KEYS)
    if body["schema_version"] != 1 or body["kind"] != START_KIND:
        raise MftSoakError("start receipt schema or kind is invalid")
    if body["venue"] != VENUE or body["mode"] != MODE:
        raise MftSoakError("start receipt is not Testnet-only")
    if body["authorization_class"] != AUTHORIZATION_CLASS:
        raise MftSoakError("authorization_class must remain operator-asserted Testnet-only")
    if body["policy_hash"] != policy.policy_hash:
        raise MftSoakError("start receipt policy_hash mismatch")
    _safety(body["safety_flags"])
    for name in (
        "human_authorization_receipt_hash",
        "testnet_mandate_hash",
        "credential_reference_hash",
    ):
        _hash(body[name], name=name)
    _text(body["run_id"], name="run_id")
    _text(body["operator_start_id"], name="operator_start_id")
    _integer(body["started_at_ms"], name="started_at_ms")
    return dict(body)


def _validated_cycle_body(
    document: Mapping[str, Any],
    policy: MftSoakPolicy,
    start_receipt: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    start = validate_start_receipt(start_receipt, policy)
    body = _mapping(document, name="cycle receipt", keys=_CYCLE_KEYS)
    if body["schema_version"] != 1 or body["kind"] != CYCLE_KIND:
        raise MftSoakError("cycle receipt schema or kind is invalid")
    if body["venue"] != VENUE or body["mode"] != MODE:
        raise MftSoakError("cycle receipt is not Testnet-only")
    if body["policy_hash"] != policy.policy_hash:
        raise MftSoakError("cycle policy_hash mismatch")
    if body["run_id"] != start["run_id"]:
        raise MftSoakError("cycle run_id mismatch")
    if body["operator_start_id"] != start["operator_start_id"]:
        raise MftSoakError("cycle operator_start_id mismatch")
    _safety(body["safety_flags"])
    _text(body["cycle_id"], name="cycle_id")
    _integer(body["cycle_index"], name="cycle_index")
    started_at_ms = _integer(body["started_at_ms"], name="cycle started_at_ms")
    completed_at_ms = _integer(body["completed_at_ms"], name="cycle completed_at_ms")
    if started_at_ms < start["started_at_ms"] or completed_at_ms < started_at_ms:
        raise MftSoakError("cycle time interval is invalid")
    metrics = _mapping(body["metrics"], name="cycle metrics", keys=_METRIC_KEYS)
    evidence_body = _mapping(body["evidence"], name="cycle evidence")
    if set(evidence_body) != set(REQUIRED_EVIDENCE_KINDS):
        raise MftSoakError("cycle evidence does not match the complete required contract")
    evidence: dict[str, dict[str, Any]] = {}
    for kind in REQUIRED_EVIDENCE_KINDS:
        item = _mapping(evidence_body[kind], name=f"{kind} evidence", keys=_EVIDENCE_KEYS)
        _artifact(item["artifact"], name=f"{kind} artifact")
        _hash(item["artifact_hash"], name=f"{kind} artifact_hash")
        observed_at_ms = _integer(item["observed_at_ms"], name=f"{kind} observed_at_ms")
        if not started_at_ms <= observed_at_ms <= completed_at_ms:
            raise MftSoakError(f"{kind} evidence time is outside the cycle")
        _text(item["status"], name=f"{kind} evidence status")
        evidence[kind] = item
    return body, metrics, evidence


def evaluate_cycle(
    document: Mapping[str, Any],
    policy: MftSoakPolicy,
    start_receipt: Mapping[str, Any],
) -> CycleEvaluation:
    body, metrics, evidence = _validated_cycle_body(document, policy, start_receipt)
    reasons: list[str] = []
    for kind in REQUIRED_EVIDENCE_KINDS:
        if evidence[kind]["status"] != "VERIFIED":
            reasons.append(f"{kind.upper()}_NOT_VERIFIED")

    integers = {}
    for name in (
        "market_gap_ms",
        "scheduler_lag_ms",
        "intent_count",
        "oms_unknown_count",
        "fill_count",
        "risk_breach_count",
        "testnet_submission_count",
        "mainnet_submission_count",
    ):
        integers[name] = _integer(metrics[name], name=name)
    numbers = {}
    for name in (
        "reconciliation_abs_error",
        "tca_abs_slippage_bps",
        "parity_position_drift",
    ):
        numbers[name] = _finite(metrics[name], name=name, nonnegative=True)
    if type(metrics["recovery_drill_passed"]) is not bool:
        raise MftSoakError("recovery_drill_passed must be boolean")

    limits = policy.thresholds
    if integers["market_gap_ms"] > limits["max_market_gap_ms"]:
        reasons.append("MARKET_GAP_EXCEEDED")
    if integers["scheduler_lag_ms"] > limits["max_scheduler_lag_ms"]:
        reasons.append("SCHEDULER_LAG_EXCEEDED")
    if integers["intent_count"] == 0:
        reasons.append("EMPTY_INTENT_SET")
    if integers["oms_unknown_count"] > limits["max_oms_unknown_count"]:
        reasons.append("OMS_UNKNOWN_EXCEEDED")
    if integers["fill_count"] == 0:
        reasons.append("EMPTY_FILL_SET")
    if numbers["reconciliation_abs_error"] > limits["max_reconciliation_abs_error"]:
        reasons.append("RECONCILIATION_ERROR_EXCEEDED")
    if integers["risk_breach_count"] > limits["max_risk_breach_count"]:
        reasons.append("RISK_BREACH_EXCEEDED")
    if numbers["tca_abs_slippage_bps"] > limits["max_abs_tca_slippage_bps"]:
        reasons.append("TCA_SLIPPAGE_EXCEEDED")
    if numbers["parity_position_drift"] > limits["max_parity_position_drift"]:
        reasons.append("PARITY_POSITION_DRIFT_EXCEEDED")
    if metrics["recovery_drill_passed"] is not True:
        reasons.append("RECOVERY_DRILL_NOT_PASSED")
    if integers["testnet_submission_count"] == 0:
        reasons.append("EMPTY_TESTNET_SUBMISSION_SET")
    if integers["mainnet_submission_count"] != 0:
        reasons.append("MAINNET_SUBMISSION_OBSERVED")
    return CycleEvaluation(
        cycle_id=body["cycle_id"],
        cycle_index=body["cycle_index"],
        started_at_ms=body["started_at_ms"],
        completed_at_ms=body["completed_at_ms"],
        status="PASS" if not reasons else "BLOCKED",
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


def build_soak_status(
    *,
    policy: MftSoakPolicy,
    start_receipt: Mapping[str, Any] | None,
    cycles: Sequence[Mapping[str, Any]],
    chain_valid: bool,
    as_of_ms: int,
) -> dict[str, Any]:
    if not isinstance(policy, MftSoakPolicy):
        raise TypeError("policy must be an MftSoakPolicy")
    as_of_ms = _integer(as_of_ms, name="as_of_ms")
    if type(chain_valid) is not bool:
        raise TypeError("chain_valid must be boolean")
    base: dict[str, Any] = {
        "schema_version": 1,
        "artifact": "QUANTOS_MFT_TESTNET_SOAK_STATUS",
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "policy_hash": policy.policy_hash,
        "as_of_ms": as_of_ms,
        "readiness": "L2_DRY_RUN_ONLY",
        "chain_valid": chain_valid,
        "recorded_cycles": 0,
        "completed_cycles": 0,
        "failed_cycles": 0,
        "minimum_completed_cycles": policy.minimum_completed_cycles,
        "minimum_duration_ms": policy.minimum_duration_ms,
        "observed_duration_ms": 0,
        "maximum_observed_cycle_gap_ms": None,
        "blockers": [],
        "cycle_failure_reasons": [],
        "micro_live_ready": False,
        "live_order_authority": False,
        "completion_effect": COMPLETION_EFFECT,
        "safety_flags": dict(SAFETY_FLAGS),
    }
    if start_receipt is None:
        base.update(
            status="OPERATIONAL_SOAK_PENDING",
            run_id=None,
            operator_start_id=None,
            blockers=["OPERATOR_START_RECEIPT_MISSING"],
        )
        return base
    start = validate_start_receipt(start_receipt, policy)
    if as_of_ms < start["started_at_ms"]:
        raise MftSoakError("as_of_ms precedes the operator start")
    evaluations = [evaluate_cycle(row, policy, start) for row in cycles]
    evaluations.sort(key=lambda row: (row.cycle_index, row.cycle_id))
    blockers: list[str] = []
    hard_blockers: list[str] = []
    ids = [row.cycle_id for row in evaluations]
    indices = [row.cycle_index for row in evaluations]
    if len(ids) != len(set(ids)) or indices != list(range(len(indices))):
        hard_blockers.append("CYCLE_SEQUENCE_INVALID")
    gaps = (
        [evaluations[0].started_at_ms - start["started_at_ms"]]
        if evaluations
        else []
    )
    gaps.extend(
        current.started_at_ms - prior.started_at_ms
        for prior, current in zip(evaluations, evaluations[1:])
    )
    maximum_gap = max(gaps) if gaps else None
    if maximum_gap is not None and maximum_gap > policy.maximum_cycle_gap_ms:
        hard_blockers.append("CYCLE_GAP_EXCEEDED")
    if any(
        current.started_at_ms <= prior.started_at_ms
        or current.completed_at_ms <= prior.completed_at_ms
        for prior, current in zip(evaluations, evaluations[1:])
    ):
        hard_blockers.append("CYCLE_TIME_SEQUENCE_INVALID")
    if any(
        row.completed_at_ms - row.started_at_ms > policy.maximum_cycle_gap_ms
        for row in evaluations
    ):
        hard_blockers.append("CYCLE_DURATION_EXCEEDED")
    failed = [row for row in evaluations if row.status != "PASS"]
    if len(failed) > policy.thresholds["max_failed_cycle_count"]:
        hard_blockers.append("FAILED_CYCLE_LIMIT_EXCEEDED")
    if not chain_valid:
        hard_blockers.append("SOAK_EVENT_CHAIN_INVALID")
    passed_count = len(evaluations) - len(failed)
    observed_duration = (
        max(row.completed_at_ms for row in evaluations) - start["started_at_ms"]
        if evaluations
        else 0
    )
    if any(row.completed_at_ms > as_of_ms for row in evaluations):
        hard_blockers.append("CYCLE_OBSERVED_AFTER_STATUS_TIME")
    if passed_count < policy.minimum_completed_cycles:
        blockers.append("MINIMUM_COMPLETED_CYCLES_NOT_MET")
    if observed_duration < policy.minimum_duration_ms:
        blockers.append("MINIMUM_DURATION_NOT_MET")
    blockers = list(dict.fromkeys((*hard_blockers, *blockers)))
    if hard_blockers:
        status = "OPERATIONAL_SOAK_BLOCKED"
    elif blockers:
        status = "OPERATIONAL_SOAK_RUNNING"
    else:
        status = "OPERATIONAL_SOAK_PASSED"
    failure_reasons = sorted(
        {reason for row in failed for reason in row.reason_codes}
    )
    base.update(
        status=status,
        run_id=start["run_id"],
        operator_start_id=start["operator_start_id"],
        recorded_cycles=len(evaluations),
        completed_cycles=passed_count,
        failed_cycles=len(failed),
        observed_duration_ms=observed_duration,
        maximum_observed_cycle_gap_ms=maximum_gap,
        blockers=blockers,
        cycle_failure_reasons=failure_reasons,
    )
    return base


__all__ = [
    "AUTHORIZATION_CLASS",
    "CALIBRATION",
    "COMPLETION_EFFECT",
    "CYCLE_KIND",
    "CycleEvaluation",
    "MftSoakError",
    "MftSoakPolicy",
    "MODE",
    "POLICY_KIND",
    "REQUIRED_EVIDENCE_KINDS",
    "SAFETY_FLAGS",
    "START_KIND",
    "VENUE",
    "build_soak_status",
    "evaluate_cycle",
    "parse_soak_policy",
    "validate_start_receipt",
]
