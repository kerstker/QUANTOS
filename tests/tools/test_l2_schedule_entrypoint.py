from pathlib import Path
import os
import re
import subprocess
from types import SimpleNamespace
import sys

import pytest

from tools.l2_schedule_entrypoint import (
    ALLOWED_L2_MODULES,
    ScheduleEntrypointError,
    _configure_headless_logging,
    run_scheduled_module,
)


def _module(code_root: Path, *, result: object = 0, observed: list | None = None):
    module_path = code_root / "tools" / "worker.py"
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text("# fixture\n", encoding="utf-8")

    def main():
        if observed is not None:
            observed.append((Path.cwd(), tuple(sys.path)))
        return result

    return SimpleNamespace(__file__=str(module_path), main=main)


def test_entrypoint_loads_allowlisted_module_from_release_root_and_keeps_state_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    code_root = tmp_path / "release"
    state_root = tmp_path / "state"
    state_root.mkdir()
    observed = []
    loaded = []
    module = _module(code_root, result=2, observed=observed)
    original_path = list(sys.path)
    monkeypatch.chdir(state_root)

    result = run_scheduled_module(
        "domain_event_ops",
        code_root=code_root,
        importer=lambda name: loaded.append(name) or module,
    )

    assert result == 2
    assert loaded == ["tools.domain_event_ops"]
    assert observed[0][0] == state_root
    assert Path(observed[0][1][0]) == code_root.resolve()
    assert sys.path == original_path


def test_entrypoint_rejects_unknown_module_before_import(tmp_path: Path) -> None:
    imported = []

    with pytest.raises(ScheduleEntrypointError, match="allowlisted"):
        run_scheduled_module(
            "live_track_advance",
            code_root=tmp_path,
            importer=lambda name: imported.append(name),
        )

    assert imported == []


def test_entrypoint_rejects_module_resolved_outside_release_root(tmp_path: Path) -> None:
    code_root = tmp_path / "release"
    foreign_root = tmp_path / "dirty-state-root"
    module = _module(foreign_root)

    with pytest.raises(ScheduleEntrypointError, match="outside the release root"):
        run_scheduled_module(
            "domain_event_ops",
            code_root=code_root,
            importer=lambda _name: module,
        )


@pytest.mark.parametrize("result", [True, "0", 1.5])
def test_entrypoint_rejects_ambiguous_exit_code(tmp_path: Path, result: object) -> None:
    code_root = tmp_path / "release"
    module = _module(code_root, result=result)

    with pytest.raises(ScheduleEntrypointError, match="exit code"):
        run_scheduled_module(
            "domain_event_ops",
            code_root=code_root,
            importer=lambda _name: module,
        )


def test_entrypoint_hides_launcher_arguments_from_worker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    code_root = tmp_path / "release"
    seen = []
    module = _module(code_root)
    module.main = lambda: seen.append(tuple(sys.argv)) or 0
    launcher_argv = ["l2_schedule_entrypoint.py", "domain_event_ops", "--stale"]
    monkeypatch.setattr(sys, "argv", launcher_argv.copy())

    assert (
        run_scheduled_module(
            "domain_event_ops",
            code_root=code_root,
            importer=lambda _name: module,
        )
        == 0
    )
    assert seen == [(str(Path(module.__file__).resolve()),)]
    assert sys.argv == launcher_argv


def test_headless_logging_is_only_enabled_for_pythonw(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

    _configure_headless_logging("domain_event_ops")

    assert sys.stdout is original_stdout
    assert sys.stderr is original_stderr
    assert not (tmp_path / ".runtime" / "logs").exists()


def test_pythonw_mode_routes_output_to_worker_log(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    code = (
        "import sys\n"
        "from tools.l2_schedule_entrypoint import _configure_headless_logging\n"
        "sys.executable = r'C:\\\\Python\\\\pythonw.exe'\n"
        "_configure_headless_logging('domain_event_ops')\n"
        "print('headless-worker-marker', flush=True)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(root)},
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0
    assert (
        tmp_path / ".runtime" / "logs" / "domain_event_ops.log"
    ).read_text(encoding="utf-8").strip() == "headless-worker-marker"


def test_entrypoint_allowlist_matches_registered_l2_surface() -> None:
    assert ALLOWED_L2_MODULES == frozenset(
        {
            "alpha_paper_cycle",
            "alpha_maintenance_sweep",
            "artifact_admission_seal",
            "binance_realtime_market",
            "binance_realtime_features",
            "binance_realtime_drift_alerts",
            "binance_realtime_stream",
            "crypto_factory_loop",
            "domain_event_ops",
            "domain_event_seal",
            "external_heartbeat",
            "forward_track_advance",
            "gonogo_report",
            "ic_panel_research",
            "l2_backup",
            "l2_scheduler_watchdog",
            "paper_health",
            "paper_alpha_evaluation",
            "paper_multivenue_sor",
            "paper_order_preview",
            "paper_research_risk_bundle",
            "paper_realtime",
            "paper_realtime_10k",
            "paper_shadow_auto",
            "paper_shadow_realtime",
        }
    )


def test_every_registered_schedule_module_is_allowlisted() -> None:
    registration = (
        Path(__file__).resolve().parents[2] / "scripts" / "register_schedules.ps1"
    ).read_text(encoding="utf-8")
    registered = set(re.findall(r'-Module\s+"([a-z0-9_]+)"', registration))

    assert registered
    assert registered <= ALLOWED_L2_MODULES


def test_script_entrypoint_ignores_dirty_state_root_pythonpath(tmp_path: Path) -> None:
    source_launcher = Path(__file__).resolve().parents[2] / "tools" / "l2_schedule_entrypoint.py"
    release = tmp_path / "release"
    state = tmp_path / "dirty-state"
    for root, marker in ((release, "RELEASE"), (state, "DIRTY")):
        tools = root / "tools"
        tools.mkdir(parents=True)
        (tools / "__init__.py").write_text("", encoding="utf-8")
        (tools / "domain_event_ops.py").write_text(
            f"def main():\n    print('{marker}')\n    return 0\n",
            encoding="utf-8",
        )
    launcher = release / "tools" / "l2_schedule_entrypoint.py"
    launcher.write_bytes(source_launcher.read_bytes())
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(state)

    completed = subprocess.run(
        [sys.executable, str(launcher), "domain_event_ops"],
        cwd=state,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0
    assert "RELEASE" in completed.stdout.splitlines()
    assert "[l2-schedule] START module=domain_event_ops" in completed.stdout
    assert "[l2-schedule] END module=domain_event_ops exit_code=0" in completed.stdout
    assert "DIRTY" not in completed.stdout
