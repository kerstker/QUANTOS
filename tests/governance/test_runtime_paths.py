from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

from quantos.runtime_paths import RuntimePathError, runtime_state_root


def test_runtime_state_root_defaults_to_process_working_directory(tmp_path: Path) -> None:
    assert runtime_state_root(environ={}, cwd=tmp_path) == tmp_path.resolve()


def test_runtime_state_root_honors_absolute_operator_root(tmp_path: Path) -> None:
    state = tmp_path / "state"
    assert runtime_state_root(
        environ={"QUANTOS_STATE_ROOT": str(state)},
        cwd=tmp_path,
    ) == state.resolve()


def test_runtime_state_root_rejects_relative_environment_override(tmp_path: Path) -> None:
    with pytest.raises(RuntimePathError, match="must be absolute"):
        runtime_state_root(
            environ={"QUANTOS_STATE_ROOT": "relative-state"},
            cwd=tmp_path,
        )


def test_scheduled_realtime_and_console_defaults_resolve_to_state_root(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[2]
    state = (tmp_path / "state").resolve()
    code = (
        "import json\n"
        "from tools import realtime_alpha_discovery as discovery\n"
        "from tools import realtime_alpha_forward_shadow as forward\n"
        "from services.console.api import artifact_paper_views as console\n"
        "print(json.dumps({"
        "'discovery': str(discovery.DEFAULT_OUTPUT), "
        "'forward': str(forward.DEFAULT_OUTPUT), "
        "'console': str(console._REPO_ROOT)"
        "}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(repository),
            "QUANTOS_STATE_ROOT": str(state),
        },
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    observed = json.loads(completed.stdout)

    assert Path(observed["discovery"]).is_relative_to(state)
    assert Path(observed["forward"]).is_relative_to(state)
    assert Path(observed["console"]) == state


def test_forward_evidence_builder_uses_runtime_state_root() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "tools" / "forward_track_advance.py"
    ).read_text(encoding="utf-8")

    assert "build_forward_evidence(\n        runtime_state_root()," in source
