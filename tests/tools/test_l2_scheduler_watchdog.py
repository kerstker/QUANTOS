from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

import tools.l2_scheduler_watchdog as watchdog
from services.governance.l2_restore_history import (
    L2RestoreHistoryStore,
    RestoreAttempt,
    RestoreOutcome,
    RestoreProof,
)
from tools.l2_scheduler_watchdog import (
    EXPECTED_TASKS,
    SchedulerInspectionError,
    TaskSnapshot,
    collect_systemd_units,
    collect_windows_tasks,
    evaluate_schedule,
    read_restore_history_health,
    run_scheduler_watchdog,
)


NOW_MS = 1_784_044_800_000
RELEASE_COMMIT = "a" * 40
CONTRACT = {
    "version": 2,
    "sources": [
        {
            "relative_path": "data/cache/l2_restore_history.db",
            "required": True,
            "role": "l2_restore_history_authority",
        }
    ],
}
CONTRACT_SHA256 = hashlib.sha256(
    (
        json.dumps(CONTRACT, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
).hexdigest()


def _launcher_arguments(code_root: Path, module: str) -> str:
    launcher = code_root / "tools" / "l2_schedule_entrypoint.py"
    return f'"{launcher}" {module} --release-commit {RELEASE_COMMIT}'


def _healthy_snapshot(
    repo: Path, name: str, *, code_root: Path | None = None
) -> TaskSnapshot:
    spec = EXPECTED_TASKS[name]
    release = repo if code_root is None else code_root
    return TaskSnapshot(
        name=name,
        state="Ready",
        last_run_ms=NOW_MS - min(spec.max_age_ms // 2, 3_600_000),
        last_result=0,
        next_run_ms=NOW_MS + 3_600_000,
        execute=f"/opt/quantos/venvs/{RELEASE_COMMIT}/bin/python",
        arguments=_launcher_arguments(release, spec.module),
        working_directory=str(repo),
    )


def _healthy_tasks(
    repo: Path,
    *,
    include_backup: bool,
    include_external: bool = False,
) -> list[TaskSnapshot]:
    conditional_external = {
        "QUANTOS_external_heartbeat",
        "QUANTOS_domain_event_seal",
        "QUANTOS_artifact_admission_seal",
    }
    return [
        _healthy_snapshot(repo, name)
        for name in EXPECTED_TASKS
        if include_backup or name != "QUANTOS_backup"
        if include_external or name not in conditional_external
    ]


def _write_restore_policy(repo: Path, **overrides: int) -> None:
    args = {
        "history_path": "data/cache/l2_restore_history.db",
        "expected_source_contract": CONTRACT,
        "max_attempt_age_ms": 10_000,
        "max_success_age_ms": 10_000,
        "max_attempt_gap_ms": 10_000,
        "max_success_gap_ms": 10_000,
        "minimum_history_span_ms": 0,
        "minimum_successes": 1,
        "clean_lookback_ms": 10_000,
        "attempt_timeout_ms": 1_000,
    }
    args.update(overrides)
    path = repo / "config" / "top_tier_requirements.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "requirements": [
                    {
                        "id": "OPS.RESTORE.DRILL",
                        "checker": "l2_restore_history_evidence",
                        "checker_args": args,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _proof(*, passing: bool) -> RestoreProof:
    return RestoreProof(
        source_contract_sha256=CONTRACT_SHA256,
        destination_class="SEPARATE_VOLUME" if passing else "UNVERIFIED",
        external_destination_verified=passing,
        encrypted=passing,
        manifest_verified=passing,
        source_contract_verified=passing,
        restore_verified=passing,
        sqlite_integrity_verified=passing,
        restore_elapsed_ms=10 if passing else None,
        source_age_ms=10 if passing else None,
        rto_met=passing,
        rpo_met=passing,
        batch_sha256="b" * 64 if passing else "",
    )


def _record_history_outcome(
    store: L2RestoreHistoryStore,
    *,
    attempt_id: str,
    started_at_ms: int,
    passing: bool,
) -> None:
    attempt = RestoreAttempt(
        attempt_id=attempt_id,
        started_at_ms=started_at_ms,
        operation_kind="BACKUP_AND_ISOLATED_RESTORE",
        mode="L2_AUTHORITY_STATE",
        source_contract_sha256=CONTRACT_SHA256,
        destination_ref_sha256="d" * 64,
        rto_ms=300_000,
        max_rpo_ms=86_400_000,
    )
    started, _ = store.begin_attempt(
        attempt, expected_head=store.authority_head()
    )
    outcome = RestoreOutcome(
        attempt_id=attempt_id,
        completed_at_ms=started_at_ms + 10,
        verdict="PASS" if passing else "FAIL",
        stage="COMPLETE" if passing else "RESTORE_VERIFICATION",
        reason_code="RESTORE_VERIFIED" if passing else "RESTORE_INTEGRITY_FAILED",
        proof=_proof(passing=passing),
    )
    store.complete_attempt(
        outcome,
        expected_start=started,
        expected_head=store.authority_head(),
    )


def _history_store(repo: Path) -> L2RestoreHistoryStore:
    path = repo / "data" / "cache" / "l2_restore_history.db"
    return L2RestoreHistoryStore(str(path), create=True, clock_ms=lambda: NOW_MS)


def test_backup_configuration_accepts_strong_owner_only_passphrase_file(
    tmp_path, monkeypatch
):
    secret = tmp_path / "backup.passphrase"
    secret.write_text("institutional-backup-passphrase\n", encoding="utf-8")
    (tmp_path / "external").mkdir()
    monkeypatch.delenv("QUANTOS_BACKUP_PASSPHRASE", raising=False)
    monkeypatch.delenv("QUANTOS_BACKUP_PASSPHRASE_FILE", raising=False)
    monkeypatch.setattr(watchdog, "_configured_environment_value", lambda _name: None)
    monkeypatch.setattr(watchdog.l2_backup, "_is_posix_platform", lambda: False)
    monkeypatch.setattr(
        watchdog.l2_backup,
        "classify_destination",
        lambda *_args: "SEPARATE_FILESYSTEM",
    )

    assert watchdog.backup_configuration_ready(
        repo_root=tmp_path / "repo",
        passphrase_file=str(secret),
        destination=str(tmp_path / "external"),
    )


def test_backup_configuration_rejects_ambiguous_passphrase_sources(
    tmp_path, monkeypatch
):
    secret = tmp_path / "backup.passphrase"
    secret.write_text("institutional-backup-passphrase", encoding="utf-8")
    monkeypatch.delenv("QUANTOS_BACKUP_PASSPHRASE", raising=False)
    monkeypatch.delenv("QUANTOS_BACKUP_PASSPHRASE_FILE", raising=False)
    monkeypatch.setattr(watchdog, "_configured_environment_value", lambda _name: None)
    monkeypatch.setattr(watchdog.l2_backup, "_is_posix_platform", lambda: False)
    monkeypatch.setattr(
        watchdog.l2_backup,
        "classify_destination",
        lambda *_args: "SEPARATE_FILESYSTEM",
    )

    assert not watchdog.backup_configuration_ready(
        repo_root=tmp_path / "repo",
        passphrase="institutional-backup-passphrase",
        passphrase_file=str(secret),
        destination=str(tmp_path / "external"),
    )


def test_backup_configuration_rejects_invalid_passphrase_file_without_raising(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("QUANTOS_BACKUP_PASSPHRASE", raising=False)
    monkeypatch.delenv("QUANTOS_BACKUP_PASSPHRASE_FILE", raising=False)
    monkeypatch.setattr(watchdog, "_configured_environment_value", lambda _name: None)
    monkeypatch.setattr(watchdog.l2_backup, "_is_posix_platform", lambda: False)

    assert not watchdog.backup_configuration_ready(
        repo_root=tmp_path / "repo",
        passphrase_file=str(tmp_path / "missing.passphrase"),
        destination=str(tmp_path / "external"),
    )


def test_event_seal_configuration_accepts_posix_separate_filesystem(
    tmp_path, monkeypatch
):
    (tmp_path / "external").mkdir()
    monkeypatch.setattr(
        watchdog.l2_backup,
        "classify_destination",
        lambda *_args: "SEPARATE_FILESYSTEM",
    )

    assert watchdog.event_seal_configuration_ready(
        repo_root=tmp_path / "repo",
        destination=str(tmp_path / "external"),
    )


def test_event_seal_configuration_rejects_unproved_destination(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        watchdog.l2_backup,
        "classify_destination",
        lambda *_args: "LOCAL_ONLY",
    )

    assert not watchdog.event_seal_configuration_ready(
        repo_root=tmp_path / "repo",
        destination=str(tmp_path / "external"),
    )


def test_schedule_evaluation_passes_only_when_every_ready_task_matches_contract(tmp_path):
    result = evaluate_schedule(
        _healthy_tasks(tmp_path, include_backup=True, include_external=True),
        now_ms=NOW_MS,
        repo_root=tmp_path,
        backup_ready=True,
        heartbeat_ready=True,
        event_seal_ready=True,
    )

    assert result["verdict"] == "PASS"
    assert result["summary"] == {
        "PASS": len(EXPECTED_TASKS),
        "PARTIAL": 0,
        "UNAVAILABLE": 0,
        "BREACH": 0,
    }


def test_running_task_ignores_prior_overlap_result_until_current_instance_completes(
    tmp_path,
):
    snapshots = _healthy_tasks(
        tmp_path, include_backup=True, include_external=True
    )
    stream_index = next(
        index
        for index, row in enumerate(snapshots)
        if row.name == "QUANTOS_binance_realtime_stream"
    )
    prior = snapshots[stream_index]
    snapshots[stream_index] = TaskSnapshot(
        name=prior.name,
        state="Running",
        last_run_ms=prior.last_run_ms,
        last_result=2_147_946_720,
        next_run_ms=prior.next_run_ms,
        execute=prior.execute,
        arguments=prior.arguments,
        working_directory=prior.working_directory,
    )

    result = evaluate_schedule(
        snapshots,
        now_ms=NOW_MS,
        repo_root=tmp_path,
        backup_ready=True,
        heartbeat_ready=True,
        event_seal_ready=True,
        resident_health={
            "generated_at_ms": NOW_MS - 1_000,
            "verdict": "PASS",
            "streaming": True,
            "safety": {
                "credentials": False,
                "private_stream": False,
                "broker_connectivity": False,
            },
        },
    )

    check = next(
        row
        for row in result["checks"]
        if row["task_name"] == "QUANTOS_binance_realtime_stream"
    )
    assert check["status"] == "PASS"
    assert check["code"] == "RUNNING_HEALTHY"


def test_running_resident_stream_requires_fresh_semantic_health(tmp_path):
    snapshot = _healthy_snapshot(tmp_path, "QUANTOS_binance_realtime_stream")
    running = TaskSnapshot(
        name=snapshot.name,
        state="Running",
        last_run_ms=NOW_MS - 60_000,
        last_result=0,
        next_run_ms=None,
        execute=snapshot.execute,
        arguments=snapshot.arguments,
        working_directory=snapshot.working_directory,
    )
    result = evaluate_schedule(
        [running, _healthy_snapshot(tmp_path, "QUANTOS_scheduler_watchdog")],
        now_ms=NOW_MS,
        repo_root=tmp_path,
        backup_ready=False,
        resident_health={
            "generated_at_ms": NOW_MS - 31_000,
            "verdict": "PASS",
            "streaming": True,
            "safety": {
                "credentials": False,
                "private_stream": False,
                "broker_connectivity": False,
            },
        },
    )
    check = next(
        row for row in result["checks"]
        if row["task_name"] == "QUANTOS_binance_realtime_stream"
    )
    assert check["status"] == "BREACH"
    assert check["code"] == "RESIDENT_HEALTH_STALE"
    assert result["verdict"] == "BREACH"


def test_binance_low_frequency_workers_are_pinned_in_watchdog_contract():
    systemic = EXPECTED_TASKS["QUANTOS_binance_systemic_risk"]
    rpi = EXPECTED_TASKS["QUANTOS_binance_rpi_depth"]

    assert systemic.module == "binance_systemic_risk"
    assert systemic.max_age_ms == 90 * 60_000
    assert systemic.partial_results == frozenset({2})
    assert rpi.module == "binance_rpi_depth"
    assert rpi.max_age_ms == 3 * 3_600_000


def test_public_forward_data_workers_are_pinned_in_watchdog_contract():
    expected = {
        "QUANTOS_binance_book_depth_forward_refresh": (
            "binance_book_depth_forward_refresh",
            2 * 86_400_000,
        ),
        "QUANTOS_defillama_tvl_forward_refresh": (
            "defillama_tvl_forward_refresh",
            2 * 86_400_000,
        ),
        "QUANTOS_defillama_stablecoin_forward_refresh": (
            "defillama_stablecoin_forward_refresh",
            2 * 86_400_000,
        ),
        "QUANTOS_hyperliquid_public_forward_refresh": (
            "hyperliquid_public_forward_refresh",
            2 * 86_400_000,
        ),
        "QUANTOS_cftc_tff_forward_refresh": (
            "cftc_tff_forward_refresh",
            9 * 86_400_000,
        ),
    }

    assert {
        name: (EXPECTED_TASKS[name].module, EXPECTED_TASKS[name].max_age_ms)
        for name in expected
    } == expected


def test_resident_binance_stream_exit_is_stale_within_three_paper_cadences():
    stream = EXPECTED_TASKS["QUANTOS_binance_realtime_stream"]

    assert stream.module == "binance_realtime_stream"
    assert stream.max_age_ms == 15 * 60_000


def test_lifecycle_and_formal_shadow_workers_are_pinned_in_watchdog_contract():
    lifecycle = EXPECTED_TASKS["QUANTOS_alpha_lifecycle_cockpit"]
    formal_shadow = EXPECTED_TASKS["QUANTOS_realtime_alpha_formal_shadow"]

    assert lifecycle.module == "alpha_lifecycle_cockpit"
    assert lifecycle.max_age_ms == 15 * 60_000
    assert formal_shadow.module == "realtime_alpha_formal_shadow"
    assert formal_shadow.max_age_ms == 5 * 60_000
    assert formal_shadow.partial_results == frozenset({2})


def test_schedule_evaluation_accepts_distinct_release_and_state_roots(tmp_path):
    state_root = tmp_path / "state"
    code_root = tmp_path / "release"
    state_root.mkdir()
    code_root.mkdir()
    snapshots = [
        _healthy_snapshot(state_root, name, code_root=code_root)
        for name in EXPECTED_TASKS
    ]

    result = evaluate_schedule(
        snapshots,
        now_ms=NOW_MS,
        repo_root=state_root,
        code_root=code_root,
        backup_ready=True,
        heartbeat_ready=True,
        event_seal_ready=True,
    )

    assert result["verdict"] == "PASS"


def test_schedule_evaluation_rejects_legacy_direct_module_action(tmp_path):
    snapshots = _healthy_tasks(
        tmp_path, include_backup=True, include_external=True
    )
    snapshots = [
        TaskSnapshot(
            **{
                **item.__dict__,
                "arguments": "-m tools.domain_event_ops",
            }
        )
        if item.name == "QUANTOS_domain_event_ops"
        else item
        for item in snapshots
    ]

    result = evaluate_schedule(
        snapshots,
        now_ms=NOW_MS,
        repo_root=tmp_path,
        backup_ready=True,
        heartbeat_ready=True,
        event_seal_ready=True,
    )

    check = next(
        item
        for item in result["checks"]
        if item["task_name"] == "QUANTOS_domain_event_ops"
    )
    assert check["status"] == "BREACH"
    assert check["code"] == "ACTION_DRIFT"


def test_schedule_evaluation_rejects_task_pinned_to_different_release(tmp_path):
    snapshots = _healthy_tasks(
        tmp_path, include_backup=True, include_external=True
    )
    snapshots = [
        TaskSnapshot(
            **{
                **item.__dict__,
                "arguments": item.arguments.replace(RELEASE_COMMIT, "b" * 40),
            }
        )
        if item.name == "QUANTOS_domain_event_ops"
        else item
        for item in snapshots
    ]

    result = evaluate_schedule(
        snapshots,
        now_ms=NOW_MS,
        repo_root=tmp_path,
        backup_ready=True,
        heartbeat_ready=True,
        event_seal_ready=True,
    )

    check = next(
        item
        for item in result["checks"]
        if item["task_name"] == "QUANTOS_domain_event_ops"
    )
    assert check["status"] == "BREACH"
    assert check["code"] == "ACTION_DRIFT"


def test_schedule_evaluation_detects_missing_disabled_stale_and_action_drift(tmp_path):
    snapshots = _healthy_tasks(
        tmp_path, include_backup=True, include_external=True
    )
    snapshots = [item for item in snapshots if item.name != "QUANTOS_factory_loop"]
    snapshots = [
        TaskSnapshot(**{**item.__dict__, "state": "Disabled"})
        if item.name == "QUANTOS_ic_panel_refresh"
        else TaskSnapshot(**{**item.__dict__, "last_run_ms": NOW_MS - 10 * 86_400_000})
        if item.name == "QUANTOS_forward_track"
        else TaskSnapshot(
            **{
                **item.__dict__,
                "execute": "cmd.exe",
                "arguments": "/c python -m tools.alpha_maintenance_sweep --submit",
            }
        )
        if item.name == "QUANTOS_alpha_maintenance"
        else item
        for item in snapshots
    ]

    result = evaluate_schedule(
        snapshots,
        now_ms=NOW_MS,
        repo_root=tmp_path,
        backup_ready=True,
        heartbeat_ready=True,
        event_seal_ready=True,
    )

    assert result["verdict"] == "BREACH"
    statuses = {check["task_name"]: check["status"] for check in result["checks"]}
    assert statuses["QUANTOS_factory_loop"] == "BREACH"
    assert statuses["QUANTOS_ic_panel_refresh"] == "BREACH"
    assert statuses["QUANTOS_forward_track"] == "BREACH"
    assert statuses["QUANTOS_alpha_maintenance"] == "BREACH"


def test_unconfigured_backup_is_unavailable_but_enabled_legacy_action_is_breach(tmp_path):
    without_backup = _healthy_tasks(tmp_path, include_backup=False)
    unavailable = evaluate_schedule(
        without_backup,
        now_ms=NOW_MS,
        repo_root=tmp_path,
        backup_ready=False,
    )
    backup_check = next(
        check for check in unavailable["checks"] if check["task_name"] == "QUANTOS_backup"
    )
    assert unavailable["verdict"] == "PARTIAL"
    assert backup_check["status"] == "UNAVAILABLE"

    legacy = TaskSnapshot(
        name="QUANTOS_backup",
        state="Ready",
        last_run_ms=NOW_MS - 86_400_000,
        last_result=1,
        next_run_ms=NOW_MS + 86_400_000,
        execute="cmd.exe",
        arguments="/c python -m tools.live_backup",
        working_directory=None,
    )
    breached = evaluate_schedule(
        [*without_backup, legacy],
        now_ms=NOW_MS,
        repo_root=tmp_path,
        backup_ready=False,
    )
    backup_check = next(
        check for check in breached["checks"] if check["task_name"] == "QUANTOS_backup"
    )
    assert breached["verdict"] == "BREACH"
    assert backup_check["status"] == "BREACH"


def test_unconfigured_external_tasks_are_unavailable_and_enabled_tasks_breach(tmp_path):
    base = _healthy_tasks(tmp_path, include_backup=False)
    unavailable = evaluate_schedule(
        base,
        now_ms=NOW_MS,
        repo_root=tmp_path,
        backup_ready=False,
        heartbeat_ready=False,
        event_seal_ready=False,
    )
    statuses = {check["task_name"]: check["status"] for check in unavailable["checks"]}
    assert statuses["QUANTOS_external_heartbeat"] == "UNAVAILABLE"
    assert statuses["QUANTOS_domain_event_seal"] == "UNAVAILABLE"

    enabled = [
        *base,
        _healthy_snapshot(tmp_path, "QUANTOS_external_heartbeat"),
        _healthy_snapshot(tmp_path, "QUANTOS_domain_event_seal"),
    ]
    breached = evaluate_schedule(
        enabled,
        now_ms=NOW_MS,
        repo_root=tmp_path,
        backup_ready=False,
        heartbeat_ready=False,
        event_seal_ready=False,
    )
    statuses = {check["task_name"]: check["status"] for check in breached["checks"]}
    assert statuses["QUANTOS_external_heartbeat"] == "BREACH"
    assert statuses["QUANTOS_domain_event_seal"] == "BREACH"


def test_windows_collector_uses_fixed_powershell_command_without_shell(tmp_path):
    payload = [
        {
            "TaskName": "QUANTOS_domain_event_ops",
            "State": "Ready",
            "LastRunTimeUtc": "2026-07-14T15:00:00.0000000Z",
            "LastTaskResult": 2,
            "NextRunTimeUtc": "2026-07-14T17:00:00.0000000Z",
            "Execute": "C:/Python/python.exe",
            "Arguments": "-m tools.domain_event_ops",
            "WorkingDirectory": str(tmp_path),
        }
    ]
    calls = []

    class Completed:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    snapshots = collect_windows_tasks(runner=runner)

    assert snapshots[0].name == "QUANTOS_domain_event_ops"
    assert snapshots[0].last_result == 2
    command, kwargs = calls[0]
    assert command[:3] == ["powershell.exe", "-NoProfile", "-NonInteractive"]
    assert kwargs["shell"] is False
    assert kwargs["creationflags"] == (
        getattr(watchdog.subprocess, "CREATE_NO_WINDOW", 0)
        if watchdog.os.name == "nt"
        else 0
    )


def _systemd_properties(
    unit: str,
    *,
    active: str,
    unit_file_state: str,
    result: str = "success",
    status: int = 0,
    started: int = NOW_MS // 1_000 - 60,
    triggered: int = NOW_MS // 1_000 - 60,
    next_run: int = NOW_MS // 1_000 + 60,
) -> str:
    return "\n".join(
        (
            f"Id={unit}",
            "LoadState=loaded",
            f"ActiveState={active}",
            "SubState=running" if active == "active" else "SubState=dead",
            f"UnitFileState={unit_file_state}",
            f"Result={result}",
            f"ExecMainStatus={status}",
            f"ExecMainStartTimestamp=@{started}",
            f"ExecMainExitTimestamp=@{started + 1}",
            f"FragmentPath=/etc/systemd/system/{unit}",
            "DropInPaths=",
            "NeedDaemonReload=no",
            "",
        )
    )


def _systemd_timer_properties(
    module: str,
    *,
    triggered: int = NOW_MS // 1_000 - 60,
    next_run: int = NOW_MS // 1_000 + 60,
) -> str:
    unit = f"quantos-l2@{module}.timer"
    return "\n".join(
        (
            f"Id={unit}",
            "LoadState=loaded",
            "ActiveState=active",
            "SubState=waiting",
            "UnitFileState=enabled",
            f"FragmentPath=/etc/systemd/system/{unit}",
            "DropInPaths=",
            "NeedDaemonReload=no",
            "",
        )
    )
def _systemd_service_unit(module: str) -> str:
    release_root = f"/opt/quantos/releases/{RELEASE_COMMIT}"
    python = f"/opt/quantos/venvs/{RELEASE_COMMIT}/bin/python"
    launcher = f"{release_root}/tools/l2_schedule_entrypoint.py"
    timeout = {"domain_event_ops": "5m", "l2_backup": "6h"}.get(module, "5m")
    unit_lines = [
        "[Unit]",
        f"Description=QUANTOS test unit ({module})",
        "After=network-online.target",
        "Wants=network-online.target",
    ]
    if module == "l2_backup":
        unit_lines.append("RequiresMountsFor=/mnt/backup")
    service_lines = [
        "[Service]",
        "Type=" + ("simple" if module == "binance_realtime_stream" else "oneshot"),
        "User=quantos",
        "Group=quantos",
        "UMask=0077",
        "Environment=LIVE_TRADING_ENABLED=false",
        "Environment=REAL_BROKER_ENABLED=false",
        "Environment=ALLOW_ORDER_SUBMISSION=false",
        "Environment=ALLOW_AI_DIRECT_TRADING=false",
        "Environment=REQUIRE_HUMAN_APPROVAL=true",
        "Environment=QUANTOS_L2_DRY_RUN_ONLY=true",
        f"Environment=QUANTOS_RELEASE_ROOT={release_root}",
        f"Environment=QUANTOS_RELEASE_COMMIT={RELEASE_COMMIT}",
        "Environment=QUANTOS_STATE_ROOT=/srv/quantos/state",
        (
            "PassEnvironment=HTTP_PROXY HTTPS_PROXY FTP_PROXY NO_PROXY ALL_PROXY "
            "http_proxy https_proxy ftp_proxy no_proxy all_proxy"
        ),
    ]
    if module != "binance_realtime_stream":
        service_lines.append("SuccessExitStatus=2")
    if module == "l2_backup":
        service_lines.extend(
            (
                "Environment=QUANTOS_BACKUP_DEST=/mnt/backup",
                "LoadCredential=l2-backup-passphrase:/etc/quantos/credentials/l2-backup-passphrase",
            )
        )
    service_lines.extend(
        (
            "WorkingDirectory=/srv/quantos/state",
            f"ExecStart={python} {launcher} {module} --release-commit {RELEASE_COMMIT}",
            f"TimeoutStartSec={timeout}",
            "Restart=" + ("always" if module == "binance_realtime_stream" else "no"),
            "KillMode=control-group",
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "ProtectHome=true",
            "PrivateTmp=true",
            "PrivateDevices=true",
            "ProtectKernelTunables=true",
            "ProtectKernelModules=true",
            "ProtectControlGroups=true",
            "RestrictSUIDSGID=true",
            "LockPersonality=true",
            "RestrictRealtime=true",
            "CapabilityBoundingSet=",
            "AmbientCapabilities=",
            f"ReadOnlyPaths={release_root}",
            "ReadWritePaths=/srv/quantos/state",
        )
    )
    if module == "l2_backup":
        service_lines.append("ReadWritePaths=/mnt/backup")
    if module == "binance_realtime_stream":
        service_lines.extend(("RestartSec=30s", "RuntimeMaxSec=23h55m", "TimeoutStopSec=90s"))
    install = (
        ["[Install]", "WantedBy=multi-user.target"]
        if module == "binance_realtime_stream"
        else []
    )
    return "\n".join((*unit_lines, *service_lines, *install, ""))


def _systemd_timer_unit(module: str) -> str:
    schedule = {
        "domain_event_ops": "*-*-* *:01/15:00 Asia/Shanghai",
        "l2_backup": "*-*-* 03:00:00 Asia/Shanghai",
    }[module]
    return "\n".join(
        (
            "[Unit]",
            f"Description=QUANTOS test timer ({module})",
            "[Timer]",
            f"OnCalendar={schedule}",
            "Persistent=true",
            "AccuracySec=1s",
            "RandomizedDelaySec=0",
            f"Unit=quantos-l2@{module}.service",
            "[Install]",
            "WantedBy=timers.target",
            "",
        )
    )


class _SystemdCompleted:
    returncode = 0
    stderr = ""

    def __init__(self, stdout: str):
        self.stdout = stdout


def _systemd_runner(
    module: str,
    *,
    service_text: str | None = None,
    timer_text: str | None = None,
    service_metadata: str | None = None,
    timer_metadata: str | None = None,
    calls: list | None = None,
):
    stream = module == "binance_realtime_stream"
    service = (
        "quantos-binance-realtime-stream.service"
        if stream
        else f"quantos-l2@{module}.service"
    )
    timer = None if stream else f"quantos-l2@{module}.timer"
    service_text = service_text or _systemd_service_unit(module)
    timer_text = None if stream else (timer_text or _systemd_timer_unit(module))
    service_metadata = service_metadata or _systemd_properties(
        service,
        active="active" if stream else "inactive",
        unit_file_state="enabled" if stream else "static",
    )
    timer_metadata = None if stream else (
        timer_metadata or _systemd_timer_properties(module)
    )

    def runner(command, **kwargs):
        if calls is not None:
            calls.append((command, kwargs))
        if command[1] == "list-unit-files":
            listing = f"{service} {'enabled' if stream else 'static'}\n"
            if timer is not None:
                listing = f"{timer} enabled\n" + listing
            return _SystemdCompleted(listing)
        if command[1] == "list-units":
            return _SystemdCompleted("")
        if command[1] == "show":
            target = command[5]
            return _SystemdCompleted(
                timer_metadata if target == timer else service_metadata
            )
        if command[1] == "cat":
            return _SystemdCompleted(timer_text if command[-1] == timer else service_text)
        raise AssertionError(f"unexpected command: {command!r}")

    return runner


def test_systemd_collector_accepts_exact_ordinary_unit_and_uses_fixed_commands() -> None:
    calls = []
    snapshot = collect_systemd_units(
        runner=_systemd_runner("domain_event_ops", calls=calls)
    )[0]

    assert snapshot.name == "QUANTOS_domain_event_ops"
    assert snapshot.state == "Ready"
    assert snapshot.last_result == 0
    assert snapshot.last_run_ms == (NOW_MS // 1_000 - 60) * 1_000
    assert snapshot.next_run_ms is None
    assert snapshot.scheduler_contract_ok is True
    assert not {
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
    }.intersection(dict(snapshot.configured_environment))
    assert all(kwargs["shell"] is False for _command, kwargs in calls)
    assert calls[0][0][:4] == [
        "systemctl",
        "list-unit-files",
        "--no-legend",
        "--no-pager",
    ]
    assert [command[:2] for command, _kwargs in calls].count(
        ["systemctl", "cat"]
    ) == 2


def test_systemd_collector_treats_executing_oneshot_as_running() -> None:
    metadata = _systemd_properties(
        "quantos-l2@domain_event_ops.service",
        active="inactive",
        unit_file_state="static",
    ).replace(
        "ActiveState=inactive\nSubState=dead",
        "ActiveState=activating\nSubState=start",
    )

    snapshot = collect_systemd_units(
        runner=_systemd_runner("domain_event_ops", service_metadata=metadata)
    )[0]

    assert snapshot.state == "Running"
    assert snapshot.scheduler_contract_ok is True


def test_systemd_collector_accepts_exact_backup_boundary() -> None:
    snapshot = collect_systemd_units(runner=_systemd_runner("l2_backup"))[0]
    assert snapshot.name == "QUANTOS_backup"
    assert snapshot.state == "Ready"
    assert snapshot.scheduler_contract_ok is True
    assert dict(snapshot.configured_environment)["QUANTOS_BACKUP_DEST"] == "/mnt/backup"


def test_systemd_collector_accepts_exact_resident_stream() -> None:
    snapshot = collect_systemd_units(
        runner=_systemd_runner("binance_realtime_stream")
    )[0]
    assert snapshot.name == "QUANTOS_binance_realtime_stream"
    assert snapshot.state == "Running"
    assert snapshot.last_result == 0
    assert snapshot.scheduler_contract_ok is True


def test_systemd_collector_excludes_only_exact_non_scheduled_support_units() -> None:
    base_runner = _systemd_runner("domain_event_ops")
    support_units = (
        "quantos-console-api.service",
        "quantos-console-frontend.service",
        "quantos-proxy-environment.service",
    )

    def runner(command, **kwargs):
        result = base_runner(command, **kwargs)
        if command[1] == "list-unit-files":
            return _SystemdCompleted(
                result.stdout
                + "".join(f"{unit} enabled\n" for unit in support_units)
            )
        if command[1] == "list-units":
            return _SystemdCompleted(
                "".join(
                    f"{unit} loaded active running approved support unit\n"
                    for unit in support_units
                )
            )
        return result

    snapshots = collect_systemd_units(runner=runner)

    assert [snapshot.name for snapshot in snapshots] == [
        "QUANTOS_domain_event_ops"
    ]


@pytest.mark.parametrize(
    "replacement",
    (
        "",
        (
            "PassEnvironment=HTTP_PROXY HTTPS_PROXY FTP_PROXY NO_PROXY ALL_PROXY "
            "http_proxy https_proxy ftp_proxy no_proxy all_proxy AWS_SECRET_ACCESS_KEY"
        ),
        (
            "PassEnvironment=HTTP_PROXY HTTPS_PROXY FTP_PROXY NO_PROXY ALL_PROXY\n"
            "PassEnvironment=http_proxy https_proxy ftp_proxy no_proxy all_proxy"
        ),
    ),
)
def test_systemd_collector_rejects_missing_extra_or_split_proxy_name_contract(
    replacement: str,
) -> None:
    exact = (
        "PassEnvironment=HTTP_PROXY HTTPS_PROXY FTP_PROXY NO_PROXY ALL_PROXY "
        "http_proxy https_proxy ftp_proxy no_proxy all_proxy"
    )
    service = _systemd_service_unit("domain_event_ops").replace(
        exact,
        replacement,
        1,
    )
    snapshot = collect_systemd_units(
        runner=_systemd_runner("domain_event_ops", service_text=service)
    )[0]
    assert snapshot.scheduler_contract_ok is False


@pytest.mark.parametrize(
    "field,replacement",
    (
        (
            "ExecStart=/opt/quantos/venvs/",
            "ExecStart=/bin/evil\nExecStart=/opt/quantos/venvs/",
        ),
        (
            "ExecStart=/opt/quantos/venvs/",
            "ExecStartPre=/bin/evil\nExecStart=/opt/quantos/venvs/",
        ),
        (
            "ExecStart=/opt/quantos/venvs/",
            "ExecReload=/bin/evil\nExecStart=/opt/quantos/venvs/",
        ),
        (
            "WorkingDirectory=/srv/quantos/state",
            "BindPaths=/tmp:/srv/quantos/state\nWorkingDirectory=/srv/quantos/state",
        ),
        (
            "WorkingDirectory=/srv/quantos/state",
            "RootDirectory=/tmp/root\nWorkingDirectory=/srv/quantos/state",
        ),
        (
            "WorkingDirectory=/srv/quantos/state",
            "InaccessiblePaths=/\nWorkingDirectory=/srv/quantos/state",
        ),
        (
            "WorkingDirectory=/srv/quantos/state",
            "EnvironmentFile=/tmp/unsafe.env\nWorkingDirectory=/srv/quantos/state",
        ),
        (
            "Environment=QUANTOS_STATE_ROOT=/srv/quantos/state",
            "Environment=QUANTOS_STATE_ROOT=/srv/quantos/state\nEnvironment=LD_PRELOAD=/tmp/evil.so",
        ),
        (
            "Environment=QUANTOS_STATE_ROOT=/srv/quantos/state",
            "Environment=\nEnvironment=QUANTOS_STATE_ROOT=/srv/quantos/state",
        ),
        ("NoNewPrivileges=true", "NoNewPrivileges=false"),
        ("ProtectSystem=strict", "ProtectSystem=full"),
        ("SuccessExitStatus=2", "SuccessExitStatus=0"),
        ("PrivateDevices=true", "PrivateDevices=false"),
        ("CapabilityBoundingSet=", "CapabilityBoundingSet=CAP_NET_ADMIN"),
        (
            f"ReadOnlyPaths=/opt/quantos/releases/{RELEASE_COMMIT}",
            f"ReadOnlyPaths=/opt/quantos/releases/{RELEASE_COMMIT}\nReadOnlyPaths=/tmp",
        ),
        (
            "ReadWritePaths=/srv/quantos/state",
            "ReadWritePaths=/srv/quantos/state\nReadWritePaths=/tmp",
        ),
        (
            "WorkingDirectory=/srv/quantos/state",
            "LoadCredential=unexpected:/tmp/secret\nWorkingDirectory=/srv/quantos/state",
        ),
    ),
)
def test_systemd_collector_rejects_extra_worker_authority(
    field: str, replacement: str
) -> None:
    service = _systemd_service_unit("domain_event_ops").replace(field, replacement, 1)
    snapshot = collect_systemd_units(
        runner=_systemd_runner("domain_event_ops", service_text=service)
    )[0]
    assert snapshot.scheduler_contract_ok is False


@pytest.mark.parametrize(
    "field,replacement",
    (
        (
            "LoadCredential=l2-backup-passphrase:/etc/quantos/credentials/l2-backup-passphrase",
            "LoadCredential=wrong:/tmp/wrong",
        ),
        (
            "LoadCredential=l2-backup-passphrase:/etc/quantos/credentials/l2-backup-passphrase",
            "LoadCredential=l2-backup-passphrase:/etc/quantos/credentials/l2-backup-passphrase\n"
            "LoadCredential=extra:/tmp/wrong",
        ),
        ("RequiresMountsFor=/mnt/backup", "RequiresMountsFor=/srv/quantos/state"),
        ("QUANTOS_BACKUP_DEST=/mnt/backup", "QUANTOS_BACKUP_DEST=/srv/quantos/state"),
        ("ReadWritePaths=/mnt/backup", "ReadWritePaths=/tmp"),
    ),
)
def test_systemd_collector_rejects_backup_boundary_drift(
    field: str, replacement: str
) -> None:
    service = _systemd_service_unit("l2_backup").replace(field, replacement, 1)
    snapshot = collect_systemd_units(
        runner=_systemd_runner("l2_backup", service_text=service)
    )[0]
    assert snapshot.scheduler_contract_ok is False


@pytest.mark.parametrize(
    "field,replacement",
    (
        ("DropInPaths=", "DropInPaths=/etc/systemd/system/quantos.d/override.conf"),
        ("NeedDaemonReload=no", "NeedDaemonReload=yes"),
        (
            "FragmentPath=/etc/systemd/system/quantos-l2@domain_event_ops.service",
            "FragmentPath=/run/systemd/transient/quantos-l2@domain_event_ops.service",
        ),
    ),
)
def test_systemd_collector_rejects_origin_or_reload_drift(
    field: str, replacement: str
) -> None:
    unit = "quantos-l2@domain_event_ops.service"
    metadata = _systemd_properties(
        unit, active="inactive", unit_file_state="static"
    ).replace(field, replacement, 1)
    snapshot = collect_systemd_units(
        runner=_systemd_runner(
            "domain_event_ops", service_metadata=metadata
        )
    )[0]
    assert snapshot.scheduler_contract_ok is False


def test_systemd_collector_rejects_runtime_only_enablement() -> None:
    unit = "quantos-l2@domain_event_ops.timer"
    metadata = _systemd_timer_properties("domain_event_ops").replace(
        "UnitFileState=enabled", "UnitFileState=enabled-runtime", 1
    )
    snapshot = collect_systemd_units(
        runner=_systemd_runner("domain_event_ops", timer_metadata=metadata)
    )[0]
    assert snapshot.state == "Disabled"
    assert snapshot.scheduler_contract_ok is False


def test_systemd_collector_rejects_metadata_race() -> None:
    calls = []
    base_runner = _systemd_runner("domain_event_ops", calls=calls)

    def runner(command, **kwargs):
        result = base_runner(command, **kwargs)
        show_calls = sum(
            1
            for observed, _observed_kwargs in calls
            if observed[1] == "show" and observed[5].endswith(".service")
        )
        if command[1] == "show" and command[5].endswith(".service") and show_calls == 2:
            return _SystemdCompleted(
                result.stdout.replace("NeedDaemonReload=no", "NeedDaemonReload=yes")
            )
        return result

    with pytest.raises(SchedulerInspectionError, match="changed during inspection"):
        collect_systemd_units(runner=runner)


@pytest.mark.parametrize(
    "field,replacement",
    (
        (
            "OnCalendar=*-*-* *:01/15:00 Asia/Shanghai",
            "OnCalendar=*-*-* *:02/15:00 Asia/Shanghai",
        ),
        (
            "OnCalendar=*-*-* *:01/15:00 Asia/Shanghai",
            "OnCalendar=*-*-* *:01/15:00 Asia/Shanghai\nOnBootSec=1s",
        ),
        (
            "Unit=quantos-l2@domain_event_ops.service",
            "Unit=quantos-l2@paper_realtime.service",
        ),
    ),
)
def test_systemd_collector_rejects_timer_contract_drift(
    field: str, replacement: str
) -> None:
    timer = _systemd_timer_unit("domain_event_ops").replace(field, replacement, 1)
    snapshot = collect_systemd_units(
        runner=_systemd_runner("domain_event_ops", timer_text=timer)
    )[0]
    assert snapshot.scheduler_contract_ok is False


def test_backup_destination_ready_rechecks_external_mount(tmp_path, monkeypatch) -> None:
    destination = tmp_path / "backup"
    destination.mkdir()
    observed = []

    def classify(repo_root, destination_root):
        observed.append((repo_root, destination_root))
        return "SEPARATE_FILESYSTEM"

    monkeypatch.setattr(watchdog.l2_backup, "classify_destination", classify)
    assert watchdog.backup_destination_ready(
        repo_root=tmp_path / "state",
        destination=str(destination),
    )
    assert len(observed) == 1


def test_backup_destination_ready_fails_closed_after_unmount(tmp_path, monkeypatch) -> None:
    destination = tmp_path / "backup"
    destination.mkdir()
    monkeypatch.setattr(
        watchdog.l2_backup,
        "classify_destination",
        lambda *_args: "LOCAL_ONLY",
    )
    assert not watchdog.backup_destination_ready(
        repo_root=tmp_path / "state",
        destination=str(destination),
    )


def test_systemd_collector_rejects_unknown_enabled_quantos_unit() -> None:
    class Completed:
        returncode = 0
        stdout = "quantos-order-submit.service enabled\n"
        stderr = ""

    with pytest.raises(SchedulerInspectionError, match="unexpected enabled"):
        collect_systemd_units(runner=lambda *_args, **_kwargs: Completed())


def test_systemd_collector_rejects_malformed_or_failed_metadata() -> None:
    unit = "quantos-l2@domain_event_ops.service"

    class Completed:
        stderr = ""

        def __init__(self, *, returncode: int, stdout: str):
            self.returncode = returncode
            self.stdout = stdout

    responses = iter(
        (
            Completed(returncode=0, stdout=f"{unit} static\n"),
            Completed(returncode=0, stdout=""),
            Completed(returncode=0, stdout="Id=wrong.service\nLoadState=loaded\n"),
        )
    )
    with pytest.raises(SchedulerInspectionError, match="metadata"):
        collect_systemd_units(runner=lambda *_args, **_kwargs: next(responses))

    with pytest.raises(SchedulerInspectionError, match="query failed"):
        collect_systemd_units(
            runner=lambda *_args, **_kwargs: Completed(returncode=1, stdout="")
        )


def test_watchdog_writes_status_and_deduplicates_unchanged_alerts(tmp_path):
    status_path = tmp_path / "scheduler_watchdog_status.json"
    alerts = []

    def collector():
        return _healthy_tasks(tmp_path, include_backup=False)

    def emit_fn(items, **kwargs):
        alerts.append((list(items), kwargs))
        return {"n": len(items), "pushed": False}

    first = run_scheduler_watchdog(
        repo_root=tmp_path,
        status_path=status_path,
        now_ms=NOW_MS,
        backup_ready=False,
        heartbeat_ready=False,
        event_seal_ready=False,
        collector=collector,
        emit_fn=emit_fn,
    )
    second = run_scheduler_watchdog(
        repo_root=tmp_path,
        status_path=status_path,
        now_ms=NOW_MS + 1_000,
        backup_ready=False,
        heartbeat_ready=False,
        event_seal_ready=False,
        collector=collector,
        emit_fn=emit_fn,
    )

    assert first["verdict"] == second["verdict"] == "PARTIAL"
    assert len(alerts) == 1
    assert json.loads(status_path.read_text(encoding="utf-8"))["verdict"] == "PARTIAL"


def test_watchdog_refreshes_clock_after_concurrent_resident_health_publication(
    monkeypatch,
    tmp_path,
):
    clock = iter(
        (
            NOW_MS / 1_000,
            (NOW_MS + 5_000) / 1_000,
            (NOW_MS + 10_000) / 1_000,
        )
    )
    monkeypatch.setattr(watchdog.time, "time", lambda: next(clock))
    snapshots = _healthy_tasks(tmp_path, include_backup=False)
    stream_index = next(
        index
        for index, row in enumerate(snapshots)
        if row.name == "QUANTOS_binance_realtime_stream"
    )
    stream = snapshots[stream_index]
    snapshots[stream_index] = TaskSnapshot(
        name=stream.name,
        state="Running",
        last_run_ms=stream.last_run_ms,
        last_result=stream.last_result,
        next_run_ms=stream.next_run_ms,
        execute=stream.execute,
        arguments=stream.arguments,
        working_directory=stream.working_directory,
    )

    result = run_scheduler_watchdog(
        repo_root=tmp_path,
        status_path=tmp_path / "status.json",
        backup_ready=False,
        heartbeat_ready=False,
        event_seal_ready=False,
        collector=lambda: snapshots,
        resident_health_reader=lambda _path: {
            "generated_at_ms": NOW_MS + 8_000,
            "verdict": "PASS",
            "streaming": True,
            "safety": {
                "credentials": False,
                "private_stream": False,
                "broker_connectivity": False,
            },
        },
        emit_fn=lambda *_args, **_kwargs: {"n": 0, "pushed": False},
    )

    stream_check = next(
        row
        for row in result["checks"]
        if row["task_name"] == "QUANTOS_binance_realtime_stream"
    )
    assert result["observed_at_ms"] == NOW_MS + 10_000
    assert stream_check["status"] == "PASS"
    assert stream_check["code"] == "RUNNING_HEALTHY"


def test_default_collector_follows_platform_provider(monkeypatch, tmp_path):
    observed = []
    monkeypatch.setenv("QUANTOS_SCHEDULER_PROVIDER", "systemd")
    monkeypatch.setattr(
        watchdog,
        "collect_systemd_units",
        lambda: observed.append("systemd") or [],
    )

    result = run_scheduler_watchdog(
        repo_root=tmp_path,
        status_path=tmp_path / "status.json",
        now_ms=NOW_MS,
        backup_ready=False,
        heartbeat_ready=False,
        event_seal_ready=False,
        emit_fn=lambda *_args, **_kwargs: {"n": 0, "pushed": False},
    )

    assert observed == ["systemd"]
    assert result["verdict"] == "BREACH"
    assert result["checks"][0]["task_name"] == "QUANTOS_factory_loop"


def test_systemd_provider_labels_collector_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANTOS_SCHEDULER_PROVIDER", "systemd")
    monkeypatch.setattr(
        watchdog,
        "collect_systemd_units",
        lambda: (_ for _ in ()).throw(SchedulerInspectionError("failed")),
    )

    result = run_scheduler_watchdog(
        repo_root=tmp_path,
        status_path=tmp_path / "status.json",
        now_ms=NOW_MS,
        backup_ready=False,
        heartbeat_ready=False,
        event_seal_ready=False,
        emit_fn=lambda *_args, **_kwargs: {"n": 0, "pushed": False},
    )

    assert result["checks"][0]["task_name"] == "SYSTEMD_SCHEDULER"


def test_read_restore_history_health_passes_real_current_authority(tmp_path):
    _write_restore_policy(tmp_path)
    store = _history_store(tmp_path)
    _record_history_outcome(
        store,
        attempt_id="healthy",
        started_at_ms=NOW_MS - 1_000,
        passing=True,
    )
    store.close()

    health = read_restore_history_health(repo_root=tmp_path, now_ms=NOW_MS)

    assert health["verdict"] == "PASS"
    assert health["reason_codes"] == ()
    assert health["success_count"] == 1


def test_restore_policy_comes_from_immutable_code_root(tmp_path):
    state_root = tmp_path / "state"
    code_root = tmp_path / "release"
    _write_restore_policy(code_root)
    store = _history_store(state_root)
    _record_history_outcome(
        store,
        attempt_id="release-policy",
        started_at_ms=NOW_MS - 1_000,
        passing=True,
    )
    store.close()

    health = read_restore_history_health(
        repo_root=state_root,
        policy_root=code_root,
        now_ms=NOW_MS,
    )

    assert health["verdict"] == "PASS"


def test_read_restore_history_health_reports_missing_empty_and_corrupt_without_path(
    tmp_path,
):
    _write_restore_policy(tmp_path)
    missing = read_restore_history_health(repo_root=tmp_path, now_ms=NOW_MS)
    assert missing == {
        "verdict": "UNAVAILABLE",
        "reason_codes": ("RESTORE_HISTORY_UNAVAILABLE",),
    }

    store = _history_store(tmp_path)
    store.close()
    empty = read_restore_history_health(repo_root=tmp_path, now_ms=NOW_MS)
    assert empty["verdict"] == "UNAVAILABLE"
    assert empty["reason_codes"] == ("NO_ATTEMPTS",)

    history_path = tmp_path / "data" / "cache" / "l2_restore_history.db"
    history_path.unlink()
    with sqlite3.connect(history_path) as connection:
        connection.execute("CREATE TABLE secret_path(value TEXT)")
    corrupt = read_restore_history_health(repo_root=tmp_path, now_ms=NOW_MS)
    assert corrupt == {
        "verdict": "BREACH",
        "reason_codes": ("RESTORE_HISTORY_INVALID",),
    }
    assert str(history_path) not in json.dumps(corrupt)


def test_restore_history_policy_rejects_duplicate_json_members(tmp_path):
    _write_restore_policy(tmp_path)
    policy_path = tmp_path / "config" / "top_tier_requirements.json"
    content = policy_path.read_text(encoding="utf-8")
    policy_path.write_text(
        content.replace(
            '"checker": "l2_restore_history_evidence"',
            '"checker": "paths_exist", "checker": "l2_restore_history_evidence"',
            1,
        ),
        encoding="utf-8",
    )

    health = read_restore_history_health(repo_root=tmp_path, now_ms=NOW_MS)

    assert health == {
        "verdict": "BREACH",
        "reason_codes": ("RESTORE_POLICY_INVALID",),
    }


def test_scheduler_success_is_breached_by_latest_history_failure(tmp_path):
    _write_restore_policy(tmp_path)
    store = _history_store(tmp_path)
    _record_history_outcome(
        store,
        attempt_id="failed",
        started_at_ms=NOW_MS - 1_000,
        passing=False,
    )
    store.close()

    result = run_scheduler_watchdog(
        repo_root=tmp_path,
        status_path=tmp_path / "watchdog.json",
        now_ms=NOW_MS,
        backup_ready=True,
        heartbeat_ready=True,
        event_seal_ready=True,
        collector=lambda: _healthy_tasks(
            tmp_path, include_backup=True, include_external=True
        ),
        emit_fn=lambda *_args, **_kwargs: {"n": 1, "pushed": False},
    )

    backup = next(
        item for item in result["checks"] if item["task_name"] == "QUANTOS_backup"
    )
    assert result["verdict"] == "BREACH"
    assert backup["status"] == "BREACH"
    assert backup["code"] == "RESTORE_HISTORY_LATEST_TERMINAL_NOT_PASS"
    assert "failed" not in backup["detail"].lower()


def test_history_failure_remains_breach_after_later_success_and_stale_is_breach(
    tmp_path,
):
    _write_restore_policy(tmp_path)
    store = _history_store(tmp_path)
    _record_history_outcome(
        store,
        attempt_id="pass-1",
        started_at_ms=NOW_MS - 3_000,
        passing=True,
    )
    _record_history_outcome(
        store,
        attempt_id="fail",
        started_at_ms=NOW_MS - 2_000,
        passing=False,
    )
    _record_history_outcome(
        store,
        attempt_id="pass-2",
        started_at_ms=NOW_MS - 1_000,
        passing=True,
    )
    store.close()
    health = read_restore_history_health(repo_root=tmp_path, now_ms=NOW_MS)
    assert health["verdict"] == "BREACH"
    assert "FAILURE_IN_CLEAN_LOOKBACK" in health["reason_codes"]

    _write_restore_policy(tmp_path, max_success_age_ms=500)
    stale = read_restore_history_health(repo_root=tmp_path, now_ms=NOW_MS)
    assert stale["verdict"] == "BREACH"
    assert "LATEST_SUCCESS_STALE" in stale["reason_codes"]


def test_longitudinal_restore_maturity_is_partial_not_operational_breach():
    check = {
        "task_name": "QUANTOS_backup",
        "status": "PASS",
        "code": "HEALTHY",
        "detail": "enabled, action-pinned, fresh, and successful",
    }
    health = {
        "verdict": "BREACH",
        "reason_codes": (
            "HISTORY_SPAN_INSUFFICIENT",
            "SUCCESS_COUNT_INSUFFICIENT",
        ),
        "latest_terminal_verdict": "PASS",
        "failure_count_in_clean_window": 0,
        "overdue_open_count": 0,
    }

    assert watchdog._merge_backup_history(check, health) == {
        "task_name": "QUANTOS_backup",
        "status": "PARTIAL",
        "code": "RESTORE_HISTORY_MATURING",
        "detail": (
            "current restore evidence is healthy; longitudinal maturity "
            "is incomplete "
            "(HISTORY_SPAN_INSUFFICIENT,SUCCESS_COUNT_INSUFFICIENT)"
        ),
    }


def test_healthy_scheduler_and_history_pass_and_history_alert_is_deduplicated(
    tmp_path,
):
    _write_restore_policy(tmp_path)
    store = _history_store(tmp_path)
    _record_history_outcome(
        store,
        attempt_id="healthy",
        started_at_ms=NOW_MS - 1_000,
        passing=True,
    )
    store.close()
    alerts = []

    healthy = run_scheduler_watchdog(
        repo_root=tmp_path,
        status_path=tmp_path / "healthy-status.json",
        now_ms=NOW_MS,
        backup_ready=True,
        heartbeat_ready=True,
        event_seal_ready=True,
        collector=lambda: _healthy_tasks(
            tmp_path, include_backup=True, include_external=True
        ),
        emit_fn=lambda *_args, **_kwargs: {"n": 0, "pushed": False},
    )
    assert healthy["verdict"] == "PASS"

    def history_failure(**_kwargs):
        return {
            "verdict": "BREACH",
            "reason_codes": ("LATEST_SUCCESS_STALE",),
        }

    def emit_fn(items, **kwargs):
        alerts.append((list(items), kwargs))
        return {"n": len(items), "pushed": False}

    status_path = tmp_path / "failing-status.json"
    for offset in (0, 1_000):
        run_scheduler_watchdog(
            repo_root=tmp_path,
            status_path=status_path,
            now_ms=NOW_MS + offset,
            backup_ready=True,
            heartbeat_ready=True,
            event_seal_ready=True,
            collector=lambda: _healthy_tasks(
                tmp_path, include_backup=True, include_external=True
            ),
            history_reader=history_failure,
            emit_fn=emit_fn,
        )
    assert len(alerts) == 1
    assert alerts[0][0] == [
        "QUANTOS_backup BREACH RESTORE_HISTORY_LATEST_SUCCESS_STALE"
    ]
