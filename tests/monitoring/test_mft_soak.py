from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from quantos.contracts.hashing import stable_hash
from services.monitoring.mft_soak import (
    REQUIRED_EVIDENCE_KINDS,
    MftSoakError,
    build_soak_status,
    evaluate_cycle,
    parse_soak_policy,
    validate_start_receipt,
)
from services.monitoring.mft_soak_store import MftSoakStore, MftSoakStoreError


SAFETY_FLAGS = {
    "LIVE_TRADING_ENABLED": False,
    "REAL_BROKER_ENABLED": False,
    "ALLOW_ORDER_SUBMISSION": False,
    "ALLOW_AI_DIRECT_TRADING": False,
    "REQUIRE_HUMAN_APPROVAL": True,
}


def _policy_document(*, minimum_cycles: int = 2) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "MFT_TESTNET_SOAK_POLICY",
        "policy_id": "mft-testnet-soak.test.v1",
        "policy_version": "1.0.0",
        "venue": "BINANCE_FUTURES_TESTNET",
        "mode": "TESTNET_ONLY",
        "cadence_ms": 100,
        "minimum_duration_ms": 100,
        "minimum_completed_cycles": minimum_cycles,
        "maximum_cycle_gap_ms": 150,
        "thresholds": {
            "max_market_gap_ms": 10,
            "max_scheduler_lag_ms": 10,
            "max_reconciliation_abs_error": 1e-8,
            "max_abs_tca_slippage_bps": 200.0,
            "max_parity_position_drift": 1e-8,
            "max_oms_unknown_count": 0,
            "max_risk_breach_count": 0,
            "max_failed_cycle_count": 0,
        },
        "required_evidence_kinds": list(REQUIRED_EVIDENCE_KINDS),
        "safety_flags": dict(SAFETY_FLAGS),
        "calibration": "PREREGISTERED_UNCALIBRATED_ENGINEERING_LIMITS",
        "completion_effect": "TESTNET_ENGINEERING_ONLY_NO_MICRO_LIVE_AUTHORITY",
    }


def _start(policy_hash: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "MFT_TESTNET_SOAK_START",
        "run_id": "task39-run-001",
        "operator_start_id": "human-start-receipt-001",
        "started_at_ms": 1_000,
        "policy_hash": policy_hash,
        "venue": "BINANCE_FUTURES_TESTNET",
        "mode": "TESTNET_ONLY",
        "authorization_class": "OPERATOR_ASSERTED_TESTNET_ONLY",
        "human_authorization_receipt_hash": "1" * 64,
        "testnet_mandate_hash": "2" * 64,
        "credential_reference_hash": "3" * 64,
        "safety_flags": dict(SAFETY_FLAGS),
    }


def _cycle(policy_hash: str, *, index: int, started_at_ms: int) -> dict[str, object]:
    completed_at_ms = started_at_ms + 50
    evidence = {
        kind: {
            "artifact": f"{kind}-{index}.json",
            "artifact_hash": stable_hash({"kind": kind, "index": index}),
            "observed_at_ms": completed_at_ms,
            "status": "VERIFIED",
        }
        for kind in REQUIRED_EVIDENCE_KINDS
    }
    return {
        "schema_version": 1,
        "kind": "MFT_TESTNET_SOAK_CYCLE",
        "run_id": "task39-run-001",
        "operator_start_id": "human-start-receipt-001",
        "cycle_id": f"cycle-{index:03d}",
        "cycle_index": index,
        "started_at_ms": started_at_ms,
        "completed_at_ms": completed_at_ms,
        "policy_hash": policy_hash,
        "venue": "BINANCE_FUTURES_TESTNET",
        "mode": "TESTNET_ONLY",
        "evidence": evidence,
        "metrics": {
            "market_gap_ms": 0,
            "scheduler_lag_ms": 0,
            "intent_count": 1,
            "oms_unknown_count": 0,
            "fill_count": 1,
            "reconciliation_abs_error": 0.0,
            "risk_breach_count": 0,
            "tca_abs_slippage_bps": 1.0,
            "parity_position_drift": 0.0,
            "recovery_drill_passed": True,
            "testnet_submission_count": 1,
            "mainnet_submission_count": 0,
        },
        "safety_flags": dict(SAFETY_FLAGS),
    }


def _materialize_evidence(root, row: dict[str, object]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for kind, item in row["evidence"].items():
        payload = json.dumps({"kind": kind, "cycle_id": row["cycle_id"]}).encode()
        (root / item["artifact"]).write_bytes(payload)
        item["artifact_hash"] = hashlib.sha256(payload).hexdigest()


def test_policy_is_exact_testnet_only_and_hash_bound() -> None:
    policy = parse_soak_policy(_policy_document())

    assert policy.policy_hash == stable_hash(policy.payload())
    assert policy.required_evidence_kinds == REQUIRED_EVIDENCE_KINDS
    assert policy.completion_effect == "TESTNET_ENGINEERING_ONLY_NO_MICRO_LIVE_AUTHORITY"

    mainnet = _policy_document()
    mainnet["venue"] = "BINANCE_MAINNET"
    with pytest.raises(MftSoakError, match="venue"):
        parse_soak_policy(mainnet)

    missing_evidence = _policy_document()
    missing_evidence["required_evidence_kinds"] = list(REQUIRED_EVIDENCE_KINDS[:-1])
    with pytest.raises(MftSoakError, match="required_evidence_kinds"):
        parse_soak_policy(missing_evidence)

    unsafe = _policy_document()
    unsafe["safety_flags"] = {**SAFETY_FLAGS, "ALLOW_ORDER_SUBMISSION": True}
    with pytest.raises(MftSoakError, match="safety_flags"):
        parse_soak_policy(unsafe)


def test_start_receipt_requires_external_hashes_and_cannot_claim_strong_identity() -> None:
    policy = parse_soak_policy(_policy_document())
    receipt = validate_start_receipt(_start(policy.policy_hash), policy)

    assert receipt["authorization_class"] == "OPERATOR_ASSERTED_TESTNET_ONLY"
    assert receipt["testnet_mandate_hash"] == "2" * 64

    fabricated = _start(policy.policy_hash)
    fabricated["authorization_class"] = "STRONG_IDENTITY_VERIFIED"
    with pytest.raises(MftSoakError, match="authorization_class"):
        validate_start_receipt(fabricated, policy)

    missing_mandate = _start(policy.policy_hash)
    missing_mandate["testnet_mandate_hash"] = ""
    with pytest.raises(MftSoakError, match="testnet_mandate_hash"):
        validate_start_receipt(missing_mandate, policy)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda row: row["metrics"].__setitem__("intent_count", 0), "EMPTY_INTENT_SET"),
        (lambda row: row["metrics"].__setitem__("fill_count", 0), "EMPTY_FILL_SET"),
        (lambda row: row["metrics"].__setitem__("oms_unknown_count", 1), "OMS_UNKNOWN_EXCEEDED"),
        (lambda row: row["metrics"].__setitem__("mainnet_submission_count", 1), "MAINNET_SUBMISSION_OBSERVED"),
        (lambda row: row["evidence"]["market_continuity"].__setitem__("status", "HTTP_429"), "MARKET_CONTINUITY_NOT_VERIFIED"),
        (lambda row: row["evidence"]["parity"].__setitem__("status", "TIMEOUT"), "PARITY_NOT_VERIFIED"),
    ],
)
def test_cycle_failures_are_recordable_but_never_pass(mutation, reason: str) -> None:
    policy = parse_soak_policy(_policy_document())
    row = _cycle(policy.policy_hash, index=0, started_at_ms=1_000)
    mutation(row)

    result = evaluate_cycle(row, policy, _start(policy.policy_hash))

    assert result.status == "BLOCKED"
    assert reason in result.reason_codes
    assert result.micro_live_authority is False


def test_soak_status_requires_chain_duration_count_and_zero_failed_cycles() -> None:
    policy = parse_soak_policy(_policy_document())
    start = _start(policy.policy_hash)
    first = _cycle(policy.policy_hash, index=0, started_at_ms=1_000)
    second = _cycle(policy.policy_hash, index=1, started_at_ms=1_100)

    pending = build_soak_status(policy=policy, start_receipt=None, cycles=(), chain_valid=True, as_of_ms=900)
    running = build_soak_status(policy=policy, start_receipt=start, cycles=(first,), chain_valid=True, as_of_ms=1_050)
    passed = build_soak_status(policy=policy, start_receipt=start, cycles=(first, second), chain_valid=True, as_of_ms=1_150)

    assert pending["status"] == "OPERATIONAL_SOAK_PENDING"
    assert running["status"] == "OPERATIONAL_SOAK_RUNNING"
    assert passed["status"] == "OPERATIONAL_SOAK_PASSED"
    assert passed["micro_live_ready"] is False
    assert passed["live_order_authority"] is False

    failed = _cycle(policy.policy_hash, index=1, started_at_ms=1_100)
    failed["metrics"]["reconciliation_abs_error"] = 1.0
    blocked = build_soak_status(policy=policy, start_receipt=start, cycles=(first, failed), chain_valid=True, as_of_ms=1_150)
    assert blocked["status"] == "OPERATIONAL_SOAK_BLOCKED"
    assert "FAILED_CYCLE_LIMIT_EXCEEDED" in blocked["blockers"]

    corrupt = build_soak_status(policy=policy, start_receipt=start, cycles=(first, second), chain_valid=False, as_of_ms=1_150)
    assert corrupt["status"] == "OPERATIONAL_SOAK_BLOCKED"
    assert "SOAK_EVENT_CHAIN_INVALID" in corrupt["blockers"]


@pytest.mark.parametrize(
    ("cycles", "reason"),
    [
        (
            (
                {"index": 0, "started_at_ms": 1_200, "completed_at_ms": None},
                {"index": 1, "started_at_ms": 1_300, "completed_at_ms": None},
            ),
            "CYCLE_GAP_EXCEEDED",
        ),
        (
            (
                {"index": 0, "started_at_ms": 1_100, "completed_at_ms": None},
                {"index": 1, "started_at_ms": 1_050, "completed_at_ms": None},
            ),
            "CYCLE_TIME_SEQUENCE_INVALID",
        ),
        (
            (
                {"index": 0, "started_at_ms": 1_000, "completed_at_ms": 1_200},
                {"index": 1, "started_at_ms": 1_100, "completed_at_ms": 1_150},
            ),
            "CYCLE_DURATION_EXCEEDED",
        ),
    ],
)
def test_soak_status_rejects_non_continuous_or_non_chronological_cycles(
    cycles, reason: str
) -> None:
    policy = parse_soak_policy(_policy_document())
    start = _start(policy.policy_hash)
    rows = []
    for item in cycles:
        row = _cycle(
            policy.policy_hash,
            index=item["index"],
            started_at_ms=item["started_at_ms"],
        )
        if item["completed_at_ms"] is not None:
            row["completed_at_ms"] = item["completed_at_ms"]
            for evidence in row["evidence"].values():
                evidence["observed_at_ms"] = item["completed_at_ms"]
        rows.append(row)

    status = build_soak_status(
        policy=policy,
        start_receipt=start,
        cycles=tuple(rows),
        chain_valid=True,
        as_of_ms=1_300,
    )

    assert status["status"] == "OPERATIONAL_SOAK_BLOCKED"
    assert reason in status["blockers"]


def test_store_is_append_only_idempotent_and_hash_chained(tmp_path) -> None:
    policy = parse_soak_policy(_policy_document())
    start = _start(policy.policy_hash)
    first = _cycle(policy.policy_hash, index=0, started_at_ms=1_000)
    path = tmp_path / "soak.db"
    evidence_root = tmp_path / "cycle-evidence"
    _materialize_evidence(evidence_root, first)
    store = MftSoakStore(str(path), create=True)
    try:
        assert store.append_start(start, policy=policy, recorded_at_ms=1_000) is True
        assert store.append_start(start, policy=policy, recorded_at_ms=1_000) is False
        assert store.append_cycle(first, policy=policy, evidence_root=evidence_root, recorded_at_ms=1_050) is True
        assert store.append_cycle(first, policy=policy, evidence_root=evidence_root, recorded_at_ms=1_050) is False
        assert store.chain_valid() is True
        assert store.start_receipt("task39-run-001") == start
        assert store.cycles("task39-run-001") == [first]

        conflict = dict(first)
        conflict["completed_at_ms"] = 1_060
        with pytest.raises(MftSoakStoreError, match="reuse"):
            store.append_cycle(conflict, policy=policy, evidence_root=evidence_root, recorded_at_ms=1_060)

        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            store._conn.execute("UPDATE mft_soak_events SET prev_hash='bad' WHERE seq=1")
    finally:
        store.close()


def test_store_rejects_missing_tampered_and_unsafe_cycle_artifacts(tmp_path) -> None:
    policy = parse_soak_policy(_policy_document())
    start = _start(policy.policy_hash)
    row = _cycle(policy.policy_hash, index=0, started_at_ms=1_000)
    evidence_root = tmp_path / "evidence"
    _materialize_evidence(evidence_root, row)
    store = MftSoakStore(str(tmp_path / "soak.db"), create=True)
    try:
        store.append_start(start, policy=policy, recorded_at_ms=1_000)
        (evidence_root / row["evidence"]["market_continuity"]["artifact"]).write_text("tampered")
        with pytest.raises(MftSoakStoreError, match="digest mismatch"):
            store.append_cycle(row, policy=policy, evidence_root=evidence_root, recorded_at_ms=1_050)

        row["evidence"]["market_continuity"]["artifact"] = "../outside.json"
        with pytest.raises(MftSoakStoreError, match="unsafe"):
            store.append_cycle(row, policy=policy, evidence_root=evidence_root, recorded_at_ms=1_050)

        row["evidence"]["market_continuity"]["artifact"] = "inside.json:stream"
        with pytest.raises(MftSoakStoreError, match="unsafe"):
            store.append_cycle(row, policy=policy, evidence_root=evidence_root, recorded_at_ms=1_050)
    finally:
        store.close()
