import json

import tools.l2_paper_operator as operator_tool
from tools.l2_paper_operator import run_operator


def _flow(*, completed: bool = True) -> dict:
    return {
        "verdict": {
            "flow_completed": completed,
            "paper_packet_ready_for_all_three": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        },
        "safety": {"paper_only": True},
    }


def _preview(*, ready: bool = True) -> dict:
    return {
        "verdict": {
            "paper_preview_ready": ready,
            "live_allowed": False,
            "order_submission_allowed": False,
        },
        "alphas": {},
        "errors": {},
        "safety": {"paper_only": True},
    }


def test_operator_composes_flow_and_preview_and_remains_paper_only(tmp_path):
    calls: list[str] = []
    output = tmp_path / "operator.json"

    report = run_operator(
        advance_forward=True,
        output_path=str(output),
        flow_runner=lambda: calls.append("flow") or _flow(),
        preview_runner=lambda: calls.append("preview") or _preview(),
    )

    assert calls == ["flow", "preview"]
    assert report["verdict"] == {
        "operator_completed": True,
        "paper_preview_ready": True,
        "research_packet_ready_for_all_three": False,
        "live_allowed": False,
        "order_submission_allowed": False,
        "human_review_required": True,
    }
    assert report["safety"] == {
        "paper_only": True,
        "private_binance_access": False,
        "credential_access": False,
        "broker_order_submission": False,
    }
    assert json.loads(output.read_text(encoding="utf-8"))["flow"] == "L2_PAPER_OPERATOR"


def test_operator_skips_preview_when_flow_fails_closed(tmp_path):
    calls: list[str] = []
    report = run_operator(
        output_path=str(tmp_path / "operator.json"),
        flow_runner=lambda: calls.append("flow") or _flow(completed=False),
        preview_runner=lambda: calls.append("preview") or _preview(),
    )

    assert calls == ["flow"]
    assert report["verdict"]["operator_completed"] is False
    assert report["stages"]["paper_order_preview"]["status"] == "SKIPPED"
    assert report["stages"]["paper_order_preview"]["paper_preview_ready"] is False


def test_operator_marks_unready_preview_as_failure(tmp_path):
    report = run_operator(
        output_path=str(tmp_path / "operator.json"),
        flow_runner=lambda: _flow(),
        preview_runner=lambda: _preview(ready=False),
    )

    assert report["verdict"]["operator_completed"] is False
    assert report["stages"]["paper_order_preview"]["status"] == "ERROR"


def test_bounded_mark_mode_skips_all_candidate_research(monkeypatch, tmp_path):
    captured = {}

    def fake_flow(**kwargs):
        captured["flow"] = kwargs
        return _flow()

    monkeypatch.setattr(operator_tool, "run_flow", fake_flow)
    monkeypatch.setattr(
        operator_tool,
        "_run_candidate_mining",
        lambda: (_ for _ in ()).throw(AssertionError("mining must stay off hot path")),
    )
    monkeypatch.setattr(
        operator_tool,
        "_run_candidate_evaluation",
        lambda: (_ for _ in ()).throw(AssertionError("evaluation must stay off hot path")),
    )
    monkeypatch.setattr(
        operator_tool,
        "_run_candidate_risk_bundle",
        lambda: (_ for _ in ()).throw(AssertionError("risk bundle must stay off hot path")),
    )

    report = run_operator(
        advance_forward=False,
        refresh_candidate_research=False,
        output_path=str(tmp_path / "operator.json"),
        preview_runner=lambda: _preview(),
    )

    assert captured["flow"] == {"advance_forward": False, "run_candidates": False}
    assert report["verdict"]["operator_completed"] is True
    for stage in (
        "candidate_mining",
        "forward_candidate_evaluation",
        "paper_research_risk_bundle",
    ):
        assert report["stages"][stage] == {
            "status": "SKIPPED",
            "reason": "INDEPENDENT_RESEARCH_SCHEDULE",
        }
