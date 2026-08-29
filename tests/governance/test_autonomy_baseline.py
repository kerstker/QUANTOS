"""Contract tests for the evidence-first Phase 0/1 baseline."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def _load_baseline_module():
    path = ROOT / "scripts" / "generate_autonomy_baseline.py"
    spec = importlib.util.spec_from_file_location("quantos_autonomy_baseline", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_baseline_records_safe_flags_and_current_admission():
    baseline_module = _load_baseline_module()
    baseline = baseline_module.build_baseline(root=ROOT, now_ms=1_800_000_000_000)

    assert baseline["readiness"] == "L2_DRY_RUN_ONLY"
    assert baseline["safety_verdict"] == "PASS"
    assert baseline["safety_flags"] == {
        "LIVE_TRADING_ENABLED": False,
        "REAL_BROKER_ENABLED": False,
        "ALLOW_ORDER_SUBMISSION": False,
        "ALLOW_AI_DIRECT_TRADING": False,
        "REQUIRE_HUMAN_APPROVAL": True,
    }
    assert baseline["order_path"]["live_allowed"] is False
    assert baseline["order_path"]["order_submission_allowed"] is False
    assert baseline["current_evidence"]["data_admission"]["verdict"] in {
        "PASS",
        "BLOCK",
        "MISSING",
    }
    assert len(baseline["input_hash"]) == 64
    assert baseline["phase_status"]["phase_1"]["status"] in {
        "PASS",
        "BLOCKED",
    }


def test_baseline_outputs_are_written_atomically(tmp_path):
    baseline_module = _load_baseline_module()
    output = tmp_path / "autonomy_baseline.json"
    gap_report = tmp_path / "autonomy_gap_analysis.md"

    baseline = baseline_module.write_outputs(
        output_path=output,
        gap_report_path=gap_report,
        now_ms=1_800_000_000_000,
    )

    assert output.is_file()
    assert gap_report.is_file()
    assert output.read_text(encoding="utf-8").find('"report_kind": "AUTONOMY_BASELINE"') >= 0
    assert "下一项唯一最高优先级" in gap_report.read_text(encoding="utf-8")
    assert baseline["commit"]


def test_baseline_cli_writes_requested_outputs(tmp_path):
    output = tmp_path / "cli-baseline.json"
    gap_report = tmp_path / "cli-gap.md"
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate_autonomy_baseline.py"),
            "--output",
            str(output),
            "--gap-report",
            str(gap_report),
            "--now-ms",
            "1800000000000",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert output.is_file()
    assert gap_report.is_file()


def test_legacy_profitability_retention_cannot_satisfy_baseline(tmp_path):
    baseline_module = _load_baseline_module()
    for relative_path in baseline_module.REQUIRED_COMPLETION_INPUTS:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {}
        if relative_path == "evidence/data_quality_admission.json":
            payload = {
                "verdict": "PASS",
                "quality_verdict": "PASS",
                "lineage_verdict": "PASS",
            }
        elif relative_path == "evidence/raw_data_lineage_backfill.json":
            payload = {"verdict": "PASS"}
        if path.suffix == ".json":
            path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        else:
            path.write_text("current source/document\n", encoding="utf-8")

    validation = tmp_path / "evidence/validation_report.json"
    validation.write_text(
        json.dumps({"selection_scope": "ALL", "overall_status": "PASS"}) + "\n",
        encoding="utf-8",
    )
    for relative_path in baseline_module.LEGACY_PROFITABILITY_INPUTS:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": "READY",
            "paper_ready": True,
            "summary": {"sharpe": 99.0, "max_drawdown": 0.0},
        }
        if relative_path == "data/cache/paper_health_10k.json":
            payload["authoritative_backtest_record"] = {"self_asserted": True}
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    baseline = baseline_module.build_baseline(
        root=tmp_path, now_ms=1_800_000_000_000
    )

    authority = baseline["current_evidence"]["backtest_authority"]
    assert authority["record_present"] is True
    assert authority["independently_admitted"] is False
    assert authority["status"] == "INVALIDATED_PERP_LEDGER_V1"
    assert baseline["phase_status"]["phase_0"]["status"] == "PARTIAL"
    assert "INVALIDATED_PERP_LEDGER_V1" in baseline["phase_status"]["phase_0"]["reasons"]
    paper = baseline["current_evidence"]["paper_health_10k"]
    assert paper["readiness"] == "BLOCKED"
    assert paper["paper_ready"] is False
    for relative_path in baseline_module.LEGACY_PROFITABILITY_INPUTS:
        artifact = baseline["inputs"][relative_path]
        assert artifact["exists"] is True
        assert len(artifact["sha256"]) == 64
        assert artifact["retention_status"] == "INVALIDATED_PERP_LEDGER_V1"
        assert artifact["counted_as_completion_evidence"] is False
        assert "summary" not in artifact
        assert "paper_ready" not in artifact
    serialized = json.dumps(baseline).lower()
    assert '"sharpe"' not in serialized
    assert '"max_drawdown"' not in serialized
