"""Hypothesis value object + economic rules (727 §8.1)."""

from __future__ import annotations

import dataclasses

import pytest

from services.alpha_factory.economic_rules import check_hypothesis_rules
from services.alpha_factory.hypothesis import (
    Hypothesis,
    HypothesisError,
    create_hypothesis,
    hypothesis_content_hash,
)


def _kwargs(**overrides):
    base = dict(
        economic_mechanism=(
            "Crowded retail long positioning mean-reverts once funding flips sign"
        ),
        expected_direction="short crowded / long uncrowded cross-section",
        expected_horizon="7d",
        required_factors=("global_ls",),
        market_scope="binance usdt perp majors",
        regime_assumptions=("OI data available since 2020",),
        expected_cost_sensitivity="edge survives 10-20bps taker",
        capacity_assumptions="participation capped at 10% ADV",
        falsification_criteria=("IC t-stat < 2.0 over 100 weekly periods",),
        known_risks=("regime flip in crowded names",),
        creator_model="human",
        created_at_ms=1000,
    )
    base.update(overrides)
    return base


def test_content_derived_id_is_stable_and_content_sensitive() -> None:
    first = create_hypothesis(**_kwargs())
    same_claim = create_hypothesis(**_kwargs(created_at_ms=9999, prompt_hash="abc"))
    changed = create_hypothesis(
        **_kwargs(economic_mechanism="A different economic mechanism entirely here")
    )

    assert first.hypothesis_id.startswith("hyp_")
    assert len(first.hypothesis_id) == len("hyp_") + 16
    # provenance of the writing act (created_at_ms / prompt_hash) is NOT identity
    assert same_claim.hypothesis_id == first.hypothesis_id
    assert changed.hypothesis_id != first.hypothesis_id


def test_content_hash_excludes_provenance_includes_claim() -> None:
    first = create_hypothesis(**_kwargs())
    assert hypothesis_content_hash(first) == hypothesis_content_hash(
        create_hypothesis(**_kwargs(created_at_ms=42, prompt_hash="zzz"))
    )
    assert hypothesis_content_hash(first) != hypothesis_content_hash(
        create_hypothesis(**_kwargs(creator_model="gpt-test-1"))
    )
    assert len(hypothesis_content_hash(first)) == 64


def test_mechanism_must_be_meaningful() -> None:
    with pytest.raises(HypothesisError, match="HYP_MECHANISM_TOO_SHORT"):
        create_hypothesis(**_kwargs(economic_mechanism="Sharpe可能更高"))


def test_falsification_criteria_required() -> None:
    with pytest.raises(HypothesisError, match="HYP_NO_FALSIFICATION"):
        create_hypothesis(**_kwargs(falsification_criteria=()))


def test_vague_falsification_criteria_rejected() -> None:
    with pytest.raises(HypothesisError, match="HYP_VAGUE_FALSIFICATION"):
        create_hypothesis(**_kwargs(falsification_criteria=("it stops working",)))
    with pytest.raises(HypothesisError, match="HYP_VAGUE_FALSIFICATION"):
        create_hypothesis(**_kwargs(falsification_criteria=("bad",)))


def test_required_factors_required() -> None:
    with pytest.raises(HypothesisError, match="HYP_NO_FACTORS"):
        create_hypothesis(**_kwargs(required_factors=()))


def test_field_validation_is_fail_closed() -> None:
    with pytest.raises(HypothesisError):
        create_hypothesis(**_kwargs(expected_direction=""))
    with pytest.raises(HypothesisError):
        create_hypothesis(**_kwargs(creator_model=""))
    with pytest.raises(HypothesisError):
        create_hypothesis(**_kwargs(expected_horizon="  padded "))
    with pytest.raises(HypothesisError):
        create_hypothesis(**_kwargs(created_at_ms=-1))
    with pytest.raises(HypothesisError):
        create_hypothesis(**_kwargs(created_at_ms=True))


def test_check_rules_pass_for_valid_hypothesis() -> None:
    assert check_hypothesis_rules(create_hypothesis(**_kwargs())) == ()


def test_rule_codes_cover_direction_factors_provenance() -> None:
    """The dataclass catches these first; the rule surface must still name them
    for the P5-2 controller re-checking hypotheses it did not construct."""
    hypothesis = create_hypothesis(**_kwargs())

    no_direction = dataclasses.replace(hypothesis)
    object.__setattr__(no_direction, "expected_direction", "")
    assert "HYP_NO_DIRECTION" in check_hypothesis_rules(no_direction)

    no_provenance = dataclasses.replace(hypothesis)
    object.__setattr__(no_provenance, "creator_model", "")
    assert "HYP_NO_PROVENANCE" in check_hypothesis_rules(no_provenance)


def test_human_and_model_creators_both_recordable() -> None:
    human = create_hypothesis(**_kwargs(creator_model="human"))
    model = create_hypothesis(
        **_kwargs(creator_model="external-llm-via-tools", prompt_hash="f" * 64)
    )
    assert isinstance(human, Hypothesis) and isinstance(model, Hypothesis)
    assert human.hypothesis_id != model.hypothesis_id
