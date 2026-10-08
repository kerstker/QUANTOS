"""One operator invocation restores services, with explicit per-batch consent."""

from contextlib import contextmanager
import json

import pytest

from tools import demo_maintenance_operator as operator


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(operator, "ROOT_PARENT", tmp_path)
    monkeypatch.setattr(operator, "_admit", lambda *args: None)
    calls = []
    state = {"phase": "PREPARED", "worker": None, "fail": None}

    def prepare(root, operation):
        calls.append("prepare")
        root.mkdir(mode=0o700)
        (root / "window.json").write_text("{}")
        (root / "window.json").chmod(0o600)

    def pause(root):
        calls.append("pause")
        state["phase"] = "PAUSED"

    def capture(*args, **kw):
        calls.append("capture")
        if state["fail"] == "capture":
            raise ValueError("private fixture data")
        return {"selected_files": 2}

    def stage(root):
        calls.append("stage")
        return {"approval_sha256": "a" * 64, "selected_files": 2}

    def execute(root, **kw):
        calls.append("execute")
        assert kw["approved_sha256"] == "a" * 64
        assert json.loads((root.parent / "approval.json").read_bytes())["approved_sha256"] == "a" * 64
        state["worker"] = {"action": "APPLY", "unit": "fixture", "grant_sha256": "b" * 64, "result": None}
        if state["fail"] == "apply":
            raise KeyboardInterrupt()
        state["worker"]["result"] = {"status": "WORKER_COMPLETED"}
        return {"status": "ARCHIVED"}

    def recover(root, **kw):
        calls.append("rollback")
        state["worker"] = {"action": "ROLLBACK", "result": {"status": "WORKER_COMPLETED"}}

    def restore(root):
        calls.append("restore")
        if state["fail"] == "restore":
            raise ValueError("private restore error")
        state["phase"] = "RESTORED"
        return {"status": "RESTORED"}

    @contextmanager
    def locked(root):
        yield root, {"phase": state["phase"]}

    for name, function in {
        "prepare": prepare,
        "pause": pause,
        "prepare_evidence": capture,
        "stage_evidence": stage,
        "execute_archive": execute,
        "recover_archive": recover,
        "restore": restore,
        "_locked": locked,
    }.items():
        monkeypatch.setattr(operator.window, name, function)
    monkeypatch.setattr(operator.window, "_worker_record", lambda *a: state["worker"])
    monkeypatch.setattr(operator.preparation, "inspect", lambda *a: None)
    return calls, state


def test_rehearsal_never_stages_or_requests_archive_approval(runtime):
    calls, _ = runtime
    result = operator.start(
        source_commit="c" * 40, mode="REHEARSE", confirm=lambda *args: pytest.fail("no archive prompt")
    )
    assert calls == ["prepare", "pause", "capture", "restore"]
    assert result["status"] == "REHEARSAL_COMPLETE" and result["services_restored"] is True
    assert result["archive_state"] == "NOT_RUN"


def test_archive_decline_restores_without_issuing_actions(runtime):
    calls, _ = runtime
    result = operator.start(source_commit="c" * 40, mode="ARCHIVE", confirm=lambda *a: False)
    assert "execute" not in calls and calls[-1] == "restore"
    assert result["status"] == "ARCHIVE_DECLINED" and result["archive_state"] == "NOT_RUN"


def test_archive_approval_is_persisted_before_execution(runtime):
    calls, _ = runtime
    result = operator.start(source_commit="c" * 40, mode="ARCHIVE", confirm=lambda *a: True)
    assert calls == ["prepare", "pause", "capture", "stage", "execute", "restore"]
    assert result["status"] == result["archive_state"] == "ARCHIVED"
    assert result["services_restored"] is True


def test_capture_failure_restores_and_sanitizes(runtime):
    calls, state = runtime
    state["fail"] = "capture"
    result = operator.start(source_commit="c" * 40, mode="REHEARSE", confirm=lambda *a: False)
    assert calls[-1] == "restore" and result["services_restored"] is True
    assert result["status"] == "MAINTENANCE_INCOMPLETE"
    assert "private" not in json.dumps(result)


def test_interrupt_after_apply_dispatch_rolls_back_before_restore(runtime):
    calls, state = runtime
    state["fail"] = "apply"
    result = operator.start(source_commit="c" * 40, mode="ARCHIVE", confirm=lambda *a: True)
    assert calls[-2:] == ["rollback", "restore"]
    assert result["archive_state"] == "ROLLED_BACK" and result["services_restored"] is True


def test_restore_failure_never_claims_services_restored(runtime):
    _, state = runtime
    state["fail"] = "restore"
    result = operator.start(source_commit="c" * 40, mode="REHEARSE", confirm=lambda *a: False)
    assert result["status"] == "RECOVERY_REQUIRED" and result["services_restored"] is False


def test_recovery_uses_saved_approval_and_never_restarts_archive(runtime, monkeypatch):
    calls, state = runtime
    state["fail"] = "apply"
    recover = operator.window.recover_archive

    def unavailable(*args, **kw):
        raise ValueError("fixture recovery unavailable")

    monkeypatch.setattr(operator.window, "recover_archive", unavailable)
    failed = operator.start(source_commit="c" * 40, mode="ARCHIVE", confirm=lambda *a: True)
    assert failed["status"] == "RECOVERY_REQUIRED"
    assert failed["archive_state"] == "UNKNOWN"
    monkeypatch.setattr(operator.window, "recover_archive", recover)
    calls.clear()
    result = operator.recover(source_commit="c" * 40, operation=failed["operation_id"])
    assert result["status"] == "RECOVERY_COMPLETE" and result["archive_state"] == "ROLLED_BACK"
    assert calls == ["rollback", "restore"]


def test_wrong_source_recovery_stops_before_service_actions(runtime):
    calls, _ = runtime
    completed = operator.start(source_commit="c" * 40, mode="REHEARSE", confirm=lambda *a: False)
    calls.clear()
    with pytest.raises(ValueError, match="OPERATOR_RUN_BINDING"):
        operator.recover(source_commit="d" * 40, operation=completed["operation_id"])
    assert calls == []


def test_public_summary_is_readable_and_contains_only_sanitized_result(runtime):
    from pathlib import Path

    result = operator.start(source_commit="c" * 40, mode="REHEARSE", confirm=lambda *a: False)
    path = Path(operator._public_result(result))
    assert json.loads(path.read_bytes()) == result
    assert path.stat().st_mode & 0o777 == 0o644
    assert "private" not in path.read_text()


def test_nonroot_admission_stops_before_source_or_service_access(monkeypatch):
    monkeypatch.setattr(operator.os, "getresuid", lambda: (1000, 1000, 1000))
    monkeypatch.setattr(operator, "verify_source", lambda *args: pytest.fail("must reject first"))
    with pytest.raises(ValueError, match="OPERATOR_ROOT_REQUIRED"):
        operator._admit("c" * 40)


def test_single_operator_lock_excludes_concurrent_invocations(tmp_path, monkeypatch):
    monkeypatch.setattr(operator.os, "getresuid", lambda: (0, 0, 0))
    monkeypatch.setattr(operator.inhibit, "CONTROL", tmp_path)

    @contextmanager
    def admitted_directory():
        yield

    monkeypatch.setattr(operator.inhibit, "_locked", admitted_directory)
    with operator.exclusive():
        with pytest.raises(BlockingIOError):
            with operator.exclusive():
                pytest.fail("second operator acquired lock")
    with operator.exclusive():
        pass


def test_archive_without_tty_fails_before_window_creation(runtime, monkeypatch, capsys):
    from types import SimpleNamespace

    calls, _ = runtime
    monkeypatch.setattr(operator.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    assert operator.main(["--source-commit", "c" * 40, "--operator-archive"]) == 3
    assert not calls
    assert json.loads(capsys.readouterr().out)["status"] == "OPERATOR_INCOMPLETE"
