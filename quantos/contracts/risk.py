"""Canonical risk decision contract (blueprint §6.5).

Pydantic v2 model. Invalid states are rejected at construction (fail-closed):
a REJECTED decision must carry at least one reason; an APPROVED decision must
carry none.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator

from .hashing import short_hash
from .rejection import RejectionReason


class RiskDecisionType(str, Enum):
    APPROVED = "APPROVED"
    APPROVED_WITH_LIMITS = "APPROVED_WITH_LIMITS"
    NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"
    REJECTED = "REJECTED"


class RiskDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision: RiskDecisionType
    reasons: tuple[RejectionReason, ...] = ()

    @model_validator(mode="after")
    def _check_invariants(self) -> "RiskDecision":
        if self.decision == RiskDecisionType.REJECTED and not self.reasons:
            raise ValueError("REJECTED decision must include at least one RejectionReason")
        if self.decision == RiskDecisionType.APPROVED and self.reasons:
            raise ValueError("APPROVED decision must not include rejection reasons")
        return self

    @property
    def approved(self) -> bool:
        return self.decision in (RiskDecisionType.APPROVED, RiskDecisionType.APPROVED_WITH_LIMITS)

    def to_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "RiskDecision":
        return cls.model_validate(data)

    @property
    def reproducibility_hash(self) -> str:
        return short_hash(self.to_dict())
