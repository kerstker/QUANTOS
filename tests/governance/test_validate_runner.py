"""Regression tests for the observable, fail-closed validation runner."""

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


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


def test_governance_alias_selects_only_governance_scanner():
    validate = _load_validate_module()
    checks = [
        ("governance-scanner", [sys.executable, "-c", "pass"], "."),
        ("other", [sys.executable, "-c", "pass"], "."),
    ]

    selected = validate._select_checks(["governance"], checks)

    assert [name for name, _, _ in selected] == ["governance-scanner"]
