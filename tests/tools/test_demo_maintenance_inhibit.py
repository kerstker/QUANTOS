"""A maintenance start inhibitor owns only its exact runtime drop-ins."""
import os
from pathlib import Path

import pytest

from tools import demo_maintenance_inhibit as inhibit


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    # Model root-owned runtime ancestors without changing host ownership or /run.
    original_lstat = Path.lstat
    def metadata(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if path in tmp_path.parents:
            values = list(info)
            values[0] = 0o40755
            values[4] = os.getuid()
            return os.stat_result(values)
        return info
    monkeypatch.setattr(Path, "lstat", metadata)
    root = tmp_path / "system"
    root.mkdir(mode=0o755)
    monkeypatch.setattr(inhibit, "SYSTEM", root)
    monkeypatch.setattr(inhibit, "CONTROL", tmp_path / "control")
    monkeypatch.setattr(inhibit.os, "geteuid", lambda: os.getuid())
    monkeypatch.setattr(inhibit, "OPERATOR_UID", os.getuid())
    return root


def test_install_is_resumable_and_removal_preserves_other_dropins(runtime):
    token = "a" * 64
    assert inhibit.install(token)["activation_verified"] is False
    assert inhibit.install(token)["files"] == len(inhibit.UNITS)
    extra = runtime / (inhibit.UNITS[0] + ".d") / "operator.conf"
    extra.write_text("[Unit]\nDescription=operator\n")
    assert inhibit.remove(token)["status"] == "DROPINS_REMOVED_RELOAD_REQUIRED"
    assert extra.exists()
    inhibit.remove(token)


def test_different_operation_cannot_remove_or_take_over(runtime):
    inhibit.install("a" * 64)
    with pytest.raises(ValueError, match="INHIBITOR_CONFLICT"):
        inhibit.install("b" * 64)
    with pytest.raises(ValueError, match="INHIBITOR_CONFLICT"):
        inhibit.remove("b" * 64)
    assert len(list(runtime.glob("*/*.conf"))) == len(inhibit.UNITS)


def test_changed_file_stops_removal_before_any_unlink(runtime):
    token = "a" * 64
    inhibit.install(token)
    target = runtime / (inhibit.UNITS[-1] + ".d") / inhibit.NAME
    target.write_text("[Unit]\nDescription=changed\n")
    with pytest.raises(ValueError, match="INHIBITOR_CONFLICT"):
        inhibit.remove(token)
    assert len(list(runtime.glob("*/*.conf"))) == len(inhibit.UNITS)


def test_partial_install_resumes_without_overwriting(runtime, monkeypatch):
    original = inhibit._publish
    count = 0

    def fail(path, raw):
        nonlocal count
        count += 1
        if count == 3:
            raise OSError("interrupted")
        original(path, raw)

    monkeypatch.setattr(inhibit, "_publish", fail)
    with pytest.raises(OSError):
        inhibit.install("a" * 64)
    assert len(list(runtime.glob("*/*.conf"))) == 2
    monkeypatch.setattr(inhibit, "_publish", original)
    inhibit.install("a" * 64)
    assert len(list(runtime.glob("*/*.conf"))) == len(inhibit.UNITS)


def test_allow_path_or_symlink_cannot_accidentally_enable_start(runtime):
    inhibit.CONTROL.mkdir(mode=0o700)
    (inhibit.CONTROL / "allow-start").symlink_to("missing")
    with pytest.raises(ValueError, match="INHIBITOR_ALLOW_PATH"):
        inhibit.install("a" * 64)
    assert not list(runtime.glob("*/*.conf"))


def test_symlink_dropin_directory_rejected(runtime, tmp_path):
    (runtime / (inhibit.UNITS[-1] + ".d")).symlink_to(tmp_path)
    with pytest.raises(ValueError, match="INHIBITOR_DIRECTORY"):
        inhibit.install("a" * 64)
    assert not list(runtime.glob("*/*.conf"))


def test_no_elevation_or_custom_unit_scope(runtime, monkeypatch):
    monkeypatch.setattr(inhibit.os, "geteuid", lambda: inhibit.OPERATOR_UID + 1)
    with pytest.raises(ValueError, match="OPERATOR_ROOT_REQUIRED"):
        inhibit.install("a" * 64)
    assert not list(runtime.iterdir())


def test_payload_adds_conjunctive_condition_without_resetting_existing_conditions(runtime):
    inhibit.install("a" * 64)
    raw = (runtime / (inhibit.UNITS[0] + ".d") / inhibit.NAME).read_text()
    assert "ConditionPathExists=" + str(inhibit.CONTROL / "allow-start") + "\n" in raw
    assert "ConditionPathExists=\n" not in raw
    assert "ExecStart" not in raw


@pytest.mark.skipif(not Path("/usr/bin/systemd-analyze").exists(), reason="requires systemd condition evaluator")
def test_systemd_evaluates_the_actual_installed_condition_as_false(runtime):
    import subprocess

    inhibit.install("a" * 64)
    raw = (runtime / (inhibit.UNITS[0] + ".d") / inhibit.NAME).read_text()
    condition = next(line for line in raw.splitlines() if line.startswith("ConditionPathExists="))
    result = subprocess.run(["/usr/bin/systemd-analyze", "condition", condition], capture_output=True)
    assert result.returncode == 1, result.stderr.decode()
    # A condition evaluation failure must not be confused with invalid syntax.
    (inhibit.CONTROL / "allow-start").touch()
    result = subprocess.run(["/usr/bin/systemd-analyze", "condition", condition], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


def test_lock_contention_prevents_competing_install_or_remove(runtime):
    with inhibit._locked():
        with pytest.raises(BlockingIOError):
            inhibit.install("a" * 64)
        with pytest.raises(BlockingIOError):
            inhibit.remove("a" * 64)
    assert not list(runtime.glob("*/*.conf"))


def test_nonempty_foreign_dropin_is_not_overwritten(runtime):
    parent = runtime / (inhibit.UNITS[-1] + ".d")
    parent.mkdir(mode=0o755)
    target = parent / inhibit.NAME
    target.write_text("operator-owned configuration")
    with pytest.raises(ValueError, match="INHIBITOR_CONFLICT"):
        inhibit.install("a" * 64)
    assert target.read_text() == "operator-owned configuration"
    assert len(list(runtime.glob("*/*.conf"))) == 1


@pytest.mark.parametrize("trigger,negate", [(False, False), (True, False), (False, True)])
def test_effective_condition_requires_non_trigger_non_negated_gate(runtime, monkeypatch, trigger, negate):
    import json
    from types import SimpleNamespace

    inhibit.install("a" * 64)
    replies = iter([
        {"type": "a(sbbsi)", "data": [["ConditionPathExists", trigger, negate,
                                        str(inhibit.CONTROL / "allow-start"), 0]]},
    ])
    monkeypatch.setattr(inhibit.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(stdout=json.dumps(next(replies)).encode()))
    if trigger or negate:
        with pytest.raises(ValueError, match="INHIBITOR_NOT_EFFECTIVE"):
            inhibit.verify_loaded(inhibit.UNITS[0], "a" * 64)
    else:
        inhibit.verify_loaded(inhibit.UNITS[0], "a" * 64)


def test_existing_file_without_effective_condition_is_rejected(runtime, monkeypatch):
    import json
    from types import SimpleNamespace

    inhibit.install("a" * 64)
    replies = iter([
        {"type": "a(sbbsi)", "data": []},
    ])
    monkeypatch.setattr(inhibit.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(stdout=json.dumps(next(replies)).encode()))
    with pytest.raises(ValueError, match="INHIBITOR_NOT_EFFECTIVE"):
        inhibit.verify_loaded(inhibit.UNITS[0], "a" * 64)


def test_effective_condition_reads_unit_object_without_getunit_race(runtime, monkeypatch):
    import json
    from types import SimpleNamespace

    inhibit.install("a" * 64)
    calls = []
    unit = "quantos-demo-executor-origin.timer"
    path = "/org/freedesktop/systemd1/unit/quantos_2ddemo_2dexecutor_2dorigin_2etimer"

    def query(args, **kwargs):
        calls.append(args)
        assert args == ["/usr/bin/busctl", "--system", "--json=short", "get-property",
                        "org.freedesktop.systemd1", path,
                        "org.freedesktop.systemd1.Unit", "Conditions"]
        assert kwargs["check"] is True
        return SimpleNamespace(stdout=json.dumps({
            "type": "a(sbbsi)", "data": [
                ["ConditionPathExists", False, False, str(inhibit.CONTROL / "allow-start"), 0]
            ]}).encode())

    monkeypatch.setattr(inhibit.subprocess, "run", query)
    inhibit.verify_loaded(unit, "a" * 64)
    assert len(calls) == 1


def test_optional_feedback_pair_gets_same_effective_inhibitor(runtime):
    from tools.demo_maintenance_services import ALLOWED_UNITS,FEEDBACK_UNITS
    token='b'*64
    result=inhibit.install(token,units=ALLOWED_UNITS)
    assert result['files']==len(ALLOWED_UNITS)
    for unit in FEEDBACK_UNITS:
        assert inhibit.dropin(unit).read_bytes()==inhibit._payload(token)
    inhibit.remove(token,units=ALLOWED_UNITS)
    assert all(not inhibit.dropin(unit).exists() for unit in ALLOWED_UNITS)
