"""2026-09-20 审计 P0-3:canonical 门禁的漏测与假绿色回归。

背景:``_run_check`` 此前只看子进程退出码。pytest 退出码 0 同时覆盖
"全部执行并通过" 与 "整个文件被 skip 掉" 两种情况,因此缺失 artifact、
缺失 adapter、``pytest.skip`` 或计数不可解析都能伪装成阶段 PASS——
这正是审计说的"不能只看退出码"。

本文件锁定三条新契约:
1. pytest 阶段执行时附加 ``--junitxml`` 报告并回读真实计数(计数进机器报告);
2. 声明为 load-bearing 的测试文件被 skip、或计数不可解析、或零执行,一律 FAIL;
3. 汇总至少暴露 skip 数量,使假绿色在证据里可见。

诚实 skip 不受影响:仓库没有 ``data/cache`` artifact 时
``tests/reproducibility`` 的 skip 是文档化的设计行为
(见 tests/reproducibility/test_evidence_snapshots.py 的 docstring),
因此策略按**文件**而非按阶段判定,不会把该阶段打成红色。
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load_validate_module():
    path = ROOT / "scripts" / "validate.py"
    spec = importlib.util.spec_from_file_location(
        "quantos_validate_runner_false_green", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_phase13(tmp_path):
    return [
        (name, [sys.executable, "-c", "pass"], str(tmp_path))
        for name in (
            "phase13-readiness:refresh",
            "phase13-readiness:current",
            "phase13-readiness:tests",
        )
    ]


def _write_skipping_test(directory: Path) -> Path:
    """一个整体 skip 的 pytest 文件——模拟缺失 artifact / 缺失 adapter。"""
    target = directory / "test_all_skipped.py"
    target.write_text(
        "import pytest\n\n\n"
        "def test_needs_missing_artifact():\n"
        "    pytest.skip('artifact absent')\n",
        encoding="utf-8",
    )
    return target


# --------------------------------------------------------------------------
# 1. 计数解析
# --------------------------------------------------------------------------


def test_junit_counts_are_read_back(tmp_path):
    validate = _load_validate_module()
    source = _write_skipping_test(tmp_path)
    report_path = tmp_path / "junit.xml"

    import subprocess

    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", f"--junitxml={report_path}", str(source)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )

    assert report_path.is_file(), "pytest did not emit a junit report"
    counts = validate._read_pytest_counts(str(report_path))

    assert counts is not None, "junit report was not parsed"
    assert counts["tests"] == 1
    assert counts["skipped"] == 1


def test_unparseable_junit_report_is_not_silently_unknown(tmp_path):
    validate = _load_validate_module()
    broken = tmp_path / "broken.xml"
    broken.write_text("<not-valid-xml", encoding="utf-8")

    assert validate._read_pytest_counts(str(broken)) is None
    assert validate._read_pytest_counts(str(tmp_path / "absent.xml")) is None


# --------------------------------------------------------------------------
# 2. load-bearing skip 策略
# --------------------------------------------------------------------------


def test_skipped_load_bearing_test_is_a_failure(tmp_path, monkeypatch):
    validate = _load_validate_module()
    source = _write_skipping_test(tmp_path)
    monkeypatch.setattr(validate, "NO_SKIP_TEST_PATHS", (str(source),))

    counts = {"tests": 1, "skipped": 1, "failed": 0, "errors": 0,
              "skipped_files": [str(source)]}
    detail = validate._load_bearing_verdict([sys.executable, "-m", "pytest", str(source)], counts)

    assert detail is not None, "a skipped load-bearing file must not pass"
    assert "skip" in detail.lower()


def test_unreadable_counts_for_load_bearing_stage_is_a_failure(tmp_path, monkeypatch):
    validate = _load_validate_module()
    source = tmp_path / "test_needed.py"
    monkeypatch.setattr(validate, "NO_SKIP_TEST_PATHS", (str(source),))

    detail = validate._load_bearing_verdict(
        [sys.executable, "-m", "pytest", str(source)], None
    )

    assert detail is not None, "unparseable counts must not silently pass"


def test_zero_executed_load_bearing_stage_is_a_failure(tmp_path, monkeypatch):
    validate = _load_validate_module()
    source = tmp_path / "test_needed.py"
    monkeypatch.setattr(validate, "NO_SKIP_TEST_PATHS", (str(source),))

    counts = {"tests": 0, "skipped": 0, "failed": 0, "errors": 0, "skipped_files": []}
    detail = validate._load_bearing_verdict(
        [sys.executable, "-m", "pytest", str(source)], counts
    )

    assert detail is not None, "a load-bearing stage that ran nothing must not pass"


def test_unrelated_skips_do_not_fail_a_stage(tmp_path, monkeypatch):
    """诚实 skip(reproducibility 无 artifact)不得把阶段打成红色。"""
    validate = _load_validate_module()
    load_bearing = tmp_path / "test_needed.py"
    honest = tmp_path / "test_reproducibility_snapshots.py"
    monkeypatch.setattr(validate, "NO_SKIP_TEST_PATHS", (str(load_bearing),))

    counts = {"tests": 2, "skipped": 1, "failed": 0, "errors": 0,
              "skipped_files": [str(honest)]}
    detail = validate._load_bearing_verdict(
        [sys.executable, "-m", "pytest", str(load_bearing), str(honest)], counts
    )

    assert detail is None, "an unrelated honest skip must not fail the stage"


def test_non_pytest_stage_is_never_skip_judged(tmp_path, monkeypatch):
    validate = _load_validate_module()
    monkeypatch.setattr(validate, "NO_SKIP_TEST_PATHS", (str(tmp_path / "t.py"),))

    assert validate._load_bearing_verdict(
        [sys.executable, "-c", "pass"], None
    ) is None


# --------------------------------------------------------------------------
# 3. 端到端:整体 skip 的 pytest 阶段不得报 PASS
# --------------------------------------------------------------------------


def test_wholly_skipped_pytest_stage_does_not_pass(tmp_path, monkeypatch):
    validate = _load_validate_module()
    source = _write_skipping_test(tmp_path)
    monkeypatch.setattr(validate, "NO_SKIP_TEST_PATHS", (str(source),))

    report_path = tmp_path / "validation.json"
    checks = [
        ("pytest: synthetic load-bearing", [sys.executable, "-m", "pytest", "-q", str(source)], str(tmp_path)),
    ]

    result = validate.run(
        stages=["pytest: synthetic load-bearing"],
        json_report=str(report_path),
        timeout_seconds=60,
        checks=checks,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    stage = report["results"][0]

    assert stage["status"] == "FAIL", (
        "a pytest stage whose load-bearing tests were all skipped reported "
        f"{stage['status']} — this is the false-green P0-3 describes"
    )
    assert result != 0
    assert stage.get("pytest_counts", {}).get("skipped") == 1


def test_skipped_required_file_cannot_hide_behind_unrelated_pass(tmp_path, monkeypatch):
    validate = _load_validate_module()
    source = _write_skipping_test(tmp_path)
    passing = tmp_path / "test_unrelated.py"
    passing.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    monkeypatch.setattr(validate, "NO_SKIP_TEST_PATHS", (str(source),))
    result = validate._run_check(
        "pytest: mixed skip regression",
        [sys.executable, "-m", "pytest", "-q", str(source), str(passing)],
        str(tmp_path), 60,
    )
    assert result["status"] == "FAIL", result
    assert result["pytest_counts"]["skipped"] == 1


def test_load_bearing_paths_that_are_actually_wired_into_the_gate():
    """P0-3 的另一半:光有策略不够,那些文件必须真的在 CHECKS 里。

    审计发现 tests/integration/test_hourly_alpha_to_paper.py 与
    tests/integration/test_mft_testnet_execution.py 存在且全绿,但从未进入
    canonical 门禁——只在手动调用时才会跑到。这个断言把"已接线"钉死。
    """
    validate = _load_validate_module()
    declared = [
        str(item)
        for _, argv, _ in validate.CHECKS
        for item in argv
    ]

    for path in (
        "tests/integration/test_hourly_alpha_to_paper.py",
        "tests/integration/test_mft_testnet_execution.py",
    ):
        assert path in declared, f"{path} is not wired into any canonical stage"
        assert path in validate.NO_SKIP_TEST_PATHS, (
            f"{path} is wired in but not protected against wholesale skipping"
        )


def test_import_graph_contracts_are_enforced_by_a_stage():
    """P1-9:.importlinter 有契约但没有 runner 等于没有门禁。"""
    validate = _load_validate_module()
    stages = {name: argv for name, argv, _ in validate.CHECKS}

    assert "import-graph-contracts" in stages, (
        ".importlinter contracts have no runner in the canonical gate"
    )
    command = " ".join(str(item) for item in stages["import-graph-contracts"])
    # `python -m importlinter.cli` 会静默地以 0 退出(没有 __main__ 守卫),
    # 那种调用方式必须被拒绝——它正是本审计要消灭的假绿形态。
    assert "-m importlinter.cli" not in command, (
        "the silently-no-op `-m importlinter.cli` form must not be used"
    )
    assert "lint_imports_command" in command


def test_summary_exposes_skip_counts(tmp_path, monkeypatch):
    validate = _load_validate_module()
    source = _write_skipping_test(tmp_path)
    # 非 load-bearing:阶段仍应 PASS,但 skip 数量必须可见。
    monkeypatch.setattr(validate, "NO_SKIP_TEST_PATHS", ())

    report_path = tmp_path / "validation.json"
    checks = [
        ("pytest: synthetic honest skip", [sys.executable, "-m", "pytest", "-q", str(source)], str(tmp_path)),
    ]

    result = validate.run(
        stages=["pytest: synthetic honest skip"],
        json_report=str(report_path),
        timeout_seconds=60,
        checks=checks,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert result == 0
    assert report["results"][0]["status"] == "PASS"
    assert report["summary"]["skipped"] == 1, (
        "skip counts must be visible in the machine report so a documented "
        "honest skip is distinguishable from a false green"
    )
