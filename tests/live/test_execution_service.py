from __future__ import annotations

from dataclasses import replace

import pytest

from live.contracts import VenueOrderResult
from live.execution.command_store import (
    ExecutionCommandState,
    SqliteExecutionCommandStore,
)
from live.execution.order_recovery import SqliteOrderRecoveryStore
from live.governance.mandate import AUTO, NEEDS_HUMAN_REVIEW, Mandate
from live.governance.validation_attestation import (
    VALIDATION_PASS,
    ValidationAttestation,
)
from live.pipeline import LiveBatchResult
from quantos.contracts.mft_execution import create_testnet_execution_intent
from quantos.contracts.portfolio import TargetPosition


NOW = 1_000_000
WORKFLOW = "1" * 64
VALIDATION = "2" * 64
MARKET = "3" * 64
POLICY = "4" * 64


def _intent(
    *, requested: bool = True, suffix: str = "", btc_weight: float = 0.25
):
    return create_testnet_execution_intent(
        portfolio_id=f"hourly-paper{suffix}",
        alpha_ids=("a" * 64,),
        decided_at_ms=NOW - 10,
        expires_at_ms=NOW + 10_000,
        targets=(TargetPosition(instrument_id="BTC", target_weight=btc_weight),),
        market_watermark_ms=NOW - 50,
        market_snapshot_hash=MARKET,
        workflow_receipt_hash=WORKFLOW,
        validation_artifact_hashes=(VALIDATION,),
        policy_version="mft-testnet.v1",
        policy_hash=POLICY,
        testnet_submission_requested=requested,
    )


def _mandate(*, expires_at: int = NOW + 5_000) -> Mandate:
    return Mandate(
        mandate_id="testnet-human-mandate",
        venue="testnet",
        exchange="binance",
        allowed_instruments=frozenset({"BTC"}),
        max_single_order_notional=1_000_000.0,
        max_gross_notional=1_000_000.0,
        max_net_notional=1_000_000.0,
        approved_by=("human-operator",),
        issued_at=NOW - 1_000,
        expires_at=expires_at,
    )


def _authority(**overrides):
    from live.execution.service import ExecutionAuthoritySnapshot

    values = {
        "workflow_receipt_hash": WORKFLOW,
        "validation_artifact_hashes": (VALIDATION,),
        "market_snapshot_hash": MARKET,
        "market_watermark_ms": NOW - 50,
        "market_contiguous": True,
        "policy_version": "mft-testnet.v1",
        "policy_hash": POLICY,
        "kill_switch_tripped": False,
        "mandate": _mandate(),
        "validation_attestation": ValidationAttestation(
            strategy_id="hourly-paper",
            kind=VALIDATION_PASS,
            attested_by="human-validator",
            attested_at=NOW - 500,
            validation_result_hash=VALIDATION,
        ),
    }
    values.update(overrides)
    return ExecutionAuthoritySnapshot(**values)


class Venue:
    venue_mode = "testnet"
    venue_name = "binance"

    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.queries = []

    def get_order(self, instrument_id, client_order_id):
        self.queries.append((instrument_id, client_order_id))
        if self.error is not None:
            raise self.error
        return self.result


class Gate:
    def evaluate(self, intent, context):
        del intent, context


class Ledger:
    def record_submission(self, *args, **kwargs):
        del args, kwargs


class Audit:
    def append(self, *args, **kwargs):
        del args, kwargs


def _inputs(intent, now_ms):
    del intent, now_ms
    return {
        "prices": {"BTC": 60_000.0},
        "aum": 100_000.0,
        "current_positions": {},
        "gate": Gate(),
        "ctx_factory": lambda order: order,
        "ledger": Ledger(),
        "audit": Audit(),
    }


def _service(tmp_path, *, venue=None, authority=None, pipeline=None, kill_switch_arm=None):
    from live.execution.service import TestnetExecutionService

    command_store = SqliteExecutionCommandStore(
        str(tmp_path / "commands.db"), create=True
    )
    recovery_store = SqliteOrderRecoveryStore(
        str(tmp_path / "recovery.db"), create=True
    )
    calls = []

    def submit(**kwargs):
        calls.append(kwargs)
        if pipeline is not None:
            return pipeline(**kwargs)
        return LiveBatchResult(tier=AUTO, submitted=(object(),))

    snapshot = authority or _authority()
    service = TestnetExecutionService(
        command_store,
        recovery_store=recovery_store,
        venue=venue or Venue(),
        authority_provider=lambda intent, now_ms: snapshot,
        pipeline_inputs_provider=_inputs,
        pipeline_submitter=submit,
        max_market_age_ms=1_000,
        lease_ms=5_000,
        kill_switch_arm=kill_switch_arm,
    )
    return service, command_store, recovery_store, calls


def test_valid_testnet_command_is_single_writer_completed_and_not_replayed(tmp_path) -> None:
    service, commands, recovery, calls = _service(tmp_path)
    intent = _intent()
    commands.enqueue(intent, recorded_at_ms=NOW)

    first = service.run_once(now_ms=NOW, worker_id="worker-a", submit_enabled=True)
    replay = service.run_once(now_ms=NOW + 1, worker_id="worker-b", submit_enabled=True)

    assert first.status == "COMPLETED"
    assert first.command is not None
    assert first.command.state is ExecutionCommandState.COMPLETED
    assert replay.status == "NO_WORK"
    assert len(calls) == 1
    call = calls[0]
    assert call["venue"].venue_mode == "testnet"
    assert call["strategy_id"] == intent.portfolio_id
    assert call["weights"] == {"BTC": 0.25}
    assert call["unknown_order_store"] is recovery
    assert call["startup_recovery"]["ready_for_new_orders"] is True
    commands.close()
    recovery.close()


def test_preview_validates_without_claim_or_pipeline_then_explicit_submit_runs(tmp_path) -> None:
    service, commands, recovery, calls = _service(tmp_path)
    intent = _intent()
    commands.enqueue(intent, recorded_at_ms=NOW)

    preview = service.run_once(
        now_ms=NOW, worker_id="worker-a", submit_enabled=False
    )
    submitted = service.run_once(
        now_ms=NOW + 1, worker_id="worker-b", submit_enabled=True
    )

    assert preview.status == "PREVIEW_READY"
    assert preview.command is not None
    assert preview.command.state is ExecutionCommandState.VALIDATED
    assert submitted.status == "COMPLETED"
    assert len(calls) == 1
    commands.close()
    recovery.close()


def test_explicit_submit_rejects_preview_only_intent(tmp_path) -> None:
    service, commands, recovery, calls = _service(tmp_path)
    intent = _intent(requested=False)
    commands.enqueue(intent, recorded_at_ms=NOW)

    result = service.run_once(now_ms=NOW, worker_id="worker", submit_enabled=True)

    assert result.status == "BLOCKED"
    assert result.command is not None
    assert result.command.reason_codes == ("TESTNET_SUBMISSION_NOT_REQUESTED",)
    assert calls == []
    commands.close()
    recovery.close()


@pytest.mark.parametrize(
    ("authority_changes", "expected_reason"),
    (
        ({"workflow_receipt_hash": "f" * 64}, "WORKFLOW_RECEIPT_MISMATCH"),
        (
            {"validation_artifact_hashes": ("f" * 64,)},
            "VALIDATION_ARTIFACT_SET_MISMATCH",
        ),
        ({"market_snapshot_hash": "f" * 64}, "MARKET_SNAPSHOT_MISMATCH"),
        ({"policy_version": "revoked.v2"}, "POLICY_VERSION_MISMATCH"),
        ({"policy_hash": "f" * 64}, "POLICY_HASH_MISMATCH"),
        ({"market_watermark_ms": NOW - 2_000}, "MARKET_WATERMARK_MISMATCH"),
        ({"market_contiguous": False}, "MARKET_GAP_BLOCKED"),
        ({"kill_switch_tripped": True}, "KILL_SWITCH_TRIPPED"),
        ({"mandate": _mandate(expires_at=NOW)}, "MANDATE_EXPIRED"),
    ),
)
def test_authority_ref_freshness_gap_kill_switch_and_mandate_fail_closed(
    tmp_path, authority_changes, expected_reason
) -> None:
    authority = replace(_authority(), **authority_changes)
    service, commands, recovery, calls = _service(
        tmp_path, authority=authority
    )
    commands.enqueue(_intent(), recorded_at_ms=NOW)

    result = service.run_once(now_ms=NOW, worker_id="worker", submit_enabled=True)

    assert result.status == "BLOCKED"
    assert result.command is not None
    assert expected_reason in result.command.reason_codes
    assert calls == []
    commands.close()
    recovery.close()


def test_malformed_authority_is_safely_classified_and_never_escapes(tmp_path) -> None:
    malformed = replace(_mandate(), max_gross_notional="sensitive-raw-value")
    service, commands, recovery, calls = _service(
        tmp_path, authority=replace(_authority(), mandate=malformed)
    )
    commands.enqueue(_intent(), recorded_at_ms=NOW)

    result = service.run_once(now_ms=NOW, worker_id="worker", submit_enabled=True)

    assert result.status == "BLOCKED"
    assert result.command is not None
    assert result.command.reason_codes == ("MANDATE_LIMITS_INVALID",)
    assert "sensitive-raw-value" not in str(result.command.result)
    assert calls == []
    recovery.close()
    commands.close()


def test_nonfinite_per_instrument_mandate_limit_cannot_weaken_gate(tmp_path) -> None:
    malformed = replace(
        _mandate(),
        per_instrument_max_notional=(("BTC", float("nan")),),
    )
    service, commands, recovery, calls = _service(
        tmp_path, authority=replace(_authority(), mandate=malformed)
    )
    commands.enqueue(_intent(), recorded_at_ms=NOW)

    result = service.run_once(now_ms=NOW, worker_id="worker", submit_enabled=True)

    assert result.status == "BLOCKED"
    assert result.command is not None
    assert result.command.reason_codes == (
        "MANDATE_PER_INSTRUMENT_LIMITS_INVALID",
    )
    assert calls == []
    recovery.close()
    commands.close()


def test_mainnet_or_unknown_venue_never_reaches_pipeline(tmp_path) -> None:
    venue = Venue()
    venue.venue_mode = "mainnet"
    service, commands, recovery, calls = _service(tmp_path, venue=venue)
    commands.enqueue(_intent(), recorded_at_ms=NOW)

    result = service.run_once(now_ms=NOW, worker_id="worker", submit_enabled=True)

    assert result.status == "BLOCKED"
    assert result.command is not None
    assert result.command.reason_codes == ("VENUE_NOT_BINANCE_TESTNET",)
    assert calls == []
    commands.close()
    recovery.close()


def test_preview_with_unknown_order_blocks_without_query_or_kill_switch_mutation(tmp_path) -> None:
    armed = []
    venue = Venue(error=AssertionError("preview must not query venue"))
    service, commands, recovery, calls = _service(
        tmp_path,
        venue=venue,
        kill_switch_arm=lambda reason, now_ms: armed.append((reason, now_ms)),
    )
    recovery.register_unknown(
        client_order_id="cid-preview-unknown",
        deployment_id="old-paper",
        instrument_id="BTC",
        side="BUY",
        quantity=0.01,
        now_ms=NOW - 100,
    )
    commands.enqueue(_intent(), recorded_at_ms=NOW)

    result = service.run_once(now_ms=NOW, worker_id="preview", submit_enabled=False)

    assert result.status == "RECOVERY_BLOCKED"
    assert venue.queries == []
    assert armed == []
    assert calls == []
    recovery.close()
    commands.close()


@pytest.mark.parametrize(
    ("pipeline", "expected_reason"),
    (
        (
            lambda **kwargs: LiveBatchResult(
                tier=AUTO, submission_errors=(("order", "timeout"),)
            ),
            "SUBMISSION_OUTCOME_UNKNOWN",
        ),
        (
            lambda **kwargs: LiveBatchResult(
                tier=AUTO, ledger_write_errors=(("order", "locked"),)
            ),
            "LEDGER_STATE_UNKNOWN",
        ),
    ),
)
def test_ack_or_ledger_uncertainty_is_durable_unknown_and_blocks_new_risk(
    tmp_path, pipeline, expected_reason
) -> None:
    service, commands, recovery, calls = _service(tmp_path, pipeline=pipeline)
    commands.enqueue(_intent(), recorded_at_ms=NOW)

    first = service.run_once(now_ms=NOW, worker_id="worker-a", submit_enabled=True)
    commands.enqueue(_intent(suffix="-next"), recorded_at_ms=NOW + 1)
    second = service.run_once(
        now_ms=NOW + 2, worker_id="worker-b", submit_enabled=True
    )

    assert first.status == "UNKNOWN"
    assert first.command is not None
    assert first.command.reason_codes == (expected_reason,)
    assert second.status == "RECOVERY_BLOCKED"
    assert len(calls) == 1
    commands.close()
    recovery.close()


def test_pipeline_exception_is_unknown_and_error_message_is_never_persisted(tmp_path) -> None:
    def crash(**kwargs):
        del kwargs
        raise TimeoutError("secret-like upstream response body")

    service, commands, recovery, calls = _service(tmp_path, pipeline=crash)
    commands.enqueue(_intent(), recorded_at_ms=NOW)

    result = service.run_once(now_ms=NOW, worker_id="worker", submit_enabled=True)

    assert result.status == "UNKNOWN"
    assert result.command is not None
    assert result.command.reason_codes == ("PIPELINE_ERROR_TIMEOUTERROR",)
    assert "secret-like" not in str(result.command.result)
    assert len(calls) == 1
    commands.close()
    recovery.close()


@pytest.mark.parametrize("field", ("rejected_by_gate", "dropped_by_filters", "pending_review"))
def test_incomplete_pipeline_result_is_never_recorded_completed(tmp_path, field) -> None:
    pipeline = lambda **kwargs: LiveBatchResult(
        tier=AUTO,
        submitted=(object(),),
        **{field: (object(),)},
    )
    service, commands, recovery, calls = _service(tmp_path, pipeline=pipeline)
    commands.enqueue(_intent(), recorded_at_ms=NOW)

    result = service.run_once(now_ms=NOW, worker_id="worker", submit_enabled=True)

    assert result.status == "UNKNOWN"
    assert result.command is not None
    assert result.command.reason_codes == ("PARTIAL_EXECUTION_REQUIRES_RECONCILIATION",)
    assert len(calls) == 1
    recovery.close()
    commands.close()


def test_pipeline_human_review_keeps_safe_reason_codes(tmp_path) -> None:
    pipeline = lambda **kwargs: LiveBatchResult(
        tier=NEEDS_HUMAN_REVIEW,
        pending_review=(object(),),
        mandate_reasons=("mandate_expired",),
    )
    service, commands, recovery, calls = _service(tmp_path, pipeline=pipeline)
    commands.enqueue(_intent(), recorded_at_ms=NOW)

    result = service.run_once(now_ms=NOW, worker_id="worker", submit_enabled=True)

    assert result.status == "BLOCKED"
    assert result.command is not None
    assert result.command.reason_codes == ("MANDATE_EXPIRED", "PIPELINE_BLOCKED")
    assert len(calls) == 1
    recovery.close()
    commands.close()


def test_pipeline_reason_text_is_classified_without_persisting_raw_content(tmp_path) -> None:
    secret = "credential-value-never-persist"
    pipeline = lambda **kwargs: LiveBatchResult(
        tier=NEEDS_HUMAN_REVIEW,
        pending_review=(object(),),
        mandate_reasons=(secret,),
    )
    service, commands, recovery, calls = _service(tmp_path, pipeline=pipeline)
    commands.enqueue(_intent(), recorded_at_ms=NOW)

    result = service.run_once(now_ms=NOW, worker_id="worker", submit_enabled=True)

    assert result.command is not None
    assert result.command.reason_codes == (
        "PIPELINE_BLOCKED",
        "UNCLASSIFIED_PIPELINE_REASON",
    )
    assert secret not in str(result.command.result)
    recovery.close()
    commands.close()


def test_invalid_pipeline_capabilities_are_blocked_before_claim(tmp_path) -> None:
    service, commands, recovery, calls = _service(tmp_path)
    service._pipeline_inputs_provider = lambda intent, now_ms: {
        "prices": {"BTC": 60_000.0},
        "aum": 100_000.0,
        "current_positions": {"BTC": float("nan")},
        "gate": object(),
        "ctx_factory": object(),
        "ledger": object(),
        "audit": object(),
    }
    commands.enqueue(_intent(), recorded_at_ms=NOW)

    result = service.run_once(now_ms=NOW, worker_id="worker", submit_enabled=True)

    assert result.status == "BLOCKED"
    assert result.command is not None
    assert {
        "PIPELINE_POSITIONS_INVALID",
        "PIPELINE_GATE_INVALID",
        "PIPELINE_CTX_FACTORY_INVALID",
        "PIPELINE_LEDGER_INVALID",
        "PIPELINE_AUDIT_INVALID",
    }.issubset(result.command.reason_codes)
    assert calls == []
    recovery.close()
    commands.close()


def test_recovered_unknown_command_is_evidence_resolved_before_next_risk(tmp_path) -> None:
    venue = Venue()
    invocation = 0

    def submit(**kwargs):
        nonlocal invocation
        invocation += 1
        if invocation == 1:
            kwargs["unknown_order_store"].register_unknown(
                client_order_id="cid-recoverable",
                deployment_id=kwargs["strategy_id"],
                instrument_id="BTC",
                side="BUY",
                quantity=0.01,
                now_ms=kwargs["now"],
            )
            return LiveBatchResult(
                tier=AUTO, submission_errors=(("order", "timeout"),)
            )
        return LiveBatchResult(tier=AUTO, submitted=(object(),))

    service, commands, recovery, calls = _service(
        tmp_path, venue=venue, pipeline=submit
    )
    first_intent = _intent()
    commands.enqueue(first_intent, recorded_at_ms=NOW)
    first = service.run_once(now_ms=NOW, worker_id="worker-a", submit_enabled=True)
    venue.result = VenueOrderResult(
        venue_order_id="venue-1",
        client_order_id="cid-recoverable",
        instrument_id="BTC",
        status="FILLED",
        filled_qty=0.01,
        avg_price=60_000.0,
    )
    commands.enqueue(_intent(btc_weight=0.20), recorded_at_ms=NOW + 1)

    second = service.run_once(
        now_ms=NOW + 2, worker_id="worker-b", submit_enabled=True
    )

    recovered = commands.get(first_intent.command_id)
    assert first.status == "UNKNOWN"
    assert second.status == "COMPLETED"
    assert recovered is not None
    assert recovered.state is ExecutionCommandState.COMPLETED
    assert len(recovered.result["recovery_evidence_hash"]) == 64
    assert len(calls) == 2
    commands.close()
    recovery.close()


def test_startup_recovery_blocks_on_query_error_and_arms_kill_switch(tmp_path) -> None:
    armed = []
    venue = Venue(error=TimeoutError("do not persist this body"))
    service, commands, recovery, calls = _service(
        tmp_path,
        venue=venue,
        kill_switch_arm=lambda reason, now_ms: armed.append((reason, now_ms)),
    )
    recovery.register_unknown(
        client_order_id="cid-unknown",
        deployment_id="old-paper",
        instrument_id="BTC",
        side="BUY",
        quantity=0.01,
        now_ms=NOW - 100,
    )
    commands.enqueue(_intent(), recorded_at_ms=NOW)

    result = service.run_once(now_ms=NOW, worker_id="worker", submit_enabled=True)

    assert result.status == "RECOVERY_BLOCKED"
    assert calls == []
    assert armed and armed[0][0] == "ORDER_SUBMISSION_UNKNOWN:cid-unknown"
    commands.close()
    recovery.close()


def test_startup_resolves_terminal_unknown_order_before_accepting_new_risk(tmp_path) -> None:
    venue = Venue(
        result=VenueOrderResult(
            venue_order_id="venue-1",
            client_order_id="cid-unknown",
            instrument_id="BTC",
            status="FILLED",
            filled_qty=0.01,
            avg_price=60_000.0,
        )
    )
    service, commands, recovery, calls = _service(tmp_path, venue=venue)
    recovery.register_unknown(
        client_order_id="cid-unknown",
        deployment_id="old-paper",
        instrument_id="BTC",
        side="BUY",
        quantity=0.01,
        now_ms=NOW - 100,
    )
    commands.enqueue(_intent(), recorded_at_ms=NOW)

    result = service.run_once(now_ms=NOW, worker_id="worker", submit_enabled=True)

    assert result.status == "COMPLETED"
    assert recovery.pending() == ()
    assert venue.queries == [("BTC", "cid-unknown")]
    assert len(calls) == 1
    commands.close()
    recovery.close()


def test_crashed_in_flight_command_becomes_unknown_before_new_command(tmp_path) -> None:
    service, commands, recovery, calls = _service(tmp_path)
    crashed = commands.enqueue(_intent(), recorded_at_ms=NOW - 9)
    commands.mark_validated(crashed.command_id, recorded_at_ms=NOW - 8)
    commands.claim(
        crashed.command_id,
        worker_id="dead-worker",
        now_ms=NOW - 7,
        lease_until_ms=NOW,
    )
    commands.enqueue(_intent(suffix="-next"), recorded_at_ms=NOW)

    result = service.run_once(now_ms=NOW, worker_id="worker", submit_enabled=True)

    assert result.status == "RECOVERY_BLOCKED"
    assert commands.get(crashed.command_id).state is ExecutionCommandState.UNKNOWN
    assert calls == []
    commands.close()
    recovery.close()
