"""Executor mutation requires a live, root-sealed, action-specific maintenance grant."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

import pytest

from tools import demo_executor_archive_write as writer
from tools import demo_executor_retention_grant as grants
from tools import demo_executor_archive_read as reader
from tools import demo_namespace_checkpoint as checkpoint
from tests.tools.test_demo_retention_transaction import reviewed_fixture


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root, names, plan, review = reviewed_fixture(tmp_path)
    archives = tmp_path / "archives"
    archives.mkdir(mode=0o700)
    archive_id = "a" * 64
    requests = archives / "requests"
    requests.mkdir(mode=0o700)
    inputs = requests / archive_id
    inputs.mkdir(mode=0o700)
    (inputs / "plan.json").write_bytes(plan.read_bytes())
    (inputs / "review.json").write_bytes(review.read_bytes())
    raw_capture = b"synthetic root-verified capture fixture"
    (inputs / "capture.zip").write_bytes(raw_capture)
    manifest = {
        "schema": checkpoint.SCHEMA,
        "namespace": "executor",
        "source_commit": "c" * 40,
        "source_uid": os.geteuid(),
        "captured_from_ms": 1,
        "captured_until_ms": 2,
        "directories": ["observation-views"],
        "state": {},
        "entries": {
            name: {
                "sha256": Path(name).stem,
                "size": (root / name).stat().st_size,
                "snapshot_method": "STABLE_FILE_READ",
            }
            for name in names
        },
    }
    (inputs / "backup-manifest.json").write_text(json.dumps(manifest))
    hashes = {
        name: hashlib.sha256((inputs / name).read_bytes()).hexdigest() for name in grants.INPUTS - {"verification.json"}
    }
    proof = {
        "schema": "DEMO_RETENTION_BACKUP_VERIFICATION_V1",
        "status": "REHEARSAL_COMPLETE",
        "capture_sha256": hashes["capture.zip"],
        "manifest_sha256": hashes["backup-manifest.json"],
        "plan_sha256": hashes["plan.json"],
        "review_sha256": hashes["review.json"],
    }
    (inputs / "verification.json").write_text(json.dumps(proof))
    hashes["verification.json"] = hashlib.sha256((inputs / "verification.json").read_bytes()).hexdigest()
    for path in inputs.iterdir():
        path.chmod(0o600)
    for relative in ("observation.lock", "origin-capture.lock", "resident-recovery/cycle.lock"):
        path = root / relative
        path.parent.mkdir(mode=0o700, exist_ok=True)
        path.write_bytes(b"")
        path.chmod(0o600)
    monkeypatch.setattr(reader, "EXECUTOR", root)
    monkeypatch.setattr(reader, "ARCHIVES", archives)
    monkeypatch.setattr(reader, "_private_root", lambda path: writer.io.directory(path))
    monkeypatch.setattr(grants, "load_registry_for_executor", lambda: {"executor_uid": os.geteuid()})
    monkeypatch.setattr(grants, "registry_sha256", lambda value: "d" * 64)
    uid = os.geteuid()
    monkeypatch.setattr(grants.os, "getresuid", lambda: (uid, uid, uid))
    monkeypatch.setattr(grants.os, "getresgid", lambda: (uid, uid, uid))
    monkeypatch.setattr(grants.os, "getgroups", lambda: [])
    monkeypatch.setattr(grants, "verify_source", lambda commit: None)
    monkeypatch.setattr(grants, "_controller", lambda doc: os.pidfd_open(os.getpid(), 0))
    original_fstat = os.fstat
    descriptors = []

    def metadata(fd):
        info = original_fstat(fd)
        if fd in descriptors:
            values = list(info)
            values[4] = 0
            return os.stat_result(values)
        return info

    monkeypatch.setattr(grants.os, "fstat", metadata)

    def grant(action, *, prepared=None, changes=None, sealed=True):
        now = time.monotonic_ns()
        info = root.stat()
        body = {
            "schema": "DEMO_RETENTION_WORKER_GRANT_V1",
            "action": action,
            "source_commit": "c" * 40,
            "executor_uid": os.geteuid(),
            "registry_sha256": "d" * 64,
            "root_identity": [info.st_dev, info.st_ino],
            "archive_id": archive_id,
            "window_sha256": "e" * 64,
            "boot_id": grants.boot_id(),
            "controller_pid": os.getpid(),
            "controller_start_ticks": 1,
            "issued_monotonic_ns": now,
            "expires_monotonic_ns": now + 60 * 10**9,
            "inputs": hashes,
            "manifest_sha256": None if prepared is None else prepared["manifest_sha256"],
            "receipt_sha256": None if prepared is None else prepared["receipt_sha256"],
        }
        body.update(changes or {})
        fd = os.memfd_create("maintenance-fixture", os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
        os.write(fd, json.dumps(body).encode())
        if sealed:
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, grants.SEALS)
        descriptors.append(fd)
        return fd

    yield root, archives / archive_id, names, inputs, grant
    for fd in descriptors:
        os.close(fd)


def test_prepared_apply_and_rollback_preserve_pins_and_bytes(setup):
    root, archive, names, _, grant = setup
    before = {name: (root / name).read_bytes() for name in names}
    prepared = writer.prepare(grant("PREPARE"))
    assert prepared["files"] == 1
    assert writer.apply(grant("APPLY", prepared=prepared))["moved"] == 1
    assert (root / names[0]).exists() and not (root / names[1]).exists()
    assert (archive / "records" / names[1]).read_bytes() == before[names[1]]
    writer.rollback(grant("ROLLBACK", prepared=prepared))
    assert all((root / name).read_bytes() == raw for name, raw in before.items())


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"action": "APPLY"}, "GRANT_SCOPE"),
        ({"root_identity": [0, 0]}, "ROOT_CHANGED"),
        ({"boot_id": "wrong"}, "GRANT_LIFETIME"),
        ({"registry_sha256": "f" * 64}, "EXECUTOR_IDENTITY"),
    ],
)
def test_invalid_grant_does_not_create_archive(setup, changes, reason):
    root, archive, names, _, grant = setup
    with pytest.raises(ValueError, match=reason):
        writer.prepare(grant("PREPARE", changes=changes))
    assert not archive.exists() and all((root / name).exists() for name in names)


def test_unsealed_grant_never_mutates(setup):
    _, archive, _, _, grant = setup
    with pytest.raises(ValueError, match="UNSEALED"):
        writer.prepare(grant("PREPARE", sealed=False))
    assert not archive.exists()


def test_changed_input_rejected_before_archive_preparation(setup):
    _, archive, _, inputs, grant = setup
    descriptor = grant("PREPARE")
    (inputs / "capture.zip").write_bytes(b"changed")
    with pytest.raises(ValueError, match="INPUT_DIGEST"):
        writer.prepare(descriptor)
    assert not archive.exists()


def test_manifest_must_match_new_action_grant(setup):
    root, _, names, _, grant = setup
    prepared = writer.prepare(grant("PREPARE"))
    with pytest.raises(ValueError, match="MANIFEST_GRANT"):
        writer.apply(grant("APPLY", prepared=prepared, changes={"manifest_sha256": "f" * 64}))
    assert all((root / name).exists() for name in names)


def test_expired_grant_is_not_a_reusable_disk_approval(setup):
    _, archive, _, _, grant = setup
    now = time.monotonic_ns()
    with pytest.raises(ValueError, match="GRANT_EXPIRED"):
        writer.prepare(grant("PREPARE", changes={"issued_monotonic_ns": now - 10**9, "expires_monotonic_ns": now - 1}))
    assert not archive.exists()


def test_partial_move_can_roll_back_under_fresh_grant_without_original_capture(setup, monkeypatch):
    root, _, names, inputs, grant = setup
    before = {name: (root / name).read_bytes() for name in names}
    prepared = writer.prepare(grant("PREPARE"))
    original = writer.io.move

    def crash(source, target):
        original(source, target)
        raise RuntimeError("interrupted after rename")

    monkeypatch.setattr(writer.io, "move", crash)
    with pytest.raises(RuntimeError):
        writer.apply(grant("APPLY", prepared=prepared))
    assert not (root / names[1]).exists()
    monkeypatch.setattr(writer.io, "move", original)
    inputs.parent.rename(inputs.parent.with_name("retained-requests"))
    writer.rollback(grant("ROLLBACK", prepared=prepared, changes={"window_sha256": "f" * 64}))
    assert all((root / name).read_bytes() == raw for name, raw in before.items())


def test_apply_cannot_silently_switch_maintenance_windows(setup):
    root, _, names, _, grant = setup
    prepared = writer.prepare(grant("PREPARE"))
    with pytest.raises(ValueError, match="RECEIPT_CONTEXT"):
        writer.apply(grant("APPLY", prepared=prepared, changes={"window_sha256": "f" * 64}))
    assert all((root / name).exists() for name in names)


@pytest.mark.skipif(os.geteuid() == 0, reason="requires an ordinary unprivileged process")
def test_research_owned_sealed_memfd_cannot_issue_authority():
    fd = os.memfd_create("not-root", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        os.write(fd, b"{}")
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, grants.SEALS)
        with pytest.raises(ValueError, match="GRANT_CUSTODY"):
            writer.prepare(fd)
    finally:
        os.close(fd)


def test_busy_writer_lock_prevents_preparation(setup):
    root, archive, _, _, grant = setup
    with writer.executor_barrier(root):
        with pytest.raises(ValueError, match="ARCHIVE_WRITER_BUSY"):
            writer.prepare(grant("PREPARE"))
    assert not archive.exists()


def test_controller_exit_prevents_prepare(setup, monkeypatch):
    import subprocess
    import sys

    _, archive, _, _, grant = setup
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    pidfd = os.pidfd_open(child.pid, 0)
    try:
        child.terminate()
        child.wait(timeout=5)
        monkeypatch.setattr(grants, "_controller", lambda doc: os.dup(pidfd))
        with pytest.raises(ValueError, match="CONTROLLER_EXITED"):
            writer.prepare(grant("PREPARE"))
        assert not archive.exists()
    finally:
        os.close(pidfd)
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


@pytest.mark.parametrize("damage", ["candidate", "failed_rehearsal"])
def test_root_bound_but_inadequate_backup_evidence_is_rejected(setup, damage):
    root, archive, names, inputs, grant = setup
    proof_path = inputs / "verification.json"
    proof = json.loads(proof_path.read_bytes())
    if damage == "candidate":
        path = inputs / "backup-manifest.json"
        manifest = json.loads(path.read_bytes())
        del manifest["entries"][names[1]]
        path.write_text(json.dumps(manifest))
        proof["manifest_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        reason = "CANDIDATE_NOT_BACKED_UP"
    else:
        proof["status"] = "REHEARSAL_INCOMPLETE"
        reason = "BACKUP_VERIFICATION"
    proof_path.write_text(json.dumps(proof))
    hashes = {name: hashlib.sha256((inputs / name).read_bytes()).hexdigest() for name in grants.INPUTS}
    with pytest.raises(ValueError, match=reason):
        writer.prepare(grant("PREPARE", changes={"inputs": hashes}))
    assert not archive.exists() and all((root / name).exists() for name in names)


def test_development_source_cannot_run_even_with_fixture_grant(setup, monkeypatch):
    from tools.demo_issuer import verify_source

    _, archive, _, _, grant = setup
    monkeypatch.setattr(grants, "verify_source", verify_source)
    with pytest.raises(ValueError, match="ISSUER_SOURCE_LOCATION"):
        writer.prepare(grant("PREPARE"))
    assert not archive.exists()


def test_effective_executor_with_retained_root_cannot_mutate(setup, monkeypatch):
    _, archive, _, _, grant = setup
    uid = os.geteuid()
    monkeypatch.setattr(grants.os, "getresuid", lambda: (0, uid, 0))
    with pytest.raises(ValueError, match="EXECUTOR_PRIVILEGES"):
        writer.prepare(grant("PREPARE"))
    assert not archive.exists()
