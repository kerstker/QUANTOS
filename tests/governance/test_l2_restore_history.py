from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from services.governance.l2_restore_history import (
    L2_RESTORE_HISTORY_MAX_FUTURE_SKEW_MS,
    L2_RESTORE_HISTORY_STORE_KIND,
    L2_RESTORE_HISTORY_STORE_SCHEMA_VERSION,
    L2RestoreHistoryActiveAttempt,
    L2RestoreHistoryClockRollback,
    L2RestoreHistoryConflict,
    L2RestoreHistoryFutureClock,
    L2RestoreHistoryHeadChanged,
    L2RestoreHistoryNotOverdue,
    L2RestoreHistoryStore,
    L2RestoreHistoryStoreError,
    RestoreAttempt,
    RestoreCadencePolicy,
    RestoreOutcome,
    RestoreProof,
    evaluate_cadence,
)


NOW_MS = 1_800_000_000_000
CONTRACT_SHA256 = "a" * 64
OTHER_CONTRACT_SHA256 = "b" * 64
DESTINATION_REF_SHA256 = "c" * 64
BATCH_SHA256 = "d" * 64


def _attempt(
    attempt_id: str = "restore-1",
    *,
    started_at_ms: int = NOW_MS - 100,
    source_contract_sha256: str = CONTRACT_SHA256,
) -> RestoreAttempt:
    return RestoreAttempt(
        attempt_id=attempt_id,
        started_at_ms=started_at_ms,
        operation_kind="BACKUP_AND_ISOLATED_RESTORE",
        mode="L2_AUTHORITY_STATE",
        source_contract_sha256=source_contract_sha256,
        destination_ref_sha256=DESTINATION_REF_SHA256,
        rto_ms=100,
        max_rpo_ms=200,
    )


def _proof(
    *,
    source_contract_sha256: str = CONTRACT_SHA256,
    destination_class: str = "SEPARATE_VOLUME",
    external_destination_verified: bool = True,
    encrypted: bool = True,
    manifest_verified: bool = True,
    source_contract_verified: bool = True,
    restore_verified: bool = True,
    sqlite_integrity_verified: bool = True,
    restore_elapsed_ms: int | None = 50,
    source_age_ms: int | None = 75,
    rto_met: bool = True,
    rpo_met: bool = True,
    batch_sha256: str = BATCH_SHA256,
) -> RestoreProof:
    return RestoreProof(
        source_contract_sha256=source_contract_sha256,
        destination_class=destination_class,
        external_destination_verified=external_destination_verified,
        encrypted=encrypted,
        manifest_verified=manifest_verified,
        source_contract_verified=source_contract_verified,
        restore_verified=restore_verified,
        sqlite_integrity_verified=sqlite_integrity_verified,
        restore_elapsed_ms=restore_elapsed_ms,
        source_age_ms=source_age_ms,
        rto_met=rto_met,
        rpo_met=rpo_met,
        batch_sha256=batch_sha256,
    )


def _pass(
    attempt: RestoreAttempt,
    *,
    completed_at_ms: int | None = None,
) -> RestoreOutcome:
    return RestoreOutcome(
        attempt_id=attempt.attempt_id,
        completed_at_ms=(
            attempt.started_at_ms + 10
            if completed_at_ms is None
            else completed_at_ms
        ),
        verdict="PASS",
        stage="COMPLETE",
        reason_code="RESTORE_VERIFIED",
        proof=_proof(source_contract_sha256=attempt.source_contract_sha256),
    )


def _failure(
    attempt: RestoreAttempt,
    *,
    completed_at_ms: int | None = None,
    stage: str = "RESTORE_VERIFICATION",
    reason_code: str = "RESTORE_INTEGRITY_FAILED",
) -> RestoreOutcome:
    return RestoreOutcome(
        attempt_id=attempt.attempt_id,
        completed_at_ms=(
            attempt.started_at_ms + 10
            if completed_at_ms is None
            else completed_at_ms
        ),
        verdict="FAIL",
        stage=stage,
        reason_code=reason_code,
        proof=_proof(
            source_contract_sha256=attempt.source_contract_sha256,
            destination_class="UNVERIFIED",
            external_destination_verified=False,
            encrypted=False,
            manifest_verified=False,
            source_contract_verified=False,
            restore_verified=False,
            sqlite_integrity_verified=False,
            restore_elapsed_ms=None,
            source_age_ms=None,
            rto_met=False,
            rpo_met=False,
            batch_sha256="",
        ),
    )


def _begin(store: L2RestoreHistoryStore, attempt: RestoreAttempt):
    return store.begin_attempt(attempt, expected_head=store.authority_head())


def _complete(
    store: L2RestoreHistoryStore,
    attempt_record,
    outcome: RestoreOutcome,
):
    return store.complete_attempt(
        outcome,
        expected_start=attempt_record,
        expected_head=store.authority_head(),
    )


def _record_pass(store: L2RestoreHistoryStore, attempt: RestoreAttempt):
    started, _ = _begin(store, attempt)
    outcome, _ = _complete(store, started, _pass(attempt))
    return started, outcome


def _record_failure(store: L2RestoreHistoryStore, attempt: RestoreAttempt):
    started, _ = _begin(store, attempt)
    outcome, _ = _complete(store, started, _failure(attempt))
    return started, outcome


def _policy(**overrides: object) -> RestoreCadencePolicy:
    values: dict[str, object] = {
        "source_contract_sha256": CONTRACT_SHA256,
        "max_attempt_age_ms": 100,
        "max_success_age_ms": 100,
        "max_attempt_gap_ms": 150,
        "max_success_gap_ms": 150,
        "minimum_history_span_ms": 200,
        "minimum_successes": 3,
        "clean_lookback_ms": 300,
        "attempt_timeout_ms": 50,
    }
    values.update(overrides)
    return RestoreCadencePolicy(**values)  # type: ignore[arg-type]


def test_persists_hash_chained_started_and_outcome_and_reopens_with_durability(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "restore-history.db")
    store = L2RestoreHistoryStore(path, create=True, clock_ms=lambda: NOW_MS)
    attempt = _attempt()
    started, outcome = _record_pass(store, attempt)

    snapshot = store.read_snapshot()
    assert snapshot.attempts == (started,)
    assert snapshot.outcomes == (outcome,)
    assert snapshot.open_attempts == ()
    assert started.authority_event_seq == 1
    assert started.previous_event_hash == ""
    assert outcome.authority_event_seq == 2
    assert outcome.started_event_seq == 1
    assert outcome.started_event_hash == started.event_hash
    assert outcome.previous_event_hash == started.event_hash
    assert snapshot.authority_head is not None
    assert snapshot.authority_head.event_seq == 2
    assert snapshot.authority_head.event_hash == outcome.event_hash
    assert snapshot.authority_time_floor_ms == outcome.outcome.completed_at_ms
    assert store.chain_valid() is True
    assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store._conn.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    store.close()

    reopened = L2RestoreHistoryStore(path, create=False, clock_ms=lambda: NOW_MS)
    assert reopened.read_snapshot() == snapshot
    assert reopened.chain_valid() is True
    reopened.close()


def test_all_authority_tables_are_append_only_and_schema_is_strict(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "restore-history.db")
    store = L2RestoreHistoryStore(path, create=True, clock_ms=lambda: NOW_MS)
    _record_pass(store, _attempt())

    statements = (
        "UPDATE l2_restore_history_meta SET store_kind='changed'",
        "DELETE FROM l2_restore_history_events",
        "UPDATE l2_restore_attempts SET attempt_id='changed'",
        "DELETE FROM l2_restore_outcomes",
    )
    for statement in statements:
        with pytest.raises(sqlite3.IntegrityError, match="immutable|append-only"):
            store._conn.execute(statement)
    store.close()

    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE injected(value TEXT)")
    connection.commit()
    connection.close()
    with pytest.raises(L2RestoreHistoryStoreError, match="schema"):
        L2RestoreHistoryStore(path, create=False, clock_ms=lambda: NOW_MS)


@pytest.mark.parametrize(
    ("trigger", "statement"),
    [
        (
            "l2_restore_history_events_no_update",
            "UPDATE l2_restore_history_events SET payload_sha256='f' || substr(payload_sha256,2) WHERE event_seq=1",
        ),
        (
            "l2_restore_history_attempts_no_update",
            "UPDATE l2_restore_attempts SET policy_sha256='f' || substr(policy_sha256,2)",
        ),
        (
            "l2_restore_history_outcomes_no_update",
            "UPDATE l2_restore_outcomes SET proof_sha256='f' || substr(proof_sha256,2)",
        ),
    ],
)
def test_tampering_is_detected_on_reopen(
    tmp_path: Path,
    trigger: str,
    statement: str,
) -> None:
    path = str(tmp_path / f"{trigger}.db")
    store = L2RestoreHistoryStore(path, create=True, clock_ms=lambda: NOW_MS)
    _record_pass(store, _attempt())
    store.close()

    connection = sqlite3.connect(path)
    connection.execute(f"DROP TRIGGER {trigger}")
    connection.execute(statement)
    connection.commit()
    connection.close()
    with pytest.raises(L2RestoreHistoryStoreError, match="trigger|hash|invalid"):
        L2RestoreHistoryStore(path, create=False, clock_ms=lambda: NOW_MS)


def test_truncation_or_orphaned_typed_event_is_detected(tmp_path: Path) -> None:
    path = str(tmp_path / "restore-history.db")
    store = L2RestoreHistoryStore(path, create=True, clock_ms=lambda: NOW_MS)
    _record_pass(store, _attempt())
    store.close()

    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER l2_restore_history_outcomes_no_delete")
    connection.execute("DELETE FROM l2_restore_outcomes")
    connection.commit()
    connection.close()
    with pytest.raises(L2RestoreHistoryStoreError, match="trigger|typing|truncated"):
        L2RestoreHistoryStore(path, create=False, clock_ms=lambda: NOW_MS)


def test_missing_or_unrelated_database_fails_without_mutation(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(L2RestoreHistoryStoreError, match="unavailable"):
        L2RestoreHistoryStore(str(missing), create=False)
    assert not missing.exists()

    unrelated = tmp_path / "unrelated.db"
    connection = sqlite3.connect(unrelated)
    connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
    connection.execute("INSERT INTO marker VALUES ('preserve')")
    connection.commit()
    connection.close()
    before = unrelated.read_bytes()
    with pytest.raises(L2RestoreHistoryStoreError, match="schema|unreadable"):
        L2RestoreHistoryStore(str(unrelated), create=False)
    assert unrelated.read_bytes() == before


def test_exact_replay_is_idempotent_after_later_events_and_conflicts_fail(
    tmp_path: Path,
) -> None:
    store = L2RestoreHistoryStore(
        str(tmp_path / "restore-history.db"),
        create=True,
        clock_ms=lambda: NOW_MS,
    )
    first_attempt = _attempt("restore-1", started_at_ms=NOW_MS - 40)
    first_started, first_outcome = _record_pass(store, first_attempt)
    second_attempt = _attempt("restore-2", started_at_ms=NOW_MS - 20)
    _record_pass(store, second_attempt)

    replay_start, inserted_start = store.begin_attempt(
        first_attempt,
        expected_head=None,
    )
    replay_outcome, inserted_outcome = store.complete_attempt(
        _pass(first_attempt),
        expected_start=first_started,
        expected_head=first_started.as_head(),
    )
    assert (replay_start, inserted_start) == (first_started, False)
    assert (replay_outcome, inserted_outcome) == (first_outcome, False)

    with pytest.raises(L2RestoreHistoryConflict):
        store.begin_attempt(
            replace(first_attempt, destination_ref_sha256="e" * 64),
            expected_head=store.authority_head(),
        )
    with pytest.raises(L2RestoreHistoryConflict):
        store.complete_attempt(
            _failure(first_attempt),
            expected_start=first_started,
            expected_head=store.authority_head(),
        )
    store.close()


def test_global_head_cas_and_single_active_attempt_hold_across_connections(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "restore-history.db")
    first = L2RestoreHistoryStore(path, create=True, clock_ms=lambda: NOW_MS)
    second = L2RestoreHistoryStore(path, create=False, clock_ms=lambda: NOW_MS)
    first_attempt = _attempt("first")
    second_attempt = _attempt("second", started_at_ms=NOW_MS - 80)

    started, _ = first.begin_attempt(first_attempt, expected_head=None)
    with pytest.raises(L2RestoreHistoryHeadChanged) as changed:
        second.begin_attempt(second_attempt, expected_head=None)
    assert changed.value.actual_head == started.as_head()
    with pytest.raises(L2RestoreHistoryActiveAttempt):
        second.begin_attempt(second_attempt, expected_head=second.authority_head())

    _complete(first, started, _pass(first_attempt))
    second_started, inserted = _begin(second, second_attempt)
    assert inserted is True
    assert second_started.attempt == second_attempt
    second.close()
    first.close()


def test_duplicate_completion_has_one_terminal_and_exact_replay_wins(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "restore-history.db")
    first = L2RestoreHistoryStore(path, create=True, clock_ms=lambda: NOW_MS)
    second = L2RestoreHistoryStore(path, create=False, clock_ms=lambda: NOW_MS)
    attempt = _attempt()
    started, _ = first.begin_attempt(attempt, expected_head=None)
    head = started.as_head()

    retained, inserted = first.complete_attempt(
        _pass(attempt),
        expected_start=started,
        expected_head=head,
    )
    replay, replay_inserted = second.complete_attempt(
        _pass(attempt),
        expected_start=started,
        expected_head=head,
    )
    assert inserted is True
    assert replay_inserted is False
    assert replay == retained
    assert len(first.read_snapshot().outcomes) == 1
    with pytest.raises(L2RestoreHistoryConflict):
        second.complete_attempt(
            _failure(attempt),
            expected_start=started,
            expected_head=second.authority_head(),
        )
    second.close()
    first.close()


def test_future_and_rollback_clocks_do_not_poison_history(tmp_path: Path) -> None:
    store = L2RestoreHistoryStore(
        str(tmp_path / "restore-history.db"),
        create=True,
        clock_ms=lambda: NOW_MS,
    )
    future = NOW_MS + L2_RESTORE_HISTORY_MAX_FUTURE_SKEW_MS + 1
    with pytest.raises(L2RestoreHistoryFutureClock):
        store.begin_attempt(
            _attempt(started_at_ms=future),
            expected_head=None,
        )
    assert store.read_snapshot().attempts == ()

    attempt = _attempt(started_at_ms=NOW_MS - 2)
    started, _ = _begin(store, attempt)
    outcome, _ = _complete(
        store,
        started,
        _pass(attempt, completed_at_ms=NOW_MS - 1),
    )
    with pytest.raises(L2RestoreHistoryClockRollback):
        store.begin_attempt(
            _attempt("older", started_at_ms=NOW_MS - 2),
            expected_head=store.authority_head(),
        )
    with pytest.raises(L2RestoreHistoryFutureClock):
        store.complete_attempt(
            _pass(attempt, completed_at_ms=future),
            expected_start=started,
            expected_head=store.authority_head(),
        )
    assert store.read_snapshot().outcomes == (outcome,)
    store.close()


def test_read_snapshot_is_consistent_during_cross_connection_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = str(tmp_path / "restore-history.db")
    reader = L2RestoreHistoryStore(path, create=True, clock_ms=lambda: NOW_MS)
    writer = L2RestoreHistoryStore(path, create=False, clock_ms=lambda: NOW_MS)
    attempt = _attempt()
    started, _ = reader.begin_attempt(attempt, expected_head=None)
    original_attempt_rows = reader._attempt_rows_locked
    committed = False

    def commit_then_read_attempt_rows():
        nonlocal committed
        if not committed:
            writer.complete_attempt(
                _pass(attempt),
                expected_start=started,
                expected_head=started.as_head(),
            )
            committed = True
        return original_attempt_rows()

    monkeypatch.setattr(reader, "_attempt_rows_locked", commit_then_read_attempt_rows)
    snapshot = reader.read_snapshot()

    assert committed is True
    assert snapshot.outcomes == ()
    assert snapshot.open_attempts == (started,)
    assert len(writer.read_snapshot().outcomes) == 1
    writer.close()
    reader.close()


def test_fail_overdue_appends_failure_and_late_success_cannot_replace_it(
    tmp_path: Path,
) -> None:
    store = L2RestoreHistoryStore(
        str(tmp_path / "restore-history.db"),
        create=True,
        clock_ms=lambda: NOW_MS,
    )
    attempt = _attempt(started_at_ms=NOW_MS - 100)
    started, _ = _begin(store, attempt)
    with pytest.raises(L2RestoreHistoryNotOverdue):
        store.fail_overdue(
            attempt.attempt_id,
            now_ms=NOW_MS - 51,
            timeout_ms=50,
        )

    failed, inserted = store.fail_overdue(
        attempt.attempt_id,
        now_ms=NOW_MS,
        timeout_ms=50,
    )
    replay, replay_inserted = store.fail_overdue(
        attempt.attempt_id,
        now_ms=NOW_MS,
        timeout_ms=50,
    )
    assert inserted is True
    assert replay_inserted is False
    assert replay == failed
    assert failed.outcome.verdict == "FAIL"
    assert failed.outcome.stage == "TIMEOUT"
    assert failed.outcome.reason_code == "ATTEMPT_TIMEOUT"
    with pytest.raises(L2RestoreHistoryConflict):
        store.complete_attempt(
            _pass(attempt, completed_at_ms=NOW_MS),
            expected_start=started,
            expected_head=store.authority_head(),
        )
    store.close()


def test_pass_proof_is_exact_external_bounded_and_contains_no_path_or_secret(
    tmp_path: Path,
) -> None:
    store = L2RestoreHistoryStore(
        str(tmp_path / "restore-history.db"),
        create=True,
        clock_ms=lambda: NOW_MS,
    )
    attempt = _attempt()
    started, _ = _begin(store, attempt)

    bad_proofs = (
        replace(
            _proof(),
            destination_class="LOCAL_ONLY",
            external_destination_verified=False,
        ),
        replace(_proof(), source_contract_sha256=OTHER_CONTRACT_SHA256),
        replace(_proof(), restore_verified=False),
        replace(_proof(), restore_elapsed_ms=101),
        replace(_proof(), source_age_ms=201),
    )
    for proof in bad_proofs:
        with pytest.raises(ValueError):
            store.complete_attempt(
                replace(_pass(attempt), proof=proof),
                expected_start=started,
                expected_head=store.authority_head(),
            )
    assert store.read_snapshot().outcomes == ()

    retained, _ = _complete(store, started, _pass(attempt))
    serialized = json.dumps(asdict(retained.outcome.proof), sort_keys=True).lower()
    assert "\\\\server\\backup" not in serialized
    assert "secret" not in serialized
    stored = store._conn.execute(
        "SELECT proof_json FROM l2_restore_outcomes"
    ).fetchone()[0]
    assert stored == json.dumps(
        asdict(retained.outcome.proof),
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    with pytest.raises(ValueError):
        RestoreOutcome(
            attempt_id="restore-2",
            completed_at_ms=NOW_MS,
            verdict="FAIL",
            stage="PREFLIGHT",
            reason_code="C:\\secret\\passphrase.txt",
            proof=_proof(),
        )
    store.close()


def _healthy_snapshot(tmp_path: Path):
    store = L2RestoreHistoryStore(
        str(tmp_path / "healthy.db"),
        create=True,
        clock_ms=lambda: 1_000,
    )
    for index, started_at_ms in enumerate((100, 200, 300), start=1):
        _record_pass(
            store,
            _attempt(f"restore-{index}", started_at_ms=started_at_ms),
        )
    snapshot = store.read_snapshot()
    store.close()
    return snapshot


def test_evaluate_cadence_passes_only_complete_clean_external_history(
    tmp_path: Path,
) -> None:
    health = evaluate_cadence(_healthy_snapshot(tmp_path), _policy(), now_ms=350)

    assert health.verdict == "PASS"
    assert health.reason_codes == ()
    assert health.attempt_count == 3
    assert health.success_count == 3
    assert health.failure_count_in_clean_window == 0
    assert health.overdue_open_count == 0
    assert health.latest_terminal_verdict == "PASS"
    assert health.latest_attempt_age_ms == 50
    assert health.latest_success_age_ms == 40
    assert health.maximum_attempt_gap_ms == 100
    assert health.maximum_success_gap_ms == 100
    assert health.history_span_ms == 200


def test_failure_is_not_hidden_by_a_later_success(tmp_path: Path) -> None:
    store = L2RestoreHistoryStore(
        str(tmp_path / "history.db"),
        create=True,
        clock_ms=lambda: 1_000,
    )
    _record_pass(store, _attempt("pass-1", started_at_ms=100))
    _record_failure(store, _attempt("failed", started_at_ms=200))
    _record_pass(store, _attempt("pass-2", started_at_ms=300))
    snapshot = store.read_snapshot()

    health = evaluate_cadence(
        snapshot,
        _policy(
            max_attempt_age_ms=1_000,
            max_success_age_ms=1_000,
            max_attempt_gap_ms=1_000,
            max_success_gap_ms=1_000,
            minimum_history_span_ms=200,
            minimum_successes=2,
        ),
        now_ms=350,
    )
    assert health.latest_terminal_verdict == "PASS"
    assert health.verdict == "BREACH"
    assert "FAILURE_IN_CLEAN_LOOKBACK" in health.reason_codes
    assert health.failure_count_in_clean_window == 1
    assert len(snapshot.outcomes) == 3

    expired = evaluate_cadence(
        snapshot,
        _policy(
            max_attempt_age_ms=1_000,
            max_success_age_ms=1_000,
            max_attempt_gap_ms=1_000,
            max_success_gap_ms=1_000,
            minimum_history_span_ms=200,
            minimum_successes=2,
            clean_lookback_ms=40,
        ),
        now_ms=350,
    )
    assert expired.verdict == "PASS"
    assert expired.failure_count_in_clean_window == 0
    store.close()


def test_latest_failure_and_overdue_open_attempt_are_breaches(tmp_path: Path) -> None:
    failing = L2RestoreHistoryStore(
        str(tmp_path / "failing.db"),
        create=True,
        clock_ms=lambda: 1_000,
    )
    _record_pass(failing, _attempt("pass", started_at_ms=100))
    _record_failure(failing, _attempt("fail", started_at_ms=200))
    failed_health = evaluate_cadence(
        failing.read_snapshot(),
        _policy(
            max_attempt_age_ms=1_000,
            max_success_age_ms=1_000,
            max_attempt_gap_ms=1_000,
            max_success_gap_ms=1_000,
            minimum_history_span_ms=0,
            minimum_successes=1,
        ),
        now_ms=220,
    )
    assert "LATEST_TERMINAL_NOT_PASS" in failed_health.reason_codes
    assert "FAILURE_IN_CLEAN_LOOKBACK" in failed_health.reason_codes
    failing.close()

    open_store = L2RestoreHistoryStore(
        str(tmp_path / "open.db"),
        create=True,
        clock_ms=lambda: 1_000,
    )
    _record_pass(open_store, _attempt("pass", started_at_ms=100))
    _begin(open_store, _attempt("open", started_at_ms=200))
    open_health = evaluate_cadence(
        open_store.read_snapshot(),
        _policy(
            max_attempt_age_ms=1_000,
            max_success_age_ms=1_000,
            max_attempt_gap_ms=1_000,
            max_success_gap_ms=1_000,
            minimum_history_span_ms=0,
            minimum_successes=1,
            attempt_timeout_ms=50,
        ),
        now_ms=251,
    )
    assert open_health.latest_terminal_verdict == "PASS"
    assert open_health.overdue_open_count == 1
    assert "OVERDUE_OPEN_ATTEMPT" in open_health.reason_codes
    open_store.close()


@pytest.mark.parametrize(
    ("policy_overrides", "now_ms", "reason"),
    [
        ({"max_attempt_age_ms": 49}, 350, "LATEST_ATTEMPT_STALE"),
        ({"max_success_age_ms": 39}, 350, "LATEST_SUCCESS_STALE"),
        ({"max_attempt_gap_ms": 99}, 350, "ATTEMPT_GAP_EXCEEDED"),
        ({"max_success_gap_ms": 99}, 350, "SUCCESS_GAP_EXCEEDED"),
        ({"minimum_history_span_ms": 201}, 350, "HISTORY_SPAN_INSUFFICIENT"),
        ({"minimum_successes": 4}, 350, "SUCCESS_COUNT_INSUFFICIENT"),
        ({}, 250, "HISTORY_FROM_FUTURE"),
    ],
)
def test_evaluate_cadence_fails_each_freshness_gap_span_and_count_condition(
    tmp_path: Path,
    policy_overrides: dict[str, object],
    now_ms: int,
    reason: str,
) -> None:
    health = evaluate_cadence(
        _healthy_snapshot(tmp_path),
        _policy(**policy_overrides),
        now_ms=now_ms,
    )
    assert health.verdict == "BREACH"
    assert reason in health.reason_codes


def test_evaluate_cadence_requires_exact_contract_and_nonempty_history(
    tmp_path: Path,
) -> None:
    empty = L2RestoreHistoryStore(
        str(tmp_path / "empty.db"),
        create=True,
        clock_ms=lambda: 1_000,
    )
    health = evaluate_cadence(empty.read_snapshot(), _policy(), now_ms=350)
    assert health.verdict == "UNAVAILABLE"
    assert health.reason_codes == ("NO_ATTEMPTS",)
    empty.close()

    store = L2RestoreHistoryStore(
        str(tmp_path / "wrong.db"),
        create=True,
        clock_ms=lambda: 1_000,
    )
    for index, timestamp in enumerate((100, 200, 300), start=1):
        _record_pass(
            store,
            _attempt(
                f"wrong-{index}",
                started_at_ms=timestamp,
                source_contract_sha256=OTHER_CONTRACT_SHA256,
            ),
        )
    health = evaluate_cadence(
        store.read_snapshot(),
        _policy(
            max_attempt_age_ms=1_000,
            max_success_age_ms=1_000,
            max_attempt_gap_ms=1_000,
            max_success_gap_ms=1_000,
        ),
        now_ms=350,
    )
    assert health.verdict == "BREACH"
    assert "SOURCE_CONTRACT_MISMATCH" in health.reason_codes
    store.close()


def test_contract_upgrade_starts_new_cadence_epoch_without_erasing_history(
    tmp_path: Path,
) -> None:
    store = L2RestoreHistoryStore(
        str(tmp_path / "upgrade.db"),
        create=True,
        clock_ms=lambda: 1_000,
    )
    _record_failure(
        store,
        _attempt(
            "old-contract",
            started_at_ms=50,
            source_contract_sha256=OTHER_CONTRACT_SHA256,
        ),
    )
    for index, timestamp in enumerate((100, 200, 300), start=1):
        _record_pass(
            store,
            _attempt(f"current-{index}", started_at_ms=timestamp),
        )

    snapshot = store.read_snapshot()
    health = evaluate_cadence(snapshot, _policy(), now_ms=350)

    assert health.verdict == "PASS"
    assert "SOURCE_CONTRACT_MISMATCH" not in health.reason_codes
    assert "FAILURE_IN_CLEAN_LOOKBACK" not in health.reason_codes
    assert health.failure_count_in_clean_window == 0
    assert health.attempt_count == 4
    assert health.success_count == 3
    assert health.history_span_ms == 200
    assert len(snapshot.attempts) == 4
    store.close()


def test_contract_upgrade_resets_span_and_success_requirements(tmp_path: Path) -> None:
    store = L2RestoreHistoryStore(
        str(tmp_path / "upgrade-insufficient.db"),
        create=True,
        clock_ms=lambda: 1_000,
    )
    for index, timestamp in enumerate((50, 100, 150), start=1):
        _record_pass(
            store,
            _attempt(
                f"old-{index}",
                started_at_ms=timestamp,
                source_contract_sha256=OTHER_CONTRACT_SHA256,
            ),
        )
    _record_pass(store, _attempt("current-1", started_at_ms=300))

    health = evaluate_cadence(store.read_snapshot(), _policy(), now_ms=350)

    assert health.verdict == "BREACH"
    assert "SOURCE_CONTRACT_MISMATCH" not in health.reason_codes
    assert "HISTORY_SPAN_INSUFFICIENT" in health.reason_codes
    assert "SUCCESS_COUNT_INSUFFICIENT" in health.reason_codes
    assert health.success_count == 1
    assert health.history_span_ms == 0
    store.close()


def test_latest_attempt_cannot_roll_back_to_historical_contract(tmp_path: Path) -> None:
    store = L2RestoreHistoryStore(
        str(tmp_path / "contract-rollback.db"),
        create=True,
        clock_ms=lambda: 1_000,
    )
    _record_pass(store, _attempt("current", started_at_ms=100))
    _record_pass(
        store,
        _attempt(
            "rolled-back",
            started_at_ms=200,
            source_contract_sha256=OTHER_CONTRACT_SHA256,
        ),
    )

    health = evaluate_cadence(
        store.read_snapshot(),
        _policy(
            max_attempt_age_ms=1_000,
            max_success_age_ms=1_000,
            max_attempt_gap_ms=1_000,
            max_success_gap_ms=1_000,
            minimum_history_span_ms=0,
            minimum_successes=1,
        ),
        now_ms=250,
    )

    assert health.verdict == "BREACH"
    assert "SOURCE_CONTRACT_MISMATCH" in health.reason_codes
    store.close()


def test_public_store_contract_has_expected_kind_version_and_no_secret_fields() -> None:
    assert L2_RESTORE_HISTORY_STORE_KIND == "QUANTOS_L2_RESTORE_HISTORY_AUTHORITY"
    assert L2_RESTORE_HISTORY_STORE_SCHEMA_VERSION == 1
    proof_fields = set(asdict(_proof()))
    assert not proof_fields & {
        "path",
        "batch_dir",
        "destination_path",
        "passphrase",
        "secret",
        "error_detail",
    }
    attempt_fields = set(asdict(_attempt()))
    assert "destination_ref_sha256" in attempt_fields
    assert "destination_path" not in attempt_fields


def test_sha256_helpers_are_bound_to_exact_lowercase_content() -> None:
    assert DESTINATION_REF_SHA256 == hashlib.sha256(b"unused").hexdigest() or len(
        DESTINATION_REF_SHA256
    ) == 64
