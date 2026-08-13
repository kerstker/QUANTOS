from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from services.console.api.state_artifact import read_json_artifact
from services.governance.json_integrity import load_json_without_duplicate_keys


REPOSITORY = Path(__file__).resolve().parents[2]


def _valid_alpha(generated_at_ms: int) -> dict:
    return {
        "artifact": "ALPHA_LIFECYCLE_COCKPIT",
        "schema_version": 1,
        "generated_at_ms": generated_at_ms,
        "readiness": "L2_DRY_RUN_ONLY",
        "alphas": [{"alpha_id": f"alpha-{generated_at_ms}"}],
        "stage_legend": {},
        "honesty_note": "alpha evidence",
    }


def _valid_go_live(generated_at_ms: int) -> dict:
    return {
        "artifact": "GO_LIVE_READINESS",
        "generated_at_ms": generated_at_ms,
        "readiness": "L2_DRY_RUN_ONLY",
        "venues": [{"venue": f"venue-{generated_at_ms}"}],
        "strategies": {},
        "guidance": {},
        "honesty_note": "go-live evidence",
    }


def _valid_investor(generated_at_ms: int) -> dict:
    return {
        "artifact": "INVESTOR_SNAPSHOT",
        "schema_version": 1,
        "generated_at_ms": generated_at_ms,
        "readiness": "L2_DRY_RUN_ONLY",
        "data_cutoff_ms": None,
        "report_snapshot_hash": "a" * 128,
        "hash_scheme": {},
        "portfolio_views": {},
        "summary": {},
        "venues": [],
        "amendments": [],
        "unknown_fields": [],
        "amendment_policy": "append-only",
    }


def _valid_degradation(generated_at_ms: int) -> dict:
    return {
        "artifact": "LIVE_DEGRADATION",
        "schema_version": 1,
        "generated_at_ms": generated_at_ms,
        "readiness": "L2_DRY_RUN_ONLY",
        "cost_model": {},
        "replay_construction": "offline replay",
        "honesty_note": "degradation evidence",
        "summary": {"marker": generated_at_ms},
        "reports": [],
    }


CASES = (
    (
        "services.console.api.alpha_lifecycle_views",
        "_LIFECYCLE_ARTIFACT",
        "alpha_lifecycle_cockpit.json",
        "_alpha_lifecycle_payload",
        _valid_alpha,
    ),
    (
        "services.console.api.go_live_views",
        "_GO_LIVE_ARTIFACT",
        "go_live_readiness.json",
        "_go_live_readiness_payload",
        _valid_go_live,
    ),
    (
        "services.console.api.investor_snapshot_views",
        "_ARTIFACT",
        "investor_snapshot.json",
        "_investor_snapshot_payload",
        _valid_investor,
    ),
    (
        "services.console.api.live_degradation_views",
        "_ARTIFACT",
        "live_degradation.json",
        "_live_degradation_payload",
        _valid_degradation,
    ),
)


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _configure(
    *,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    module_name: str,
    release_attribute: str,
    filename: str,
) -> tuple[object, Path, Path]:
    state_root = (tmp_path / "state").resolve()
    release_path = (tmp_path / "release" / "evidence" / filename).resolve()
    monkeypatch.setenv("QUANTOS_STATE_ROOT", str(state_root))
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "_STATE_ROOT", state_root)
    monkeypatch.setattr(module, "_STATE_ARTIFACT", str(state_root / "evidence" / filename))
    monkeypatch.setattr(module, "_REPO_ROOT", release_path.parents[1])
    monkeypatch.setattr(module, release_attribute, str(release_path))
    return module, state_root / "evidence" / filename, release_path


@pytest.mark.parametrize(
    "module_name,release_attribute,filename,payload_name,builder", CASES
)
def test_valid_state_artifact_wins_without_cross_root_timestamp_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    release_attribute: str,
    filename: str,
    payload_name: str,
    builder,
) -> None:
    module, state_path, release_path = _configure(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        module_name=module_name,
        release_attribute=release_attribute,
        filename=filename,
    )
    _write(state_path, builder(10))
    _write(release_path, builder(999))
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (state_path, release_path)
    }

    payload = getattr(module, payload_name)()

    assert payload["snapshot_status"] == "REAL"
    assert payload["generated_at_ms"] == 10
    assert payload["artifact_provenance"] == "QUANTOS_STATE_ROOT"
    assert payload["snapshot_error_reason"] is None
    assert payload["release_fallback_suppressed"] is False
    assert {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (state_path, release_path)
    } == before


@pytest.mark.parametrize(
    "module_name,release_attribute,filename,payload_name,builder", CASES
)
def test_absent_state_uses_only_a_validated_release_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    release_attribute: str,
    filename: str,
    payload_name: str,
    builder,
) -> None:
    module, state_path, release_path = _configure(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        module_name=module_name,
        release_attribute=release_attribute,
        filename=filename,
    )
    _write(release_path, builder(20))
    release_before = release_path.read_bytes(), release_path.stat().st_mtime_ns

    payload = getattr(module, payload_name)()

    assert not state_path.exists()
    assert payload["snapshot_status"] == "REAL"
    assert payload["generated_at_ms"] == 20
    assert payload["artifact_provenance"] == "RELEASE_BASELINE"
    assert payload["snapshot_error_reason"] is None
    assert payload["release_fallback_suppressed"] is False
    assert (release_path.read_bytes(), release_path.stat().st_mtime_ns) == release_before


@pytest.mark.parametrize(
    "module_name,release_attribute,filename,payload_name,builder", CASES
)
def test_invalid_state_is_unavailable_and_suppresses_release_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    release_attribute: str,
    filename: str,
    payload_name: str,
    builder,
) -> None:
    module, state_path, release_path = _configure(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        module_name=module_name,
        release_attribute=release_attribute,
        filename=filename,
    )
    invalid_state = builder(30)
    invalid_state["generated_at_ms"] = True
    _write(state_path, invalid_state)
    _write(release_path, builder(999))
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (state_path, release_path)
    }

    payload = getattr(module, payload_name)()

    assert payload["snapshot_status"] == "UNAVAILABLE"
    assert payload["snapshot_present"] is False
    assert payload["data_provenance"] == "UNAVAILABLE"
    assert payload["generated_at_ms"] is None
    assert payload["artifact_provenance"] == "QUANTOS_STATE_ROOT"
    assert payload["snapshot_error_reason"] == "STATE_SNAPSHOT_INVALID"
    assert payload["release_fallback_suppressed"] is True
    assert "STATE_SNAPSHOT_INVALID" in payload["honesty_note"]
    assert "999" not in json.dumps(payload)
    assert {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (state_path, release_path)
    } == before


@pytest.mark.parametrize(
    "module_name,release_attribute,filename,payload_name,builder", CASES
)
def test_invalid_release_baseline_is_unavailable_when_state_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    release_attribute: str,
    filename: str,
    payload_name: str,
    builder,
) -> None:
    module, state_path, release_path = _configure(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        module_name=module_name,
        release_attribute=release_attribute,
        filename=filename,
    )
    invalid_release = builder(40)
    invalid_release["readiness"] = "LIVE"
    _write(release_path, invalid_release)

    payload = getattr(module, payload_name)()

    assert not state_path.exists()
    assert payload["snapshot_status"] == "UNAVAILABLE"
    assert payload["data_provenance"] == "UNAVAILABLE"
    assert payload["artifact_provenance"] == "RELEASE_BASELINE"
    assert payload["snapshot_error_reason"] == "RELEASE_SNAPSHOT_INVALID"
    assert payload["release_fallback_suppressed"] is False
    assert "RELEASE_SNAPSHOT_INVALID" in payload["honesty_note"]


@pytest.mark.parametrize(
    "module_name,release_attribute,filename,payload_name,builder", CASES
)
def test_both_artifacts_absent_remains_honest_no_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    release_attribute: str,
    filename: str,
    payload_name: str,
    builder,
) -> None:
    module, state_path, release_path = _configure(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        module_name=module_name,
        release_attribute=release_attribute,
        filename=filename,
    )

    payload = getattr(module, payload_name)()

    assert not state_path.exists() and not release_path.exists()
    assert payload["snapshot_status"] == "NO_SNAPSHOT"
    assert payload["data_provenance"] == "SYNTHETIC_DETERMINISTIC"
    assert payload["artifact_provenance"] is None
    assert payload["snapshot_error_reason"] is None
    assert payload["release_fallback_suppressed"] is False


@pytest.mark.parametrize("module_name", [case[0] for case in CASES])
def test_tracked_evidence_views_fail_closed_at_import_for_relative_state_root(
    tmp_path: Path,
    module_name: str,
) -> None:
    environ = {
        **os.environ,
        "PYTHONPATH": str(REPOSITORY),
        "QUANTOS_STATE_ROOT": "relative-state",
    }

    completed = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        cwd=tmp_path,
        env=environ,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode != 0
    assert "QUANTOS_STATE_ROOT must be absolute" in completed.stderr
    assert not (tmp_path / "relative-state").exists()
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize(
    "module_name,release_attribute,filename,payload_name,builder", CASES
)
def test_state_artifact_requires_the_complete_existing_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    release_attribute: str,
    filename: str,
    payload_name: str,
    builder,
) -> None:
    module, state_path, _ = _configure(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        module_name=module_name,
        release_attribute=release_attribute,
        filename=filename,
    )
    incomplete = builder(50)
    required_shape_field = {
        "alpha_lifecycle_cockpit.json": "alphas",
        "go_live_readiness.json": "guidance",
        "investor_snapshot.json": "summary",
        "live_degradation.json": "reports",
    }[filename]
    del incomplete[required_shape_field]
    _write(state_path, incomplete)

    payload = getattr(module, payload_name)()

    assert payload["snapshot_status"] == "UNAVAILABLE"
    assert payload["snapshot_error_reason"] == "STATE_SNAPSHOT_INVALID"


def _invalid_schema_mutations(filename: str) -> tuple[tuple[str, object], ...]:
    common = (
        ("artifact", "WRONG_ARTIFACT"),
        ("generated_at_ms", True),
        ("generated_at_ms", -1),
        ("readiness", "LIVE"),
    )
    per_artifact = {
        "alpha_lifecycle_cockpit.json": (
            ("schema_version", True),
            ("alphas", {}),
            ("alphas", [None]),
            ("stage_legend", []),
            ("honesty_note", " "),
        ),
        "go_live_readiness.json": (
            ("venues", {}),
            ("venues", [None]),
            ("strategies", []),
            ("guidance", []),
            ("honesty_note", " "),
        ),
        "investor_snapshot.json": (
            ("schema_version", True),
            ("data_cutoff_ms", True),
            ("data_cutoff_ms", -1),
            ("report_snapshot_hash", "a" * 127),
            ("report_snapshot_hash", "g" * 128),
            ("hash_scheme", []),
            ("amendment_policy", " "),
            ("portfolio_views", []),
            ("summary", []),
            ("venues", {}),
            ("venues", [None]),
            ("amendments", {}),
            ("amendments", [None]),
            ("unknown_fields", {}),
            ("unknown_fields", [None]),
            ("unknown_fields", [" "]),
        ),
        "live_degradation.json": (
            ("schema_version", True),
            ("cost_model", []),
            ("replay_construction", " "),
            ("honesty_note", " "),
            ("summary", []),
            ("reports", {}),
            ("reports", [None]),
        ),
    }
    return common + per_artifact[filename]


@pytest.mark.parametrize(
    "module_name,release_attribute,filename,payload_name,builder", CASES
)
def test_state_artifact_rejects_every_malformed_required_schema_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    release_attribute: str,
    filename: str,
    payload_name: str,
    builder,
) -> None:
    module, state_path, _ = _configure(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        module_name=module_name,
        release_attribute=release_attribute,
        filename=filename,
    )

    for field, malformed in _invalid_schema_mutations(filename):
        artifact = builder(60)
        artifact[field] = malformed
        _write(state_path, artifact)

        payload = getattr(module, payload_name)()

        assert payload["snapshot_status"] == "UNAVAILABLE", (field, malformed)
        assert payload["snapshot_error_reason"] == "STATE_SNAPSHOT_INVALID"
        assert payload["release_fallback_suppressed"] is True


@pytest.mark.parametrize(
    "content",
    (
        b'{"key": 1, "key": 2}',
        b'{"value": NaN}',
        b'{"value": Infinity}',
        b'{"value": -Infinity}',
    ),
)
def test_strict_artifact_reader_rejects_ambiguous_or_nonstandard_json(
    tmp_path: Path,
    content: bytes,
) -> None:
    path = tmp_path / "artifact.json"
    path.write_bytes(content)

    assert read_json_artifact(path, trusted_root=tmp_path) == ("INVALID", None)


def test_shared_strict_json_loader_rejects_float_overflow() -> None:
    with pytest.raises(ValueError, match="non-finite JSON number"):
        load_json_without_duplicate_keys('{"value": 1e9999}')


@pytest.mark.parametrize("location", ("key", "value"))
def test_shared_strict_json_loader_rejects_surrogate_code_points(
    location: str,
) -> None:
    payload = '{"\\ud800": 1}' if location == "key" else '{"value": "\\udfff"}'
    with pytest.raises(ValueError, match="surrogate code point"):
        load_json_without_duplicate_keys(payload)


@pytest.mark.parametrize(
    "module_name,release_attribute,filename,payload_name,builder", CASES
)
def test_complete_state_artifact_with_nested_float_overflow_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    release_attribute: str,
    filename: str,
    payload_name: str,
    builder,
) -> None:
    module, state_path, _ = _configure(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        module_name=module_name,
        release_attribute=release_attribute,
        filename=filename,
    )
    encoded = json.dumps(builder(100))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(encoded[:-1] + ', "nested_overflow": [1e9999]}')

    payload = getattr(module, payload_name)()

    assert payload["snapshot_status"] == "UNAVAILABLE"
    assert payload["snapshot_error_reason"] == "STATE_SNAPSHOT_INVALID"


@pytest.mark.parametrize(
    "module_name,release_attribute,filename,payload_name,builder", CASES
)
def test_complete_state_artifact_with_surrogate_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    release_attribute: str,
    filename: str,
    payload_name: str,
    builder,
) -> None:
    module, state_path, _ = _configure(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        module_name=module_name,
        release_attribute=release_attribute,
        filename=filename,
    )
    encoded = json.dumps(builder(110))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(encoded[:-1] + ', "nested_surrogate": "\\ud800"}')

    payload = getattr(module, payload_name)()

    assert payload["snapshot_status"] == "UNAVAILABLE"
    assert payload["snapshot_error_reason"] == "STATE_SNAPSHOT_INVALID"


def test_strict_artifact_reader_rejects_excessive_json_nesting(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_text("[" * 2000 + "0" + "]" * 2000, encoding="utf-8")

    assert read_json_artifact(path, trusted_root=tmp_path) == ("INVALID", None)


def test_strict_artifact_reader_does_not_count_brackets_inside_strings(
    tmp_path: Path,
) -> None:
    path = tmp_path / "artifact.json"
    expected = {"text": "[" * 1000 + "\\\"{still text}"}
    _write(path, expected)

    assert read_json_artifact(path, trusted_root=tmp_path) == ("VALID", expected)


def test_strict_artifact_reader_rejects_symlink_without_following_it(
    tmp_path: Path,
) -> None:
    target = tmp_path / "outside.json"
    target.write_text(json.dumps(_valid_alpha(70)), encoding="utf-8")
    link = tmp_path / "artifact.json"
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("symlink creation is unavailable")
    before = target.read_bytes(), target.stat().st_mtime_ns

    assert read_json_artifact(link, trusted_root=tmp_path) == ("INVALID", None)
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before


def test_strict_artifact_reader_rejects_parent_directory_symlink_escape(
    tmp_path: Path,
) -> None:
    trusted = tmp_path / "trusted"
    outside = tmp_path / "outside"
    outside.mkdir()
    _write(outside / "artifact.json", _valid_alpha(120))
    try:
        (trusted / "evidence").parent.mkdir(parents=True)
        (trusted / "evidence").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlink creation is unavailable")

    assert read_json_artifact(
        trusted / "evidence" / "artifact.json", trusted_root=trusted
    ) == ("INVALID", None)


def test_strict_artifact_reader_rejects_non_regular_path_before_open(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "artifact.json"
    directory.mkdir()

    assert read_json_artifact(directory, trusted_root=tmp_path) == ("INVALID", None)


def test_strict_artifact_reader_rejects_oversized_file(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_bytes(b" " * (8 * 1024 * 1024 + 1))

    assert read_json_artifact(path, trusted_root=tmp_path) == ("INVALID", None)


def test_strict_artifact_reader_rejects_identity_replacement_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.console.api import state_artifact

    path = tmp_path / "artifact.json"
    replacement = tmp_path / "replacement.json"
    _write(path, _valid_alpha(80))
    _write(replacement, _valid_alpha(81))
    real_fstat = state_artifact.os.fstat
    calls = 0

    def replace_after_open(fd: int):
        nonlocal calls
        result = real_fstat(fd)
        calls += 1
        if calls == 1:
            os.replace(replacement, path)
        return result

    monkeypatch.setattr(state_artifact.os, "fstat", replace_after_open)

    assert read_json_artifact(path, trusted_root=tmp_path) == ("INVALID", None)


def test_strict_artifact_reader_reports_close_failure_without_finally_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.console.api import state_artifact

    path = tmp_path / "artifact.json"
    _write(path, _valid_alpha(90))
    real_close = state_artifact.os.close

    def fail_close(fd: int) -> None:
        real_close(fd)
        raise OSError("simulated close failure")

    monkeypatch.setattr(state_artifact.os, "close", fail_close)

    assert read_json_artifact(path, trusted_root=tmp_path) == ("INVALID", None)


def test_strict_artifact_reader_handles_disappearance_after_component_walk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.console.api import state_artifact

    path = tmp_path / "artifact.json"
    _write(path, _valid_alpha(130))
    real_inspect = state_artifact._inspect_components
    calls = 0

    def remove_after_component_walk(target: Path, *, trusted_root: Path):
        nonlocal calls
        result = real_inspect(target, trusted_root=trusted_root)
        calls += 1
        if calls == 1:
            path.unlink()
        return result

    monkeypatch.setattr(state_artifact, "_inspect_components", remove_after_component_walk)

    assert read_json_artifact(path, trusted_root=tmp_path) == ("INVALID", None)
