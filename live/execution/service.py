"""Single-writer Binance Futures Testnet execution service.

The service owns the final revalidation boundary and delegates order mechanics
to :func:`live.pipeline.submit_live_batch`.  It never creates a mainnet venue,
never signs a mandate, and never retries an ambiguous submission.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from live.execution.command_store import (
    ExecutionCommandRecord,
    ExecutionCommandState,
    SqliteExecutionCommandStore,
)
from live.execution.order_recovery import (
    RecoveryState,
    SqliteOrderRecoveryStore,
    resolve_unknown_orders,
)
from live.governance.mandate import AUTO, Mandate
from live.governance.validation_attestation import (
    VALIDATION_PASS,
    ValidationAttestation,
    require_validation_attestation,
)
from live.pipeline import LiveBatchResult, submit_live_batch
from quantos.contracts.mft_execution import (
    TestnetExecutionIntent,
    verify_testnet_execution_intent,
)
from quantos.contracts.hashing import stable_hash


_HEX64 = re.compile(r"[0-9a-f]{64}")
_REQUIRED_PIPELINE_INPUTS = frozenset(
    {"prices", "aum", "current_positions", "gate", "ctx_factory", "ledger", "audit"}
)
_RESERVED_PIPELINE_INPUTS = frozenset(
    {
        "weights",
        "venue",
        "mandate",
        "validation_attestation",
        "strategy_id",
        "decided_ts",
        "now",
        "unknown_order_store",
        "startup_recovery",
    }
)
_KNOWN_PIPELINE_REASON_STEMS = frozenset(
    {
        "account_gross_notional_exceeded",
        "account_net_notional_exceeded",
        "account_position_unpriced",
        "evaluation_error",
        "exchange_filters_failed",
        "exchange_mismatch",
        "exchange_unset",
        "gross_notional_exceeded",
        "instrument_not_allowed",
        "limit_price_missing",
        "mandate_expired",
        "mandate_missing",
        "net_notional_exceeded",
        "no_price",
        "non_finite_mandate_field",
        "order_retry_authorization_required",
        "order_submission_unknown",
        "per_instrument_cap_exceeded",
        "single_order_notional_exceeded",
        "startup_recovery_not_ready",
        "unknown_order_type",
        "validation_attestation",
        "venue_mismatch",
    }
)


class TestnetExecutionServiceError(RuntimeError):
    """The execution service cannot establish a safe processing state."""


def _time(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be non-empty canonical text")
    return value


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase sha256 digest")
    return value


def _digests(values: Iterable[str], name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must be a digest collection")
    materialized = tuple(_digest(value, name) for value in values)
    canonical = tuple(sorted(set(materialized)))
    if not canonical or len(canonical) != len(materialized):
        raise ValueError(f"{name} must be non-empty and unique")
    return canonical


def _safe_error_code(prefix: str, exc: BaseException) -> str:
    error_class = re.sub(r"[^A-Za-z0-9]+", "_", type(exc).__name__).upper()
    return f"{prefix}_{error_class}"[:128]


def _safe_reason_code(value: object) -> str:
    raw = str(value)
    stem = raw.split(":", 1)[0]
    if stem not in _KNOWN_PIPELINE_REASON_STEMS:
        return "UNCLASSIFIED_PIPELINE_REASON"
    return stem.upper()


@dataclass(frozen=True, slots=True)
class ExecutionAuthoritySnapshot:
    workflow_receipt_hash: str
    validation_artifact_hashes: tuple[str, ...]
    market_snapshot_hash: str
    market_watermark_ms: int
    market_contiguous: bool
    policy_version: str
    policy_hash: str
    kill_switch_tripped: bool
    mandate: Mandate | None
    validation_attestation: ValidationAttestation | None

    def __post_init__(self) -> None:
        _digest(self.workflow_receipt_hash, "workflow_receipt_hash")
        canonical = _digests(
            self.validation_artifact_hashes, "validation_artifact_hash"
        )
        if self.validation_artifact_hashes != canonical:
            raise ValueError("validation_artifact_hashes must be canonical")
        _digest(self.market_snapshot_hash, "market_snapshot_hash")
        _time(self.market_watermark_ms, "market_watermark_ms")
        if type(self.market_contiguous) is not bool:
            raise TypeError("market_contiguous must be bool")
        _text(self.policy_version, "policy_version")
        _digest(self.policy_hash, "policy_hash")
        if type(self.kill_switch_tripped) is not bool:
            raise TypeError("kill_switch_tripped must be bool")
        if self.mandate is not None and not isinstance(self.mandate, Mandate):
            raise TypeError("mandate must be a Mandate")
        if self.validation_attestation is not None and not isinstance(
            self.validation_attestation, ValidationAttestation
        ):
            raise TypeError("validation_attestation must be a ValidationAttestation")


@dataclass(frozen=True, slots=True)
class ExecutionServiceRun:
    status: str
    command: ExecutionCommandRecord | None = None
    recovery_report: Mapping[str, object] | None = None


AuthorityProvider = Callable[
    [TestnetExecutionIntent, int], ExecutionAuthoritySnapshot
]
PipelineInputsProvider = Callable[
    [TestnetExecutionIntent, int], Mapping[str, object]
]
PipelineSubmitter = Callable[..., LiveBatchResult]


class TestnetExecutionService:
    """Process at most one durable Testnet command under a fenced owner."""

    def __init__(
        self,
        command_store: SqliteExecutionCommandStore,
        *,
        recovery_store: SqliteOrderRecoveryStore,
        venue: object,
        authority_provider: AuthorityProvider,
        pipeline_inputs_provider: PipelineInputsProvider,
        pipeline_submitter: PipelineSubmitter = submit_live_batch,
        max_market_age_ms: int,
        lease_ms: int,
        kill_switch_arm: Callable[[str, int], object] | None = None,
    ) -> None:
        if not isinstance(command_store, SqliteExecutionCommandStore):
            raise TypeError("command_store must be a SqliteExecutionCommandStore")
        if not isinstance(recovery_store, SqliteOrderRecoveryStore):
            raise TypeError("recovery_store must be a SqliteOrderRecoveryStore")
        if not callable(authority_provider):
            raise TypeError("authority_provider must be callable")
        if not callable(pipeline_inputs_provider):
            raise TypeError("pipeline_inputs_provider must be callable")
        if not callable(pipeline_submitter):
            raise TypeError("pipeline_submitter must be callable")
        self._commands = command_store
        self._recovery = recovery_store
        self._venue = venue
        self._authority_provider = authority_provider
        self._pipeline_inputs_provider = pipeline_inputs_provider
        self._pipeline_submitter = pipeline_submitter
        self._max_market_age_ms = _time(max_market_age_ms, "max_market_age_ms")
        self._lease_ms = _time(lease_ms, "lease_ms")
        if self._lease_ms <= 0:
            raise ValueError("lease_ms must be positive")
        if kill_switch_arm is not None and not callable(kill_switch_arm):
            raise TypeError("kill_switch_arm must be callable")
        self._kill_switch_arm = kill_switch_arm

    def enqueue(
        self, intent: TestnetExecutionIntent, *, recorded_at_ms: int
    ) -> ExecutionCommandRecord:
        return self._commands.enqueue(intent, recorded_at_ms=recorded_at_ms)

    def _recover_before_new_risk(
        self, now_ms: int, *, query_venue: bool
    ) -> tuple[bool, dict[str, object]]:
        expired = self._commands.mark_expired_in_flight_unknown(now_ms=now_ms)
        report: dict[str, object] = {
            "expired_command_ids": [record.command_id for record in expired],
            "outcomes": [],
            "blocked_deployments": [],
            "retryable_orders": [],
        }
        if query_venue and self._recovery.pending():
            recovered = resolve_unknown_orders(
                self._recovery,
                self._venue,
                now_ms=now_ms,
                kill_switch_arm=self._kill_switch_arm,
            )
            report.update(recovered)
        resolved_commands = self._resolve_command_unknowns(now_ms)
        report["resolved_command_ids"] = [
            record.command_id for record in resolved_commands
        ]
        blocked = bool(
            self._commands.by_state(ExecutionCommandState.UNKNOWN)
            or self._recovery.pending()
        )
        return not blocked, report

    def _resolve_command_unknowns(
        self, now_ms: int
    ) -> tuple[ExecutionCommandRecord, ...]:
        resolved: list[ExecutionCommandRecord] = []
        for command in self._commands.by_state(ExecutionCommandState.UNKNOWN):
            raw_ids = command.result.get("recovery_order_ids", ())
            if (
                not isinstance(raw_ids, list)
                or not raw_ids
                or any(not isinstance(value, str) or not value for value in raw_ids)
            ):
                continue
            records = tuple(self._recovery.latest(value) for value in raw_ids)
            if any(record is None for record in records):
                continue
            concrete = tuple(record for record in records if record is not None)
            if any(
                record.state
                in {
                    RecoveryState.ORDER_SUBMISSION_UNKNOWN,
                    RecoveryState.FOUND,
                    RecoveryState.INDETERMINATE,
                    RecoveryState.KILL_SWITCHED,
                }
                for record in concrete
            ):
                continue
            evidence = {
                "command_id": command.command_id,
                "orders": [
                    {
                        "client_order_id": record.client_order_id,
                        "state": record.state.value,
                        "observed_status": record.observed_status,
                        "record_hash": record.record_hash,
                    }
                    for record in concrete
                ],
            }
            evidence_hash = stable_hash(evidence)
            if all(record.state is RecoveryState.RESOLVED for record in concrete):
                resolved.append(
                    self._commands.resolve_unknown(
                        command.command_id,
                        state=ExecutionCommandState.COMPLETED,
                        recorded_at_ms=now_ms,
                        recovery_evidence_hash=evidence_hash,
                        result={"recovery": evidence},
                    )
                )
            else:
                resolved.append(
                    self._commands.resolve_unknown(
                        command.command_id,
                        state=ExecutionCommandState.BLOCKED,
                        recorded_at_ms=now_ms,
                        recovery_evidence_hash=evidence_hash,
                        reason_codes=("RECOVERY_REQUIRES_EXPLICIT_RETRY",),
                        result={"recovery": evidence},
                    )
                )
        return tuple(resolved)

    def _blocking_recovery_order_ids(self, deployment_id: str) -> list[str]:
        return sorted(
            record.client_order_id
            for record in self._recovery.pending(deployment_id=deployment_id)
        )

    def _load_authority(
        self, intent: TestnetExecutionIntent, now_ms: int
    ) -> tuple[ExecutionAuthoritySnapshot | None, tuple[str, ...]]:
        try:
            snapshot = self._authority_provider(intent, now_ms)
        except Exception as exc:
            return None, (_safe_error_code("AUTHORITY_ERROR", exc),)
        if not isinstance(snapshot, ExecutionAuthoritySnapshot):
            return None, ("AUTHORITY_UNAVAILABLE",)
        try:
            reasons = self._preflight_reasons(intent, snapshot, now_ms)
        except Exception as exc:
            return None, (_safe_error_code("AUTHORITY_VALIDATION_ERROR", exc),)
        return snapshot, reasons

    def _preflight_reasons(
        self,
        intent: TestnetExecutionIntent,
        snapshot: ExecutionAuthoritySnapshot,
        now_ms: int,
    ) -> tuple[str, ...]:
        verification = verify_testnet_execution_intent(
            intent,
            now_ms=now_ms,
            expected_workflow_receipt_hash=snapshot.workflow_receipt_hash,
            expected_validation_artifact_hashes=snapshot.validation_artifact_hashes,
            expected_market_snapshot_hash=snapshot.market_snapshot_hash,
        )
        reasons = list(verification.reasons)
        if snapshot.market_watermark_ms != intent.market_watermark_ms:
            reasons.append("MARKET_WATERMARK_MISMATCH")
        if snapshot.policy_version != intent.policy_version:
            reasons.append("POLICY_VERSION_MISMATCH")
        if snapshot.policy_hash != intent.policy_hash:
            reasons.append("POLICY_HASH_MISMATCH")
        if not snapshot.market_contiguous:
            reasons.append("MARKET_GAP_BLOCKED")
        if (
            now_ms < intent.market_watermark_ms
            or now_ms - intent.market_watermark_ms > self._max_market_age_ms
        ):
            reasons.append("MARKET_WATERMARK_STALE")
        if snapshot.kill_switch_tripped:
            reasons.append("KILL_SWITCH_TRIPPED")
        if (
            getattr(self._venue, "venue_mode", None) != "testnet"
            or getattr(self._venue, "venue_name", None) != "binance"
        ):
            reasons.append("VENUE_NOT_BINANCE_TESTNET")
        reasons.extend(self._mandate_reasons(intent, snapshot.mandate, now_ms))
        reasons.extend(
            self._attestation_reasons(
                intent, snapshot.validation_attestation, now_ms
            )
        )
        return tuple(dict.fromkeys(sorted(reasons)))

    @staticmethod
    def _mandate_reasons(
        intent: TestnetExecutionIntent,
        mandate: Mandate | None,
        now_ms: int,
    ) -> tuple[str, ...]:
        if mandate is None:
            return ("MANDATE_MISSING",)
        reasons: list[str] = []
        if mandate.venue != "testnet" or mandate.exchange != "binance":
            reasons.append("MANDATE_VENUE_MISMATCH")
        if (
            not isinstance(mandate.mandate_id, str)
            or not mandate.mandate_id.strip()
            or mandate.mandate_id != mandate.mandate_id.strip()
        ):
            reasons.append("MANDATE_ID_INVALID")
        if (
            not isinstance(mandate.approved_by, tuple)
            or not mandate.approved_by
            or any(
                not isinstance(actor, str)
                or not actor.strip()
                or actor != actor.strip()
                for actor in mandate.approved_by
            )
        ):
            reasons.append("MANDATE_HUMAN_APPROVAL_MISSING")
        if (
            isinstance(mandate.issued_at, bool)
            or not isinstance(mandate.issued_at, int)
            or mandate.issued_at < 0
        ):
            reasons.append("MANDATE_ISSUED_AT_INVALID")
        elif mandate.issued_at > now_ms:
            reasons.append("MANDATE_ISSUED_IN_FUTURE")
        if mandate.expires_at is None:
            reasons.append("MANDATE_EXPIRY_MISSING")
        elif (
            isinstance(mandate.expires_at, bool)
            or not isinstance(mandate.expires_at, int)
            or mandate.expires_at < 0
        ):
            reasons.append("MANDATE_EXPIRY_INVALID")
        elif now_ms >= mandate.expires_at:
            reasons.append("MANDATE_EXPIRED")
        target_instruments = {target.instrument_id for target in intent.targets}
        if (
            not isinstance(mandate.allowed_instruments, frozenset)
            or any(
                not isinstance(instrument, str)
                or not instrument.strip()
                or instrument != instrument.strip()
                for instrument in mandate.allowed_instruments
            )
        ):
            reasons.append("MANDATE_INSTRUMENT_SCOPE_INVALID")
        elif not target_instruments.issubset(mandate.allowed_instruments):
            reasons.append("MANDATE_INSTRUMENT_SCOPE_MISMATCH")
        numeric_limits = (
            mandate.max_single_order_notional,
            mandate.max_gross_notional,
            mandate.max_net_notional,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) <= 0
            for value in numeric_limits
        ):
            reasons.append("MANDATE_LIMITS_INVALID")
        per_instrument = mandate.per_instrument_max_notional
        invalid_per_instrument = not isinstance(per_instrument, tuple)
        seen: set[str] = set()
        if not invalid_per_instrument:
            for item in per_instrument:
                if not isinstance(item, tuple) or len(item) != 2:
                    invalid_per_instrument = True
                    break
                instrument, limit = item
                if (
                    not isinstance(instrument, str)
                    or not instrument.strip()
                    or instrument != instrument.strip()
                    or instrument in seen
                    or isinstance(limit, bool)
                    or not isinstance(limit, (int, float))
                    or not math.isfinite(float(limit))
                    or float(limit) <= 0
                ):
                    invalid_per_instrument = True
                    break
                seen.add(instrument)
        if invalid_per_instrument:
            reasons.append("MANDATE_PER_INSTRUMENT_LIMITS_INVALID")
        return tuple(reasons)

    @staticmethod
    def _attestation_reasons(
        intent: TestnetExecutionIntent,
        attestation: ValidationAttestation | None,
        now_ms: int,
    ) -> tuple[str, ...]:
        mapping = (
            {intent.portfolio_id: attestation}
            if attestation is not None
            else None
        )
        reason = require_validation_attestation(intent.portfolio_id, mapping)
        if reason is not None:
            return ("VALIDATION_ATTESTATION_BLOCKED",)
        assert attestation is not None
        reasons: list[str] = []
        if attestation.kind != VALIDATION_PASS:
            reasons.append("WAVE1_VALIDATION_ATTESTATION_REQUIRED")
        if attestation.attested_at > now_ms:
            reasons.append("VALIDATION_ATTESTATION_IN_FUTURE")
        if attestation.validation_result_hash not in intent.validation_artifact_hashes:
            reasons.append("VALIDATION_ATTESTATION_REF_MISMATCH")
        return tuple(reasons)

    def _load_pipeline_inputs(
        self, intent: TestnetExecutionIntent, now_ms: int
    ) -> tuple[dict[str, object] | None, tuple[str, ...]]:
        try:
            raw = self._pipeline_inputs_provider(intent, now_ms)
        except Exception as exc:
            return None, (_safe_error_code("PIPELINE_INPUTS_ERROR", exc),)
        if not isinstance(raw, dict):
            return None, ("PIPELINE_INPUTS_UNAVAILABLE",)
        values = dict(raw)
        missing = sorted(_REQUIRED_PIPELINE_INPUTS - set(values))
        forbidden = sorted(_RESERVED_PIPELINE_INPUTS & set(values))
        reasons: list[str] = []
        if missing:
            reasons.append("PIPELINE_INPUTS_INCOMPLETE")
        if forbidden:
            reasons.append("PIPELINE_INPUTS_OVERRIDE_FORBIDDEN")
        prices = values.get("prices")
        target_instruments = {target.instrument_id for target in intent.targets}
        if (
            not isinstance(prices, dict)
            or not target_instruments.issubset(prices)
            or any(
                not isinstance(instrument, str)
                or not instrument.strip()
                or instrument != instrument.strip()
                or isinstance(price, bool)
                or not isinstance(price, (int, float))
                or not math.isfinite(float(price))
                or float(price) <= 0
                for instrument, price in (
                    prices.items() if isinstance(prices, dict) else ()
                )
            )
        ):
            reasons.append("PIPELINE_PRICES_INVALID")
        aum = values.get("aum")
        if (
            isinstance(aum, bool)
            or not isinstance(aum, (int, float))
            or not math.isfinite(float(aum))
            or float(aum) <= 0
        ):
            reasons.append("PIPELINE_AUM_INVALID")
        positions = values.get("current_positions")
        if not isinstance(positions, dict) or any(
            not isinstance(instrument, str)
            or not instrument.strip()
            or isinstance(quantity, bool)
            or not isinstance(quantity, (int, float))
            or not math.isfinite(float(quantity))
            for instrument, quantity in (
                positions.items() if isinstance(positions, dict) else ()
            )
        ):
            reasons.append("PIPELINE_POSITIONS_INVALID")
        account_positions = values.get("account_positions")
        if account_positions is not None and (
            not isinstance(account_positions, dict)
            or any(
                not isinstance(instrument, str)
                or not instrument.strip()
                or isinstance(quantity, bool)
                or not isinstance(quantity, (int, float))
                or not math.isfinite(float(quantity))
                for instrument, quantity in (
                    account_positions.items()
                    if isinstance(account_positions, dict)
                    else ()
                )
            )
        ):
            reasons.append("PIPELINE_ACCOUNT_POSITIONS_INVALID")
        if not callable(getattr(values.get("gate"), "evaluate", None)):
            reasons.append("PIPELINE_GATE_INVALID")
        if not callable(values.get("ctx_factory")):
            reasons.append("PIPELINE_CTX_FACTORY_INVALID")
        if not callable(getattr(values.get("ledger"), "record_submission", None)):
            reasons.append("PIPELINE_LEDGER_INVALID")
        if not callable(getattr(values.get("audit"), "append", None)):
            reasons.append("PIPELINE_AUDIT_INVALID")
        return (values if not reasons else None), tuple(sorted(set(reasons)))

    @staticmethod
    def _result_summary(result: LiveBatchResult) -> dict[str, object]:
        return {
            "tier": result.tier,
            "submitted_count": len(result.submitted),
            "pending_review_count": len(result.pending_review),
            "rejected_by_gate_count": len(result.rejected_by_gate),
            "dropped_by_filters_count": len(result.dropped_by_filters),
            "submission_error_count": len(result.submission_errors),
            "ledger_write_error_count": len(result.ledger_write_errors),
            "mandate_reason_codes": [
                _safe_reason_code(reason) for reason in result.mandate_reasons
            ],
        }

    def run_once(
        self,
        *,
        now_ms: int,
        worker_id: str,
        submit_enabled: bool = False,
    ) -> ExecutionServiceRun:
        now_ms = _time(now_ms, "now_ms")
        worker_id = _text(worker_id, "worker_id")
        if type(submit_enabled) is not bool:
            raise TypeError("submit_enabled must be bool")
        recovery_ready, recovery_report = self._recover_before_new_risk(
            now_ms, query_venue=submit_enabled
        )
        if not recovery_ready:
            return ExecutionServiceRun(
                status="RECOVERY_BLOCKED", recovery_report=recovery_report
            )
        candidates = tuple(
            sorted(
                (
                    *self._commands.by_state(ExecutionCommandState.VALIDATED),
                    *self._commands.by_state(ExecutionCommandState.RECEIVED),
                ),
                key=lambda record: (
                    record.intent.decided_at_ms,
                    record.intent.command_id,
                ),
            )
        )
        if not candidates:
            return ExecutionServiceRun(
                status="NO_WORK", recovery_report=recovery_report
            )
        command = candidates[0]
        snapshot, reasons = self._load_authority(command.intent, now_ms)
        if reasons:
            blocked = self._commands.block(
                command.command_id,
                reason_codes=reasons,
                recorded_at_ms=now_ms,
                result={"stage": "PRE_FLIGHT"},
            )
            return ExecutionServiceRun("BLOCKED", blocked, recovery_report)
        assert snapshot is not None
        if command.state is ExecutionCommandState.RECEIVED:
            command = self._commands.mark_validated(
                command.command_id, recorded_at_ms=now_ms
            )
        if not submit_enabled:
            return ExecutionServiceRun("PREVIEW_READY", command, recovery_report)
        if not command.intent.testnet_submission_requested:
            blocked = self._commands.block(
                command.command_id,
                reason_codes=("TESTNET_SUBMISSION_NOT_REQUESTED",),
                recorded_at_ms=now_ms,
                result={"stage": "SUBMISSION_AUTHORITY"},
            )
            return ExecutionServiceRun("BLOCKED", blocked, recovery_report)
        pipeline_inputs, input_reasons = self._load_pipeline_inputs(
            command.intent, now_ms
        )
        if input_reasons:
            blocked = self._commands.block(
                command.command_id,
                reason_codes=input_reasons,
                recorded_at_ms=now_ms,
                result={"stage": "PIPELINE_INPUTS"},
            )
            return ExecutionServiceRun("BLOCKED", blocked, recovery_report)
        assert pipeline_inputs is not None
        claim = self._commands.claim(
            command.command_id,
            worker_id=worker_id,
            now_ms=now_ms,
            lease_until_ms=now_ms + self._lease_ms,
        )
        final_snapshot, final_reasons = self._load_authority(claim.intent, now_ms)
        if final_reasons:
            blocked = self._commands.settle(
                claim.command_id,
                worker_id=worker_id,
                fencing_token=claim.fencing_token,
                state=ExecutionCommandState.BLOCKED,
                recorded_at_ms=now_ms,
                reason_codes=final_reasons,
                result={"stage": "FINAL_AUTHORITY_RECHECK"},
            )
            return ExecutionServiceRun("BLOCKED", blocked, recovery_report)
        assert final_snapshot is not None
        kwargs = {
            **pipeline_inputs,
            "weights": {
                target.instrument_id: float(target.target_weight)
                for target in claim.intent.targets
            },
            "venue": self._venue,
            "mandate": final_snapshot.mandate,
            "validation_attestation": final_snapshot.validation_attestation,
            "strategy_id": claim.intent.portfolio_id,
            "decided_ts": claim.intent.decided_at_ms,
            "now": now_ms,
            "unknown_order_store": self._recovery,
            "startup_recovery": {"ready_for_new_orders": True},
        }
        try:
            result = self._pipeline_submitter(**kwargs)
            if not isinstance(result, LiveBatchResult):
                raise TypeError("pipeline returned an invalid result")
        except Exception as exc:
            recovery_ids = self._blocking_recovery_order_ids(
                claim.intent.portfolio_id
            )
            unknown = self._commands.settle(
                claim.command_id,
                worker_id=worker_id,
                fencing_token=claim.fencing_token,
                state=ExecutionCommandState.UNKNOWN,
                recorded_at_ms=now_ms,
                reason_codes=(_safe_error_code("PIPELINE_ERROR", exc),),
                result={
                    "recovery_required": True,
                    "recovery_order_ids": recovery_ids,
                },
            )
            return ExecutionServiceRun("UNKNOWN", unknown, recovery_report)
        summary = self._result_summary(result)
        unknown_reasons = []
        if result.submission_errors:
            unknown_reasons.append("SUBMISSION_OUTCOME_UNKNOWN")
        if result.ledger_write_errors:
            unknown_reasons.append("LEDGER_STATE_UNKNOWN")
        if unknown_reasons:
            recovery_ids = self._blocking_recovery_order_ids(
                claim.intent.portfolio_id
            )
            unknown = self._commands.settle(
                claim.command_id,
                worker_id=worker_id,
                fencing_token=claim.fencing_token,
                state=ExecutionCommandState.UNKNOWN,
                recorded_at_ms=now_ms,
                reason_codes=tuple(unknown_reasons),
                result={
                    **summary,
                    "recovery_required": True,
                    "recovery_order_ids": recovery_ids,
                },
            )
            return ExecutionServiceRun("UNKNOWN", unknown, recovery_report)
        if result.tier != AUTO:
            reason_codes = tuple(
                sorted(
                    {
                        "PIPELINE_BLOCKED",
                        *(
                            _safe_reason_code(reason)
                            for reason in result.mandate_reasons
                        ),
                    }
                )
            )
            if result.submitted:
                unknown = self._commands.settle(
                    claim.command_id,
                    worker_id=worker_id,
                    fencing_token=claim.fencing_token,
                    state=ExecutionCommandState.UNKNOWN,
                    recorded_at_ms=now_ms,
                    reason_codes=("PIPELINE_TIER_INCONSISTENT_AFTER_SUBMISSION",),
                    result={**summary, "recovery_required": True},
                )
                return ExecutionServiceRun("UNKNOWN", unknown, recovery_report)
            blocked = self._commands.settle(
                claim.command_id,
                worker_id=worker_id,
                fencing_token=claim.fencing_token,
                state=ExecutionCommandState.BLOCKED,
                recorded_at_ms=now_ms,
                reason_codes=reason_codes,
                result=summary,
            )
            return ExecutionServiceRun("BLOCKED", blocked, recovery_report)
        incomplete = bool(
            result.pending_review
            or result.rejected_by_gate
            or result.dropped_by_filters
        )
        if incomplete and result.submitted:
            recovery_ids = self._blocking_recovery_order_ids(
                claim.intent.portfolio_id
            )
            unknown = self._commands.settle(
                claim.command_id,
                worker_id=worker_id,
                fencing_token=claim.fencing_token,
                state=ExecutionCommandState.UNKNOWN,
                recorded_at_ms=now_ms,
                reason_codes=("PARTIAL_EXECUTION_REQUIRES_RECONCILIATION",),
                result={
                    **summary,
                    "recovery_required": True,
                    "recovery_order_ids": recovery_ids,
                },
            )
            return ExecutionServiceRun("UNKNOWN", unknown, recovery_report)
        if incomplete:
            blocked = self._commands.settle(
                claim.command_id,
                worker_id=worker_id,
                fencing_token=claim.fencing_token,
                state=ExecutionCommandState.BLOCKED,
                recorded_at_ms=now_ms,
                reason_codes=("EXECUTION_INCOMPLETE",),
                result=summary,
            )
            return ExecutionServiceRun("BLOCKED", blocked, recovery_report)
        completed = self._commands.settle(
            claim.command_id,
            worker_id=worker_id,
            fencing_token=claim.fencing_token,
            state=ExecutionCommandState.COMPLETED,
            recorded_at_ms=now_ms,
            result=summary,
        )
        return ExecutionServiceRun("COMPLETED", completed, recovery_report)


__all__ = [
    "ExecutionAuthoritySnapshot",
    "ExecutionServiceRun",
    "TestnetExecutionService",
    "TestnetExecutionServiceError",
]
