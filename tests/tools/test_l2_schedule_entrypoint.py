from pathlib import Path
import os
import subprocess
from types import SimpleNamespace
import sys

import pytest

from tools.l2_schedule_entrypoint import (
    ALLOWED_L2_MODULES,
    ScheduleEntrypointError,
    run_scheduled_module,
)


def _module(code_root: Path, *, result: object = 0, observed: list | None = None):
    module_path = code_root / "tools" / "worker.py"
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text("# fixture\n", encoding="utf-8")

    def main():
        if observed is not None:
            observed.append((Path.cwd(), tuple(sys.path), tuple(sys.argv)))
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


def test_entrypoint_does_not_leak_module_argument_into_worker_argv(
    tmp_path: Path, monkeypatch
) -> None:
    code_root = tmp_path / "release"
    state_root = tmp_path / "state"
    state_root.mkdir()
    observed = []
    module = _module(code_root, observed=observed)
    monkeypatch.chdir(state_root)
    monkeypatch.setattr(sys, "argv", ["l2_schedule_entrypoint.py", "domain_event_ops"])

    result = run_scheduled_module(
        "domain_event_ops",
        code_root=code_root,
        importer=lambda _name: module,
    )

    assert result == 0
    assert observed
    assert observed[0][1][0] == str(code_root.resolve())
    assert observed[0][2] == ("tools.domain_event_ops",)
    assert sys.argv == ["l2_schedule_entrypoint.py", "domain_event_ops"]


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


def test_entrypoint_allowlist_matches_registered_l2_surface() -> None:
    assert ALLOWED_L2_MODULES == frozenset(
        {
            "ashare_kline_fetch",
            "ashare_ifind_fetch",
            "ashare_paper_track",
            "alpha_maintenance_sweep",
            "crypto_factory_loop",
            "domain_event_ops",
            "domain_event_seal",
            "artifact_admission_seal",
            "external_heartbeat",
            "forward_track_advance",
            "gonogo_report",
            "alpha_paper_cycle",
            "paper_order_preview",
            "paper_realtime",
            "paper_realtime_10k",
            "paper_health",
            "l2_paper_operator",
            "ic_panel_research",
            "l2_backup",
            "l2_scheduler_watchdog",
        }
    )


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
    assert completed.stdout.strip() == "RELEASE"
    assert "DIRTY" not in completed.stdout


def test_script_entrypoint_redirects_scheduled_output_to_per_worker_log(
    tmp_path: Path,
) -> None:
    source_launcher = Path(__file__).resolve().parents[2] / "tools" / "l2_schedule_entrypoint.py"
    release = tmp_path / "release"
    state = tmp_path / "state"
    log_dir = tmp_path / "logs"
    for root in (release, state):
        (root / "tools").mkdir(parents=True)
        (root / "tools" / "__init__.py").write_text("", encoding="utf-8")
    (release / "tools" / "domain_event_ops.py").write_text(
        "def main():\n    print('RELEASE')\n    return 0\n",
        encoding="utf-8",
    )
    launcher = release / "tools" / "l2_schedule_entrypoint.py"
    launcher.write_bytes(source_launcher.read_bytes())

    completed = subprocess.run(
        [sys.executable, str(launcher), "domain_event_ops", "--log-dir", str(log_dir)],
        cwd=state,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    log_text = (log_dir / "domain_event_ops.log").read_text(encoding="utf-8")
    assert "START module=domain_event_ops" in log_text
    assert "RELEASE" in log_text
    assert "END module=domain_event_ops exit_code=0" in log_text
