"""Maintenance-only sealed actions require staged evidence and bound approval."""

import fcntl
import json
import os
import hashlib
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from tools import demo_maintenance_grants as issuer
from tools import demo_maintenance_window as window
from tools import demo_executor_archive_write as writer
from tests.tools.test_demo_maintenance_staging import prepared as prepared, job as job


def test_prepare_descriptor_is_sealed_and_binds_exact_staged_evidence(prepared):
    root, doc, _, _, _ = prepared
    staged = issuer.staging.stage(root, doc)
    with issuer.issue(root, doc, action="PREPARE", approved_sha256=staged["approval_sha256"]) as fd:
        body = json.loads(os.pread(fd, 65536, 0))
        assert fcntl.fcntl(fd, fcntl.F_GET_SEALS) & issuer.grants.SEALS == issuer.grants.SEALS
        assert body["inputs"] == staged["inputs"]
        assert body["window_sha256"] == doc["inhibitor_sha256"]
        assert body["archive_id"] == doc["operation_sha256"]
        assert body["manifest_sha256"] is body["receipt_sha256"] is None
        with pytest.raises(OSError):
            os.write(fd, b"changed")
    with pytest.raises(OSError):
        os.fstat(fd)


def test_wrong_approval_cannot_create_descriptor(prepared, monkeypatch):
    root, doc, _, _, _ = prepared
    issuer.staging.stage(root, doc)
    monkeypatch.setattr(issuer.os, "memfd_create", lambda *a, **kw: pytest.fail("must not issue"))
    with pytest.raises(ValueError, match="MAINTENANCE_APPROVAL_BINDING"):
        with issuer.issue(root, doc, action="PREPARE", approved_sha256="f" * 64):
            pass


def test_apply_requires_successful_prepare_before_sealing(prepared):
    root, doc, _, _, _ = prepared
    staged = issuer.staging.stage(root, doc)
    with pytest.raises(ValueError, match="MAINTENANCE_PREPARE_REQUIRED"):
        with issuer.issue(root, doc, action="APPLY", approved_sha256=staged["approval_sha256"]):
            pass


def test_preparation_descriptor_not_reissued_after_dispatch(prepared, monkeypatch):
    root, doc, _, _, _ = prepared
    staged = issuer.staging.stage(root, doc)
    monkeypatch.setattr(window, "_worker_record", lambda *a: {"action": "PREPARE"})
    with pytest.raises(ValueError, match="MAINTENANCE_ALREADY_DISPATCHED"):
        with issuer.issue(root, doc, action="PREPARE", approved_sha256=staged["approval_sha256"]):
            pass


@pytest.mark.parametrize("interrupt", [False, True])
def test_actual_transaction_can_rollback_without_capture_inputs(prepared, monkeypatch, interrupt):
    root, doc, _, _, _ = prepared
    staged = issuer.staging.stage(root, doc)
    source = issuer.staging.reader.EXECUTOR
    for name in ("observation.lock", "origin-capture.lock", "resident-recovery/cycle.lock"):
        path = source / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(b"")
            path.chmod(0o600)
    before = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
    monkeypatch.setattr(writer.reader, "_private_root", writer.io.directory)

    @contextmanager
    def admitted(fd, action):
        raw = os.pread(fd, 65536, 0)
        document = json.loads(raw)
        assert document["action"] == action
        # Real sealed descriptor + storage, mocked root-to-executor identity handoff.
        assert fcntl.fcntl(fd, fcntl.F_GET_SEALS) & issuer.grants.SEALS == issuer.grants.SEALS
        yield SimpleNamespace(document=document, raw=raw, check=lambda: None)

    monkeypatch.setattr(writer.grants, "admitted", admitted)
    previous = None
    monkeypatch.setattr(window, "_worker_record", lambda *args: previous)
    monkeypatch.setattr(window.handoff, "drained", lambda *args: True)
    approved = staged["approval_sha256"]
    with issuer.issue(root, doc, action="PREPARE", approved_sha256=approved) as fd:
        digest = hashlib.sha256(os.pread(fd, 65536, 0)).hexdigest()
        summary = writer.prepare(fd)
    previous = {
        "action": "PREPARE",
        "unit": "fixture",
        "grant_sha256": digest,
        "result": {"status": "WORKER_COMPLETED", "result": {"summary": summary}},
    }
    with issuer.issue(root, doc, action="APPLY", approved_sha256=approved) as fd:
        previous = {"action": "APPLY", "unit": "fixture", "result": None}
        if interrupt:
            move = writer.io.move

            def fail_after_move(source, target):
                move(source, target)
                raise ValueError("fixture interruption")

            with monkeypatch.context() as failure:
                failure.setattr(writer.io, "move", fail_after_move)
                with pytest.raises(ValueError, match="fixture interruption"):
                    writer.apply(fd)
        else:
            assert writer.apply(fd)["status"] == "ARCHIVED"
    # Recovery depends on transaction evidence, not availability of the large backup.
    (issuer.staging.reader.ARCHIVES / "requests" / staged["archive_id"] / "capture.zip").unlink()
    with issuer.issue(root, doc, action="ROLLBACK", approved_sha256=approved) as fd:
        assert writer.rollback(fd)["status"] == "RESTORED"
    assert before == {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
