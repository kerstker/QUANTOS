from __future__ import annotations

from quantos.contracts.config import RESEARCH_SAFE_DEFAULTS
from services.deployment.gate import PLATFORM_READINESS
from tools.live_readiness_report import build_report


def test_live_readiness_is_fail_closed_and_does_not_arm_platform():
    report = build_report()
    assert report["verdict"] == "BLOCKED_POST_L2_REQUIREMENTS"
    assert report["live_allowed"] is False
    assert report["order_submission_allowed"] is False
    assert report["readiness"] == "L2_DRY_RUN_ONLY"
    assert PLATFORM_READINESS.value == "L2_DRY_RUN_ONLY"
    assert RESEARCH_SAFE_DEFAULTS.live_trading_armed is False
    assert "L2_READINESS_PINNED" in report["blockers"]


def test_live_readiness_exposes_all_three_forward_candidates():
    report = build_report()
    periods = report["checks"]["alpha_forward_clock"]["realized_periods"]
    assert set(periods) >= {"carry_oi_majors", "crowd_contra", "top_contra"}
    # The report reads the durable forward-paper ledger.  Once a period has
    # been honestly realized, the value must remain a non-negative integer;
    # tests must not regress the evidence back to an empty synthetic clock.
    assert all(isinstance(value, int) and value >= 0 for value in periods.values())


def test_live_readiness_prefers_current_forward_status_over_stale_cycle(monkeypatch):
    import tools.live_readiness_report as readiness

    stale_cycle = {
        "alphas": {
            alpha_id: {"forward": {"n_forward_periods": 0, "pending_decisions": None}}
            for alpha_id in ("carry_oi_majors", "crowd_contra", "top_contra")
        }
    }
    current_status = {
        "ran_at": 123456,
        "alphas": {
            "carry_oi_majors": {"n_forward_periods": 1, "pending_decisions": 1},
            "crowd_contra": {"n_forward_periods": 1, "pending_decisions": 1},
            "top_contra": {"n_forward_periods": 0, "pending_decisions": 1},
        },
    }
    original_load = readiness._load

    def fake_load(path):
        if path == "evidence/alpha_paper_cycle.json":
            return stale_cycle
        if path == "data/cache/forward_track_status.json":
            return current_status
        return original_load(path)

    monkeypatch.setattr(readiness, "_load", fake_load)
    report = readiness.build_report()
    clock = report["checks"]["alpha_forward_clock"]
    assert clock["source"] == "data/cache/forward_track_status.json"
    assert clock["realized_periods"] == {
        "carry_oi_majors": 1,
        "crowd_contra": 1,
        "top_contra": 0,
    }
    assert clock["pending_decisions"]["top_contra"] == 1
    assert clock["ran_at"] == 123456


def test_live_readiness_reads_attested_release_status(monkeypatch):
    import tools.live_readiness_report as readiness

    current_status = {
        "ran_at": 654321,
        "refresh_evidence_available": True,
        "alphas": {
            alpha_id: {
                "n_forward_periods": 0,
                "attested_pending_decisions": 0,
                "legacy_unattested_decisions": 2 if alpha_id != "top_contra" else 1,
                "evidence_state": "ADMISSION_READY_PENDING_DECISION",
            }
            for alpha_id in ("carry_oi_majors", "crowd_contra", "top_contra")
        },
    }
    original_load = readiness._load

    def fake_load(path):
        if path == "data/cache/forward_track_status.json":
            return current_status
        return original_load(path)

    monkeypatch.setattr(readiness, "_load", fake_load)
    clock = readiness.build_report()["checks"]["alpha_forward_clock"]
    assert clock["pending_decisions"]["crowd_contra"] == 0
    assert clock["attested_pending_decisions"]["crowd_contra"] == 0
    assert clock["legacy_unattested_decisions"]["crowd_contra"] == 2
    assert clock["evidence_state"]["top_contra"] == "ADMISSION_READY_PENDING_DECISION"
    assert clock["refresh_evidence_available"] is True


def test_live_readiness_blocks_stale_gonogo_snapshot(monkeypatch):
    import tools.live_readiness_report as readiness

    current_status = {
        "ran_at": 200,
        "alphas": {
            alpha_id: {"n_forward_periods": 0, "attested_pending_decisions": 0}
            for alpha_id in ("carry_oi_majors", "crowd_contra", "top_contra")
        },
    }
    stale_gonogo = {"forward_evidence": {"present": True, "ran_at": 199}}
    original_load = readiness._load

    def fake_load(path):
        if path == "data/cache/forward_track_status.json":
            return current_status
        if path == "data/cache/gonogo_report.json":
            return stale_gonogo
        return original_load(path)

    monkeypatch.setattr(readiness, "_load", fake_load)
    report = readiness.build_report()
    assert report["checks"]["go_no_go"]["status"] == "BLOCK"
    assert report["checks"]["go_no_go"]["current_for_forward_status"] is False
    assert "GONOGO_FORWARD_EVIDENCE_STALE" in report["blockers"]
