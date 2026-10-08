"""Process metadata evidence must not turn a partial scan into writer exclusion."""
from pathlib import Path
import subprocess
import sys

import pytest

from tools import demo_maintenance_processes as processes


def process(root, pid=101, uid=1000, *, start=123, fd_target=None, cgroup="/user.slice/test.service"):
    directory = root / str(pid)
    directory.mkdir()
    (directory / "status").write_text(f"Name:\tfixture\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
    # stat fields 3..22; comm deliberately contains spaces and parentheses.
    fields = ["S"] + ["0"] * 18 + [str(start)]
    (directory / "stat").write_text(str(pid) + " (name ) test) " + " ".join(fields))
    (directory / "cgroup").write_text("0::" + cgroup + "\n")
    (directory / "cwd").symlink_to("/unrelated")
    (directory / "exe").symlink_to("/usr/bin/python3")
    (directory / "fd").mkdir()
    (directory / "maps").write_text("")
    if fd_target:
        (directory / "fd" / "4").symlink_to(fd_target)
    # Access to either of these would violate this diagnostic's scope.
    (directory / "cmdline").mkdir()
    (directory / "environ").mkdir()
    return directory


@pytest.fixture
def proc(tmp_path, monkeypatch):
    monkeypatch.setattr(processes, "PROC", tmp_path)
    monkeypatch.setattr(processes.os, "geteuid", lambda: 0)
    return tmp_path


def test_dedicated_uid_blocks_even_without_open_state_files(proc):
    process(proc, uid=1001)
    result = processes.observe(executor_uid=1001, issuer_uid=1002)
    assert result["status"] == "SCOPED_PROCESSES_PRESENT"
    assert result["processes"] == [{"pid": 101, "start_ticks": 123, "reasons": ["DEDICATED_UID"]}]
    assert result["quiescence_proven"] is False


def test_root_manual_open_handle_blocks_and_does_not_disclose_target(proc):
    secret_path = "/var/lib/quantos-demo-controller/private-result.json"
    process(proc, uid=0, fd_target=secret_path)
    result = processes.observe(executor_uid=1001, issuer_uid=1002)
    assert result["processes"][0]["reasons"] == ["STATE_HANDLE"]
    assert secret_path not in str(result)


def test_unknown_demo_service_is_included(proc):
    process(proc, uid=0, cgroup="/system.slice/quantos-demo-executor-abc123.service")
    result = processes.observe(executor_uid=1001, issuer_uid=1002)
    assert result["processes"][0]["reasons"] == ["DEMO_CGROUP"]


def test_empty_scope_never_claims_manual_or_future_root_exclusion(proc):
    process(proc, uid=0)
    result = processes.observe(executor_uid=1001, issuer_uid=1002)
    assert result["status"] == "NO_SCOPED_PROCESS_OBSERVED"
    assert result["quiescence_proven"] is False
    assert result["manual_exclusion_proven"] is False


def test_unreadable_or_malformed_metadata_is_unknown_not_empty(proc):
    directory = process(proc)
    (directory / "status").write_text("Uid:\tinvalid\n")
    result = processes.observe(executor_uid=1001, issuer_uid=1002)
    assert result["status"] == "PROCESS_SCAN_INCOMPLETE"
    assert result["processes"] is None


def test_process_identity_change_invalidates_snapshot(proc, monkeypatch):
    process(proc)
    original = processes._identity
    calls = 0
    def changed(path):
        nonlocal calls
        calls += 1
        pid, ticks = original(path)
        return pid, ticks + (calls > 1)
    monkeypatch.setattr(processes, "_identity", changed)
    assert processes.observe(executor_uid=1001, issuer_uid=1002)["status"] == "PROCESS_SCAN_INCOMPLETE"


def test_no_process_killing_or_arbitrary_uid_exemptions(proc):
    process(proc, uid=1002)
    with pytest.raises(ValueError, match="PROCESS_UIDS"):
        processes.observe(executor_uid=0, issuer_uid=1002)
    assert (proc / "101").exists()


def test_mapped_state_is_reported_when_original_fd_is_closed(proc):
    directory = process(proc)
    (directory / "maps").write_text("1234000-1235000 rw-s 00000000 00:01 123 /var/lib/quantos-demo-executor/ledger.db\n")
    result = processes.observe(executor_uid=1001, issuer_uid=1002)
    assert result["processes"][0]["reasons"] == ["STATE_MAPPING"]


def test_exited_zombie_without_fd_or_exe_does_not_break_scan(proc):
    directory = process(proc)
    (directory / "stat").write_text((directory / "stat").read_text().replace(") S ", ") Z "))
    (directory / "exe").unlink()
    assert processes.observe(executor_uid=1001, issuer_uid=1002)["status"] == "NO_SCOPED_PROCESS_OBSERVED"


def test_fd_directory_permission_failure_cannot_report_empty(proc, monkeypatch):
    process(proc)
    original = processes._names
    def denied(path, maximum):
        if path.name == "fd":
            raise PermissionError("private path must not be exported")
        return original(path, maximum)
    monkeypatch.setattr(processes, "_names", denied)
    result = processes.observe(executor_uid=1001, issuer_uid=1002)
    assert result["status"] == "PROCESS_SCAN_INCOMPLETE"
    assert "private path" not in str(result)


def test_similar_path_prefix_is_not_protected_state(proc):
    process(proc, fd_target="/var/lib/quantos-demo-controller-unrelated/file")
    assert processes.observe(executor_uid=1001, issuer_uid=1002)["status"] == "NO_SCOPED_PROCESS_OBSERVED"


def test_deleted_protected_handle_is_still_reported(proc):
    process(proc, fd_target="/var/lib/quantos-demo-executor/record (deleted)")
    result = processes.observe(executor_uid=1001, issuer_uid=1002)
    assert result["processes"][0]["reasons"] == ["STATE_HANDLE"]


@pytest.mark.skipif(not Path("/proc/self/status").exists(), reason="Linux proc metadata")
def test_actual_child_open_handle_observed_without_reading_file(tmp_path, monkeypatch):
    protected = tmp_path / "private"
    protected.mkdir()
    target = protected / "record.json"
    target.write_bytes(b"never read this content")
    monkeypatch.setattr(processes, "STATE_ROOTS", (str(protected),))
    child = subprocess.Popen([sys.executable, "-c",
        "import sys,time; f=open(sys.argv[1]); print('ready',flush=True); time.sleep(20)", str(target)],
        stdout=subprocess.PIPE, text=True)
    try:
        import select
        assert select.select([child.stdout], [], [], 5)[0]
        assert child.stdout.readline().strip() == "ready"
        row = processes._inspect_process(child.pid, {2147483000, 2147483001})
        assert "STATE_HANDLE" in row["reasons"]
        assert "never read" not in str(row)
    finally:
        child.terminate()
        child.wait(timeout=5)
