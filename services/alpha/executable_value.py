"""Fail-closed executable-value scoring for Alpha research candidates.

This module ranks *research evidence*, not trading permission.  It deliberately
uses conservative incremental dollars instead of a standalone Sharpe ratio and
has no dependency on lifecycle, deployment, credentials, or order submission.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
import math
import re
from typing import Mapping, Sequence

from quantos.contracts.hashing import stable_hash


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_EVIDENCE = (
    "incremental_net_return",
    "double_cost_incremental_net_return",
    "orthogonality",
    "execution",
    "capacity",
    "turnover",
    "tail_risk",
    "independent_bets",
)


class ExecutableValueStatus(str, Enum):
    INCOMPLETE = "INCOMPLETE"
    FAIL = "FAIL"
    PASS = "PASS"


def _finite_number(name: str, value: object, *, allow_none: bool = False) -> None:
    if value is None and allow_none:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    if not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True)
class ExecutableValuePolicy:
    min_incremental_net_return: float = 0.0
    min_double_cost_incremental_net_return: float = 0.0
    min_orthogonality: float = 0.50
    min_execution_confidence: float = 0.50
    min_capacity_to_aum_ratio: float = 1.0
    max_tail_loss_fraction: float = 0.20
    min_independent_bet_count: int = 100
    turnover_scale: float = 24.0
    tail_loss_scale: float = 0.20
    policy_version: str = "executable_value.v1"

    def __post_init__(self) -> None:
        for name in (
            "min_incremental_net_return",
            "min_double_cost_incremental_net_return",
            "min_orthogonality",
            "min_execution_confidence",
            "min_capacity_to_aum_ratio",
            "max_tail_loss_fraction",
            "turnover_scale",
            "tail_loss_scale",
        ):
            _finite_number(name, getattr(self, name))
        if not 0.0 <= self.min_orthogonality <= 1.0:
            raise ValueError("min_orthogonality must be in [0, 1]")
        if not 0.0 <= self.min_execution_confidence <= 1.0:
            raise ValueError("min_execution_confidence must be in [0, 1]")
        if self.min_capacity_to_aum_ratio <= 0.0:
            raise ValueError("min_capacity_to_aum_ratio must be positive")
        if self.max_tail_loss_fraction < 0.0:
            raise ValueError("max_tail_loss_fraction must be non-negative")
        if self.turnover_scale <= 0.0 or self.tail_loss_scale <= 0.0:
            raise ValueError("penalty scales must be positive")
        if (
            isinstance(self.min_independent_bet_count, bool)
            or not isinstance(self.min_independent_bet_count, int)
            or self.min_independent_bet_count <= 0
        ):
            raise ValueError("min_independent_bet_count must be a positive integer")
        if not isinstance(self.policy_version, str) or not self.policy_version.strip():
            raise ValueError("policy_version must be non-empty")

    @property
    def content_hash(self) -> str:
        return stable_hash(asdict(self))


@dataclass(frozen=True)
class ExecutableValueInputs:
    candidate_id: str
    reference_aum: float
    incremental_net_return: float | None
    double_cost_incremental_net_return: float | None
    orthogonality: float | None
    execution_confidence: float | None
    capacity_low: float | None
    annual_turnover: float | None
    tail_loss_fraction: float | None
    independent_bet_count: int | None
    evidence_hashes: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not self.candidate_id.strip():
            raise ValueError("candidate_id must be non-empty")
        _finite_number("reference_aum", self.reference_aum)
        if self.reference_aum <= 0.0:
            raise ValueError("reference_aum must be positive")
        for name in (
            "incremental_net_return",
            "double_cost_incremental_net_return",
            "orthogonality",
            "execution_confidence",
            "capacity_low",
            "annual_turnover",
            "tail_loss_fraction",
        ):
            _finite_number(name, getattr(self, name), allow_none=True)
        for name in ("orthogonality", "execution_confidence"):
            value = getattr(self, name)
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.capacity_low is not None and self.capacity_low <= 0.0:
            raise ValueError("capacity_low must be positive")
        if self.annual_turnover is not None and self.annual_turnover < 0.0:
            raise ValueError("annual_turnover must be non-negative")
        if self.tail_loss_fraction is not None and self.tail_loss_fraction < 0.0:
            raise ValueError("tail_loss_fraction must be non-negative")
        if self.independent_bet_count is not None and (
            isinstance(self.independent_bet_count, bool)
            or not isinstance(self.independent_bet_count, int)
            or self.independent_bet_count < 0
        ):
            raise ValueError("independent_bet_count must be a non-negative integer")
        if not isinstance(self.evidence_hashes, Mapping):
            raise TypeError("evidence_hashes must be a mapping")
        for key, value in self.evidence_hashes.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("evidence hash keys must be non-empty")
            if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
                raise ValueError("evidence hashes must be lowercase sha256 values")


@dataclass(frozen=True)
class ExecutableValueResult:
    candidate_id: str
    status: ExecutableValueStatus
    reasons: tuple[str, ...]
    conservative_incremental_profit_usd: float | None
    capacity_multiplier: float | None
    turnover_penalty: float | None
    tail_penalty: float | None
    score_usd: float | None
    policy_version: str
    policy_hash: str
    evidence_hashes: Mapping[str, str]
    rank: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "status": self.status.value,
            "reasons": list(self.reasons),
            "conservative_incremental_profit_usd": self.conservative_incremental_profit_usd,
            "capacity_multiplier": self.capacity_multiplier,
            "turnover_penalty": self.turnover_penalty,
            "tail_penalty": self.tail_penalty,
            "score_usd": self.score_usd,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "evidence_hashes": dict(sorted(self.evidence_hashes.items())),
            "rank": self.rank,
        }


def evaluate_executable_value(
    inputs: ExecutableValueInputs,
    policy: ExecutableValuePolicy,
) -> ExecutableValueResult:
    """Evaluate a candidate conservatively; missing evidence is never imputed."""
    missing: list[str] = []
    field_reason = {
        "incremental_net_return": "INCREMENTAL_NET_RETURN_MISSING",
        "double_cost_incremental_net_return": "DOUBLE_COST_INCREMENTAL_NET_RETURN_MISSING",
        "orthogonality": "ORTHOGONALITY_MISSING",
        "execution_confidence": "EXECUTION_CONFIDENCE_MISSING",
        "capacity_low": "CAPACITY_MISSING",
        "annual_turnover": "TURNOVER_MISSING",
        "tail_loss_fraction": "TAIL_RISK_MISSING",
        "independent_bet_count": "INDEPENDENT_BET_COUNT_MISSING",
    }
    for field, reason in field_reason.items():
        if getattr(inputs, field) is None:
            missing.append(reason)
    for name in _REQUIRED_EVIDENCE:
        if name not in inputs.evidence_hashes:
            missing.append(f"{name.upper()}_EVIDENCE_HASH_MISSING")
    if missing:
        return ExecutableValueResult(
            candidate_id=inputs.candidate_id,
            status=ExecutableValueStatus.INCOMPLETE,
            reasons=tuple(sorted(missing)),
            conservative_incremental_profit_usd=None,
            capacity_multiplier=None,
            turnover_penalty=None,
            tail_penalty=None,
            score_usd=None,
            policy_version=policy.policy_version,
            policy_hash=policy.content_hash,
            evidence_hashes=dict(sorted(inputs.evidence_hashes.items())),
        )

    incremental_return = float(inputs.incremental_net_return)  # type: ignore[arg-type]
    stressed_return = float(inputs.double_cost_incremental_net_return)  # type: ignore[arg-type]
    orthogonality = float(inputs.orthogonality)  # type: ignore[arg-type]
    execution_confidence = float(inputs.execution_confidence)  # type: ignore[arg-type]
    capacity_low = float(inputs.capacity_low)  # type: ignore[arg-type]
    annual_turnover = float(inputs.annual_turnover)  # type: ignore[arg-type]
    tail_loss = float(inputs.tail_loss_fraction)  # type: ignore[arg-type]
    bet_count = int(inputs.independent_bet_count)  # type: ignore[arg-type]

    conservative_profit = inputs.reference_aum * min(incremental_return, stressed_return)
    capacity_ratio = capacity_low / inputs.reference_aum
    capacity_multiplier = min(1.0, capacity_ratio)
    turnover_penalty = 1.0 + annual_turnover / policy.turnover_scale
    tail_penalty = 1.0 + tail_loss / policy.tail_loss_scale
    score = (
        conservative_profit
        * orthogonality
        * execution_confidence
        * capacity_multiplier
        / (turnover_penalty * tail_penalty)
    )

    failures: list[str] = []
    if incremental_return <= policy.min_incremental_net_return:
        failures.append("INCREMENTAL_PROFIT_NON_POSITIVE")
    if stressed_return <= policy.min_double_cost_incremental_net_return:
        failures.append("DOUBLE_COST_INCREMENTAL_PROFIT_NON_POSITIVE")
    if orthogonality < policy.min_orthogonality:
        failures.append("ORTHOGONALITY_BELOW_MIN")
    if execution_confidence < policy.min_execution_confidence:
        failures.append("EXECUTION_CONFIDENCE_BELOW_MIN")
    if capacity_ratio < policy.min_capacity_to_aum_ratio:
        failures.append("CAPACITY_RATIO_BELOW_MIN")
    if tail_loss > policy.max_tail_loss_fraction:
        failures.append("TAIL_LOSS_ABOVE_MAX")
    if bet_count < policy.min_independent_bet_count:
        failures.append("INDEPENDENT_BET_COUNT_BELOW_MIN")

    return ExecutableValueResult(
        candidate_id=inputs.candidate_id,
        status=(ExecutableValueStatus.FAIL if failures else ExecutableValueStatus.PASS),
        reasons=tuple(sorted(failures)),
        conservative_incremental_profit_usd=conservative_profit,
        capacity_multiplier=capacity_multiplier,
        turnover_penalty=turnover_penalty,
        tail_penalty=tail_penalty,
        score_usd=score,
        policy_version=policy.policy_version,
        policy_hash=policy.content_hash,
        evidence_hashes=dict(sorted(inputs.evidence_hashes.items())),
    )


def rank_executable_candidates(
    results: Sequence[ExecutableValueResult],
) -> tuple[ExecutableValueResult, ...]:
    """Rank only passing evidence; failed/incomplete rows remain visible below it."""
    passing = sorted(
        (item for item in results if item.status is ExecutableValueStatus.PASS),
        key=lambda item: (-(item.score_usd or 0.0), item.candidate_id),
    )
    ranked = [replace(item, rank=index) for index, item in enumerate(passing, 1)]
    failed = sorted(
        (item for item in results if item.status is ExecutableValueStatus.FAIL),
        key=lambda item: item.candidate_id,
    )
    incomplete = sorted(
        (item for item in results if item.status is ExecutableValueStatus.INCOMPLETE),
        key=lambda item: item.candidate_id,
    )
    return tuple(ranked + failed + incomplete)


__all__ = [
    "ExecutableValueInputs",
    "ExecutableValuePolicy",
    "ExecutableValueResult",
    "ExecutableValueStatus",
    "evaluate_executable_value",
    "rank_executable_candidates",
]
