"""Content-bound contracts for the L2 Binance Futures Testnet execution seam.

These values make an execution request deterministic and tamper-evident.  The
hashes are deliberately unkeyed: they are consistency evidence, not an
external identity, signature, mandate, or grant.  Every consumer must still
apply the execution gate, testnet mandate, kill-switch, freshness, and venue
checks before allowing a Testnet side effect.
"""
from __future__ import annotations

from enum import Enum
from math import isfinite
from string import hexdigits
from typing import Iterable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quantos.contracts.hashing import stable_hash
from quantos.contracts.portfolio import TargetPosition


MFT_TESTNET_ENVIRONMENT = "BINANCE_FUTURES_TESTNET"
MFT_TESTNET_INTENT_SCHEMA = "mft-testnet-execution.v1"
MFT_TESTNET_RECEIPT_SCHEMA = "mft-testnet-execution-receipt.v1"
_COMMAND_PREFIX = "mft-testnet-"
_HEX = frozenset(hexdigits)


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be non-empty canonical text")
    return value


def _require_time(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _require_digest(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _HEX for character in value)
        or value != value.lower()
    ):
        raise ValueError(f"{name} must be a lowercase sha256 digest")
    return value


def _canonical_texts(values: Iterable[object], *, name: str) -> tuple[str, ...]:
    materialized = tuple(_require_text(value, name=name) for value in values)
    canonical = tuple(sorted(set(materialized)))
    if not canonical:
        raise ValueError(f"{name} must not be empty")
    if len(canonical) != len(materialized):
        raise ValueError(f"{name} must not contain duplicates")
    return canonical


def _canonical_digests(values: Iterable[object], *, name: str) -> tuple[str, ...]:
    materialized = tuple(_require_digest(value, name=name) for value in values)
    canonical = tuple(sorted(set(materialized)))
    if not canonical:
        raise ValueError(f"{name} must not be empty")
    if len(canonical) != len(materialized):
        raise ValueError(f"{name} must not contain duplicates")
    return canonical


def _canonical_targets(values: Iterable[TargetPosition]) -> tuple[TargetPosition, ...]:
    materialized = tuple(values)
    if not materialized:
        raise ValueError("targets must not be empty")
    if any(not isinstance(target, TargetPosition) for target in materialized):
        raise TypeError("targets must contain TargetPosition values")
    if any(not isfinite(float(target.target_weight)) for target in materialized):
        raise ValueError("target weights must be finite")
    instruments = tuple(target.instrument_id for target in materialized)
    if len(set(instruments)) != len(instruments):
        raise ValueError("targets must contain unique instruments")
    return tuple(sorted(materialized, key=lambda target: target.instrument_id))


def _target_payload(targets: Sequence[TargetPosition]) -> list[dict[str, object]]:
    return [
        {
            "instrument_id": target.instrument_id,
            "target_weight": float(target.target_weight),
        }
        for target in targets
    ]


def _intent_payload(intent: "TestnetExecutionIntent") -> dict[str, object]:
    return {
        "schema_version": intent.schema_version,
        "portfolio_id": intent.portfolio_id,
        "alpha_ids": list(intent.alpha_ids),
        "decided_at_ms": intent.decided_at_ms,
        "expires_at_ms": intent.expires_at_ms,
        "targets": _target_payload(intent.targets),
        "market_watermark_ms": intent.market_watermark_ms,
        "market_snapshot_hash": intent.market_snapshot_hash,
        "workflow_receipt_hash": intent.workflow_receipt_hash,
        "validation_artifact_hashes": list(intent.validation_artifact_hashes),
        "policy_version": intent.policy_version,
        "policy_hash": intent.policy_hash,
        "execution_environment": intent.execution_environment,
        "testnet_submission_requested": intent.testnet_submission_requested,
        "real_capital": intent.real_capital,
        "live_allowed": intent.live_allowed,
        "real_order_submission_allowed": intent.real_order_submission_allowed,
    }


def _command_id(intent_hash: str) -> str:
    return f"{_COMMAND_PREFIX}{intent_hash[:32]}"


def _idempotency_key(intent_hash: str) -> str:
    return stable_hash({"kind": MFT_TESTNET_INTENT_SCHEMA, "intent_hash": intent_hash})


class TestnetExecutionIntent(BaseModel):
    """Immutable Testnet-only command content; never an authority grant."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal["mft-testnet-execution.v1"] = MFT_TESTNET_INTENT_SCHEMA
    command_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    portfolio_id: str = Field(min_length=1)
    alpha_ids: tuple[str, ...] = Field(min_length=1)
    decided_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    targets: tuple[TargetPosition, ...] = Field(min_length=1)
    market_watermark_ms: int = Field(ge=0)
    market_snapshot_hash: str = Field(min_length=64, max_length=64)
    workflow_receipt_hash: str = Field(min_length=64, max_length=64)
    validation_artifact_hashes: tuple[str, ...] = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_hash: str = Field(min_length=64, max_length=64)
    execution_environment: Literal["BINANCE_FUTURES_TESTNET"] = MFT_TESTNET_ENVIRONMENT
    testnet_submission_requested: bool = False
    real_capital: Literal[False] = False
    live_allowed: Literal[False] = False
    real_order_submission_allowed: Literal[False] = False
    intent_hash: str = Field(min_length=64, max_length=64)

    @field_validator(
        "real_capital",
        "live_allowed",
        "real_order_submission_allowed",
        mode="before",
    )
    @classmethod
    def _require_literal_false(cls, value: object) -> bool:
        if value is not False:
            raise ValueError("real-capital authority fields must be literal false")
        return False

    @model_validator(mode="after")
    def _validate_contract(self) -> "TestnetExecutionIntent":
        _require_text(self.portfolio_id, name="portfolio_id")
        if self.alpha_ids != _canonical_texts(self.alpha_ids, name="alpha_ids"):
            raise ValueError("alpha_ids must be canonical")
        if self.targets != _canonical_targets(self.targets):
            raise ValueError("targets must be canonical")
        if self.validation_artifact_hashes != _canonical_digests(
            self.validation_artifact_hashes,
            name="validation_artifact_hashes",
        ):
            raise ValueError("validation_artifact_hashes must be canonical")
        _require_digest(self.market_snapshot_hash, name="market_snapshot_hash")
        _require_digest(self.workflow_receipt_hash, name="workflow_receipt_hash")
        _require_text(self.policy_version, name="policy_version")
        _require_digest(self.policy_hash, name="policy_hash")
        _require_digest(self.intent_hash, name="intent_hash")
        if self.expires_at_ms <= self.decided_at_ms:
            raise ValueError("expires_at_ms must be after decided_at_ms")
        if self.market_watermark_ms > self.decided_at_ms:
            raise ValueError("market_watermark_ms must not be after decided_at_ms")
        expected_hash = stable_hash(_intent_payload(self))
        if self.intent_hash != expected_hash:
            raise ValueError("intent_hash does not bind the intent payload")
        if self.command_id != _command_id(expected_hash):
            raise ValueError("command_id does not bind intent_hash")
        if self.idempotency_key != _idempotency_key(expected_hash):
            raise ValueError("idempotency_key does not bind intent_hash")
        return self


class TestnetIntentVerification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    reasons: tuple[str, ...] = ()
    intent_hash: str | None = None


def create_testnet_execution_intent(
    *,
    portfolio_id: str,
    alpha_ids: Iterable[str],
    decided_at_ms: int,
    expires_at_ms: int,
    targets: Iterable[TargetPosition],
    market_watermark_ms: int,
    market_snapshot_hash: str,
    workflow_receipt_hash: str,
    validation_artifact_hashes: Iterable[str],
    policy_version: str,
    policy_hash: str,
    testnet_submission_requested: bool = False,
) -> TestnetExecutionIntent:
    """Create a canonical Testnet intent with derived command identities."""

    portfolio_id = _require_text(portfolio_id, name="portfolio_id")
    canonical_alphas = _canonical_texts(alpha_ids, name="alpha_ids")
    decided_at_ms = _require_time(decided_at_ms, name="decided_at_ms")
    expires_at_ms = _require_time(expires_at_ms, name="expires_at_ms")
    canonical_targets = _canonical_targets(targets)
    market_watermark_ms = _require_time(market_watermark_ms, name="market_watermark_ms")
    market_snapshot_hash = _require_digest(
        market_snapshot_hash, name="market_snapshot_hash"
    )
    workflow_receipt_hash = _require_digest(
        workflow_receipt_hash, name="workflow_receipt_hash"
    )
    canonical_validation = _canonical_digests(
        validation_artifact_hashes,
        name="validation_artifact_hashes",
    )
    policy_version = _require_text(policy_version, name="policy_version")
    policy_hash = _require_digest(policy_hash, name="policy_hash")
    if not isinstance(testnet_submission_requested, bool):
        raise TypeError("testnet_submission_requested must be bool")

    provisional = TestnetExecutionIntent.model_construct(
        schema_version=MFT_TESTNET_INTENT_SCHEMA,
        command_id="pending",
        idempotency_key="pending",
        portfolio_id=portfolio_id,
        alpha_ids=canonical_alphas,
        decided_at_ms=decided_at_ms,
        expires_at_ms=expires_at_ms,
        targets=canonical_targets,
        market_watermark_ms=market_watermark_ms,
        market_snapshot_hash=market_snapshot_hash,
        workflow_receipt_hash=workflow_receipt_hash,
        validation_artifact_hashes=canonical_validation,
        policy_version=policy_version,
        policy_hash=policy_hash,
        execution_environment=MFT_TESTNET_ENVIRONMENT,
        testnet_submission_requested=testnet_submission_requested,
        real_capital=False,
        live_allowed=False,
        real_order_submission_allowed=False,
        intent_hash="pending",
    )
    intent_hash = stable_hash(_intent_payload(provisional))
    return TestnetExecutionIntent(
        schema_version=MFT_TESTNET_INTENT_SCHEMA,
        command_id=_command_id(intent_hash),
        idempotency_key=_idempotency_key(intent_hash),
        portfolio_id=portfolio_id,
        alpha_ids=canonical_alphas,
        decided_at_ms=decided_at_ms,
        expires_at_ms=expires_at_ms,
        targets=canonical_targets,
        market_watermark_ms=market_watermark_ms,
        market_snapshot_hash=market_snapshot_hash,
        workflow_receipt_hash=workflow_receipt_hash,
        validation_artifact_hashes=canonical_validation,
        policy_version=policy_version,
        policy_hash=policy_hash,
        execution_environment=MFT_TESTNET_ENVIRONMENT,
        testnet_submission_requested=testnet_submission_requested,
        real_capital=False,
        live_allowed=False,
        real_order_submission_allowed=False,
        intent_hash=intent_hash,
    )


def verify_testnet_execution_intent(
    intent: object,
    *,
    now_ms: int,
    expected_workflow_receipt_hash: str | None = None,
    expected_validation_artifact_hashes: Iterable[str] | None = None,
    expected_market_snapshot_hash: str | None = None,
) -> TestnetIntentVerification:
    """Recheck content, safety constants, time, and optional authority lookups."""

    now_ms = _require_time(now_ms, name="now_ms")
    if not isinstance(intent, TestnetExecutionIntent):
        return TestnetIntentVerification(valid=False, reasons=("INVALID_INTENT_TYPE",))
    reasons: list[str] = []
    try:
        expected_hash = stable_hash(_intent_payload(intent))
        if (
            intent.intent_hash != expected_hash
            or intent.command_id != _command_id(expected_hash)
            or intent.idempotency_key != _idempotency_key(expected_hash)
        ):
            reasons.append("INTENT_HASH_MISMATCH")
        if intent.execution_environment != MFT_TESTNET_ENVIRONMENT:
            reasons.append("INVALID_EXECUTION_ENVIRONMENT")
        if intent.real_capital or intent.live_allowed or intent.real_order_submission_allowed:
            reasons.append("REAL_AUTHORITY_FORBIDDEN")
        if now_ms < intent.decided_at_ms:
            reasons.append("DECISION_IN_FUTURE")
        elif now_ms >= intent.expires_at_ms:
            reasons.append("INTENT_EXPIRED")
        if (
            expected_market_snapshot_hash is not None
            and intent.market_snapshot_hash != expected_market_snapshot_hash
        ):
            reasons.append("MARKET_SNAPSHOT_MISMATCH")
        if expected_validation_artifact_hashes is not None:
            try:
                expected = _canonical_digests(
                    expected_validation_artifact_hashes,
                    name="expected_validation_artifact_hashes",
                )
            except (TypeError, ValueError):
                expected = ()
            if intent.validation_artifact_hashes != expected:
                reasons.append("VALIDATION_ARTIFACT_SET_MISMATCH")
        if (
            expected_workflow_receipt_hash is not None
            and intent.workflow_receipt_hash != expected_workflow_receipt_hash
        ):
            reasons.append("WORKFLOW_RECEIPT_MISMATCH")
    except Exception:
        reasons = ["INTENT_MALFORMED"]
    canonical_reasons = tuple(dict.fromkeys(reasons))
    return TestnetIntentVerification(
        valid=not canonical_reasons,
        reasons=canonical_reasons,
        intent_hash=intent.intent_hash,
    )


class TestnetExecutionReceiptStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    IN_FLIGHT_UNKNOWN = "IN_FLIGHT_UNKNOWN"


def _receipt_payload(receipt: "TestnetExecutionReceipt") -> dict[str, object]:
    return {
        "schema_version": receipt.schema_version,
        "command_id": receipt.command_id,
        "intent_hash": receipt.intent_hash,
        "intent_decided_at_ms": receipt.intent_decided_at_ms,
        "status": receipt.status.value,
        "recorded_at_ms": receipt.recorded_at_ms,
        "venue_mode": receipt.venue_mode,
        "oms_event_head": receipt.oms_event_head,
        "ledger_head": receipt.ledger_head,
        "testnet_orders_submitted": receipt.testnet_orders_submitted,
        "error_codes": list(receipt.error_codes),
        "real_capital": receipt.real_capital,
        "live_allowed": receipt.live_allowed,
        "real_order_submission_allowed": receipt.real_order_submission_allowed,
    }


class TestnetExecutionReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal["mft-testnet-execution-receipt.v1"] = MFT_TESTNET_RECEIPT_SCHEMA
    command_id: str = Field(min_length=1)
    intent_hash: str = Field(min_length=64, max_length=64)
    intent_decided_at_ms: int = Field(ge=0)
    status: TestnetExecutionReceiptStatus
    recorded_at_ms: int = Field(ge=0)
    venue_mode: Literal["testnet"] = "testnet"
    oms_event_head: str | None = None
    ledger_head: str | None = None
    testnet_orders_submitted: int = Field(default=0, ge=0)
    error_codes: tuple[str, ...] = ()
    real_capital: Literal[False] = False
    live_allowed: Literal[False] = False
    real_order_submission_allowed: Literal[False] = False
    receipt_hash: str = Field(min_length=64, max_length=64)

    @field_validator(
        "real_capital",
        "live_allowed",
        "real_order_submission_allowed",
        mode="before",
    )
    @classmethod
    def _require_literal_false(cls, value: object) -> bool:
        if value is not False:
            raise ValueError("real-capital authority fields must be literal false")
        return False

    @model_validator(mode="after")
    def _validate_contract(self) -> "TestnetExecutionReceipt":
        _require_text(self.command_id, name="command_id")
        _require_digest(self.intent_hash, name="intent_hash")
        if self.recorded_at_ms < self.intent_decided_at_ms:
            raise ValueError("recorded_at_ms must not predate the intent")
        _require_time(self.testnet_orders_submitted, name="testnet_orders_submitted")
        for name, value in (
            ("oms_event_head", self.oms_event_head),
            ("ledger_head", self.ledger_head),
        ):
            if value is not None:
                _require_digest(value, name=name)
        canonical_errors = tuple(sorted(set(self.error_codes)))
        if self.error_codes != canonical_errors or any(
            not isinstance(code, str) or not code.strip() for code in self.error_codes
        ):
            raise ValueError("error_codes must be unique canonical text")
        if self.status in {
            TestnetExecutionReceiptStatus.BLOCKED,
            TestnetExecutionReceiptStatus.FAILED,
            TestnetExecutionReceiptStatus.IN_FLIGHT_UNKNOWN,
        } and not self.error_codes:
            raise ValueError("non-success receipt requires an error code")
        _require_digest(self.receipt_hash, name="receipt_hash")
        if self.receipt_hash != stable_hash(_receipt_payload(self)):
            raise ValueError("receipt_hash does not bind the receipt payload")
        return self


def create_testnet_execution_receipt(
    *,
    intent: TestnetExecutionIntent,
    status: TestnetExecutionReceiptStatus,
    recorded_at_ms: int,
    oms_event_head: str | None = None,
    ledger_head: str | None = None,
    testnet_orders_submitted: int = 0,
    error_codes: Iterable[str] = (),
) -> TestnetExecutionReceipt:
    if not isinstance(intent, TestnetExecutionIntent):
        raise TypeError("intent must be TestnetExecutionIntent")
    if not isinstance(status, TestnetExecutionReceiptStatus):
        raise TypeError("status must be TestnetExecutionReceiptStatus")
    recorded_at_ms = _require_time(recorded_at_ms, name="recorded_at_ms")
    testnet_orders_submitted = _require_time(
        testnet_orders_submitted,
        name="testnet_orders_submitted",
    )
    if not intent.testnet_submission_requested and testnet_orders_submitted:
        raise ValueError("preview intent cannot report submitted Testnet orders")
    canonical_errors = tuple(sorted(set(error_codes)))
    provisional = TestnetExecutionReceipt.model_construct(
        schema_version=MFT_TESTNET_RECEIPT_SCHEMA,
        command_id=intent.command_id,
        intent_hash=intent.intent_hash,
        intent_decided_at_ms=intent.decided_at_ms,
        status=status,
        recorded_at_ms=recorded_at_ms,
        venue_mode="testnet",
        oms_event_head=oms_event_head,
        ledger_head=ledger_head,
        testnet_orders_submitted=testnet_orders_submitted,
        error_codes=canonical_errors,
        real_capital=False,
        live_allowed=False,
        real_order_submission_allowed=False,
        receipt_hash="pending",
    )
    receipt_hash = stable_hash(_receipt_payload(provisional))
    return TestnetExecutionReceipt(
        schema_version=MFT_TESTNET_RECEIPT_SCHEMA,
        command_id=intent.command_id,
        intent_hash=intent.intent_hash,
        intent_decided_at_ms=intent.decided_at_ms,
        status=status,
        recorded_at_ms=recorded_at_ms,
        venue_mode="testnet",
        oms_event_head=oms_event_head,
        ledger_head=ledger_head,
        testnet_orders_submitted=testnet_orders_submitted,
        error_codes=canonical_errors,
        real_capital=False,
        live_allowed=False,
        real_order_submission_allowed=False,
        receipt_hash=receipt_hash,
    )


__all__ = [
    "MFT_TESTNET_ENVIRONMENT",
    "MFT_TESTNET_INTENT_SCHEMA",
    "MFT_TESTNET_RECEIPT_SCHEMA",
    "TestnetExecutionIntent",
    "TestnetIntentVerification",
    "TestnetExecutionReceiptStatus",
    "TestnetExecutionReceipt",
    "create_testnet_execution_intent",
    "verify_testnet_execution_intent",
    "create_testnet_execution_receipt",
]
