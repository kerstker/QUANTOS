"""Small, deterministic contracts for evidence owned outside QUANTOS.

The repository can verify local facts and define the exact external facts that
are still missing, but it cannot manufacture an IdP, a WORM store, a remote
heartbeat receipt, or an artifact signature.  This module gives those
boundaries one explicit vocabulary and a reusable continuity evaluator.  It
has no I/O, network, signing, credential, or deployment capability.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REASON = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")

EvidenceState = Literal[
    "VERIFIED",
    "PARTIAL",
    "EXTERNAL_BLOCKED",
    "BREACH",
    "UNAVAILABLE",
]
ObservationState = Literal["PASS", "BREACH", "UNAVAILABLE", "EXTERNAL_BLOCKED"]
ContinuityVerdict = Literal[
    "PASS",
    "PARTIAL",
    "BREACH",
    "UNAVAILABLE",
    "EXTERNAL_BLOCKED",
]


class _FrozenContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


class ExternalEvidenceStatus(_FrozenContract):
    """One auditable status for a capability owned by an external authority.

    ``EXTERNAL_BLOCKED`` is deliberately different from ``UNAVAILABLE``:
    the former means the local implementation is present but an external
    prerequisite has not been provisioned or evidenced.  ``VERIFIED`` is
    impossible to construct without both an authority and a retained evidence
    reference, preventing a status-only local fixture from looking complete.
    """

    schema_version: Literal[1] = 1
    capability: str = Field(min_length=1, max_length=255)
    status: EvidenceState
    reason_code: str = Field(min_length=1, max_length=128)
    required_action: str = Field(default="", max_length=2_000)
    observed_at_ms: int = Field(ge=0)
    external_authority: str = Field(default="", max_length=255)
    evidence_ref: str = Field(default="", max_length=1_024)

    @field_validator("reason_code")
    @classmethod
    def _reason_code(cls, value: str) -> str:
        if _REASON.fullmatch(value) is None:
            raise ValueError("reason_code must be an uppercase identifier")
        return value

    @field_validator("observed_at_ms")
    @classmethod
    def _timestamp_not_bool(cls, value: int) -> int:
        if isinstance(value, bool):
            raise ValueError("observed_at_ms must be an integer")
        return value

    @model_validator(mode="after")
    def _require_proof_for_verified(self) -> ExternalEvidenceStatus:
        if self.status == "VERIFIED" and (
            not self.external_authority or not self.evidence_ref
        ):
            raise ValueError(
                "VERIFIED evidence requires an external authority and retained evidence_ref"
            )
        if self.status == "EXTERNAL_BLOCKED" and not self.required_action:
            raise ValueError(
                "EXTERNAL_BLOCKED evidence requires a concrete required_action"
            )
        return self


def build_external_blocked_status(
    *,
    capability: str,
    reason_code: str,
    required_action: str,
    observed_at_ms: int,
    external_authority: str = "",
) -> ExternalEvidenceStatus:
    """Build an honest external blocker without claiming a missing proof."""

    return ExternalEvidenceStatus(
        capability=capability,
        status="EXTERNAL_BLOCKED",
        reason_code=reason_code,
        required_action=required_action,
        observed_at_ms=observed_at_ms,
        external_authority=external_authority,
    )


class ContinuityObservation(_FrozenContract):
    """One timestamped, already-verified local or external observation."""

    schema_version: Literal[1] = 1
    observed_at_ms: int = Field(ge=0)
    status: ObservationState
    reason_code: str = Field(default="", max_length=128)
    evidence_sha256: str = Field(default="", max_length=64)

    @field_validator("observed_at_ms")
    @classmethod
    def _timestamp_not_bool(cls, value: int) -> int:
        if isinstance(value, bool):
            raise ValueError("observed_at_ms must be an integer")
        return value

    @field_validator("reason_code")
    @classmethod
    def _optional_reason_code(cls, value: str) -> str:
        if value and _REASON.fullmatch(value) is None:
            raise ValueError("reason_code must be an uppercase identifier")
        return value

    @field_validator("evidence_sha256")
    @classmethod
    def _optional_sha256(cls, value: str) -> str:
        if value and _SHA256.fullmatch(value) is None:
            raise ValueError("evidence_sha256 must be a lowercase SHA-256 digest")
        return value


class ContinuityAssessment(_FrozenContract):
    """Result of evaluating a bounded, timestamped evidence window."""

    schema_version: Literal[1] = 1
    verdict: ContinuityVerdict
    reason_codes: tuple[str, ...] = ()
    observation_count: int = Field(ge=0)
    pass_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    unavailable_count: int = Field(ge=0)
    breach_count: int = Field(ge=0)
    history_span_ms: int = Field(ge=0)
    maximum_gap_ms: int | None = Field(default=None, ge=0)
    latest_age_ms: int | None = Field(default=None, ge=0)

    @field_validator("reason_codes")
    @classmethod
    def _reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(_REASON.fullmatch(item) is None for item in value):
            raise ValueError("reason_codes must be unique uppercase identifiers")
        return value


def _nonnegative_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _positive_int(name: str, value: int) -> None:
    _nonnegative_int(name, value)
    if value == 0:
        raise ValueError(f"{name} must be positive")


def evaluate_continuity(
    observations: Iterable[ContinuityObservation],
    *,
    now_ms: int,
    max_gap_ms: int,
    minimum_history_span_ms: int,
    minimum_passes: int,
    clean_lookback_ms: int,
) -> ContinuityAssessment:
    """Evaluate cadence without mutating or consulting an external system.

    The input order is part of the evidence contract.  A timestamp rollback or
    duplicate is a breach, even when sorting the same values would otherwise
    produce a passing window.  External blockers take precedence over ordinary
    insufficiency, while an actual breach always remains the most severe state.
    """

    _nonnegative_int("now_ms", now_ms)
    _positive_int("max_gap_ms", max_gap_ms)
    _nonnegative_int("minimum_history_span_ms", minimum_history_span_ms)
    _positive_int("minimum_passes", minimum_passes)
    _positive_int("clean_lookback_ms", clean_lookback_ms)
    items = tuple(observations)
    if any(not isinstance(item, ContinuityObservation) for item in items):
        raise TypeError("observations must contain ContinuityObservation values")
    if not items:
        return ContinuityAssessment(
            verdict="UNAVAILABLE",
            reason_codes=("NO_OBSERVATIONS",),
            observation_count=0,
            pass_count=0,
            blocked_count=0,
            unavailable_count=0,
            breach_count=0,
            history_span_ms=0,
        )

    reasons: list[str] = []

    def add(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    timestamps = tuple(item.observed_at_ms for item in items)
    if any(right <= left for left, right in zip(timestamps, timestamps[1:])):
        add("OBSERVATIONS_NOT_MONOTONIC")
    ordered = tuple(sorted(items, key=lambda item: item.observed_at_ms))
    latest = ordered[-1]
    first = ordered[0]
    latest_age = now_ms - latest.observed_at_ms
    history_span = latest.observed_at_ms - first.observed_at_ms
    gaps = tuple(
        right.observed_at_ms - left.observed_at_ms
        for left, right in zip(ordered, ordered[1:])
    )
    maximum_gap = max(gaps, default=0)
    if any(item.observed_at_ms > now_ms for item in items):
        add("OBSERVATION_FROM_FUTURE")
    if latest_age > max_gap_ms:
        add("LATEST_OBSERVATION_STALE")
    if maximum_gap > max_gap_ms:
        add("OBSERVATION_GAP_EXCEEDED")
    if history_span < minimum_history_span_ms:
        add("HISTORY_SPAN_INSUFFICIENT")

    pass_count = sum(item.status == "PASS" for item in items)
    blocked_count = sum(item.status == "EXTERNAL_BLOCKED" for item in items)
    unavailable_count = sum(item.status == "UNAVAILABLE" for item in items)
    breach_count = sum(item.status == "BREACH" for item in items)
    if breach_count:
        add("EVIDENCE_BREACH")
    if blocked_count:
        add("EXTERNAL_EVIDENCE_BLOCKED")
    if unavailable_count:
        add("EVIDENCE_UNAVAILABLE")
    if pass_count < minimum_passes:
        add("PASS_COUNT_INSUFFICIENT")
    clean_floor = now_ms - clean_lookback_ms
    if any(
        item.status == "BREACH" and item.observed_at_ms >= clean_floor
        for item in items
    ):
        add("BREACH_IN_CLEAN_LOOKBACK")

    severe_breach = any(
        code
        in {
            "OBSERVATIONS_NOT_MONOTONIC",
            "OBSERVATION_FROM_FUTURE",
            "LATEST_OBSERVATION_STALE",
            "OBSERVATION_GAP_EXCEEDED",
            "EVIDENCE_BREACH",
            "BREACH_IN_CLEAN_LOOKBACK",
        }
        for code in reasons
    )
    if severe_breach:
        verdict: ContinuityVerdict = "BREACH"
    elif blocked_count:
        verdict = "EXTERNAL_BLOCKED"
    elif unavailable_count:
        verdict = "UNAVAILABLE"
    elif "HISTORY_SPAN_INSUFFICIENT" in reasons or pass_count < minimum_passes:
        verdict = "PARTIAL"
    else:
        verdict = "PASS"
    return ContinuityAssessment(
        verdict=verdict,
        reason_codes=tuple(reasons),
        observation_count=len(items),
        pass_count=pass_count,
        blocked_count=blocked_count,
        unavailable_count=unavailable_count,
        breach_count=breach_count,
        history_span_ms=history_span,
        maximum_gap_ms=maximum_gap,
        latest_age_ms=max(0, latest_age),
    )


__all__ = [
    "ContinuityAssessment",
    "ContinuityObservation",
    "EvidenceState",
    "ExternalEvidenceStatus",
    "build_external_blocked_status",
    "evaluate_continuity",
]
