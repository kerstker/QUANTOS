from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

import pytest

from services.governance.artifact_admission import ArtifactAdmissionDecision
from services.governance.artifact_admission_store import (
    ARTIFACT_ADMISSION_STORE_KIND,
    ARTIFACT_ADMISSION_STORE_SCHEMA_VERSION,
    ARTIFACT_ADMISSION_MAX_FUTURE_SKEW_MS,
    POLICY_REGISTRY_KIND,
    RECEIPT_AUTHORITY_REGISTRY_KIND,
    ArtifactAdmissionRegistryHead,
    ArtifactAdmissionStore,
    ArtifactAdmissionStoreClockRollback,
    ArtifactAdmissionStoreFutureClock,
    ArtifactAdmissionStoreConflict,
    ArtifactAdmissionStoreError,
    ArtifactAdmissionStoreRegistryConflict,
    ArtifactAdmissionStoreRegistryGenerationGap,
    ArtifactAdmissionStoreRegistryHeadChanged,
    ArtifactAdmissionStoreRegistryHeadMismatch,
    ArtifactAdmissionStoreRegistryRollback,
    ArtifactAdmissionStoreRegistryUnprovisioned,
)


POLICY_SHA256 = "1" * 64
RECEIPT_SHA256 = "2" * 64
ARTIFACT_SHA256 = "3" * 64
RECORDED_AT_MS = 1_700_000_010_000
DEFAULT_DECISION_ID = "admission-release-1"


def _registry_bytes(
    registry_kind: str,
    generation: int,
    *,
    marker: str = "",
) -> bytes:
    if registry_kind == POLICY_REGISTRY_KIND:
        collection = "policies"
        entries = (
            {}
            if not marker
            else {
                "release-policy-v2": {
                    "policy_sha256": hashlib.sha256(marker.encode("utf-8")).hexdigest(),
                    "status": "REVOKED" if marker == "revoked" else "ACTIVE",
                }
            }
        )
    else:
        collection = "keys"
        entries = {} if not marker else {"marker": marker}
    return json.dumps(
        {
            "schema_version": 2,
            "readiness": "L2_DRY_RUN_ONLY",
            "generation": generation,
            collection: entries,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


POLICY_REGISTRY_V0 = _registry_bytes(POLICY_REGISTRY_KIND, 0)
RECEIPT_REGISTRY_V0 = _registry_bytes(RECEIPT_AUTHORITY_REGISTRY_KIND, 0)
POLICY_REGISTRY_SHA256 = hashlib.sha256(POLICY_REGISTRY_V0).hexdigest()
RECEIPT_AUTHORITY_REGISTRY_SHA256 = hashlib.sha256(RECEIPT_REGISTRY_V0).hexdigest()


def _decision(
    *,
    reason_code: str = "VERIFIED_PROVENANCE_POLICY_MATCH",
    recorded_at_ms: int = RECORDED_AT_MS,
    decision_id: str = DEFAULT_DECISION_ID,
    policy_registry_generation: int = 0,
    policy_registry_sha256: str = POLICY_REGISTRY_SHA256,
    receipt_authority_registry_generation: int = 0,
    receipt_authority_registry_sha256: str = (RECEIPT_AUTHORITY_REGISTRY_SHA256),
    policy_registry_event_seq: int = 1,
    policy_registry_event_hash: str = "b" * 64,
    receipt_authority_registry_event_seq: int = 2,
    receipt_authority_registry_event_hash: str = "c" * 64,
) -> ArtifactAdmissionDecision:
    return ArtifactAdmissionDecision(
        verdict="ALLOW",
        reason_code=reason_code,
        policy_id="release-policy-v2",
        policy_sha256=POLICY_SHA256,
        policy_registry_sha256=policy_registry_sha256,
        policy_registry_generation=policy_registry_generation,
        policy_registry_event_seq=policy_registry_event_seq,
        policy_registry_event_hash=policy_registry_event_hash,
        artifact_name="quantos-console.oci.tar",
        artifact_sha256=ARTIFACT_SHA256,
        bundle_sha256="4" * 64,
        trusted_root_sha256="5" * 64,
        certificate_identity=("https://github.example/workflows/release.yml@refs/heads/main"),
        certificate_oidc_issuer="https://token.actions.githubusercontent.com",
        subject_name="quantos-console.oci.tar",
        predicate_type="https://slsa.dev/provenance/v1",
        builder_id="https://github.com/actions/runner",
        build_type="https://quantos.example/build/console-oci/v1",
        source_uri="git+https://github.example/quantos",
        source_digest="6" * 40,
        verification_receipt_sha256=RECEIPT_SHA256,
        receipt_envelope_sha256="8" * 64,
        receipt_authority_registry_sha256=receipt_authority_registry_sha256,
        receipt_authority_registry_generation=(receipt_authority_registry_generation),
        receipt_authority_registry_event_seq=(receipt_authority_registry_event_seq),
        receipt_authority_registry_event_hash=(receipt_authority_registry_event_hash),
        receipt_authority_id="external-receipt-authority",
        receipt_key_id="receipt-key-2026-q3",
        receipt_public_key_sha256="a" * 64,
        receipt_signature_algorithm="Ed25519",
        receipt_signature_verified=True,
        receipt_id="receipt-release-1",
        verifier_id="sigstore-python-4.4.0-offline",
        verifier_runtime_id="sigstore-python-4.4.0-offline",
        certificate_sha256="7" * 64,
        verified_at_ms=1_700_000_000_000,
        transparency_integrated_at_ms=1_699_999_900_000,
        transparency_checkpoint_at_ms=1_699_999_950_000,
        revocation_status="GOOD",
        revocation_checked_at_ms=1_699_999_990_000,
        revocation_source="operator-approved-fulcio-status-v1",
        admission_at_ms=recorded_at_ms,
        authority_store_kind=ARTIFACT_ADMISSION_STORE_KIND,
        authority_store_schema_version=ARTIFACT_ADMISSION_STORE_SCHEMA_VERSION,
        authority_decision_id=decision_id,
    )


def _provision(
    store: ArtifactAdmissionStore,
    *,
    before_ms: int = RECORDED_AT_MS,
) -> tuple[ArtifactAdmissionRegistryHead, ArtifactAdmissionRegistryHead]:
    policy, policy_inserted = store.append_registry_snapshot(
        POLICY_REGISTRY_KIND,
        POLICY_REGISTRY_V0,
        recorded_at_ms=before_ms - 2,
        expected_head=None,
    )
    receipt, receipt_inserted = store.append_registry_snapshot(
        RECEIPT_AUTHORITY_REGISTRY_KIND,
        RECEIPT_REGISTRY_V0,
        recorded_at_ms=before_ms - 1,
        expected_head=None,
    )
    assert policy_inserted is True
    assert receipt_inserted is True
    return policy, receipt


def _current_heads(
    store: ArtifactAdmissionStore,
) -> tuple[ArtifactAdmissionRegistryHead, ArtifactAdmissionRegistryHead]:
    return (
        store.registry_head(POLICY_REGISTRY_KIND),
        store.registry_head(RECEIPT_AUTHORITY_REGISTRY_KIND),
    )


def _bound_decision(
    policy_head: ArtifactAdmissionRegistryHead,
    receipt_head: ArtifactAdmissionRegistryHead,
    **overrides: object,
) -> ArtifactAdmissionDecision:
    arguments: dict[str, object] = {
        "policy_registry_generation": policy_head.generation,
        "policy_registry_sha256": policy_head.registry_sha256,
        "policy_registry_event_seq": policy_head.authority_event_seq,
        "policy_registry_event_hash": policy_head.event_hash,
        "receipt_authority_registry_generation": receipt_head.generation,
        "receipt_authority_registry_sha256": receipt_head.registry_sha256,
        "receipt_authority_registry_event_seq": receipt_head.authority_event_seq,
        "receipt_authority_registry_event_hash": receipt_head.event_hash,
    }
    arguments.update(overrides)
    return _decision(**arguments)  # type: ignore[arg-type]


def _record(store: ArtifactAdmissionStore, **overrides: object):
    try:
        policy_head, receipt_head = _current_heads(store)
    except ArtifactAdmissionStoreRegistryUnprovisioned:
        policy_head, receipt_head = _provision(store)
    arguments = {
        "policy_sha256": POLICY_SHA256,
        "receipt_sha256": RECEIPT_SHA256,
        "artifact_sha256": ARTIFACT_SHA256,
        "recorded_at_ms": RECORDED_AT_MS,
        "decision_id": DEFAULT_DECISION_ID,
        "expected_policy_registry_head": policy_head,
        "expected_receipt_authority_registry_head": receipt_head,
    }
    arguments.update(overrides)
    recorded_at_ms = arguments["recorded_at_ms"]
    decision_id = arguments["decision_id"]
    decision = _bound_decision(
        policy_head,
        receipt_head,
        recorded_at_ms=(
            recorded_at_ms
            if isinstance(recorded_at_ms, int) and not isinstance(recorded_at_ms, bool)
            else RECORDED_AT_MS
        ),
        decision_id=(decision_id if isinstance(decision_id, str) else DEFAULT_DECISION_ID),
    )
    return store.record(decision, **arguments)


def test_persists_unified_authority_chain_and_reopens_with_wal_full(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "artifact-admission.db")
    store = ArtifactAdmissionStore(path, create=True)
    policy_head, receipt_head = _provision(store)

    record, inserted = _record(store)

    assert inserted is True
    assert record.decision == _bound_decision(policy_head, receipt_head)
    assert record.authority_event_seq == 3
    assert record.previous_record_hash == receipt_head.event_hash
    assert policy_head.registry_bytes == POLICY_REGISTRY_V0
    assert receipt_head.registry_bytes == RECEIPT_REGISTRY_V0
    assert store.registry_snapshots() == (policy_head, receipt_head)
    assert store.decisions() == (record,)
    assert store.latest() == record
    assert store.latest_decision() == record
    assert store.chain_valid()
    assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store._conn.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    stored_json = store._conn.execute("SELECT decision_json FROM artifact_admission_decisions").fetchone()[0]
    assert stored_json == json.dumps(
        _bound_decision(policy_head, receipt_head).model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    store.close()

    reopened = ArtifactAdmissionStore(path, create=False)
    assert reopened.registry_snapshots() == (policy_head, receipt_head)
    assert reopened.decisions() == (record,)
    assert reopened.chain_valid()
    reopened.close()


def test_exact_decision_replay_is_idempotent_and_conflict_is_rejected(
    tmp_path: Path,
) -> None:
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=True)
    policy_head, receipt_head = _provision(store)
    arguments = {
        "policy_sha256": POLICY_SHA256,
        "receipt_sha256": RECEIPT_SHA256,
        "artifact_sha256": ARTIFACT_SHA256,
        "recorded_at_ms": RECORDED_AT_MS,
        "decision_id": DEFAULT_DECISION_ID,
        "expected_policy_registry_head": policy_head,
        "expected_receipt_authority_registry_head": receipt_head,
    }

    decision = _bound_decision(policy_head, receipt_head)
    first, inserted = store.record(decision, **arguments)
    replay, replay_inserted = store.record(decision, **arguments)

    assert inserted is True
    assert replay_inserted is False
    assert replay == first
    with pytest.raises(ArtifactAdmissionStoreConflict, match="conflict"):
        store.record(
            _bound_decision(
                policy_head,
                receipt_head,
                reason_code="CHANGED_DECISION",
            ),
            **arguments,
        )
    assert store.decisions() == (first,)
    store.close()


def test_exact_replay_remains_idempotent_after_a_later_decision(
    tmp_path: Path,
) -> None:
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=True)
    policy_head, receipt_head = _provision(store)
    first_decision = _bound_decision(policy_head, receipt_head)
    first_arguments = {
        "policy_sha256": POLICY_SHA256,
        "receipt_sha256": RECEIPT_SHA256,
        "artifact_sha256": ARTIFACT_SHA256,
        "recorded_at_ms": RECORDED_AT_MS,
        "decision_id": DEFAULT_DECISION_ID,
        "expected_policy_registry_head": policy_head,
        "expected_receipt_authority_registry_head": receipt_head,
    }
    first, _ = store.record(first_decision, **first_arguments)
    _record(
        store,
        recorded_at_ms=RECORDED_AT_MS + 1,
        decision_id="admission-release-2",
    )

    replay, inserted = store.record(first_decision, **first_arguments)

    assert inserted is False
    assert replay == first
    assert len(store.decisions()) == 2
    store.close()


def test_consistent_read_snapshot_survives_cross_connection_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = str(tmp_path / "artifact-admission.db")
    reader = ArtifactAdmissionStore(path, create=True)
    policy_head, receipt_head = _provision(reader)
    writer = ArtifactAdmissionStore(path, create=False)
    event_rows_read = threading.Event()
    writer_done = threading.Event()
    failures: list[BaseException] = []
    original_event_rows = reader._event_rows_locked

    def pause_after_event_rows():
        rows = original_event_rows()
        event_rows_read.set()
        assert writer_done.wait(timeout=5)
        return rows

    monkeypatch.setattr(reader, "_event_rows_locked", pause_after_event_rows)

    def advance_policy() -> None:
        try:
            assert event_rows_read.wait(timeout=5)
            writer.append_registry_snapshot(
                POLICY_REGISTRY_KIND,
                _registry_bytes(POLICY_REGISTRY_KIND, 1, marker="revoked"),
                recorded_at_ms=RECORDED_AT_MS + 1,
                expected_head=policy_head,
            )
        except BaseException as exc:
            failures.append(exc)
        finally:
            writer_done.set()

    thread = threading.Thread(target=advance_policy)
    thread.start()
    observed = reader.registry_heads()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert failures == []
    assert observed == {
        POLICY_REGISTRY_KIND: policy_head,
        RECEIPT_AUTHORITY_REGISTRY_KIND: receipt_head,
    }
    assert reader.registry_head(POLICY_REGISTRY_KIND).generation == 1
    assert reader.chain_valid()
    writer.close()
    reader.close()


def test_far_future_timestamp_is_rejected_without_poisoning_history(
    tmp_path: Path,
) -> None:
    store = ArtifactAdmissionStore(
        str(tmp_path / "artifact-admission.db"),
        create=True,
        clock_ms=lambda: RECORDED_AT_MS,
    )

    with pytest.raises(
        ArtifactAdmissionStoreFutureClock,
        match="ARTIFACT_ADMISSION_AUTHORITY_FUTURE_CLOCK",
    ):
        store.append_registry_snapshot(
            POLICY_REGISTRY_KIND,
            POLICY_REGISTRY_V0,
            recorded_at_ms=(RECORDED_AT_MS + ARTIFACT_ADMISSION_MAX_FUTURE_SKEW_MS + 1),
            expected_head=None,
        )

    assert store.registry_snapshots() == ()
    policy_head, receipt_head = _provision(store)
    assert policy_head.generation == 0
    assert receipt_head.generation == 0
    with pytest.raises(
        ArtifactAdmissionStoreFutureClock,
        match="ARTIFACT_ADMISSION_AUTHORITY_FUTURE_CLOCK",
    ):
        _record(
            store,
            recorded_at_ms=(RECORDED_AT_MS + ARTIFACT_ADMISSION_MAX_FUTURE_SKEW_MS + 1),
        )
    assert store.decisions() == ()
    assert store.chain_valid()
    store.close()


def test_registry_and_decision_events_have_one_global_order(tmp_path: Path) -> None:
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=True)
    _provision(store)
    first, _ = _record(store)
    old_policy = store.registry_head(POLICY_REGISTRY_KIND)
    policy_v1, _ = store.append_registry_snapshot(
        POLICY_REGISTRY_KIND,
        _registry_bytes(POLICY_REGISTRY_KIND, 1, marker="revoked"),
        recorded_at_ms=RECORDED_AT_MS + 1,
        expected_head=old_policy,
    )
    second, _ = _record(
        store,
        recorded_at_ms=RECORDED_AT_MS + 2,
        decision_id="admission-release-2",
    )

    assert first.authority_event_seq == 3
    assert policy_v1.authority_event_seq == 4
    assert second.authority_event_seq == 5
    assert second.previous_record_hash == policy_v1.event_hash
    assert store.chain_valid()
    store.close()


def test_rejects_clock_rollback_inside_write_transaction(tmp_path: Path) -> None:
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=True)
    first, _ = _record(store)

    with pytest.raises(ArtifactAdmissionStoreClockRollback, match="rollback"):
        _record(
            store,
            recorded_at_ms=first.recorded_at_ms - 1,
            decision_id="older-admission",
        )

    assert store.decisions() == (first,)
    store.close()


def test_all_authority_tables_are_append_only(tmp_path: Path) -> None:
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=True)
    _record(store)

    for statement in (
        "UPDATE artifact_admission_authority_events SET recorded_at_ms=0",
        "DELETE FROM artifact_admission_authority_events",
        "UPDATE artifact_admission_registry_snapshots SET generation=9",
        "DELETE FROM artifact_admission_registry_snapshots",
        "UPDATE artifact_admission_decisions SET recorded_at_ms=0",
        "DELETE FROM artifact_admission_decisions",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._conn.execute(statement)
    assert store.chain_valid()
    store.close()


@pytest.mark.parametrize(
    ("trigger", "statement"),
    [
        (
            "artifact_admission_events_no_update",
            "UPDATE artifact_admission_authority_events SET payload_sha256='8'"
            " || substr(payload_sha256,2) WHERE event_seq=1",
        ),
        (
            "artifact_admission_registry_snapshots_no_update",
            "UPDATE artifact_admission_registry_snapshots SET registry_bytes=x'7b7d' WHERE authority_event_seq=1",
        ),
        (
            "artifact_admission_decisions_no_update",
            "UPDATE artifact_admission_decisions SET policy_sha256='8' || substr(policy_sha256,2) WHERE decision_seq=1",
        ),
    ],
)
def test_event_snapshot_and_decision_tampering_are_detected_on_reopen(
    tmp_path: Path,
    trigger: str,
    statement: str,
) -> None:
    path = str(tmp_path / f"{trigger}.db")
    store = ArtifactAdmissionStore(path, create=True)
    _record(store)
    store.close()

    connection = sqlite3.connect(path)
    connection.execute(f"DROP TRIGGER {trigger}")
    connection.execute(statement)
    connection.commit()
    connection.close()

    with pytest.raises(ArtifactAdmissionStoreError, match="trigger|hash|snapshot"):
        ArtifactAdmissionStore(path, create=False)


def test_create_false_missing_and_unrelated_database_fail_without_mutation(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(ArtifactAdmissionStoreError, match="unavailable"):
        ArtifactAdmissionStore(str(missing), create=False)
    assert not missing.exists()

    unrelated = tmp_path / "unrelated.db"
    connection = sqlite3.connect(unrelated)
    connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
    connection.execute("INSERT INTO marker(value) VALUES ('preserve')")
    connection.commit()
    connection.close()
    before = unrelated.read_bytes()

    with pytest.raises(ArtifactAdmissionStoreError, match="schema|unreadable"):
        ArtifactAdmissionStore(str(unrelated), create=False)

    assert unrelated.read_bytes() == before
    connection = sqlite3.connect(unrelated)
    assert connection.execute("SELECT value FROM marker").fetchone()[0] == "preserve"
    assert (
        connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'artifact_admission%'").fetchone()[0]
        == 0
    )
    connection.close()


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"recorded_at_ms": True}, "recorded_at_ms"),
        ({"policy_sha256": "not-a-hash"}, "policy_sha256"),
        ({"policy_sha256": ""}, "policy_sha256"),
        ({"receipt_sha256": "8" * 64}, "receipt_sha256 does not match"),
        ({"artifact_sha256": "8" * 64}, "artifact_sha256 does not match"),
    ],
)
def test_rejects_invalid_time_hashes_and_decision_bindings(
    tmp_path: Path,
    overrides: dict[str, object],
    match: str,
) -> None:
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=True)

    with pytest.raises(ValueError, match=match):
        _record(store, **overrides)

    assert store.decisions() == ()
    store.close()


def test_denial_can_commit_without_provisioned_registry_heads(tmp_path: Path) -> None:
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=True)
    denial = ArtifactAdmissionDecision(
        verdict="DENY",
        reason_code="ADMISSION_INPUT_INVALID",
        admission_at_ms=RECORDED_AT_MS,
    )

    record, inserted = store.record(
        denial,
        policy_sha256="",
        receipt_sha256="",
        artifact_sha256="",
        recorded_at_ms=RECORDED_AT_MS,
    )

    assert inserted is True
    assert record.authority_event_seq == 1
    assert record.previous_record_hash == ""
    assert record.decision == denial
    assert store.registry_snapshots() == ()
    assert store.chain_valid()
    store.close()


def test_store_rejects_allow_without_authenticated_receipt_evidence(
    tmp_path: Path,
) -> None:
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=True)
    policy_head, receipt_head = _provision(store)
    unauthenticated = _bound_decision(policy_head, receipt_head).model_copy(
        update={
            "receipt_envelope_sha256": "",
            "receipt_signature_verified": False,
        }
    )

    with pytest.raises(ValueError, match="authenticated"):
        store.record(
            unauthenticated,
            policy_sha256=POLICY_SHA256,
            receipt_sha256=RECEIPT_SHA256,
            artifact_sha256=ARTIFACT_SHA256,
            recorded_at_ms=RECORDED_AT_MS,
            decision_id=DEFAULT_DECISION_ID,
            expected_policy_registry_head=policy_head,
            expected_receipt_authority_registry_head=receipt_head,
        )

    assert store.decisions() == ()
    store.close()


@pytest.mark.parametrize(
    ("updates", "match"),
    [
        ({"admission_at_ms": RECORDED_AT_MS - 1}, "admission time"),
        ({"authority_store_kind": "wrong"}, "store kind"),
        ({"authority_store_schema_version": 999}, "store schema"),
        ({"authority_decision_id": "wrong"}, "decision_id"),
        ({"receipt_id": ""}, "authenticated"),
        ({"revocation_status": "UNKNOWN"}, "authenticated"),
        ({"policy_registry_generation": None}, "policy registry generation"),
        (
            {"receipt_authority_registry_generation": None},
            "receipt authority registry generation",
        ),
    ],
)
def test_store_rejects_allow_without_complete_authority_binding(
    tmp_path: Path,
    updates: dict[str, object],
    match: str,
) -> None:
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=True)
    policy_head, receipt_head = _provision(store)
    decision = _bound_decision(policy_head, receipt_head).model_copy(update=updates)

    with pytest.raises(ValueError, match=match):
        store.record(
            decision,
            policy_sha256=POLICY_SHA256,
            receipt_sha256=RECEIPT_SHA256,
            artifact_sha256=ARTIFACT_SHA256,
            recorded_at_ms=RECORDED_AT_MS,
            decision_id=DEFAULT_DECISION_ID,
            expected_policy_registry_head=policy_head,
            expected_receipt_authority_registry_head=receipt_head,
        )

    assert store.decisions() == ()
    store.close()


def test_registry_requires_generation_zero_then_exact_plus_one(tmp_path: Path) -> None:
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=True)
    with pytest.raises(
        ArtifactAdmissionStoreRegistryGenerationGap,
        match="POLICY_REGISTRY_GENERATION_GAP",
    ):
        store.append_registry_snapshot(
            POLICY_REGISTRY_KIND,
            _registry_bytes(POLICY_REGISTRY_KIND, 1),
            recorded_at_ms=RECORDED_AT_MS,
            expected_head=None,
        )

    head0, inserted = store.append_registry_snapshot(
        POLICY_REGISTRY_KIND,
        POLICY_REGISTRY_V0,
        recorded_at_ms=RECORDED_AT_MS,
        expected_head=None,
    )
    replay, replay_inserted = store.append_registry_snapshot(
        POLICY_REGISTRY_KIND,
        POLICY_REGISTRY_V0,
        recorded_at_ms=RECORDED_AT_MS,
        expected_head=head0,
    )
    assert inserted is True
    assert replay_inserted is False
    assert replay == head0

    with pytest.raises(
        ArtifactAdmissionStoreRegistryConflict,
        match="POLICY_REGISTRY_GENERATION_CONFLICT",
    ):
        store.append_registry_snapshot(
            POLICY_REGISTRY_KIND,
            _registry_bytes(POLICY_REGISTRY_KIND, 0, marker="different"),
            recorded_at_ms=RECORDED_AT_MS,
            expected_head=head0,
        )
    with pytest.raises(
        ArtifactAdmissionStoreRegistryGenerationGap,
        match="POLICY_REGISTRY_GENERATION_GAP",
    ):
        store.append_registry_snapshot(
            POLICY_REGISTRY_KIND,
            _registry_bytes(POLICY_REGISTRY_KIND, 2),
            recorded_at_ms=RECORDED_AT_MS + 1,
            expected_head=head0,
        )

    head1, _ = store.append_registry_snapshot(
        POLICY_REGISTRY_KIND,
        _registry_bytes(POLICY_REGISTRY_KIND, 1),
        recorded_at_ms=RECORDED_AT_MS + 1,
        expected_head=head0,
    )
    with pytest.raises(
        ArtifactAdmissionStoreRegistryRollback,
        match="POLICY_REGISTRY_GENERATION_ROLLBACK",
    ):
        store.append_registry_snapshot(
            POLICY_REGISTRY_KIND,
            POLICY_REGISTRY_V0,
            recorded_at_ms=RECORDED_AT_MS + 2,
            expected_head=head1,
        )
    assert [item.generation for item in store.registry_snapshots()] == [0, 1]
    store.close()


def test_registry_cas_rejects_stale_head_across_connections(tmp_path: Path) -> None:
    path = str(tmp_path / "artifact-admission.db")
    first = ArtifactAdmissionStore(path, create=True)
    head0, _ = first.append_registry_snapshot(
        POLICY_REGISTRY_KIND,
        POLICY_REGISTRY_V0,
        recorded_at_ms=RECORDED_AT_MS,
        expected_head=None,
    )
    second = ArtifactAdmissionStore(path, create=False)
    head1, _ = first.append_registry_snapshot(
        POLICY_REGISTRY_KIND,
        _registry_bytes(POLICY_REGISTRY_KIND, 1, marker="revoked"),
        recorded_at_ms=RECORDED_AT_MS + 1,
        expected_head=head0,
    )

    with pytest.raises(
        ArtifactAdmissionStoreRegistryHeadChanged,
        match="POLICY_REGISTRY_HEAD_CHANGED",
    ) as error:
        second.append_registry_snapshot(
            POLICY_REGISTRY_KIND,
            _registry_bytes(POLICY_REGISTRY_KIND, 1, marker="different"),
            recorded_at_ms=RECORDED_AT_MS + 1,
            expected_head=head0,
        )

    assert error.value.expected_head == head0
    assert error.value.actual_head == head1
    assert second.registry_head(POLICY_REGISTRY_KIND) == head1
    second.close()
    first.close()


def test_allow_requires_exact_current_policy_and_receipt_heads(tmp_path: Path) -> None:
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=True)
    with pytest.raises(
        ArtifactAdmissionStoreRegistryUnprovisioned,
        match="POLICY_REGISTRY_UNPROVISIONED",
    ):
        store.record(
            _decision(),
            policy_sha256=POLICY_SHA256,
            receipt_sha256=RECEIPT_SHA256,
            artifact_sha256=ARTIFACT_SHA256,
            recorded_at_ms=RECORDED_AT_MS,
            decision_id=DEFAULT_DECISION_ID,
        )

    policy_head, receipt_head = _provision(store)
    with pytest.raises(
        ArtifactAdmissionStoreRegistryHeadMismatch,
        match="POLICY_REGISTRY_HEAD_REQUIRED",
    ):
        store.record(
            _bound_decision(policy_head, receipt_head),
            policy_sha256=POLICY_SHA256,
            receipt_sha256=RECEIPT_SHA256,
            artifact_sha256=ARTIFACT_SHA256,
            recorded_at_ms=RECORDED_AT_MS,
            decision_id=DEFAULT_DECISION_ID,
        )

    wrong_decision = _bound_decision(policy_head, receipt_head).model_copy(update={"policy_registry_sha256": "f" * 64})
    with pytest.raises(
        ArtifactAdmissionStoreRegistryHeadMismatch,
        match="POLICY_REGISTRY_DECISION_HEAD_MISMATCH",
    ):
        store.record(
            wrong_decision,
            policy_sha256=POLICY_SHA256,
            receipt_sha256=RECEIPT_SHA256,
            artifact_sha256=ARTIFACT_SHA256,
            recorded_at_ms=RECORDED_AT_MS,
            decision_id=DEFAULT_DECISION_ID,
            expected_policy_registry_head=policy_head,
            expected_receipt_authority_registry_head=receipt_head,
        )

    event_binding_updates = (
        {"policy_registry_event_seq": policy_head.authority_event_seq + 1},
        {"policy_registry_event_hash": "f" * 64},
        {"receipt_authority_registry_event_seq": (receipt_head.authority_event_seq + 1)},
        {"receipt_authority_registry_event_hash": "e" * 64},
    )
    for update in event_binding_updates:
        wrong_event_binding = _bound_decision(policy_head, receipt_head).model_copy(update=update)
        reason_prefix = (
            RECEIPT_AUTHORITY_REGISTRY_KIND if next(iter(update)).startswith("receipt_") else POLICY_REGISTRY_KIND
        )
        with pytest.raises(
            ArtifactAdmissionStoreRegistryHeadMismatch,
            match=f"{reason_prefix}_DECISION_HEAD_MISMATCH",
        ):
            store.record(
                wrong_event_binding,
                policy_sha256=POLICY_SHA256,
                receipt_sha256=RECEIPT_SHA256,
                artifact_sha256=ARTIFACT_SHA256,
                recorded_at_ms=RECORDED_AT_MS,
                decision_id=DEFAULT_DECISION_ID,
                expected_policy_registry_head=policy_head,
                expected_receipt_authority_registry_head=receipt_head,
            )
    assert store.decisions() == ()
    store.close()


@pytest.mark.parametrize(
    "registry_kind",
    [POLICY_REGISTRY_KIND, RECEIPT_AUTHORITY_REGISTRY_KIND],
)
def test_revocation_after_head_acquisition_prevents_stale_allow(
    tmp_path: Path,
    registry_kind: str,
) -> None:
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=True)
    policy_head, receipt_head = _provision(store)
    stale_policy_head = policy_head
    stale_receipt_head = receipt_head
    current = policy_head if registry_kind == POLICY_REGISTRY_KIND else receipt_head
    advanced, _ = store.append_registry_snapshot(
        registry_kind,
        _registry_bytes(registry_kind, 1, marker="revoked"),
        recorded_at_ms=RECORDED_AT_MS - 1,
        expected_head=current,
    )

    with pytest.raises(
        ArtifactAdmissionStoreRegistryHeadChanged,
        match=f"{registry_kind}_HEAD_CHANGED",
    ) as error:
        store.record(
            _bound_decision(stale_policy_head, stale_receipt_head),
            policy_sha256=POLICY_SHA256,
            receipt_sha256=RECEIPT_SHA256,
            artifact_sha256=ARTIFACT_SHA256,
            recorded_at_ms=RECORDED_AT_MS,
            decision_id=DEFAULT_DECISION_ID,
            expected_policy_registry_head=stale_policy_head,
            expected_receipt_authority_registry_head=stale_receipt_head,
        )

    assert error.value.actual_head == advanced
    assert store.decisions() == ()
    assert store.chain_valid()
    store.close()


def test_registry_contract_and_exact_bytes_are_fail_closed(tmp_path: Path) -> None:
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=True)
    invalid_documents = (
        b"{}",
        b'{"schema_version":2,"schema_version":2}',
        json.dumps(
            {
                "schema_version": 2,
                "readiness": "L2_DRY_RUN_ONLY",
                "generation": True,
                "policies": {},
            }
        ).encode("utf-8"),
    )
    for content in invalid_documents:
        with pytest.raises(ValueError, match="registry"):
            store.append_registry_snapshot(
                POLICY_REGISTRY_KIND,
                content,
                recorded_at_ms=RECORDED_AT_MS,
                expected_head=None,
            )
    assert store.registry_snapshots() == ()
    store.close()


def test_store_has_no_external_or_authority_granting_capability() -> None:
    path = Path(__file__).resolve().parents[2] / "services" / "governance" / "artifact_admission_store.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    }
    imported_roots.update(
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    )

    assert imported_roots <= {
        "__future__",
        "dataclasses",
        "hashlib",
        "json",
        "os",
        "re",
        "sqlite3",
        "threading",
        "time",
        "typing",
    }
