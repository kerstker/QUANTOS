import json

from tools import paper_shadow_realtime


def test_shadow_realtime_is_a_fixed_isolated_profile(monkeypatch, tmp_path):
    captured = {}
    output = tmp_path / "shadow.json"
    monkeypatch.setattr(paper_shadow_realtime, "OUTPUT_PATH", str(output))
    profile = list(paper_shadow_realtime.PROFILE_ARGS)
    profile[profile.index("data/cache/paper_shadow_realtime_status.json")] = str(output)
    monkeypatch.setattr(paper_shadow_realtime, "PROFILE_ARGS", tuple(profile))
    expected = (
        "carry_oi_majors",
        "crowd_contra",
        "top_contra",
        "challenger",
    )
    monkeypatch.setattr(paper_shadow_realtime, "paper_candidate_ids", lambda: expected)
    monkeypatch.setattr(
        paper_shadow_realtime.paper_shadow_auto,
        "run_shadow",
        lambda: {"verdict": {"shadow_paper_running": True}, "errors": {}},
    )

    def fake_main(args):
        captured["args"] = tuple(args)
        output.write_text(
            json.dumps(
                {
                    "verdict": {"paper_mark_ready": True},
                    "safety": {
                        "paper_only": True,
                        "broker_connectivity": False,
                        "broker_order_submission": False,
                    },
                    "alphas": {
                        alpha_id: {"status": "PAPER_MARKED"}
                        for alpha_id in expected
                    },
                }
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(paper_shadow_realtime.paper_realtime, "main", fake_main)
    assert paper_shadow_realtime.main([]) == 0
    args = captured["args"]
    assert "--no-bootstrap" in args
    assert "data/cache/paper_shadow_routed_ledger_v2.db" in args
    assert "data/cache/paper_shadow_realtime.db" in args
    assert "data/cache/paper_shadow_auto.json" in args
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["verdict"]["shadow_paper_running"] is True
    assert report["verdict"]["candidate_coverage_complete"] is True
    assert report["verdict"]["candidate_count"] == 4
    assert report["verdict"]["backtest_reference_required"] is False
    assert report["promotion_eligible"] is False
    assert report["upstream_cycle"]["exit_code"] == 0
    assert report["upstream_cycle"]["artifact_refreshed"] is True


def test_shadow_realtime_fails_closed_before_marking_incomplete_population(
    monkeypatch, tmp_path
):
    output = tmp_path / "shadow-blocked.json"
    monkeypatch.setattr(paper_shadow_realtime, "OUTPUT_PATH", str(output))
    monkeypatch.setattr(
        paper_shadow_realtime.paper_shadow_auto,
        "run_shadow",
        lambda: {
            "verdict": {"shadow_paper_running": False},
            "errors": {"challenger": {"error": "missing attested decision"}},
        },
    )
    monkeypatch.setattr(
        paper_shadow_realtime.paper_realtime,
        "main",
        lambda args: (_ for _ in ()).throw(AssertionError("marking must not run")),
    )

    # Missing attested decisions are a current fail-closed paper state, not
    # an infrastructure failure; the scheduler watchdog must classify it as
    # SAFE_BLOCKED.
    assert paper_shadow_realtime.main([]) == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "SAFE_BLOCKED"
    assert report["verdict"]["shadow_paper_running"] is False
    assert report["verdict"]["live_allowed"] is False


def test_shadow_realtime_accepts_refreshed_nonpromotable_operational_cycle(
    monkeypatch, tmp_path
):
    output = tmp_path / "shadow-nonpromotable.json"
    monkeypatch.setattr(paper_shadow_realtime, "OUTPUT_PATH", str(output))
    profile = list(paper_shadow_realtime.PROFILE_ARGS)
    profile[profile.index("data/cache/paper_shadow_realtime_status.json")] = str(output)
    monkeypatch.setattr(paper_shadow_realtime, "PROFILE_ARGS", tuple(profile))
    expected = ("carry", "crowd")
    monkeypatch.setattr(paper_shadow_realtime, "paper_candidate_ids", lambda: expected)
    monkeypatch.setattr(
        paper_shadow_realtime.paper_shadow_auto,
        "run_shadow",
        lambda: {"verdict": {"shadow_paper_running": True}, "errors": {}},
    )

    def fake_main(_args):
        output.write_text(
            json.dumps(
                {
                    "errors": {},
                    "verdict": {
                        "paper_mark_ready": True,
                        "backtest_reference_present_for_all_execution_candidates": False,
                    },
                    "safety": {
                        "paper_only": True,
                        "broker_connectivity": False,
                        "broker_order_submission": False,
                    },
                    "alphas": {
                        alpha_id: {"status": "PAPER_MARKED"}
                        for alpha_id in expected
                    },
                }
            ),
            encoding="utf-8",
        )
        return 1

    monkeypatch.setattr(paper_shadow_realtime.paper_realtime, "main", fake_main)
    assert paper_shadow_realtime.main([]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["verdict"]["shadow_paper_running"] is True
    assert report["verdict"]["official_paper_ready"] is False
    assert report["verdict"]["promotion_eligible"] is False
    assert report["upstream_cycle"]["exit_code"] == 1
    assert report["upstream_cycle"]["artifact_refreshed"] is True
    assert report["upstream_cycle"]["operational_artifact_accepted"] is True


def test_shadow_realtime_preserves_real_upstream_failure(
    monkeypatch, tmp_path
):
    output = tmp_path / "upstream-error.json"
    monkeypatch.setattr(paper_shadow_realtime, "OUTPUT_PATH", str(output))
    profile = list(paper_shadow_realtime.PROFILE_ARGS)
    profile[profile.index("data/cache/paper_shadow_realtime_status.json")] = str(output)
    monkeypatch.setattr(paper_shadow_realtime, "PROFILE_ARGS", tuple(profile))
    expected = ("carry", "crowd")
    monkeypatch.setattr(paper_shadow_realtime, "paper_candidate_ids", lambda: expected)
    monkeypatch.setattr(
        paper_shadow_realtime.paper_shadow_auto,
        "run_shadow",
        lambda: {"verdict": {"shadow_paper_running": True}, "errors": {}},
    )

    def fake_main(_args):
        output.write_text(
            json.dumps(
                {
                    "errors": {"carry": {"error": "upstream failure"}},
                    "verdict": {
                        "paper_mark_ready": True,
                        "backtest_reference_present_for_all_execution_candidates": True,
                    },
                    "safety": {
                        "paper_only": True,
                        "broker_connectivity": False,
                        "broker_order_submission": False,
                    },
                    "alphas": {
                        alpha_id: {"status": "PAPER_MARKED"}
                        for alpha_id in expected
                    },
                }
            ),
            encoding="utf-8",
        )
        return 1

    monkeypatch.setattr(paper_shadow_realtime.paper_realtime, "main", fake_main)
    assert paper_shadow_realtime.main([]) == 1


def test_shadow_realtime_rejects_stale_success_artifact_after_upstream_failure(
    monkeypatch, tmp_path
):
    output = tmp_path / "stale-success.json"
    output.write_text('{"verdict":{"paper_mark_ready":true}}', encoding="utf-8")
    monkeypatch.setattr(paper_shadow_realtime, "OUTPUT_PATH", str(output))
    monkeypatch.setattr(
        paper_shadow_realtime.paper_shadow_auto,
        "run_shadow",
        lambda: {"verdict": {"shadow_paper_running": True}, "errors": {}},
    )
    monkeypatch.setattr(
        paper_shadow_realtime.paper_realtime,
        "main",
        lambda _args: 1,
    )

    assert paper_shadow_realtime.main([]) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "SAFE_BLOCKED"
    assert report["errors"]["paper_realtime"] == {
        "code": "UPSTREAM_PAPER_REALTIME_FAILED",
        "exit_code": 1,
    }
    assert report["verdict"]["paper_mark_ready"] is False
    assert report["verdict"]["shadow_paper_running"] is False


def test_shadow_realtime_requires_current_cycle_artifact_refresh(
    monkeypatch, tmp_path
):
    output = tmp_path / "not-refreshed.json"
    output.write_text('{"verdict":{"paper_mark_ready":true}}', encoding="utf-8")
    monkeypatch.setattr(paper_shadow_realtime, "OUTPUT_PATH", str(output))
    monkeypatch.setattr(
        paper_shadow_realtime.paper_shadow_auto,
        "run_shadow",
        lambda: {"verdict": {"shadow_paper_running": True}, "errors": {}},
    )
    monkeypatch.setattr(
        paper_shadow_realtime.paper_realtime,
        "main",
        lambda _args: 0,
    )

    assert paper_shadow_realtime.main([]) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "SAFE_BLOCKED"
    assert report["errors"]["paper_realtime"]["code"] == (
        "PAPER_REALTIME_ARTIFACT_NOT_REFRESHED"
    )
    assert report["upstream_cycle"]["artifact_refreshed"] is False
