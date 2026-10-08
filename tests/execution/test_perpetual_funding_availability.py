from dataclasses import replace
from decimal import Decimal as D

import pytest

from services.execution.perpetual_ledger_v2 import FundingEvent
from services.execution.perpetual_simulation import LinearPerpetualSimulationSession, OrderIntent
from tests.execution.test_perpetual_simulation import T, frame
from tests.execution.test_perpetual_simulation_session import configuration


def delayed(rate='.01', available=1100):
    return replace(frame(1000, funding=(FundingEvent(T + 1000, 'BTCUSDT', D(rate), D('100')),)),
                   funding_available_at=T + available)


def test_delayed_funding_charges_settlement_inventory_and_embargoes_trading():
    seen = []
    def strategy(ctx):
        seen.append(ctx.timestamp)
        if ctx.timestamp == T:
            return (OrderIntent('entry', 'BTCUSDT', D('.1')),)
        if ctx.timestamp == T + 20:
            return (OrderIntent('resting', 'BTCUSDT', D('.1'), limit_price=D('90')),)
        if ctx.timestamp == T + 1100:
            return (OrderIntent('exit', 'BTCUSDT', D('-.1'), reduce_only=True),)
        return ()
    s = LinearPerpetualSimulationSession(**configuration(strategy))
    s.advance((frame(0), frame(10), frame(20)))
    s.advance((delayed(), frame(1050, '90')))
    assert seen == [T, T + 10, T + 20]
    with pytest.raises(ValueError, match='FUNDING_RECEIPT_PENDING'):
        s.report()
    s.advance((frame(1100), frame(1110)))
    result = s.report()
    assert result['reconciliation']['funding'] == '-0.1'
    assert result['reconciliation']['residual'] == '0'
    assert result['ending_state']['positions'] == {}
    resting = next(o for o in result['orders'] if o['order_id'] == 'resting')
    assert resting['status'] == 'CANCELLED'
    assert resting['filled_quantity'] == '0'
    assert result['funding_availability_policy'] == 'CANCEL_AND_PAUSE_UNTIL_RECEIPT_V1'


def test_delayed_rate_does_not_change_prior_strategy_decisions():
    def execute(rate):
        seen = []
        def strategy(ctx):
            seen.append((ctx.timestamp, ctx.equity))
            return (OrderIntent('entry', 'BTCUSDT', D('.1')), ) if ctx.timestamp == T else ()
        s = LinearPerpetualSimulationSession(**configuration(strategy))
        s.advance((frame(0), frame(10), delayed(rate), frame(1050), frame(1100)))
        return seen, s.report()
    left, a = execute('.01')
    right, b = execute('.02')
    assert left[:-1] == right[:-1]
    assert left[-1][0] == right[-1][0] == T + 1100
    assert left[-1][1] != right[-1][1]
    assert a['input_hash'] != b['input_hash']


@pytest.mark.parametrize('value', [T + 999, True, '1100'])
def test_invalid_receipt_clock_rejected(value):
    with pytest.raises(ValueError):
        replace(delayed(), funding_available_at=value)


def test_receipt_without_settlement_rejected():
    with pytest.raises(ValueError):
        replace(frame(0), funding_available_at=T + 10)


def test_clock_changes_are_bound_and_completed_checkpoint_restores():
    def strategy(ctx):
        return (OrderIntent('entry', 'BTCUSDT', D('.1')), ) if ctx.timestamp == T else ()
    prefix = (frame(0), frame(10), delayed(), frame(1100))
    s = LinearPerpetualSimulationSession(**configuration(strategy))
    s.advance(prefix)
    restored = LinearPerpetualSimulationSession.restore(s.checkpoint(), frames=prefix,
        **configuration(strategy))
    assert restored.report() == s.report()
    different = LinearPerpetualSimulationSession(**configuration(strategy))
    different.advance((frame(0), frame(10), delayed(available=1099), frame(1100)))
    assert different.report()['input_hash'] != s.report()['input_hash']


def test_receipt_clock_validation_does_not_consume_event_iterator():
    original = delayed()
    value = replace(original, market_events=iter(original.market_events))
    assert value.market_events == original.market_events


def test_observed_funding_adapter_retains_actual_settlement_and_receipt_clocks():
    from services.research.gold_funding_dataset import ObservedFundingSettlement
    event = FundingEvent(T + 1001, 'XAUUSDT', D('.001'), D('4000'))
    observed = ObservedFundingSettlement(event, T + 1200, T + 1300, 'a' * 64)
    actual = observed.simulation_frame()
    assert actual.timestamp == T + 1001
    assert actual.market_events == (event,)
    assert actual.funding_available_at == T + 1300
    assert actual.quotes == ()


def test_gold_loss_limit_retains_economic_trigger_and_exits_after_receipt():
    from services.execution.perpetual_simulation import SimulationCostModel
    from tests.research.test_gold_loss_control import run_control
    from tests.research.test_gold_v2_strategy import frame as gold_frame, T as gold_t
    settlement = gold_frame(1000)
    settlement = replace(settlement, market_events=(
        FundingEvent(gold_t + 1000, 'XAUUSDT', D('.1'), D('100')),
        *settlement.market_events), funding_available_at=gold_t + 1100)
    control, result = run_control((gold_frame(0), gold_frame(10, size='.05'), settlement,
        gold_frame(1050), gold_frame(1100), gold_frame(1110), gold_frame(1120)),
        costs=SimulationCostModel(fee_rate=D('.9')))
    entry, exit_order = result['orders']
    assert entry['status'] == 'CANCELLED'
    assert entry['filled_quantity'] == '0.05'
    assert exit_order['created_at'] == gold_t + 1100
    assert exit_order['filled_at'] == gold_t + 1110
    assert result['reconciliation']['funding'] == '-0.5'
    assert control.report(result)['trigger']['timestamp'] == gold_t + 1000
    assert control.report(result)['status'] == 'STOPPED_FLAT'
