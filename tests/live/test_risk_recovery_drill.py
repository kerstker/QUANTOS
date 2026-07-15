import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_risk_recovery_drill_reopens_critical_state_and_fails_closed(tmp_path):
    from tools.risk_recovery_drill import run_risk_recovery_drill

    result = run_risk_recovery_drill(
        drill_dir=str(tmp_path / "drill"),
        rto_ms=30_000,
        observed_at_ms=1_783_734_400_000,
    )

    assert result["schema_version"] == 2
    assert result["verdict"] == "PASS"
    assert result["drill_mode"] == "ISOLATED_OFFLINE"
    assert result["artifact_names"] == [
        "audit.db",
        "kill_switch.db",
        "kill_switch.json",
        "ledger.db",
    ]
    assert result["recovered_state_count"] == 4
    assert result["kill_switch_persisted"] is True
    assert result["kill_switch_chain_verified"] is True
    assert result["kill_switch_event_recoverable"] is True
    assert result["kill_switch_outbox_pending_count"] == 1
    assert result["gate_rejected_while_tripped"] is True
    assert result["gate_rejection_code"] == "KILL_SWITCH_ACTIVE"
    assert result["ledger_recovered"] is True
    assert result["audit_chain_verified"] is True
    assert result["corrupt_kill_switch_failed_closed"] is True
    assert result["tampered_kill_switch_detected"] is True
    assert result["tampered_audit_detected"] is True
    assert result["active_state_untouched"] is True
    assert result["pre_restart_sha256"] == result["post_restart_sha256"]
    assert result["ledger_before"] == result["ledger_after"]
    assert result["rto_met"] is True
    json.dumps(result)


def test_prepare_drill_dir_rejects_active_state_path(tmp_path):
    from tools.risk_recovery_drill import RiskRecoveryDrillError, _prepare_drill_dir

    active = tmp_path / "active"
    active.mkdir()
    with pytest.raises(RiskRecoveryDrillError, match="active"):
        _prepare_drill_dir(str(active / "drill"), forbidden_roots=(active,))


def test_prepare_drill_dir_rejects_parent_of_active_state_path(tmp_path):
    from tools.risk_recovery_drill import RiskRecoveryDrillError, _prepare_drill_dir

    parent = tmp_path / "candidate"
    active = parent / "active"
    with pytest.raises(RiskRecoveryDrillError, match="active"):
        _prepare_drill_dir(str(parent), forbidden_roots=(active,))


def test_prepare_drill_dir_rejects_nonempty_directory(tmp_path):
    from tools.risk_recovery_drill import RiskRecoveryDrillError, _prepare_drill_dir

    target = tmp_path / "drill"
    target.mkdir()
    (target / "sentinel").write_text("keep", encoding="utf-8")
    with pytest.raises(RiskRecoveryDrillError, match="empty"):
        _prepare_drill_dir(str(target), forbidden_roots=())


def test_prepare_drill_dir_rejects_symlink(tmp_path):
    from tools.risk_recovery_drill import RiskRecoveryDrillError, _prepare_drill_dir

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(RiskRecoveryDrillError, match="symlink"):
        _prepare_drill_dir(str(link), forbidden_roots=())


@pytest.mark.parametrize(
    ("rto_ms", "observed_at_ms", "message"),
    [(0, 1, "rto_ms"), (1, -1, "observed_at_ms")],
)
def test_risk_recovery_drill_rejects_invalid_timing(
    tmp_path, rto_ms, observed_at_ms, message
):
    from tools.risk_recovery_drill import RiskRecoveryDrillError, run_risk_recovery_drill

    with pytest.raises(RiskRecoveryDrillError, match=message):
        run_risk_recovery_drill(
            drill_dir=str(tmp_path / "drill"),
            rto_ms=rto_ms,
            observed_at_ms=observed_at_ms,
        )


def test_risk_recovery_drill_fails_when_rto_is_exceeded(tmp_path, monkeypatch):
    import tools.risk_recovery_drill as drill

    ticks = iter([10.0, 10.2])
    monkeypatch.setattr(drill.time, "perf_counter", lambda: next(ticks))
    with pytest.raises(drill.RiskRecoveryDrillError, match="rto"):
        drill.run_risk_recovery_drill(
            drill_dir=str(tmp_path / "drill"),
            rto_ms=100,
            observed_at_ms=1_783_734_400_000,
        )


def test_failed_drill_does_not_replace_prior_pass_evidence(tmp_path, monkeypatch):
    import tools.risk_recovery_drill as drill

    evidence = tmp_path / "evidence.json"
    prior = {"schema_version": 1, "verdict": "PASS", "marker": "prior"}
    evidence.write_text(json.dumps(prior), encoding="utf-8")

    def fail(**_kwargs):
        raise drill.RiskRecoveryDrillError("expected failure")

    monkeypatch.setattr(drill, "run_risk_recovery_drill", fail)
    with pytest.raises(drill.RiskRecoveryDrillError, match="expected failure"):
        drill.run_and_write_evidence(
            drill_dir=str(tmp_path / "drill"),
            evidence_path=str(evidence),
            rto_ms=30_000,
            observed_at_ms=1_783_734_400_000,
        )
    assert json.loads(evidence.read_text(encoding="utf-8")) == prior


def test_cli_entrypoint_runs_directly_and_publishes_pass_evidence(tmp_path):
    evidence = tmp_path / "evidence.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "risk_recovery_drill.py"),
            "--drill-dir",
            str(tmp_path / "drill"),
            "--rto-ms",
            "30000",
            "--observed-at-ms",
            "1783734400000",
            "--evidence",
            str(evidence),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(evidence.read_text(encoding="utf-8"))["verdict"] == "PASS"
