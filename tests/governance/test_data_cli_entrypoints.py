"""Keep documented data-admission CLIs executable from the repository root."""

import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "relative_script",
    [
        "tools/data_quality_admission.py",
        "tools/raw_data_lineage_backfill.py",
        "tools/paper_order_preview.py",
        "tools/three_alpha_paper_flow.py",
        "tools/dry_run_candidate_multi.py",
        "tools/paper_realtime_10k.py",
        "tools/paper_shadow_auto.py",
        "tools/paper_shadow_realtime.py",
        "tools/paper_operational_evidence.py",
        "tools/search_trial_ledger_history.py",
    ],
)
def test_documented_data_cli_help_starts_from_script_path(relative_script):
    proc = subprocess.run(
        [sys.executable, str(ROOT / relative_script), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "usage:" in proc.stdout


@pytest.mark.parametrize(
    "relative_script",
    [
        "tools/paper_alpha_evaluation.py",
        "tools/paper_research_risk_bundle.py",
    ],
)
def test_invalidated_v1_authority_clis_do_not_offer_argument_bypasses(
    relative_script,
):
    proc = subprocess.run(
        [sys.executable, str(ROOT / relative_script), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode != 0
    assert "INVALIDATED_PERP_LEDGER_V1" in proc.stdout + proc.stderr
