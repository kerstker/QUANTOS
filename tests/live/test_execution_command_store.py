from __future__ import annotations

import sqlite3

import pytest

from quantos.contracts.mft_execution import create_testnet_execution_intent
from quantos.contracts.portfolio import TargetPosition


def _intent(*, requested: bool = True, btc_weight: float = 0.25):
    return create_testnet_execution_intent(
        portfolio_id="hourly-paper",
        alpha_ids=("a" * 64,),
        decided_at_ms=1_000_000,
        expires_at_ms=1_100_000,
        targets=(TargetPosition(instrument_id="BTC", target_weight=btc_weight),),
        market_watermark_ms=999_950,
        market_snapshot_hash="1" * 64,
        workflow_receipt_hash="2" * 64,
        validation_artifact_hashes=("3" * 64,),
        policy_version="mft-testnet.v1",
        policy_hash="4" * 64,
        testnet_submission_requested=requested,
    )


def test_enqueue_is_idempotent_and_rejects_malformed_identity(tmp_path) -> None:
    from live.execution.command_store import (
        ExecutionCommandError,
        ExecutionCommandState,
        SqliteExecutionCommandStore,
    )

    store = SqliteExecutionCommandStore(str(tmp_path / "commands.db"), create=True)
    first = store.enqueue(_intent(), recorded_at_ms=1_000_001)
    replay = store.enqueue(_intent(), recorded_at_ms=1_000_002)

    assert first == replay
    assert first.state is ExecutionCommandState.RECEIVED
    assert store.row_count() == 1
    malformed = _intent().model_copy(update={"portfolio_id": "tampered"})
    with pytest.raises(ExecutionCommandError, match="intent"):
        store.enqueue(malformed, recorded_at_ms=1_000_003)
    assert store.row_count() == 1
    store.close()


def test_command_cannot_be_received_before_its_bound_decision(tmp_path) -> None:
    from live.execution.command_store import (
        ExecutionCommandError,
        SqliteExecutionCommandStore,
    )

    store = SqliteExecutionCommandStore(str(tmp_path / "commands.db"), create=True)

    with pytest.raises(ExecutionCommandError, match="before its decision"):
        store.enqueue(_intent(), recorded_at_ms=999_999)

    assert store.row_count() == 0
    store.close()


def test_two_workers_cannot_claim_the_same_validated_command(tmp_path) -> None:
    from live.execution.command_store import (
        ExecutionCommandState,
        ExecutionCommandStateError,
        SqliteExecutionCommandStore,
    )

    path = str(tmp_path / "commands.db")
    first_store = SqliteExecutionCommandStore(path, create=True)
    second_store = SqliteExecutionCommandStore(path)
    received = first_store.enqueue(_intent(), recorded_at_ms=1_000_001)
    first_store.mark_validated(received.command_id, recorded_at_ms=1_000_002)

    claimed = first_store.claim(
        received.command_id,
        worker_id="worker-a",
        now_ms=1_000_003,
        lease_until_ms=1_000_100,
    )

    assert claimed.state is ExecutionCommandState.IN_FLIGHT
    assert claimed.fencing_token == 1
    with pytest.raises(ExecutionCommandStateError, match="IN_FLIGHT"):
        second_store.claim(
            received.command_id,
            worker_id="worker-b",
            now_ms=1_000_004,
            lease_until_ms=1_000_200,
        )
    first_store.close()
    second_store.close()


def test_stale_worker_is_fenced_and_expired_in_flight_becomes_unknown(tmp_path) -> None:
    from live.execution.command_store import (
        ExecutionCommandState,
        ExecutionCommandStateError,
        SqliteExecutionCommandStore,
    )

    store = SqliteExecutionCommandStore(str(tmp_path / "commands.db"), create=True)
    received = store.enqueue(_intent(), recorded_at_ms=1_000_001)
    store.mark_validated(received.command_id, recorded_at_ms=1_000_002)
    claim = store.claim(
        received.command_id,
        worker_id="worker-a",
        now_ms=1_000_003,
        lease_until_ms=1_000_010,
    )

    assert store.mark_expired_in_flight_unknown(now_ms=1_000_009) == ()
    unknowns = store.mark_expired_in_flight_unknown(now_ms=1_000_010)

    assert len(unknowns) == 1
    assert unknowns[0].state is ExecutionCommandState.UNKNOWN
    assert unknowns[0].reason_codes == ("WORKER_LEASE_EXPIRED",)
    with pytest.raises(ExecutionCommandStateError, match="UNKNOWN"):
        store.settle(
            received.command_id,
            worker_id="worker-a",
            fencing_token=claim.fencing_token,
            state=ExecutionCommandState.COMPLETED,
            recorded_at_ms=1_000_011,
            result={"submitted": 1},
        )
    store.close()


def test_only_current_owner_can_settle_and_terminal_state_is_immutable(tmp_path) -> None:
    from live.execution.command_store import (
        ExecutionCommandState,
        ExecutionCommandStateError,
        SqliteExecutionCommandStore,
    )

    store = SqliteExecutionCommandStore(str(tmp_path / "commands.db"), create=True)
    received = store.enqueue(_intent(), recorded_at_ms=1_000_001)
    store.mark_validated(received.command_id, recorded_at_ms=1_000_002)
    claim = store.claim(
        received.command_id,
        worker_id="worker-a",
        now_ms=1_000_003,
        lease_until_ms=1_000_100,
    )

    with pytest.raises(ExecutionCommandStateError, match="owner"):
        store.settle(
            received.command_id,
            worker_id="worker-b",
            fencing_token=claim.fencing_token,
            state=ExecutionCommandState.COMPLETED,
            recorded_at_ms=1_000_004,
            result={"submitted": 1},
        )
    completed = store.settle(
        received.command_id,
        worker_id="worker-a",
        fencing_token=claim.fencing_token,
        state=ExecutionCommandState.COMPLETED,
        recorded_at_ms=1_000_005,
        result={"submitted": 1},
    )

    assert completed.state is ExecutionCommandState.COMPLETED
    with pytest.raises(ExecutionCommandStateError, match="COMPLETED"):
        store.block(
            received.command_id,
            reason_codes=("LATE_BLOCK",),
            recorded_at_ms=1_000_006,
        )
    store.close()


def test_unknown_requires_explicit_evidence_backed_resolution(tmp_path) -> None:
    from live.execution.command_store import (
        ExecutionCommandState,
        ExecutionCommandStateError,
        SqliteExecutionCommandStore,
    )

    store = SqliteExecutionCommandStore(str(tmp_path / "commands.db"), create=True)
    received = store.enqueue(_intent(), recorded_at_ms=1_000_001)
    store.mark_validated(received.command_id, recorded_at_ms=1_000_002)
    claim = store.claim(
        received.command_id,
        worker_id="worker-a",
        now_ms=1_000_003,
        lease_until_ms=1_000_100,
    )
    store.settle(
        received.command_id,
        worker_id="worker-a",
        fencing_token=claim.fencing_token,
        state=ExecutionCommandState.UNKNOWN,
        recorded_at_ms=1_000_004,
        reason_codes=("ACK_UNCERTAIN",),
        result={"recovery_required": True},
    )

    with pytest.raises(ExecutionCommandStateError, match="evidence"):
        store.resolve_unknown(
            received.command_id,
            state=ExecutionCommandState.COMPLETED,
            recorded_at_ms=1_000_005,
            recovery_evidence_hash="",
            result={"venue_status": "FILLED"},
        )
    resolved = store.resolve_unknown(
        received.command_id,
        state=ExecutionCommandState.COMPLETED,
        recorded_at_ms=1_000_006,
        recovery_evidence_hash="f" * 64,
        result={"venue_status": "FILLED"},
    )

    assert resolved.state is ExecutionCommandState.COMPLETED
    assert resolved.result["recovery_evidence_hash"] == "f" * 64
    store.close()


def test_store_is_append_only_hash_chained_and_detects_tampering(tmp_path) -> None:
    from live.execution.command_store import (
        ExecutionCommandError,
        SqliteExecutionCommandStore,
    )

    path = tmp_path / "commands.db"
    store = SqliteExecutionCommandStore(str(path), create=True)
    received = store.enqueue(_intent(), recorded_at_ms=1_000_001)
    store.mark_validated(received.command_id, recorded_at_ms=1_000_002)
    assert store.chain_valid()
    store.close()

    connection = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(
            "UPDATE testnet_execution_command_events SET state='BLOCKED' WHERE seq=1"
        )
    connection.rollback()
    connection.execute("DROP TRIGGER testnet_execution_command_events_no_update")
    connection.execute(
        "UPDATE testnet_execution_command_events SET row_hash=? WHERE seq=2",
        ("0" * 64,),
    )
    connection.commit()
    connection.close()

    with pytest.raises(ExecutionCommandError, match="row hash"):
        SqliteExecutionCommandStore(str(path))
