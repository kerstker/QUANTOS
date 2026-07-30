"""Independent metadata watchdog for QUANTOS L2 Windows scheduled tasks.

This module does not import any monitored research, execution, or event worker.
It inspects Task Scheduler metadata and the exact registered action contract.
An external heartbeat monitor is still required to detect loss of this host or
of Task Scheduler itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from typing import Callable, Iterable
import urllib.parse

from services.governance.l2_restore_history import (
    L2RestoreHistoryStore,
    L2RestoreHistoryStoreError,
    RestoreCadencePolicy,
    evaluate_cadence,
)
from services.governance.artifact_admission import load_json_without_duplicate_keys
from quantos.contracts.scheduled_worker import SCHEDULE_EXIT_SAFE_BLOCKED


_HOUR_MS = 3_600_000
_DAY_MS = 86_400_000
_RUNNING_RESULT = 267_009
_PASSPHRASE_MIN_LENGTH = 12


def _persistent_windows_environment(name: str) -> str | None:
    """Read the same User/Machine environment scopes as schedule registration."""
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None
    locations = (
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    )
    for hive, key_path in locations:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                value, _kind = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        if isinstance(value, str) and value.strip():
            return value
    return None


def _configured_environment_value(
    name: str,
    *,
    persistent_reader: Callable[[str], str | None] | None = None,
) -> str | None:
    """Prefer process state, then persistent Windows User/Machine state."""
    value = os.environ.get(name)
    if isinstance(value, str) and value.strip():
        return value
    reader = persistent_reader or _persistent_windows_environment
    value = reader(name)
    return value if isinstance(value, str) and value.strip() else None


@dataclass(frozen=True)
class ExpectedTask:
    module: str
    max_age_ms: int
    partial_results: frozenset[int] = frozenset()


@dataclass(frozen=True)
class TaskSnapshot:
    name: str
    state: str
    last_run_ms: int | None
    last_result: int | None
    next_run_ms: int | None
    execute: str | None
    arguments: str | None
    working_directory: str | None


EXPECTED_TASKS: dict[str, ExpectedTask] = {
    "QUANTOS_factory_loop": ExpectedTask("crypto_factory_loop", 9 * _DAY_MS),
    "QUANTOS_ic_panel_refresh": ExpectedTask("ic_panel_research", 9 * _DAY_MS),
    "QUANTOS_paper_alpha_evaluation": ExpectedTask(
        "paper_alpha_evaluation", 9 * _DAY_MS, frozenset({2})
    ),
    "QUANTOS_forward_track": ExpectedTask("forward_track_advance", 9 * _DAY_MS),
    "QUANTOS_alpha_maintenance": ExpectedTask("alpha_maintenance_sweep", 9 * _DAY_MS),
    "QUANTOS_domain_event_ops": ExpectedTask(
        "domain_event_ops", 3 * _HOUR_MS, frozenset({2})
    ),
    "QUANTOS_gonogo_report": ExpectedTask("gonogo_report", 2 * _DAY_MS),
    "QUANTOS_alpha_paper_cycle": ExpectedTask("alpha_paper_cycle", 2 * _DAY_MS),
    "QUANTOS_paper_research_risk_bundle": ExpectedTask(
        "paper_research_risk_bundle", 2 * _DAY_MS, frozenset({2})
    ),
    "QUANTOS_paper_order_preview": ExpectedTask(
        "paper_order_preview",
        2 * _DAY_MS,
        frozenset({SCHEDULE_EXIT_SAFE_BLOCKED}),
    ),
    "QUANTOS_paper_realtime": ExpectedTask("paper_realtime", 15 * 60_000),
    "QUANTOS_binance_realtime_market": ExpectedTask(
        "binance_realtime_market", 15 * 60_000
    ),
    "QUANTOS_binance_systemic_risk": ExpectedTask(
        "binance_systemic_risk", 90 * 60_000
    ),
    "QUANTOS_binance_rpi_depth": ExpectedTask(
        "binance_rpi_depth", 3 * _HOUR_MS
    ),
    "QUANTOS_binance_realtime_features": ExpectedTask(
        "binance_realtime_features", 15 * 60_000
    ),
    "QUANTOS_binance_realtime_drift_alerts": ExpectedTask(
        "binance_realtime_drift_alerts", 15 * 60_000
    ),
    "QUANTOS_binance_realtime_stream": ExpectedTask(
        "binance_realtime_stream", 15 * 60_000
    ),
    "QUANTOS_paper_multivenue_sor": ExpectedTask(
        "paper_multivenue_sor", 15 * 60_000
    ),
    "QUANTOS_paper_realtime_10k": ExpectedTask(
        "paper_realtime_10k",
        15 * 60_000,
        frozenset({SCHEDULE_EXIT_SAFE_BLOCKED}),
    ),
    "QUANTOS_paper_shadow_auto": ExpectedTask(
        "paper_shadow_auto", 2 * _DAY_MS, frozenset({SCHEDULE_EXIT_SAFE_BLOCKED})
    ),
    "QUANTOS_paper_shadow_realtime": ExpectedTask(
        "paper_shadow_realtime", 15 * 60_000, frozenset({SCHEDULE_EXIT_SAFE_BLOCKED})
    ),
    "QUANTOS_paper_health": ExpectedTask(
        "paper_health",
        15 * 60_000,
        frozenset({SCHEDULE_EXIT_SAFE_BLOCKED}),
    ),
    "QUANTOS_scheduler_watchdog": ExpectedTask(
        "l2_scheduler_watchdog", 2 * _HOUR_MS, frozenset({2})
    ),
    "QUANTOS_backup": ExpectedTask("l2_backup", 2 * _DAY_MS),
    "QUANTOS_external_heartbeat": ExpectedTask("external_heartbeat", 20 * 60_000),
    "QUANTOS_domain_event_seal": ExpectedTask(
        "domain_event_seal", 2 * _DAY_MS, frozenset({2})
    ),
    "QUANTOS_artifact_admission_seal": ExpectedTask(
        "artifact_admission_seal", 2 * _DAY_MS, frozenset({2})
    ),
}


class SchedulerInspectionError(RuntimeError):
    """Task Scheduler metadata could not be obtained or parsed."""


_RESTORE_POLICY_FIELDS = (
    "max_attempt_age_ms",
    "max_success_age_ms",
    "max_attempt_gap_ms",
    "max_success_gap_ms",
    "minimum_history_span_ms",
    "minimum_successes",
    "clean_lookback_ms",
    "attempt_timeout_ms",
)
_RESTORE_HISTORY_MATURITY_REASONS = frozenset(
    {
        "HISTORY_SPAN_INSUFFICIENT",
        "SUCCESS_COUNT_INSUFFICIENT",
    }
)


def _restore_policy_and_path(repo_root: Path) -> tuple[RestoreCadencePolicy, Path]:
    registry_path = repo_root / "config" / "top_tier_requirements.json"
    document = load_json_without_duplicate_keys(
        registry_path.read_text(encoding="utf-8")
    )
    requirements = document.get("requirements") if isinstance(document, dict) else None
    if not isinstance(requirements, list):
        raise ValueError("restore policy registry is invalid")
    matches = [
        item
        for item in requirements
        if isinstance(item, dict) and item.get("id") == "OPS.RESTORE.DRILL"
    ]
    if len(matches) != 1 or matches[0].get("checker") != "l2_restore_history_evidence":
        raise ValueError("restore policy requirement is invalid")
    args = matches[0].get("checker_args")
    if not isinstance(args, dict):
        raise ValueError("restore policy arguments are invalid")
    history_value = args.get("history_path")
    contract = args.get("expected_source_contract")
    if not isinstance(history_value, str) or not isinstance(contract, dict):
        raise ValueError("restore policy contract is invalid")
    relative_history = Path(history_value)
    if relative_history.is_absolute() or ".." in relative_history.parts:
        raise ValueError("restore history path is invalid")
    values = {name: args.get(name) for name in _RESTORE_POLICY_FIELDS}
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in values.values()
    ):
        raise ValueError("restore cadence policy is invalid")
    canonical_contract = (
        json.dumps(contract, sort_keys=True, separators=(",", ":")) + "\n"
    )
    policy = RestoreCadencePolicy(
        source_contract_sha256=hashlib.sha256(
            canonical_contract.encode("utf-8")
        ).hexdigest(),
        **values,
    )
    return policy, repo_root.joinpath(*relative_history.parts)


def read_restore_history_health(*, repo_root: str | Path, now_ms: int) -> dict:
    """Read and evaluate the configured restore-history authority without mutation."""
    root = Path(repo_root).resolve()
    try:
        policy, history_path = _restore_policy_and_path(root)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return {
            "verdict": "BREACH",
            "reason_codes": ("RESTORE_POLICY_INVALID",),
        }
    try:
        store = L2RestoreHistoryStore(os.fspath(history_path), create=False)
    except L2RestoreHistoryStoreError:
        return {
            "verdict": "BREACH" if history_path.is_file() else "UNAVAILABLE",
            "reason_codes": (
                "RESTORE_HISTORY_INVALID"
                if history_path.is_file()
                else "RESTORE_HISTORY_UNAVAILABLE",
            ),
        }
    try:
        health = evaluate_cadence(store.read_snapshot(), policy, now_ms=now_ms)
    except (L2RestoreHistoryStoreError, TypeError, ValueError):
        return {
            "verdict": "BREACH",
            "reason_codes": ("RESTORE_HISTORY_INVALID",),
        }
    finally:
        store.close()
    return {
        "verdict": health.verdict,
        "reason_codes": health.reason_codes,
        "attempt_count": health.attempt_count,
        "success_count": health.success_count,
        "failure_count_in_clean_window": health.failure_count_in_clean_window,
        "overdue_open_count": health.overdue_open_count,
        "latest_terminal_verdict": health.latest_terminal_verdict,
        "latest_attempt_age_ms": health.latest_attempt_age_ms,
        "latest_success_age_ms": health.latest_success_age_ms,
        "maximum_attempt_gap_ms": health.maximum_attempt_gap_ms,
        "maximum_success_gap_ms": health.maximum_success_gap_ms,
        "history_span_ms": health.history_span_ms,
    }


_POWERSHELL_QUERY = r"""
$ErrorActionPreference = 'Stop'
$items = @(Get-ScheduledTask -TaskName 'QUANTOS_*' -ErrorAction SilentlyContinue | ForEach-Object {
  $task = $_
  $info = $task | Get-ScheduledTaskInfo
  $action = @($task.Actions)[0]
  [pscustomobject]@{
    TaskName = $task.TaskName
    State = [string]$task.State
    LastRunTimeUtc = if ($info.LastRunTime.Year -lt 2000) { $null } else { $info.LastRunTime.ToUniversalTime().ToString('o') }
    LastTaskResult = [int64]$info.LastTaskResult
    NextRunTimeUtc = if ($info.NextRunTime.Year -lt 2000) { $null } else { $info.NextRunTime.ToUniversalTime().ToString('o') }
    Execute = $action.Execute
    Arguments = $action.Arguments
    WorkingDirectory = $action.WorkingDirectory
  }
})
ConvertTo-Json -InputObject $items -Depth 4 -Compress
"""


def _timestamp_ms(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise SchedulerInspectionError("Task Scheduler returned an invalid timestamp")
    normalized = value
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    if "." in normalized:
        prefix, suffix = normalized.split(".", 1)
        offset_index = max(suffix.find("+"), suffix.find("-"))
        if offset_index >= 0:
            fraction, offset = suffix[:offset_index], suffix[offset_index:]
        else:
            fraction, offset = suffix, ""
        normalized = f"{prefix}.{fraction[:6]}{offset}"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SchedulerInspectionError("Task Scheduler returned an invalid timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def collect_windows_tasks(
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[TaskSnapshot]:
    completed = runner(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _POWERSHELL_QUERY,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        shell=False,
        check=False,
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if os.name == "nt"
            else 0
        ),
    )
    if completed.returncode != 0:
        raise SchedulerInspectionError("Task Scheduler query failed")
    try:
        document = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise SchedulerInspectionError("Task Scheduler returned invalid JSON") from exc
    if isinstance(document, dict):
        document = [document]
    if not isinstance(document, list):
        raise SchedulerInspectionError("Task Scheduler result must be a list")
    snapshots: list[TaskSnapshot] = []
    for item in document:
        if not isinstance(item, dict) or not isinstance(item.get("TaskName"), str):
            raise SchedulerInspectionError("Task Scheduler returned an invalid task record")
        result = item.get("LastTaskResult")
        snapshots.append(
            TaskSnapshot(
                name=item["TaskName"],
                state=str(item.get("State") or "Unknown"),
                last_run_ms=_timestamp_ms(item.get("LastRunTimeUtc")),
                last_result=int(result) if isinstance(result, (int, float)) else None,
                next_run_ms=_timestamp_ms(item.get("NextRunTimeUtc")),
                execute=item.get("Execute") if isinstance(item.get("Execute"), str) else None,
                arguments=item.get("Arguments")
                if isinstance(item.get("Arguments"), str)
                else None,
                working_directory=item.get("WorkingDirectory")
                if isinstance(item.get("WorkingDirectory"), str)
                else None,
            )
        )
    return snapshots


def backup_configuration_ready(
    *,
    repo_root: str | Path,
    passphrase: str | None = None,
    destination: str | None = None,
) -> bool:
    passphrase = (
        _configured_environment_value("QUANTOS_BACKUP_PASSPHRASE")
        if passphrase is None
        else passphrase
    )
    destination = (
        _configured_environment_value("QUANTOS_BACKUP_DEST")
        if destination is None
        else destination
    )
    if not passphrase or len(passphrase.strip()) < _PASSPHRASE_MIN_LENGTH or not destination:
        return False
    destination_path = Path(destination)
    if not destination_path.is_absolute():
        return False
    raw = os.fspath(destination_path)
    if raw.startswith("\\\\") or raw.startswith("//"):
        return True
    repo_drive = Path(repo_root).resolve().drive.casefold()
    destination_drive = destination_path.drive.casefold()
    return bool(repo_drive and destination_drive and repo_drive != destination_drive)


def external_heartbeat_configuration_ready(endpoint: str | None = None) -> bool:
    endpoint = (
        _configured_environment_value("QUANTOS_HEARTBEAT_URL")
        if endpoint is None
        else endpoint
    )
    if not endpoint:
        return False
    parsed = urllib.parse.urlsplit(endpoint)
    structurally_valid = bool(
        parsed.scheme == "https"
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )
    if not structurally_valid:
        return False
    hostname = parsed.hostname.casefold()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False
    try:
        return not ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return True


def event_seal_configuration_ready(
    *, repo_root: str | Path, destination: str | None = None
) -> bool:
    destination = (
        _configured_environment_value("QUANTOS_EVENT_SEAL_DEST")
        if destination is None
        else destination
    )
    if not destination:
        return False
    path = Path(destination)
    if not path.is_absolute():
        return False
    raw = os.fspath(path)
    if raw.startswith("\\\\") or raw.startswith("//"):
        return True
    repo_drive = Path(repo_root).resolve().drive.casefold()
    destination_drive = path.drive.casefold()
    return bool(repo_drive and destination_drive and repo_drive != destination_drive)


_LAUNCHER_ARGUMENTS = re.compile(
    r'^"(?P<launcher>[^"]+)" (?P<module>[a-z0-9_]+)$'
)


def _action_matches(
    snapshot: TaskSnapshot,
    spec: ExpectedTask,
    state_root: Path,
    code_root: Path,
) -> bool:
    if not snapshot.execute or Path(snapshot.execute).name.casefold() not in {
        "python",
        "python.exe",
        "pythonw",
        "pythonw.exe",
    }:
        return False
    match = _LAUNCHER_ARGUMENTS.fullmatch((snapshot.arguments or "").strip())
    if match is None or match.group("module") != spec.module:
        return False
    try:
        launcher = Path(match.group("launcher")).resolve(strict=False)
        expected_launcher = (
            code_root / "tools" / "l2_schedule_entrypoint.py"
        ).resolve(strict=False)
    except OSError:
        return False
    if launcher != expected_launcher:
        return False
    if not snapshot.working_directory:
        return False
    try:
        actual_working_directory = Path(snapshot.working_directory).resolve(strict=False)
    except OSError:
        return False
    return actual_working_directory == state_root.resolve()


def _check_task(
    snapshot: TaskSnapshot | None,
    *,
    name: str,
    spec: ExpectedTask,
    now_ms: int,
    repo_root: Path,
    code_root: Path,
    self_check: bool = False,
) -> dict:
    if snapshot is None:
        return {"task_name": name, "status": "BREACH", "code": "MISSING", "detail": "task is missing"}
    if snapshot.state not in {"Ready", "Running"}:
        return {
            "task_name": name,
            "status": "BREACH",
            "code": "NOT_ENABLED",
            "detail": f"task state is {snapshot.state}",
        }
    if not _action_matches(snapshot, spec, repo_root, code_root):
        return {
            "task_name": name,
            "status": "BREACH",
            "code": "ACTION_DRIFT",
            "detail": "executable, module arguments, or working directory drifted",
        }
    if snapshot.last_run_ms is None:
        if snapshot.next_run_ms is not None and snapshot.next_run_ms > now_ms:
            return {
                "task_name": name,
                "status": "PARTIAL",
                "code": "NOT_YET_RUN",
                "detail": "registered task has not reached its first trigger",
            }
        return {
            "task_name": name,
            "status": "BREACH",
            "code": "NEVER_RAN",
            "detail": "task has no successful scheduling history",
        }
    age_ms = max(0, now_ms - snapshot.last_run_ms)
    if age_ms > spec.max_age_ms:
        return {
            "task_name": name,
            "status": "BREACH",
            "code": "STALE",
            "detail": f"last run is stale by policy ({age_ms}ms > {spec.max_age_ms}ms)",
        }
    if snapshot.state == "Running":
        return {
            "task_name": name,
            "status": "PASS",
            "code": "RUNNING",
            "detail": (
                "task is currently running with the expected action; "
                "the previous result is not a completion result for this instance"
            ),
        }
    if snapshot.last_result in spec.partial_results:
        return {
            "task_name": name,
            "status": "PARTIAL",
            "code": f"RESULT_{snapshot.last_result}",
            "detail": f"task completed with an explicitly partial result ({snapshot.last_result})",
        }
    # A watchdog invocation is the authority that is evaluating the current
    # scheduler metadata.  Its previous non-zero exit code is not an
    # independent worker failure: the previous run may have been non-zero
    # precisely because it reported another task's breach, which otherwise
    # creates a permanent self-referential RESULT_1 alert.  Freshness,
    # action-pinning, and the current invocation's own exit status remain
    # observable through the external heartbeat / next scheduler poll.
    if self_check and snapshot.last_result != 0:
        return {
            "task_name": name,
            "status": "PASS",
            "code": "SELF_CHECK",
            "detail": "fresh watchdog metadata is being evaluated; prior watchdog result is not self-reported",
        }
    if snapshot.last_result != 0:
        return {
            "task_name": name,
            "status": "BREACH",
            "code": f"RESULT_{snapshot.last_result}",
            "detail": f"task completed with failure result {snapshot.last_result}",
        }
    return {
        "task_name": name,
        "status": "PASS",
        "code": "HEALTHY",
        "detail": "enabled, action-pinned, fresh, and successful",
    }


def _merge_backup_history(check: dict, health: dict | None) -> dict:
    if check["status"] != "PASS" or health is None:
        return check
    verdict = health.get("verdict")
    reasons = health.get("reason_codes")
    if verdict == "PASS" and (reasons == () or reasons == []):
        return check
    valid_reasons = (
        isinstance(reasons, (tuple, list))
        and bool(reasons)
        and all(
            isinstance(reason, str)
            and reason
            and reason.replace("_", "").isalnum()
            and reason.upper() == reason
            for reason in reasons
        )
    )
    reason = reasons[0] if valid_reasons else "INVALID_RESULT"
    if check["code"] == "RUNNING" and reason == "NO_ATTEMPTS":
        return {
            "task_name": "QUANTOS_backup",
            "status": "PARTIAL",
            "code": "RESTORE_HISTORY_BACKUP_IN_PROGRESS",
            "detail": "the first recorded backup attempt is still in progress",
        }
    if (
        valid_reasons
        and set(reasons).issubset(_RESTORE_HISTORY_MATURITY_REASONS)
        and health.get("latest_terminal_verdict") == "PASS"
        and health.get("failure_count_in_clean_window") == 0
        and health.get("overdue_open_count") == 0
    ):
        return {
            "task_name": "QUANTOS_backup",
            "status": "PARTIAL",
            "code": "RESTORE_HISTORY_MATURING",
            "detail": (
                "current restore evidence is healthy; longitudinal maturity "
                f"is incomplete ({','.join(reasons)})"
            ),
        }
    code = (
        reason
        if reason.startswith("RESTORE_HISTORY_")
        else f"RESTORE_HISTORY_{reason}"
    )
    return {
        "task_name": "QUANTOS_backup",
        "status": "BREACH",
        "code": code,
        "detail": f"restore history authority is not healthy ({reason})",
    }


def evaluate_schedule(
    snapshots: Iterable[TaskSnapshot],
    *,
    now_ms: int,
    repo_root: str | Path,
    code_root: str | Path | None = None,
    backup_ready: bool,
    backup_history_health: dict | None = None,
    heartbeat_ready: bool = False,
    event_seal_ready: bool = False,
) -> dict:
    if now_ms < 0:
        raise ValueError("now_ms must be non-negative")
    root = Path(repo_root).resolve()
    release_root = root if code_root is None else Path(code_root).resolve()
    by_name: dict[str, TaskSnapshot] = {}
    duplicates: set[str] = set()
    for snapshot in snapshots:
        if snapshot.name in by_name:
            duplicates.add(snapshot.name)
        by_name[snapshot.name] = snapshot

    checks: list[dict] = []
    conditional_readiness = {
        "QUANTOS_backup": (
            backup_ready,
            "strong passphrase and external destination are not both configured",
        ),
        "QUANTOS_external_heartbeat": (
            heartbeat_ready,
            "operator-owned HTTPS heartbeat endpoint is not configured",
        ),
        "QUANTOS_domain_event_seal": (
            event_seal_ready,
            "external event-seal destination is not configured",
        ),
        "QUANTOS_artifact_admission_seal": (
            event_seal_ready,
            "external event-seal destination is not configured",
        ),
    }
    for name, spec in EXPECTED_TASKS.items():
        snapshot = by_name.get(name)
        readiness = conditional_readiness.get(name)
        if readiness is not None and not readiness[0]:
            if snapshot is None or snapshot.state == "Disabled":
                checks.append(
                    {
                        "task_name": name,
                        "status": "UNAVAILABLE",
                        "code": "CONFIG_UNAVAILABLE",
                        "detail": readiness[1],
                    }
                )
            else:
                checks.append(
                    {
                        "task_name": name,
                        "status": "BREACH",
                        "code": "ENABLED_WITHOUT_CONFIG",
                        "detail": "backup task is enabled without a valid encrypted external destination",
                    }
                )
            continue
        check = _check_task(
            snapshot,
            name=name,
            spec=spec,
            now_ms=now_ms,
            repo_root=root,
            code_root=release_root,
            self_check=name == "QUANTOS_scheduler_watchdog",
        )
        if name == "QUANTOS_backup":
            check = _merge_backup_history(check, backup_history_health)
        checks.append(check)

    for name in sorted(duplicates):
        checks.append(
            {
                "task_name": name,
                "status": "BREACH",
                "code": "DUPLICATE",
                "detail": "Task Scheduler returned duplicate task definitions",
            }
        )
    for name, snapshot in sorted(by_name.items()):
        if name in EXPECTED_TASKS or snapshot.state == "Disabled":
            continue
        checks.append(
            {
                "task_name": name,
                "status": "BREACH",
                "code": "UNEXPECTED_ENABLED_TASK",
                "detail": "unexpected enabled QUANTOS task is outside the L2 allowlist",
            }
        )

    summary = {status: 0 for status in ("PASS", "PARTIAL", "UNAVAILABLE", "BREACH")}
    for check in checks:
        summary[check["status"]] += 1
    verdict = "BREACH" if summary["BREACH"] else (
        "PARTIAL" if summary["PARTIAL"] or summary["UNAVAILABLE"] else "PASS"
    )
    return {
        "schema_version": 1,
        "verdict": verdict,
        "observed_at_ms": now_ms,
        "backup_configuration_ready": backup_ready,
        "external_heartbeat_configuration_ready": heartbeat_ready,
        "event_seal_configuration_ready": event_seal_ready,
        "summary": summary,
        "checks": checks,
    }


def _write_status(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}-{secrets.token_hex(4)}")
    try:
        temporary.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _alert_signature(result: dict) -> str:
    degraded = [
        (check["task_name"], check["status"], check["code"])
        for check in result["checks"]
        if check["status"] != "PASS"
    ]
    return hashlib.sha256(
        json.dumps(degraded, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def run_scheduler_watchdog(
    *,
    repo_root: str | Path,
    code_root: str | Path | None = None,
    status_path: str | Path,
    now_ms: int | None = None,
    backup_ready: bool | None = None,
    heartbeat_ready: bool | None = None,
    event_seal_ready: bool | None = None,
    collector: Callable[[], list[TaskSnapshot]] = collect_windows_tasks,
    history_reader: Callable[..., dict] = read_restore_history_health,
    emit_fn: Callable | None = None,
) -> dict:
    observed_at_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if backup_ready is None:
        backup_ready = backup_configuration_ready(repo_root=repo_root)
    if heartbeat_ready is None:
        heartbeat_ready = external_heartbeat_configuration_ready()
    if event_seal_ready is None:
        event_seal_ready = event_seal_configuration_ready(repo_root=repo_root)
    try:
        snapshots = collector()
        history_health = None
        backup_snapshot = next(
            (item for item in snapshots if item.name == "QUANTOS_backup"), None
        )
        if (
            backup_ready
            and backup_snapshot is not None
            and backup_snapshot.state in {"Ready", "Running"}
        ):
            try:
                history_health = history_reader(
                    repo_root=repo_root,
                    now_ms=observed_at_ms,
                )
            except Exception:
                history_health = {
                    "verdict": "BREACH",
                    "reason_codes": ("RESTORE_HISTORY_INSPECTION_FAILED",),
                }
        result = evaluate_schedule(
            snapshots,
            now_ms=observed_at_ms,
            repo_root=repo_root,
            code_root=code_root,
            backup_ready=backup_ready,
            backup_history_health=history_health,
            heartbeat_ready=heartbeat_ready,
            event_seal_ready=event_seal_ready,
        )
    except Exception as exc:
        result = {
            "schema_version": 1,
            "verdict": "BREACH",
            "observed_at_ms": observed_at_ms,
            "backup_configuration_ready": backup_ready,
            "external_heartbeat_configuration_ready": heartbeat_ready,
            "event_seal_configuration_ready": event_seal_ready,
            "summary": {"PASS": 0, "PARTIAL": 0, "UNAVAILABLE": 0, "BREACH": 1},
            "checks": [
                {
                    "task_name": "WINDOWS_TASK_SCHEDULER",
                    "status": "BREACH",
                    "code": "INSPECTION_FAILED",
                    "detail": f"scheduler metadata inspection failed ({type(exc).__name__})",
                }
            ],
        }
    signature = _alert_signature(result)
    status = Path(status_path)
    previous_signature = None
    try:
        previous = json.loads(status.read_text(encoding="utf-8"))
        previous_signature = previous.get("alert_signature")
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    result["alert_signature"] = signature
    if signature != previous_signature and result["verdict"] != "PASS":
        if emit_fn is None:
            from tools.alert_sink import emit as emit_fn
        alerts = [
            f"{check['task_name']} {check['status']} {check['code']}"
            for check in result["checks"]
            if check["status"] != "PASS"
        ]
        emit_fn(alerts, context="l2_scheduler_watchdog", now_ms=observed_at_ms)
    _write_status(status, result)
    return result


def main() -> None:
    repo_root = Path.cwd()
    code_root = Path(__file__).resolve().parents[1]
    result = run_scheduler_watchdog(
        repo_root=repo_root,
        code_root=code_root,
        status_path=repo_root / "data" / "cache" / "scheduler_watchdog_status.json",
    )
    print(json.dumps(result, sort_keys=True))
    sys.exit(0 if result["verdict"] == "PASS" else 1 if result["verdict"] == "BREACH" else 2)


if __name__ == "__main__":
    main()
