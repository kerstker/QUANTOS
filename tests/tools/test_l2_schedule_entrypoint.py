from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
from types import SimpleNamespace
import sys

import pytest

from quantos.contracts.l2_schedule_policy import (
    HARD_QUARANTINED_MODULES,
    HARD_QUARANTINED_TASKS,
    MVP_CONDITIONAL_MODULES,
    MVP_DISABLED_MODULES,
    MVP_DISABLED_TASKS,
    MVP_EXPECTED_TASKS,
    MVP_RESIDENT_MODULES,
    MVP_SAFE_MODULES,
)
from tools.l2_schedule_entrypoint import (
    ALLOWED_L2_MODULES,
    QUARANTINED_LEGACY_AUTHORITY_MODULES,
    REQUIRED_INPUT_ATTESTATION_MODULES,
    ScheduleEntrypointError,
    _configure_headless_logging,
    _install_headless_subprocess_guard,
    _verify_release_identity,
    run_scheduled_module,
)


COMMIT = "a" * 40
ROOT = Path(__file__).resolve().parents[2]


def _worker_attestation(module: str = "binance_realtime_features") -> dict:
    return {
        "schema_version": 1,
        "module": module,
        "release_commit": COMMIT,
        "complete": True,
        "input_hashes": {
            "candidate_identity_set": "b" * 64,
            "historical_evaluations": "c" * 64,
        },
        "output_hashes": {
            "workflow_projection_file": "d" * 64,
            "population_chain_head": "e" * 64,
            "event_chain_head": "f" * 64,
        },
        "safety": {
            "paper_only": True,
            "live_allowed": False,
            "order_submission_allowed": False,
            "credentials_accessed": False,
            "broker_connectivity": False,
        },
    }


def _accept_release(root: Path, expected_commit: str) -> str:
    assert root.is_absolute()
    assert expected_commit == COMMIT
    return expected_commit


def _accept_release_evidence(
    root: Path,
    expected_commit: str,
) -> tuple[dict[str, object], str]:
    assert root.is_absolute()
    assert expected_commit == COMMIT
    return {
        "schema_version": 1,
        "readiness": "L2_DRY_RUN_ONLY",
        "validation": {"source_commit": COMMIT},
    }, "9" * 64


def _module(code_root: Path, *, result: object = 0, observed: list | None = None):
    module_path = code_root / "tools" / "worker.py"
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text("# fixture\n", encoding="utf-8")

    def main():
        if observed is not None:
            observed.append((Path.cwd(), tuple(sys.path)))
        return result

    return SimpleNamespace(__file__=str(module_path), main=main)


def _install_release_evidence_support(release: Path) -> None:
    scripts = release / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/evidence_binding.py", scripts / "evidence_binding.py")
    (scripts / "validate.py").write_text("# validation fixture\n", encoding="utf-8")
    contracts = release / "quantos" / "contracts"
    contracts.mkdir(parents=True)
    shutil.copyfile(
        ROOT / "quantos" / "contracts" / "l2_schedule_policy.py",
        contracts / "l2_schedule_policy.py",
    )


def _seal_release_evidence(release: Path, source_commit: str) -> str:
    from scripts.evidence_binding import write_release_evidence_manifest

    def write(relative: str, document: object) -> str:
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        return hashlib.sha256(path.read_bytes()).hexdigest()

    sbom_sha = write("evidence/sbom.cdx.json", {"bomFormat": "CycloneDX"})
    container_sha = write(
        "evidence/vulnerability/container_trivy_arm64_latest.json",
        {"schema_version": 1, "verdict": "PASS"},
    )
    reproducibility_sha = write(
        "evidence/reproducibility/container_build_arm64_latest.json",
        {"schema_version": 1, "verdict": "PASS"},
    )
    smoke_sha = write(
        "evidence/runtime/console_arm64_smoke_latest.json",
        {"schema_version": 1, "verdict": "PASS"},
    )
    checks, derived, phase13 = "1" * 64, "2" * 64, "3" * 64
    run_payload = json.dumps(
        {
            "domain": "quantos-validation-run:v2",
            "source_commit": source_commit,
            "checks_manifest_sha256": checks,
            "derived_refresh_manifest_sha256": derived,
            "phase13_manifest_sha256": phase13,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    binding = {
        "run_id": hashlib.sha256(run_payload).hexdigest(),
        "source_commit": source_commit,
        "generated_at": 1,
        "current": True,
    }
    stages = [
        "phase13-readiness:refresh",
        "phase13-readiness:current",
        "phase13-readiness:tests",
    ]
    write(
        "evidence/validation_report.json",
        {
            "schema_version": 1,
            "report_kind": "FAIL_CLOSED_VALIDATION",
            "commit": source_commit,
            "validation": binding,
            "readiness": "L2_DRY_RUN_ONLY",
            "selection_scope": "ALL",
            "selected_stages": stages,
            "checks_manifest_sha256": checks,
            "derived_refresh_manifest_sha256": derived,
            "phase13_manifest_sha256": phase13,
            "validate_script_sha256": hashlib.sha256(
                (release / "scripts/validate.py").read_bytes()
            ).hexdigest(),
            "summary": {
                "selected": 3,
                "passed": 3,
                "failed": 0,
                "timed_out": 0,
                "errored": 0,
            },
            "overall_status": "PASS",
            "results": [{"stage": stage, "status": "PASS"} for stage in stages],
        },
    )
    write(
        "evidence/supply_chain_baseline.json",
        {
            "schema_version": 1,
            "validation": binding,
            "verdict": "PARTIAL",
            "sbom_sha256": sbom_sha,
            "container_vulnerability_evidence_status": (
                "VERIFIED_EXTERNAL_BASE_REMEDIATED_RUNTIME_ZERO_KNOWN"
            ),
            "container_vulnerability_evidence_sha256": container_sha,
            "container_runtime_image_digest": "sha256:" + "4" * 64,
            "container_reproducibility_evidence_status": (
                "VERIFIED_LOCAL_TWO_COLD_BUILDS_BYTE_IDENTICAL"
            ),
            "container_reproducibility_evidence_sha256": reproducibility_sha,
            "container_reproducible_archive_sha256": "5" * 64,
            "container_reproducible_manifest_digest": "sha256:" + "4" * 64,
            "container_smoke_evidence_status": (
                "VERIFIED_NATIVE_ARM64_READ_ONLY_HEALTH"
            ),
            "container_smoke_evidence_sha256": smoke_sha,
            "cross_host_container_reproducibility_status": "NOT_PROVIDED",
            "artifact_signature_status": "VERIFIER_READY_EXTERNAL_SIGNATURE_MISSING",
        },
    )
    write(
        "evidence/top_tier_assessment.json",
        {"schema_version": 1, "validation": binding, "verdict": "NOT_TOP_TIER"},
    )
    write_release_evidence_manifest(release)
    subprocess.run(["git", "add", "."], cwd=release, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "release evidence"],
        cwd=release,
        check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=release,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


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
        expected_release_commit=COMMIT,
        release_verifier=_accept_release,
        release_evidence_verifier=_accept_release_evidence,
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
            expected_release_commit=COMMIT,
            release_verifier=_accept_release,
            release_evidence_verifier=_accept_release_evidence,
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
            expected_release_commit=COMMIT,
            release_verifier=_accept_release,
            release_evidence_verifier=_accept_release_evidence,
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
            expected_release_commit=COMMIT,
            release_verifier=_accept_release,
            release_evidence_verifier=_accept_release_evidence,
            importer=lambda _name: module,
        )


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [(None, 0), (0, 0), (1, 1), (2, 2)],
)
def test_entrypoint_normalizes_system_exit_to_worker_result(
    tmp_path: Path,
    exit_code: int | None,
    expected: int,
) -> None:
    code_root = tmp_path / "release"
    module = _module(code_root)

    def exit_from_main() -> None:
        raise SystemExit(exit_code)

    module.main = exit_from_main

    assert run_scheduled_module(
        "domain_event_ops",
        code_root=code_root,
        expected_release_commit=COMMIT,
        release_verifier=_accept_release,
        release_evidence_verifier=_accept_release_evidence,
        importer=lambda _name: module,
    ) == expected


@pytest.mark.parametrize("exit_code", [True, "failure", 1.5])
def test_entrypoint_rejects_ambiguous_system_exit_code(
    tmp_path: Path,
    exit_code: object,
) -> None:
    code_root = tmp_path / "release"
    module = _module(code_root)

    def exit_from_main() -> None:
        raise SystemExit(exit_code)

    module.main = exit_from_main

    with pytest.raises(ScheduleEntrypointError, match="exit code"):
        run_scheduled_module(
            "domain_event_ops",
            code_root=code_root,
            expected_release_commit=COMMIT,
            release_verifier=_accept_release,
            release_evidence_verifier=_accept_release_evidence,
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
            expected_release_commit=COMMIT,
            release_verifier=_accept_release,
            release_evidence_verifier=_accept_release_evidence,
            importer=lambda _name: module,
        )
        == 0
    )
    assert seen == [(str(Path(module.__file__).resolve()),)]
    assert sys.argv == launcher_argv


def test_entrypoint_binds_release_identity_into_worker_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    code_root = tmp_path / "release"
    seen = []
    module = _module(code_root)
    module.main = lambda: seen.append(
        (
            os.environ.get("QUANTOS_RELEASE_COMMIT"),
            os.environ.get("QUANTOS_RELEASE_ROOT"),
            os.environ.get("QUANTOS_STATE_ROOT"),
        )
    ) or 0

    assert run_scheduled_module(
        "domain_event_ops",
        code_root=code_root,
        expected_release_commit=COMMIT,
        release_verifier=_accept_release,
        release_evidence_verifier=_accept_release_evidence,
        importer=lambda _name: module,
    ) == 0

    assert seen == [(COMMIT, str(code_root.resolve()), str(Path.cwd().resolve()))]
    assert "QUANTOS_RELEASE_COMMIT" not in os.environ
    assert "QUANTOS_RELEASE_ROOT" not in os.environ
    assert "QUANTOS_STATE_ROOT" not in os.environ


def test_entrypoint_rejects_release_and_state_root_overlap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    code_root = tmp_path / "release"
    code_root.mkdir()
    monkeypatch.chdir(code_root)

    with pytest.raises(ScheduleEntrypointError, match="separate and non-overlapping"):
        run_scheduled_module(
            "domain_event_ops",
            code_root=code_root,
            expected_release_commit=COMMIT,
            release_verifier=_accept_release,
            release_evidence_verifier=_accept_release_evidence,
            importer=lambda _name: _module(code_root),
        )


def test_entrypoint_reports_release_verification_before_worker_runs(
    tmp_path: Path,
) -> None:
    code_root = tmp_path / "release"
    observed = []
    module = _module(code_root, result=0)

    assert run_scheduled_module(
        "domain_event_ops",
        code_root=code_root,
        expected_release_commit=COMMIT,
        release_verifier=_accept_release,
        release_evidence_verifier=_accept_release_evidence,
        on_release_verified=lambda root, commit: observed.append((root, commit)),
        importer=lambda _name: module,
    ) == 0

    assert observed == [(code_root.resolve(), COMMIT)]


def test_entrypoint_requires_release_evidence_before_worker_import(
    tmp_path: Path,
) -> None:
    code_root = tmp_path / "release"
    imported = []

    def reject_evidence(_root: Path, _commit: str):
        raise ScheduleEntrypointError("release evidence verification failed")

    with pytest.raises(ScheduleEntrypointError, match="release evidence"):
        run_scheduled_module(
            "domain_event_ops",
            code_root=code_root,
            expected_release_commit=COMMIT,
            release_verifier=_accept_release,
            release_evidence_verifier=reject_evidence,
            importer=lambda name: imported.append(name),
        )

    assert imported == []


def test_entrypoint_reports_release_evidence_before_worker_runs(
    tmp_path: Path,
) -> None:
    code_root = tmp_path / "release"
    observed = []
    module = _module(code_root, result=0)

    assert run_scheduled_module(
        "domain_event_ops",
        code_root=code_root,
        expected_release_commit=COMMIT,
        release_verifier=_accept_release,
        release_evidence_verifier=_accept_release_evidence,
        on_release_evidence_verified=lambda document, digest: observed.append(
            (document, digest)
        ),
        importer=lambda _name: module,
    ) == 0

    assert observed == [(_accept_release_evidence(code_root.resolve(), COMMIT))]


@pytest.mark.parametrize(
    "module_name",
    sorted(REQUIRED_INPUT_ATTESTATION_MODULES & ALLOWED_L2_MODULES),
)
def test_required_worker_fails_closed_without_input_attestation(
    tmp_path: Path,
    module_name: str,
) -> None:
    code_root = tmp_path / "release"
    module = _module(code_root, result=0)

    with pytest.raises(ScheduleEntrypointError, match="input/evidence attestation"):
        run_scheduled_module(
            module_name,
            code_root=code_root,
            expected_release_commit=COMMIT,
            release_verifier=_accept_release,
            release_evidence_verifier=_accept_release_evidence,
            importer=lambda _name: module,
        )


@pytest.mark.parametrize(
    "module_name",
    sorted(REQUIRED_INPUT_ATTESTATION_MODULES & ALLOWED_L2_MODULES),
)
def test_required_worker_attestation_is_validated_and_hash_bound(
    tmp_path: Path,
    module_name: str,
) -> None:
    code_root = tmp_path / "release"
    module = _module(code_root, result=0)
    module.release_run_attestation = lambda: _worker_attestation(module_name)
    observed = []

    assert run_scheduled_module(
        module_name,
        code_root=code_root,
        expected_release_commit=COMMIT,
        release_verifier=_accept_release,
        release_evidence_verifier=_accept_release_evidence,
        importer=lambda _name: module,
        on_worker_attested=lambda document, digest: observed.append(
            (document, digest)
        ),
    ) == 0

    assert observed[0][0] == _worker_attestation(module_name)
    assert re.fullmatch(r"[0-9a-f]{64}", observed[0][1])


def test_required_worker_rejects_malformed_input_hash(tmp_path: Path) -> None:
    code_root = tmp_path / "release"
    module = _module(code_root, result=0)
    malformed = _worker_attestation("binance_realtime_features")
    malformed["input_hashes"]["historical_evaluations"] = "not-a-digest"
    module.release_run_attestation = lambda: malformed

    with pytest.raises(ScheduleEntrypointError, match="input/evidence attestation"):
        run_scheduled_module(
            "binance_realtime_features",
            code_root=code_root,
            expected_release_commit=COMMIT,
            release_verifier=_accept_release,
            release_evidence_verifier=_accept_release_evidence,
            importer=lambda _name: module,
        )


def test_required_worker_fatal_exit_preserves_result_without_attestation(
    tmp_path: Path,
) -> None:
    code_root = tmp_path / "release"
    module = _module(code_root, result=1)
    module.release_run_attestation = lambda: None
    observed = []

    assert run_scheduled_module(
        "binance_realtime_features",
        code_root=code_root,
        expected_release_commit=COMMIT,
        release_verifier=_accept_release,
        release_evidence_verifier=_accept_release_evidence,
        importer=lambda _name: module,
        on_worker_attested=lambda document, digest: observed.append((document, digest)),
    ) == 1

    assert observed == []


def test_release_identity_verifies_clean_head_without_optional_git_locks(
    tmp_path: Path,
) -> None:
    calls = []

    class Completed:
        def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
            self.stdout = stdout
            self.returncode = returncode
            self.stderr = stderr

    results = [Completed(COMMIT + "\n"), Completed("")]

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return results.pop(0)

    assert _verify_release_identity(tmp_path.resolve(), COMMIT, runner=runner) == COMMIT
    safe_directory = f"safe.directory={tmp_path.resolve()}"
    assert calls[0][0][:5] == [
        "git",
        "-c",
        safe_directory,
        "-C",
        str(tmp_path.resolve()),
    ]
    assert calls[1][0][:5] == [
        "git",
        "-c",
        safe_directory,
        "-C",
        str(tmp_path.resolve()),
    ]
    assert calls[0][0][-3:] == ["rev-parse", "--verify", "HEAD"]
    assert calls[1][0][-4:] == [
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignore-submodules=none",
        "--",
    ]
    assert calls[0][1]["env"]["GIT_OPTIONAL_LOCKS"] == "0"


def test_release_identity_rejects_dirty_or_mismatched_release(tmp_path: Path) -> None:
    class Completed:
        def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
            self.stdout = stdout
            self.returncode = returncode
            self.stderr = stderr

    dirty = iter([Completed(COMMIT + "\n"), Completed(" M tools/worker.py\n")])
    with pytest.raises(ScheduleEntrypointError, match="not clean"):
        _verify_release_identity(
            tmp_path.resolve(),
            COMMIT,
            runner=lambda *_args, **_kwargs: next(dirty),
        )

    mismatch = iter([Completed("b" * 40 + "\n"), Completed("")])
    with pytest.raises(ScheduleEntrypointError, match="does not match"):
        _verify_release_identity(
            tmp_path.resolve(),
            COMMIT,
            runner=lambda *_args, **_kwargs: next(mismatch),
        )


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


def test_pythonw_mode_forces_child_processes_to_stay_headless(monkeypatch) -> None:
    observed = []

    class FakePopen:
        def __init__(self, *args, **kwargs):
            observed.append((args, kwargs))

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010, raising=False)

    _install_headless_subprocess_guard(
        executable=r"H:\miniconda\pythonw.exe",
        platform_name="nt",
    )
    subprocess.Popen(["helper.exe"], creationflags=0x00000200)

    assert observed[0][1]["creationflags"] == 0x08000200


def test_pythonw_mode_rejects_child_request_for_new_console(monkeypatch) -> None:
    class FakePopen:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010, raising=False)

    _install_headless_subprocess_guard(
        executable=r"H:\miniconda\pythonw.exe",
        platform_name="nt",
    )

    with pytest.raises(ScheduleEntrypointError, match="interactive console"):
        subprocess.Popen(["helper.exe"], creationflags=0x00000010)


def test_static_mvp_schedule_policy_is_complete_and_disjoint() -> None:
    assert MVP_EXPECTED_TASKS == {
        "QUANTOS_artifact_admission_seal": "artifact_admission_seal",
        "QUANTOS_ashare_alpha_search": "ashare_alpha_search_worker",
        "QUANTOS_ashare_forward_observation": "ashare_forward_observation",
        "QUANTOS_binance_book_depth_forward_refresh": (
            "binance_book_depth_forward_refresh"
        ),
        "QUANTOS_binance_realtime_drift_alerts": "binance_realtime_drift_alerts",
        "QUANTOS_binance_realtime_features": "binance_realtime_features",
        "QUANTOS_binance_realtime_market": "binance_realtime_market",
        "QUANTOS_binance_realtime_stream": "binance_realtime_stream",
        "QUANTOS_binance_rpi_depth": "binance_rpi_depth",
        "QUANTOS_binance_systemic_risk": "binance_systemic_risk",
        "QUANTOS_bybit_realtime_stream": "bybit_realtime_stream",
        "QUANTOS_cftc_tff_forward_refresh": "cftc_tff_forward_refresh",
        "QUANTOS_cross_venue_liquidation_health": (
            "cross_venue_liquidation_health"
        ),
        "QUANTOS_defillama_stablecoin_forward_refresh": (
            "defillama_stablecoin_forward_refresh"
        ),
        "QUANTOS_defillama_tvl_forward_refresh": "defillama_tvl_forward_refresh",
        "QUANTOS_domain_event_ops": "domain_event_ops",
        "QUANTOS_domain_event_seal": "domain_event_seal",
        "QUANTOS_external_heartbeat": "external_heartbeat",
        "QUANTOS_hyperliquid_public_forward_refresh": (
            "hyperliquid_public_forward_refresh"
        ),
        "QUANTOS_backup": "l2_backup",
        "QUANTOS_okx_borrow_forward_refresh": "okx_borrow_forward_refresh",
        "QUANTOS_raw_archive_audit": "raw_archive_audit",
        "QUANTOS_scheduler_watchdog": "l2_scheduler_watchdog",
        "QUANTOS_wikimedia_attention_forward_refresh": (
            "wikimedia_attention_forward_refresh"
        ),
    }
    assert HARD_QUARANTINED_TASKS == {
        "QUANTOS_alpha_lifecycle_cockpit": "alpha_lifecycle_cockpit",
        "QUANTOS_alpha_maintenance": "alpha_maintenance_sweep",
        "QUANTOS_alpha_paper_cycle": "alpha_paper_cycle",
        "QUANTOS_alpha_production_workflow": "alpha_production_workflow",
        "QUANTOS_candidate_dry_run": "dry_run_candidate_multi",
        "QUANTOS_candidate_portfolio_evidence": "candidate_portfolio_evidence",
        "QUANTOS_factory_loop": "crypto_factory_loop",
        "QUANTOS_ic_panel_refresh": "ic_panel_research",
        "QUANTOS_paper_alpha_evaluation": "paper_alpha_evaluation",
        "QUANTOS_paper_realtime": "paper_realtime",
        "QUANTOS_paper_realtime_10k": "paper_realtime_10k",
        "QUANTOS_paper_research_risk_bundle": "paper_research_risk_bundle",
        "QUANTOS_register_alpha_catalog": "register_alpha_catalog",
    }
    assert MVP_DISABLED_TASKS == {
        "QUANTOS_forward_track": "forward_track_advance",
        "QUANTOS_gonogo_report": "gonogo_report",
        "QUANTOS_hourly_alpha_advance": "hourly_alpha_advance",
        "QUANTOS_mft_l2_runtime": "mft_l2_runtime",
        "QUANTOS_paper_health": "paper_health",
        "QUANTOS_paper_multivenue_sor": "paper_multivenue_sor",
        "QUANTOS_paper_operational_evidence": "paper_operational_evidence",
        "QUANTOS_paper_order_preview": "paper_order_preview",
        "QUANTOS_paper_shadow_auto": "paper_shadow_auto",
        "QUANTOS_paper_shadow_realtime": "paper_shadow_realtime",
        "QUANTOS_realtime_alpha_discovery": "realtime_alpha_discovery",
        "QUANTOS_realtime_alpha_execution_paper": (
            "realtime_alpha_execution_paper"
        ),
        "QUANTOS_realtime_alpha_formal_shadow": "realtime_alpha_formal_shadow",
        "QUANTOS_realtime_alpha_forward_shadow": "realtime_alpha_forward_shadow",
        "QUANTOS_rpi_low_frequency_research": "rpi_low_frequency_research",
    }

    assert MVP_SAFE_MODULES == frozenset(MVP_EXPECTED_TASKS.values())
    assert HARD_QUARANTINED_MODULES == frozenset(HARD_QUARANTINED_TASKS.values())
    assert MVP_DISABLED_MODULES == frozenset(MVP_DISABLED_TASKS.values())
    assert (len(MVP_SAFE_MODULES), len(HARD_QUARANTINED_MODULES), len(MVP_DISABLED_MODULES)) == (
        24,
        13,
        15,
    )
    assert MVP_SAFE_MODULES.isdisjoint(HARD_QUARANTINED_MODULES)
    assert MVP_SAFE_MODULES.isdisjoint(MVP_DISABLED_MODULES)
    assert HARD_QUARANTINED_MODULES.isdisjoint(MVP_DISABLED_MODULES)
    assert MVP_RESIDENT_MODULES == frozenset(
        {"binance_realtime_stream", "bybit_realtime_stream"}
    )
    assert MVP_CONDITIONAL_MODULES == frozenset(
        {
            "artifact_admission_seal",
            "domain_event_seal",
            "external_heartbeat",
            "l2_backup",
        }
    )
    assert ALLOWED_L2_MODULES == MVP_SAFE_MODULES
    assert QUARANTINED_LEGACY_AUTHORITY_MODULES == HARD_QUARANTINED_MODULES


def test_required_input_attestation_surface_is_explicit() -> None:
    assert REQUIRED_INPUT_ATTESTATION_MODULES == frozenset(
        {
            "ashare_alpha_search_worker",
            "ashare_forward_observation",
            "binance_realtime_market",
            "binance_realtime_features",
        }
    )


def test_legacy_authority_workers_are_quarantined_before_release_or_import(
    tmp_path: Path,
) -> None:
    assert QUARANTINED_LEGACY_AUTHORITY_MODULES == frozenset(
        {
            "alpha_paper_cycle",
            "alpha_lifecycle_cockpit",
            "alpha_maintenance_sweep",
            "alpha_production_workflow",
            "candidate_portfolio_evidence",
            "dry_run_candidate_multi",
            "crypto_factory_loop",
            "ic_panel_research",
            "paper_alpha_evaluation",
            "paper_realtime",
            "paper_realtime_10k",
            "paper_research_risk_bundle",
            "register_alpha_catalog",
        }
    )
    assert QUARANTINED_LEGACY_AUTHORITY_MODULES.isdisjoint(ALLOWED_L2_MODULES)

    for module_name in sorted(QUARANTINED_LEGACY_AUTHORITY_MODULES):
        touched: list[str] = []

        def forbidden(label):
            def _forbidden(*_args, **_kwargs):
                touched.append(label)
                pytest.fail(f"{label} ran before scheduler quarantine")

            return _forbidden

        with pytest.raises(
            ScheduleEntrypointError,
            match="INVALIDATED_PERP_LEDGER_V1",
        ):
            run_scheduled_module(
                module_name,
                code_root=tmp_path / "release",
                expected_release_commit=COMMIT,
                release_verifier=forbidden("release verifier"),
                release_evidence_verifier=forbidden("release evidence"),
                importer=forbidden("module importer"),
            )

        assert touched == []


def test_mvp_disabled_workers_are_rejected_before_release_or_import(
    tmp_path: Path,
) -> None:
    for module_name in sorted(MVP_DISABLED_MODULES):
        touched: list[str] = []

        def forbidden(label):
            def _forbidden(*_args, **_kwargs):
                touched.append(label)
                pytest.fail(f"{label} ran before MVP policy rejection")

            return _forbidden

        with pytest.raises(
            ScheduleEntrypointError,
            match="L2_MVP_POLICY_DISABLED",
        ):
            run_scheduled_module(
                module_name,
                code_root=tmp_path / "release",
                expected_release_commit=COMMIT,
                release_verifier=forbidden("release verifier"),
                release_evidence_verifier=forbidden("release evidence"),
                importer=forbidden("module importer"),
            )

        assert touched == []


def test_every_registered_schedule_module_is_statically_classified() -> None:
    registration = (
        Path(__file__).resolve().parents[2] / "scripts" / "register_schedules.ps1"
    ).read_text(encoding="utf-8")
    registered = set(re.findall(r'-Module\s+"([a-z0-9_]+)"', registration))

    assert registered
    assert registered <= MVP_SAFE_MODULES | MVP_DISABLED_MODULES
    assert registered.isdisjoint(HARD_QUARANTINED_MODULES)


def test_script_entrypoint_ignores_dirty_state_root_pythonpath(tmp_path: Path) -> None:
    source_launcher = ROOT / "tools" / "l2_schedule_entrypoint.py"
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
    dirty_contracts = state / "quantos" / "contracts"
    dirty_contracts.mkdir(parents=True)
    (dirty_contracts / "l2_schedule_policy.py").write_text(
        "raise RuntimeError('DIRTY POLICY LOADED')\n",
        encoding="utf-8",
    )
    launcher = release / "tools" / "l2_schedule_entrypoint.py"
    launcher.write_bytes(source_launcher.read_bytes())
    _install_release_evidence_support(release)
    subprocess.run(["git", "init", "-q"], cwd=release, check=True)
    subprocess.run(
        ["git", "config", "user.email", "quantos-tests@example.invalid"],
        cwd=release,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "QUANTOS Tests"],
        cwd=release,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=release, check=True)
    subprocess.run(["git", "commit", "-qm", "release"], cwd=release, check=True)
    source_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=release,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    commit = _seal_release_evidence(release, source_commit)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(state)

    completed = subprocess.run(
        [
            sys.executable,
            str(launcher),
            "domain_event_ops",
            "--release-commit",
            commit,
        ],
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
    receipts = list((state / ".runtime" / "l2-release-runs").glob("*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["status"] == "COMPLETED"
    assert receipt["release_commit"] == commit
    assert receipt["release_verified"] is True
    assert receipt["release_evidence_verified"] is True
    assert re.fullmatch(r"[0-9a-f]{64}", receipt["release_evidence_sha256"])
    assert receipt["verified_at"] is not None


def test_script_receipt_embeds_required_worker_input_attestation(
    tmp_path: Path,
) -> None:
    source_launcher = ROOT / "tools" / "l2_schedule_entrypoint.py"
    release = tmp_path / "release"
    state = tmp_path / "state"
    tools = release / "tools"
    tools.mkdir(parents=True)
    state.mkdir()
    (tools / "__init__.py").write_text("", encoding="utf-8")
    (tools / "binance_realtime_features.py").write_text(
        "import os\n"
        "def main():\n"
        "    return 0\n"
        "def release_run_attestation():\n"
        "    return {\n"
        "        'schema_version': 1,\n"
        "        'module': 'binance_realtime_features',\n"
        "        'release_commit': os.environ['QUANTOS_RELEASE_COMMIT'],\n"
        "        'complete': True,\n"
        "        'input_hashes': {'candidate_identity_set': 'b' * 64},\n"
        "        'output_hashes': {'workflow_projection_file': 'c' * 64},\n"
        "        'safety': {\n"
        "            'paper_only': True,\n"
        "            'live_allowed': False,\n"
        "            'order_submission_allowed': False,\n"
        "            'credentials_accessed': False,\n"
        "            'broker_connectivity': False,\n"
        "        },\n"
        "    }\n",
        encoding="utf-8",
    )
    launcher = tools / "l2_schedule_entrypoint.py"
    launcher.write_bytes(source_launcher.read_bytes())
    _install_release_evidence_support(release)
    subprocess.run(["git", "init", "-q"], cwd=release, check=True)
    subprocess.run(
        ["git", "config", "user.email", "quantos-tests@example.invalid"],
        cwd=release,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "QUANTOS Tests"],
        cwd=release,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=release, check=True)
    subprocess.run(["git", "commit", "-qm", "release"], cwd=release, check=True)
    source_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=release,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    commit = _seal_release_evidence(release, source_commit)

    completed = subprocess.run(
        [
            sys.executable,
            str(launcher),
            "binance_realtime_features",
            "--release-commit",
            commit,
        ],
        cwd=state,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0
    receipts = list((state / ".runtime" / "l2-release-runs").glob("*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["schema_version"] == 3
    assert receipt["release_evidence_required"] is True
    assert receipt["release_evidence_verified"] is True
    assert receipt["worker_attestation_required"] is True
    assert receipt["worker_attested"] is True
    assert re.fullmatch(r"[0-9a-f]{64}", receipt["worker_attestation_sha256"])
    assert receipt["worker_attestation"]["release_commit"] == commit


def test_script_receipt_distinguishes_verified_system_exit_failure(
    tmp_path: Path,
) -> None:
    source_launcher = ROOT / "tools" / "l2_schedule_entrypoint.py"
    release = tmp_path / "release"
    state = tmp_path / "state"
    tools = release / "tools"
    tools.mkdir(parents=True)
    state.mkdir()
    (tools / "__init__.py").write_text("", encoding="utf-8")
    (tools / "domain_event_ops.py").write_text(
        "def main():\n    raise SystemExit(7)\n",
        encoding="utf-8",
    )
    launcher = tools / "l2_schedule_entrypoint.py"
    launcher.write_bytes(source_launcher.read_bytes())
    _install_release_evidence_support(release)
    subprocess.run(["git", "init", "-q"], cwd=release, check=True)
    subprocess.run(
        ["git", "config", "user.email", "quantos-tests@example.invalid"],
        cwd=release,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "QUANTOS Tests"],
        cwd=release,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=release, check=True)
    subprocess.run(["git", "commit", "-qm", "release"], cwd=release, check=True)
    source_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=release,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    commit = _seal_release_evidence(release, source_commit)

    completed = subprocess.run(
        [
            sys.executable,
            str(launcher),
            "domain_event_ops",
            "--release-commit",
            commit,
        ],
        cwd=state,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 7
    receipts = list((state / ".runtime" / "l2-release-runs").glob("*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["status"] == "FAILED"
    assert receipt["exit_code"] == 7
    assert receipt["release_verified"] is True
    assert receipt["release_evidence_verified"] is True
    assert receipt["verified_at"] is not None
