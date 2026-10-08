"""Actual order turnover supplements target exposure without skipping risk."""
from dataclasses import replace
from decimal import Decimal as D, ROUND_DOWN, localcontext

import pytest

from services.risk.research_risk.domain.gate import RiskApprovalGate
from services.risk.research_risk.domain.models import RiskRun
from services.risk.research_risk.dry_run_guard import evaluate_dry_run_risk
from services.validation.research_risk_authority import full_research_risk_approved
from tests.execution import test_authoritative_perpetual_backtest as original


def flow(current="0.1", target="0", price="100", multiplier="1", equity="1000"):
    from quantos.contracts.research_capacity_flow import build_capacity_flow

    return build_capacity_flow(
        current_quantities={"BTCUSDT": D(current)},
        target_quantities={"BTCUSDT": D(target)},
        valuation_prices={"BTCUSDT": D(price)},
        contract_multipliers={"BTCUSDT": D(multiplier)},
        risk_equity=D(equity),
    )


@pytest.mark.parametrize("current,target,order,notional", [
    ("0", "0.1", "0.1", "10"),
    ("0.1", "0", "-0.1", "10"),
    ("0.1", "0.05", "-0.05", "10"),
    ("0.1", "-0.1", "-0.2", "20"),
    ("-0.1", "0.1", "0.2", "20"),
])
def test_order_and_both_books_determine_conservative_notional(current, target, order, notional):
    value = flow(current, target)
    assert value["order_quantities"] == {"BTCUSDT": order}
    assert value["capacity_notionals"] == {"BTCUSDT": notional}
    assert D(value["capacity_flow_weights"]["BTCUSDT"]) == D(notional) / D("1000")
    assert value["current_quantities"] == {"BTCUSDT": current}
    assert value["target_quantities"] == {"BTCUSDT": target}


def test_flow_uses_contract_quantity_multiplier_and_same_risk_equity():
    value = flow("2", "-3", "11", "0.01", "7")
    assert value["capacity_notionals"] == {"BTCUSDT": "0.55"}
    assert D(value["capacity_flow_weights"]["BTCUSDT"]) * D("7") == pytest.approx(D("0.55"))


@pytest.mark.parametrize("precision", [6, 28, 80])
def test_receipt_and_hash_are_independent_of_process_decimal_context(precision):
    baseline = flow("2", "-3", "11", "0.01", "7")
    with localcontext() as external:
        external.prec = precision
        external.rounding = ROUND_DOWN
        assert flow("2", "-3", "11", "0.01", "7") == baseline
        assert external.prec == precision and external.rounding == ROUND_DOWN


def evaluate(weights, capacity_flow_weights=None, *, evidence=None, require_full=True):
    evidence = evidence or original._risk()
    return evaluate_dry_run_risk(
        list(evidence.returns), weights, list(evidence.equity_curve),
        timestamps=list(evidence.timestamps), config=evidence.config,
        full_inputs=evidence.full_inputs, require_full_inputs=require_full,
        capacity_flow_weights=capacity_flow_weights,
    )


def rebind_full_inputs(evidence, full):
    sources = dict(evidence.source_records)
    values = dict(alpha_metadata=full.alpha_metadata, capacity_and_aum=dict(
        capacity_cny=dict(full.capacity_cny), daily_volume_cny=full.daily_volume_cny, total_aum=full.total_aum))
    for name, value in values.items():
        sources[name] = replace(sources[name], value=value)
    return replace(evidence, full_inputs=full, source_records=sources)


def test_known_close_runs_and_passes_all_nine_with_nonzero_actual_flow():
    evidence = original._risk()
    evidence = rebind_full_inputs(evidence, replace(evidence.full_inputs,
        capacity_cny={"BTCUSDT": 20.0}, daily_volume_cny=200.0))
    result = evaluate({"BTCUSDT": 0.0}, {"BTCUSDT": 0.01}, evidence=evidence)
    assert full_research_risk_approved(result)
    assert result["gates_run"] == result["gates_passed"] == 9
    assert result["reports"]["concentration"]["hhi"] == 0.0
    assert result["reports"]["liquidity"]["avg_daily_volume_usage"] > 0
    assert result["reports"]["capacity"]["capacity_usage"] > 0


def test_zero_target_without_order_evidence_keeps_original_rejection():
    result = evaluate({"BTCUSDT": 0.0})
    assert not full_research_risk_approved(result)
    assert result["engines_run"]["liquidity"] is result["engines_run"]["capacity"] is False


def test_zero_current_target_and_order_do_not_fabricate_capacity():
    receipt = flow("0", "0")
    assert receipt["order_quantities"] == receipt["capacity_notionals"] == {"BTCUSDT": "0"}
    result = evaluate({"BTCUSDT": 0.0}, {"BTCUSDT": 0.0})
    assert result["engines_run"]["liquidity"] is result["engines_run"]["capacity"] is False


@pytest.mark.parametrize("damaged", [
    {}, {"other": 0.02}, {"BTCUSDT": 0.02, "other": 0.01},
    {"BTCUSDT": -0.02}, {"BTCUSDT": 0.001}, {"BTCUSDT": True},
    {"BTCUSDT": "0.02"}, {"BTCUSDT": float("nan")}, {"BTCUSDT": float("inf")},
])
def test_invalid_or_understated_flow_cannot_fall_back_to_target(damaged):
    result = evaluate({"BTCUSDT": -0.01}, damaged)
    assert result["gates_run"] == 9
    assert not full_research_risk_approved(result)
    assert result["engines_run"]["liquidity"] is result["engines_run"]["capacity"] is False


def test_flow_only_changes_liquidity_and_capacity_reports_and_remains_load_bearing():
    evidence = original._risk()
    kwargs = dict(returns=list(evidence.returns), weights={"BTCUSDT": -0.01},
        equity_curve=list(evidence.equity_curve), capacity_cny={"BTCUSDT": 20.0},
        alpha_returns=dict(evidence.full_inputs.alpha_returns),
        regime_labels=list(evidence.full_inputs.regime_labels),
        alpha_metadata=list(evidence.full_inputs.alpha_metadata), daily_volume_cny=200.0,
        total_aum=1000.0, timestamps=list(evidence.timestamps))
    before, after = RiskRun(portfolio_id="original"), RiskRun(portfolio_id="original")
    gate = RiskApprovalGate(evidence.config)
    gate.evaluate(run=before, **kwargs)
    gate.evaluate(run=after, capacity_flow_weights={"BTCUSDT": 0.02}, **kwargs)
    assert before.gate_results["capacity"] is True
    assert after.gate_results["capacity"] is False
    assert after.gate_reports["capacity"].capacity_usage == pytest.approx(1.0)
    assert after.gate_reports["liquidity"].avg_daily_volume_usage == pytest.approx(0.1)
    for name in before.gate_reports.keys() - {"capacity", "liquidity"}:
        assert before.gate_reports[name] == after.gate_reports[name], name


def test_close_does_not_skip_failed_model_risk():
    evidence = original._risk()
    damaged = [dict(item, leakage_flag=True) for item in evidence.full_inputs.alpha_metadata]
    evidence = rebind_full_inputs(evidence, replace(evidence.full_inputs, alpha_metadata=damaged))
    result = evaluate({"BTCUSDT": 0.0}, {"BTCUSDT": 0.01}, evidence=evidence)
    assert result["gates_run"] == 9 and result["engines_run"]["model_alpha"] is False
    assert not full_research_risk_approved(result)


def test_flow_never_creates_subset_approval_or_missing_full_evidence():
    result = evaluate_dry_run_risk([0.001], {"BTCUSDT": 0.0}, [1000.0],
        capacity_flow_weights={"BTCUSDT": 0.01})
    assert result["status"] == "PENDING" and result["gates_run"] == 0
    assert result["provenance"] == "REAL_RESEARCH_RISK_FULL_PENDING"


@pytest.mark.parametrize("field,value", [
    ("current_quantities", {"wrong": D("0.1")}),
    ("target_quantities", {}), ("valuation_prices", {"BTCUSDT": D("0")}),
    ("contract_multipliers", {"BTCUSDT": D("-1")}),
    ("risk_equity", D("0")), ("risk_equity", D("NaN")),
    ("current_quantities", {"BTCUSDT": 0.1}),
])
def test_builder_rejects_missing_mismatched_or_invalid_original_values(field, value):
    from quantos.contracts.research_capacity_flow import build_capacity_flow

    values = dict(current_quantities={"BTCUSDT": D("0.1")},
        target_quantities={"BTCUSDT": D("0")}, valuation_prices={"BTCUSDT": D("100")},
        contract_multipliers={"BTCUSDT": D("1")}, risk_equity=D("1000"))
    values[field] = value
    with pytest.raises((TypeError, ValueError)):
        build_capacity_flow(**values)
