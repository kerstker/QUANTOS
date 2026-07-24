"""Start, inspect, and stop the local QUANTOS research console stack.

This operational helper is intentionally limited to the three L2 surfaces:

* read-only console API on ``127.0.0.1:8400``;
* guarded research runner on ``127.0.0.1:8420``;
* the React/Vite research console on ``127.0.0.1:3100``.

It does not start ``live/``, promotion, broker, schedule, or credential paths.
Each child is recorded in ``.runtime/console-stack/manifest.json`` and its log is
written below the same ignored runtime directory so ``stop`` can terminate only
processes started by this helper.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime" / "console-stack"
MANIFEST = RUNTIME / "manifest.json"
LOGS = RUNTIME / "logs"


def _npm() -> str:
    candidate = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if candidate:
        return candidate
    raise RuntimeError("npm is not available on PATH; install Node.js before starting the frontend")


def _port_available(port: int) -> bool:
    """Return whether the current user can bind the local port.

    Windows may reserve a port range even when no process is listening.  A
    connect-only check cannot see that case, so the launcher probes a bind and
    falls back to the next documented local port.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", int(port)))
        except OSError:
            return False
    return True


def _frontend_port() -> int:
    requested = os.getenv("QUANTOS_FRONTEND_PORT", "").strip()
    if requested:
        try:
            port = int(requested)
        except ValueError as exc:
            raise RuntimeError("QUANTOS_FRONTEND_PORT must be an integer") from exc
        if not (1024 <= port <= 65535) or not _port_available(port):
            raise RuntimeError(f"requested frontend port is unavailable: {port}")
        return port
    # Keep the documented 3100 first; this Windows host currently excludes
    # 3005-3104, so the helper will select 3110 without changing project code.
    for port in (3100, 3110, 3120, 3130, 3140):
        if _port_available(port):
            return port
    raise RuntimeError("no available local frontend port in 3100/3110-3140")


def service_specs() -> list[dict[str, Any]]:
    """Return the only processes this helper is allowed to start."""
    frontend_port = _frontend_port()
    frontend_origin = f"http://127.0.0.1:{frontend_port},http://localhost:{frontend_port}"
    return [
        {
            "name": "console_api",
            "port": 8400,
            "cwd": ROOT,
            "command": [sys.executable, "-m", "uvicorn", "services.console.main:app",
                        "--host", "127.0.0.1", "--port", "8400"],
            "health": "http://127.0.0.1:8400/openapi.json",
            "env": {"QUANTOS_CONSOLE_CORS_ORIGINS": frontend_origin},
        },
        {
            "name": "research_runner",
            "port": 8420,
            "cwd": ROOT,
            "command": [sys.executable, "-m", "uvicorn", "services.research_runner.main:app",
                        "--host", "127.0.0.1", "--port", "8420"],
            "health": "http://127.0.0.1:8420/health",
            "env": {"QUANTOS_RUNNER_CORS_ORIGINS": frontend_origin},
        },
        {
            "name": "frontend",
            "port": frontend_port,
            "cwd": ROOT / "apps" / "research-console",
            "command": [_npm(), "run", "dev", "--", "--host", "127.0.0.1", "--port", str(frontend_port)],
            "health": f"http://127.0.0.1:{frontend_port}/",
            "env": {
                "VITE_API_URL": "http://127.0.0.1:8400",
                "VITE_RUNNER_URL": "http://127.0.0.1:8420",
                "VITE_MOCK_MODE": "false",
            },
        },
    ]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _read_manifest() -> dict[str, Any]:
    try:
        value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.15)
        return sock.connect_ex(("127.0.0.1", int(port))) == 0


def _pid_alive(pid: Any) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _wait_health(url: str, *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if 200 <= int(response.status) < 500:
                    return
                last_error = f"HTTP {response.status}"
        except (OSError, urllib.error.URLError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.25)
    raise RuntimeError(f"health check failed for {url}: {last_error}")


def _kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass


def stop() -> dict[str, Any]:
    manifest = _read_manifest()
    stopped = []
    for service in manifest.get("services", []) if isinstance(manifest.get("services"), list) else []:
        if not isinstance(service, dict):
            continue
        pid = service.get("pid")
        if _pid_alive(pid):
            _kill(int(pid))
            stopped.append({"name": service.get("name"), "pid": int(pid)})
    if MANIFEST.exists():
        MANIFEST.unlink()
    return {"stopped": stopped, "manifest": str(MANIFEST)}


def status() -> dict[str, Any]:
    manifest = _read_manifest()
    rows = []
    for service in manifest.get("services", []) if isinstance(manifest.get("services"), list) else []:
        if not isinstance(service, dict):
            continue
        port = service.get("port")
        rows.append({
            "name": service.get("name"),
            "pid": service.get("pid"),
            "port": port,
            "pid_alive": _pid_alive(service.get("pid")),
            "port_listening": _port_busy(int(port)) if isinstance(port, int) else False,
            "log": service.get("log"),
        })
    return {"running": bool(rows) and all(row["pid_alive"] and row["port_listening"] for row in rows),
            "services": rows, "manifest": str(MANIFEST)}


def start() -> dict[str, Any]:
    current = status()
    if current.get("running"):
        return current
    if current.get("services"):
        stop()
    specs = service_specs()
    busy = [spec["port"] for spec in specs if _port_busy(spec["port"])]
    if busy:
        raise RuntimeError(f"refusing to start: local ports already in use: {busy}")
    LOGS.mkdir(parents=True, exist_ok=True)
    started: list[dict[str, Any]] = []
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        for spec in specs:
            env = os.environ.copy()
            env.update(spec["env"])
            log_path = LOGS / f"{spec['name']}.log"
            log = log_path.open("a", encoding="utf-8", buffering=1)
            try:
                process = subprocess.Popen(
                    spec["command"],
                    cwd=str(spec["cwd"]),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=flags,
                )
            finally:
                log.close()
            record = {"name": spec["name"], "pid": process.pid, "port": spec["port"],
                      "health": spec["health"], "log": str(log_path)}
            started.append(record)
            _write_json(MANIFEST, {"schema_version": 1, "started_at": int(time.time()),
                                   "services": started})
            _wait_health(spec["health"])
    except BaseException:
        for service in started:
            if _pid_alive(service.get("pid")):
                _kill(int(service["pid"]))
        if MANIFEST.exists():
            MANIFEST.unlink()
        raise
    return status()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "status", "stop"))
    args = parser.parse_args(argv)
    result = {"start": start, "status": status, "stop": stop}[args.action]()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
