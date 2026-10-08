"""Regression tests for the observable, fail-closed validation runner."""

import importlib.util
import json
import os
import subprocess
import sys
import pytest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_validate_script_starts_from_outside_repo():
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate.py"), "--help"],
        cwd=ROOT.parent,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "validation_report.json" in result.stdout


def _load_validate_module(repo_root=None):
    path = ROOT / "scripts" / "validate.py"
    spec = importlib.util.spec_from_file_location("quantos_validate_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if repo_root is not None:
        module.REPO = str(repo_root)
    return module


@pytest.fixture
def clean_validation_repo(tmp_path):
    """Real Git identity for synthetic full-run tests, not the developer checkout."""
    repo = tmp_path / "source"
    repo.mkdir()
    for args in (
        ["init", "--quiet"],
        ["-c", "user.name=Validation Test", "-c", "user.email=validation@example.invalid",
         "-c", "commit.gpgsign=false",
         "commit", "--quiet", "--allow-empty", "-m", "Synthetic validation source"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=10)
    return repo


@pytest.mark.parametrize("staged", [False, True])
def test_full_run_still_rejects_dirty_source_before_executing(
    tmp_path, clean_validation_repo, staged,
):
    validate = _load_validate_module(clean_validation_repo)
    dirty = clean_validation_repo / "unreviewed.py"
    dirty.write_text("VALUE = 1\n", encoding="utf-8")
    if staged:
        subprocess.run(["git", "add", "unreviewed.py"], cwd=clean_validation_repo,
                       check=True, capture_output=True, timeout=10)
    marker = tmp_path / "executed.txt"
    report = tmp_path / "validation.json"
    command = [sys.executable, "-c",
               "import pathlib,sys;pathlib.Path(sys.argv[1]).touch()", str(marker)]
    with pytest.raises(ValueError, match="dirty path"):
        validate.run(
            json_report=str(report), checks=[("synthetic", command, str(tmp_path))],
            derived_refresh_checks=[], phase13_refresh_checks=_fake_phase13(tmp_path),
            timeout_seconds=2,
        )
    assert not marker.exists()
    assert not report.exists()


def _fake_phase13(tmp_path):
    return [
        (name, [sys.executable, "-c", "pass"], str(tmp_path))
        for name in (
            "phase13-readiness:refresh",
            "phase13-readiness:current",
            "phase13-readiness:tests",
        )
    ]


def test_selected_stage_writes_machine_report(tmp_path):
    validate = _load_validate_module()
    report_path = tmp_path / "validation.json"
    checks = [
        (
            "fake-pass",
            [sys.executable, "-c", "pass"],
            str(tmp_path),
        )
    ]

    result = validate.run(
        stages=["fake-pass"],
        json_report=str(report_path),
        timeout_seconds=2,
        checks=checks,
    )

    assert result == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["overall_status"] == "PASS"
    assert report["selection_scope"] == "SUBSET"
    assert report["summary"] == {
        "selected": 1,
        "passed": 1,
        "failed": 0,
        "timed_out": 0,
        "errored": 0,
        # 2026-09-20 审计 P0-3:汇总新增 skip 计数,使"诚实 skip"与"整体没跑"
        # 在机器报告里可区分(本用例是非 pytest 命令,故为 0)。
        "skipped": 0,
    }
    assert report["results"][0]["status"] == "PASS"


def test_pytest_child_does_not_inherit_canonical_identity(tmp_path):
    validate = _load_validate_module()
    report_path = tmp_path / "validation.json"
    test_file = tmp_path / "test_binding.py"
    test_file.write_text(
        "import os\n"
        "def test_explicit_binding_is_local():\n"
        "    assert 'QUANTOS_VALIDATION_RUN_ID' not in os.environ\n"
        "    assert 'QUANTOS_VALIDATION_SOURCE_COMMIT' not in os.environ\n"
        "    assert 'QUANTOS_VALIDATION_STARTED_AT_MS' not in os.environ\n"
        "    from scripts.evidence_binding import validation_binding\n"
        "    validation_binding(generated_at=1, run_id='a' * 64, source_commit='b' * 40)\n",
        encoding="utf-8",
    )
    checks = [
        (
            "pytest: synthetic canonical environment isolation",
            [sys.executable, "-m", "pytest", "-q", str(test_file)],
            str(ROOT),
        )
    ]

    result = validate.run(
        stages=["pytest: synthetic canonical environment isolation"],
        json_report=str(report_path),
        # This checks child environment isolation, not pytest startup latency.
        # Keep a finite startup allowance under the full ARM64 suite; the
        # separate 0.1s timeout/cleanup test still checks deadline enforcement.
        timeout_seconds=15,
        checks=checks,
    )

    assert result == 0
    assert json.loads(report_path.read_text(encoding="utf-8"))["overall_status"] == "PASS"


def test_subset_evidence_child_does_not_claim_canonical_identity(tmp_path):
    validate = _load_validate_module()
    report_path = tmp_path / "validation.json"
    check_file = tmp_path / "check_subset_environment.py"
    check_file.write_text(
        "import os\n"
        "assert 'QUANTOS_VALIDATION_RUN_ID' not in os.environ\n"
        "assert 'QUANTOS_VALIDATION_SOURCE_COMMIT' not in os.environ\n"
        "assert 'QUANTOS_VALIDATION_STARTED_AT_MS' not in os.environ\n",
        encoding="utf-8",
    )
    checks = [
        (
            "synthetic-evidence:current",
            [sys.executable, str(check_file)],
            str(ROOT),
        )
    ]

    result = validate.run(
        stages=["synthetic-evidence:current"],
        json_report=str(report_path),
        timeout_seconds=2,
        checks=checks,
    )

    assert result == 0
    assert json.loads(report_path.read_text(encoding="utf-8"))["overall_status"] == "PASS"


def test_pytest_phase13_child_inherits_canonical_identity():
    validate = _load_validate_module()
    validate._VALIDATION_ENV = {
        "QUANTOS_VALIDATION_RUN_ID": "a" * 64,
        "QUANTOS_VALIDATION_SOURCE_COMMIT": "b" * 40,
        "QUANTOS_VALIDATION_STARTED_AT_MS": "1",
    }

    env = validate._child_env(
        [sys.executable, "-m", "pytest", "tests/governance/test_727_final_audit.py"],
        preserve_canonical=True,
    )

    assert env["QUANTOS_VALIDATION_RUN_ID"] == "a" * 64
    assert env["QUANTOS_VALIDATION_SOURCE_COMMIT"] == "b" * 40
    assert env["QUANTOS_VALIDATION_STARTED_AT_MS"] == "1"


def test_run_check_preserves_canonical_identity_only_for_exact_phase13_stages(monkeypatch, tmp_path):
    validate = _load_validate_module()
    calls = []

    def capture(command, *, preserve_canonical=False):
        calls.append(preserve_canonical)
        return dict(os.environ)

    monkeypatch.setattr(validate, "_child_env", capture)
    for stage in (
        "phase13-readiness:refresh",
        "phase13-readiness:current",
        "phase13-readiness:tests",
        "phase13-readiness:custom",
    ):
        validate._run_check(stage, [sys.executable, "-c", "pass"], str(tmp_path), 2)

    assert calls == [True, True, True, False]


def test_default_subset_report_does_not_overwrite_full_validation_report(
    tmp_path, monkeypatch
):
    validate = _load_validate_module()
    full_report = tmp_path / "validation_report.json"
    subset_report = tmp_path / "validation_report_subset.json"
    full_report.write_text('{"overall_status":"PASS"}\n', encoding="utf-8")
    monkeypatch.setattr(validate, "DEFAULT_REPORT", str(full_report))
    monkeypatch.setattr(validate, "DEFAULT_SUBSET_REPORT", str(subset_report))
    checks = [
        (
            "fake-pass",
            [sys.executable, "-c", "pass"],
            str(tmp_path),
        )
    ]

    result = validate.run(
        stages=["fake-pass"],
        json_report=str(full_report),
        timeout_seconds=2,
        checks=checks,
    )

    assert result == 0
    assert json.loads(full_report.read_text(encoding="utf-8")) == {
        "overall_status": "PASS"
    }
    subset = json.loads(subset_report.read_text(encoding="utf-8"))
    assert subset["selection_scope"] == "SUBSET"
    assert subset["overall_status"] == "PASS"


def test_stage_timeout_is_reported_and_fails_closed(tmp_path):
    validate = _load_validate_module()
    report_path = tmp_path / "timeout.json"
    checks = [
        (
            "fake-timeout",
            [sys.executable, "-c", "import time; time.sleep(1)"],
            str(tmp_path),
        )
    ]

    result = validate.run(
        stages=["fake-timeout"],
        json_report=str(report_path),
        timeout_seconds=0.1,
        checks=checks,
    )

    assert result == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["overall_status"] == "FAIL"
    assert report["summary"]["timed_out"] == 1
    timeout_result = report["results"][0]
    assert timeout_result["status"] == "TIMEOUT"
    assert timeout_result["stuck_process"]["stage"] == "fake-timeout"
    assert timeout_result["stuck_process"]["pid"] > 0


def test_full_validation_refreshes_phase13_after_publishing_primary_report(tmp_path, clean_validation_repo):
    validate = _load_validate_module(clean_validation_repo)
    report_path = tmp_path / "validation.json"
    marker = tmp_path / "phase13-marker.txt"
    primary = (
        "primary-pass",
        [sys.executable, "-c", "pass"],
        str(tmp_path),
    )
    refresh = (
        "phase13-readiness:refresh",
        [
            sys.executable,
            "-c",
            (
                "import json,pathlib,sys;"
                "report=json.loads(pathlib.Path(sys.argv[1]).read_text());"
                "assert report['overall_status']=='PASS';"
                "pathlib.Path(sys.argv[2]).write_text('refreshed')"
            ),
            str(report_path),
            str(marker),
        ],
        str(tmp_path),
    )

    result = validate.run(
        stages=None,
        json_report=str(report_path),
        timeout_seconds=2,
        checks=[primary],
        phase13_refresh_checks=[
            refresh,
            ("phase13-readiness:current", [sys.executable, "-c", "pass"], str(tmp_path)),
            ("phase13-readiness:tests", [sys.executable, "-c", "pass"], str(tmp_path)),
        ],
    )

    assert result == 0
    assert marker.read_text(encoding="utf-8") == "refreshed"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["overall_status"] == "PASS"
    assert report["selection_scope"] == "ALL"
    assert report["selected_stages"] == [
        "primary-pass",
        "phase13-readiness:refresh",
        "phase13-readiness:current",
        "phase13-readiness:tests",
    ]
    assert report["summary"] == {
        "selected": 4,
        "passed": 4,
        "failed": 0,
        "timed_out": 0,
        "errored": 0,
        # 2026-09-20 审计 P0-3:见上,汇总新增 skip 计数。
        "skipped": 0,
    }


def test_canonical_full_validation_publishes_release_evidence_last(
    tmp_path,
    monkeypatch,
):
    validate = _load_validate_module()
    report_path = tmp_path / "validation_report.json"
    observed = []
    monkeypatch.setattr(validate, "DEFAULT_REPORT", str(report_path))
    monkeypatch.setattr(
        validate,
        "resolve_canonical_source_anchor",
        lambda _repo: "a" * 40,
    )

    def publish_release_evidence():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        observed.append((report["overall_status"], report["selected_stages"][-1]))

    monkeypatch.setattr(
        validate,
        "_publish_release_evidence_manifest",
        publish_release_evidence,
    )

    result = validate.run(
        stages=None,
        json_report=str(report_path),
        timeout_seconds=2,
        checks=[("primary-pass", [sys.executable, "-c", "pass"], str(tmp_path))],
        phase13_refresh_checks=_fake_phase13(tmp_path),
    )

    assert result == 0
    assert observed == [("PASS", "phase13-readiness:tests")]


def test_canonical_release_evidence_publication_failure_fails_closed(
    tmp_path,
    monkeypatch,
):
    validate = _load_validate_module()
    report_path = tmp_path / "validation_report.json"
    monkeypatch.setattr(validate, "DEFAULT_REPORT", str(report_path))
    monkeypatch.setattr(
        validate,
        "resolve_canonical_source_anchor",
        lambda _repo: "a" * 40,
    )

    def reject_publication():
        raise ValueError("release evidence drift")

    monkeypatch.setattr(
        validate,
        "_publish_release_evidence_manifest",
        reject_publication,
    )

    result = validate.run(
        stages=None,
        json_report=str(report_path),
        timeout_seconds=2,
        checks=[("primary-pass", [sys.executable, "-c", "pass"], str(tmp_path))],
        phase13_refresh_checks=_fake_phase13(tmp_path),
    )

    assert result == 1
    persisted = json.loads(report_path.read_text())
    assert persisted['overall_status'] == 'FAIL'
    assert persisted['validation']['current'] is False
    assert persisted['release_evidence_publication']['status'] == 'FAIL'
    assert validate._completed_canonical_report_matches(persisted, persisted) is False


def test_full_validation_converges_derived_evidence_in_dependency_order(tmp_path, clean_validation_repo):
    validate = _load_validate_module(clean_validation_repo)
    report_path = tmp_path / "validation.json"
    supply = tmp_path / "supply.txt"
    top_tier = tmp_path / "top-tier.txt"
    checks = [
        (
            "top-tier-assessment-artifacts",
            [
                sys.executable,
                "-c",
                "import pathlib,sys;assert pathlib.Path(sys.argv[1]).read_text()=='top'",
                str(top_tier),
            ],
            str(tmp_path),
        ),
        (
            "primary-pass",
            [sys.executable, "-c", "pass"],
            str(tmp_path),
        ),
        (
            "supply-chain-baseline:current",
            [
                sys.executable,
                "-c",
                "import pathlib,sys;assert pathlib.Path(sys.argv[1]).read_text()=='supply'",
                str(supply),
            ],
            str(tmp_path),
        ),
    ]
    derived = [
        (
            "supply-chain-baseline:current",
            (
                "supply-chain-baseline:refresh",
                [
                    sys.executable,
                    "-c",
                    "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('supply')",
                    str(supply),
                ],
                str(tmp_path),
            ),
        ),
        (
            "top-tier-assessment-artifacts",
            (
                "top-tier-assessment:refresh",
                [
                    sys.executable,
                    "-c",
                    (
                        "import pathlib,sys;"
                        "assert pathlib.Path(sys.argv[1]).read_text()=='supply';"
                        "pathlib.Path(sys.argv[2]).write_text('top')"
                    ),
                    str(supply),
                    str(top_tier),
                ],
                str(tmp_path),
            ),
        ),
    ]

    result = validate.run(
        stages=None,
        json_report=str(report_path),
        timeout_seconds=2,
        checks=checks,
        derived_refresh_checks=derived,
        phase13_refresh_checks=_fake_phase13(tmp_path),
    )

    assert result == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["selected_stages"] == [
        "primary-pass",
        "supply-chain-baseline:refresh",
        "supply-chain-baseline:current",
        "top-tier-assessment:refresh",
        "top-tier-assessment-artifacts",
        "phase13-readiness:refresh",
        "phase13-readiness:current",
        "phase13-readiness:tests",
    ]
    assert report["summary"]["passed"] == 8


def test_evidence_consumers_run_after_derived_refresh(tmp_path, clean_validation_repo):
    validate = _load_validate_module(clean_validation_repo)
    report_path = tmp_path / "validation.json"
    supply = tmp_path / "supply.txt"
    checks = [
        ("prerequisite-pass", [sys.executable, "-c", "pass"], str(tmp_path)),
        (
            "pytest: synthetic evidence consumer",
            [
                sys.executable,
                "-c",
                "import pathlib,sys;assert pathlib.Path(sys.argv[1]).read_text()=='fresh'",
                str(supply),
            ],
            str(tmp_path),
        ),
        (
            "supply-chain-baseline:current",
            [
                sys.executable,
                "-c",
                "import pathlib,sys;assert pathlib.Path(sys.argv[1]).read_text()=='fresh'",
                str(supply),
            ],
            str(tmp_path),
        ),
    ]
    derived = [
        (
            "supply-chain-baseline:current",
            (
                "supply-chain-baseline:refresh",
                [
                    sys.executable,
                    "-c",
                    "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('fresh')",
                    str(supply),
                ],
                str(tmp_path),
            ),
        )
    ]

    result = validate.run(
        stages=None,
        json_report=str(report_path),
        timeout_seconds=2,
        checks=checks,
        derived_refresh_checks=derived,
        phase13_refresh_checks=_fake_phase13(tmp_path),
    )

    assert result == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["selected_stages"] == [
        "prerequisite-pass",
        "supply-chain-baseline:refresh",
        "supply-chain-baseline:current",
        "pytest: synthetic evidence consumer",
        "phase13-readiness:refresh",
        "phase13-readiness:current",
        "phase13-readiness:tests",
    ]


def test_failed_primary_check_never_rewrites_derived_evidence(tmp_path, clean_validation_repo):
    validate = _load_validate_module(clean_validation_repo)
    report_path = tmp_path / "validation.json"
    marker = tmp_path / "must-not-exist.txt"
    checks = [
        (
            "primary-fail",
            [sys.executable, "-c", "raise SystemExit(1)"],
            str(tmp_path),
        ),
        (
            "supply-chain-baseline:current",
            [sys.executable, "-c", "pass"],
            str(tmp_path),
        ),
    ]
    derived = [
        (
            "supply-chain-baseline:current",
            (
                "supply-chain-baseline:refresh",
                [
                    sys.executable,
                    "-c",
                    "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('bad')",
                    str(marker),
                ],
                str(tmp_path),
            ),
        )
    ]

    result = validate.run(
        stages=None,
        json_report=str(report_path),
        timeout_seconds=2,
        checks=checks,
        derived_refresh_checks=derived,
        phase13_refresh_checks=_fake_phase13(tmp_path),
    )

    assert result == 1
    assert not marker.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["selected_stages"] == [
        "primary-fail",
        "supply-chain-baseline:current",
        "phase13-readiness:refresh",
        "phase13-readiness:current",
        "phase13-readiness:tests",
    ]


def test_failed_derived_refresh_blocks_downstream_rewrite(tmp_path, clean_validation_repo):
    validate = _load_validate_module(clean_validation_repo)
    report_path = tmp_path / "validation.json"
    downstream_marker = tmp_path / "must-not-exist.txt"
    checks = [
        ("primary-pass", [sys.executable, "-c", "pass"], str(tmp_path)),
        (
            "supply-chain-baseline:current",
            [sys.executable, "-c", "raise SystemExit(1)"],
            str(tmp_path),
        ),
        (
            "top-tier-assessment-artifacts",
            [sys.executable, "-c", "pass"],
            str(tmp_path),
        ),
    ]
    derived = [
        (
            "supply-chain-baseline:current",
            (
                "supply-chain-baseline:refresh",
                [sys.executable, "-c", "raise SystemExit(1)"],
                str(tmp_path),
            ),
        ),
        (
            "top-tier-assessment-artifacts",
            (
                "top-tier-assessment:refresh",
                [
                    sys.executable,
                    "-c",
                    "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('bad')",
                    str(downstream_marker),
                ],
                str(tmp_path),
            ),
        ),
    ]

    result = validate.run(
        stages=None,
        json_report=str(report_path),
        timeout_seconds=2,
        checks=checks,
        derived_refresh_checks=derived,
        phase13_refresh_checks=_fake_phase13(tmp_path),
    )

    assert result == 1
    assert not downstream_marker.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["selected_stages"] == [
        "primary-pass",
        "supply-chain-baseline:refresh",
        "supply-chain-baseline:current",
        "top-tier-assessment-artifacts",
        "phase13-readiness:refresh",
        "phase13-readiness:current",
        "phase13-readiness:tests",
    ]


def test_subset_derived_check_is_read_only(tmp_path):
    validate = _load_validate_module()
    report_path = tmp_path / "validation.json"
    marker = tmp_path / "must-not-exist.txt"
    checks = [
        (
            "supply-chain-baseline:current",
            [sys.executable, "-c", "pass"],
            str(tmp_path),
        )
    ]
    derived = [
        (
            "supply-chain-baseline:current",
            (
                "supply-chain-baseline:refresh",
                [
                    sys.executable,
                    "-c",
                    "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('bad')",
                    str(marker),
                ],
                str(tmp_path),
            ),
        )
    ]

    result = validate.run(
        stages=["supply-chain-baseline:current"],
        json_report=str(report_path),
        timeout_seconds=2,
        checks=checks,
        derived_refresh_checks=derived,
    )

    assert result == 0
    assert not marker.exists()


def test_phase13_tests_move_post_report_without_losing_coverage():
    validate = _load_validate_module()
    main_pytest = next(
        argv
        for name, argv, _cwd in validate.CHECKS
        if name.startswith("pytest: all suites")
    )
    assert "--ignore=tests/governance/test_727_final_audit.py" in main_pytest

    post_report = validate._phase13_refresh_checks()
    assert [name for name, _argv, _cwd in post_report] == [
        "phase13-readiness:refresh",
        "phase13-readiness:current",
        "phase13-readiness:tests",
    ]
    phase13_test_argv = post_report[-1][1]
    assert phase13_test_argv[:4] == [sys.executable, "-m", "pytest", "-q"]
    assert Path(phase13_test_argv[-1]).resolve() == (
        ROOT / "tests/governance/test_727_final_audit.py"
    ).resolve()


def test_governance_alias_selects_only_governance_scanner():
    validate = _load_validate_module()
    checks = [
        ("governance-scanner", [sys.executable, "-c", "pass"], "."),
        ("other", [sys.executable, "-c", "pass"], "."),
    ]

    selected = validate._select_checks(["governance"], checks)

    assert [name for name, _, _ in selected] == ["governance-scanner"]




def test_full_run_id_is_deterministic_and_domain_bound():
    validate = _load_validate_module()
    first = validate._deterministic_validation_run_id("a" * 40, "b" * 64, "c" * 64)
    second = validate._deterministic_validation_run_id("a" * 40, "b" * 64, "c" * 64)
    assert first == second
    assert len(first) == 64
    assert first == first.lower()
    assert first != validate._deterministic_validation_run_id("d" * 40, "b" * 64, "c" * 64)


def test_full_run_uses_stable_anchor_after_artifact_only_head(tmp_path, monkeypatch):
    validate = _load_validate_module()
    report_path = tmp_path / "validation.json"
    anchor = "a" * 40
    monkeypatch.setattr(validate, "resolve_canonical_source_anchor", lambda _repo: anchor)
    monkeypatch.setattr(validate, "_git_commit", lambda: "b" * 40)
    checks = [("fake-pass", [sys.executable, "-c", "pass"], str(tmp_path))]

    assert validate.run(
        stages=None,
        json_report=str(report_path),
        timeout_seconds=2,
        checks=checks,
        derived_refresh_checks=[],
        phase13_refresh_checks=_fake_phase13(tmp_path),
    ) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["commit"] == anchor
    assert report["validation"]["source_commit"] == anchor


def test_repeated_full_run_preserves_timing_only_report_changes(tmp_path, monkeypatch, clean_validation_repo):
    validate = _load_validate_module(clean_validation_repo)
    report_path = tmp_path / "validation.json"
    monkeypatch.setattr(validate.time, "time", lambda: 1000.0)
    checks = [("fake-pass", [sys.executable, "-c", "pass"], str(tmp_path))]
    monkeypatch.setattr(validate, "_phase13_refresh_checks", lambda: _fake_phase13(tmp_path))
    kwargs = dict(
        stages=None,
        json_report=str(report_path),
        timeout_seconds=2,
        checks=checks,
        derived_refresh_checks=[],
    )

    assert validate.run(**kwargs) == 0
    first = report_path.read_bytes()
    first_stat = report_path.stat()
    assert validate.run(**kwargs) == 0
    second_stat = report_path.stat()
    second = report_path.read_bytes()
    assert second == first
    assert second_stat.st_mtime_ns == first_stat.st_mtime_ns


def test_pre_phase13_failure_rewrites_fail_closed_report(tmp_path, clean_validation_repo):
    validate = _load_validate_module(clean_validation_repo)
    report_path = tmp_path / "validation.json"
    passing = [("fake-pass", [sys.executable, "-c", "pass"], str(tmp_path))]
    failing = [("fake-pass", [sys.executable, "-c", "raise SystemExit(1)"], str(tmp_path))]
    common = dict(
        stages=None,
        json_report=str(report_path),
        timeout_seconds=2,
        derived_refresh_checks=[],
        phase13_refresh_checks=_fake_phase13(tmp_path),
    )

    assert validate.run(checks=passing, **common) == 0
    before = report_path.stat()
    assert validate.run(checks=failing, **common) == 1
    after = report_path.stat()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["overall_status"] == "FAIL"
    assert report["validation"]["current"] is False
    assert after.st_mtime_ns != before.st_mtime_ns


def test_phase13_failure_rewrites_fail_closed_report(tmp_path, clean_validation_repo):
    validate = _load_validate_module(clean_validation_repo)
    report_path = tmp_path / "validation.json"
    primary = [("fake-pass", [sys.executable, "-c", "pass"], str(tmp_path))]
    passing_phase13 = _fake_phase13(tmp_path)
    failing_phase13 = _fake_phase13(tmp_path)
    failing_phase13[-1] = (
        "phase13-readiness:tests", [sys.executable, "-c", "raise SystemExit(1)"], str(tmp_path)
    )
    common = dict(
        stages=None,
        json_report=str(report_path),
        timeout_seconds=2,
        checks=primary,
        derived_refresh_checks=[],
    )

    assert validate.run(phase13_refresh_checks=passing_phase13, **common) == 0
    before = report_path.stat()
    assert validate.run(phase13_refresh_checks=failing_phase13, **common) == 1
    after = report_path.stat()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["overall_status"] == "FAIL"
    assert report["validation"]["current"] is False
    assert after.st_mtime_ns != before.st_mtime_ns


def test_nonvolatile_report_change_rewrites_report(tmp_path, clean_validation_repo):
    validate = _load_validate_module(clean_validation_repo)
    report_path = tmp_path / "validation.json"
    passing = [("fake-pass", [sys.executable, "-c", "pass"], str(tmp_path))]
    failing = [("fake-pass", [sys.executable, "-c", "raise SystemExit(1)"], str(tmp_path))]
    common = dict(
        stages=None,
        json_report=str(report_path),
        timeout_seconds=2,
        derived_refresh_checks=[],
        phase13_refresh_checks=_fake_phase13(tmp_path),
    )

    assert validate.run(checks=passing, **common) == 0
    first = report_path.read_bytes()
    assert validate.run(checks=failing, **common) == 1
    second = report_path.read_bytes()
    assert second != first
    assert json.loads(second)["results"][0]["status"] == "FAIL"




def test_repeated_full_run_reuses_started_at_for_phase13_children(tmp_path, monkeypatch, clean_validation_repo):
    validate = _load_validate_module(clean_validation_repo)
    report_path = tmp_path / "validation.json"
    marker = tmp_path / "started.txt"
    monkeypatch.setattr(validate.time, "time", lambda: 1000.0)
    capture = (
        "import os,pathlib;"
        "pathlib.Path(r'%s').open('a').write(os.environ['QUANTOS_VALIDATION_STARTED_AT_MS']+'\\n')"
    ) % marker
    checks = [("primary-pass", [sys.executable, "-c", "pass"], str(tmp_path))]
    phase13 = [
        (name, [sys.executable, "-c", capture], str(tmp_path))
        for name in (
            "phase13-readiness:refresh",
            "phase13-readiness:current",
            "phase13-readiness:tests",
        )
    ]
    kwargs = dict(
        stages=None,
        json_report=str(report_path),
        timeout_seconds=2,
        checks=checks,
        derived_refresh_checks=[],
    )
    monkeypatch.setattr(validate, "_phase13_refresh_checks", lambda: phase13)

    assert validate.run(**kwargs) == 0
    first_started = json.loads(report_path.read_text(encoding="utf-8"))["started_at_ms"]
    assert validate.run(**kwargs) == 0
    second_started = json.loads(report_path.read_text(encoding="utf-8"))["started_at_ms"]

    assert second_started == first_started
    assert marker.read_text(encoding="utf-8").splitlines()[-3:] == [
        str(first_started), str(first_started), str(first_started)
    ]


def test_canonical_started_at_reuse_rejects_identity_or_failed_report(tmp_path):
    validate = _load_validate_module()
    report_path = tmp_path / "validation.json"
    report = {
        "report_kind": "FAIL_CLOSED_VALIDATION",
        "selection_scope": "ALL",
        "overall_status": "PASS",
        "selected_stages": [
            "phase13-readiness:refresh",
            "phase13-readiness:current",
            "phase13-readiness:tests",
        ],
        "results": [
            {"stage": stage, "status": "PASS"}
            for stage in (
                "phase13-readiness:refresh",
                "phase13-readiness:current",
                "phase13-readiness:tests",
            )
        ],
        "commit": "a",
        "checks_manifest_sha256": "b",
        "derived_refresh_manifest_sha256": "c",
        "phase13_manifest_sha256": "p",
        "validate_script_sha256": "d",
        "started_at_ms": 123,
        # 2026-09-20 审计 P0-3:_summary 新增 skip 计数后,canonical 报告的 summary
        # 必须含该键才能被 _canonical_started_at_ms 认作可复用(旧 schema 的报告
        # 不再匹配 —— 这是有意的:身份变了就不该复用旧的 run identity)。
        "summary": {"selected": 3, "passed": 3, "failed": 0, "timed_out": 0, "errored": 0, "skipped": 0},
        "validation": {"current": True, "run_id": "e", "source_commit": "a", "generated_at": 123},
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    identity = {key: report[key] for key in (
        "commit", "checks_manifest_sha256", "derived_refresh_manifest_sha256",
        "validate_script_sha256",
    )}
    identity["phase13_manifest_sha256"] = "p"
    identity["validation"] = {"run_id": "e"}
    assert validate._canonical_started_at_ms(
        str(report_path), identity, fresh_started_at_ms=123
    ) == 123

    identity["commit"] = "different"
    assert validate._canonical_started_at_ms(
        str(report_path), identity, fresh_started_at_ms=123
    ) is None
    report["overall_status"] = "FAIL"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    identity["commit"] = "a"
    assert validate._canonical_started_at_ms(
        str(report_path), identity, fresh_started_at_ms=123
    ) is None


def test_canonical_report_rejects_malformed_or_inconsistent_results(tmp_path):
    validate = _load_validate_module()
    path = tmp_path / "validation.json"
    stages = [
        "phase13-readiness:refresh",
        "phase13-readiness:current",
        "phase13-readiness:tests",
    ]
    base = {
        "report_kind": "FAIL_CLOSED_VALIDATION", "selection_scope": "ALL",
        "overall_status": "PASS", "selected_stages": stages,
        "results": [{"stage": stage, "status": "PASS"} for stage in stages],
        "commit": "a", "checks_manifest_sha256": "b",
        "derived_refresh_manifest_sha256": "c", "phase13_manifest_sha256": "p", "validate_script_sha256": "d",
        "started_at_ms": 123, "validation": {"current": True, "run_id": "e"},
    }
    identity = {key: base[key] for key in (
        "commit", "checks_manifest_sha256", "derived_refresh_manifest_sha256",
        "validate_script_sha256",
    )}
    identity["phase13_manifest_sha256"] = "p"
    identity["validation"] = {"run_id": "e"}
    for mutate in (
        lambda report: report.update(selected_stages=[]),
        lambda report: report.update(selected_stages={}),
        lambda report: report.update(results=[None]),
        lambda report: report.update(results=report["results"] + [report["results"][0]]),
        lambda report: report.update(selected_stages=stages + ["garbage"]),
        lambda report: report["results"].__setitem__(0, {"stage": stages[0], "status": "FAIL"}),
        lambda report: report["validation"].update(generated_at=True),
        lambda report: report["validation"].update(source_commit="wrong"),
    ):
        candidate = json.loads(json.dumps(base))
        mutate(candidate)
        path.write_text(json.dumps(candidate), encoding="utf-8")
        assert validate._completed_canonical_report_matches(str(path), identity) is False

    future = json.loads(json.dumps(base))
    future["started_at_ms"] = 124
    path.write_text(json.dumps(future), encoding="utf-8")
    assert validate._canonical_started_at_ms(
        str(path), identity, fresh_started_at_ms=123
    ) is None


def test_future_canonical_timing_is_not_reused_by_default_dag(tmp_path, monkeypatch, clean_validation_repo):
    validate = _load_validate_module(clean_validation_repo)
    report_path = tmp_path / "validation.json"
    marker = tmp_path / "started.txt"
    monkeypatch.setattr(validate.time, "time", lambda: 1000.0)
    capture = (
        "import os,pathlib;"
        "pathlib.Path(r'%s').open('a').write(os.environ['QUANTOS_VALIDATION_STARTED_AT_MS']+'\\n')"
    ) % marker
    checks = [("primary-pass", [sys.executable, "-c", "pass"], str(tmp_path))]
    phase13 = [
        (name, [sys.executable, "-c", capture], str(tmp_path))
        for name in (
            "phase13-readiness:refresh",
            "phase13-readiness:current",
            "phase13-readiness:tests",
        )
    ]
    monkeypatch.setattr(validate, "_phase13_refresh_checks", lambda: phase13)
    kwargs = dict(
        stages=None, json_report=str(report_path), timeout_seconds=2,
        checks=checks, derived_refresh_checks=[],
    )
    assert validate.run(**kwargs) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["started_at_ms"] = 9999999
    report_path.write_text(json.dumps(report), encoding="utf-8")
    assert validate.run(**kwargs) == 0
    final = json.loads(report_path.read_text(encoding="utf-8"))
    assert final["started_at_ms"] == 1000000
    assert marker.read_text(encoding="utf-8").splitlines()[-3:] == [
        "1000000", "1000000", "1000000"
    ]


def test_all_validation_rejects_empty_phase13_override(tmp_path):
    validate = _load_validate_module()
    with pytest.raises(ValueError, match="Phase 13"):
        validate.run(
            stages=None,
            json_report=str(tmp_path / "validation.json"),
            timeout_seconds=2,
            checks=[("fake-pass", [sys.executable, "-c", "pass"], str(tmp_path))],
            phase13_refresh_checks=[],
            derived_refresh_checks=[],
        )


def test_frontend_tests_are_resource_bounded_for_the_canonical_gate():
    validate = _load_validate_module()
    argv = next(
        argv
        for name, argv, _cwd in validate.CHECKS
        if name == "frontend: tests"
    )
    assert argv == [
        validate.NPM,
        "test",
        "--",
        "--maxWorkers=1",
        "--no-file-parallelism",
    ]
