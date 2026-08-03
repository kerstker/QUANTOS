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
        return 1

    monkeypatch.setattr(paper_shadow_realtime.paper_realtime, "main", fake_main)
    assert paper_shadow_realtime.main([]) == 0
    args = captured["args"]
    assert "--no-bootstrap" in args
    assert "data/cache/paper_shadow_ledger.db" in args
    assert "data/cache/paper_shadow_realtime.db" in args
    assert "data/cache/paper_shadow_auto.json" in args
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["verdict"]["shadow_paper_running"] is True
    assert report["verdict"]["candidate_coverage_complete"] is True
    assert report["verdict"]["candidate_count"] == 4
    assert report["verdict"]["backtest_reference_required"] is False
    assert report["promotion_eligible"] is False


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

    assert paper_shadow_realtime.main([]) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "SAFE_BLOCKED"
    assert report["verdict"]["shadow_paper_running"] is False
    assert report["verdict"]["live_allowed"] is False
