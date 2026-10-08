from dataclasses import replace
from decimal import Decimal as D

import pytest

from services.execution.perpetual_simulation import ExecutableQuote, SimulationFrame, SimulationCostModel
from services.execution.perpetual_ledger_v2 import MarkEvent, FundingEvent
from services.research.gold_v2_strategy import GoldV2Strategy
from tests.execution.test_perpetual_simulation import F, T, run


def frame(offset,price='100',size='1'):
    return SimulationFrame(T+offset,quotes=(ExecutableQuote('XAUUSDT',T+offset,T+offset,
        D(price),D(price),D(size),D(size)),),market_events=(MarkEvent(T+offset,'XAUUSDT',D(price)),),
        capacity_snapshots=(replace(F['_capacity'](),instrument_id='XAUUSDT'),))


def strategy():
    return GoldV2Strategy(signal_time_ms=T,signal_id='a'*64,quantity=D('.1'),
        stop=D('95'),target=D('105'),entry_timeout_ms=50,holding_ms=100,max_quote_age_ms=20)


def execute(frames,s,**kwargs):
    # Explicit synthetic fixture, NOT real XAUUSDT venue rules.
    rule=replace(F['_rule'](),rule_id='synthetic-gold-rules',
        spec=replace(F['_spec'](),instrument_id='XAUUSDT'))
    return run(frames,s,contract_rules=(rule,),**kwargs)


def test_signal_is_not_same_frame_fill_and_target_closes_through_v2():
    s=strategy()
    result=execute((frame(0),frame(10),frame(20,'106'),frame(30,'107')),s,
                   costs=SimulationCostModel(fee_rate=D('.001')))
    assert result['orders'][0]['filled_at']==T+10
    assert result['orders'][1]['filled_at']==T+30
    assert result['ending_state']['positions']=={}
    assert D(result['reconciliation']['price_pnl'])==D('.7')
    assert D(result['reconciliation']['fee'])==D('.0207')
    assert D(result['reconciliation']['residual'])==0
    assert s.events[-1]['reason']=='POSITION_CLOSED'


def test_stop_cancels_remaining_entry_before_reducing_actual_partial_position():
    s=strategy()
    result=execute((frame(0),frame(10,size='.04'),frame(20,'94',size='.01'),
                    frame(30,'93',size='1'),frame(40,'93')),s)
    assert result['orders'][0]['status']=='CANCELLED'
    assert D(result['orders'][0]['filled_quantity'])==D('.05')
    assert D(result['orders'][1]['filled_quantity'])==D('-.05')
    assert result['ending_state']['positions']=={}


def test_rejected_entry_is_not_treated_as_position_or_retried():
    s=strategy()
    result=execute((frame(0),frame(10),frame(20,'106')),s,initial_collateral=D('.01'))
    assert len(result['orders'])==1 and result['orders'][0]['status']=='REJECTED'
    assert result['ending_state']['positions']=={}
    assert not any(e['reason']=='TARGET' for e in s.events)


def test_missed_signal_time_does_not_submit_late():
    s=strategy(); result=execute((frame(10),frame(20)),s)
    assert result['orders']==[]
    assert s.events[0]['reason']=='MISSED_SIGNAL_TIME'


def test_generated_signal_connects_to_canonical_ledger_without_pnl_copy():
    from services.research.gold_signal import GoldSignal,Rule,Zone
    from tests.research.test_gold_signal import window
    engine=GoldSignal(Rule(1000,100,D('10'),2),Zone(D('99'),D('100'),T-3000,T+1000))
    first=window(start=T-2010,received_at_ms=T-1000)
    pending=engine.observe(first,decision_ms=T-1000)
    plan=dict(quantity=D('.1'),stop=D('95'),target=D('105'),entry_timeout_ms=50,
              holding_ms=100,max_quote_age_ms=20)
    with pytest.raises(ValueError): GoldV2Strategy.from_signal(pending,**plan)
    second=window(start=T-1010,received_at_ms=T,high=D('102'),close=D('101.5'))
    confirmed=engine.observe(second,decision_ms=T)
    s=GoldV2Strategy.from_signal(confirmed,**plan)
    result=execute((frame(0),frame(10),frame(20,'106'),frame(30,'107')),s)
    assert result['ending_state']['positions']=={}
    assert result['authoritative'] is False
    assert s.identity['signal_id']==confirmed['decision_id']


def test_holding_timeout_uses_first_fill_not_signal_time():
    s=strategy()
    result=execute((frame(0),frame(10),frame(100),frame(110),frame(120)),s)
    assert result['orders'][1]['created_at']==T+110
    assert result['orders'][1]['filled_at']==T+120


def test_gold_adapter_funding_and_fees_reconcile_only_in_v2():
    s=strategy()
    funding=FundingEvent(T+20,'XAUUSDT',D('.01'),D('106'))
    payment=replace(frame(20,'106'),market_events=(funding,MarkEvent(T+20,'XAUUSDT',D('106'))))
    result=execute((frame(0),frame(10),payment,frame(30,'107')),s,
        funding_policies=(replace(F['_funding_policy'](),instrument_id='XAUUSDT'),),
        costs=SimulationCostModel(fee_rate=D('.001')))
    assert D(result['reconciliation']['funding'])==D('-.106')
    assert D(result['reconciliation']['ending_equity'])==D('1000.5733')
    assert D(result['reconciliation']['residual'])==0


def test_completed_adapter_rejects_new_unowned_position():
    from services.execution.perpetual_simulation import OrderIntent
    s=strategy()
    def mixed_strategy(ctx):
        actions=s(ctx)
        if ctx.timestamp==T+30:
            assert s.state=='DONE'
            return (*actions,OrderIntent('unrelated-entry','XAUUSDT',D('.1')))
        return actions
    with pytest.raises(ValueError,match='UNEXPECTED_GOLD_POSITION'):
        execute((frame(0),frame(10),frame(20,'106'),frame(30,'107'),frame(40,'107')),mixed_strategy)


def test_partial_exit_does_not_duplicate_or_reverse_position():
    s=strategy()
    r=execute((frame(0),frame(10),frame(20,'106'),frame(30,'106',size='.04'),
               frame(40,'106',size='.04'),frame(50,'106',size='.1')),s)
    assert len(r['orders'])==2
    assert r['orders'][1]['status']=='FILLED'
    assert D(r['orders'][1]['filled_quantity'])==D('-.1')
    assert r['ending_state']['positions']=={}
    assert sum(e['reason']=='TARGET' for e in s.events)==1


def test_rejected_exit_cannot_be_reported_as_closed_or_silently_retried():
    from services.execution.perpetual_simulation import RiskVerdict
    s=strategy()
    def policy(ctx,intent):
        return RiskVerdict(not intent.reduce_only,'TEST_EXIT_DENIED' if intent.reduce_only else None)
    with pytest.raises(ValueError,match='EXIT_TERMINATED_WITH_POSITION_REMAINING'):
        execute((frame(0),frame(10),frame(20,'106'),frame(30,'107')),s,
                risk_policy=policy,risk_policy_identity={'version':'exit-denial-fixture'})
    assert not any(e['reason']=='POSITION_CLOSED' for e in s.events)
