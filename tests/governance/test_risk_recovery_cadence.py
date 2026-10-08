import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_canonical_gate_runs_an_explicit_fresh_risk_recovery_drill():
    source = (ROOT / "scripts" / "validate.py").read_text(encoding="utf-8")
    assert "risk-recovery-drill:fresh" in source
    assert "run_risk_recovery_gate.py" in source


def test_ci_schedules_the_canonical_gate_recurringly():
    workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text(
        encoding="utf-8"
    )
    assert "schedule:" in workflow
    assert "cron:" in workflow


def test_fresh_risk_recovery_gate_runs_in_an_ephemeral_directory():
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_risk_recovery_gate.py"),
            "--rto-ms",
            "30000",
            "--observed-at-ms",
            "1783734400000",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["verdict"] == "PASS"
    assert result["active_state_untouched"] is True
    assert result["rto_met"] is True
