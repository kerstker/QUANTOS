"""Regression tests for the observable, fail-closed validation runner."""

import importlib.util
import json
import os
import subprocess
import sys
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


def _load_validate_module():
    path = ROOT / "scripts" / "validate.py"
    spec = importlib.util.spec_from_file_location("quantos_validate_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    }
    assert report["results"][0]["status"] == "PASS"


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


def test_full_validation_refreshes_phase13_after_publishing_primary_report(tmp_path):
    validate = _load_validate_module()
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
        phase13_refresh_checks=[refresh],
    )

    assert result == 0
    assert marker.read_text(encoding="utf-8") == "refreshed"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["overall_status"] == "PASS"
    assert report["selection_scope"] == "ALL"
    assert report["selected_stages"] == [
        "primary-pass",
        "phase13-readiness:refresh",
    ]
    assert report["summary"] == {
        "selected": 2,
        "passed": 2,
        "failed": 0,
        "timed_out": 0,
        "errored": 0,
    }


def test_full_validation_converges_derived_evidence_in_dependency_order(tmp_path):
    validate = _load_validate_module()
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
        phase13_refresh_checks=[],
    )

    assert result == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["selected_stages"] == [
        "primary-pass",
        "supply-chain-baseline:refresh",
        "supply-chain-baseline:current",
        "top-tier-assessment:refresh",
        "top-tier-assessment-artifacts",
    ]
    assert report["summary"]["passed"] == 5


def test_evidence_consumers_run_after_derived_refresh(tmp_path):
    validate = _load_validate_module()
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
        phase13_refresh_checks=[],
    )

    assert result == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["selected_stages"] == [
        "prerequisite-pass",
        "supply-chain-baseline:refresh",
        "supply-chain-baseline:current",
        "pytest: synthetic evidence consumer",
    ]


def test_failed_primary_check_never_rewrites_derived_evidence(tmp_path):
    validate = _load_validate_module()
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
        phase13_refresh_checks=[],
    )

    assert result == 1
    assert not marker.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["selected_stages"] == [
        "primary-fail",
        "supply-chain-baseline:current",
    ]


def test_failed_derived_refresh_blocks_downstream_rewrite(tmp_path):
    validate = _load_validate_module()
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
        phase13_refresh_checks=[],
    )

    assert result == 1
    assert not downstream_marker.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["selected_stages"] == [
        "primary-pass",
        "supply-chain-baseline:refresh",
        "supply-chain-baseline:current",
        "top-tier-assessment-artifacts",
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
