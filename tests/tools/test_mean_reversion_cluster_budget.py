"""Driver-level W10 budget tests for tools/mean_reversion_cluster.py — the declared
RESEARCH_BUDGET is pinned to the real pre-registered family, and the budget path is
exercised end-to-end at registration time on a tiny synthetic family (in-memory
registry; ResearchAgentLoop.run stubbed — registration itself runs for real).

Honest note: the REAL 8-trial family already consumed its trials on 2026-07-24 under
the pre-budget regime (registry 2459→2467). The expensive pipeline is deliberately
NOT re-run for this contract; the budget is a forward constraint on any future
family growth/re-run declaration, verified here on synthetic inputs instead."""
from __future__ import annotations

import pytest

from services.intelligence import registered_candidate_batch as batch_module
from services.research.alpha_expression import FieldPanel, evaluate
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.research_budget import BudgetExhausted, ResearchBudget
from tools import mean_reversion_cluster as mrc

_DAY = 86_400_000


def _tiny_panel(n_days=30, n_coins=3):
    closes = [
        [100.0 + 10 * i + (t * (0.5 if (i + t) % 3 else -0.4)) for i in range(n_coins)]
        for t in range(n_days)
    ]
    return FieldPanel(tuple(f"C{i}" for i in range(n_coins)),
                      tuple((t + 1) * _DAY for t in range(n_days)),
                      {"close": closes})


def _registry() -> ExperimentRegistry:
    return ExperimentRegistry(SqliteExperimentStore(":memory:", create=True))


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


# --- declared budget is pinned to the real family --------------------------------


def test_declared_budget_matches_the_preregistered_family():
    specs = mrc.build_variant_specs()
    assert mrc.RESEARCH_BUDGET.max_trials == len(specs) == 8
    definitions = [mrc._definition_for(s) for s in specs]
    assert max(len(d.factor_ids) for d in definitions) == 1
    assert mrc.RESEARCH_BUDGET.max_feature_count == 1
    assert max(len(d.params) for d in definitions) == 5
    assert mrc.RESEARCH_BUDGET.max_parameter_dimensions == 5
    assert mrc.RESEARCH_BUDGET.allowed_data_sources == ("crypto_funding.db",)
    # compute hours deliberately undeclared (unmetered at registration)
    assert mrc.RESEARCH_BUDGET.max_compute_hours is None
    assert mrc.RESEARCH_BUDGET.locked_oos_period is None
    assert mrc.PARENT_HYPOTHESIS.strip() and mrc.SEARCH_SPACE.strip()


# --- budget path on a tiny synthetic family ---------------------------------------


def test_register_family_trials_records_budget_declaration(monkeypatch):
    _stub_agent_run(monkeypatch)
    registry = _registry()
    panel = _tiny_panel()
    specs = mrc.build_variant_specs()[:2]          # tiny synthetic family of 2
    signals = {spec["id"]: evaluate(spec["expr"], panel) for spec in specs}

    out = mrc.register_family_trials(specs, signals, panel, registry=registry,
                                     recorded_at=1_000)

    assert out["trial_count_before"] == 0
    assert out["trial_count_after"] == 2
    assert out["trial_count_delta"] == 2
    declaration = out["budget_declaration"]
    assert declaration["budget"] == mrc.RESEARCH_BUDGET.metadata()
    assert declaration["parent_hypothesis"] == mrc.PARENT_HYPOTHESIS
    assert declaration["search_space"] == mrc.SEARCH_SPACE
    assert declaration["data_source"] == "crypto_funding.db"
    assert declaration["usage"] == {
        "trials_used": 2, "feature_count": 1, "parameter_dimensions": 5,
    }
    assert declaration["check"]["exhausted"] is False
    statuses = {d["dimension"]: d["status"] for d in declaration["check"]["dimensions"]}
    assert statuses["trials"] == "OK"
    assert statuses["feature_count"] == "OK"
    assert statuses["parameter_dimensions"] == "OK"
    assert statuses["data_source"] == "OK"
    assert statuses["locked_oos"] == "UNCONSTRAINED"
    assert statuses["compute_hours"] == "UNCONSTRAINED"
    assert [
        row["status"] for row in registry.search_attempt_events(out["search_id"])
    ] == ["STARTED", "COMPLETED"]


def test_register_family_trials_refuses_when_family_exceeds_budget(monkeypatch):
    _stub_agent_run(monkeypatch)
    registry = _registry()
    panel = _tiny_panel()
    specs = mrc.build_variant_specs()[:2]
    signals = {spec["id"]: evaluate(spec["expr"], panel) for spec in specs}
    monkeypatch.setattr(mrc, "RESEARCH_BUDGET", ResearchBudget(max_trials=1))

    with pytest.raises(BudgetExhausted) as captured:
        mrc.register_family_trials(specs, signals, panel, registry=registry,
                                   recorded_at=2_000)

    dim = {d.dimension: d for d in captured.value.check.dimensions}["trials"]
    assert dim.usage == 2 and dim.limit == 1
    # refusal is pre-write: the durable ledger stays empty
    assert registry._store.trial_count() == 0
    assert registry.search_history_valid() is True
