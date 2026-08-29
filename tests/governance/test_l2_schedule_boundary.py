import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCHEDULE_SCRIPT = ROOT / "scripts" / "register_schedules.ps1"


def _source() -> str:
    return SCHEDULE_SCRIPT.read_text(encoding="utf-8")


def _registered_task_pairs() -> list[tuple[str, str]]:
    return re.findall(
        r'Register-L2Task\s+-Name\s+"([A-Za-z0-9_]+)"\s*'
        r'(?:`\s*)?-Module\s+"([a-z0-9_]+)"',
        _source(),
    )


def _registered_task_map() -> dict[str, str]:
    return dict(_registered_task_pairs())


def test_schedule_registration_has_no_live_execution_entrypoint():
    source = _source()
    lowered = source.lower()
    for forbidden in (
        "--submit",
        "--act",
        "tools.live_track_advance",
        "tools.live_risk_sentinel",
        "tools.live_ops_api",
    ):
        assert forbidden not in lowered


def test_schedule_registration_only_runs_explicit_l2_modules():
    from quantos.contracts.l2_schedule_policy import MVP_EXPECTED_TASKS

    source = _source()
    pairs = _registered_task_pairs()
    assert len(pairs) == source.count("Register-L2Task -Name")
    assert len(pairs) == len({task_name for task_name, _module in pairs})
    assert len(pairs) == len({module for _task_name, module in pairs})
    assert dict(pairs) == MVP_EXPECTED_TASKS


def test_invalidated_v1_authority_tasks_are_quiesced_and_not_registered():
    from quantos.contracts.l2_schedule_policy import HARD_QUARANTINED_TASKS

    source = _source()

    for task_name, module_name in HARD_QUARANTINED_TASKS.items():
        assert not re.search(
            rf'Register-L2Task -Name "{task_name}" -Module "{module_name}"',
            source,
        )
        assert (
            f'[pscustomobject]@{{ Name = "{task_name}"; '
            f'Module = "{module_name}" }}'
        ) in source

    assert "INVALIDATED_PERP_LEDGER_V1" in (
        ROOT / "tools" / "l2_schedule_entrypoint.py"
    ).read_text(encoding="utf-8")
    assert "Disable-TaskIfPresent -Name $task.Name -Module $task.Module" in source


def test_non_mvp_tasks_are_quiesced_and_cannot_be_registered():
    from quantos.contracts.l2_schedule_policy import MVP_DISABLED_TASKS

    source = _source()
    registered = _registered_task_map()
    for task_name, module_name in MVP_DISABLED_TASKS.items():
        assert registered.get(task_name) is None
        assert (
            f'[pscustomobject]@{{ Name = "{task_name}"; '
            f'Module = "{module_name}" }}'
        ) in source

    assert "foreach ($task in $mvpDisabledTasks)" in source
    assert "Disable-TaskIfPresent -Name $task.Name -Module $task.Module" in source


def test_domain_event_ops_has_frequent_bounded_recovery_schedule():
    source = _source()
    assert re.search(
        r"\$eventOpsTrigger\s*=.*?\n\s*"
        r"-RepetitionInterval \(New-TimeSpan -Minutes 15\)",
        source,
    )
    assert re.search(
        r'Register-L2Task -Name "QUANTOS_domain_event_ops".*?\n\s*'
        r"-Trigger \$eventOpsTrigger -ExecutionTimeLimit "
        r"\(New-TimeSpan -Minutes 5\)",
        source,
    )


def test_defillama_forward_vintage_refresh_is_daily_and_bounded():
    source = _source()
    assert re.search(
        r'\$defillamaTvlForwardRefreshTrigger\s*=\s*'
        r'New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "09:30"',
        source,
    )
    assert re.search(
        r'Register-L2Task -Name "QUANTOS_defillama_tvl_forward_refresh" '
        r'-Module "defillama_tvl_forward_refresh".*?\n\s*'
        r'-Trigger \$defillamaTvlForwardRefreshTrigger -ExecutionTimeLimit '
        r'\(New-TimeSpan -Minutes 10\)',
        source,
    )
    assert re.search(
        r'\$defillamaStablecoinForwardRefreshTrigger\s*=\s*'
        r'New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "09:40"',
        source,
    )
    assert re.search(
        r'Register-L2Task -Name "QUANTOS_defillama_stablecoin_forward_refresh" '
        r'-Module "defillama_stablecoin_forward_refresh".*?\n\s*'
        r'-Trigger \$defillamaStablecoinForwardRefreshTrigger -ExecutionTimeLimit '
        r'\(New-TimeSpan -Minutes 5\)',
        source,
    )


def test_wikimedia_attention_forward_refresh_is_daily_and_bounded():
    source = _source()
    assert re.search(
        r'\$wikimediaAttentionForwardRefreshTrigger\s*=\s*'
        r'New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "09:45"',
        source,
    )
    assert re.search(
        r'Register-L2Task -Name "QUANTOS_wikimedia_attention_forward_refresh" '
        r'-Module "wikimedia_attention_forward_refresh".*?\n\s*'
        r'-Trigger \$wikimediaAttentionForwardRefreshTrigger -ExecutionTimeLimit '
        r'\(New-TimeSpan -Minutes 5\)',
        source,
    )


def test_okx_borrow_forward_refresh_is_daily_and_bounded():
    source = _source()
    assert re.search(
        r'\$okxBorrowForwardRefreshTrigger\s*=\s*'
        r'New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "09:55"',
        source,
    )
    assert re.search(
        r'Register-L2Task -Name "QUANTOS_okx_borrow_forward_refresh" '
        r'-Module "okx_borrow_forward_refresh".*?\n\s*'
        r'-Trigger \$okxBorrowForwardRefreshTrigger -ExecutionTimeLimit '
        r'\(New-TimeSpan -Minutes 5\)',
        source,
    )


def test_binance_book_depth_forward_refresh_is_daily_and_bounded():
    source = _source()
    assert re.search(
        r'\$binanceBookDepthForwardRefreshTrigger\s*=\s*'
        r'New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "10:00"',
        source,
    )
    assert re.search(
        r'Register-L2Task -Name "QUANTOS_binance_book_depth_forward_refresh" '
        r'-Module "binance_book_depth_forward_refresh".*?\n\s*'
        r'-Trigger \$binanceBookDepthForwardRefreshTrigger -ExecutionTimeLimit '
        r'\(New-TimeSpan -Minutes 30\)',
        source,
    )


def test_cross_venue_health_is_bounded_and_registered():
    source = _source()
    assert re.search(
        r"\$crossVenueLiquidationHealthTrigger\s*=.*?\n\s*"
        r"-At \(Get-Date\)\.AddMinutes\(17\).*?\n\s*"
        r"-RepetitionInterval \(New-TimeSpan -Minutes 30\)",
        source,
        flags=re.DOTALL,
    )
    assert re.search(
        r'Register-L2Task -Name "QUANTOS_cross_venue_liquidation_health".*?'
        r'-Module "cross_venue_liquidation_health".*?\n\s*'
        r'-Trigger \$crossVenueLiquidationHealthTrigger -ExecutionTimeLimit '
        r'\(New-TimeSpan -Minutes 10\)',
        source,
        flags=re.DOTALL,
    )


def test_schedule_registration_is_workspace_relative_and_disables_legacy_live_tasks():
    source = _source()
    assert "$PSScriptRoot" in source
    assert "StateRoot" in source
    assert "l2_schedule_entrypoint.py" in source
    assert "Get-Command python" in source
    assert '-c "import sys; print(sys.executable)"' in source
    assert "$pythonExecutable" in source
    assert "configured Python runtime could not resolve its real executable" in source
    assert (
        '$pythonw = Join-Path (Split-Path -Parent $pythonExecutable) "pythonw.exe"'
        in source
    )
    assert (
        'Register-L2Task -Name "QUANTOS_binance_realtime_features" '
        '-Module "binance_realtime_features" `' in source
    )
    assert (
        '-Trigger $binanceRealtimeFeaturesTrigger '
        '-ExecutionTimeLimit (New-TimeSpan -Minutes 45)' in source
    )
    assert "QUANTOS_hourly_alpha_advance" in source
    assert "QUANTOS_mft_l2_runtime" in source
    assert '"pythonw.exe"' in source
    assert "New-ScheduledTaskAction" in source
    assert "New-ScheduledTaskPrincipal" in source
    assert "-LogonType S4U" in source
    assert "-Principal $l2Principal" in source
    assert "-MultipleInstances IgnoreNew" in source
    assert "-WorkingDirectory $stateRoot" in source
    assert "-ExecutionTimeLimit (New-TimeSpan -Minutes 5)" in source
    assert "cmd /" not in source.lower()
    assert "H:\\code\\QUANTOS" not in source
    assert "H:\\miniconda\\python.exe" not in source
    for task_name in (
        "QUANTOS_live_track_binance",
        "QUANTOS_live_track_bybit_demo",
        "QUANTOS_risk_sentinel",
        "QUANTOS_ops_api",
        "QUANTOS_watchdog",
        "QUANTOS_backup",
        "QUANTOS_external_heartbeat",
        "QUANTOS_domain_event_seal",
        "QUANTOS_artifact_admission_seal",
    ):
        assert task_name in source
    assert "Disable-ScheduledTask" in source
    assert "Stop-ScheduledTask" in source
    assert "Get-ScheduledTask" in source


def test_schedule_registration_quiesces_old_release_instances_before_force_replace():
    source = _source()
    function = re.search(
        r"function Register-L2Task \{(.*?)\n\}",
        source,
        flags=re.DOTALL,
    )

    assert function is not None
    body = function.group(1)
    assert "Disable-TaskIfPresent -Name $Name" in body
    assert body.index("Disable-TaskIfPresent -Name $Name") < body.index(
        "Register-ScheduledTask -TaskName"
    )
    assert "running instance did not stop before release registration" in source
    assert "AddSeconds(30)" in source
    assert "Get-CimInstance Win32_Process" in source
    assert "l2_schedule_entrypoint.py* ${Module} --release-commit" in source
    assert (
        "Stop-Process -Id $worker.ProcessId -Force -ErrorAction SilentlyContinue"
        in source
    )
    assert "orphaned L2 worker did not stop before release registration" in source


def test_schedule_registration_quiesces_owned_descendants_and_cursor_verifier_orphans():
    source = _source()
    disable = re.search(
        r"function Disable-TaskIfPresent \{(.*?)\n\}",
        source,
        flags=re.DOTALL,
    )

    assert disable is not None
    body = disable.group(1)
    assert "Get-L2OwnedProcesses -Module $Module" in body
    assert "ParentProcessId" in source
    assert "CreationDate" in source
    assert '$cursorStore = Join-Path $stateRoot "data\\cache\\binance_realtime_stream.db"' in source
    assert (
        "-m tools.binance_realtime_stream --verify-cursor-chain "
        "--store $cursorStore --cursor-checkpoint-seq"
    ) in source
    assert source.count(
        '$process.Name -in @("python.exe", "pythonw.exe")'
    ) >= 2
    assert "*tools.binance_realtime_stream*" not in source
    assert body.index("Get-L2OwnedProcesses -Module $Module") < body.index(
        'if ($null -eq $task)'
    )
    assert body.index("Disable-ScheduledTask -TaskName $Name") < body.index(
        "Stop-ScheduledTask -TaskName $Name"
    )
    assert "owned L2 subprocess did not stop before release registration" in source


def test_schedule_registration_separates_release_code_from_mutable_state():
    source = _source()
    assert "$codeRoot = (Resolve-Path (Join-Path $PSScriptRoot \"..\")).Path" in source
    assert "$stateRoot = (Resolve-Path -LiteralPath $StateRoot).Path" in source
    assert "release and state roots must be different" in source
    assert "status --porcelain=v1 --untracked-files=all --ignore-submodules=none" in source
    assert "$releaseCommit" in source
    assert '$launcher = Join-Path $codeRoot "tools\\l2_schedule_entrypoint.py"' in source
    assert '-Execute $pythonw' in source
    assert '--release-commit $releaseCommit' in source
    assert '-m tools.$Module' not in source


def test_resident_public_streams_have_bounded_restart_poll():
    source = _source()
    trigger = re.search(
        r"\$binanceRealtimeStreamTrigger\s*=\s*@\((.*?)\n\)",
        source,
        flags=re.DOTALL,
    )

    assert trigger is not None
    contract = trigger.group(1)
    assert "New-ScheduledTaskTrigger -AtStartup" in contract
    assert "New-ScheduledTaskTrigger -Once" in contract
    assert "New-TimeSpan -Minutes 5" in contract
    assert "New-ScheduledTaskTrigger -Daily" not in contract
    assert "-MultipleInstances IgnoreNew" in source
    registered = _registered_task_map()
    assert registered["QUANTOS_binance_realtime_stream"] == (
        "binance_realtime_stream"
    )
    assert registered["QUANTOS_bybit_realtime_stream"] == (
        "bybit_realtime_stream"
    )


def test_backup_registration_never_embeds_a_secret_or_local_default_destination():
    from quantos.contracts.l2_schedule_policy import (
        MVP_CONDITIONAL_MODULES,
        MVP_EXPECTED_TASKS,
    )

    source = _source()
    assert "QUANTOS_BACKUP_PASSPHRASE" in source
    assert "QUANTOS_BACKUP_DEST" in source
    assert "QUANTOS_HEARTBEAT_URL" in source
    assert "QUANTOS_EVENT_SEAL_DEST" in source
    assert "-m tools.l2_backup" not in source
    assert "tools.live_backup" not in source
    assert "--allow-local-destination" not in source
    conditional_tasks = {
        task_name: module
        for task_name, module in MVP_EXPECTED_TASKS.items()
        if module in MVP_CONDITIONAL_MODULES
    }
    for task_name, module in conditional_tasks.items():
        assert (
            f'[pscustomobject]@{{ Name = "{task_name}"; Module = "{module}" }}'
        ) in source
    assert "foreach ($task in $conditionalMvpTasks)" in source


def test_conditional_workers_are_first_quiesced_with_module_identity():
    source = _source()
    legacy = re.search(
        r"\$legacyLiveTasks\s*=\s*@\((.*?)\n\)",
        source,
        flags=re.DOTALL,
    )
    conditional = re.search(
        r"\$conditionalMvpTasks\s*=\s*@\((.*?)\n\)",
        source,
        flags=re.DOTALL,
    )

    assert legacy is not None
    assert conditional is not None
    for task_name in (
        "QUANTOS_backup",
        "QUANTOS_external_heartbeat",
        "QUANTOS_domain_event_seal",
        "QUANTOS_artifact_admission_seal",
    ):
        assert task_name not in legacy.group(1)
        assert task_name in conditional.group(1)
    assert source.index("$conditionalMvpTasks") < source.index(
        "$backupReadiness = Get-L2BackupReadiness"
    )
