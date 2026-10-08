"""Search controller (727 §8.3 layers 1-4): plan, eliminate, persist end-to-end."""

from __future__ import annotations

import dataclasses

import pytest

from services.alpha_factory.constrained_search_space import SearchSpaceConstraints
from services.alpha_factory.hypothesis import create_hypothesis
from services.alpha_factory.search_controller import (
    EliminationRecord,
    SearchController,
    SearchPlanError,
)
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.research_budget import (
    BudgetExhausted,
    ResearchBudget,
)
from services.research.trial_provenance import (
    ActorType,
    TrialProvenance,
    TrialStatus,
    trial_id_for_attempt,
)


def _hypothesis(**overrides):
    base = dict(
        economic_mechanism=(
            "Crowded retail long positioning mean-reverts once funding flips sign"
        ),
        expected_direction="short crowded / long uncrowded cross-section",
        expected_horizon="7d",
        required_factors=("global_ls",),
        market_scope="binance usdt perp majors",
        expected_cost_sensitivity="edge survives 10-20bps taker",
        capacity_assumptions="participation capped at 10% ADV",
        falsification_criteria=("IC t-stat < 2.0 over 100 weekly periods",),
        creator_model="human",
        created_at_ms=1000,
    )
    base.update(overrides)
    return create_hypothesis(**base)


def _constraints(**overrides):
    base = dict(allowed_fields=("global_ls", "close"), max_window=60)
    base.update(overrides)
    return SearchSpaceConstraints(**base)


def _plan_kwargs(**overrides):
    base = dict(
        search_id="ctrl-search",
        hypothesis=_hypothesis(),
        budget=ResearchBudget(),
        constraints=_constraints(),
        candidate_exprs=[
            ("cs_rank", ("neg", ("field", "global_ls"))),
            ("cs_rank", ("ts_mean", ("field", "close"), 10)),
        ],
        now_ms=100,
    )
    base.update(overrides)
    return base


def test_unqualified_hypothesis_never_starts_a_search() -> None:
    hypothesis = dataclasses.replace(_hypothesis())
    object.__setattr__(hypothesis, "economic_mechanism", "short")
    with pytest.raises(SearchPlanError, match="HYP_MECHANISM_TOO_SHORT"):
        SearchController().plan_search(**_plan_kwargs(hypothesis=hypothesis))


def test_semantic_eliminations_record_status_and_codes() -> None:
    plan = SearchController().plan_search(
        **_plan_kwargs(
            candidate_exprs=[
                ("cs_rank", ("neg", ("field", "global_ls"))),      # survives
                ("ts_mean", ("field", "close"), 10),               # root
                ("cs_rank", ("ts_mean", ("field", "close"), 500)), # window
                ("cs_rank", ("field", "volume")),                  # field
            ]
        )
    )
    assert len(plan.candidates) == 1
    by_reason = {e.reasons[0]: e for e in plan.eliminations}
    assert set(by_reason) == {
        "SPACE_ROOT_NOT_ECONOMIC",
        "SPACE_WINDOW_OUT_OF_RANGE",
        "SPACE_FIELD_NOT_ALLOWED",
    }
    for elimination in plan.eliminations:
        assert elimination.status is TrialStatus.ELIMINATED_SEMANTIC
        assert elimination.expression_hash  # compilable candidates keep identity


def test_compile_failure_is_a_semantic_elimination() -> None:
    plan = SearchController().plan_search(
        **_plan_kwargs(candidate_exprs=["not-a-tuple", ("cs_rank", ("field", "close"))])
    )
    assert len(plan.candidates) == 1
    compile_elimination = plan.eliminations[0]
    assert compile_elimination.status is TrialStatus.ELIMINATED_SEMANTIC
    assert compile_elimination.reasons[0] == "COMPILE_INVALID_DSL"
    assert compile_elimination.expression_hash == ""  # honest: no identity derivable


def test_budget_exhaustion_refuses_before_any_work() -> None:
    with pytest.raises(BudgetExhausted):
        SearchController().plan_search(
            **_plan_kwargs(budget=ResearchBudget(max_trials=1))
        )


def test_survivors_keep_first_seen_order_and_duplicates_eliminate() -> None:
    plan = SearchController().plan_search(
        **_plan_kwargs(
            candidate_exprs=[
                ("cs_rank", ("neg", ("field", "global_ls"))),
                ("cs_rank", ("ts_mean", ("field", "close"), 10)),
                ("cs_rank", ("neg", ("field", "global_ls"))),  # exact duplicate
            ]
        )
    )
    assert [c.canonical_expression for c in plan.candidates] == [
        "(cs_rank (neg (field global_ls)))",
        "(cs_rank (ts_mean (field close) 10))",
    ]
    assert len(plan.eliminations) == 1
    elimination = plan.eliminations[0]
    assert elimination.status is TrialStatus.ELIMINATED_DUPLICATE
    assert elimination.reasons[0] == "EXACT_DUPLICATE"
    assert elimination.expression_hash == elimination.reasons[1]


def test_hypothesis_binding_and_mismatch_warning_propagate() -> None:
    hypothesis = _hypothesis()
    plan = SearchController().plan_search(**_plan_kwargs(hypothesis=hypothesis))
    bound = plan.candidates[0]
    mismatched = plan.candidates[1]
    assert bound.hypothesis_id == hypothesis.hypothesis_id
    assert bound.compile_warnings == ()
    assert mismatched.compile_warnings == ("HYPOTHESIS_FACTOR_MISMATCH",)
    assert plan.hypothesis_id == hypothesis.hypothesis_id
    assert plan.planned_at_ms == 100


def test_evaluate_stopping_forwards_rules_and_trial_cap() -> None:
    controller = SearchController()
    assert controller.evaluate_stopping(10, [1.0], ResearchBudget(max_trials=10))
    assert not controller.evaluate_stopping(9, [1.0], ResearchBudget(max_trials=10))
    no_improve = ResearchBudget(stopping_rules={"no_improvement_trials": 2})
    assert controller.evaluate_stopping(3, [1.0, 0.5, 0.4], no_improve)
    assert not controller.evaluate_stopping(2, [1.0, 2.0], no_improve)
    assert not controller.evaluate_stopping(100, [1.0], ResearchBudget())


def test_plan_validation_is_fail_closed() -> None:
    with pytest.raises(SearchPlanError):
        SearchController().plan_search(**_plan_kwargs(candidate_exprs=[]))
    with pytest.raises(SearchPlanError):
        SearchController().plan_search(**_plan_kwargs(now_ms=-1))
    with pytest.raises(SearchPlanError):
        SearchController().plan_search(**_plan_kwargs(search_id=" "))
    with pytest.raises(SearchPlanError):
        EliminationRecord(
            expression_hash="x",
            status=TrialStatus.ELIMINATED_SEMANTIC,
            reasons=(),
        )


def test_eliminations_and_survivors_persist_end_to_end() -> None:
    """The controller writes nothing; the caller persists plan output through
    the P5-1 wiring. This consumes EliminationRecord/CompiledCandidate into
    real TrialProvenance rows and verifies the chain."""
    registry = ExperimentRegistry(SqliteExperimentStore(":memory:", create=True))
    plan = SearchController().plan_search(
        **_plan_kwargs(
            candidate_exprs=[
                ("cs_rank", ("neg", ("field", "global_ls"))),
                ("cs_rank", ("neg", ("field", "global_ls"))),  # duplicate
                ("ts_mean", ("field", "close"), 10),           # root violation
            ]
        )
    )

    config_hashes: list[str] = []
    for candidate in plan.candidates:
        config_hash = candidate.expression_hash
        config_hashes.append(config_hash)
        assert registry.record_trial_provenance(
            TrialProvenance(
                trial_id=trial_id_for_attempt(plan.search_id, config_hash),
                research_session_id=plan.search_id,
                config_hash=config_hash,
                actor_id="tests.alpha_factory",
                actor_type=ActorType.AUTOMATED_SEARCH,
                status=TrialStatus.RECORDED,
                started_at=plan.planned_at_ms,
                completed_at=plan.planned_at_ms,
                hypothesis_id=candidate.hypothesis_id,
                canonical_expression=candidate.canonical_expression,
                expression_hash=candidate.expression_hash,
            )
        )
    for index, elimination in enumerate(plan.eliminations):
        config_hash = elimination.expression_hash or f"unparseable-{index}"
        assert registry.record_trial_provenance(
            TrialProvenance(
                trial_id=trial_id_for_attempt(plan.search_id, config_hash)
                + f":eliminated-{index}",
                research_session_id=plan.search_id,
                config_hash=config_hash,
                actor_id="tests.alpha_factory",
                actor_type=ActorType.AUTOMATED_SEARCH,
                status=elimination.status,
                started_at=plan.planned_at_ms,
                completed_at=plan.planned_at_ms,
                failure_reasons=elimination.reasons,
            )
        )

    rows = [
        row
        for config_hash in config_hashes
        for row in registry.trial_provenance_for_config(config_hash)
    ]
    # §7.2「重复候选也必须记账」: the duplicate elimination shares the
    # survivor's config_hash, so the config carries TWO attempt rows.
    assert sorted(row["record"]["status"] for row in rows) == [
        "ELIMINATED_DUPLICATE",
        "RECORDED",
    ]
    import json

    statuses = sorted(
        json.loads(raw["record_json"])["status"]
        for raw in registry._store._conn.execute(
            "SELECT record_json FROM trial_provenance"
        )
    )
    assert statuses == [
        "ELIMINATED_DUPLICATE",
        "ELIMINATED_SEMANTIC",
        "RECORDED",
    ]
    assert registry.trial_provenance_chain_valid() is True
