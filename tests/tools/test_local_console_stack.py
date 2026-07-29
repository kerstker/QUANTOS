from __future__ import annotations

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
    assert all("live" not in str(part).lower() for part in commands)
    assert specs[0]["cors_probe"].endswith("/health")
    assert set(specs[0]["cors_origins"]) == {
        f"http://127.0.0.1:{specs[2]['port']}",
        f"http://localhost:{specs[2]['port']}",
    }


def test_frontend_port_override_is_explicit_and_fail_closed(monkeypatch):
    monkeypatch.setenv("QUANTOS_FRONTEND_PORT", "3111")
    monkeypatch.setattr(stack, "_port_available", lambda port: True)
    specs = service_specs()
    assert specs[2]["port"] == 3111
    assert specs[2]["env"]["VITE_API_URL"].endswith(":8400")
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


def test_pid_alive_treats_windows_permission_denied_as_alive(monkeypatch):
    def denied(*args):
        raise PermissionError("access denied")

    monkeypatch.setattr(stack.os, "name", "posix")
    monkeypatch.setattr(stack.os, "kill", denied)
    assert stack._pid_alive(4321) is True
