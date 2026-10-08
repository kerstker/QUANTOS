"""Full Gold risk policy is predeclared, unit-bound, and never authority."""

import copy
from dataclasses import fields
from decimal import Decimal as D

import pytest

from quantos.contracts.decimal_arithmetic import canonical_decimal
from services.risk.research_risk.domain.models import RiskConfig
from tests.contracts.test_gold_forward_evaluation_design import declaration, START, INTERVAL
from tests.validation.test_gold_account_risk_inputs import case, inputs


def policy(*, start=START, end=START + 10000, frequency=10000, declared=None):
    from quantos.contracts import gold_research_risk_policy as contract

    values = {
        field.name: (
            getattr(RiskConfig(), field.name)
            if field.name in contract.INTEGER_FIELDS
            else canonical_decimal(D(str(getattr(RiskConfig(), field.name))))
        )
        for field in fields(RiskConfig)
    }
    values["max_drawdown"] = "0.05"
    values["periods_per_year"] = canonical_decimal(D(365 * 86400000) / frequency)
    return dict(
        schema=contract.SCHEMA,
        declared_at_ms=start - INTERVAL - 180000 if declared is None else declared,
        sample=dict(start_ms=start, end_ms=end, frequency_ms=frequency),
        currency="USDT",
        config=values,
        input_contract=copy.deepcopy(contract.INPUT_CONTRACT),
        independent_verification="REQUIRED_NOT_ESTABLISHED",
        economic_authority=False,
        submission_enabled=False,
    )


def test_every_original_risk_config_field_is_explicit_and_same_unit():
    from quantos.contracts import gold_research_risk_policy as contract

    value = policy()
    assert contract.validate(value) == value
    assert set(value["config"]) == {field.name for field in fields(RiskConfig)}
    assert value["config"]["periods_per_year"] == "3153600"
    assert value["currency"] == "USDT"
    assert value["input_contract"]["alpha_allocation"] == "BOUND_NET_ACCOUNT_ALPHA_ATTRIBUTION_REQUIRED"
    assert value["economic_authority"] is value["submission_enabled"] is False


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "extra",
        "nan",
        "boolean",
        "frequency",
        "unit",
        "late",
        "looser_drawdown",
        "looser_leverage",
        "looser_confidence",
        "altered_method",
        "authority",
        "duration_float",
    ],
)
def test_incomplete_late_unit_ambiguous_or_weakened_risk_policies_reject(damage):
    from quantos.contracts import gold_research_risk_policy as contract

    value = policy()
    if damage == "missing":
        del value["config"]["max_cvar_95"]
    elif damage == "extra":
        value["config"]["ignored"] = "1"
    elif damage == "nan":
        value["config"]["max_var_95"] = "NaN"
    elif damage == "boolean":
        value["config"]["max_drawdown"] = True
    elif damage == "frequency":
        value["sample"]["frequency_ms"] = 20000
    elif damage == "unit":
        value["currency"] = "CNY"
    elif damage == "late":
        value["declared_at_ms"] = value["sample"]["start_ms"]
    elif damage == "looser_drawdown":
        value["config"]["max_drawdown"] = "0.25"
    elif damage == "looser_leverage":
        value["config"]["max_leverage"] = "3"
    elif damage == "looser_confidence":
        value["config"]["min_paper_confidence"] = "0"
    elif damage == "altered_method":
        value["input_contract"]["alpha_allocation"] = "TARGET_QUANTITY"
    elif damage == "duration_float":
        value["config"]["max_drawdown_duration"] = "60"
    else:
        value["submission_enabled"] = True
    with pytest.raises(ValueError, match="GOLD_RESEARCH_RISK_POLICY"):
        contract.validate(value)


def test_full_policy_changes_future_declaration_identity_without_relabeling_legacy():
    from quantos.contracts import gold_forward_evaluation_design as design
    from quantos.contracts.registered_research_run import digest

    original = declaration(long_window=True)
    bound = declaration(long_window=True, research_risk_policy=policy())
    assert original["schema"] == design.LONG_REVIEW_SCHEMA
    assert bound["schema"] == design.RISK_REVIEW_SCHEMA
    assert bound["risk_policy"]["research_risk_policy"] == policy()
    assert digest(original) != digest(bound)
    assert design.validate(bound) == bound
    with pytest.raises(ValueError):
        design.validate(dict(bound, schema=design.LONG_REVIEW_SCHEMA))
    del bound["risk_policy"]["research_risk_policy"]
    with pytest.raises(ValueError):
        design.validate(bound)


def test_declaration_checks_policy_time_sample_and_no_legacy_mode():
    for change in ("time", "sample"):
        value = policy()
        if change == "time":
            value["declared_at_ms"] += 1
        else:
            value["sample"]["end_ms"] += 10000
        with pytest.raises(ValueError):
            declaration(long_window=True, research_risk_policy=value)
    with pytest.raises(ValueError):
        declaration(legacy=True, research_risk_policy=policy())


def test_account_risk_exports_registered_policy_but_keeps_five_external_groups_missing():
    from quantos.contracts import gold_research_risk_policy as contract
    from tests.research.test_gold_v2_strategy import T

    risk_policy = policy(start=T, end=T + 40, frequency=10)
    simulation = case()
    original = copy.deepcopy((simulation, risk_policy))
    value = inputs(simulation, research_risk_policy=risk_policy)
    projected = value.projection
    assert projected["schema"] == "QUANTOS_GOLD_ACCOUNT_RISK_INPUTS_V3"
    assert projected["source_groups"]["risk_config"]["status"] == "AVAILABLE"
    assert projected["risk_policy"]["policy_sha256"] == contract.policy_sha256(risk_policy)
    assert set(projected["missing_groups"]) == {
        "weights",
        "alpha_returns",
        "regime_labels",
        "alpha_metadata",
        "capacity_and_aum",
    }
    assert projected["full_research_risk_ready"] is False
    assert projected["economic_authority"] is projected["submission_enabled"] is False
    assert (simulation, risk_policy) == original


def test_policy_cannot_be_applied_to_another_account_sample_or_changed_cadence():
    from tests.research.test_gold_v2_strategy import T

    value = policy(start=T, end=T + 40, frequency=10)
    with pytest.raises(ValueError):
        inputs(case(), frequency=20, research_risk_policy=value)


def test_future_collection_plan_persists_complete_policy_before_public_input(tmp_path, monkeypatch):
    from tests.tools.test_gold_forward_collection import prepare

    coordinator, plan = prepare(tmp_path / "future", monkeypatch, window_bars=1, research_risk_policy=policy())
    assert plan["forward_design"]["risk_policy"]["research_risk_policy"] == policy()
    assert coordinator.read_plan(tmp_path / "future") == plan
    assert {p.name for p in (tmp_path / "future").iterdir()} == {"plan.json"}


def test_risk_schema_retains_original_long_computation_budget():
    from quantos.contracts import gold_forward_evaluation_design as design
    from tests.contracts.test_gold_forward_evaluation_design import SOURCE, CONFIG

    value = policy(end=START + 2880 * INTERVAL + 10000)
    bound = design.declare(
        program_id=1,
        attempt=1,
        source_identity=SOURCE,
        configuration=CONFIG,
        first_close_ms=START,
        window_bars=2881,
        declared_at_ms=value["declared_at_ms"],
        long_window=True,
        selected_candidate="channel",
        proposal_sha256="a" * 64,
        research_risk_policy=value,
    )
    assert len(bound["window"]["closed_bars"]) == 2881
    assert (
        bound["statistical_policy"]["computation_budget"]
        == design.full_statistics(2881, scoped=True, long_window=True)["computation_budget"]
    )


def test_policy_may_be_prepared_before_plan_but_never_after_its_declaration():
    earlier = policy()
    earlier["declared_at_ms"] -= 1000
    assert declaration(long_window=True, research_risk_policy=earlier)["risk_policy"]["research_risk_policy"] == earlier
    later = policy()
    later["declared_at_ms"] += 1
    with pytest.raises(ValueError):
        declaration(long_window=True, research_risk_policy=later)


@pytest.mark.parametrize("precision", [6, 28, 80])
def test_policy_identity_and_annualization_do_not_depend_on_consumer_decimal_context(precision):
    from decimal import localcontext
    from quantos.contracts import gold_research_risk_policy as contract

    value = policy()
    expected = contract.policy_sha256(value)
    with localcontext() as context:
        context.prec = precision
        assert contract.policy_sha256(value) == expected
