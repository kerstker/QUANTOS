import json

from tools import paper_shadow_realtime


def test_shadow_realtime_is_a_fixed_isolated_profile(monkeypatch, tmp_path):
    captured = {}
    output = tmp_path / "shadow.json"
    monkeypatch.setattr(paper_shadow_realtime, "OUTPUT_PATH", str(output))
    profile = list(paper_shadow_realtime.PROFILE_ARGS)
    profile[profile.index("data/cache/paper_shadow_realtime_status.json")] = str(output)
    monkeypatch.setattr(paper_shadow_realtime, "PROFILE_ARGS", tuple(profile))

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
                        "carry_oi_majors": {},
                        "crowd_contra": {},
                        "top_contra": {},
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
    assert report["verdict"]["backtest_reference_required"] is False
    assert report["promotion_eligible"] is False
