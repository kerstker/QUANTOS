import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCHEDULE_SCRIPT = ROOT / "scripts" / "register_schedules.ps1"


def _source() -> str:
    return SCHEDULE_SCRIPT.read_text(encoding="utf-8")


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
    source = _source()
    registered_modules = set(
        re.findall(r'-Module\s+"([a-z0-9_]+)"', source)
    )
    assert registered_modules
    assert registered_modules <= {
        "alpha_maintenance_sweep",
        "crypto_factory_loop",
        "domain_event_ops",
        "domain_event_seal",
        "artifact_admission_seal",
        "binance_realtime_market",
        "binance_realtime_features",
        "binance_realtime_drift_alerts",
        "binance_realtime_stream",
        "external_heartbeat",
        "forward_track_advance",
        "gonogo_report",
        "alpha_paper_cycle",
        "paper_alpha_evaluation",
        "paper_multivenue_sor",
        "paper_research_risk_bundle",
        "paper_order_preview",
        "paper_realtime",
        "paper_realtime_10k",
        "paper_shadow_auto",
        "paper_shadow_realtime",
        "paper_health",
        "ic_panel_research",
        "l2_backup",
        "l2_scheduler_watchdog",
    }
    assert "domain_event_ops" in registered_modules
    assert "QUANTOS_domain_event_ops" in source
    assert "QUANTOS_scheduler_watchdog" in source
    assert "QUANTOS_backup" in source
    assert "QUANTOS_external_heartbeat" in source
    assert "QUANTOS_domain_event_seal" in source
    assert "QUANTOS_artifact_admission_seal" in source


def test_schedule_registration_is_workspace_relative_and_disables_legacy_live_tasks():
    source = _source()
    assert "$PSScriptRoot" in source
    assert "StateRoot" in source
    assert "l2_schedule_entrypoint.py" in source
    assert "Get-Command python" in source
    assert '"pythonw.exe"' in source
    assert "New-ScheduledTaskAction" in source
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
    assert "Get-ScheduledTask" in source


def test_schedule_registration_separates_release_code_from_mutable_state():
    source = _source()
    assert "$codeRoot = (Resolve-Path (Join-Path $PSScriptRoot \"..\")).Path" in source
    assert "$stateRoot = (Resolve-Path -LiteralPath $StateRoot).Path" in source
    assert '$launcher = Join-Path $codeRoot "tools\\l2_schedule_entrypoint.py"' in source
    assert '-Execute $pythonw' in source
    assert '-Argument "`"$launcher`" $Module"' in source
    assert '-m tools.$Module' not in source


def test_backup_registration_never_embeds_a_secret_or_local_default_destination():
    source = _source()
    assert "QUANTOS_BACKUP_PASSPHRASE" in source
    assert "QUANTOS_BACKUP_DEST" in source
    assert "QUANTOS_HEARTBEAT_URL" in source
    assert "QUANTOS_EVENT_SEAL_DEST" in source
    assert "-m tools.l2_backup" not in source
    assert "tools.live_backup" not in source
    assert "--allow-local-destination" not in source
