from __future__ import annotations

from pathlib import Path

import tools.local_console_stack as stack
from tools.local_console_stack import service_specs


def test_local_stack_is_l2_console_only(monkeypatch):
    monkeypatch.delenv("QUANTOS_FRONTEND_PORT", raising=False)
    specs = service_specs()
    assert {spec["name"] for spec in specs} == {"console_api", "research_runner", "frontend"}
    assert {spec["port"] for spec in specs[:2]} == {8400, 8420}
    assert specs[2]["port"] >= 3100
    commands = [part for spec in specs for part in spec["command"]]
    assert "services.console.main:app" in commands
    assert "services.research_runner.main:app" in commands
    if stack.os.name == "nt":
        assert Path(specs[0]["command"][0]).name.lower() == "pythonw.exe"
        assert Path(specs[1]["command"][0]).name.lower() == "pythonw.exe"
    assert all("live" not in str(part).lower() for part in commands)
    frontend_command = [str(part).lower() for part in specs[2]["command"]]
    assert frontend_command[0].endswith(("python", "python3", "python.exe", "pythonw.exe"))
    assert "tools.local_frontend_server" in frontend_command
    assert all("node" not in part and "npm" not in part and "cmd.exe" not in part for part in frontend_command)
    assert specs[0]["cors_probe"].endswith("/health")
    assert set(specs[0]["cors_origins"]) == {
        f"http://127.0.0.1:{specs[2]['port']}",
        f"http://localhost:{specs[2]['port']}",
    }
    assert specs[0]["env"]["QUANTOS_BUILD_SHA"] != "dirty"


def test_source_revision_distinguishes_clean_and_dirty_worktrees(monkeypatch):
    class Completed:
        def __init__(self, stdout=b"", returncode=0):
            self.stdout = stdout
            self.returncode = returncode

    clean = [
        Completed("a" * 40 + "\n"),
        Completed(),
        Completed(),
    ]
    monkeypatch.setattr(stack.subprocess, "run", lambda *args, **kwargs: clean.pop(0))
    assert stack._source_revision() == "a" * 40

    dirty = [
        Completed("a" * 40 + "\n"),
        Completed(b"diff --git a/x b/x\n"),
        Completed(),
    ]
    monkeypatch.setattr(stack.subprocess, "run", lambda *args, **kwargs: dirty.pop(0))
    revision = stack._source_revision()
    assert revision.startswith("a" * 40 + "+worktree.")
    assert len(revision.rsplit(".", 1)[1]) == 16


def test_frontend_port_override_is_explicit_and_fail_closed(monkeypatch):
    monkeypatch.setenv("QUANTOS_FRONTEND_PORT", "3111")
    monkeypatch.setattr(stack, "_port_available", lambda port: True)
    specs = service_specs()
    assert specs[2]["port"] == 3111
    assert specs[2]["command"][-1] == "3111"
    assert "3111" in specs[0]["env"]["QUANTOS_CONSOLE_CORS_ORIGINS"]


def test_listener_pid_parser_tracks_the_real_child(monkeypatch):
    class Completed:
        stdout = "TCP    127.0.0.1:3110    0.0.0.0:0    LISTENING    4321\n"

    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return Completed()

    monkeypatch.setattr(stack.subprocess, "run", run)
    monkeypatch.setattr(stack.os, "name", "nt")
    monkeypatch.setattr(stack.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert stack._listening_pid(3110) == 4321
    assert calls[0][1]["creationflags"] == 0x08000000


def test_windows_kill_is_headless(monkeypatch):
    calls = []

    monkeypatch.setattr(
        stack.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(stack.os, "name", "nt")
    monkeypatch.setattr(stack.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    stack._kill(4321)

    assert calls[0][0][0] == ["taskkill", "/PID", "4321", "/T", "/F"]
    assert calls[0][1]["creationflags"] == 0x08000000


def test_windows_startupinfo_forces_hidden_window(monkeypatch):
    class StartupInfo:
        def __init__(self):
            self.dwFlags = 0
            self.wShowWindow = None

    monkeypatch.setattr(stack.os, "name", "nt")
    monkeypatch.setattr(stack.subprocess, "STARTUPINFO", StartupInfo, raising=False)
    monkeypatch.setattr(stack.subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(stack.subprocess, "SW_HIDE", 0, raising=False)

    startupinfo = stack._headless_startupinfo()

    assert startupinfo is not None
    assert startupinfo.dwFlags & 1
    assert startupinfo.wShowWindow == 0


def test_windows_python_worker_uses_pythonw_sibling(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    pythonw = tmp_path / "pythonw.exe"
    python.write_bytes(b"")
    pythonw.write_bytes(b"")
    monkeypatch.setattr(stack.os, "name", "nt")
    monkeypatch.setattr(stack.sys, "executable", str(python))

    assert stack._python_worker() == str(pythonw)


def test_pid_alive_treats_windows_permission_denied_as_alive(monkeypatch):
    def denied(*args):
        raise PermissionError("access denied")

    monkeypatch.setattr(stack.os, "name", "posix")
    monkeypatch.setattr(stack.os, "kill", denied)
    assert stack._pid_alive(4321) is True
