from __future__ import annotations

from contextlib import nullcontext
import json
from types import SimpleNamespace

from services.factor_garden.canonical import (
    CROWD_CONTRA_EXPRESSION,
    TOP_CONTRA_EXPRESSION,
)
from services.research.alpha_expression import FieldPanel
from tools import positioning_composite_evaluation as driver


def _panel(n_periods: int = 56, n_instruments: int = 12) -> FieldPanel:
    instruments = tuple(f"COIN{i}" for i in range(n_instruments))
    event_times = tuple(1_700_000_000_000 + row * 86_400_000 for row in range(n_periods))
    close = []
    global_ls = []
    top_ls = []
    for row in range(n_periods):
        close.append(
            [
                100.0 + row * 0.2 + column * 0.4 + ((row + column) % 3) * 0.1
                for column in range(n_instruments)
            ]
        )
        global_ls.append(
            [1.0 + 0.1 * ((row + column) % 7) for column in range(n_instruments)]
        )
        top_ls.append(
            [0.8 + 0.08 * ((2 * row + column) % 9) for column in range(n_instruments)]
        )
    return FieldPanel(
        instruments,
        event_times,
        {"close": close, "global_ls": global_ls, "top_ls": top_ls},
    )


def test_candidate_is_one_fixed_equal_standardized_positioning_composite() -> None:
    assert driver.POSITIONING_COMPOSITE_EXPRESSION == (
        "div",
        (
            "add",
            ("cs_zscore", CROWD_CONTRA_EXPRESSION),
            TOP_CONTRA_EXPRESSION,
        ),
        ("const", 2.0),
    )
    assert tuple(driver.CANDIDATE_EXPRESSIONS) == (
        "crowd_contra_control",
        "top_contra_control",
        "positioning_consensus",
    )


def test_build_candidate_batch_preregisters_controls_and_candidate() -> None:
    candidates, evidence, mappings = driver.build_candidate_batch(_panel())

    assert [mapping for mapping in mappings] == list(driver.CANDIDATE_EXPRESSIONS)
    assert len(candidates) == 3
    assert set(evidence) == {
        mapping["research_alpha_id"] for mapping in mappings.values()
    }
    for definition, periods in candidates:
        assert definition.definition_hash in {
            mapping["definition_hash"] for mapping in mappings.values()
        }
        assert periods
        assert evidence[definition.alpha_id]["leakage_ok"] is True
    composite = next(
        definition
        for definition, _periods in candidates
        if definition.name == "positioning_consensus"
    )
    assert composite.factor_ids == ("global_ls", "top_ls")


def test_registered_run_uses_closed_budget_and_never_marks_testnet_eligible(
    tmp_path, monkeypatch
) -> None:
    panel = _panel()
    captured = {}
    fake_registry = SimpleNamespace(get_evaluation=lambda _run_id: None)

    def run_batch(candidates, **kwargs):
        captured["candidates"] = tuple(candidates)
        captured.update(kwargs)
        return {"recorded_run_ids": [], "registered_candidate_count": 3}

    monkeypatch.setattr(driver, "run_registered_candidate_batch", run_batch)
    monkeypatch.setattr(
        driver,
        "production_experiment_registry",
        lambda: nullcontext(fake_registry),
    )

    document = driver.run(
        output=tmp_path / "result.json",
        recorded_at=123,
        source_commit="a" * 40,
        panel=panel,
    )

    assert len(captured["candidates"]) == 3
    assert captured["budget"].max_trials == 3
    assert captured["budget"].max_feature_count == 2
    assert captured["budget"].max_parameter_dimensions == 2
    assert captured["data_source"] == driver.DATA_SOURCE
    assert captured["uses_locked_oos"] is False
    assert captured["code_hash"] == "a" * 40
    assert document["status"] == "PENDING"
    assert document["decision"] == {
        "formal_evaluation_pass": False,
        "human_review_required": False,
        "forward_admission_allowed": False,
        "testnet_eligible": False,
    }
    assert document["safety"]["order_submission_allowed"] is False
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8")) == document


def test_decision_requires_verified_formal_pass_before_forward_admission() -> None:
    assert driver._decision(None) == {
        "formal_evaluation_pass": False,
        "human_review_required": False,
        "forward_admission_allowed": False,
        "testnet_eligible": False,
    }
    assert driver._decision({"verified": True, "verdict": "REQUIRES_HUMAN_REVIEW"}) == {
        "formal_evaluation_pass": False,
        "human_review_required": True,
        "forward_admission_allowed": False,
        "testnet_eligible": False,
    }
    assert driver._decision({"verified": True, "verdict": "PASS"}) == {
        "formal_evaluation_pass": True,
        "human_review_required": False,
        "forward_admission_allowed": True,
        "testnet_eligible": False,
    }
