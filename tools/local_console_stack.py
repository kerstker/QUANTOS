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
import hashlib
import json
import os
import re
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


def _headless_creationflags() -> int:
    """Prevent helper subprocesses from flashing a console on Windows."""
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _headless_startupinfo() -> subprocess.STARTUPINFO | None:
    """Hide a Windows child even if its executable requests a show state."""
    if os.name != "nt":
        return None
    startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
    use_show_window = getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    hide_window = getattr(subprocess, "SW_HIDE", 0)
    if startupinfo_type is None or not use_show_window:
        raise RuntimeError("Windows hidden-process startup information is unavailable")
    startupinfo = startupinfo_type()
    startupinfo.dwFlags |= use_show_window
    startupinfo.wShowWindow = hide_window
    return startupinfo


def _python_worker() -> str:
    """Use the windowless sibling interpreter for resident Windows APIs."""
    if os.name != "nt":
        return sys.executable
    candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if os.path.isfile(candidate):
        return candidate
    raise RuntimeError("pythonw.exe is unavailable beside the configured Python runtime")


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
    # Keep the documented 3100 first and preserve the established 3105 local
    # console URL before moving to the wider fallback range.
    for port in (3100, 3105, 3110, 3120, 3130, 3140):
        if _port_available(port):
            return port
    raise RuntimeError("no available local frontend port in 3100/3105/3110-3140")


def _source_revision() -> str:
    """Bind the runtime to the exact local source state without claiming clean Git."""
    run_options = {
        "cwd": ROOT,
        "capture_output": True,
        "check": False,
        "creationflags": _headless_creationflags(),
        "timeout": 10,
    }
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            text=True,
            **run_options,
        )
        if head.returncode != 0:
            return "unknown"
        commit = head.stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            return "unknown"
        diff = subprocess.run(
            ["git", "diff", "--binary", "HEAD", "--", "."],
            **run_options,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            **run_options,
        )
        if diff.returncode != 0 or untracked.returncode != 0:
            return "unknown"
        paths = sorted(path for path in untracked.stdout.split(b"\0") if path)
        if not diff.stdout and not paths:
            return commit
        digest = hashlib.sha256()
        digest.update(diff.stdout)
        for encoded_path in paths:
            path = ROOT / encoded_path.decode("utf-8", errors="strict")
            digest.update(encoded_path)
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return f"{commit}+worktree.{digest.hexdigest()[:16]}"
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return "unknown"


def service_specs() -> list[dict[str, Any]]:
    """Return the only processes this helper is allowed to start."""
    frontend_port = _frontend_port()
    frontend_origin = f"http://127.0.0.1:{frontend_port},http://localhost:{frontend_port}"
    python_worker = _python_worker()
    source_revision = _source_revision()
    return [
        {
            "name": "console_api",
            "port": 8400,
            "cwd": ROOT,
            "command": [python_worker, "-m", "uvicorn", "services.console.main:app",
                        "--host", "127.0.0.1", "--port", "8400"],
            "health": "http://127.0.0.1:8400/openapi.json",
            "cors_probe": "http://127.0.0.1:8400/health",
            "cors_origins": [f"http://127.0.0.1:{frontend_port}", f"http://localhost:{frontend_port}"],
            "env": {
                "QUANTOS_CONSOLE_CORS_ORIGINS": frontend_origin,
                "QUANTOS_BUILD_SHA": source_revision,
            },
        },
        {
            "name": "research_runner",
            "port": 8420,
            "cwd": ROOT,
            "command": [python_worker, "-m", "uvicorn", "services.research_runner.main:app",
                        "--host", "127.0.0.1", "--port", "8420"],
            "health": "http://127.0.0.1:8420/health",
            "env": {"QUANTOS_RUNNER_CORS_ORIGINS": frontend_origin},
        },
        {
            "name": "frontend",
            "port": frontend_port,
            "cwd": ROOT,
            # Serve the built SPA through pythonw.  Node/Vite is a build-time
            # IDE tool only; a resident node.exe can allocate conhost.exe even
            # when its parent requested CREATE_NO_WINDOW.
            "command": [
                python_worker,
                "-m",
                "tools.local_frontend_server",
                "--host",
                "127.0.0.1",
                "--port",
                str(frontend_port),
            ],
            "health": f"http://127.0.0.1:{frontend_port}/",
            "env": {},
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


def _listening_pid(port: int) -> int | None:
    """Return the process that owns a local TCP listener, when discoverable.

    ``npm.cmd`` and some Windows Python launchers can exit after handing the
    actual service to a child.  Tracking only ``Popen.pid`` therefore creates
    false ``running=false`` states and leaves an orphan listener on stop.  The
    listener PID is the resource we must own and reclaim.
    """
    if os.name == "nt":
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=_headless_creationflags(),
        )
        target = f":{int(port)}"
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 5 or fields[0].upper() != "TCP":
                continue
            if fields[1].endswith(target) and fields[3].upper() == "LISTENING":
                try:
                    pid = int(fields[4])
                except ValueError:
                    continue
                return pid if pid > 0 else None
        return None
    # Linux/macOS runners normally provide lsof.  If not, retaining the
    # launcher PID remains the safest fail-closed fallback.
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    for line in result.stdout.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid > 0:
            return pid
    return None


def _pid_alive(pid: Any) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        # ``os.kill(pid, 0)`` is not a validity probe on Windows (it returns
        # WinError 87 for both live and dead PIDs).  Ask the kernel for the
        # process exit code instead so Node/Vite children are tracked too.
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            try:
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return int(exit_code.value) == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except (AttributeError, OSError, ctypes.error):
            # Fall through to the portable probe for unusual Windows hosts.
            pass
    try:
        os.kill(pid, 0)
    except PermissionError:
        # Windows may deny the zero-signal probe for a live process owned by
        # another token (notably the Node/Vite child).  Permission denied is
        # evidence that the PID exists, not evidence that it is dead.
        return True
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


def _wait_cors(url: str, origin: str, *, timeout: float = 30.0) -> None:
    """Fail closed unless the API explicitly echoes the managed frontend origin."""
    deadline = time.monotonic() + timeout
    last_error = "not attempted"
    while time.monotonic() < deadline:
        request = urllib.request.Request(url, headers={"Origin": origin})
        try:
            with urllib.request.urlopen(request, timeout=1.0) as response:
                allowed = response.headers.get("Access-Control-Allow-Origin")
                if allowed == origin:
                    return
                last_error = f"Access-Control-Allow-Origin={allowed!r}"
        except (OSError, urllib.error.URLError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.25)
    raise RuntimeError(f"CORS handshake failed for {origin} via {url}: {last_error}")


def _kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=_headless_creationflags(),
        )
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
        pids = []
        for key in ("pid", "launcher_pid"):
            try:
                candidate = int(service.get(key))
            except (TypeError, ValueError):
                continue
            if candidate > 0 and candidate not in pids:
                pids.append(candidate)
        service_stopped = []
        for pid in pids:
            if _pid_alive(pid):
                _kill(pid)
                service_stopped.append(pid)
        if service_stopped:
            stopped.append({"name": service.get("name"), "pids": service_stopped})
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
            "launcher_pid": service.get("launcher_pid"),
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
    flags = _headless_creationflags()
    startupinfo = _headless_startupinfo()
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
                    startupinfo=startupinfo,
                )
            finally:
                log.close()
            record = {"name": spec["name"], "pid": process.pid, "launcher_pid": process.pid,
                      "port": spec["port"], "health": spec["health"], "log": str(log_path)}
            started.append(record)
            _write_json(MANIFEST, {"schema_version": 1, "started_at": int(time.time()),
                                   "services": started})
            _wait_health(spec["health"])
            cors_probe = spec.get("cors_probe")
            cors_origins = spec.get("cors_origins", [])
            if isinstance(cors_probe, str) and isinstance(cors_origins, list):
                for origin in cors_origins:
                    if isinstance(origin, str) and origin:
                        _wait_cors(cors_probe, origin)
            listener_pid = _listening_pid(int(spec["port"]))
            if listener_pid is not None:
                record["pid"] = listener_pid
                _write_json(MANIFEST, {"schema_version": 1, "started_at": int(time.time()),
                                       "services": started})
    except BaseException:
        for service in started:
            for key in ("pid", "launcher_pid"):
                if _pid_alive(service.get(key)):
                    _kill(int(service[key]))
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
