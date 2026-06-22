"""Canonical risk decision contract (blueprint §6.5).

A RiskDecision pairs an outcome with the reasons behind it. Invalid states are
rejected at construction (fail-closed): a REJECTED decision must carry at least
one reason; an APPROVED decision must carry none.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .rejection import RejectionReason
from .hashing import short_hash


class RiskDecisionType(str, Enum):
    APPROVED = "APPROVED"
    APPROVED_WITH_LIMITS = "APPROVED_WITH_LIMITS"
    NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class RiskDecision:
    decision: RiskDecisionType
    reasons: tuple[RejectionReason, ...] = ()

    def __post_init__(self):
        if self.decision == RiskDecisionType.REJECTED and not self.reasons:
            raise ValueError("REJECTED decision must include at least one RejectionReason")
        if self.decision == RiskDecisionType.APPROVED and self.reasons:
            raise ValueError("APPROVED decision must not include rejection reasons")
        for r in self.reasons:
            if not isinstance(r, RejectionReason):
                raise TypeError(f"reason must be RejectionReason, got {type(r).__name__}")

    @property
    def approved(self) -> bool:
        return self.decision in (RiskDecisionType.APPROVED, RiskDecisionType.APPROVED_WITH_LIMITS)

    def to_dict(self) -> dict:
        return {"decision": self.decision.value, "reasons": [r.value for r in self.reasons]}

    @classmethod
    def from_dict(cls, data: dict) -> "RiskDecision":
        return cls(
            decision=RiskDecisionType(data["decision"]),
            reasons=tuple(RejectionReason(r) for r in data.get("reasons", [])),
        )

    @property
    def reproducibility_hash(self) -> str:
        return short_hash(self.to_dict())
