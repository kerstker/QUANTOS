from __future__ import annotations

import ast
import json
from pathlib import Path
import sqlite3

import pytest

from quantos.contracts.identity import (
    GovernanceRole,
    StrongIdentityAssurance,
    VerifiedIdentity,
)
from services.governance.identity_authorization import (
    IdentityAuthorizationGrant,
    IdentityAuthorizationRegistry,
    evaluate_authorization,
)
from services.governance.identity_authorization_store import (
    IDENTITY_AUTHORIZATION_MAX_FUTURE_SKEW_MS,
    IdentityAuthorizationStore,
    IdentityAuthorizationStoreClockRollback,
    IdentityAuthorizationStoreConflict,
    IdentityAuthorizationStoreError,
    IdentityAuthorizationStoreFutureClock,
    IdentityAuthorizationStoreRegistryConflict,
    IdentityAuthorizationStoreRegistryGenerationGap,
    IdentityAuthorizationStoreRegistryHeadChanged,
    IdentityAuthorizationStoreRegistryHeadMismatch,
    IdentityAuthorizationStoreRegistryRollback,
    IdentityAuthorizationStoreRegistryUnprovisioned,
)


ISSUER = "https://identity.example.test"
NOW_MS = 50_000


def _identity() -> VerifiedIdentity:
    return VerifiedIdentity(
        issuer=ISSUER,
        subject="alice",
        audience="quantos-governance",
        roles=(GovernanceRole.PROMOTION_APPROVER,),
        assurance=StrongIdentityAssurance.OIDC_MTLS_MFA,
        acr="urn:quantos:aal2",
        amr=("pwd", "mfa"),
        issued_at=40,
        not_before=40,
        authentication_time=39,
        expires_at=60,
        token_id="token-alice",
        certificate_sha256="a" * 64,
    )


def _registry(generation: int, *, revoked: bool = False) -> IdentityAuthorizationRegistry:
    if generation == 0:
        return IdentityAuthorizationRegistry(generation=0)
    grant = IdentityAuthorizationGrant(
        issuer=ISSUER,
        subject="alice",
        principal_kind="HUMAN",
        enabled=not revoked,
        revoked=revoked,
        roles=(GovernanceRole.PROMOTION_APPROVER,),
        tokens_valid_after_ms=30_000,
        access_review_expires_at_ms=60_000,
    )
    return IdentityAuthorizationRegistry(
        generation=generation,
        principals={grant.actor_id: grant},
    )


def _registry_bytes(generation: int, *, revoked: bool = False) -> bytes:
    return json.dumps(
        _registry(generation, revoked=revoked).model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _provision(store: IdentityAuthorizationStore):
    head0, _ = store.append_registry_snapshot(
        _registry_bytes(0),
        recorded_at_ms=NOW_MS - 2,
        expected_head=None,
    )
    head1, _ = store.append_registry_snapshot(
        _registry_bytes(1),
        recorded_at_ms=NOW_MS - 1,
        expected_head=head0,
    )
    return head0, head1


def _allow_decision(generation: int = 1):
    return evaluate_authorization(
        _registry(generation),
        _identity(),
        GovernanceRole.PROMOTION_APPROVER,
        evaluated_at_ms=NOW_MS,
    )


def test_registry_requires_generation_zero_then_exact_plus_one(tmp_path: Path) -> None:
    store = IdentityAuthorizationStore(
        str(tmp_path / "identity-auth.db"),
        create=True,
        clock_ms=lambda: NOW_MS,
    )
    with pytest.raises(IdentityAuthorizationStoreRegistryGenerationGap):
        store.append_registry_snapshot(_registry_bytes(1), recorded_at_ms=NOW_MS - 2, expected_head=None)

    head0, inserted = store.append_registry_snapshot(_registry_bytes(0), recorded_at_ms=NOW_MS - 2, expected_head=None)
    replay, replay_inserted = store.append_registry_snapshot(
        _registry_bytes(0), recorded_at_ms=NOW_MS - 2, expected_head=head0
    )
    assert inserted is True
    assert replay_inserted is False
    assert replay == head0

    with pytest.raises(IdentityAuthorizationStoreRegistryConflict):
        alternate = json.dumps(_registry(0).model_dump(mode="json"), indent=2, sort_keys=True).encode("utf-8")
        store.append_registry_snapshot(alternate, recorded_at_ms=NOW_MS - 1, expected_head=head0)
    with pytest.raises(IdentityAuthorizationStoreRegistryGenerationGap):
        store.append_registry_snapshot(_registry_bytes(2), recorded_at_ms=NOW_MS - 1, expected_head=head0)

    head1, _ = store.append_registry_snapshot(_registry_bytes(1), recorded_at_ms=NOW_MS - 1, expected_head=head0)
    with pytest.raises(IdentityAuthorizationStoreRegistryRollback):
        store.append_registry_snapshot(_registry_bytes(0), recorded_at_ms=NOW_MS, expected_head=head1)
    store.close()


def test_allow_commits_against_exact_head_in_one_unified_chain(tmp_path: Path) -> None:
    path = str(tmp_path / "identity-auth.db")
    store = IdentityAuthorizationStore(path, create=True, clock_ms=lambda: NOW_MS)
    head0, head1 = _provision(store)

    record, inserted = store.record(
        _allow_decision(),
        recorded_at_ms=NOW_MS,
        expected_registry_head=head1,
        decision_id="authz-1",
    )
    snapshot = store.read_snapshot()

    assert inserted is True
    assert head0.authority_event_seq == 1
    assert head1.authority_event_seq == 2
    assert record.authority_event_seq == 3
    assert record.registry_generation == head1.generation
    assert record.registry_sha256 == head1.registry_sha256
    assert record.registry_event_seq == head1.authority_event_seq
    assert record.registry_event_hash == head1.event_hash
    assert record.previous_event_hash == head1.event_hash
    assert snapshot.registry_head == head1
    assert snapshot.decisions == (record,)
    assert snapshot.authority_head_hash == record.event_hash
    assert snapshot.authority_time_floor_ms == NOW_MS
    assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store._conn.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    store.close()

    reopened = IdentityAuthorizationStore(path, create=False, clock_ms=lambda: NOW_MS)
    assert reopened.read_snapshot() == snapshot
    assert reopened.chain_valid()
    reopened.close()


def test_stale_registry_head_cannot_commit_allow(tmp_path: Path) -> None:
    store = IdentityAuthorizationStore(str(tmp_path / "identity-auth.db"), create=True, clock_ms=lambda: NOW_MS)
    _, stale_head = _provision(store)
    current_head, _ = store.append_registry_snapshot(
        _registry_bytes(2, revoked=True),
        recorded_at_ms=NOW_MS,
        expected_head=stale_head,
    )

    with pytest.raises(IdentityAuthorizationStoreRegistryHeadChanged) as error:
        store.record(
            _allow_decision(),
            recorded_at_ms=NOW_MS,
            expected_registry_head=stale_head,
        )

    assert error.value.actual_head == current_head
    assert store.decisions() == ()
    store.close()


def test_allow_generation_must_match_exact_current_head(tmp_path: Path) -> None:
    store = IdentityAuthorizationStore(str(tmp_path / "identity-auth.db"), create=True, clock_ms=lambda: NOW_MS)
    _, head1 = _provision(store)

    with pytest.raises(
        IdentityAuthorizationStoreRegistryHeadMismatch,
        match="DECISION_REGISTRY_HEAD_MISMATCH",
    ):
        store.record(
            _allow_decision(generation=2),
            recorded_at_ms=NOW_MS,
            expected_registry_head=head1,
        )
    assert store.decisions() == ()
    store.close()


def test_deny_is_persisted_with_exact_registry_evidence(tmp_path: Path) -> None:
    store = IdentityAuthorizationStore(str(tmp_path / "identity-auth.db"), create=True, clock_ms=lambda: NOW_MS)
    _, head1 = _provision(store)
    denied = evaluate_authorization(
        _registry(1),
        _identity(),
        GovernanceRole.PROMOTION_REVIEWER,
        evaluated_at_ms=NOW_MS,
    )

    record, inserted = store.record(
        denied,
        recorded_at_ms=NOW_MS,
        expected_registry_head=head1,
    )

    assert inserted is True
    assert record.decision.verdict == "DENY"
    assert record.registry_sha256 == head1.registry_sha256
    assert store.decisions() == (record,)
    store.close()


def test_exact_replay_is_idempotent_and_id_conflict_is_rejected(tmp_path: Path) -> None:
    store = IdentityAuthorizationStore(str(tmp_path / "identity-auth.db"), create=True, clock_ms=lambda: NOW_MS)
    _, head1 = _provision(store)
    arguments = {
        "recorded_at_ms": NOW_MS,
        "expected_registry_head": head1,
        "decision_id": "authz-1",
    }
    first, inserted = store.record(_allow_decision(), **arguments)
    replay, replay_inserted = store.record(_allow_decision(), **arguments)

    assert inserted is True
    assert replay_inserted is False
    assert replay == first
    changed = _allow_decision().model_copy(update={"reason_code": "CHANGED"})
    with pytest.raises(IdentityAuthorizationStoreConflict):
        store.record(changed, **arguments)
    store.close()


def test_missing_registry_is_fail_closed(tmp_path: Path) -> None:
    store = IdentityAuthorizationStore(str(tmp_path / "identity-auth.db"), create=True, clock_ms=lambda: NOW_MS)
    with pytest.raises(IdentityAuthorizationStoreRegistryUnprovisioned):
        store.registry_head()
    with pytest.raises(IdentityAuthorizationStoreRegistryUnprovisioned):
        store.record(
            _allow_decision(),
            recorded_at_ms=NOW_MS,
            expected_registry_head=None,
        )
    store.close()


def test_future_and_rollback_clocks_do_not_poison_history(tmp_path: Path) -> None:
    store = IdentityAuthorizationStore(str(tmp_path / "identity-auth.db"), create=True, clock_ms=lambda: NOW_MS)
    future = NOW_MS + IDENTITY_AUTHORIZATION_MAX_FUTURE_SKEW_MS + 1
    with pytest.raises(IdentityAuthorizationStoreFutureClock):
        store.append_registry_snapshot(_registry_bytes(0), recorded_at_ms=future, expected_head=None)
    assert store.registry_snapshots() == ()

    _, head1 = _provision(store)
    record, _ = store.record(
        _allow_decision(),
        recorded_at_ms=NOW_MS,
        expected_registry_head=head1,
    )
    with pytest.raises(IdentityAuthorizationStoreClockRollback):
        store.record(
            _allow_decision(),
            recorded_at_ms=NOW_MS - 1,
            expected_registry_head=head1,
            decision_id="older",
        )
    assert store.decisions() == (record,)
    store.close()


def test_registry_cas_rejects_stale_head_across_connections(tmp_path: Path) -> None:
    path = str(tmp_path / "identity-auth.db")
    first = IdentityAuthorizationStore(path, create=True, clock_ms=lambda: NOW_MS)
    head0, _ = first.append_registry_snapshot(_registry_bytes(0), recorded_at_ms=NOW_MS - 2, expected_head=None)
    second = IdentityAuthorizationStore(path, create=False, clock_ms=lambda: NOW_MS)
    head1, _ = first.append_registry_snapshot(_registry_bytes(1), recorded_at_ms=NOW_MS - 1, expected_head=head0)

    with pytest.raises(IdentityAuthorizationStoreRegistryHeadChanged) as error:
        second.append_registry_snapshot(_registry_bytes(1), recorded_at_ms=NOW_MS, expected_head=head0)
    assert error.value.actual_head == head1
    second.close()
    first.close()


def test_read_snapshot_is_consistent_when_writer_commits_between_selects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = str(tmp_path / "identity-auth.db")
    reader = IdentityAuthorizationStore(path, create=True, clock_ms=lambda: NOW_MS)
    head0, _ = reader.append_registry_snapshot(_registry_bytes(0), recorded_at_ms=NOW_MS - 2, expected_head=None)
    writer = IdentityAuthorizationStore(path, create=False, clock_ms=lambda: NOW_MS)
    original_snapshot_rows = reader._snapshot_rows_locked
    committed = False

    def write_then_read_snapshot_rows():
        nonlocal committed
        if not committed:
            writer.append_registry_snapshot(
                _registry_bytes(1),
                recorded_at_ms=NOW_MS - 1,
                expected_head=head0,
            )
            committed = True
        return original_snapshot_rows()

    monkeypatch.setattr(
        reader,
        "_snapshot_rows_locked",
        write_then_read_snapshot_rows,
    )

    snapshot = reader.read_snapshot()

    assert committed is True
    assert snapshot.registry_head == head0
    assert snapshot.registry_snapshots == (head0,)
    assert writer.registry_head().generation == 1
    writer.close()
    reader.close()


def test_append_only_triggers_and_tamper_detection(tmp_path: Path) -> None:
    path = str(tmp_path / "identity-auth.db")
    store = IdentityAuthorizationStore(path, create=True, clock_ms=lambda: NOW_MS)
    _, head1 = _provision(store)
    store.record(_allow_decision(), recorded_at_ms=NOW_MS, expected_registry_head=head1)
    for statement in (
        "UPDATE identity_authorization_events SET recorded_at_ms=0",
        "DELETE FROM identity_authorization_registry_snapshots",
        "UPDATE identity_authorization_decisions SET registry_sha256='f' || substr(registry_sha256,2)",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._conn.execute(statement)
    store.close()

    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER identity_authorization_events_no_update")
    connection.execute(
        "UPDATE identity_authorization_events SET payload_sha256='f' || substr(payload_sha256,2) WHERE event_seq=1"
    )
    connection.commit()
    connection.close()
    with pytest.raises(IdentityAuthorizationStoreError, match="trigger|hash"):
        IdentityAuthorizationStore(path, create=False, clock_ms=lambda: NOW_MS)


def test_missing_or_unrelated_database_fails_without_mutation(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(IdentityAuthorizationStoreError, match="unavailable"):
        IdentityAuthorizationStore(str(missing), create=False)
    assert not missing.exists()

    unrelated = tmp_path / "unrelated.db"
    connection = sqlite3.connect(unrelated)
    connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
    connection.execute("INSERT INTO marker VALUES ('preserve')")
    connection.commit()
    connection.close()
    before = unrelated.read_bytes()
    with pytest.raises(IdentityAuthorizationStoreError, match="schema|unreadable"):
        IdentityAuthorizationStore(str(unrelated), create=False)
    assert unrelated.read_bytes() == before


def test_store_has_no_external_or_authority_granting_capability() -> None:
    path = Path(__file__).resolve().parents[2] / "services" / "governance" / "identity_authorization_store.py"
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
        "sqlite3",
        "threading",
        "time",
        "typing",
    }
