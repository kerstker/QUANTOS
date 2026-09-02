"""Independent metadata watchdog for QUANTOS L2 scheduled workers.

This module does not import any monitored research, execution, or event worker.
It inspects the host scheduler metadata and the exact registered action contract.
An external heartbeat monitor is still required to detect loss of this host or
of the host scheduler itself.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable, Iterable, Mapping
import urllib.parse

from services.governance.l2_restore_history import (
    L2RestoreHistoryStore,
    L2RestoreHistoryStoreError,
    RestoreCadencePolicy,
    evaluate_cadence,
)
from services.governance.artifact_admission import load_json_without_duplicate_keys
from services.governance import l2_backup_boundary as l2_backup
from quantos.contracts.l2_schedule_policy import (
    HARD_QUARANTINED_TASKS,
    MVP_CONDITIONAL_MODULES,
    MVP_DISABLED_TASKS,
    MVP_EXPECTED_TASKS,
    MVP_RESIDENT_MODULES,
)
from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SAFE_BLOCKED,
)


_HOUR_MS = 3_600_000
_DAY_MS = 86_400_000
_RUNNING_RESULT = 267_009
SYSTEMD_PROXY_ENVIRONMENT_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "FTP_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "ftp_proxy",
    "no_proxy",
    "all_proxy",
)
_SYSTEMD_PROXY_PASS_ENVIRONMENT = " ".join(SYSTEMD_PROXY_ENVIRONMENT_NAMES)
_ATTENTION_RELEASE_ENVIRONMENT = "QUANTOS_WIKIMEDIA_ATTENTION_RELEASE_COMMIT"
# These services are outside the scheduled-worker namespace.  Recognizing an
# exact name here only keeps the scheduler collector scoped to scheduler
# targets; it does not make a health or safety claim about the support service.
SYSTEMD_NON_SCHEDULED_SUPPORT_UNITS = frozenset(
    {
        "quantos-console-api.service",
        "quantos-console-frontend.service",
        "quantos-research-runner.service",
        "quantos-proxy-environment.service",
        "quantos-public-proxy-runtime.service",
    }
)


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
    scheduler_contract_ok: bool = True
    configured_environment: tuple[tuple[str, str], ...] = ()


_EXPECTED_TASK_SPECS: dict[str, tuple[int, frozenset[int]]] = {
    "artifact_admission_seal": (2 * _DAY_MS, frozenset({2})),
    "ashare_alpha_search_worker": (
        9 * _DAY_MS,
        frozenset({SCHEDULE_EXIT_SAFE_BLOCKED}),
    ),
    "ashare_forward_observation": (
        2 * _DAY_MS,
        frozenset({SCHEDULE_EXIT_SAFE_BLOCKED}),
    ),
    "binance_book_depth_forward_refresh": (2 * _DAY_MS, frozenset()),
    "binance_realtime_drift_alerts": (15 * 60_000, frozenset()),
    "binance_realtime_features": (15 * 60_000, frozenset()),
    "binance_realtime_market": (15 * 60_000, frozenset()),
    "binance_realtime_stream": (15 * 60_000, frozenset()),
    "binance_rpi_depth": (3 * _HOUR_MS, frozenset()),
    "binance_systemic_risk": (
        90 * 60_000,
        frozenset({SCHEDULE_EXIT_SAFE_BLOCKED}),
    ),
    "bybit_realtime_stream": (15 * 60_000, frozenset()),
    "cftc_tff_forward_refresh": (9 * _DAY_MS, frozenset()),
    "cross_venue_liquidation_health": (2 * _HOUR_MS, frozenset()),
    "defillama_stablecoin_forward_refresh": (2 * _DAY_MS, frozenset()),
    "defillama_tvl_forward_refresh": (2 * _DAY_MS, frozenset()),
    "domain_event_ops": (3 * _HOUR_MS, frozenset({2})),
    "domain_event_seal": (2 * _DAY_MS, frozenset({2})),
    "external_heartbeat": (20 * 60_000, frozenset()),
    "hyperliquid_public_forward_refresh": (2 * _DAY_MS, frozenset()),
    "l2_backup": (2 * _DAY_MS, frozenset()),
    "mexc_klines_fetch": (2 * _DAY_MS, frozenset()),
    "l2_scheduler_watchdog": (2 * _HOUR_MS, frozenset({2})),
    "okx_borrow_forward_refresh": (2 * _DAY_MS, frozenset()),
    "raw_archive_audit": (2 * _DAY_MS, frozenset()),
    "wikimedia_attention_forward_refresh": (
        2 * _DAY_MS,
        # Wikimedia attention is an optional enrichment.  Upstream/public
        # transport failure (1) and explicit no-data/safe-blocked (2) are both
        # visible PARTIAL states, never a deployment/runtime BREACH.
        frozenset({SCHEDULE_EXIT_FAILURE, SCHEDULE_EXIT_SAFE_BLOCKED}),
    ),
}
if set(_EXPECTED_TASK_SPECS) != set(MVP_EXPECTED_TASKS.values()):
    raise RuntimeError("MVP watchdog specifications do not match the static policy")

EXPECTED_TASKS: dict[str, ExpectedTask] = {
    task_name: ExpectedTask(module, *_EXPECTED_TASK_SPECS[module])
    for task_name, module in MVP_EXPECTED_TASKS.items()
}

# Stable aliases retained for callers.  Their authority comes from the single
# immutable scheduling policy rather than a second local allow/deny list.
QUARANTINED_TASKS: dict[str, str] = dict(HARD_QUARANTINED_TASKS)
DISABLED_MVP_TASKS: dict[str, str] = dict(MVP_DISABLED_TASKS)
_POLICY_TASK_BY_MODULE = {
    module: task_name
    for task_name, module in (
        *MVP_EXPECTED_TASKS.items(),
        *HARD_QUARANTINED_TASKS.items(),
        *MVP_DISABLED_TASKS.items(),
    )
}
if len(_POLICY_TASK_BY_MODULE) != (
    len(MVP_EXPECTED_TASKS)
    + len(HARD_QUARANTINED_TASKS)
    + len(MVP_DISABLED_TASKS)
):
    raise RuntimeError("L2 policy task modules must be unique")
_DENIED_POLICY_MODULES = frozenset(
    {*HARD_QUARANTINED_TASKS.values(), *MVP_DISABLED_TASKS.values()}
)
_CONDITIONAL_TASK_NAMES = frozenset(
    task_name
    for task_name, module in MVP_EXPECTED_TASKS.items()
    if module in MVP_CONDITIONAL_MODULES
)


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
_RESTORE_HISTORY_RECOVERED_REASONS = frozenset(
    {*_RESTORE_HISTORY_MATURITY_REASONS, "FAILURE_IN_CLEAN_LOOKBACK"}
)


def _restore_policy_and_path(
    repo_root: Path,
    *,
    policy_root: Path | None = None,
) -> tuple[RestoreCadencePolicy, Path]:
    registry_root = repo_root if policy_root is None else policy_root
    registry_path = registry_root / "config" / "top_tier_requirements.json"
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


def read_restore_history_health(
    *,
    repo_root: str | Path,
    now_ms: int,
    policy_root: str | Path | None = None,
) -> dict:
    """Read and evaluate the configured restore-history authority without mutation."""
    root = Path(repo_root).resolve()
    resolved_policy_root = None if policy_root is None else Path(policy_root).resolve()
    try:
        policy, history_path = _restore_policy_and_path(
            root,
            policy_root=resolved_policy_root,
        )
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


_SYSTEMD_UNIT = re.compile(
    r"^(?:quantos-l2@(?P<module>[a-z0-9_]+)\.(?P<kind>service|timer)|"
    r"(?P<stream>quantos-(?:binance|bybit)-realtime-stream)\.service|"
    r"(?P<watchdog>quantos-l2-watchdog)\.(?P<watchdog_kind>service|timer))$"
)
_SYSTEMD_GENERIC_TEMPLATE_UNITS = frozenset(
    {"quantos-l2@.service", "quantos-l2@.timer"}
)
_SYSTEMD_TIMESTAMP = re.compile(r"^@(?P<seconds>[0-9]+)$")
_SYSTEMD_SHOW_PROPERTIES = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "UnitFileState",
    "Result",
    "ExecMainStatus",
    "ExecMainStartTimestamp",
    "ExecMainExitTimestamp",
    "FragmentPath",
    "DropInPaths",
    "NeedDaemonReload",
)
_SYSTEMD_TIMER_PROPERTIES = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "UnitFileState",
    "FragmentPath",
    "DropInPaths",
    "NeedDaemonReload",
)
_SYSTEMD_SAFE_ENVIRONMENT = frozenset(
    {
        "LIVE_TRADING_ENABLED=false",
        "REAL_BROKER_ENABLED=false",
        "ALLOW_ORDER_SUBMISSION=false",
        "ALLOW_AI_DIRECT_TRADING=false",
        "REQUIRE_HUMAN_APPROVAL=true",
        "QUANTOS_L2_DRY_RUN_ONLY=true",
    }
)
_RESIDENT_STREAM_MODULES = MVP_RESIDENT_MODULES
_RESIDENT_HEALTH_PATHS = {
    "QUANTOS_binance_realtime_stream": "binance_realtime_stream_status.json",
    "QUANTOS_bybit_realtime_stream": "bybit_realtime_stream_status.json",
}


def _systemd_timestamp_ms(value: str | None) -> int | None:
    if not value:
        return None
    match = _SYSTEMD_TIMESTAMP.fullmatch(value)
    if match is None:
        raise SchedulerInspectionError("systemd returned non-machine timestamp metadata")
    return int(match.group("seconds")) * 1_000


def _parse_systemd_properties(
    raw: str,
    expected_unit: str,
    expected_names: tuple[str, ...] = _SYSTEMD_SHOW_PROPERTIES,
    *,
    allowed_load_states: frozenset[str] = frozenset({"loaded"}),
) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in raw.splitlines():
        if not line:
            continue
        name, separator, value = line.partition("=")
        if not separator or name not in expected_names or name in properties:
            raise SchedulerInspectionError("systemd returned invalid unit metadata")
        properties[name] = value
    if set(properties) != set(expected_names):
        raise SchedulerInspectionError("systemd returned incomplete unit metadata")
    if (
        properties["Id"] != expected_unit
        or properties["LoadState"] not in allowed_load_states
    ):
        raise SchedulerInspectionError("systemd returned inconsistent unit metadata")
    return properties


_SYSTEMD_UNIT_SECTIONS = frozenset({"Unit", "Service", "Timer", "Install"})


def _parse_systemd_unit_text(raw: str) -> dict[str, tuple[str, ...]]:
    """Parse the small rendered-unit subset and preserve repeated directives."""

    directives: dict[str, list[str]] = {}
    section: str | None = None
    for original in raw.splitlines():
        line = original.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            if section not in _SYSTEMD_UNIT_SECTIONS:
                raise SchedulerInspectionError("systemd unit contains an unknown section")
            continue
        if section is None:
            # ``systemctl cat`` prefixes source paths as comments, so every
            # non-comment directive still has to belong to a known section.
            raise SchedulerInspectionError("systemd unit contains a directive outside a section")
        name, separator, value = line.partition("=")
        if not separator or not name or any(character.isspace() for character in name):
            raise SchedulerInspectionError("systemd unit contains an invalid directive")
        directives.setdefault(f"{section}.{name}", []).append(value)
    return {name: tuple(values) for name, values in directives.items()}


def _one_directive(
    directives: dict[str, tuple[str, ...]], name: str
) -> str | None:
    values = directives.get(name, ())
    return values[0] if len(values) == 1 else None


def _unit_directives_exact(
    directives: dict[str, tuple[str, ...]],
    *,
    module: str,
    release_commit: str,
) -> tuple[bool, tuple[tuple[str, str], ...]]:
    release_root = f"/opt/quantos/releases/{release_commit}"
    state_root = "/srv/quantos/state"
    environment_values = directives.get("Service.Environment", ())
    # In systemd an empty Environment= directive resets the complete list.
    # Never discard it while normalizing: a later drop-in could otherwise
    # erase the literal L2 safety flags yet leave the visible text looking safe.
    environment_has_reset = any(value == "" for value in environment_values)
    environment_raw = " ".join(environment_values)
    environment_ok = _systemd_environment_is_safe(
        environment_raw,
        module,
        release_commit=release_commit,
    )
    environment = tuple(
        sorted(item.partition("=")[::2] for item in environment_values if "=" in item)
    )
    expected_python = f"/opt/quantos/venvs/{release_commit}/bin/python"
    expected_launcher = f"{release_root}/tools/l2_schedule_entrypoint.py"
    expected_exec = (
        f"{expected_python} {expected_launcher} {module} "
        f"--release-commit {release_commit}"
    )
    expected_hardening = {
        "Service.User": "quantos",
        "Service.Group": "quantos",
        "Service.UMask": "0077",
        "Service.WorkingDirectory": state_root,
        "Service.NoNewPrivileges": "true",
        "Service.ProtectSystem": "strict",
        "Service.ProtectHome": "true",
        "Service.PrivateTmp": "true",
        "Service.PrivateDevices": "true",
        "Service.ProtectKernelTunables": "true",
        "Service.ProtectKernelModules": "true",
        "Service.ProtectControlGroups": "true",
        "Service.RestrictSUIDSGID": "true",
        "Service.LockPersonality": "true",
        "Service.RestrictRealtime": "true",
        "Service.CapabilityBoundingSet": "",
        "Service.AmbientCapabilities": "",
        "Service.ReadOnlyPaths": release_root,
    }
    expected_writes = {state_root}
    expected_mounts: set[str] = set()
    expected_credentials: tuple[str, ...] = ()
    if module == "l2_backup":
        destination = _systemd_environment_value(environment_raw, "QUANTOS_BACKUP_DEST")
        if destination is None:
            return False, environment
        expected_writes.add(destination)
        expected_mounts.add(destination)
        expected_credentials = (
            "l2-backup-passphrase:/etc/quantos/credentials/l2-backup-passphrase",
        )
    elif module in {"domain_event_seal", "artifact_admission_seal"}:
        destination = _systemd_environment_value(
            environment_raw, "QUANTOS_EVENT_SEAL_DEST"
        )
        if destination is None:
            return False, environment
        expected_writes.add(destination)
        expected_mounts.add(destination)
    restart = "always" if module in _RESIDENT_STREAM_MODULES else "no"
    runtime_max = "23h55m" if module in _RESIDENT_STREAM_MODULES else None
    required_keys = {
        "Unit.After",
        "Unit.Wants",
        "Service.Type",
        "Service.User",
        "Service.Group",
        "Service.UMask",
        "Service.Environment",
        "Service.WorkingDirectory",
        "Service.ExecStart",
        "Service.TimeoutStartSec",
        "Service.Restart",
        "Service.KillMode",
        "Service.NoNewPrivileges",
        "Service.ProtectSystem",
        "Service.ProtectHome",
        "Service.PrivateTmp",
        "Service.PrivateDevices",
        "Service.ProtectKernelTunables",
        "Service.ProtectKernelModules",
        "Service.ProtectControlGroups",
        "Service.RestrictSUIDSGID",
        "Service.LockPersonality",
        "Service.RestrictRealtime",
        "Service.CapabilityBoundingSet",
        "Service.AmbientCapabilities",
        "Service.ReadOnlyPaths",
        "Service.ReadWritePaths",
    }
    inherits_public_proxy_transport = module != "l2_scheduler_watchdog"
    if inherits_public_proxy_transport:
        required_keys.add("Service.PassEnvironment")
    accepts_safe_partial_exit = module not in {
        "binance_realtime_stream",
        "bybit_realtime_stream",
    }
    if accepts_safe_partial_exit:
        required_keys.add("Service.SuccessExitStatus")
    expected_success_exit_status = (
        "1 2" if module == "wikimedia_attention_forward_refresh" else "2"
    )
    optional_descriptive_keys = {"Unit.Description", "Unit.Documentation"}
    if expected_mounts:
        required_keys.add("Unit.RequiresMountsFor")
    if expected_credentials:
        required_keys.add("Service.LoadCredential")
    if module in _RESIDENT_STREAM_MODULES:
        required_keys.update(
            {
                "Service.RestartSec",
                "Service.RuntimeMaxSec",
                "Service.TimeoutStopSec",
                "Install.WantedBy",
            }
        )
    keys_are_exact = bool(
        required_keys <= directives.keys()
        and directives.keys() <= required_keys | optional_descriptive_keys
        and all(
            len(directives.get(name, ())) == 1
            for name in optional_descriptive_keys & directives.keys()
        )
    )
    ok = bool(
        keys_are_exact
        and not environment_has_reset
        and environment_ok
        and _one_directive(directives, "Unit.After") == "network-online.target"
        and _one_directive(directives, "Unit.Wants") == "network-online.target"
        and _one_directive(directives, "Service.Type")
        == ("simple" if module in _RESIDENT_STREAM_MODULES else "oneshot")
        and (
            not accepts_safe_partial_exit
            or _one_directive(directives, "Service.SuccessExitStatus")
            == expected_success_exit_status
        )
        and _one_directive(directives, "Service.ExecStart") == expected_exec
        and all(_one_directive(directives, name) == value for name, value in expected_hardening.items())
        and set(directives.get("Service.ReadWritePaths", ())) == expected_writes
        and len(directives.get("Service.ReadWritePaths", ())) == len(expected_writes)
        and set(directives.get("Unit.RequiresMountsFor", ())) == expected_mounts
        and len(directives.get("Unit.RequiresMountsFor", ())) == len(expected_mounts)
        and directives.get("Service.LoadCredential", ()) == expected_credentials
        and "Service.EnvironmentFile" not in directives
        and directives.get("Service.PassEnvironment", ())
        == (
            (_SYSTEMD_PROXY_PASS_ENVIRONMENT,)
            if inherits_public_proxy_transport
            else ()
        )
        and _one_directive(directives, "Service.Restart") == restart
        and _one_directive(directives, "Service.KillMode") == "control-group"
        and (
            module not in _RESIDENT_STREAM_MODULES
            or (
                _one_directive(directives, "Service.RestartSec") == "30s"
                and _one_directive(directives, "Service.TimeoutStopSec") == "90s"
                and _one_directive(directives, "Install.WantedBy")
                == "multi-user.target"
            )
        )
        and (
            runtime_max is None
            or _one_directive(directives, "Service.RuntimeMaxSec") == runtime_max
        )
    )
    return ok, environment


def _systemd_environment_is_safe(
    raw: str,
    module: str,
    *,
    release_commit: str,
) -> bool:
    values = raw.split()
    required = {item.split("=", 1)[0] for item in _SYSTEMD_SAFE_ENVIRONMENT}
    observed: dict[str, str] = {}
    for item in values:
        name, separator, _value = item.partition("=")
        if separator and name in required:
            if name in observed:
                return False
            observed[name] = item
    if set(observed.values()) != set(_SYSTEMD_SAFE_ENVIRONMENT):
        return False
    allowed = {
        *(item.split("=", 1)[0] for item in _SYSTEMD_SAFE_ENVIRONMENT),
        "QUANTOS_RELEASE_ROOT",
        "QUANTOS_RELEASE_COMMIT",
        "QUANTOS_STATE_ROOT",
    }
    if module == "l2_scheduler_watchdog":
        allowed.add("QUANTOS_SCHEDULER_PROVIDER")
        allowed.add(_ATTENTION_RELEASE_ENVIRONMENT)
    elif module == "l2_backup":
        allowed.add("QUANTOS_BACKUP_DEST")
    elif module in {"domain_event_seal", "artifact_admission_seal"}:
        allowed.add("QUANTOS_EVENT_SEAL_DEST")
    elif module == "external_heartbeat":
        allowed.add("QUANTOS_HEARTBEAT_URL")
    parsed = [item.partition("=") for item in values]
    observed_values = {
        name: value for name, separator, value in parsed if separator
    }
    fixed_values_ok = bool(
        observed_values.get("QUANTOS_RELEASE_ROOT")
        == f"/opt/quantos/releases/{release_commit}"
        and observed_values.get("QUANTOS_RELEASE_COMMIT") == release_commit
        and observed_values.get("QUANTOS_STATE_ROOT") == "/srv/quantos/state"
        and (
            module != "l2_scheduler_watchdog"
            or (
                observed_values.get("QUANTOS_SCHEDULER_PROVIDER") == "systemd"
                and re.fullmatch(
                    r"[0-9a-f]{40}",
                    observed_values.get(_ATTENTION_RELEASE_ENVIRONMENT, ""),
                )
                is not None
            )
        )
    )
    if module == "external_heartbeat":
        fixed_values_ok = bool(
            fixed_values_ok
            and external_heartbeat_configuration_ready(
                observed_values.get("QUANTOS_HEARTBEAT_URL")
            )
        )
    return bool(
        fixed_values_ok
        and
        all(separator and name in allowed for name, separator, _value in parsed)
        and len({name for name, _separator, _value in parsed}) == len(parsed)
        and {name for name, _separator, _value in parsed} == allowed
    )


def _systemd_environment_value(raw: str, name: str) -> str | None:
    matches = [item.split("=", 1)[1] for item in raw.split() if item.startswith(name + "=")]
    return matches[0] if len(matches) == 1 else None


def _systemd_duration_seconds(value: str) -> float | None:
    position = 0
    total = 0.0
    scale = {
        "us": 1e-6,
        "ms": 1e-3,
        "s": 1.0,
        "m": 60.0,
        "min": 60.0,
        "h": 3600.0,
    }
    pattern = re.compile(
        r"(?P<number>[0-9]+(?:\.[0-9]+)?)(?P<unit>us|ms|min|h|m|s)"
    )
    for match in pattern.finditer(value):
        if match.start() != position:
            return None
        total += float(match.group("number")) * scale[match.group("unit")]
        position = match.end()
    return total if position == len(value) and position > 0 else None


def _expected_timeout_seconds(module: str) -> float | None:
    try:
        from tools.render_systemd_l2_units import CALENDARS, INTERVALS
    except (ImportError, OSError):
        return None
    if module == "l2_scheduler_watchdog":
        return 5 * 60.0
    if module in _RESIDENT_STREAM_MODULES:
        return 5 * 60.0
    if module in CALENDARS:
        return _systemd_duration_seconds(CALENDARS[module][1])
    if module in INTERVALS:
        return _systemd_duration_seconds(INTERVALS[module][1])
    return None


def _systemd_timer_contract_ok(
    *,
    module: str,
    directives: dict[str, tuple[str, ...]],
) -> bool:
    try:
        from tools.render_systemd_l2_units import CALENDARS, INTERVALS
    except (ImportError, OSError):
        return False
    expected_service = (
        "quantos-l2-watchdog.service"
        if module == "l2_scheduler_watchdog"
        else f"quantos-l2@{module}.service"
    )
    required_keys = {
        "Timer.Persistent",
        "Timer.AccuracySec",
        "Timer.RandomizedDelaySec",
        "Timer.Unit",
        "Install.WantedBy",
    }
    if module == "l2_scheduler_watchdog":
        required_keys.update({"Timer.OnBootSec", "Timer.OnUnitInactiveSec"})
    else:
        required_keys.add("Timer.OnCalendar")
    optional_descriptive_keys = {"Unit.Description", "Unit.Documentation"}
    # Empty list-valued timer directives reset earlier values in systemd.
    # Exact key/value cardinality below rejects both resets and appended
    # triggers, including definitions introduced by a drop-in.
    if not (
        required_keys <= directives.keys()
        and directives.keys() <= required_keys | optional_descriptive_keys
        and all(
            len(directives.get(name, ())) == 1
            for name in optional_descriptive_keys & directives.keys()
        )
    ):
        return False
    if (
        _one_directive(directives, "Timer.Unit") != expected_service
        or _one_directive(directives, "Install.WantedBy") != "timers.target"
    ):
        return False
    common = bool(
        _one_directive(directives, "Timer.Persistent") == "true"
        and _one_directive(directives, "Timer.AccuracySec") == "1s"
        and _one_directive(directives, "Timer.RandomizedDelaySec") == "0"
    )
    if module == "l2_scheduler_watchdog":
        return bool(
            common
            and directives.get("Timer.OnBootSec", ()) == ("5m",)
            and directives.get("Timer.OnUnitInactiveSec", ()) == ("15m",)
            and "Timer.OnCalendar" not in directives
        )
    if module in INTERVALS:
        calendar, _timeout = INTERVALS[module]
        return bool(
            common
            and directives.get("Timer.OnCalendar", ()) == (calendar,)
            and "Timer.OnBootSec" not in directives
            and "Timer.OnUnitInactiveSec" not in directives
        )
    if module in CALENDARS:
        calendar, _timeout = CALENDARS[module]
        return bool(
            common
            and directives.get("Timer.OnCalendar", ()) == (calendar,)
            and "Timer.OnBootSec" not in directives
            and "Timer.OnUnitInactiveSec" not in directives
        )
    return False


def _systemd_module(unit: str) -> str:
    match = _SYSTEMD_UNIT.fullmatch(unit)
    if match is None or match.group("kind") == "timer":
        raise SchedulerInspectionError("systemd unit name is outside the L2 policy")
    if match.group("stream"):
        return (
            "binance_realtime_stream"
            if match.group("stream") == "quantos-binance-realtime-stream"
            else "bybit_realtime_stream"
        )
    if match.group("watchdog"):
        return "l2_scheduler_watchdog"
    module = match.group("module")
    if module is None:
        raise SchedulerInspectionError("systemd unit has no L2 module")
    return module


def _denied_systemd_state(
    properties: Mapping[str, str],
    timer_properties: Mapping[str, str] | None,
) -> str:
    """Classify denied units without trusting their obsolete action contract."""

    if properties["ActiveState"] in {"active", "activating", "reloading"}:
        return "Running"
    timer_active = bool(
        timer_properties is not None
        and timer_properties["ActiveState"] in {"active", "activating", "reloading"}
    )
    timer_quiesced = bool(
        timer_properties is None
        or (
            timer_properties["ActiveState"] == "inactive"
            and timer_properties["UnitFileState"] in {"disabled", "masked"}
        )
    )
    # A merely static/disabled service remains manually startable.  Since stale
    # denied units intentionally skip action-contract parsing, only masking the
    # service proves there is no systemctl-start bypass outside the watchdog.
    fully_quiesced = bool(
        properties["ActiveState"] == "inactive"
        and properties["UnitFileState"] == "masked"
        and not timer_active
        and timer_quiesced
    )
    return "Disabled" if fully_quiesced else "Ready"


def _systemd_snapshot(
    unit: str,
    properties: dict[str, str],
    *,
    timer_properties: dict[str, str] | None,
    service_directives: dict[str, tuple[str, ...]],
    timer_directives: dict[str, tuple[str, ...]] | None,
) -> TaskSnapshot:
    module = _systemd_module(unit)
    task_name = _POLICY_TASK_BY_MODULE.get(module)
    if task_name is None:
        raise SchedulerInspectionError("systemd module does not map to one watchdog task")
    # A systemd oneshot remains ``activating/start`` for its entire execution.
    # Treat that as a real running state; otherwise a watchdog sample taken on
    # a timer boundary falsely reports the task as Disabled while the worker is
    # legitimately computing.
    active = properties["ActiveState"] in {"active", "activating"}
    running = active and properties["SubState"] in {"running", "start"}
    if running:
        state = "Running"
    elif timer_properties is not None and (
        timer_properties["ActiveState"] == "active"
        and timer_properties["SubState"] == "waiting"
        and timer_properties["UnitFileState"] == "enabled"
    ):
        state = "Ready"
    else:
        state = "Disabled"
    result = properties["Result"]
    raw_status = properties["ExecMainStatus"]
    last_result = int(raw_status) if raw_status.isdigit() else None
    if result and result not in {"success", "done"} and last_result == 0:
        last_result = 1
    last_run_ms = _systemd_timestamp_ms(properties["ExecMainStartTimestamp"])
    if module in _DENIED_POLICY_MODULES:
        return TaskSnapshot(
            name=task_name,
            state=_denied_systemd_state(properties, timer_properties),
            last_run_ms=last_run_ms,
            last_result=last_result,
            next_run_ms=None,
            execute=None,
            arguments=None,
            working_directory=None,
            # Evaluation ignores action/cadence metadata for denied tasks.  The
            # only authoritative requirement is that they remain quiesced.
            scheduler_contract_ok=True,
            configured_environment=(),
        )
    environment_raw = " ".join(service_directives.get("Service.Environment", ()))
    release_commit = _systemd_environment_value(
        environment_raw, "QUANTOS_RELEASE_COMMIT"
    )
    if release_commit is None or re.fullmatch(r"[0-9a-f]{40}", release_commit) is None:
        service_contract_ok = False
        configured_environment: tuple[tuple[str, str], ...] = ()
    else:
        service_contract_ok, configured_environment = _unit_directives_exact(
            service_directives,
            module=module,
            release_commit=release_commit,
        )
    expected_python = (
        None
        if release_commit is None
        else f"/opt/quantos/venvs/{release_commit}/bin/python"
    )
    expected_launcher = (
        None
        if release_commit is None
        else f"/opt/quantos/releases/{release_commit}/tools/l2_schedule_entrypoint.py"
    )
    executable = expected_python
    arguments = (
        None
        if expected_launcher is None
        else f'"{expected_launcher}" {module} --release-commit {release_commit}'
    )
    timeout = _one_directive(service_directives, "Service.TimeoutStartSec")
    scheduler_contract_ok = bool(
        service_contract_ok
        and timeout is not None
        and _systemd_duration_seconds(timeout) == _expected_timeout_seconds(module)
        and properties["FragmentPath"] == f"/etc/systemd/system/{unit}"
        and properties["UnitFileState"]
        == ("enabled" if module in _RESIDENT_STREAM_MODULES else "static")
        and properties["DropInPaths"] == ""
        and properties["NeedDaemonReload"] == "no"
    )
    if timer_properties is not None:
        scheduler_contract_ok = bool(
            scheduler_contract_ok
            and timer_directives is not None
            and timer_properties["FragmentPath"]
            == f"/etc/systemd/system/{timer_properties['Id']}"
            and timer_properties["UnitFileState"] == "enabled"
            and timer_properties["DropInPaths"] == ""
            and timer_properties["NeedDaemonReload"] == "no"
            and _systemd_timer_contract_ok(
                module=module,
                directives=timer_directives,
            )
        )
    return TaskSnapshot(
        name=task_name,
        state=state,
        last_run_ms=last_run_ms,
        last_result=last_result,
        # systemctl does not expose timer realtime fields in a locale-neutral
        # machine format on every supported systemd release. A not-yet-run
        # timer therefore remains fail-closed as NEVER_RAN rather than parsing
        # an ambiguous localized timestamp.
        next_run_ms=None,
        execute=executable,
        arguments=arguments,
        working_directory=_one_directive(
            service_directives, "Service.WorkingDirectory"
        ),
        scheduler_contract_ok=scheduler_contract_ok,
        configured_environment=configured_environment,
    )


def collect_systemd_units(
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[TaskSnapshot]:
    """Collect exact QUANTOS systemd metadata without invoking a shell."""

    list_result = runner(
        ["systemctl", "list-unit-files", "--no-legend", "--no-pager", "quantos-*"],
        capture_output=True,
        text=True,
        timeout=30,
        shell=False,
        check=False,
    )
    if list_result.returncode != 0:
        raise SchedulerInspectionError("systemd unit-file query failed")
    listed: dict[str, str] = {}
    support_units: set[str] = set()
    for line in list_result.stdout.splitlines():
        fields = line.split()
        if fields and fields[0] in _SYSTEMD_GENERIC_TEMPLATE_UNITS:
            raise SchedulerInspectionError(
                "generic systemd L2 templates must not be installed"
            )
        if len(fields) >= 2 and fields[0] in SYSTEMD_NON_SCHEDULED_SUPPORT_UNITS:
            if fields[0] in support_units:
                raise SchedulerInspectionError(
                    "systemd returned duplicate support-unit metadata"
                )
            support_units.add(fields[0])
            continue
        if len(fields) < 2 or _SYSTEMD_UNIT.fullmatch(fields[0]) is None:
            raise SchedulerInspectionError("systemd has an unexpected QUANTOS unit")
        if fields[0] in listed:
            raise SchedulerInspectionError("systemd returned duplicate unit metadata")
        listed[fields[0]] = fields[1]
    runtime_result = runner(
        ["systemctl", "list-units", "--all", "--no-legend", "--no-pager", "--plain", "quantos-*"],
        capture_output=True,
        text=True,
        timeout=30,
        shell=False,
        check=False,
    )
    if runtime_result.returncode != 0:
        raise SchedulerInspectionError("systemd runtime-unit query failed")
    for line in runtime_result.stdout.splitlines():
        fields = line.split(maxsplit=4)
        if len(fields) < 4:
            raise SchedulerInspectionError("systemd returned invalid runtime metadata")
        unit, _load, active, _sub = fields[:4]
        if unit in _SYSTEMD_GENERIC_TEMPLATE_UNITS:
            raise SchedulerInspectionError(
                "generic systemd L2 templates must not be installed"
            )
        if unit in SYSTEMD_NON_SCHEDULED_SUPPORT_UNITS:
            continue
        if _SYSTEMD_UNIT.fullmatch(unit) is None:
            raise SchedulerInspectionError("systemd has an unexpected QUANTOS unit")
        listed.setdefault(unit, "runtime")
    service_units = sorted(
        unit for unit in listed if unit.endswith(".service")
    )
    for timer in sorted(unit for unit in listed if unit.endswith(".timer")):
        match = _SYSTEMD_UNIT.fullmatch(timer)
        if match is None or match.group("stream") or match.group("kind") == "service":
            raise SchedulerInspectionError("systemd timer is outside the L2 policy")
        module = (
            "l2_scheduler_watchdog"
            if match.group("watchdog")
            else match.group("module")
        )
        if module is None or module not in _POLICY_TASK_BY_MODULE:
            raise SchedulerInspectionError(
                "systemd module does not map to one watchdog task"
            )
        expected_service = (
            "quantos-l2-watchdog.service"
            if module == "l2_scheduler_watchdog"
            else f"quantos-l2@{module}.service"
        )
        if expected_service not in listed:
            raise SchedulerInspectionError(
                "systemd timer has no corresponding L2 service"
            )
    snapshots: list[TaskSnapshot] = []
    for unit in service_units:
        module = _systemd_module(unit)
        if module not in _POLICY_TASK_BY_MODULE:
            raise SchedulerInspectionError(
                "systemd module does not map to one watchdog task"
            )
        denied_policy_module = module in _DENIED_POLICY_MODULES
        command = ["systemctl", "show", "--all", "--no-pager", "--timestamp=unix", unit]
        for name in _SYSTEMD_SHOW_PROPERTIES:
            command.extend(("--property", name))
        result = runner(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
            check=False,
        )
        if result.returncode != 0:
            raise SchedulerInspectionError("systemd unit metadata query failed")
        allowed_load_states = (
            frozenset({"loaded", "masked"})
            if denied_policy_module
            else frozenset({"loaded"})
        )
        properties = _parse_systemd_properties(
            result.stdout,
            unit,
            allowed_load_states=allowed_load_states,
        )
        if denied_policy_module:
            service_directives: dict[str, tuple[str, ...]] = {}
        else:
            cat_result = runner(
                ["systemctl", "cat", "--no-pager", unit],
                capture_output=True,
                text=True,
                timeout=30,
                shell=False,
                check=False,
            )
            if cat_result.returncode != 0:
                raise SchedulerInspectionError("systemd unit definition query failed")
            service_directives = _parse_systemd_unit_text(cat_result.stdout)
        confirmed_result = runner(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
            check=False,
        )
        if confirmed_result.returncode != 0:
            raise SchedulerInspectionError("systemd unit metadata confirmation failed")
        if _parse_systemd_properties(
            confirmed_result.stdout,
            unit,
            allowed_load_states=allowed_load_states,
        ) != properties:
            raise SchedulerInspectionError("systemd unit metadata changed during inspection")
        match = _SYSTEMD_UNIT.fullmatch(unit)
        if match is None or match.group("stream"):
            timer = None
        elif match.group("watchdog"):
            timer = "quantos-l2-watchdog.timer"
        else:
            timer = unit[:-8] + ".timer"
        timer_properties = None
        timer_directives = None
        if timer is not None and timer in listed:
            timer_command = [
                "systemctl", "show", "--all", "--no-pager", "--timestamp=unix", timer
            ]
            for name in _SYSTEMD_TIMER_PROPERTIES:
                timer_command.extend(("--property", name))
            timer_result = runner(
                timer_command,
                capture_output=True,
                text=True,
                timeout=30,
                shell=False,
                check=False,
            )
            if timer_result.returncode != 0:
                raise SchedulerInspectionError("systemd timer metadata query failed")
            timer_properties = _parse_systemd_properties(
                timer_result.stdout,
                timer,
                _SYSTEMD_TIMER_PROPERTIES,
                allowed_load_states=allowed_load_states,
            )
            if not denied_policy_module:
                timer_cat_result = runner(
                    ["systemctl", "cat", "--no-pager", timer],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    shell=False,
                    check=False,
                )
                if timer_cat_result.returncode != 0:
                    raise SchedulerInspectionError("systemd timer definition query failed")
                timer_directives = _parse_systemd_unit_text(timer_cat_result.stdout)
            timer_confirmed_result = runner(
                timer_command,
                capture_output=True,
                text=True,
                timeout=30,
                shell=False,
                check=False,
            )
            if timer_confirmed_result.returncode != 0:
                raise SchedulerInspectionError(
                    "systemd timer metadata confirmation failed"
                )
            if (
                _parse_systemd_properties(
                    timer_confirmed_result.stdout,
                    timer,
                    _SYSTEMD_TIMER_PROPERTIES,
                    allowed_load_states=allowed_load_states,
                )
                != timer_properties
            ):
                raise SchedulerInspectionError(
                    "systemd timer metadata changed during inspection"
                )
        snapshots.append(
            _systemd_snapshot(
                unit,
                properties,
                timer_properties=timer_properties,
                service_directives=service_directives,
                timer_directives=timer_directives,
            )
        )
    # ``systemctl show`` renders calendar timestamps using the host locale even
    # with ``--timestamp=unix``. ``list-timers --output=json`` is the supported
    # machine interface and returns epoch microseconds, so it can distinguish a
    # newly installed timer that has not reached its first trigger from a task
    # that missed an already-due trigger. This avoids deterministic NEVER_RAN
    # breaches immediately after an otherwise healthy release cutover.
    timer_result = runner(
        [
            "systemctl",
            "list-timers",
            "--all",
            "--no-pager",
            "--output=json",
            "quantos-*",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        shell=False,
        check=False,
    )
    if timer_result.returncode != 0:
        raise SchedulerInspectionError("systemd timer schedule query failed")
    try:
        timer_rows = json.loads(timer_result.stdout)
    except (TypeError, ValueError) as exc:
        raise SchedulerInspectionError("systemd returned invalid timer schedule metadata") from exc
    if not isinstance(timer_rows, list):
        raise SchedulerInspectionError("systemd returned invalid timer schedule metadata")
    next_run_by_task: dict[str, int | None] = {}
    for row in timer_rows:
        if not isinstance(row, dict):
            raise SchedulerInspectionError("systemd returned invalid timer schedule metadata")
        unit = row.get("unit")
        activates = row.get("activates")
        if not isinstance(unit, str) or _SYSTEMD_UNIT.fullmatch(unit) is None:
            raise SchedulerInspectionError("systemd returned an unexpected timer schedule")
        match = _SYSTEMD_UNIT.fullmatch(unit)
        assert match is not None
        if match.group("kind") == "service" or match.group("stream"):
            raise SchedulerInspectionError("systemd returned an unexpected timer schedule")
        module = "l2_scheduler_watchdog" if match.group("watchdog") else match.group("module")
        task_name = None if module is None else _POLICY_TASK_BY_MODULE.get(module)
        expected_service = (
            "quantos-l2-watchdog.service"
            if module == "l2_scheduler_watchdog"
            else f"quantos-l2@{module}.service"
        )
        if task_name is None or activates != expected_service or task_name in next_run_by_task:
            raise SchedulerInspectionError("systemd timer schedule metadata is inconsistent")
        next_value = row.get("next")
        if isinstance(next_value, bool) or not isinstance(next_value, int) or next_value < 0:
            raise SchedulerInspectionError("systemd timer next-run metadata is invalid")
        next_run_by_task[task_name] = None if next_value == 0 else next_value // 1_000
    return [
        replace(snapshot, next_run_ms=next_run_by_task.get(snapshot.name))
        for snapshot in snapshots
    ]


def backup_configuration_ready(
    *,
    repo_root: str | Path,
    passphrase: str | None = None,
    passphrase_file: str | None = None,
    destination: str | None = None,
) -> bool:
    passphrase = (
        _configured_environment_value("QUANTOS_BACKUP_PASSPHRASE")
        if passphrase is None
        else passphrase
    )
    passphrase_file = (
        (
            _configured_environment_value("QUANTOS_BACKUP_PASSPHRASE_FILE")
            or l2_backup._default_passphrase_file()
        )
        if passphrase_file is None
        else passphrase_file
    )
    destination = (
        _configured_environment_value("QUANTOS_BACKUP_DEST")
        if destination is None
        else destination
    )
    try:
        l2_backup.resolve_backup_passphrase(
            environment_passphrase=passphrase,
            passphrase_file=passphrase_file,
        )
    except l2_backup.L2BackupError:
        return False
    if not destination:
        return False
    destination_path = Path(destination)
    if not destination_path.is_absolute():
        return False
    try:
        metadata = destination_path.lstat()
        resolved = destination_path.resolve(strict=True)
    except OSError:
        return False
    if not stat.S_ISDIR(metadata.st_mode) or destination_path.is_symlink():
        return False
    destination_class = l2_backup.classify_destination(Path(repo_root).resolve(), resolved)
    return destination_class in l2_backup._EXTERNAL_DESTINATION_CLASSES


def backup_destination_ready(
    *, repo_root: str | Path, destination: str | None
) -> bool:
    """Prove the configured destination is still an external durable mount.

    This intentionally does not read the backup credential.  It lets the Linux
    watchdog detect an unmounted destination that has fallen back to a local
    directory without expanding secret access to the watchdog process.
    """

    if not destination:
        return False
    destination_path = Path(destination)
    if not destination_path.is_absolute():
        return False
    try:
        metadata = destination_path.lstat()
        resolved = destination_path.resolve(strict=True)
    except OSError:
        return False
    if not stat.S_ISDIR(metadata.st_mode) or destination_path.is_symlink():
        return False
    try:
        destination_class = l2_backup.classify_destination(
            Path(repo_root).resolve(), resolved
        )
    except OSError:
        return False
    return destination_class in l2_backup._EXTERNAL_DESTINATION_CLASSES


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
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError:
        return False
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        return False
    destination_class = l2_backup.classify_destination(Path(repo_root).resolve(), resolved)
    return destination_class in l2_backup._EXTERNAL_DESTINATION_CLASSES


_LAUNCHER_ARGUMENTS = re.compile(
    r'^"(?P<launcher>[^"]+)" (?P<module>[a-z0-9_]+) '
    r'--release-commit (?P<release_commit>[0-9a-f]{40})$'
)


def _action_release_commit(snapshot: TaskSnapshot | None) -> str | None:
    if snapshot is None:
        return None
    match = _LAUNCHER_ARGUMENTS.fullmatch((snapshot.arguments or "").strip())
    return None if match is None else match.group("release_commit")


def _action_matches(
    snapshot: TaskSnapshot,
    spec: ExpectedTask,
    state_root: Path,
    code_root: Path,
    expected_release_commit: str | None,
) -> bool:
    match = _LAUNCHER_ARGUMENTS.fullmatch((snapshot.arguments or "").strip())
    if (
        match is None
        or match.group("module") != spec.module
        or expected_release_commit is None
        or match.group("release_commit") != expected_release_commit
    ):
        return False
    try:
        expected_python = Path(
            f"/opt/quantos/venvs/{expected_release_commit}/bin/python"
        ).resolve(strict=False)
        actual_python = Path(snapshot.execute or "").resolve(strict=False)
        launcher = Path(match.group("launcher")).resolve(strict=False)
        expected_launcher = (
            code_root / "tools" / "l2_schedule_entrypoint.py"
        ).resolve(strict=False)
    except OSError:
        return False
    if actual_python != expected_python or launcher != expected_launcher:
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
    expected_release_commit: str | None,
    self_check: bool = False,
    resident_health: dict | None = None,
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
    if not snapshot.scheduler_contract_ok:
        return {
            "task_name": name,
            "status": "BREACH",
            "code": "SCHEDULER_CONTRACT_DRIFT",
            "detail": "systemd safety, cadence, timeout, mount, or credential metadata drifted",
        }
    if not _action_matches(
        snapshot,
        spec,
        repo_root,
        code_root,
        expected_release_commit,
    ):
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
    if name in _RESIDENT_HEALTH_PATHS and snapshot.state == "Running":
        selected_health = None
        if isinstance(resident_health, dict):
            task_health = resident_health.get(name)
            if isinstance(task_health, dict):
                selected_health = task_health
            elif name == "QUANTOS_binance_realtime_stream":
                # Backward-compatible direct Binance receipt used by callers
                # and retained evidence generated before the second stream.
                selected_health = resident_health
        generated_at_ms = (
            selected_health.get("generated_at_ms")
            if isinstance(selected_health, dict)
            else None
        )
        resilience = (
            selected_health.get("resilience")
            if isinstance(selected_health, dict)
            and name == "QUANTOS_bybit_realtime_stream"
            else None
        )
        resilience_healthy = bool(
            name != "QUANTOS_bybit_realtime_stream"
            or (
                isinstance(resilience, dict)
                and isinstance(resilience.get("head_seq"), int)
                and not isinstance(resilience.get("head_seq"), bool)
                and resilience["head_seq"] > 0
                and isinstance(resilience.get("head_hash"), str)
                and re.fullmatch(r"[0-9a-f]{64}", resilience["head_hash"])
                is not None
                and resilience.get("chain_valid") is True
            )
        )
        healthy = bool(
            isinstance(generated_at_ms, int)
            and not isinstance(generated_at_ms, bool)
            and 0 <= now_ms - generated_at_ms <= 30_000
            and selected_health.get("verdict") == "PASS"
            and selected_health.get("streaming") is True
            and isinstance(selected_health.get("safety"), dict)
            and selected_health["safety"].get("credentials") is False
            and selected_health["safety"].get("private_stream") is False
            and selected_health["safety"].get("broker_connectivity") is False
            and selected_health["safety"].get("order_submission") is False
            and selected_health["safety"].get("live_trading") is False
            and resilience_healthy
        )
        if not healthy:
            return {
                "task_name": name,
                "status": "BREACH",
                "code": "RESIDENT_HEALTH_STALE",
                "detail": "resident public stream lacks a fresh fail-closed semantic health receipt",
            }
        return {
            "task_name": name,
            "status": "PASS",
            "code": "RUNNING_HEALTHY",
            "detail": "resident public stream has a fresh safe semantic health receipt",
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
    if health is None:
        return check
    verdict = health.get("verdict")
    reasons = health.get("reason_codes")
    if (
        check["status"] == "BREACH"
        and check["code"] == "RESULT_15"
        and health.get("overdue_open_count") == 0
    ):
        return {
            "task_name": "QUANTOS_backup",
            "status": "PARTIAL",
            "code": "RESTORE_HISTORY_BACKUP_INTERRUPTED",
            "detail": (
                "the backup was terminated before completion; the external "
                "destination remains configured and the scheduled retry is retained"
            ),
        }
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
    # A scheduler breach already carries the stronger operational signal.  A
    # pre-first-trigger PARTIAL, however, must not mask unhealthy durable
    # restore evidence from an earlier deployment/release.
    if check["status"] == "BREACH":
        return check
    if check["code"] == "RUNNING" and reason == "NO_ATTEMPTS":
        return {
            "task_name": "QUANTOS_backup",
            "status": "PARTIAL",
            "code": "RESTORE_HISTORY_BACKUP_IN_PROGRESS",
            "detail": "the first recorded backup attempt is still in progress",
        }
    if (
        check["code"] == "RUNNING"
        and valid_reasons
        and health.get("overdue_open_count") == 0
    ):
        return {
            "task_name": "QUANTOS_backup",
            "status": "PARTIAL",
            "code": "RESTORE_HISTORY_RECOVERY_IN_PROGRESS",
            "detail": "a bounded full backup and isolated restore drill is in progress",
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
    if (
        valid_reasons
        and set(reasons).issubset(_RESTORE_HISTORY_RECOVERED_REASONS)
        and "FAILURE_IN_CLEAN_LOOKBACK" in reasons
        and health.get("latest_terminal_verdict") == "PASS"
        and isinstance(health.get("success_count"), int)
        and health["success_count"] > 0
        and health.get("overdue_open_count") == 0
    ):
        return {
            "task_name": "QUANTOS_backup",
            "status": "PARTIAL",
            "code": "RESTORE_HISTORY_RECOVERED_AFTER_FAILURE",
            "detail": (
                "the latest external restore drill passed, but a recent failed "
                "attempt remains visible during the clean-lookback window"
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
    resident_health: dict | None = None,
    task_release_pins: Mapping[str, str] | None = None,
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
    expected_release_commit = _action_release_commit(
        by_name.get("QUANTOS_scheduler_watchdog")
    )
    release_pins = dict(task_release_pins or {})
    if any(
        name not in EXPECTED_TASKS
        or name == "QUANTOS_scheduler_watchdog"
        or re.fullmatch(r"[0-9a-f]{40}", commit) is None
        for name, commit in release_pins.items()
    ):
        raise ValueError("task release pins must name known non-watchdog tasks and exact commits")
    if release_pins and (
        expected_release_commit is None
        or release_root.name != expected_release_commit
    ):
        raise ValueError(
            "task release pins require a watchdog code root named by its commit"
        )

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
    if set(conditional_readiness) != _CONDITIONAL_TASK_NAMES:
        raise RuntimeError("watchdog conditional tasks do not match the static policy")
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
                        "detail": (
                            "conditional task is enabled without its prerequisite: "
                            f"{readiness[1]}"
                        ),
                    }
                )
            continue
        task_release_commit = release_pins.get(name, expected_release_commit)
        task_code_root = (
            release_root.parent / task_release_commit
            if name in release_pins and task_release_commit is not None
            else release_root
        )
        check = _check_task(
            snapshot,
            name=name,
            spec=spec,
            now_ms=now_ms,
            repo_root=root,
            code_root=task_code_root,
            expected_release_commit=task_release_commit,
            self_check=name == "QUANTOS_scheduler_watchdog",
            resident_health=resident_health,
        )
        if name == "QUANTOS_backup":
            check = _merge_backup_history(check, backup_history_health)
        checks.append(check)

    for name in QUARANTINED_TASKS:
        snapshot = by_name.get(name)
        if snapshot is None:
            checks.append(
                {
                    "task_name": name,
                    "status": "PASS",
                    "code": "QUARANTINED_NOT_REGISTERED",
                    "detail": "invalidated legacy task is not registered",
                }
            )
        elif snapshot.state == "Disabled":
            checks.append(
                {
                    "task_name": name,
                    "status": "PASS",
                    "code": "DISABLED_EXPECTED",
                    "detail": "invalidated legacy task remains disabled",
                }
            )
        else:
            code = (
                "QUARANTINED_PRESENT_STARTABLE"
                if snapshot.state == "Ready"
                else "QUARANTINED_TASK_RUNNING"
                if snapshot.state == "Running"
                else "QUARANTINED_TASK_ENABLED"
            )
            checks.append(
                {
                    "task_name": name,
                    "status": "BREACH",
                    "code": code,
                    "detail": (
                        "invalidated legacy task must be absent or masked and inactive; "
                        f"task state is {snapshot.state}"
                    ),
                }
            )

    # These authority-bearing or research/paper workers are not hard legacy
    # quarantine, but they are outside the safe MVP runtime profile.  Treat a
    # stale or manually enabled registration as a first-class breach rather
    # than letting it hide under the generic unexpected-task diagnostic.
    for name in DISABLED_MVP_TASKS:
        snapshot = by_name.get(name)
        if snapshot is None:
            checks.append(
                {
                    "task_name": name,
                    "status": "PASS",
                    "code": "MVP_DISABLED_NOT_REGISTERED",
                    "detail": "non-MVP task is not registered",
                }
            )
        elif snapshot.state == "Disabled":
            checks.append(
                {
                    "task_name": name,
                    "status": "PASS",
                    "code": "MVP_DISABLED_EXPECTED",
                    "detail": "non-MVP task remains disabled",
                }
            )
        else:
            code = (
                "MVP_DISABLED_PRESENT_STARTABLE"
                if snapshot.state == "Ready"
                else "MVP_DISABLED_TASK_RUNNING"
                if snapshot.state == "Running"
                else "MVP_DISABLED_TASK_ENABLED"
            )
            checks.append(
                {
                    "task_name": name,
                    "status": "BREACH",
                    "code": code,
                    "detail": (
                        "task is outside the static MVP profile and must be absent "
                        f"or masked and inactive; task state is {snapshot.state}"
                    ),
                }
            )
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
        if (
            name in EXPECTED_TASKS
            or name in QUARANTINED_TASKS
            or name in DISABLED_MVP_TASKS
            or snapshot.state == "Disabled"
        ):
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
    collector: Callable[[], list[TaskSnapshot]] | None = None,
    history_reader: Callable[..., dict] = read_restore_history_health,
    emit_fn: Callable | None = None,
    resident_health_reader: Callable[[Path], dict] | None = None,
) -> dict:
    observed_at_ms = int(time.time() * 1000) if now_ms is None else now_ms
    provider = os.environ.get("QUANTOS_SCHEDULER_PROVIDER", "").strip().casefold()
    if not provider:
        provider = "windows" if os.name == "nt" else "systemd"
    if provider not in {"windows", "systemd"}:
        raise ValueError("scheduler provider must be windows or systemd")
    if collector is None:
        collector = (
            collect_windows_tasks if provider == "windows" else collect_systemd_units
        )
    # The Linux watchdog must not receive the backup credential. The systemd
    # collector proves the backup unit's credential/mount metadata, and the
    # restore-history authority proves real encrypted backup/restore outcomes.
    if backup_ready is None and provider == "windows":
        backup_ready = backup_configuration_ready(repo_root=repo_root)
    if heartbeat_ready is None and provider == "windows":
        heartbeat_ready = external_heartbeat_configuration_ready()
    if event_seal_ready is None and provider == "windows":
        event_seal_ready = event_seal_configuration_ready(repo_root=repo_root)
    try:
        snapshots = collector()
        snapshot_by_name = {item.name: item for item in snapshots}
        if heartbeat_ready is None:
            heartbeat_snapshot = snapshot_by_name.get("QUANTOS_external_heartbeat")
            heartbeat_environment = dict(
                () if heartbeat_snapshot is None else heartbeat_snapshot.configured_environment
            )
            heartbeat_ready = bool(
                heartbeat_snapshot is not None
                and heartbeat_snapshot.scheduler_contract_ok
                and external_heartbeat_configuration_ready(
                    heartbeat_environment.get("QUANTOS_HEARTBEAT_URL")
                )
            )
        if event_seal_ready is None:
            seal_snapshots = tuple(
                snapshot_by_name.get(name)
                for name in (
                    "QUANTOS_domain_event_seal",
                    "QUANTOS_artifact_admission_seal",
                )
            )
            destinations = {
                dict(snapshot.configured_environment).get("QUANTOS_EVENT_SEAL_DEST")
                for snapshot in seal_snapshots
                if snapshot is not None
            }
            destination = next(iter(destinations)) if len(destinations) == 1 else None
            event_seal_ready = bool(
                all(
                    snapshot is not None and snapshot.scheduler_contract_ok
                    for snapshot in seal_snapshots
                )
                and event_seal_configuration_ready(
                    repo_root=repo_root,
                    destination=destination,
                )
            )
        if backup_ready is None:
            backup_snapshot = next(
                (item for item in snapshots if item.name == "QUANTOS_backup"), None
            )
            backup_environment = dict(
                ()
                if backup_snapshot is None
                else backup_snapshot.configured_environment
            )
            backup_ready = bool(
                backup_snapshot is not None
                and backup_snapshot.state in {"Ready", "Running"}
                and backup_snapshot.scheduler_contract_ok
                and backup_destination_ready(
                    repo_root=repo_root,
                    destination=backup_environment.get("QUANTOS_BACKUP_DEST"),
                )
            )
        # Scheduler collection can take several seconds on a busy host.  Use a
        # post-collection clock for restore-history age checks instead of the
        # invocation clock captured before the metadata inspection began.
        if now_ms is None:
            observed_at_ms = int(time.time() * 1000)
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
                    policy_root=code_root,
                )
            except Exception:
                history_health = {
                    "verdict": "BREACH",
                    "reason_codes": ("RESTORE_HISTORY_INSPECTION_FAILED",),
                }
        if resident_health_reader is None:
            def resident_health_reader(path: Path) -> dict:
                return load_json_without_duplicate_keys(path.read_bytes())
        resident_health: dict[str, dict] = {}
        for task_name, filename in _RESIDENT_HEALTH_PATHS.items():
            try:
                resident_health[task_name] = resident_health_reader(
                    Path(repo_root) / "data" / "cache" / filename
                )
            except Exception:
                continue
        # The resident stream publishes concurrently with this oneshot.  Read
        # its receipt first and only then capture the evaluation clock; using
        # the invocation clock can misclassify a valid receipt published during
        # scheduler inspection as being from the future.
        if now_ms is None:
            observed_at_ms = int(time.time() * 1000)
        task_release_pins: dict[str, str] = {}
        if provider == "systemd":
            attention_release_commit = os.environ.get(
                _ATTENTION_RELEASE_ENVIRONMENT, ""
            ).strip()
            if attention_release_commit:
                task_release_pins[
                    "QUANTOS_wikimedia_attention_forward_refresh"
                ] = attention_release_commit
        result = evaluate_schedule(
            snapshots,
            now_ms=observed_at_ms,
            repo_root=repo_root,
            code_root=code_root,
            backup_ready=backup_ready,
            backup_history_health=history_health,
            heartbeat_ready=heartbeat_ready,
            event_seal_ready=event_seal_ready,
            resident_health=resident_health,
            task_release_pins=task_release_pins,
        )
    except Exception as exc:
        if backup_ready is None:
            backup_ready = False
        if heartbeat_ready is None:
            heartbeat_ready = False
        if event_seal_ready is None:
            event_seal_ready = False
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
                    "task_name": (
                        "WINDOWS_TASK_SCHEDULER"
                        if provider == "windows"
                        else "SYSTEMD_SCHEDULER"
                    ),
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
