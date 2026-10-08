from dataclasses import replace
from decimal import Decimal as D

import pytest

from services.execution.perpetual_simulation import OrderIntent, SimulationCostModel
from services.execution.perpetual_ledger_v2 import FundingEvent
from services.research.gold_loss_control import GoldResearchLossControl
from tests.research.test_gold_v2_strategy import execute, frame, T


def always_enter(ctx):
    if not ctx.positions and not ctx.open_orders:
        return (OrderIntent('entry-' + str(ctx.timestamp), 'XAUUSDT', D('.1')),)
    return ()


def run_control(frames, *, costs=None):
    control = GoldResearchLossControl(always_enter, initial_equity=D('100'))
    result = execute(frames, control, initial_collateral=D('100'),
        costs=costs or SimulationCostModel(), risk_policy=control.risk,
        risk_policy_identity=control.identity)
    return control, result


def test_fee_loss_at_exact_five_percent_latches_and_exits_without_reentry():
    control, result = run_control(tuple(frame(i) for i in (0, 10, 20, 30, 40)),
                                  costs=SimulationCostModel(fee_rate=D('.5')))
    assert len(result['orders']) == 2
    assert result['orders'][0]['filled_at'] == T + 10
    assert result['orders'][1]['created_at'] == T + 10
    assert result['orders'][1]['filled_at'] == T + 20
    report = control.report(result)
    assert report['status'] == 'STOPPED_FLAT'
    assert report['trigger']['equity'] == '95'
    assert report['trigger']['timestamp'] == T + 10
    assert result['ending_state']['positions'] == {}
    assert result['reconciliation']['residual'] == '0'
    # The stop is a trigger, not a guarantee that exit costs cannot exceed 5%.
    assert result['ending_state']['account_equity'] == '90'


def test_partial_entry_is_cancelled_before_risk_exit_of_actual_inventory():
    control, result = run_control((frame(0), frame(10, size='.07'), frame(20), frame(30)),
                                  costs=SimulationCostModel(fee_rate=D('.75')))
    entry, exit_order = result['orders']
    assert entry['status'] == 'CANCELLED'
    assert entry['filled_quantity'] == '0.07'
    assert exit_order['filled_quantity'] == '-0.07'
    assert control.report(result)['status'] == 'STOPPED_FLAT'
    assert result['reconciliation']['residual'] == '0'


def test_funding_loss_blocks_pending_opening_fill_before_strategy_callback():
    settlement = frame(1000)
    settlement = replace(settlement, market_events=(FundingEvent(T + 1000, 'XAUUSDT', D('.1'), D('100')),
                                                    *settlement.market_events))
    control, result = run_control((frame(0), frame(10, size='.05'), settlement, frame(1010), frame(1020)),
                                  costs=SimulationCostModel(fee_rate=D('.9')))
    entry, exit_order = result['orders']
    assert entry['filled_quantity'] == '0.05'
    assert entry['status'] == 'REJECTED'
    assert entry['reason'] == 'GOLD_CUMULATIVE_LOSS_LIMIT'
    assert exit_order['filled_quantity'] == '-0.05'
    assert result['reconciliation']['funding'] == '-0.5'
    assert control.report(result)['status'] == 'STOPPED_FLAT'


def test_end_of_tape_with_unfilled_risk_exit_is_unresolved_not_stopped_flat():
    control, result = run_control((frame(0), frame(10)), costs=SimulationCostModel(fee_rate=D('.5')))
    assert control.report(result)['status'] == 'STOPPING_UNRESOLVED'
    assert result['ending_state']['positions']


def test_loss_threshold_is_original_equity_not_peak_and_cannot_reset():
    from services.execution.perpetual_simulation import EquityPoint, SimulationContext
    history = []
    control = GoldResearchLossControl(lambda ctx: (), initial_equity=D('100'))
    for i, equity in enumerate(('100', '110', '104', '95', '110')):
        history.append(EquityPoint(timestamp=T + i, sequence=i, phase='MARK', equity=D(equity),
                                   drawdown=D(0), return_since_previous=None))
        context = SimulationContext(T + i, D(equity), {}, {}, {}, tuple(history), {}, D(equity))
        control(context)
        assert (control.trigger is not None) is (i >= 3)
    assert control.trigger['equity'] == '95'


@pytest.mark.parametrize('equity', ['95', '95.000000000001', '94.9', '110'])
def test_research_threshold_matches_existing_demo_opening_guard(equity):
    from live.execution.demo_position_context import require_gold_cumulative_loss, PositionContextRejected
    from services.execution.perpetual_simulation import EquityPoint, SimulationContext
    initial = EquityPoint(T, 0, D('100'), None, D(0), 'INITIAL')
    point = EquityPoint(T + 1, 1, D(equity), None, D(0), 'MARK')
    control = GoldResearchLossControl(lambda ctx: (), initial_equity=D('100'))
    control(SimulationContext(T + 1, D(equity), {}, {}, {}, (initial, point), {}, D(equity)))
    context = dict(schema='DEMO_CANONICAL_POSITION_CONTEXT_V1', symbol='XAUUSDT',
                   account_ref_sha256='a' * 64, initial_equity='100')
    account = dict(account_ref_sha256='a' * 64, equity=equity)
    try:
        require_gold_cumulative_loss([dict(symbol='XAUUSDT', reduceOnly=False)], account, context)
        rejected = False
    except PositionContextRejected:
        rejected = True
    assert rejected is (control.trigger is not None)
