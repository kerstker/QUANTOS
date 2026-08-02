"""Driver-level tests for tools/cross_venue_research.py — pre-registration invariants,
verdict logic (the 3 absolute bars), xvenue coverage pure function, and the W10 budget
path exercised end-to-end at registration time on a tiny synthetic family (in-memory
registry; ResearchAgentLoop.run stubbed — registration itself runs for real).

The expensive real pipeline (24-coin panel, dry-run) is deliberately NOT re-run for
this contract; it runs once via the driver and lands in evidence/cross_venue_research.json."""
from __future__ import annotations

import pytest

from services.intelligence import registered_candidate_batch as batch_module
from services.research.alpha_expression import FieldPanel, evaluate
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.research_budget import BudgetExhausted, ResearchBudget
from tools import cross_venue_research as xvr

_DAY = 86_400_000


def _tiny_panel(n_days=30, n_coins=3):
    closes = [
        [100.0 + 10 * i + (t * (0.5 if (i + t) % 3 else -0.4)) for i in range(n_coins)]
        for t in range(n_days)
    ]
    xcloses = [[c * (1.0 + 0.001 * ((i + t) % 2)) for i, c in enumerate(row)]
               for t, row in enumerate(closes)]
    funding = [[0.0001 * ((i + t) % 3 - 1) for i in range(n_coins)] for t in range(n_days)]
    xfunding = [[0.0001 * ((i - t) % 3 - 1) for i in range(n_coins)] for t in range(n_days)]
    return FieldPanel(tuple(f"C{i}" for i in range(n_coins)),
                      tuple((t + 1) * _DAY for t in range(n_days)),
                      {"close": closes, "xclose": xcloses,
                       "funding": funding, "xfunding": xfunding})


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


# --- pre-registration invariants -------------------------------------------------


def test_preregistered_family_is_six_unique_specs_with_rationales():
    specs = xvr.build_variant_specs()
    assert len(specs) == 6 == xvr.PRE_REGISTRATION["trial_count_planned"]
    assert len({s["id"] for s in specs}) == 6
    for spec in specs:
        assert spec["rationale"].strip()
        assert spec["quantile"] == 0.30 and spec["cadence_days"] == 7
        assert set(spec["fields"]) <= {"close", "xclose", "funding", "xfunding"}
    # 正反两方向都预注册(杜绝事后挑方向)
    ids = {s["id"] for s in specs}
    assert {"xv_basis_rev", "xv_basis_tilt"} <= ids


def test_all_expressions_evaluate_on_a_panel_with_xvenue_fields():
    panel = _tiny_panel()
    for spec in xvr.build_variant_specs():
        sig = evaluate(spec["expr"], panel)
        assert len(sig) == 30 and len(sig[0]) == 3


def test_verdict_bar_matches_the_preregistered_mission_bar():
    assert xvr.VERDICT_BAR == {
        "min_t_stat": 2.5,
        "t_stat_horizon": "weekly_non_overlapping_h7",
        "min_falsification_passes": 4,
        "min_vt_sharpe": 1.0,
        "max_drawdown_vt": 0.20,
    }
    assert xvr.PRE_REGISTRATION["verdict_bar"] == xvr.VERDICT_BAR


# --- verdict logic(3 条绝对门槛,fail-closed on None)-------------------------------


def _passing():
    return {"t_stat": 3.0, "falsification_passes": 4, "vt_sharpe": 1.2, "max_drawdown_vt": 0.15}


def test_verdict_candidate_only_when_all_three_bars_pass():
    assert xvr.verdict_for(_passing()) == {"verdict": "CANDIDATE", "failed_bars": []}


@pytest.mark.parametrize("mut,failed", [
    ({"t_stat": 2.49}, ["t_stat"]),
    ({"falsification_passes": 3}, ["falsification"]),
    ({"vt_sharpe": 0.99}, ["vt_sharpe"]),
    ({"max_drawdown_vt": 0.21}, ["max_drawdown"]),
    ({"t_stat": None}, ["t_stat"]),
    ({"vt_sharpe": None}, ["vt_sharpe"]),
])
def test_verdict_rejects_on_any_failed_bar(mut, failed):
    stages = _passing()
    stages.update(mut)
    out = xvr.verdict_for(stages)
    assert out["verdict"] == "REJECTED"
    assert out["failed_bars"] == failed


def test_verdict_boundary_values_are_inclusive():
    stages = {"t_stat": 2.5, "falsification_passes": 4, "vt_sharpe": 1.0, "max_drawdown_vt": 0.20}
    assert xvr.verdict_for(stages)["verdict"] == "CANDIDATE"


# --- xvenue_coverage(纯函数)-------------------------------------------------------


def test_xvenue_coverage_counts_both_present_cells():
    closes = [[100.0, 200.0], [101.0, None]]
    xcloses = [[100.5, None], [None, 201.0]]
    panel = FieldPanel(("A", "B"), (_DAY, 2 * _DAY),
                       {"close": closes, "xclose": xcloses})
    cov = xvr.xvenue_coverage(panel)
    # 细胞:(A,t0) both,(B,t0) close-only,(A,t1) close-only,(B,t1) xclose-only
    assert cov["cells_both_present"] == 1
    assert cov["cells_either_present"] == 4
    assert cov["coverage_ratio"] == 0.25
    assert set(cov["xclose_first_date_by_instrument"]) == {"A", "B"}


# --- declared budget is pinned to the real family --------------------------------


def test_declared_budget_matches_the_preregistered_family():
    specs = xvr.build_variant_specs()
    assert xvr.RESEARCH_BUDGET.max_trials == len(specs) == 6
    definitions = [xvr._definition_for(s) for s in specs]
    assert max(len(d.factor_ids) for d in definitions) == 2
    assert xvr.RESEARCH_BUDGET.max_feature_count == 2
    assert max(len(d.params) for d in definitions) == 5
    assert xvr.RESEARCH_BUDGET.max_parameter_dimensions == 5
    assert xvr.RESEARCH_BUDGET.allowed_data_sources == ("crypto_funding.db+bybit_perp.db",)
    # compute hours deliberately undeclared (unmetered at registration)
    assert xvr.RESEARCH_BUDGET.max_compute_hours is None
    assert xvr.RESEARCH_BUDGET.locked_oos_period is None
    assert xvr.PARENT_HYPOTHESIS.strip() and xvr.SEARCH_SPACE.strip()


# --- budget path on a tiny synthetic family ---------------------------------------


def test_register_family_trials_records_budget_declaration(monkeypatch):
    _stub_agent_run(monkeypatch)
    registry = _registry()
    panel = _tiny_panel()
    specs = xvr.build_variant_specs()[:2]          # tiny synthetic family of 2
    signals = {spec["id"]: evaluate(spec["expr"], panel) for spec in specs}

    out = xvr.register_family_trials(specs, signals, panel, registry=registry,
                                     recorded_at=1_000)

    assert out["trial_count_before"] == 0
    assert out["trial_count_after"] == 2
    declaration = out["budget_declaration"]
    assert declaration["budget"] == xvr.RESEARCH_BUDGET.metadata()
    assert declaration["parent_hypothesis"] == xvr.PARENT_HYPOTHESIS
    assert declaration["search_space"] == xvr.SEARCH_SPACE
    assert declaration["data_source"] == "crypto_funding.db+bybit_perp.db"
    assert declaration["usage"] == {
        "trials_used": 2, "feature_count": 2, "parameter_dimensions": 5,
    }
    assert declaration["check"]["exhausted"] is False
    statuses = {d["dimension"]: d["status"] for d in declaration["check"]["dimensions"]}
    assert statuses["trials"] == "OK"
    assert statuses["feature_count"] == "OK"
    assert statuses["data_source"] == "OK"
    assert statuses["locked_oos"] == "UNCONSTRAINED"
    assert [
        row["status"] for row in registry.search_attempt_events(out["search_id"])
    ] == ["STARTED", "COMPLETED"]


def test_register_family_trials_refuses_when_family_exceeds_budget(monkeypatch):
    _stub_agent_run(monkeypatch)
    registry = _registry()
    panel = _tiny_panel()
    specs = xvr.build_variant_specs()[:2]
    signals = {spec["id"]: evaluate(spec["expr"], panel) for spec in specs}
    monkeypatch.setattr(xvr, "RESEARCH_BUDGET", ResearchBudget(max_trials=1))

    with pytest.raises(BudgetExhausted) as captured:
        xvr.register_family_trials(specs, signals, panel, registry=registry,
                                   recorded_at=2_000)

    dim = {d.dimension: d for d in captured.value.check.dimensions}["trials"]
    assert dim.usage == 2 and dim.limit == 1
    # refusal is pre-write: the durable ledger stays empty
    assert registry._store.trial_count() == 0
    assert registry.search_history_valid() is True
