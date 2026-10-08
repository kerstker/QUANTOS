"""Pure-core tests for services/research/research_budget.py — W10 §14.2 ResearchBudget.

Covers: contract validation, per-dimension check_budget semantics (OK / UNCONSTRAINED /
EXCEEDED / UNVERIFIED fail-closed), locked-OOS and data-source attestations, stopping
rules over metric histories, and the typed BudgetExhausted carrier. No IO, no registry."""
from __future__ import annotations

import dataclasses

import pytest

from services.research.research_budget import (
    BudgetCheck,
    BudgetExhausted,
    ResearchBudget,
    check_budget,
    evaluate_stopping_rules,
)


def _dim(check: BudgetCheck, name: str):
    matches = [d for d in check.dimensions if d.dimension == name]
    assert len(matches) == 1
    return matches[0]


# --- ResearchBudget validation ------------------------------------------------


def test_default_budget_is_fully_unconstrained():
    budget = ResearchBudget()
    check = check_budget(budget, {})
    assert check.exhausted is False
    assert check.reasons == ()
    assert {d.status for d in check.dimensions} == {"UNCONSTRAINED"}
    assert [d.dimension for d in check.dimensions] == [
        "trials", "compute_hours", "feature_count", "parameter_dimensions",
        "locked_oos", "data_source",
    ]


def test_budget_is_frozen():
    budget = ResearchBudget(max_trials=8)
    with pytest.raises(dataclasses.FrozenInstanceError):
        budget.max_trials = 9


@pytest.mark.parametrize("field,value", [
    ("max_trials", True), ("max_trials", 0), ("max_trials", -1), ("max_trials", 8.5),
    ("max_compute_hours", True), ("max_compute_hours", 0.0),
    ("max_compute_hours", -1.0), ("max_compute_hours", float("inf")),
    ("max_compute_hours", float("nan")), ("max_compute_hours", "4"),
    ("max_feature_count", 0), ("max_feature_count", -2),
    ("max_parameter_dimensions", 0), ("max_parameter_dimensions", 1.5),
])
def test_budget_rejects_invalid_numeric_limits(field, value):
    with pytest.raises((TypeError, ValueError)):
        ResearchBudget(**{field: value})


def test_budget_accepts_int_compute_hours_as_number():
    assert ResearchBudget(max_compute_hours=4).max_compute_hours == 4.0


@pytest.mark.parametrize("period", [
    (5, 1),           # start > end
    (-1, 5),          # negative
    (True, 5),        # bool
    (1.5, 5),         # non-int
    (1,),             # wrong arity
    "2025",           # not a pair
])
def test_budget_rejects_invalid_locked_oos_period(period):
    with pytest.raises((TypeError, ValueError)):
        ResearchBudget(locked_oos_period=period)


def test_budget_normalizes_locked_oos_period_to_tuple():
    budget = ResearchBudget(locked_oos_period=[10, 20])
    assert budget.locked_oos_period == (10, 20)


@pytest.mark.parametrize("sources", [
    "crypto.db",                    # bare string is not a whitelist
    (),                             # empty whitelist
    ("crypto.db", ""),              # blank entry
    ("crypto.db", "crypto.db"),     # duplicate
    ("crypto.db", 3),               # non-string
])
def test_budget_rejects_invalid_allowed_data_sources(sources):
    with pytest.raises((TypeError, ValueError)):
        ResearchBudget(allowed_data_sources=sources)


def test_budget_normalizes_allowed_data_sources_to_tuple():
    budget = ResearchBudget(allowed_data_sources=["b.db", "a.db"])
    assert budget.allowed_data_sources == ("b.db", "a.db")


@pytest.mark.parametrize("rules", [
    {"unknown_rule": 3},
    {"max_trials": 0},
    {"max_trials": -1},
    {"no_improvement_trials": True},
    {"no_improvement_trials": 2.5},
    {},
    [("max_trials", 3)],
])
def test_budget_rejects_invalid_stopping_rules(rules):
    with pytest.raises((TypeError, ValueError)):
        ResearchBudget(stopping_rules=rules)


def test_budget_metadata_is_json_safe_and_preserves_none():
    budget = ResearchBudget(
        max_trials=8, max_feature_count=1, locked_oos_period=(100, 200),
        allowed_data_sources=("crypto_funding.db",),
        stopping_rules={"max_trials": 8, "no_improvement_trials": 4},
    )
    assert budget.metadata() == {
        "max_trials": 8,
        "max_compute_hours": None,
        "max_feature_count": 1,
        "max_parameter_dimensions": None,
        "locked_oos_period": [100, 200],
        "allowed_data_sources": ["crypto_funding.db"],
        "stopping_rules": {"max_trials": 8, "no_improvement_trials": 4},
    }


# --- check_budget per-dimension semantics --------------------------------------


def test_numeric_dimensions_ok_at_or_under_limit():
    budget = ResearchBudget(max_trials=8, max_feature_count=2, max_parameter_dimensions=5)
    check = check_budget(
        budget, {"trials_used": 8, "feature_count": 1, "parameter_dimensions": 5})
    assert check.exhausted is False
    for name in ("trials", "feature_count", "parameter_dimensions"):
        assert _dim(check, name).status == "OK"
    assert _dim(check, "compute_hours").status == "UNCONSTRAINED"


def test_numeric_dimension_exceeded_is_reported_with_reason():
    budget = ResearchBudget(max_trials=8)
    check = check_budget(budget, {"trials_used": 9})
    dim = _dim(check, "trials")
    assert check.exhausted is True
    assert dim.status == "EXCEEDED"
    assert dim.reason_code == "limit_exceeded"
    assert "trials" in dim.reason and "9" in dim.reason and "8" in dim.reason
    assert check.reasons == (dim.reason,)


@pytest.mark.parametrize("key,field", [
    ("trials_used", "max_trials"),
    ("compute_hours_used", "max_compute_hours"),
    ("feature_count", "max_feature_count"),
    ("parameter_dimensions", "max_parameter_dimensions"),
])
def test_missing_usage_with_declared_limit_is_unverified_fail_closed(key, field):
    budget = ResearchBudget(**{field: 4})
    check = check_budget(budget, {})          # usage metric absent entirely
    name = key.removesuffix("_used") if key.endswith("_used") else key
    dim = _dim(check, name)
    assert check.exhausted is True
    assert dim.status == "UNVERIFIED"
    assert dim.reason_code == "unverified_dimension"
    assert dim.usage is None and dim.limit == 4


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), float("-inf")])
def test_non_finite_or_none_usage_is_unverified_never_compared(bad):
    budget = ResearchBudget(max_trials=8)
    check = check_budget(budget, {"trials_used": bad})
    dim = _dim(check, "trials")
    assert dim.status == "UNVERIFIED" and check.exhausted is True


def test_usage_validation_rejects_bad_shapes():
    budget = ResearchBudget(max_trials=8)
    with pytest.raises(ValueError, match="unknown usage keys"):
        check_budget(budget, {"trial_used": 1})          # typo must not silently unverify
    with pytest.raises(TypeError):
        check_budget(budget, {"trials_used": True})
    with pytest.raises(TypeError):
        check_budget(budget, {"trials_used": "8"})
    with pytest.raises(ValueError):
        check_budget(budget, {"trials_used": -1})
    with pytest.raises(TypeError):
        check_budget(budget, [("trials_used", 1)])
    with pytest.raises(TypeError):
        check_budget("not-a-budget", {})


def test_all_dimensions_reported_without_short_circuit():
    budget = ResearchBudget(
        max_trials=1, max_feature_count=1, allowed_data_sources=("a.db",),
        locked_oos_period=(10, 20))
    check = check_budget(
        budget, {"trials_used": 5, "feature_count": 3},
        now_known_oos=True, data_source="b.db")
    assert check.exhausted is True
    assert len(check.reasons) == 4        # trials, feature_count, locked_oos, data_source
    statuses = {d.dimension: d.status for d in check.dimensions}
    assert statuses == {
        "trials": "EXCEEDED", "compute_hours": "UNCONSTRAINED",
        "feature_count": "EXCEEDED", "parameter_dimensions": "UNCONSTRAINED",
        "locked_oos": "EXCEEDED", "data_source": "EXCEEDED",
    }
    assert check.to_dict()["exhausted"] is True


# --- locked OOS attestation -----------------------------------------------------


def test_locked_oos_none_attestation_is_fail_closed():
    budget = ResearchBudget(locked_oos_period=(100, 200))
    check = check_budget(budget, {}, now_known_oos=None)
    dim = _dim(check, "locked_oos")
    assert dim.status == "UNVERIFIED" and check.exhausted is True
    assert dim.reason_code == "unverified_dimension"


def test_locked_oos_true_attestation_is_breach():
    budget = ResearchBudget(locked_oos_period=(100, 200))
    check = check_budget(budget, {}, now_known_oos=True)
    dim = _dim(check, "locked_oos")
    assert dim.status == "EXCEEDED" and dim.reason_code == "locked_oos_breach"
    assert check.exhausted is True


def test_locked_oos_false_attestation_passes():
    budget = ResearchBudget(locked_oos_period=(100, 200))
    check = check_budget(budget, {}, now_known_oos=False)
    assert _dim(check, "locked_oos").status == "OK"
    assert check.exhausted is False


def test_locked_oos_unconstrained_ignores_attestation():
    check = check_budget(ResearchBudget(), {}, now_known_oos=True)
    assert _dim(check, "locked_oos").status == "UNCONSTRAINED"
    assert check.exhausted is False


def test_now_known_oos_must_be_bool_or_none():
    with pytest.raises(TypeError):
        check_budget(ResearchBudget(), {}, now_known_oos="yes")


# --- data source attestation ----------------------------------------------------


def test_data_source_member_passes_and_stranger_is_refused():
    budget = ResearchBudget(allowed_data_sources=("crypto_funding.db", "fred"))
    ok = check_budget(budget, {}, data_source="fred")
    assert _dim(ok, "data_source").status == "OK" and ok.exhausted is False
    bad = check_budget(budget, {}, data_source="binance_mainnet")
    dim = _dim(bad, "data_source")
    assert dim.status == "EXCEEDED"
    assert dim.reason_code == "data_source_not_allowed"
    assert bad.exhausted is True


def test_data_source_missing_attestation_is_fail_closed():
    budget = ResearchBudget(allowed_data_sources=("crypto_funding.db",))
    check = check_budget(budget, {}, data_source=None)
    dim = _dim(check, "data_source")
    assert dim.status == "UNVERIFIED" and check.exhausted is True


def test_data_source_must_be_nonempty_string_or_none():
    with pytest.raises(ValueError):
        check_budget(ResearchBudget(), {}, data_source="   ")
    with pytest.raises(ValueError):
        check_budget(ResearchBudget(), {}, data_source=3)


# --- stopping rules -------------------------------------------------------------


def test_no_stopping_rules_declared_never_stops():
    report = evaluate_stopping_rules(None, [0.1, 0.2])
    assert report.stop is False and report.rules == ()
    assert report.to_dict() == {"stop": False, "rules": []}


def test_max_trials_rule_triggers_at_boundary():
    rules = {"max_trials": 3}
    assert evaluate_stopping_rules(rules, [0.1, 0.2]).stop is False
    report = evaluate_stopping_rules(rules, [0.1, 0.2, 0.3])
    assert report.stop is True
    assert report.rules[0].rule == "max_trials" and report.rules[0].triggered is True
    assert "3" in report.rules[0].detail


def test_no_improvement_rule_counts_trials_since_last_new_best():
    rules = {"no_improvement_trials": 3}
    # best improves at index 1; indices 2,3,4 are three non-improving trials -> trigger
    assert evaluate_stopping_rules(rules, [0.1, 0.5, 0.2, 0.3, 0.4]).stop is True
    # only two trials since the last improvement -> no trigger
    assert evaluate_stopping_rules(rules, [0.1, 0.5, 0.2, 0.3]).stop is False
    # a new best resets the clock
    assert evaluate_stopping_rules(rules, [0.1, 0.5, 0.2, 0.6]).stop is False


def test_no_improvement_rule_edge_cases():
    rules = {"no_improvement_trials": 1}
    assert evaluate_stopping_rules(rules, []).stop is False          # empty history
    assert evaluate_stopping_rules(rules, [0.1]).stop is False       # first trial improves
    assert evaluate_stopping_rules(rules, [0.1, 0.1]).stop is True   # strict improvement only


def test_stopping_rules_all_evaluated_no_short_circuit():
    rules = {"max_trials": 2, "no_improvement_trials": 2}
    report = evaluate_stopping_rules(rules, [0.3, 0.1, 0.2])
    assert report.stop is True
    assert [r.rule for r in report.rules] == ["max_trials", "no_improvement_trials"]
    assert all(r.triggered for r in report.rules)


def test_stopping_rules_reject_bad_inputs():
    with pytest.raises(ValueError, match="unsupported stopping rule"):
        evaluate_stopping_rules({"patience": 3}, [0.1])
    with pytest.raises(ValueError, match="finite"):
        evaluate_stopping_rules({"max_trials": 5}, [0.1, float("nan")])
    with pytest.raises(TypeError):
        evaluate_stopping_rules({"max_trials": 5}, [0.1, True])


# --- BudgetExhausted carrier ----------------------------------------------------


def test_budget_exhausted_carries_check_and_readable_message():
    budget = ResearchBudget(max_trials=1)
    check = check_budget(budget, {"trials_used": 2})
    exc = BudgetExhausted(check)
    assert exc.check is check
    assert "trials" in str(exc) and "limit_exceeded" in str(exc)
    with pytest.raises(TypeError):
        BudgetExhausted("not-a-check")
