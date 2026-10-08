"""Explicit ARM64 development preflight and isolated execution/risk test entry.

This diagnostic entry is not the canonical release gate. Run with the absolute
path to the development venv's Python; no activation or shell startup is needed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
LOCK = "requirements-ci-linux-aarch64.lock"


def check_runtime(machine: str, actual_python: str, expected_python: str) -> None:
    if machine != "aarch64":
        raise ValueError("DGX development requires aarch64")
    if actual_python != expected_python:
        raise ValueError(f"Python {expected_python} required; found {actual_python}")


def check_dependencies(lock: str, installed=importlib.metadata.version) -> dict[str, str]:
    packages = re.findall(r"^# package: ([^=]+)==(.+)$", lock, re.MULTILINE)
    if not packages:
        raise ValueError("dependency lock is empty")
    actual, errors = {}, []
    for name, expected in packages:
        try:
            version = installed(name)
        except importlib.metadata.PackageNotFoundError:
            version = "MISSING"
        actual[name] = version
        if version != expected:
            errors.append(f"{name}: expected {expected}, found {version}")
    if errors:
        raise ValueError("dependency mismatch: " + "; ".join(errors))
    return actual


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(REPO), *args], stdin=subprocess.DEVNULL)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--node", required=True, type=Path, help="absolute Node executable")
    parser.add_argument("--test", action="store_true", help="run execution/risk and preflight tests")
    args = parser.parse_args(argv)
    head = git("rev-parse", "HEAD").decode().strip()
    if args.expected_head != head:
        parser.error(f"source changed: expected {args.expected_head}, found {head}")
    check_runtime(platform.machine(), f"{sys.version_info.major}.{sys.version_info.minor}",
                  (REPO / ".python-version").read_text().strip())
    lock = (REPO / LOCK).read_text()
    packages = check_dependencies(lock)
    for name, expected in re.findall(r"^# source-sha256: ([^=]+)=(\w+)$", lock, re.MULTILINE):
        if hashlib.sha256((REPO / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"stale dependency lock: {name}")
    if not args.node.is_absolute() or not args.node.is_file():
        parser.error("--node must be an existing absolute executable path")
    node_version = subprocess.check_output([str(args.node), "--version"],
                                           stdin=subprocess.DEVNULL, text=True).strip()
    # A newly allocated directory prevents pytest --basetemp from erasing an
    # existing caller-selected state directory. Never write /srv/quantos/state.
    output = Path(tempfile.mkdtemp(prefix="quantos-dgx-development-", dir="/tmp"))
    source_names = git("ls-files", "--cached", "--others", "--exclude-standard", "--",
                       "services", "quantos", "scripts", "tests").decode().splitlines()
    source_hashes = {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
                     for name in sorted(set(source_names))
                     if name.endswith(".py") and (REPO / name).is_file()}
    manifest = {
        "kind": "DGX_DEVELOPMENT_DIAGNOSTIC", "authoritative": False,
        "repo": str(REPO), "commit": head,
        "branch": git("branch", "--show-current").decode().strip(),
        "working_tree_status": git("status", "--short").decode(),
        "source_tree_sha256": hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest(),
        "architecture": platform.machine(), "python": sys.executable,
        "python_version": platform.python_version(), "node": str(args.node), "node_version": node_version,
        "lock_sha256": hashlib.sha256((REPO / LOCK).read_bytes()).hexdigest(),
        "installed_versions": packages, "output_root": str(output),
        "scope": "execution/risk offline fixtures only", "tests_returncode": None,
    }
    report = output / "baseline.json"
    report.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Development baseline: {report}", flush=True)
    if not args.test:
        return 0
    env = {"PATH": f"{Path(sys.executable).parent}:{args.node.parent}:/usr/bin:/bin",
           "HOME": os.environ["HOME"], "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
           "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
           "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "TMPDIR": str(output),
           "XDG_CACHE_HOME": str(output / "cache")}
    command = [sys.executable, "-m", "pytest", "-q", "-p", "pytest_asyncio.plugin",
               "-o", f"cache_dir={output / 'pytest-cache'}", "--basetemp", str(output / "fixtures"),
               "--junitxml", str(output / "tests.xml"), "tests/execution", "tests/risk",
               "tests/tools/test_dgx_develop.py"]
    with (output / "tests.log").open("w") as log:
        completed = subprocess.run(command, cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, timeout=600)
    manifest["tests_returncode"] = completed.returncode
    manifest["command"] = command
    report.write_text(json.dumps(manifest, indent=2) + "\n")
    print((output / "tests.log").read_text(), end="")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
