"""Bounded root-to-executor systemd handoff; never restores services or issues grants."""

import hashlib
import os
from pathlib import Path
import re
import select
import subprocess
import time

from live.execution.demo_authority import _read_root_owned_json, _REGISTRY_PATH
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from services.validation.demo_submission import validate_registry, registry_sha256
from tools import demo_executor_retention_grant as grants
from tools.demo_execution_session import read_frame
from tools.demo_retention_transaction import SCOPES, require
from tools.demo_retention_worker import SCHEMA, REASONS

CGROUP = Path("/sys/fs/cgroup")


def _unit(unit):
    require(type(unit) is str and re.fullmatch(r"quantos-demo-retention-[a-f0-9]{32}\.service", unit), "WORKER_UNIT")


def description(digest):
    require(grants.digest(digest), "WORKER_GRANT_DIGEST")
    return "QUANTOS retention grant " + digest


def command(*, source_commit, uid, action, unit, grant_sha256):
    _unit(unit)
    require(type(source_commit) is str and re.fullmatch("[a-f0-9]{40}", source_commit), "WORKER_SOURCE")
    require(type(uid) is int and 0 < uid < 2**31 and action in {"PREPARE", "APPLY", "ROLLBACK"}, "WORKER_ROLE")
    source = "/opt/quantos/releases/" + source_commit
    venv = "/opt/quantos/venvs/" + source_commit
    properties = {
        "User": str(uid),
        "Group": str(uid),
        "SupplementaryGroups": "",
        "UMask": "0077",
        "Description": description(grant_sha256),
        "Slice": "system.slice",
        "NoNewPrivileges": "yes",
        "PrivateNetwork": "yes",
        "RestrictAddressFamilies": "AF_UNIX",
        "ProtectSystem": "strict",
        "ProtectHome": "yes",
        "PrivateDevices": "yes",
        "ProtectKernelTunables": "yes",
        "ProtectKernelModules": "yes",
        "ProtectKernelLogs": "yes",
        "ProtectControlGroups": "yes",
        "ProtectClock": "yes",
        "ProtectHostname": "yes",
        "RestrictSUIDSGID": "yes",
        "LockPersonality": "yes",
        "RestrictRealtime": "yes",
        "RestrictNamespaces": "yes",
        "CapabilityBoundingSet": "",
        "AmbientCapabilities": "",
        "SystemCallArchitectures": "native",
        "WorkingDirectory": "/var/lib/quantos-demo-executor",
        "KillMode": "control-group",
        "RuntimeMaxSec": "600s",
        "TimeoutStopSec": "10s",
        "MemoryMax": "4G",
        "TasksMax": "32",
        "TemporaryFileSystem": "/etc/quantos:ro,mode=0755",
        "BindReadOnlyPaths": "/etc/quantos/demo-submission/authority.json",
        "ReadOnlyPaths": source + " /var/lib/quantos-demo-archives/requests",
        "ReadWritePaths": " ".join("/var/lib/quantos-demo-executor/" + name for name in sorted(SCOPES))
        + " /var/lib/quantos-demo-archives",
        "InaccessiblePaths": "-/var/lib/quantos-demo-issuer -/var/lib/quantos-demo-controller",
        "StandardError": "null",
    }
    return [
        "/usr/bin/systemd-run",
        "--quiet",
        "--wait",
        "--pipe",
        "--collect",
        "--no-ask-password",
        "--unit=" + unit,
        *["--property=" + key + "=" + value for key, value in properties.items()],
        "--",
        "/usr/bin/env",
        "-i",
        "PATH=" + venv + "/bin:/usr/bin:/bin",
        "LANG=C.UTF-8",
        "PYTHONPATH=" + source,
        "PYTHONNOUSERSITE=1",
        "PYTHONDONTWRITEBYTECODE=1",
        "OPENBLAS_NUM_THREADS=1",
        "OMP_NUM_THREADS=1",
        "LIVE_TRADING_ENABLED=false",
        "REAL_BROKER_ENABLED=false",
        "ALLOW_ORDER_SUBMISSION=false",
        "ALLOW_AI_DIRECT_TRADING=false",
        "REQUIRE_HUMAN_APPROVAL=true",
        venv + "/bin/python",
        "-P",
        "-m",
        "tools.demo_retention_worker",
        "--action",
        action,
    ]


def show(unit):
    _unit(unit)
    result = subprocess.run(
        ["/usr/bin/systemctl", "show", unit, "--no-pager", "--property=LoadState,ActiveState,ControlGroup,Description"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def drained(unit):
    before = show(unit)
    if before.get("LoadState") not in {"loaded", "not-found"} or before.get("ActiveState") not in {
        "inactive",
        "failed",
    }:
        return False
    expected = "/system.slice/" + unit
    require(before.get("ControlGroup", "") in {"", expected}, "WORKER_CGROUP")
    path = CGROUP / "system.slice" / unit / "cgroup.events"
    require(not any(p.is_symlink() for p in (path, *path.parents)), "WORKER_CGROUP")
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            raw = stream.read(4097)
        rows = [line.split() for line in raw.decode("ascii").splitlines()]
        require(
            len(raw) <= 4096 and all(len(row) == 2 for row in rows) and len(rows) == len({row[0] for row in rows}),
            "WORKER_CGROUP",
        )
        require(dict(rows).get("populated") in {"0", "1"}, "WORKER_CGROUP")
        if dict(rows)["populated"] != "0":
            return False
    except FileNotFoundError:
        # A removed cgroup is fine; an existing directory with unreadable/missing
        # events is not evidence of an empty group.
        require(not path.parent.exists(), "WORKER_CGROUP")
    return show(unit) == before


def stop_owned(unit, digest):
    value = show(unit)
    if value.get("LoadState") == "not-found":
        return
    require(value.get("Description") == description(digest), "WORKER_OWNERSHIP")
    require(os.geteuid() == 0, "OPERATOR_ROOT_REQUIRED")
    subprocess.run(
        ["/usr/bin/systemctl", "stop", unit],
        check=True,
        timeout=20,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _preflight(fd, action, unit):
    require(os.getresuid() == (0, 0, 0), "OPERATOR_ROOT_REQUIRED")
    doc, raw = grants.read_descriptor(fd, action)
    require(
        (doc["controller_pid"], doc["controller_start_ticks"]) == grants._identity(Path("/proc") / str(os.getpid())),
        "WORKER_CONTROLLER",
    )
    registry = validate_registry(decode(_read_root_owned_json(_REGISTRY_PATH)))
    require(
        doc["executor_uid"] == registry["executor_uid"] and doc["registry_sha256"] == registry_sha256(registry),
        "WORKER_EXECUTOR",
    )
    grants.verify_source(doc["source_commit"])
    require(doc["issued_monotonic_ns"] <= time.monotonic_ns() < doc["expires_monotonic_ns"], "WORKER_EXPIRED")
    require(show(unit).get("LoadState") == "not-found" and drained(unit), "WORKER_UNIT_IN_USE")
    return doc, hashlib.sha256(raw).hexdigest()


def _packet(raw, action):
    value = decode(raw)
    require(
        type(value) is dict
        and set(value) == {"schema", "action", "ok", "summary"}
        and value["schema"] == SCHEMA
        and value["action"] == action
        and type(value["ok"]) is bool
        and type(value["summary"]) is dict,
        "WORKER_PROTOCOL",
    )
    summary = value["summary"]
    shapes = {
        "PREPARED": {
            "status",
            "files",
            "bytes",
            "pinned_files",
            "all_references_proven",
            "production_ready",
            "manifest_sha256",
            "receipt_sha256",
            "production_data_archived",
        },
        "NO_ELIGIBLE_RECORDS": {
            "status",
            "files",
            "bytes",
            "pinned_files",
            "all_references_proven",
            "production_ready",
        },
        "ARCHIVED": {"status", "moved", "production_ready", "production_data_archived", "submission_enabled"},
        "RESTORED": {"status", "restored", "production_ready", "production_data_archived", "submission_enabled"},
        "REJECTED": {"status", "reason", "transaction_state", "submission_enabled"},
    }
    status = summary.get("status")
    require(type(status) is str and status in shapes and set(summary) == shapes[status], "WORKER_PROTOCOL")
    for field in {"files", "bytes", "pinned_files", "moved", "restored"} & set(summary):
        limit = 512 * 1024**2 if field == "bytes" else 30000
        require(type(summary[field]) is int and 0 <= summary[field] <= limit, "WORKER_PROTOCOL")
    for field in {"all_references_proven", "production_ready", "submission_enabled"} & set(summary):
        require(summary[field] is False, "WORKER_PROTOCOL")
    if "production_data_archived" in summary:
        require(summary["production_data_archived"] is (status == "ARCHIVED"), "WORKER_PROTOCOL")
    if value["ok"]:
        require(
            summary["status"]
            in {"PREPARE": {"PREPARED", "NO_ELIGIBLE_RECORDS"}, "APPLY": {"ARCHIVED"}, "ROLLBACK": {"RESTORED"}}[
                action
            ],
            "WORKER_PROTOCOL",
        )
        if summary["status"] == "PREPARED":
            require(
                all(grants.digest(summary.get(key)) for key in ("manifest_sha256", "receipt_sha256")), "WORKER_PROTOCOL"
            )
        if status == "NO_ELIGIBLE_RECORDS":
            require(summary["files"] == summary["bytes"] == 0, "WORKER_PROTOCOL")
    else:
        require(
            status == "REJECTED"
            and summary["reason"] in REASONS | {"WORKER_UNAVAILABLE"}
            and summary["transaction_state"] == "INSPECTION_REQUIRED",
            "WORKER_PROTOCOL",
        )
    return value


def run_once(fd, *, action, unit):
    """One invocation only. An error may leave moved files; never retry or restore here."""
    doc, digest = _preflight(fd, action, unit)
    argv = command(
        source_commit=doc["source_commit"], uid=doc["executor_uid"], action=action, unit=unit, grant_sha256=digest
    )
    deadline = min(time.monotonic() + 600, doc["expires_monotonic_ns"] / 10**9)
    return _transport(
        fd, argv=argv, deadline=deadline, unit=unit, digest=digest, parse=lambda raw: _packet(raw, action)
    )


def _transport(fd, *, argv, deadline, unit, digest, parse):
    """Shared bounded transport; admission and command scope stay with callers."""
    process = subprocess.Popen(argv, stdin=fd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    packet = None
    success = False
    try:
        raw = read_frame(process.stdout.fileno(), maximum=16384, deadline=deadline)
        packet = parse(raw)
        code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        require(code == (0 if packet["ok"] else 78), "WORKER_EXIT_STATUS")
        require(drained(unit), "WORKER_NOT_DRAINED")
        require(
            select.select([process.stdout], [], [], 2)[0] and os.read(process.stdout.fileno(), 1) == b"",
            "WORKER_EXTRA_OUTPUT",
        )
        success = packet["ok"]
    except Exception:
        try:
            stop_owned(unit, digest)
        except Exception:
            pass
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        process.stdout.close()
    try:
        empty = drained(unit)
    except Exception:
        empty = False
    return {
        "status": "WORKER_COMPLETED" if success and empty else "WORKER_INSPECTION_REQUIRED",
        "worker_drained": empty,
        "result": packet if success and empty else None,
        "safe_to_restore_services": False,
    }
