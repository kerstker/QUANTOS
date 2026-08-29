from __future__ import annotations

from contextlib import nullcontext
import json
from types import SimpleNamespace

from services.research.alpha_expression import FieldPanel
from tools import positioning_divergence_evaluation as driver


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


def test_family_is_one_fixed_top_vs_retail_divergence_hypothesis() -> None:
    assert driver.RETAIL_CONTRA_EXPRESSION == (
        "cs_zscore",
        ("neg", ("field", "global_ls")),
    )
    assert driver.TOP_FOLLOW_EXPRESSION == (
        "cs_zscore",
        ("field", "top_ls"),
    )
    assert driver.POSITIONING_DIVERGENCE_EXPRESSION == (
        "div",
        (
            "add",
            driver.RETAIL_CONTRA_EXPRESSION,
            driver.TOP_FOLLOW_EXPRESSION,
        ),
        ("const", 2.0),
    )
    assert tuple(driver.CANDIDATE_EXPRESSIONS) == (
        "retail_contra_control",
        "top_follow_control",
        "top_vs_retail_divergence",
    )
    assert driver.EVALUATION_QUANTILE == 0.3
    assert driver.EVALUATION_CONTRACT["quantile"] == 0.3


def test_build_candidate_batch_preregisters_complete_family() -> None:
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
    divergence = next(
        definition
        for definition, _periods in candidates
        if definition.name == "top_vs_retail_divergence"
    )
    assert divergence.factor_ids == ("global_ls", "top_ls")


def test_registered_run_uses_fixed_quantile_closed_budget_and_no_testnet(
    tmp_path, monkeypatch
) -> None:
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

    document = driver._run_legacy_diagnostic(
        output=tmp_path / "result.json",
        recorded_at=123,
        source_commit="a" * 40,
        panel=_panel(),
    )

    assert len(captured["candidates"]) == 3
    assert captured["quantile"] == 0.3
    assert captured["budget"].max_trials == 3
    assert captured["budget"].max_feature_count == 2
    assert captured["budget"].max_parameter_dimensions == 2
    assert captured["uses_locked_oos"] is False
    assert captured["code_hash"] == "a" * 40
    assert document["status"] == "PENDING"
    assert document["decision"] == {
        "selected_alias": None,
        "formal_evaluation_pass": False,
        "human_review_required": False,
        "forward_admission_allowed": False,
        "testnet_eligible": False,
    }
    assert document["safety"]["order_submission_allowed"] is False
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8")) == document


def test_selection_rule_is_preregistered_and_requires_verified_formal_pass() -> None:
    evaluations = {
        "retail_contra_control": {
            "verified": True,
            "verdict": "PASS",
            "net_sharpe": 1.0,
        },
        "top_follow_control": {
            "verified": True,
            "verdict": "REQUIRES_HUMAN_REVIEW",
            "net_sharpe": 3.0,
        },
        "top_vs_retail_divergence": {
            "verified": True,
            "verdict": "PASS",
            "net_sharpe": 2.0,
        },
    }

    assert driver._decision(evaluations) == {
        "selected_alias": "top_vs_retail_divergence",
        "formal_evaluation_pass": True,
        "human_review_required": True,
        "forward_admission_allowed": True,
        "testnet_eligible": False,
    }


def test_evaluation_row_uses_immutable_search_link_for_definition_binding() -> None:
    _candidates, _evidence, mappings = driver.build_candidate_batch(_panel())
    mapping = mappings["top_vs_retail_divergence"]
    run_id = "run-divergence"
    content_hash = "c" * 64
    registry = SimpleNamespace(
        get_evaluation=lambda candidate_run_id: {
            "run_id": candidate_run_id,
            "alpha_id": mapping["research_alpha_id"],
            "dataset_version": "dataset-v1",
            "content_hash": content_hash,
            "evaluation_json": json.dumps(
                {
                    "verdict": "FAIL",
                    "deflated_sharpe": 0.33,
                    "pbo": 0.15,
                    "net_sharpe": 2.16,
                    "n_periods": 196,
                    "reasons": ["DEFLATED_SHARPE_BELOW_MIN"],
                }
            ),
        },
        search_evaluations=lambda search_id: [
            {
                "search_id": search_id,
                "config_hash": mapping["definition_hash"],
                "run_id": run_id,
                "evaluation_content_hash": content_hash,
            }
        ],
        verify_evaluation=lambda candidate_run_id: candidate_run_id == run_id,
    )

    item = driver._evaluation_row(registry, "search-divergence", run_id, mappings)

    assert item is not None
    alias, evaluation = item
    assert alias == "top_vs_retail_divergence"
    assert evaluation["verified"] is True
    assert evaluation["verdict"] == "FAIL"
