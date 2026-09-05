"""Executable strategy/account loop over the canonical perpetual ledger."""
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
import runpy

import pytest

from services.execution.perpetual_simulation import (
    CancelOrder, ExecutableQuote, OrderIntent, SimulationCostModel,
    SimulationFrame, SimulationLimits, RiskVerdict, run_linear_perpetual_simulation,
)
from services.execution.perpetual_ledger_v2 import MarkEvent, FundingEvent

F = runpy.run_path(str(Path(__file__).with_name("test_authoritative_perpetual_backtest.py")))
T = F["DECISION"]

def quote(offset, bid="100", ask="100", size="1", name="BTCUSDT", event_offset=None):
    return ExecutableQuote(name, T + (offset if event_offset is None else event_offset),
        T+offset, D(bid), D(ask), D(size), D(size))

def frame(offset, price="100", *, bid=None, ask=None, size="1", quotes=None, funding=()):
    q = (quote(offset, bid or price, ask or price, size),) if quotes is None else quotes
    return SimulationFrame(T+offset, quotes=q,
        market_events=(*funding, MarkEvent(T+offset, "BTCUSDT", D(price))),
        capacity_snapshots=(F["_capacity"](),))

def run(frames, strategy=lambda ctx: (), **kwargs):
    kwargs.setdefault("costs", SimulationCostModel(fee_rate=D("0")))
    rules = kwargs.pop("contract_rules", (F["_rule"](),))
    policies = kwargs.pop("funding_policies", tuple(
        replace(F["_funding_policy"](), instrument_id=r.spec.instrument_id,
                settlement_times=(T+1000,)) for r in rules))
    return run_linear_perpetual_simulation(
        release_identity={"commit":"f"*40}, dataset_identity={"dataset":"synthetic"},
        strategy_identity={"strategy":"regression.v1"},
        contract_rules=rules, funding_policies=policies,
        initial_collateral=kwargs.pop("initial_collateral", D("1000")),
        frames=frames, strategy=strategy, **kwargs)

def test_cash_baseline_and_hold_are_valid():
    result = run((frame(0), frame(10, "120")))
    assert result["ending_state"]["positions"] == {}
    assert result["reconciliation"]["ending_equity"] == "1000"
    assert result["orders"] == []
    assert result["risk_history_source"] == "LEDGER_EVENT_REPLAY"
    assert result["authoritative"] is False

def test_future_prices_cannot_change_the_decision_prefix():
    def strategy(ctx):
        return (OrderIntent("entry", "BTCUSDT", D(".1")),) if ctx.timestamp == T else ()
    a = run((frame(0), frame(10)), strategy)
    b = run((frame(0), frame(10, "200")), strategy)
    assert a["decision_events"][0] == b["decision_events"][0]
    assert a["order_events"][:1] == b["order_events"][:1]
    assert a["orders"][0]["filled_at"] == T+10
    assert b["orders"][0]["filled_at"] == T+10
    assert a["result_hash"] != b["result_hash"]

def test_bid_ask_fees_and_funding_are_counted_once_and_hand_reconcile():
    def strategy(ctx):
        if ctx.timestamp == T:
            return (OrderIntent("entry", "BTCUSDT", D(".1")),)
        if ctx.timestamp == T+20:
            return (OrderIntent("exit", "BTCUSDT", D("-.1"), reduce_only=True),)
        return ()
    result = run((
        frame(0, bid="99", ask="101"),
        frame(10, bid="99", ask="101"),
        frame(20, "105", funding=(FundingEvent(T+20, "BTCUSDT", D(".01"), D("105")),)),
        frame(30, "110", bid="109", ask="111"),
    ), strategy, costs=SimulationCostModel(fee_rate=D(".001")),
        funding_policies=(F["_funding_policy"](),))
    r = result["reconciliation"]
    assert D(r["ending_equity"]) == D("1000.674")
    assert D(r["price_pnl"]) == D(".8")
    assert D(r["fee"]) == D(".021")
    assert D(r["funding"]) == D("-.105")
    assert D(r["spread"]) == D("0")
    assert D(r["impact"]) == D("0")
    assert D(r["residual"]) == D("0")
    assert result["ending_state"]["positions"] == {}

def test_limit_can_rest_across_multiple_strategy_decisions():
    def strategy(ctx):
        return (OrderIntent("limit", "BTCUSDT", D(".1"), limit_price=D("95")),) if ctx.timestamp == T else ()
    result = run((frame(0), frame(10), frame(20, "94")), strategy)
    assert result["orders"][0]["status"] == "FILLED"
    assert result["orders"][0]["filled_at"] == T+20

def test_displayed_liquidity_supports_partial_fills_without_reusing_depth():
    def strategy(ctx):
        return (OrderIntent("entry", "BTCUSDT", D(".1")),) if ctx.timestamp == T else ()
    result = run((frame(0, "1000"), frame(10, "1000", size=".04"),
                  frame(20, "1000", size=".06")), strategy)
    fills = [e for e in result["order_events"] if e["status"] in ("PARTIALLY_FILLED","FILLED")]
    assert [D(e["filled_quantity"]) for e in fills] == [D(".04"), D(".06")]
    assert D(result["orders"][0]["filled_quantity"]) == D(".1")

def test_multi_leg_decision_and_same_timestamp_fills():
    eth_rule = replace(F["_rule"](), rule_id="eth-rules",
        spec=replace(F["_spec"](), instrument_id="ETHUSDT"))
    eth_capacity = replace(F["_capacity"](), instrument_id="ETHUSDT")
    def market(offset):
        return SimulationFrame(T+offset,
            quotes=(quote(offset),quote(offset,name="ETHUSDT")),
            market_events=(MarkEvent(T+offset,"BTCUSDT",D("100")),
                           MarkEvent(T+offset,"ETHUSDT",D("100"))),
            capacity_snapshots=(F["_capacity"](),eth_capacity))
    def strategy(ctx):
        if ctx.timestamp != T: return ()
        return (OrderIntent("btc","BTCUSDT",D(".05")), OrderIntent("eth","ETHUSDT",D(".05")))
    result = run((market(0),market(10)),strategy,contract_rules=(F["_rule"](),eth_rule))
    assert len(result["ending_state"]["positions"]) == 2
    assert {o["filled_at"] for o in result["orders"]} == {T+10}

def test_cancellation_and_expiry_do_not_abort_simulation():
    def strategy(ctx):
        if ctx.timestamp == T:
            return (OrderIntent("cancel","BTCUSDT",D(".1"),limit_price=D("90")),
                    OrderIntent("expire","BTCUSDT",D(".1"),limit_price=D("90"),expires_at=T+6))
        if ctx.timestamp == T+5: return (CancelOrder("cancel"),)
        return ()
    result = run((frame(0),frame(5),frame(10)),strategy)
    assert [o["status"] for o in result["orders"]] == ["CANCELLED","EXPIRED"]
    assert result["ending_state"]["positions"] == {}

def test_insufficient_margin_is_a_rejection_and_market_replay_continues():
    def strategy(ctx):
        return (OrderIntent("too-large","BTCUSDT",D("2")),) if ctx.timestamp == T else ()
    result = run((frame(0,size="10"),frame(10,size="10"),frame(20)),strategy,initial_collateral=D("10"),
                 limits=SimulationLimits(max_gross_leverage=D("30"),max_single_weight=D("30")))
    assert result["orders"][0]["status"] == "REJECTED"
    assert result["orders"][0]["reason"] == "INSUFFICIENT_MARGIN"
    assert result["ending_state"]["positions"] == {}
    assert result["equity_history"][-1]["timestamp"] == T+20

def test_late_old_quote_cannot_fill_an_order_created_after_its_event():
    def strategy(ctx):
        return (OrderIntent("entry","BTCUSDT",D(".1")),) if ctx.timestamp == T else ()
    result = run((frame(0),frame(10,quotes=(quote(10,event_offset=-1),)),frame(20)),strategy)
    assert result["orders"][0]["filled_at"] == T+20

def test_strategy_context_cannot_observe_future_history_or_mutate_positions():
    contexts = []
    def strategy(ctx):
        contexts.append(ctx)
        with pytest.raises(TypeError):
            ctx.positions["BTCUSDT"] = None
        return ()
    result = run((frame(0),frame(10),frame(20)),strategy)
    first = contexts[0].history
    assert first[-1].timestamp == T
    with pytest.raises(IndexError): _ = first[len(first)]
    assert len(first) < len(result["equity_history"])

def test_reduce_only_must_never_reverse_after_other_orders_close_inventory():
    def strategy(ctx):
        if ctx.timestamp == T: return (OrderIntent("entry","BTCUSDT",D(".1")),)
        if ctx.timestamp == T+10:
            return (OrderIntent("exit-a","BTCUSDT",D("-.1"),reduce_only=True),
                    OrderIntent("exit-b","BTCUSDT",D("-.1"),reduce_only=True))
        return ()
    result = run((frame(0),frame(10),frame(20)),strategy)
    assert result["ending_state"]["positions"] == {}
    assert result["orders"][2]["status"] == "REJECTED"
    assert result["orders"][2]["reason"] == "REDUCE_ONLY_NO_POSITION"

def test_execution_reprices_risk_without_rewriting_prior_permission():
    def strategy(ctx):
        return (OrderIntent("entry","BTCUSDT",D(".1")),) if ctx.timestamp == T else ()
    result = run((frame(0),frame(10,"2000"),frame(20,"2000")),strategy)
    assert result["order_events"][0]["status"] == "OPEN"
    assert result["orders"][0]["status"] == "REJECTED"
    assert result["orders"][0]["reason"] == "SINGLE_NAME_LIMIT"
    assert result["ending_state"]["positions"] == {}

def test_limit_slippage_cannot_execute_beyond_limit_price():
    def strategy(ctx):
        return (OrderIntent("limit","BTCUSDT",D(".1"),limit_price=D("100")),) if ctx.timestamp == T else ()
    result = run((frame(0),frame(10,"100"),frame(20,"90")),strategy,
                 costs=SimulationCostModel(adverse_slippage_rate=D(".01")))
    assert result["orders"][0]["filled_at"] == T+20
    assert D(result["ending_state"]["positions"]["BTCUSDT"]["average_entry_price"]) == D("90.9")

def test_missing_settlement_event_is_not_silently_zero_funding():
    with pytest.raises(ValueError, match="funding schedule"):
        run((frame(0),frame(20)),funding_policies=(F["_funding_policy"](),))

def test_duplicate_venue_quote_cannot_replenish_consumed_liquidity():
    def strategy(ctx):
        return (OrderIntent("entry","BTCUSDT",D(".1")),) if ctx.timestamp == T else ()
    repeated = quote(10,"1000","1000",".04")
    result = run((frame(0,"1000"),frame(10,"1000",quotes=(repeated,)),
        frame(20,"1000",quotes=(repeated,)),frame(30,"1000",size=".06")),strategy)
    fills = [e for e in result["order_events"] if e["status"] in ("FILLED","PARTIALLY_FILLED")]
    assert [e["timestamp"] for e in fills] == [T+10,T+30]

def test_short_mark_cannot_inflate_the_budget_for_another_instrument():
    eth_rule = replace(F["_rule"](), rule_id="eth-rules",
        spec=replace(F["_spec"](), instrument_id="ETHUSDT"))
    eth_capacity = replace(F["_capacity"](), instrument_id="ETHUSDT")
    def market(offset, btc_mark="100"):
        return SimulationFrame(T+offset, quotes=(quote(offset,size="10"),
            quote(offset,size="10",name="ETHUSDT")),market_events=(
                MarkEvent(T+offset,"BTCUSDT",D(btc_mark)),
                MarkEvent(T+offset,"ETHUSDT",D("100"))),
            capacity_snapshots=(F["_capacity"](),eth_capacity))
    def strategy(ctx):
        if ctx.timestamp == T: return (OrderIntent("short","BTCUSDT",D("-1.4")),)
        if ctx.timestamp == T+20: return (OrderIntent("eth","ETHUSDT",D("1.7")),)
        return ()
    result = run((market(0),market(10),market(20,"1"),market(30,"1")),strategy,
        contract_rules=(F["_rule"](),eth_rule))
    assert result["orders"][1]["status"] == "REJECTED"
    assert result["orders"][1]["reason"] == "SINGLE_NAME_LIMIT"

def test_result_is_json_serializable_without_lossy_custom_encoder():
    import json
    result = run((frame(0),frame(10)))
    assert json.loads(json.dumps(result))["result_hash"] == result["result_hash"]

def test_cost_assumptions_must_be_explicit():
    with pytest.raises(ValueError,match="explicit cost"):
        run((frame(0),frame(10)),costs=None)

@pytest.mark.parametrize("mode", ["deny", "raise", "invalid"])
def test_additional_risk_policy_fails_closed(mode):
    def strategy(ctx):
        return (OrderIntent("entry","BTCUSDT",D(".1")),) if ctx.timestamp == T else ()
    def policy(ctx, intent):
        if mode == "raise": raise RuntimeError("fixture failure")
        if mode == "invalid": return True
        return RiskVerdict(False,"FIXTURE_DENIAL")
    result = run((frame(0),frame(10)),strategy,risk_policy=policy,
                 risk_policy_identity={"version":mode})
    assert result["orders"][0]["status"] == "REJECTED"
    assert result["ending_state"]["positions"] == {}

def test_risk_callback_cannot_override_intrinsic_exposure_limit():
    def strategy(ctx):
        return (OrderIntent("entry","BTCUSDT",D("2")),) if ctx.timestamp == T else ()
    result = run((frame(0),frame(10)),strategy,risk_policy=lambda ctx,intent:RiskVerdict(True),
        risk_policy_identity={"version":"approve-all"})
    assert result["orders"][0]["reason"] == "SINGLE_NAME_LIMIT"

def test_risk_callback_requires_identity_and_changes_input_identity():
    policy = lambda ctx,intent:RiskVerdict(True)
    with pytest.raises(ValueError,match="frozen identity"):
        run((frame(0),frame(10)),risk_policy=policy)
    a = run((frame(0),frame(10)),risk_policy=policy,risk_policy_identity={"version":"a"})
    b = run((frame(0),frame(10)),risk_policy=policy,risk_policy_identity={"version":"b"})
    assert a["input_hash"] != b["input_hash"]

def test_latency_and_aggregate_liquidity_are_respected():
    def strategy(ctx):
        if ctx.timestamp == T:
            return (OrderIntent("a","BTCUSDT",D(".05")),
                    OrderIntent("b","BTCUSDT",D(".05")))
        return ()
    result = run((frame(0,"1000"),frame(5,"1000",size=".1"),
                  frame(10,"1000",size=".05"),frame(20,"1000",size=".05")),strategy,
                  limits=SimulationLimits(order_latency_ms=5))
    assert [o["filled_at"] for o in result["orders"]] == [T+10,T+20]
