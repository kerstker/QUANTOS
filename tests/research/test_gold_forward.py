import copy
import pytest
from tests.tools.test_gold_signal_replay import archive
from services.research.gold_forward import ForwardObservation
from services.data.gold_execution_tape import new_gold_trade_cursor

def plan():
    d=archive();d.pop('packets');d['schema']='GOLD_FORWARD_PLAN_V1';d['max_lateness_ms']=20
    return d

def test_rules_are_frozen_before_window_and_only_due_decisions_run():
    d=plan();engine=ForwardObservation(d,registered_at_ms=400,cursor_factory=new_gold_trade_cursor)
    d['zone']['lower']='500'
    assert engine.advance(archive()['packets'],now_ms=2000) is None
    first=engine.advance(archive()['packets'],now_ms=2010)
    assert first['decision']['state']=='CONFIRMING'
    last=engine.advance(archive()['packets'],now_ms=3010)
    assert last['decision']['intent']=='LONG_RESEARCH_SIGNAL'
    assert engine.status=='COMPLETE_OBSERVATION'
    with pytest.raises(ValueError):engine.advance([],now_ms=3011)

def test_late_schedule_is_not_replayed_as_live_decision():
    e=ForwardObservation(plan(),registered_at_ms=400,cursor_factory=new_gold_trade_cursor)
    r=e.advance(archive()['packets'],now_ms=2031)
    assert r['reason']=='MISSED_DECISION_DEADLINE' and r['decision'] is None
    assert e.status=='INPUT_INCOMPLETE'

def test_missing_watermark_cannot_be_repaired_after_decision():
    e=ForwardObservation(plan(),registered_at_ms=400,cursor_factory=new_gold_trade_cursor)
    r=e.advance([],now_ms=2010)
    assert r['reason']=='MISSING_WINDOW_COVERAGE'
    with pytest.raises(ValueError):e.advance(archive()['packets'],now_ms=2011)

@pytest.mark.parametrize('case',['past','too_long','too_early','unknown','future_zone'])
def test_invalid_forward_protocol_rejected(case):
    d=plan();registered=400
    if case=='past':registered=500
    if case=='too_long':d['decision_times_ms'][-1]=70000
    if case=='too_early':d['decision_times_ms'][0]=1999
    if case=='unknown':d['leverage']=100
    if case=='future_zone':d['zone']['registered_at_ms']=1001
    with pytest.raises(ValueError):ForwardObservation(d,registered_at_ms=registered,cursor_factory=new_gold_trade_cursor)

def test_clock_reversal_poisoning():
    e=ForwardObservation(plan(),registered_at_ms=400,cursor_factory=new_gold_trade_cursor)
    e.advance([],now_ms=500)
    with pytest.raises(ValueError):e.advance([],now_ms=499)
    assert e.status=='CLOCK_INVALID'
