"""Candidate compiler (727 §8.2): form legality + identity stability, never quality."""

from __future__ import annotations

import pytest

from services.alpha_factory.candidate_compiler import (
    CandidateCompileError,
    compile_candidate,
    compile_from_hypothesis,
)
from services.alpha_factory.hypothesis import create_hypothesis
from services.research import alpha_expression as ax


def _hypothesis(required_factors=("global_ls",)):
    return create_hypothesis(
        economic_mechanism=(
            "Crowded retail long positioning mean-reverts once funding flips sign"
        ),
        expected_direction="short crowded / long uncrowded cross-section",
        expected_horizon="7d",
        required_factors=required_factors,
        market_scope="binance usdt perp majors",
        expected_cost_sensitivity="edge survives 10-20bps taker",
        capacity_assumptions="participation capped at 10% ADV",
        falsification_criteria=("IC t-stat < 2.0 over 100 weekly periods",),
        creator_model="human",
        created_at_ms=1000,
    )


def test_identity_is_stable_and_canonical() -> None:
    expr = ("cs_rank", ("add", ("field", "close"), ("field", "funding")))
    swapped = ("cs_rank", ("add", ("field", "funding"), ("field", "close")))
    first, second = compile_candidate(expr), compile_candidate(swapped)

    assert first.expression_hash == second.expression_hash  # commutative dedup
    assert first.expression == ax.canonicalize(expr)
    assert first.canonical_expression == ax.serialize(first.expression)
    # the canonical form round-trips through the DSL's own parser
    assert ax.deserialize(first.canonical_expression) == first.expression
    assert first.hypothesis_id == ""  # honest absence
    assert first.compile_warnings == ()


def test_invalid_dsl_is_rejected_fail_closed() -> None:
    with pytest.raises(CandidateCompileError):
        compile_candidate("not-a-tuple")
    with pytest.raises(CandidateCompileError):
        compile_candidate(())
    with pytest.raises(CandidateCompileError):
        compile_candidate(("unknown_op", ("field", "close")))
    with pytest.raises(CandidateCompileError):
        compile_candidate(("const", True))  # bool literal is a generator bug
    with pytest.raises(CandidateCompileError):
        compile_candidate(("ts_mean", ("field", "close"), "not-an-int"))


def test_params_and_hypothesis_id_validation() -> None:
    candidate = compile_candidate(
        ("cs_rank", ("field", "close")), params={"family": "test", "index": "0"}
    )
    assert candidate.params == {"family": "test", "index": "0"}
    with pytest.raises(CandidateCompileError):
        compile_candidate(("cs_rank", ("field", "close")), params={"k": 1})
    with pytest.raises(CandidateCompileError):
        compile_candidate(("cs_rank", ("field", "close")), hypothesis_id=7)


def test_hypothesis_binding_records_id_without_warnings_on_match() -> None:
    hypothesis = _hypothesis()
    candidate = compile_from_hypothesis(
        hypothesis, ("cs_rank", ("neg", ("field", "global_ls")))
    )
    assert candidate.hypothesis_id == hypothesis.hypothesis_id
    assert candidate.compile_warnings == ()


def test_hypothesis_factor_mismatch_warns_but_does_not_reject() -> None:
    """The expression may reach the mechanism through intermediate transforms;
    a hard reject would false-kill — the mismatch is a visible warning."""
    hypothesis = _hypothesis(required_factors=("global_ls",))
    candidate = compile_from_hypothesis(
        hypothesis, ("cs_rank", ("ts_mean", ("field", "close"), 10))
    )
    assert candidate.hypothesis_id == hypothesis.hypothesis_id
    assert candidate.compile_warnings == ("HYPOTHESIS_FACTOR_MISMATCH",)


def test_compile_from_hypothesis_requires_a_hypothesis() -> None:
    with pytest.raises(CandidateCompileError):
        compile_from_hypothesis("not-a-hypothesis", ("cs_rank", ("field", "close")))
