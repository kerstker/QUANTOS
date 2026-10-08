from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPOSITORY = Path(__file__).resolve().parents[2]


def _subprocess_env(**overrides: str) -> dict[str, str]:
    environ = {
        **os.environ,
        "PYTHONPATH": str(REPOSITORY),
    }
    environ.pop("QUANTOS_HOURLY_ALPHA_STATE_ROOT", None)
    environ.pop("QUANTOS_MFT_READ_MODEL_DB", None)
    environ.update(overrides)
    return environ


def test_console_p1_mutable_inputs_resolve_only_below_explicit_state_root(
    tmp_path: Path,
) -> None:
    state_root = (tmp_path / "state").resolve()
    code = (
        "import json\n"
        "from services.console.api import (\n"
        "  alpha_family_views, alpha_library_views, artifact_diagnostic_views,\n"
        "  data_catalog, hourly_alpha_views, mft_execution_views, realtime_inputs,\n"
        ")\n"
        "print(json.dumps({\n"
        "  'alpha_family': str(alpha_family_views._DB),\n"
        "  'alpha_library': str(alpha_library_views._CRYPTO_SNAPSHOT_PATH),\n"
        "  'fx_research': str(artifact_diagnostic_views._FX_RESEARCH_SNAPSHOT),\n"
        "  'fx_gauntlet': str(artifact_diagnostic_views._FX_GAUNTLET_SNAPSHOT),\n"
        "  'carry_strategy': str(artifact_diagnostic_views._CARRY_STRATEGY_SNAPSHOT),\n"
        "  'carry_walkforward': str(artifact_diagnostic_views._CARRY_WALKFWD_SNAPSHOT),\n"
        "  'carry_capacity': str(artifact_diagnostic_views._CARRY_CAPACITY_SNAPSHOT),\n"
        "  'hourly_cache': str(hourly_alpha_views._hourly_alpha_cache_path()),\n"
        "  'mft_rest': str(mft_execution_views._mft_read_model_path()),\n"
        "  'data_status': data_catalog._status_candidates('status.json'),\n"
        "  'data_inventory': str(data_catalog._data_channel_inventory_path()),\n"
        "  'realtime_status': [str(p) for p in realtime_inputs._status_candidates('status.json')],\n"
        "  'api_config': str(data_catalog._BINANCE_API_COVERAGE_CONTRACT),\n"
        "}))\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=_subprocess_env(QUANTOS_STATE_ROOT=str(state_root)),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )

    observed = json.loads(completed.stdout)
    cache = state_root / "data" / "cache"
    assert {name: Path(observed[name]) for name in (
        "alpha_family",
        "alpha_library",
        "fx_research",
        "fx_gauntlet",
        "carry_strategy",
        "carry_walkforward",
        "carry_capacity",
        "mft_rest",
    )} == {
        "alpha_family": cache / "alpha_family_registry.db",
        "alpha_library": cache / "crypto_research_snapshot.json",
        "fx_research": cache / "fx_research_snapshot.json",
        "fx_gauntlet": cache / "fx_gauntlet_snapshot.json",
        "carry_strategy": cache / "crypto_carry_strategy.json",
        "carry_walkforward": cache / "crypto_carry_walkforward.json",
        "carry_capacity": cache / "crypto_carry_capacity.json",
        "mft_rest": cache / "mft_read_model.db",
    }
    assert Path(observed["hourly_cache"]) == cache
    assert [Path(item) for item in observed["data_status"]] == [cache / "status.json"]
    assert Path(observed["data_inventory"]) == cache / "data_channel_inventory.json"
    assert [Path(item) for item in observed["realtime_status"]] == [cache / "status.json"]
    assert Path(observed["api_config"]) == (
        REPOSITORY / "config" / "binance_usdm_market_data_coverage.json"
    )
    assert not state_root.exists()
    assert not (tmp_path / "data").exists()


def test_data_catalog_status_is_state_only_and_does_not_expose_absolute_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.console.api import data_catalog

    state = tmp_path / "state"
    state_path = state / "data" / "cache" / "status.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"generated_at_ms": 1, "marker": "state"}))
    release_path = Path(data_catalog._PROJECT_ROOT) / "data" / "cache" / "status.json"
    monkeypatch.setenv("QUANTOS_STATE_ROOT", str(state))

    payload, source = data_catalog._read_latest_status("status.json")

    assert payload == {"generated_at_ms": 1, "marker": "state"}
    assert source == "QUANTOS_STATE_ROOT/data/cache/status.json"
    assert str(state) not in source
    assert str(release_path) not in source


def test_data_catalog_coverage_contracts_remain_release_anchored(tmp_path: Path) -> None:
    from services.console.api import data_catalog

    release_config = Path(data_catalog._PROJECT_ROOT) / "config"
    assert Path(data_catalog._BINANCE_API_COVERAGE_CONTRACT) == (
        release_config / "binance_usdm_market_data_coverage.json"
    )
    assert Path(data_catalog._BINANCE_STREAM_COVERAGE_CONTRACT) == (
        release_config / "binance_usdm_ws_stream_coverage.json"
    )


@pytest.mark.parametrize(
    ("module", "expression"),
    (
        ("services.console.api.alpha_family_views", "module._DB"),
        ("services.console.api.alpha_library_views", "module._CRYPTO_SNAPSHOT_PATH"),
        ("services.console.api.artifact_diagnostic_views", "module._FX_RESEARCH_SNAPSHOT"),
        ("services.console.api.ashare_views", "module._ashare_snapshot_bundle()"),
        ("services.console.api.hourly_alpha_views", "module._hourly_alpha_cache_path()"),
        ("services.console.api.mft_execution_views", "module._mft_read_model_path()"),
        ("services.console.api.live_stream_views", "module._mft_read_model_path()"),
        ("services.console.api.data_catalog", "module._status_candidates('status.json')"),
        ("services.console.api.realtime_inputs", "module._status_candidates('status.json')"),
    ),
)
def test_console_p1_modules_fail_closed_on_relative_state_root(
    tmp_path: Path,
    module: str,
    expression: str,
) -> None:
    code = (
        "import importlib\n"
        f"module = importlib.import_module({module!r})\n"
        f"{expression}\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=_subprocess_env(QUANTOS_STATE_ROOT="relative-state"),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode != 0
    assert "QUANTOS_STATE_ROOT must be absolute" in completed.stderr
    assert not (tmp_path / "relative-state").exists()
    assert not (tmp_path / "data").exists()


def test_legacy_path_overrides_do_not_form_a_second_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.console.api import hourly_alpha_views, mft_execution_views

    state = (tmp_path / "state").resolve()
    monkeypatch.setenv("QUANTOS_STATE_ROOT", str(state))
    monkeypatch.setenv(
        "QUANTOS_HOURLY_ALPHA_STATE_ROOT", str(tmp_path / "legacy-hourly")
    )
    monkeypatch.setenv("QUANTOS_MFT_READ_MODEL_DB", str(tmp_path / "legacy-mft.db"))

    assert hourly_alpha_views._hourly_alpha_cache_path() == state / "data" / "cache"
    assert mft_execution_views._mft_read_model_path() == (
        state / "data" / "cache" / "mft_read_model.db"
    )


def _write_snapshot_set(root: Path, marker: str, *, names: tuple[str, ...]) -> None:
    directory = root / "data" / "stocks" / "snapshots"
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_text(json.dumps({"marker": marker}), encoding="utf-8")


def test_ashare_snapshot_set_is_atomic_state_first_then_complete_release_fallback(
    tmp_path: Path,
) -> None:
    from services.console.api.ashare_views import _load_bundle

    names = (
        "alpha_snapshot.json",
        "paper_track_snapshot.json",
        "klines_snapshot.json",
    )
    state = tmp_path / "state"
    release = tmp_path / "release"
    _write_snapshot_set(release, "release", names=names)
    _write_snapshot_set(state, "partial-state", names=names[:1])

    selected = _load_bundle(state) or _load_bundle(release)
    assert selected is not None
    assert {value["marker"] for value in selected} == {"release"}

    _write_snapshot_set(state, "state", names=names)
    selected = _load_bundle(state) or _load_bundle(release)
    assert selected is not None
    assert {value["marker"] for value in selected} == {"state"}

    (state / "data" / "stocks" / "snapshots" / names[1]).unlink()
    (release / "data" / "stocks" / "snapshots" / names[2]).unlink()
    assert (_load_bundle(state) or _load_bundle(release)) is None


def test_ashare_corrupt_state_bundle_falls_back_wholly_to_release(tmp_path: Path) -> None:
    from services.console.api.ashare_views import _load_bundle

    names = (
        "alpha_snapshot.json",
        "paper_track_snapshot.json",
        "klines_snapshot.json",
    )
    state = tmp_path / "state"
    release = tmp_path / "release"
    _write_snapshot_set(state, "state", names=names)
    _write_snapshot_set(release, "release", names=names)
    (state / "data" / "stocks" / "snapshots" / names[1]).write_text(
        "not-json", encoding="utf-8"
    )

    selected = _load_bundle(state) or _load_bundle(release)
    assert selected is not None
    assert {value["marker"] for value in selected} == {"release"}


def test_artifact_duplicate_routes_keep_paper_authority_first() -> None:
    from services.console.api.artifact_snapshots import router

    duplicates = {
        "/api/v1/console/ic-panel",
        "/api/v1/console/research-findings",
        "/api/v1/console/lance-1m-validation",
        "/api/v1/console/live-trading",
        "/api/v1/console/gonogo",
    }
    for path in duplicates:
        endpoints = [route.endpoint.__module__ for route in router.routes if route.path == path]
        assert endpoints[0] == "services.console.api.artifact_paper_views"

    for path in ("/api/v1/console/fx-research", "/api/v1/console/carry-readiness"):
        endpoints = [route.endpoint.__module__ for route in router.routes if route.path == path]
        assert endpoints == ["services.console.api.artifact_diagnostic_views"]


def test_mft_rest_and_sse_share_one_path_resolver() -> None:
    from services.console.api import live_stream_views, mft_execution_views

    assert live_stream_views._mft_read_model_path is mft_execution_views._mft_read_model_path
