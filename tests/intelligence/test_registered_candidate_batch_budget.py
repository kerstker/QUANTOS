"""W10 §14.2 budget path tests for run_registered_candidate_batch — budget checked
FIRST (refusal writes nothing to the durable ledger), metadata recorded in the run
artifact when declared, and byte-identical behavior when budget is absent.
All in-memory; ResearchAgentLoop.run is stubbed only where evaluation is irrelevant."""
from __future__ import annotations

import pytest

from quantos.contracts.alpha import AlphaDefinition
from services.intelligence import registered_candidate_batch as batch_module
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.research_budget import BudgetExhausted, ResearchBudget


run_registered_candidate_batch = (
    batch_module._run_registered_candidate_batch_legacy_diagnostic
)


def _registry() -> ExperimentRegistry:
    return ExperimentRegistry(SqliteExperimentStore(":memory:", create=True))


def _candidates(count: int, *, factor_count: int = 1, param_count: int = 2):
    return tuple(
        (
            AlphaDefinition(
                alpha_id=f"budget-candidate-{index}",
                name=f"Budget Candidate {index}",
                factor_ids=tuple(f"f{j}" for j in range(factor_count)),
                params={f"p{j}": str(index) for j in range(param_count)},
            ),
            (),
        )
        for index in range(count)
    )


def _stub_agent_run(monkeypatch):
    monkeypatch.setattr(
        batch_module.ResearchAgentLoop,
        "run",
        lambda _self, received, **_kwargs: {
            "report": {
                definition.alpha_id: {"state": None}
                for definition, _periods in received
            },
            "human_review_queue": [],
        },
    )


def _run(registry, candidates, *, search_id="budget-batch", recorded_at=100, **kwargs):
    return run_registered_candidate_batch(
        candidates,
        registry=registry,
        search_id=search_id,
        search_kind="CURATED_CANDIDATE_BATCH",
        search_source="tests.intelligence",
        dataset_version="dataset-v1",
        recorded_at=recorded_at,
        **kwargs,
    )


# --- refusal writes nothing -----------------------------------------------------


def test_exhausted_trial_budget_refuses_before_any_durable_write(monkeypatch):
    registry = _registry()
    _stub_agent_run(monkeypatch)
    with pytest.raises(BudgetExhausted) as captured:
        _run(
            registry,
            _candidates(3),
            budget=ResearchBudget(max_trials=2),
            parent_hypothesis="h",
            search_space="s",
        )
    check = captured.value.check
    assert check.exhausted is True
    assert any(
        d.dimension == "trials" and d.reason_code == "limit_exceeded"
        for d in check.dimensions
    )
    # nothing entered the durable ledger: no STARTED event, no candidates, no trials
    assert registry.search_attempt_events("budget-batch") == []
    assert registry.search_candidates("budget-batch") == []
    assert registry._store.trial_count() == 0
    assert registry.search_history_valid() is True


def test_unmetered_compute_budget_is_refused_fail_closed(monkeypatch):
    registry = _registry()
    _stub_agent_run(monkeypatch)
    with pytest.raises(BudgetExhausted) as captured:
        _run(
            registry,
            _candidates(1),
            budget=ResearchBudget(max_compute_hours=1.0),
            parent_hypothesis="h",
            search_space="s",
        )
    dim = {d.dimension: d for d in captured.value.check.dimensions}["compute_hours"]
    assert dim.status == "UNVERIFIED"
    assert dim.reason_code == "unverified_dimension"
    assert registry._store.trial_count() == 0


def test_disallowed_data_source_is_refused(monkeypatch):
    registry = _registry()
    _stub_agent_run(monkeypatch)
    with pytest.raises(BudgetExhausted) as captured:
        _run(
            registry,
            _candidates(1),
            budget=ResearchBudget(allowed_data_sources=("crypto_funding.db",)),
            parent_hypothesis="h",
            search_space="s",
            data_source="binance_mainnet",
        )
    dim = {d.dimension: d for d in captured.value.check.dimensions}["data_source"]
    assert dim.reason_code == "data_source_not_allowed"
    assert registry._store.trial_count() == 0


def test_locked_oos_attestation_gates_registration(monkeypatch):
    budget = ResearchBudget(locked_oos_period=(1000, 2000))
    for attestation, expected_code in (
        (None, "unverified_dimension"),
        (True, "locked_oos_breach"),
    ):
        registry = _registry()
        _stub_agent_run(monkeypatch)
        with pytest.raises(BudgetExhausted) as captured:
            _run(
                registry,
                _candidates(1),
                budget=budget,
                parent_hypothesis="h",
                search_space="s",
                uses_locked_oos=attestation,
            )
        dim = {d.dimension: d for d in captured.value.check.dimensions}["locked_oos"]
        assert dim.reason_code == expected_code
        assert registry._store.trial_count() == 0

    registry = _registry()
    _stub_agent_run(monkeypatch)
    result = _run(
        registry,
        _candidates(1),
        budget=budget,
        parent_hypothesis="h",
        search_space="s",
        uses_locked_oos=False,
    )
    assert result["registered_candidate_count"] == 1


def test_feature_and_parameter_dimensions_are_checked_from_pool(monkeypatch):
    registry = _registry()
    _stub_agent_run(monkeypatch)
    with pytest.raises(BudgetExhausted) as captured:
        _run(
            registry,
            _candidates(2, factor_count=3, param_count=1),
            budget=ResearchBudget(max_feature_count=2),
            parent_hypothesis="h",
            search_space="s",
        )
    dim = {d.dimension: d for d in captured.value.check.dimensions}["feature_count"]
    assert dim.usage == 3 and dim.limit == 2
    registry = _registry()
    _stub_agent_run(monkeypatch)
    with pytest.raises(BudgetExhausted) as captured:
        _run(
            registry,
            _candidates(2, factor_count=1, param_count=4),
            budget=ResearchBudget(max_parameter_dimensions=3),
            parent_hypothesis="h",
            search_space="s",
        )
    dim = {d.dimension: d for d in captured.value.check.dimensions}[
        "parameter_dimensions"
    ]
    assert dim.usage == 4 and dim.limit == 3


# --- declaration requirements ---------------------------------------------------


@pytest.mark.parametrize("kwargs", [
    {"parent_hypothesis": "h"},
    {"search_space": "s"},
    {"data_source": "crypto_funding.db"},
    {"uses_locked_oos": False},
])
def test_budget_kwargs_without_budget_are_rejected(kwargs):
    with pytest.raises(ValueError, match="require budget="):
        _run(_registry(), _candidates(1), **kwargs)
    assert _registry()._store.trial_count() == 0


@pytest.mark.parametrize("kwargs", [
    {"parent_hypothesis": None, "search_space": "s"},
    {"parent_hypothesis": "  ", "search_space": "s"},
    {"parent_hypothesis": "h", "search_space": None},
    {"parent_hypothesis": "h", "search_space": ""},
])
def test_budget_requires_hypothesis_and_search_space(kwargs):
    with pytest.raises(ValueError, match="must be a non-empty string"):
        _run(_registry(), _candidates(1), budget=ResearchBudget(max_trials=1), **kwargs)


def test_budget_must_be_a_research_budget():
    with pytest.raises(TypeError, match="ResearchBudget"):
        _run(
            _registry(),
            _candidates(1),
            budget={"max_trials": 1},
            parent_hypothesis="h",
            search_space="s",
        )


# --- metadata recorded in the run artifact ---------------------------------------


def test_budget_declaration_is_recorded_with_derived_usage(monkeypatch):
    registry = _registry()
    _stub_agent_run(monkeypatch)
    budget = ResearchBudget(
        max_trials=3,
        max_feature_count=2,
        max_parameter_dimensions=3,
        allowed_data_sources=("crypto_funding.db",),
        stopping_rules={"max_trials": 3, "no_improvement_trials": 2},
    )
    result = _run(
        registry,
        _candidates(3, factor_count=2, param_count=3),
        budget=budget,
        parent_hypothesis="日频反转含有独立边",
        search_space="3 候选 × 窗 {2,3,5}",
        data_source="crypto_funding.db",
    )
    declaration = result["budget_declaration"]
    assert declaration["parent_hypothesis"] == "日频反转含有独立边"
    assert declaration["search_space"] == "3 候选 × 窗 {2,3,5}"
    assert declaration["data_source"] == "crypto_funding.db"
    assert declaration["uses_locked_oos"] is None
    assert declaration["budget"] == budget.metadata()
    assert declaration["usage"] == {
        "trials_used": 3, "feature_count": 2, "parameter_dimensions": 3,
    }
    assert declaration["check"]["exhausted"] is False
    assert len(declaration["check"]["dimensions"]) == 6
    # the registration itself proceeded exactly as before
    assert result["registered_candidate_count"] == 3
    assert registry._store.trial_count() == 3
    assert [
        row["status"] for row in registry.search_attempt_events("budget-batch")
    ] == ["STARTED", "COMPLETED"]


def test_budget_at_exact_limit_passes(monkeypatch):
    registry = _registry()
    _stub_agent_run(monkeypatch)
    result = _run(
        registry,
        _candidates(2),
        budget=ResearchBudget(max_trials=2),
        parent_hypothesis="h",
        search_space="s",
    )
    assert result["registered_candidate_count"] == 2
    assert result["budget_declaration"]["check"]["exhausted"] is False


# --- absent budget: byte-identical behavior --------------------------------------


def test_absent_budget_leaves_result_shape_unchanged(monkeypatch):
    registry = _registry()
    _stub_agent_run(monkeypatch)
    result = _run(registry, _candidates(2))
    assert "budget_declaration" not in result
    assert result["registered_candidate_count"] == 2
    assert registry._store.trial_count() == 2
    assert [
        row["status"] for row in registry.search_attempt_events("budget-batch")
    ] == ["STARTED", "COMPLETED"]
