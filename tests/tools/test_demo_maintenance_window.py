"""Restartable scoped maintenance, including start inhibition and configuration binding."""

import json

import pytest

from tools import demo_maintenance_window as window
from tools import demo_maintenance_services as services


class Runtime:
    def __init__(self):
        self.states = {name: "inactive" for name in services.UNITS}
        self.states[services.UNITS[0]] = "active"
        self.states[services.UNITS[-1]] = "active"
        self.inhibited = False
        self.loaded = False
        self.calls = []
        self.failure = None
        self.fingerprint = "a" * 64

    def install(self, token,*,units=services.UNITS):
        assert set(units)==set(self.states)
        self.calls.append("install")
        self.inhibited = True

    def remove(self, token,*,units=services.UNITS):
        assert set(units)==set(self.states)
        self.calls.append("remove")
        self.inhibited = False

    def reload(self):
        self.calls.append("reload")
        if self.failure == "reload":
            raise RuntimeError("reload failed")
        self.loaded = self.inhibited

    def backend(self, token=None, *, require_inhibition=True):
        runtime = self

        class Backend:
            @property
            def units(self):return tuple(runtime.states)

            def inspect(self, name):
                if token and require_inhibition:
                    assert runtime.inhibited and runtime.loaded
                elif not token:
                    assert not runtime.loaded
                return {"active": runtime.states[name], "fingerprint": runtime.fingerprint}

            def idle(self, name):
                return runtime.states[name] in ("inactive", "failed")

            def change(self, action, name):
                runtime.calls.append((action, name))
                if runtime.failure == (action, name):
                    raise RuntimeError("service failed")
                if action == "start":
                    assert not runtime.loaded
                runtime.states[name] = "active" if action == "start" else "inactive"

        return Backend()


@pytest.fixture
def environment(tmp_path, monkeypatch):
    runtime = Runtime()
    monkeypatch.setattr(window, "backend", runtime.backend)
    monkeypatch.setattr(window.inhibit, "install", runtime.install)
    monkeypatch.setattr(window.inhibit, "remove", runtime.remove)
    monkeypatch.setattr(window, "reload_manager", runtime.reload)
    monkeypatch.setattr(window, "boot_id", lambda: "a" * 32)
    root = tmp_path / "window"
    window.prepare(root, "b" * 64)
    return root, runtime


def test_pause_reload_and_restore_original_states(environment):
    root, runtime = environment
    original = dict(runtime.states)
    assert window.pause(root)["quiescence_proven"] is False
    assert runtime.calls[:2] == ["install", "reload"]
    assert runtime.loaded
    assert set(runtime.states.values()) == {"inactive"}
    window.restore(root)
    assert runtime.states == original
    assert runtime.calls.index("remove") < runtime.calls.index(("start", services.UNITS[0]))
    calls = list(runtime.calls)
    window.restore(root)
    assert runtime.calls == calls


def test_evidence_preparation_requires_paused_locked_window(environment, monkeypatch):
    import fcntl

    root, runtime = environment
    calls = []

    def prepare(path, document, **kwargs):
        assert path == root and document["phase"] == "PAUSED"
        assert runtime.loaded and set(runtime.states.values()) == {"inactive"}
        with (root / "window.lock").open("rb") as stream:
            with pytest.raises(BlockingIOError):
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        calls.append(kwargs)
        return {"status": "WINDOW_EVIDENCE_PREPARED", "authorization_issued": False}

    monkeypatch.setattr(window.preparation, "run", prepare)
    with pytest.raises(ValueError, match="WINDOW_NOT_PAUSED"):
        window.prepare_evidence(root, source_commit="c" * 40)
    assert not calls
    window.pause(root)
    assert window.prepare_evidence(root, source_commit="c" * 40)["authorization_issued"] is False
    assert len(calls) == 1


def test_preparation_child_blocks_restoring_service_configuration(environment, monkeypatch):
    root, runtime = environment
    window.pause(root)

    def pending(*args):
        raise ValueError("PREPARATION_WORKER_STILL_RUNNING")

    monkeypatch.setattr(window.preparation, "restore_guard", pending)
    with pytest.raises(ValueError, match="PREPARATION_WORKER_STILL_RUNNING"):
        window.restore(root)
    assert runtime.loaded and runtime.inhibited
    assert "remove" not in runtime.calls


def test_failed_prepare_can_restore_only_after_fresh_unmoved_proof(environment, monkeypatch):
    from tools import demo_maintenance_staging as staging

    root, runtime = environment
    window.pause(root)
    previous = {"action": "PREPARE", "unit": "fixture", "grant_sha256": "c" * 64, "result": None}
    monkeypatch.setattr(window, "_worker_record", lambda *args: previous)
    monkeypatch.setattr(window.handoff, "drained", lambda *args: True)
    monkeypatch.setattr(staging, "bound_receipt", lambda *args: {"approval_sha256": "a" * 64})
    checks = []
    monkeypatch.setattr(
        staging, "verify_unmoved", lambda *args, **kw: checks.append(kw) or {"status": "SOURCE_CANDIDATES_INTACT"}
    )
    with pytest.raises(ValueError, match="WINDOW_WORKER_RECOVERY_REQUIRED"):
        window.restore(root)
    window.abandon_prepare(root, approved_sha256="a" * 64)
    window.restore(root)
    assert len(checks) == 2 and not runtime.inhibited


def test_prepare_abort_never_accepts_an_apply_dispatch(environment, monkeypatch):
    from tools import demo_maintenance_staging as staging

    root, _ = environment
    window.pause(root)
    monkeypatch.setattr(window, "_worker_record", lambda *args: {"action": "APPLY"})
    monkeypatch.setattr(staging, "bound_receipt", lambda *args: {"approval_sha256": "a" * 64})
    with pytest.raises(ValueError, match="PREPARE_ABORT_SCOPE"):
        window.abandon_prepare(root, approved_sha256="a" * 64)


@pytest.mark.parametrize("failure", ["none", "apply", "interrupted", "rollback"])
def test_archive_execution_rolls_back_once_on_failed_apply(environment, monkeypatch, failure):
    root, runtime = environment
    window.pause(root)
    previous = None
    calls = []
    monkeypatch.setattr(window, "_worker_record", lambda *args: previous)
    monkeypatch.setattr(window.handoff, "drained", lambda *args: True)
    from tools import demo_maintenance_staging as staging

    monkeypatch.setattr(staging, "bound_receipt", lambda *args: {"approval_sha256": "a" * 64})

    def action(root, document, action, approved):
        nonlocal previous
        calls.append(action)
        previous = {"action": action, "unit": "fixture", "grant_sha256": "b" * 64, "result": None}
        if action == "APPLY" and failure == "interrupted":
            raise ValueError("interrupted after dispatch")
        failed = (action == "APPLY" and failure in {"apply", "rollback"}) or (
            action == "ROLLBACK" and failure == "rollback"
        )
        result = {
            "status": "WORKER_INSPECTION_REQUIRED" if failed else "WORKER_COMPLETED",
            "result": {
                "summary": {"status": {"PREPARE": "PREPARED", "APPLY": "ARCHIVED", "ROLLBACK": "RESTORED"}[action]}
            },
        }
        previous["result"] = result
        return result

    monkeypatch.setattr(window, "_action", action)
    if failure == "rollback":
        with pytest.raises(ValueError, match="MAINTENANCE_RECOVERY_REQUIRED"):
            window.execute_archive(root, approved_sha256="a" * 64)
    else:
        result = window.execute_archive(root, approved_sha256="a" * 64)
        assert result["status"] == ("ARCHIVED" if failure == "none" else "ARCHIVE_FAILED_ROLLED_BACK")
    assert calls == (["PREPARE", "APPLY"] if failure == "none" else ["PREPARE", "APPLY", "ROLLBACK"])
    assert runtime.inhibited and runtime.loaded
    assert json.loads((root / "window.json").read_bytes())["phase"] == "PAUSED"


def test_recovery_rejects_wrong_approval_before_stopping_worker(environment, monkeypatch):
    from tools import demo_maintenance_staging as staging

    root, runtime = environment
    window.pause(root)
    monkeypatch.setattr(staging, "bound_receipt", lambda *args: {"approval_sha256": "a" * 64})
    monkeypatch.setattr(
        window, "_worker_record", lambda *args: {"action": "APPLY", "unit": "fixture", "grant_sha256": "b" * 64}
    )
    monkeypatch.setattr(window.handoff, "drained", lambda *args: False)
    monkeypatch.setattr(window.handoff, "stop_owned", lambda *args: pytest.fail("must check approval before stopping"))
    with pytest.raises(ValueError, match="MAINTENANCE_APPROVAL_BINDING"):
        window.recover_archive(root, approved_sha256="c" * 64)


def test_pause_failure_keeps_restore_recoverable(environment):
    root, runtime = environment
    original = dict(runtime.states)
    runtime.failure = ("stop", services.UNITS[0])
    with pytest.raises(RuntimeError):
        window.pause(root)
    runtime.failure = None
    window.restore(root)
    assert runtime.states == original
    assert not runtime.loaded


def test_reload_failure_never_stops_units_and_can_cleanup(environment):
    root, runtime = environment
    runtime.failure = "reload"
    with pytest.raises(RuntimeError):
        window.pause(root)
    assert not any(isinstance(call, tuple) for call in runtime.calls)
    runtime.failure = None
    window.restore(root)
    assert not runtime.inhibited and not runtime.loaded


def test_config_drift_retains_inhibitor_and_starts_nothing(environment):
    root, runtime = environment
    window.pause(root)
    runtime.fingerprint = "c" * 64
    with pytest.raises(ValueError, match="CONFIGURATION_CHANGED"):
        window.restore(root)
    assert runtime.loaded and runtime.inhibited
    assert not any(isinstance(call, tuple) and call[0] == "start" for call in runtime.calls)


def test_reboot_cannot_reuse_old_pause_evidence(environment, monkeypatch):
    root, runtime = environment
    window.pause(root)
    calls = list(runtime.calls)
    monkeypatch.setattr(window, "boot_id", lambda: "d" * 32)
    with pytest.raises(ValueError, match="WINDOW_BOOT_CHANGED"):
        window.pause(root)
    assert runtime.calls == calls


def test_interruption_after_remove_resumes_reload_and_restore(environment, monkeypatch):
    root, runtime = environment
    window.pause(root)
    runtime.failure = "reload"
    with pytest.raises(RuntimeError):
        window.restore(root)
    assert not runtime.inhibited
    runtime.failure = None
    window.restore(root)
    assert runtime.states[services.UNITS[0]] == "active"
    assert json.loads((root / "window.json").read_bytes())["phase"] == "RESTORED"


def test_same_request_in_two_directories_has_different_inhibitor_ownership(environment):
    root, _ = environment
    other = root.parent / "other"
    window.prepare(other, "b" * 64)
    first = json.loads((root / "window.json").read_bytes())
    second = json.loads((other / "window.json").read_bytes())
    assert first["inhibitor_sha256"] != second["inhibitor_sha256"]


def test_copied_window_cannot_restore_other_windows_services(environment):
    import shutil

    root, runtime = environment
    window.pause(root)
    other = root.parent / "copy"
    shutil.copytree(root, other)
    calls = list(runtime.calls)
    with pytest.raises(ValueError, match="WINDOW_IDENTITY_CHANGED"):
        window.restore(other)
    assert runtime.calls == calls


def test_process_scan_only_inside_still_paused_window(environment, monkeypatch):
    root, runtime = environment
    calls = []

    def observe(**kwargs):
        calls.append(kwargs)
        return {"status": "SCOPED_PROCESSES_PRESENT", "quiescence_proven": False}

    monkeypatch.setattr(window.processes, "observe", observe)
    with pytest.raises(ValueError, match="WINDOW_NOT_PAUSED"):
        window.check_processes(root, executor_uid=1001, issuer_uid=1002)
    assert calls == []
    window.pause(root)
    result = window.check_processes(root, executor_uid=1001, issuer_uid=1002)
    assert result["status"] == "SCOPED_PROCESSES_PRESENT"
    assert runtime.loaded and not any(state == "active" for state in runtime.states.values())
    assert json.loads((root / "process-check.json").read_bytes()) == result


def test_loss_of_pause_during_scan_cannot_publish_success(environment, monkeypatch):
    root, runtime = environment
    window.pause(root)

    def observe(**kwargs):
        runtime.states[services.UNITS[0]] = "active"
        return {"status": "NO_SCOPED_PROCESS_OBSERVED", "quiescence_proven": False}

    monkeypatch.setattr(window.processes, "observe", observe)
    with pytest.raises(ValueError, match="PAUSE_LOST"):
        window.check_processes(root, executor_uid=1001, issuer_uid=1002)
    assert not (root / "process-check.json").exists()


@pytest.mark.parametrize("completed", [False, True])
def test_worker_dispatch_is_bound_and_unresolved_job_blocks_restore(environment, monkeypatch, completed):
    root, runtime = environment
    window.pause(root)
    document = json.loads((root / "window.json").read_bytes())
    grant = {"window_sha256": document["inhibitor_sha256"], "archive_id": document["operation_sha256"]}
    monkeypatch.setattr(window.handoff.grants, "read_descriptor", lambda *a: (grant, b"fixture"))
    result = {
        "status": "WORKER_COMPLETED" if completed else "WORKER_INSPECTION_REQUIRED",
        "worker_drained": True,
        "safe_to_restore_services": False,
        "result": None,
    }
    if completed:
        result["result"] = {
            "schema": window.handoff.SCHEMA,
            "action": "PREPARE",
            "ok": True,
            "summary": {
                "status": "NO_ELIGIBLE_RECORDS",
                "files": 0,
                "bytes": 0,
                "pinned_files": 1,
                "all_references_proven": False,
                "production_ready": False,
            },
        }

    def run(fd, **kwargs):
        assert json.loads((root / "worker.json").read_bytes())["result"] is None
        assert runtime.loaded
        return result

    monkeypatch.setattr(window.handoff, "run_once", run)
    monkeypatch.setattr(window.handoff, "drained", lambda unit: True)
    unit = "quantos-demo-retention-" + "a" * 32 + ".service"
    assert window.run_worker(root, 0, action="PREPARE", unit=unit) == result
    if completed:
        window.restore(root)
        assert runtime.states[services.UNITS[0]] == "active"
    else:
        with pytest.raises(ValueError, match="WORKER_RECOVERY_REQUIRED"):
            window.restore(root)
        assert runtime.loaded


def test_other_window_grant_never_dispatches(environment, monkeypatch):
    root, _ = environment
    window.pause(root)
    monkeypatch.setattr(
        window.handoff.grants,
        "read_descriptor",
        lambda *a: ({"window_sha256": "0" * 64, "archive_id": "b" * 64}, b"fixture"),
    )
    monkeypatch.setattr(window.handoff, "run_once", lambda *a, **kw: pytest.fail("must not dispatch"))
    with pytest.raises(ValueError, match="WINDOW_WORKER_BINDING"):
        window.run_worker(root, 0, action="APPLY", unit="quantos-demo-retention-" + "a" * 32 + ".service")


def test_success_flag_without_bound_worker_packet_cannot_restore(environment, monkeypatch):
    root, runtime = environment
    window.pause(root)
    document = json.loads((root / "window.json").read_bytes())
    record = {
        "schema": "DEMO_MAINTENANCE_WORKER_DISPATCH_V1",
        "window_sha256": document["inhibitor_sha256"],
        "unit": "quantos-demo-retention-" + "a" * 32 + ".service",
        "action": "APPLY",
        "grant_sha256": "b" * 64,
        "result": {
            "status": "WORKER_COMPLETED",
            "worker_drained": True,
            "safe_to_restore_services": False,
            "result": None,
        },
    }
    services._save(root / "worker.json", record)
    monkeypatch.setattr(window.handoff, "drained", lambda unit: True)
    with pytest.raises(ValueError, match="WORKER_PROTOCOL"):
        window.restore(root)
    assert runtime.loaded


def test_feedback_enrollment_flows_through_inhibit_pause_and_restore(tmp_path,monkeypatch):
    runtime=Runtime();runtime.states.update({name:'active' for name in services.FEEDBACK_UNITS})
    monkeypatch.setattr(window,'backend',runtime.backend)
    monkeypatch.setattr(window.inhibit,'install',runtime.install)
    monkeypatch.setattr(window.inhibit,'remove',runtime.remove)
    monkeypatch.setattr(window,'reload_manager',runtime.reload)
    monkeypatch.setattr(window,'boot_id',lambda:'a'*32)
    root=tmp_path/'window';window.prepare(root,'b'*64)
    original=dict(runtime.states)
    window.pause(root)
    assert runtime.loaded and set(runtime.states.values())=={'inactive'}
    assert all(('stop',name) in runtime.calls for name in services.FEEDBACK_UNITS)
    window.restore(root)
    assert not runtime.loaded and runtime.states==original
    assert all(('start',name) in runtime.calls for name in services.FEEDBACK_UNITS)
