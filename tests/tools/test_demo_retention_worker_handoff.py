"""Sealed input is preserved; a worker result never substitutes for process drain."""

import json

import pytest

from tools import demo_retention_worker_handoff as handoff
from tools import demo_retention_worker as worker


def test_command_denies_network_credentials_and_database_writes():
    unit = "quantos-demo-retention-" + "a" * 32 + ".service"
    command = handoff.command(source_commit="b" * 40, uid=1001, action="APPLY", unit=unit, grant_sha256="c" * 64)
    properties = dict(
        item.removeprefix("--property=").split("=", 1) for item in command if item.startswith("--property=")
    )
    assert properties["PrivateNetwork"] == "yes"
    assert properties["RestrictAddressFamilies"] == "AF_UNIX"
    assert properties["TemporaryFileSystem"] == "/etc/quantos:ro,mode=0755"
    assert properties["BindReadOnlyPaths"] == "/etc/quantos/demo-submission/authority.json"
    assert properties["User"] == properties["Group"] == "1001"
    assert properties["CapabilityBoundingSet"] == ""
    assert properties["ReadOnlyPaths"].endswith("/var/lib/quantos-demo-archives/requests")
    writable = properties["ReadWritePaths"].split()
    assert "/var/lib/quantos-demo-executor" not in writable
    assert not any("sqlite" in path or "/etc/" in path for path in writable)
    assert properties["KillMode"] == "control-group"
    assert "--pipe" in command and "--no-ask-password" in command
    assert "ALLOW_ORDER_SUBMISSION=false" in command


@pytest.mark.parametrize(
    "uid,action,unit",
    [(0, "APPLY", "x"), (1001, "TRADE", "x"), (1001, "APPLY", "quantos-gold-account-monitor.service")],
)
def test_command_rejects_other_roles_actions_and_units(uid, action, unit):
    with pytest.raises(ValueError):
        handoff.command(source_commit="b" * 40, uid=uid, action=action, unit=unit, grant_sha256="c" * 64)


def test_worker_uses_descriptor_zero_and_sanitizes_failures(monkeypatch, capsys):
    calls = []

    def fail(fd):
        calls.append(fd)
        raise ValueError("private account material must never be printed")

    monkeypatch.setattr(worker.writer, "apply", fail)
    assert worker.main(["--action", "APPLY"]) == 78
    output = capsys.readouterr()
    value = json.loads(output.out)
    assert calls == [0]
    assert "private account" not in output.out + output.err
    assert value["ok"] is False and value["summary"]["transaction_state"] == "INSPECTION_REQUIRED"


def test_worker_success_does_not_assert_service_restoration(monkeypatch, capsys):
    monkeypatch.setattr(
        worker.writer,
        "rollback",
        lambda fd: {
            "status": "RESTORED",
            "restored": 3,
            "production_ready": False,
            "production_data_archived": False,
            "submission_enabled": False,
        },
    )
    assert worker.main(["--action", "ROLLBACK"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["ok"] is True
    assert "services_restored" not in value


def test_live_descendants_are_not_a_drained_worker(monkeypatch, tmp_path):
    unit = "quantos-demo-retention-" + "a" * 32 + ".service"
    path = tmp_path / "system.slice" / unit
    path.mkdir(parents=True)
    (path / "cgroup.events").write_text("populated 1\nfrozen 0\n")
    monkeypatch.setattr(handoff, "CGROUP", tmp_path)
    monkeypatch.setattr(
        handoff,
        "show",
        lambda unit: {"LoadState": "loaded", "ActiveState": "inactive", "ControlGroup": "/system.slice/" + unit},
    )
    assert handoff.drained(unit) is False


def test_collected_unit_requires_absent_or_empty_cgroup(monkeypatch, tmp_path):
    unit = "quantos-demo-retention-" + "a" * 32 + ".service"
    monkeypatch.setattr(handoff, "CGROUP", tmp_path)
    monkeypatch.setattr(
        handoff, "show", lambda unit: {"LoadState": "not-found", "ActiveState": "inactive", "ControlGroup": ""}
    )
    assert handoff.drained(unit) is True
    path = tmp_path / "system.slice" / unit
    path.mkdir(parents=True)
    (path / "cgroup.events").write_text("populated 1\n")
    assert handoff.drained(unit) is False


def test_different_job_is_never_stopped(monkeypatch):
    unit = "quantos-demo-retention-" + "a" * 32 + ".service"
    monkeypatch.setattr(handoff, "show", lambda unit: {"Description": "someone else's job", "LoadState": "loaded"})
    monkeypatch.setattr(handoff.subprocess, "run", lambda *a, **kw: pytest.fail("must not stop a foreign job"))
    with pytest.raises(ValueError, match="WORKER_OWNERSHIP"):
        handoff.stop_owned(unit, "b" * 64)


@pytest.mark.parametrize(
    "change",
    [{"restored": "private data"}, {"production_ready": True}, {"submission_enabled": True}, {"status": "ARCHIVED"}],
)
def test_malformed_or_authority_claiming_output_is_rejected(change):
    summary = {
        "status": "RESTORED",
        "restored": 3,
        "production_ready": False,
        "production_data_archived": False,
        "submission_enabled": False,
    }
    summary.update(change)
    raw = json.dumps({"schema": worker.SCHEMA, "action": "ROLLBACK", "ok": True, "summary": summary}).encode()
    with pytest.raises(ValueError, match="WORKER_PROTOCOL"):
        handoff._packet(raw, "ROLLBACK")


@pytest.mark.parametrize("lingering", [False, True])
def test_real_child_completion_or_timeout_never_restores_services(monkeypatch, lingering):
    import os
    import sys
    import time

    packet = {
        "schema": worker.SCHEMA,
        "action": "ROLLBACK",
        "ok": True,
        "summary": {
            "status": "RESTORED",
            "restored": 1,
            "production_ready": False,
            "production_data_archived": False,
            "submission_enabled": False,
        },
    }
    script = "import json,time; print(" + repr(json.dumps(packet)) + ",flush=True)"
    if lingering:
        script += "; time.sleep(20)"
    doc = {
        "source_commit": "a" * 40,
        "executor_uid": 1001,
        "expires_monotonic_ns": time.monotonic_ns() + (300_000_000 if lingering else 10**10),
    }
    monkeypatch.setattr(handoff, "_preflight", lambda *args: (doc, "b" * 64))
    monkeypatch.setattr(handoff, "command", lambda **kwargs: [sys.executable, "-c", script])
    monkeypatch.setattr(handoff, "drained", lambda unit: not lingering)
    stops = []
    monkeypatch.setattr(handoff, "stop_owned", lambda *args: stops.append(args))
    fd = os.memfd_create("fixture")
    try:
        result = handoff.run_once(fd, action="ROLLBACK", unit="quantos-demo-retention-" + "c" * 32 + ".service")
    finally:
        os.close(fd)
    assert result["safe_to_restore_services"] is False
    assert result["worker_drained"] is (not lingering)
    assert result["status"] == ("WORKER_INSPECTION_REQUIRED" if lingering else "WORKER_COMPLETED")
    assert bool(stops) is lingering
