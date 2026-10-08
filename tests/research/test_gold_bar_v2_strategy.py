from dataclasses import replace
from decimal import Decimal as D

import pytest

from services.execution.perpetual_simulation import SimulationCostModel, RiskVerdict
from services.execution.perpetual_ledger_v2 import FundingEvent
from services.research.gold_bar_candidates import INTERVAL_MS
from services.research.gold_bar_v2_strategy import GoldBarV2Strategy
from tests.research.test_gold_bar_candidates import bars
from tests.research.test_gold_v2_strategy import execute, frame, T


def strategy(side):
    values = bars()
    shift = (T // INTERVAL_MS - 401) * INTERVAL_MS
    values = [replace(b, open_ms=b.open_ms + shift, close_ms=b.close_ms + shift,
                      available_ms=b.available_ms + shift) for b in values]
    values[-1] = replace(values[-1], available_ms=T,
                         high=D('110') if side == 'BUY' else D('101'),
                         low=D('90') if side == 'SELL' else D('99'),
                         close=D('109') if side == 'BUY' else D('91'))
    return GoldBarV2Strategy(values, candidate='channel', quantity=D('.1'),
                             entry_timeout_ms=50, max_quote_age_ms=20)


@pytest.mark.parametrize('side,exit_price', [('BUY', '95'), ('SELL', '105')])
def test_both_directions_use_actual_v2_fills_stop_and_fees(side, exit_price):
    s = strategy(side)
    result = execute((frame(0), frame(10), frame(20, exit_price),
                      frame(30, exit_price), frame(40, exit_price)), s,
                     costs=SimulationCostModel(fee_rate=D('.001')))
    assert len(result['orders']) == 2
    assert result['orders'][0]['filled_at'] == T + 10
    assert result['orders'][1]['filled_at'] == T + 30
    assert result['ending_state']['positions'] == {}
    assert D(result['reconciliation']['price_pnl']) == D('-.5')
    assert D(result['reconciliation']['fee']) > 0
    assert D(result['reconciliation']['residual']) == 0
    assert any(e['reason'] == 'STOP' for e in s.events)
    assert result['authoritative'] is False


def test_partial_fill_is_cancelled_before_remaining_position_exit():
    s = strategy('BUY')
    result = execute((frame(0), frame(10, size='.04'), frame(20, '95', size='.01'),
                      frame(30, '95'), frame(40, '95')), s)
    assert result['orders'][0]['status'] == 'CANCELLED'
    assert D(result['orders'][1]['filled_quantity']) == D('-.05')
    assert result['ending_state']['positions'] == {}


def test_rejected_entry_does_not_retry_same_bar():
    s = strategy('BUY')
    result = execute((frame(0), frame(10), frame(20)), s, initial_collateral=D('.01'))
    assert len(result['orders']) == 1
    assert result['orders'][0]['status'] == 'REJECTED'


def test_funding_crossing_is_charged_by_original_ledger():
    settlement = frame(1000)
    settlement = replace(settlement, market_events=(
        FundingEvent(T + 1000, 'XAUUSDT', D('.01'), D('100')),
        *settlement.market_events))
    result = execute((frame(0), frame(10), settlement, frame(1010, '95'),
                      frame(1020, '95'), frame(1030, '95')), strategy('BUY'))
    assert D(result['reconciliation']['funding']) == D('-.1')
    assert D(result['reconciliation']['price_pnl']) == D('-.5')
    assert D(result['reconciliation']['residual']) == 0


def test_observed_mode_does_not_open_from_historical_warmup_signal():
    seed = strategy('BUY')
    observed = GoldBarV2Strategy(seed.bars, candidate='channel', quantity=D('.1'),
        entry_timeout_ms=50, max_quote_age_ms=20, entry_not_before_ms=T + 20)
    result = execute((frame(0), frame(10), frame(20), frame(30)), observed)
    assert result['orders'] == []


def test_rejected_exit_retries_after_price_recovers_without_reopening():
    s = strategy('BUY')
    def risk(ctx, intent):
        return RiskVerdict(not (intent.reduce_only and ctx.timestamp == T + 20), 'TEST_TRANSIENT_EXIT_REJECTION')
    result = execute((frame(0), frame(10), frame(20, '95'), frame(30), frame(40), frame(50)), s,
        risk_policy=risk, risk_policy_identity={'synthetic': 'exit-rejection'})
    entry, rejected, retried = result['orders']
    assert entry['filled_quantity'] == '0.1'
    assert rejected['status'] == 'REJECTED'
    assert retried['created_at'] == T + 30
    assert retried['filled_quantity'] == '-0.1'
    assert all(a['reduce_only'] for row in result['decision_events'] for a in row['actions']
               if a.get('order_id') == retried['order_id'])
    assert result['ending_state']['positions'] == {}
    assert result['reconciliation']['residual'] == '0'


def test_partial_exit_rejection_retries_only_remaining_position():
    s = strategy('BUY')
    def risk(ctx, intent):
        return RiskVerdict(not (intent.reduce_only and intent.order_id.endswith('-exit')
                               and ctx.timestamp == T + 40), 'TEST_EXIT_FILL_REJECTED')
    result = execute((frame(0), frame(10), frame(20, '95'), frame(30, '95', size='.04'),
                      frame(40, '95'), frame(50, '95'), frame(60, '95')), s,
        risk_policy=risk, risk_policy_identity={'synthetic': 'partial-exit-rejection'})
    entry, partial, retried = result['orders']
    assert partial['status'] == 'REJECTED'
    assert partial['filled_quantity'] == '-0.04'
    assert retried['filled_quantity'] == '-0.06'
    assert result['ending_state']['positions'] == {}
    assert result['reconciliation']['residual'] == '0'


def test_rejected_exit_waits_for_new_fresh_quote_and_keeps_unresolved_inventory():
    s = strategy('BUY')
    rejected_quote = frame(20, '95')
    same_quote = replace(frame(30, '95'), quotes=rejected_quote.quotes)
    stale_quote = replace(frame(50, '95'), quotes=rejected_quote.quotes)
    def risk(ctx, intent):
        return RiskVerdict(not intent.reduce_only, 'TEST_PERSISTENT_EXIT_REJECTION')
    result = execute((frame(0), frame(10), rejected_quote, same_quote, stale_quote, frame(60, '95')), s,
        risk_policy=risk, risk_policy_identity={'synthetic': 'persistent-exit-rejection'})
    assert [o['created_at'] for o in result['orders']] == [T, T + 20, T + 60]
    assert [o['status'] for o in result['orders']] == ['FILLED', 'REJECTED', 'REJECTED']
    assert result['ending_state']['positions']['XAUUSDT']['quantity'] == '0.1'
    assert result['reconciliation']['residual'] == '0'


def test_appended_bar_preserves_strategy_state_and_rejects_backdated_receipt():
    from services.execution.perpetual_simulation import SimulationContext
    s = strategy('BUY')
    s(SimulationContext(T, D('1000'), {}, {}, {}, (), {}, D('1000')))
    consumed = set(s.consumed)
    last = s.bars[-1]
    next_bar = replace(last, open_ms=last.open_ms + INTERVAL_MS,
                       close_ms=last.close_ms + INTERVAL_MS, available_ms=T + INTERVAL_MS)
    s.append_bars((next_bar,))
    assert s.last_time == T
    assert s.consumed == consumed
    assert s.bars[-1] == next_bar
    before = s.bars
    with pytest.raises(ValueError):
        s.append_bars((next_bar,))
    assert s.bars == before
    s.last_time = T + 3 * INTERVAL_MS
    late = replace(next_bar, open_ms=next_bar.open_ms + INTERVAL_MS,
                   close_ms=next_bar.close_ms + INTERVAL_MS, available_ms=T + 2 * INTERVAL_MS)
    with pytest.raises(ValueError, match='BAR_RECEIPT_ALREADY_PROCESSED'):
        s.append_bars((late,))
    assert s.bars == before
