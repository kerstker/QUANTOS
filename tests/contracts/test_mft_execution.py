from __future__ import annotations

import pytest
from pydantic import ValidationError

from quantos.contracts.mft_execution import (
    MFT_TESTNET_ENVIRONMENT,
    TestnetExecutionReceiptStatus as ReceiptStatus,
    TestnetExecutionIntent as ExecutionIntent,
    TestnetExecutionReceipt as ExecutionReceipt,
    create_testnet_execution_intent,
    create_testnet_execution_receipt,
    verify_testnet_execution_intent,
)
from quantos.contracts.portfolio import TargetPosition


_WORKFLOW = "1" * 64
_VALIDATION_A = "2" * 64
_VALIDATION_B = "3" * 64
_MARKET = "4" * 64
_POLICY = "5" * 64


def _intent(**overrides):
    kwargs = {
        "portfolio_id": "hourly-alpha-paper",
        "alpha_ids": ("alpha-b", "alpha-a"),
        "decided_at_ms": 1_000_000,
        "expires_at_ms": 1_003_600,
        "targets": (
            TargetPosition(instrument_id="ETH", target_weight=-0.25),
            TargetPosition(instrument_id="BTC", target_weight=0.25),
        ),
        "market_watermark_ms": 999_999,
        "market_snapshot_hash": _MARKET,
        "workflow_receipt_hash": _WORKFLOW,
        "validation_artifact_hashes": (_VALIDATION_B, _VALIDATION_A),
        "policy_version": "mft-testnet-policy.v1",
        "policy_hash": _POLICY,
        "testnet_submission_requested": False,
    }
    kwargs.update(overrides)
    return create_testnet_execution_intent(**kwargs)


def test_factory_canonicalizes_set_like_inputs_and_has_stable_identity() -> None:
    left = _intent()
    right = _intent(
        alpha_ids=("alpha-a", "alpha-b"),
        targets=tuple(reversed(_intent().targets)),
        validation_artifact_hashes=(_VALIDATION_A, _VALIDATION_B),
    )

    assert left == right
    assert left.alpha_ids == ("alpha-a", "alpha-b")
    assert tuple(target.instrument_id for target in left.targets) == ("BTC", "ETH")
    assert left.command_id.startswith("mft-testnet-")
    assert len(left.intent_hash) == 64
    assert left.idempotency_key == right.idempotency_key


def test_factory_requires_concrete_wave1_and_market_evidence() -> None:
    for field, value in (
        ("workflow_receipt_hash", ""),
        ("validation_artifact_hashes", ()),
        ("market_snapshot_hash", "self-reported-pass"),
        ("policy_hash", "0" * 63),
    ):
        with pytest.raises((TypeError, ValueError, ValidationError)):
            _intent(**{field: value})


def test_contract_structurally_rejects_mainnet_and_real_authority_fields() -> None:
    base = _intent().model_dump(mode="python")
    for field, value in (
        ("execution_environment", "BINANCE_FUTURES_MAINNET"),
        ("real_capital", True),
        ("live_allowed", True),
        ("real_order_submission_allowed", True),
    ):
        with pytest.raises(ValidationError):
            type(_intent())(**{**base, field: value})

    assert _intent().execution_environment == MFT_TESTNET_ENVIRONMENT

    for field in ("real_capital", "live_allowed", "real_order_submission_allowed"):
        with pytest.raises(ValidationError):
            type(_intent())(**{**base, field: 0})


def test_time_and_target_invariants_fail_closed() -> None:
    with pytest.raises((ValueError, ValidationError)):
        _intent(expires_at_ms=1_000_000)
    with pytest.raises((ValueError, ValidationError)):
        _intent(market_watermark_ms=1_000_001)
    with pytest.raises((ValueError, ValidationError)):
        _intent(
            targets=(
                TargetPosition(instrument_id="BTC", target_weight=0.1),
                TargetPosition(instrument_id="BTC", target_weight=-0.1),
            )
        )
    with pytest.raises((ValueError, ValidationError)):
        _intent(targets=(TargetPosition(instrument_id="BTC", target_weight=float("inf")),))


def test_verification_rejects_expiry_future_decision_mismatch_and_tampering() -> None:
    intent = _intent()
    assert verify_testnet_execution_intent(
        intent,
        now_ms=1_000_001,
        expected_workflow_receipt_hash=_WORKFLOW,
        expected_validation_artifact_hashes=(_VALIDATION_A, _VALIDATION_B),
        expected_market_snapshot_hash=_MARKET,
    ).valid

    expired = verify_testnet_execution_intent(intent, now_ms=intent.expires_at_ms)
    assert expired.reasons == ("INTENT_EXPIRED",)

    future = verify_testnet_execution_intent(intent, now_ms=intent.decided_at_ms - 1)
    assert future.reasons == ("DECISION_IN_FUTURE",)

    mismatched = verify_testnet_execution_intent(
        intent,
        now_ms=1_000_001,
        expected_workflow_receipt_hash="a" * 64,
        expected_validation_artifact_hashes=(_VALIDATION_A,),
        expected_market_snapshot_hash="b" * 64,
    )
    assert mismatched.reasons == (
        "MARKET_SNAPSHOT_MISMATCH",
        "VALIDATION_ARTIFACT_SET_MISMATCH",
        "WORKFLOW_RECEIPT_MISMATCH",
    )

    tampered = intent.model_copy(update={"portfolio_id": "tampered"})
    verification = verify_testnet_execution_intent(tampered, now_ms=1_000_001)
    assert verification.reasons == ("INTENT_HASH_MISMATCH",)

    malformed = intent.model_copy(update={"decided_at_ms": "not-a-time"})
    verification = verify_testnet_execution_intent(malformed, now_ms=1_000_001)
    assert verification.reasons == ("INTENT_MALFORMED",)


def test_receipt_is_hash_bound_and_never_claims_real_authority() -> None:
    intent = _intent(testnet_submission_requested=True)
    receipt = create_testnet_execution_receipt(
        intent=intent,
        status=ReceiptStatus.COMPLETED,
        recorded_at_ms=1_000_100,
        oms_event_head="6" * 64,
        ledger_head="7" * 64,
        testnet_orders_submitted=2,
    )

    assert receipt.command_id == intent.command_id
    assert receipt.intent_hash == intent.intent_hash
    assert receipt.venue_mode == "testnet"
    assert receipt.real_capital is False
    assert receipt.live_allowed is False
    assert receipt.real_order_submission_allowed is False
    assert len(receipt.receipt_hash) == 64

    with pytest.raises(ValidationError):
        type(receipt)(**{**receipt.model_dump(mode="python"), "receipt_hash": "8" * 64})


def test_blocked_receipt_requires_machine_readable_error_code() -> None:
    intent = _intent()
    with pytest.raises((ValueError, ValidationError)):
        create_testnet_execution_receipt(
            intent=intent,
            status=ReceiptStatus.BLOCKED,
            recorded_at_ms=1_000_100,
        )

    receipt = create_testnet_execution_receipt(
        intent=intent,
        status=ReceiptStatus.BLOCKED,
        recorded_at_ms=1_000_100,
        error_codes=("MARKET_GAP_BLOCKED",),
    )
    assert receipt.error_codes == ("MARKET_GAP_BLOCKED",)
    assert receipt.testnet_orders_submitted == 0

    with pytest.raises((TypeError, ValueError, ValidationError)):
        create_testnet_execution_receipt(
            intent=intent,
            status=ReceiptStatus.COMPLETED,
            recorded_at_ms=1_000_100,
            testnet_orders_submitted=True,
        )

    with pytest.raises((TypeError, ValueError, ValidationError)):
        create_testnet_execution_receipt(
            intent=intent,
            status=ReceiptStatus.COMPLETED,
            recorded_at_ms=1_000_100,
            testnet_orders_submitted=1,
        )


def test_json_round_trip_revalidates_content_hashes() -> None:
    intent = _intent()
    restored_intent = ExecutionIntent.model_validate_json(intent.model_dump_json())
    assert restored_intent == intent

    receipt = create_testnet_execution_receipt(
        intent=intent,
        status=ReceiptStatus.ACCEPTED,
        recorded_at_ms=1_000_001,
    )
    restored_receipt = ExecutionReceipt.model_validate_json(receipt.model_dump_json())
    assert restored_receipt == receipt


def test_public_contract_package_exports_testnet_types() -> None:
    from quantos import contracts

    assert contracts.TestnetExecutionIntent is ExecutionIntent
    assert contracts.TestnetExecutionReceipt is ExecutionReceipt
    assert contracts.TestnetExecutionReceiptStatus is ReceiptStatus
