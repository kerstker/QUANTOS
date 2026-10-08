"""Actual canonical ledger orders supply capacity risk at both causal seams."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from services.execution.authoritative_perpetual_backtest import (
    AuthoritativeBacktestInputError, ExecutableVenueEvent, run_authoritative_linear_perpetual_v2,
)
from services.execution.backtest_authority import is_authoritative_backtest_record
from tests.execution import test_authoritative_perpetual_backtest as original
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments
from tests.validation.test_project_research_statistics import long_arguments


def test_original_thirty_open_and_close_orders_use_nonzero_bound_capacity(technical_arguments):
    result = run_authoritative_linear_perpetual_v2(**long_arguments(technical_arguments))
    body = result["detail"]["result_hash_payload"]
    assert body["ending_state"]["positions"] == {}
    assert body["reconciliation"]["residual"] == "0"
    assert len(body["decision_contexts"]) == len(body["execution_risk_contexts"]) == 30
    for decisions in (body["decision_contexts"], body["execution_risk_contexts"]):
        for index, context in enumerate(decisions):
            flow = context["capacity_flow"]
            assert flow["order_quantities"] == {"BTCUSDT": "0.1" if index % 2 == 0 else "-0.1"}
            assert D(flow["capacity_notionals"]["BTCUSDT"]) > 0
            assert context["research_risk"]["gates_run"] == context["research_risk"]["gates_passed"] == 9
            if index % 2:
                assert flow["target_quantities"] == {"BTCUSDT": "0"}
                assert D(str(context["target_weights"]["BTCUSDT"])) == 0
    assert result["authoritative"] is False and not is_authoritative_backtest_record(result)


def reversal_arguments(technical_arguments, *, adv=None):
    args = long_arguments(technical_arguments)
    first, second = args["decisions"][:2]
    execution = replace(second.execution, quantity_delta=D("-0.2"))
    risk = second.research_risk
    if adv is not None:
        capacity = original._capacity(period_index=second.timestamp // 86_400_000, adv_notional=adv)
        execution = replace(execution, capacity=capacity)
        full = replace(risk.full_inputs, capacity_cny={"BTCUSDT": float(capacity.policy.maximum_notional)},
            daily_volume_cny=float(capacity.policy.adv_notional))
        sources = dict(risk.source_records)
        sources["capacity_and_aum"] = replace(sources["capacity_and_aum"], value=dict(
            capacity_cny=dict(full.capacity_cny), daily_volume_cny=full.daily_volume_cny, total_aum=full.total_aum))
        risk = replace(risk, full_inputs=full, source_records=sources)
    signal = replace(second.signal_inputs[0], value={"instrument_id": "BTCUSDT", "score": D("-0.25")})
    args["decisions"] = (first, replace(second, execution=execution, signal_inputs=(signal,), research_risk=risk,
        capacity_snapshots=(execution.capacity,)))
    return args


def test_reversal_binds_full_two_sided_order_not_only_new_short_position(technical_arguments):
    record = run_authoritative_linear_perpetual_v2(**reversal_arguments(technical_arguments))
    payload = record["detail"]["result_hash_payload"]
    for contexts in (payload["decision_contexts"], payload["execution_risk_contexts"]):
        value = contexts[1]["capacity_flow"]
        assert value["current_quantities"] == {"BTCUSDT": "0.1"}
        assert value["target_quantities"] == {"BTCUSDT": "-0.1"}
        assert value["order_quantities"] == {"BTCUSDT": "-0.2"}
        assert value["capacity_notionals"] == {"BTCUSDT": "20.2"}


def test_reversal_is_rejected_when_target_alone_fits_but_actual_order_does_not(technical_arguments):
    # capacity=25.0, target=10.1 (<80%), actual order=20.2 (>80%).
    with pytest.raises(AuthoritativeBacktestInputError, match="Capacity: usage"):
        run_authoritative_linear_perpetual_v2(**reversal_arguments(technical_arguments, adv="250"))


def test_decision_flow_does_not_read_future_execution_price_but_execution_reprices():
    execution = original._execution()
    execution = replace(execution, execution_price=D("150"), opportunity=replace(execution.opportunity,
        executable_events=(ExecutableVenueEvent(original.DECISION - 10, D("100")),
            ExecutableVenueEvent(execution.timestamp, D("150")))))
    result = original._run(decision=original._decision(execution=execution))
    body = result["detail"]["result_hash_payload"]
    assert body["decision_contexts"][0]["capacity_flow"]["capacity_notionals"] == {"BTCUSDT": "10"}
    assert body["execution_risk_contexts"][0]["capacity_flow"]["capacity_notionals"] == {"BTCUSDT": "15"}
