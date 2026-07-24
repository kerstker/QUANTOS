from __future__ import annotations

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


def test_frontend_port_override_is_explicit_and_fail_closed(monkeypatch):
    monkeypatch.setenv("QUANTOS_FRONTEND_PORT", "3111")
    specs = service_specs()
    assert specs[2]["port"] == 3111
    assert specs[2]["env"]["VITE_API_URL"].endswith(":8400")
    assert "3111" in specs[0]["env"]["QUANTOS_CONSOLE_CORS_ORIGINS"]
