from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

import pytest

from services.governance.l2_restore_history import L2RestoreHistoryStore
import tools.l2_backup as l2_backup_module
from tools.l2_backup import (
    BackupSource,
    DEFAULT_SOURCES,
    L2BackupError,
    SOURCE_CONTRACT_VERSION,
    restore_l2_batch,
    run_recorded_l2_backup,
    run_l2_backup,
)
from tools.live_backup import decrypt_bytes, decrypt_file, encrypt_bytes, encrypt_file


PASSPHRASE = "institutional-backup-test-passphrase"


def test_default_backup_sources_require_data_quality_admission_authority():
    source = next(item for item in DEFAULT_SOURCES if item.relative_path == "data/cache/data_quality_admission.db")

    assert source.role == "data_quality_admission"
    assert source.required is True


def test_default_backup_sources_require_restore_history_authority():
    source = next(item for item in DEFAULT_SOURCES if item.relative_path == "data/cache/l2_restore_history.db")

    assert source.role == "l2_restore_history_authority"
    assert source.required is True


def test_default_backup_sources_require_alpha_production_workflow():
    source = next(
        item
        for item in DEFAULT_SOURCES
        if item.relative_path == "data/cache/alpha_production_workflow.db"
    )

    assert source.role == "alpha_production_workflow"
    assert source.required is True


def test_default_backup_sources_require_realtime_execution_paper_ledger():
    source = next(
        item
        for item in DEFAULT_SOURCES
        if item.relative_path == "data/cache/realtime_alpha_execution_paper.db"
    )

    assert source.role == "realtime_engineering_execution_paper"
    assert source.required is True


def test_fixed_artifact_admission_authority_is_an_optional_backup_source():
    source = next(item for item in DEFAULT_SOURCES if item.relative_path == "data/cache/artifact_admission.db")

    assert source.role == "artifact_admission_authority"
    assert source.required is False


def test_mft_testnet_soak_evidence_is_an_optional_backup_source():
    source = next(
        item
        for item in DEFAULT_SOURCES
        if item.relative_path == "data/cache/mft_testnet_soak.db"
    )

    assert source.role == "mft_testnet_soak_evidence_authority"
    assert source.required is False


@pytest.mark.parametrize(
    ("relative_path", "role"),
    [
        ("data/cache/approvals.db", "approval_authority"),
        (
            "data/cache/identity_authorization.db",
            "identity_authorization_authority",
        ),
    ],
)
def test_governance_authorities_are_optional_backup_sources(relative_path, role):
    source = next(item for item in DEFAULT_SOURCES if item.relative_path == relative_path)

    assert source.role == role
    assert source.required is False


def _seed_sqlite(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE authority (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("INSERT INTO authority(value) VALUES ('committed-state')")
    connection.commit()
    return connection


def test_online_sqlite_backup_is_paginated_and_yields_to_authority_writers(monkeypatch):
    calls: dict[str, object] = {}
    pauses: list[float] = []

    class SourceConnection:
        def backup(self, target, *, pages, progress, sleep):
            calls.update(target=target, pages=pages, sleep=sleep)
            progress(0, 10, 20)
            progress(101, 0, 20)

    target = object()
    monkeypatch.setattr(l2_backup_module.time, "sleep", pauses.append)

    l2_backup_module._online_sqlite_backup(SourceConnection(), target)

    assert calls == {
        "target": target,
        "pages": l2_backup_module.SQLITE_BACKUP_PAGES,
        "sleep": l2_backup_module.SQLITE_BACKUP_BUSY_SLEEP_SECONDS,
    }
    assert 0 < l2_backup_module.SQLITE_BACKUP_PAGES <= 1_024
    assert pauses == [l2_backup_module.SQLITE_BACKUP_WRITER_YIELD_SECONDS]


def test_online_sqlite_backup_allows_delete_journal_writer_to_commit(tmp_path, monkeypatch):
    source = tmp_path / "authority.db"
    connection = sqlite3.connect(source)
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("CREATE TABLE authority (id INTEGER PRIMARY KEY, payload BLOB NOT NULL)")
    connection.executemany(
        "INSERT INTO authority(payload) VALUES (zeroblob(4096))",
        [()] * 2_048,
    )
    connection.commit()
    connection.close()

    first_yield = threading.Event()
    errors: list[BaseException] = []
    real_sleep = time.sleep

    def writer_window(seconds: float) -> None:
        if not first_yield.is_set():
            first_yield.set()
            real_sleep(0.1)
        else:
            real_sleep(min(seconds, 0.001))

    monkeypatch.setattr(l2_backup_module, "SQLITE_BACKUP_PAGES", 32)
    monkeypatch.setattr(l2_backup_module.time, "sleep", writer_window)

    def take_snapshot() -> None:
        try:
            l2_backup_module._snapshot_source_file(source, tmp_path / "scratch")
        except BaseException as exc:  # surfaced in the asserting thread
            errors.append(exc)

    (tmp_path / "scratch").mkdir()
    backup = threading.Thread(target=take_snapshot)
    backup.start()
    assert first_yield.wait(timeout=5)

    writer = sqlite3.connect(source, timeout=1)
    writer.execute("INSERT INTO authority(payload) VALUES (zeroblob(4096))")
    writer.commit()
    writer.close()

    backup.join(timeout=10)
    assert not backup.is_alive()
    assert errors == []


def test_l2_backup_uses_consistent_sqlite_snapshot_and_verified_restore(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    database = repo / "data" / "cache" / "authority.db"
    status = repo / "data" / "cache" / "l2_backup_status.json"
    evidence = repo / "evidence" / "l2_backup_latest.json"
    connection = _seed_sqlite(database)
    state = repo / "data" / "cache" / "state.json"
    state.write_text(json.dumps({"marker": "sensitive-state-marker"}), encoding="utf-8")

    sources = (
        BackupSource("data/cache/authority.db", "event_authority", required=True),
        BackupSource("data/cache/state.json", "operational_state", required=True),
    )
    monkeypatch.setattr(l2_backup_module, "L2_ENCRYPTION_CHUNK_SIZE", 16)
    result = run_l2_backup(
        repo_root=repo,
        destination_root=tmp_path / "backup",
        passphrase=PASSPHRASE,
        sources=sources,
        evidence_path=evidence,
        status_path=status,
        allow_local_destination=True,
        now_ms=1_784_044_800_000,
    )
    connection.close()

    assert result["verdict"] == "PASS"
    assert result["mode"] == "L2_AUTHORITY_STATE"
    assert result["encrypted"] is True
    assert result["file_count"] == 2
    assert result["manifest_verified"] is True
    assert result["restore_verified"] is True
    assert result["sqlite_integrity_verified"] is True
    assert result["destination_class"] == "LOCAL_ONLY"
    assert result["source_contract"]["version"] == SOURCE_CONTRACT_VERSION
    assert result["source_contract"]["sources"] == [
        {
            "relative_path": "data/cache/authority.db",
            "required": True,
            "role": "event_authority",
        },
        {
            "relative_path": "data/cache/state.json",
            "required": True,
            "role": "operational_state",
        },
    ]
    assert len(result["source_contract_sha256"]) == 64
    assert result["source_contract_verified"] is True
    assert result["backed_up_sources"] == result["source_contract"]["sources"]
    assert json.loads(evidence.read_text(encoding="utf-8")) == result
    assert json.loads(status.read_text(encoding="utf-8"))["last_success_at_ms"] == result["observed_at_ms"]

    batch = Path(result["batch_dir"])
    assert batch.is_dir()
    assert {path.suffix for path in batch.iterdir()} == {".enc"}
    assert all(b"sensitive-state-marker" not in path.read_bytes() for path in batch.iterdir())
    manifest = _read_manifest(batch)
    for entry in manifest["entries"]:
        assert (batch / f"{entry['artifact']}.enc").read_bytes().startswith(b"QBK2")

    restored = tmp_path / "restored"
    verification = restore_l2_batch(
        batch_dir=batch,
        restore_dir=restored,
        passphrase=PASSPHRASE,
        rto_ms=30_000,
        max_rpo_ms=86_400_000,
        now_ms=result["observed_at_ms"],
        sources=sources,
    )
    assert verification["manifest_verified"] is True
    assert verification["source_contract_verified"] is True
    assert verification["source_contract_sha256"] == result["source_contract_sha256"]
    restored_db = restored / "data" / "cache" / "authority.db"
    with sqlite3.connect(restored_db) as restored_connection:
        assert restored_connection.execute("SELECT value FROM authority").fetchone() == ("committed-state",)
    assert json.loads((restored / "data" / "cache" / "state.json").read_text()) == {"marker": "sensitive-state-marker"}


def test_l2_backup_missing_required_source_fails_without_publishing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    destination = tmp_path / "backup"

    with pytest.raises(L2BackupError, match="required backup source is missing"):
        run_l2_backup(
            repo_root=repo,
            destination_root=destination,
            passphrase=PASSPHRASE,
            sources=(BackupSource("data/cache/missing.db", "authority", required=True),),
            evidence_path=repo / "evidence.json",
            status_path=repo / "status.json",
            allow_local_destination=True,
        )

    assert not destination.exists()
    assert not (repo / "evidence.json").exists()
    assert not (repo / "status.json").exists()


def test_l2_backup_rejects_sources_outside_allowlisted_roots(tmp_path):
    repo = tmp_path / "repo"
    secret = repo / "key" / "broker_secret.json"
    secret.parent.mkdir(parents=True)
    secret.write_text("must-not-be-backed-up", encoding="utf-8")

    with pytest.raises(L2BackupError, match="allowlisted backup roots"):
        run_l2_backup(
            repo_root=repo,
            destination_root=tmp_path / "backup",
            passphrase=PASSPHRASE,
            sources=(BackupSource("key/broker_secret.json", "forbidden", required=True),),
            evidence_path=repo / "evidence.json",
            status_path=repo / "status.json",
            allow_local_destination=True,
        )


def test_l2_backup_requires_external_destination_without_explicit_local_optout(tmp_path):
    repo = tmp_path / "repo"
    state = repo / "data" / "cache" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text("state", encoding="utf-8")

    with pytest.raises(L2BackupError, match="separate volume or UNC"):
        run_l2_backup(
            repo_root=repo,
            destination_root=tmp_path / "backup",
            passphrase=PASSPHRASE,
            sources=(BackupSource("data/cache/state.json", "state", required=True),),
            evidence_path=repo / "evidence.json",
            status_path=repo / "status.json",
        )


def test_l2_backup_rejects_corrupt_nonempty_sqlite_source(tmp_path):
    repo = tmp_path / "repo"
    database = repo / "data" / "cache" / "authority.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"not-a-sqlite-database")

    with pytest.raises(L2BackupError, match="not a valid SQLite database"):
        run_l2_backup(
            repo_root=repo,
            destination_root=tmp_path / "backup",
            passphrase=PASSPHRASE,
            sources=(BackupSource("data/cache/authority.db", "authority", required=True),),
            evidence_path=repo / "evidence.json",
            status_path=repo / "status.json",
            allow_local_destination=True,
        )


def _write_manifest(batch: Path, document: dict) -> None:
    encoded = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    (batch / "manifest.json.enc").write_bytes(encrypt_bytes(encoded, PASSPHRASE))


def _read_manifest(batch: Path) -> dict:
    encoded = decrypt_bytes((batch / "manifest.json.enc").read_bytes(), PASSPHRASE)
    return json.loads(encoded)


def _contract_batch(tmp_path) -> tuple[Path, tuple[BackupSource, ...], int]:
    repo = tmp_path / "repo"
    first = repo / "data" / "cache" / "first.json"
    second = repo / "data" / "cache" / "second.json"
    first.parent.mkdir(parents=True)
    first.write_text('{"first":1}', encoding="utf-8")
    second.write_text('{"second":2}', encoding="utf-8")
    sources = (
        BackupSource("data/cache/first.json", "first_authority", required=True),
        BackupSource("data/cache/second.json", "second_authority", required=False),
    )
    now_ms = 1_784_044_800_000
    result = run_l2_backup(
        repo_root=repo,
        destination_root=tmp_path / "backup",
        passphrase=PASSPHRASE,
        sources=sources,
        evidence_path=repo / "evidence.json",
        status_path=repo / "status.json",
        allow_local_destination=True,
        now_ms=now_ms,
    )
    return Path(result["batch_dir"]), sources, now_ms


def _restore_contract_batch(tmp_path, batch: Path, sources: tuple[BackupSource, ...], now_ms: int) -> None:
    restore_l2_batch(
        batch_dir=batch,
        restore_dir=tmp_path / "restored",
        passphrase=PASSPHRASE,
        rto_ms=30_000,
        max_rpo_ms=86_400_000,
        now_ms=now_ms,
        sources=sources,
    )


def test_restore_rejects_tampered_source_contract_digest(tmp_path):
    batch, sources, now_ms = _contract_batch(tmp_path)
    manifest = _read_manifest(batch)
    manifest["source_contract_sha256"] = "0" * 64
    _write_manifest(batch, manifest)

    with pytest.raises(L2BackupError, match="source contract digest"):
        _restore_contract_batch(tmp_path, batch, sources, now_ms)


def test_restore_rejects_missing_contracted_source_entry(tmp_path):
    batch, sources, now_ms = _contract_batch(tmp_path)
    manifest = _read_manifest(batch)
    removed = manifest["entries"].pop()
    (batch / f"{removed['artifact']}.enc").unlink()
    _write_manifest(batch, manifest)

    with pytest.raises(L2BackupError, match="source entries do not match"):
        _restore_contract_batch(tmp_path, batch, sources, now_ms)


def test_restore_rejects_extra_source_entry(tmp_path):
    batch, sources, now_ms = _contract_batch(tmp_path)
    manifest = _read_manifest(batch)
    extra = dict(manifest["entries"][0])
    extra.update(
        artifact="extra-artifact",
        relative_path="data/cache/extra.json",
        role="extra_authority",
    )
    manifest["entries"].append(extra)
    source_artifact = batch / f"{manifest['entries'][0]['artifact']}.enc"
    (batch / "extra-artifact.enc").write_bytes(source_artifact.read_bytes())
    _write_manifest(batch, manifest)

    with pytest.raises(L2BackupError, match="source entry is outside"):
        _restore_contract_batch(tmp_path, batch, sources, now_ms)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("role", "wrong_authority", "source entry does not match"),
        ("relative_path", "data/cache/wrong.json", "source entry is outside"),
        ("required", False, "source entry does not match"),
    ],
)
def test_restore_rejects_path_role_or_requiredness_mismatch(tmp_path, field, value, error):
    batch, sources, now_ms = _contract_batch(tmp_path)
    manifest = _read_manifest(batch)
    manifest["entries"][0][field] = value
    _write_manifest(batch, manifest)

    with pytest.raises(L2BackupError, match=error):
        _restore_contract_batch(tmp_path, batch, sources, now_ms)


def _recorded_sources() -> tuple[BackupSource, ...]:
    return (
        BackupSource(
            "data/cache/l2_restore_history.db",
            "l2_restore_history_authority",
            required=True,
        ),
        BackupSource("data/cache/state.json", "operational_state", required=True),
    )


def _seed_recorded_state(repo: Path) -> None:
    state = repo / "data" / "cache" / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text('{"state":"committed"}', encoding="utf-8")


def test_recorded_backup_commits_pass_before_sanitized_compatibility_projection(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _seed_recorded_state(repo)
    monkeypatch.setattr(
        l2_backup_module,
        "classify_destination",
        lambda *_args: "SEPARATE_VOLUME",
    )

    result = run_recorded_l2_backup(
        repo_root=repo,
        destination_root=tmp_path / "external-backup",
        passphrase=PASSPHRASE,
        sources=_recorded_sources(),
        now_ms=1_784_044_800_000,
    )

    history_path = repo / "data" / "cache" / "l2_restore_history.db"
    store = L2RestoreHistoryStore(str(history_path), create=False)
    snapshot = store.read_snapshot()
    store.close()
    assert len(snapshot.attempts) == len(snapshot.outcomes) == 1
    assert snapshot.open_attempts == ()
    assert snapshot.outcomes[0].outcome.verdict == "PASS"
    assert snapshot.outcomes[0].outcome.reason_code == "RESTORE_VERIFIED"

    evidence = json.loads((repo / "evidence" / "l2_backup_latest.json").read_text(encoding="utf-8"))
    status = json.loads((repo / "data" / "cache" / "l2_backup_status.json").read_text(encoding="utf-8"))
    assert evidence["verdict"] == "PASS"
    assert "batch_dir" not in evidence
    assert result["restore_history_verified"] is True
    assert status["schema_version"] == 2
    assert status["verdict"] == "PASS"
    assert status["destination_class"] == "SEPARATE_VOLUME"
    assert status["last_attempt_verdict"] == "PASS"
    assert status["history_event_hash"] == snapshot.authority_head.event_hash
    assert "batch_dir" not in status
    assert result["batch_dir"]


def test_recorded_backup_appends_redacted_failure_and_overwrites_old_pass_projection(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _seed_recorded_state(repo)
    evidence_path = repo / "evidence" / "l2_backup_latest.json"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text('{"schema_version":1,"verdict":"PASS"}', encoding="utf-8")
    secret_path = tmp_path / "SECRET-DESTINATION-PATH"
    secret_passphrase = "SECRET"

    def fail_core(**_kwargs):
        raise L2BackupError(f"failure exposed {secret_path} and {secret_passphrase}")

    monkeypatch.setattr(l2_backup_module, "run_l2_backup", fail_core)
    with pytest.raises(L2BackupError, match="BACKUP_ATTEMPT_FAILED") as raised:
        run_recorded_l2_backup(
            repo_root=repo,
            destination_root=secret_path,
            passphrase=secret_passphrase,
            sources=_recorded_sources(),
            now_ms=1_784_044_800_000,
        )
    assert str(secret_path) not in str(raised.value)
    assert secret_passphrase not in str(raised.value)

    history_path = repo / "data" / "cache" / "l2_restore_history.db"
    store = L2RestoreHistoryStore(str(history_path), create=False)
    snapshot = store.read_snapshot()
    store.close()
    assert snapshot.open_attempts == ()
    assert snapshot.outcomes[0].outcome.verdict == "FAIL"
    assert snapshot.outcomes[0].outcome.reason_code == "BACKUP_ATTEMPT_FAILED"
    status_path = repo / "data" / "cache" / "l2_backup_status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert status["schema_version"] == 2
    assert status["verdict"] == "BREACH"
    assert evidence["verdict"] == "FAIL"
    serialized = history_path.read_bytes() + status_path.read_bytes() + evidence_path.read_bytes()
    assert str(secret_path).encode() not in serialized
    assert secret_passphrase.encode() not in serialized


def test_recorded_backup_fails_closed_when_history_authority_is_unavailable(tmp_path):
    repo = tmp_path / "repo"
    history_path = repo / "data" / "cache" / "l2_restore_history.db"
    history_path.parent.mkdir(parents=True)
    with sqlite3.connect(history_path) as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
    before = hashlib.sha256(history_path.read_bytes()).hexdigest()

    with pytest.raises(L2BackupError, match="RESTORE_HISTORY_UNAVAILABLE"):
        run_recorded_l2_backup(
            repo_root=repo,
            destination_root=None,
            passphrase=None,
            sources=_recorded_sources(),
            now_ms=1_784_044_800_000,
        )

    status = json.loads((repo / "data" / "cache" / "l2_backup_status.json").read_text(encoding="utf-8"))
    assert status["schema_version"] == 2
    assert status["verdict"] == "BREACH"
    assert status["last_reason_code"] == "RESTORE_HISTORY_UNAVAILABLE"
    assert hashlib.sha256(history_path.read_bytes()).hexdigest() == before


def test_recorded_backup_hard_interrupt_leaves_authoritative_open_attempt(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _seed_recorded_state(repo)

    class SimulatedHardInterrupt(BaseException):
        pass

    def interrupt_core(**_kwargs):
        raise SimulatedHardInterrupt()

    monkeypatch.setattr(l2_backup_module, "run_l2_backup", interrupt_core)
    with pytest.raises(SimulatedHardInterrupt):
        run_recorded_l2_backup(
            repo_root=repo,
            destination_root=tmp_path / "backup",
            passphrase=PASSPHRASE,
            sources=_recorded_sources(),
            now_ms=1_784_044_800_000,
        )

    store = L2RestoreHistoryStore(str(repo / "data" / "cache" / "l2_restore_history.db"), create=False)
    snapshot = store.read_snapshot()
    store.close()
    assert len(snapshot.open_attempts) == 1
    assert snapshot.outcomes == ()


def test_restore_rejects_semantically_tampered_restore_history_database(tmp_path):
    repo = tmp_path / "repo"
    history_path = repo / "data" / "cache" / "l2_restore_history.db"
    store = L2RestoreHistoryStore(str(history_path), create=True)
    store.close()
    sources = (
        BackupSource(
            "data/cache/l2_restore_history.db",
            "l2_restore_history_authority",
            required=True,
        ),
    )
    result = run_l2_backup(
        repo_root=repo,
        destination_root=tmp_path / "backup",
        passphrase=PASSPHRASE,
        sources=sources,
        evidence_path=repo / "evidence.json",
        status_path=repo / "status.json",
        allow_local_destination=True,
        now_ms=1_784_044_800_000,
    )
    batch = Path(result["batch_dir"])
    manifest = _read_manifest(batch)
    entry = next(item for item in manifest["entries"] if item["role"] == "l2_restore_history_authority")
    artifact_path = batch / f"{entry['artifact']}.enc"
    tampered_path = tmp_path / "tampered-history.db"
    decrypt_file(str(artifact_path), str(tampered_path), PASSPHRASE)
    with sqlite3.connect(tampered_path) as connection:
        connection.execute("DROP TRIGGER l2_restore_history_events_no_update")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    tampered = tampered_path.read_bytes()
    encrypt_file(str(tampered_path), str(artifact_path), PASSPHRASE, chunk_size=32)
    entry["sha256"] = hashlib.sha256(tampered).hexdigest()
    entry["size"] = len(tampered)
    _write_manifest(batch, manifest)

    with pytest.raises(L2BackupError, match="restore history semantic verification"):
        restore_l2_batch(
            batch_dir=batch,
            restore_dir=tmp_path / "restored-tampered",
            passphrase=PASSPHRASE,
            rto_ms=30_000,
            max_rpo_ms=86_400_000,
            now_ms=result["observed_at_ms"],
            sources=sources,
        )
