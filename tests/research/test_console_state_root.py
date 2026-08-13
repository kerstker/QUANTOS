from __future__ import annotations

import gc
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPOSITORY = Path(__file__).resolve().parents[2]


def _subprocess_env(state_root: str) -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONPATH": str(REPOSITORY),
        "QUANTOS_STATE_ROOT": state_root,
    }


def test_console_p0_mutable_inputs_resolve_only_below_explicit_state_root(
    tmp_path: Path,
) -> None:
    release_cwd = tmp_path / "release-cwd"
    state_root = (tmp_path / "state").resolve()
    release_cwd.mkdir()
    code = (
        "import json\n"
        "from services.console import data_console\n"
        "from services.console.api import main, registry_snapshots, research_run_views, system_views\n"
        "print(json.dumps({\n"
        "  'crypto_daily': str(data_console.DEFAULT_DATA_CONSOLE_PORT._crypto_daily_db),\n"
        "  'crypto_snapshot': str(main._CRYPTO_SNAPSHOT_PATH),\n"
        "  'kill_switch': str(main._LIVE_KILL_SWITCH_STATE),\n"
        "  'governance_audit': str(main._GOVERNANCE_AUDIT_DB),\n"
        "  'experiment_registry': str(registry_snapshots._EXP_DB),\n"
        "  'paper_track': str(registry_snapshots._PAPER_TRACK_DB),\n"
        "  'research_runs': str(research_run_views._RUNS_DB),\n"
        "  'domain_events': str(system_views._DOMAIN_EVENT_DB),\n"
        "  'domain_event_projection': str(system_views._DOMAIN_EVENT_PROJECTION_DB),\n"
        "}))\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=release_cwd,
        env=_subprocess_env(str(state_root)),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    observed = json.loads(completed.stdout)

    expected = {
        "crypto_daily": state_root / "data" / "cache" / "crypto_daily.db",
        "crypto_snapshot": state_root / "data" / "cache" / "crypto_research_snapshot.json",
        "kill_switch": state_root / "data" / "cache" / "live_kill_switch.json",
        "governance_audit": state_root / "data" / "cache" / "governance_audit.db",
        "experiment_registry": state_root / "data" / "cache" / "experiment_registry.db",
        "paper_track": state_root / "data" / "cache" / "paper_track.db",
        "research_runs": state_root / "data" / "cache" / "research_runs.db",
        "domain_events": state_root / "data" / "cache" / "domain_events.db",
        "domain_event_projection": state_root / "data" / "cache" / "domain_event_projection.db",
    }
    assert {name: Path(value) for name, value in observed.items()} == expected
    assert not state_root.exists()
    assert not (release_cwd / "data").exists()


@pytest.mark.parametrize(
    "module",
    (
        "services.console.data_console",
        "services.console.api.main",
        "services.console.api.registry_snapshots",
        "services.console.api.research_run_views",
        "services.console.api.system_views",
    ),
)
def test_console_p0_modules_fail_closed_on_relative_state_root(
    tmp_path: Path,
    module: str,
) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=tmp_path,
        env=_subprocess_env("relative-state"),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode != 0
    assert "QUANTOS_STATE_ROOT must be absolute" in completed.stderr
    assert not (tmp_path / "relative-state").exists()
    assert not (tmp_path / "data").exists()


def test_missing_research_run_registry_is_read_only_and_does_not_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.console.api import research_run_views
    from services.research.run_registry import indexer

    missing = tmp_path / "state" / "data" / "cache" / "research_runs.db"
    monkeypatch.setattr(research_run_views, "_RUNS_DB", str(missing))
    monkeypatch.setattr(
        indexer,
        "index_existing",
        lambda *args, **kwargs: pytest.fail("console GET attempted to index results"),
    )

    registry = research_run_views._runs_registry()

    assert registry.list() == []
    assert registry.get("absent") is None
    assert not missing.exists()
    assert not missing.parent.exists()


def test_existing_empty_research_run_registry_is_not_modified_or_indexed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.console.api import research_run_views
    from services.research.run_registry import indexer
    from services.research.run_registry.store import ResearchRunStore

    path = tmp_path / "research_runs.db"
    store = ResearchRunStore(str(path), create=True)
    del store
    gc.collect()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(research_run_views, "_RUNS_DB", str(path))
    monkeypatch.setattr(
        indexer,
        "index_existing",
        lambda *args, **kwargs: pytest.fail("console GET attempted to index results"),
    )

    registry = research_run_views._runs_registry()

    assert registry.list() == []
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before

