from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import tools.l2_scheduler_watchdog as watchdog
from services.governance.l2_restore_history import (
    L2RestoreHistoryStore,
    RestoreAttempt,
    RestoreOutcome,
    RestoreProof,
)
from tools.l2_scheduler_watchdog import (
    EXPECTED_TASKS,
    TaskSnapshot,
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
        execute="C:/Python/python.exe",
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
    )

    check = next(
        row
        for row in result["checks"]
        if row["task_name"] == "QUANTOS_binance_realtime_stream"
    )
    assert check["status"] == "PASS"
    assert check["code"] == "RUNNING"
    assert result["verdict"] == "PASS"


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
