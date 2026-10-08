import pytest
from live.execution.demo_batch_risk import _digest


@pytest.mark.parametrize('state', ['HOLDING', 'NO_ENTRY_SIGNAL', 'RECOVERY_REQUIRED',
                                  'STRATEGY_EPISODE_SETTLED', 'DECISION_REQUIRES_APPROVAL',
                                  'OTHER_POSITION_REQUIRES_RECONCILIATION','PILOT_STOPPED'])
@pytest.mark.parametrize('execute', [False, True])
def test_tick_hands_only_actionable_decisions_to_original_dispatch(monkeypatch, state, execute):
    from tools import gold_strategy_tick as tick
    calls = []
    observed = dict(status='DECISION_RETAINED', decision={'status':state,
        'execution_constraints':{'valid_until_ms':2**63-1}})
    def observe(**kwargs):
        calls.append(('observe',kwargs)); return observed
    def dispatch(**kwargs):
        calls.append(('dispatch',kwargs)); return {'status':'QUERY_ONLY'}
    monkeypatch.setattr(tick.controller, 'run', observe)
    monkeypatch.setattr(tick.dispatcher, 'run', dispatch)
    result = tick.run(source_commit='c'*40, execute_approved=execute)
    assert result['observation'] == observed
    assert calls[0] == ('observe', {'source_commit':'c'*40})
    if state == 'DECISION_REQUIRES_APPROVAL' and execute:
        assert calls[1:] == [('dispatch',dict(source_commit='c'*40,execute=True,use_approved_policy=True,result_sha256=_digest(observed)))]
        assert result['execution']['status'] == 'QUERY_ONLY'
    else:
        assert len(calls) == 1 and result['execution'] is None
    if state == 'OTHER_POSITION_REQUIRES_RECONCILIATION':
        assert result['status'] == 'OBSERVATION_REQUIRES_ATTENTION'
    if state == 'PILOT_STOPPED':assert result['status']=='PILOT_STOPPED'


def test_pilot_stop_ends_session_without_dispatch(monkeypatch):
    from tools import gold_strategy_tick as tick
    def session(**kw):
        assert kw['on_decision'](dict(status='DECISION_RETAINED',decision={'status':'PILOT_STOPPED'})) is False
        return {'status':'BOUNDED_SESSION_COMPLETE'}
    monkeypatch.setattr(tick.controller,'run',session)
    monkeypatch.setattr(tick.dispatcher,'run',lambda **kw:pytest.fail('stopped pilot dispatched'))
    result=tick.run_loop(source_commit='c'*40,execute_approved=True,max_ticks=60)
    assert result['status']=='PILOT_STOPPED' and len(result['ticks'])==1


def test_pilot_required_cannot_fall_back_to_legacy_handoff(monkeypatch):
    from tools import gold_strategy_tick as tick
    observed=dict(status='DECISION_RETAINED',decision={'status':'DECISION_REQUIRES_APPROVAL'})
    monkeypatch.setattr(tick.controller,'run',lambda **kw:observed)
    monkeypatch.setattr(tick.dispatcher,'run',lambda **kw:pytest.fail('missing pilot dispatched'))
    result=tick.run(source_commit='c'*40,execute_approved=True,require_pilot=True)
    assert result['status']=='PILOT_CONFIGURATION_REQUIRED' and result['execution'] is None


def test_failed_observation_never_releases_previously_approved_task(monkeypatch):
    from tools import gold_strategy_tick as tick
    monkeypatch.setattr(tick.controller, 'run', lambda **kw: {'status':'INPUTS_UNRESOLVED'})
    monkeypatch.setattr(tick.dispatcher, 'run', lambda **kw: pytest.fail('dispatch after failed observation'))
    result = tick.run(source_commit='c'*40, execute_approved=True)
    assert result['status'] == 'OBSERVATION_REQUIRES_ATTENTION'


def test_loop_observes_executes_and_stops_after_research_delivery(monkeypatch):
    from tools import gold_strategy_tick as tick
    states=iter(['DECISION_REQUIRES_APPROVAL','HOLDING','DECISION_REQUIRES_APPROVAL','STRATEGY_EPISODE_SETTLED'])
    observed=[];dispatched=[];waits=[]
    launches=[]
    def observe(**kwargs):
        launches.append(kwargs)
        for index in range(kwargs['ticks']):
            if index: waits.append(kwargs['interval_seconds'])
            state=next(states);observed.append(state)
            value=dict(status='DECISION_RETAINED',decision={'status':state,
                'execution_constraints':{'valid_until_ms':2**63-1}},
                research_feedback={'status':'RESEARCH_FEEDBACK_DELIVERED'} if state=='STRATEGY_EPISODE_SETTLED' else {})
            if not kwargs['on_decision'](value): break
        return {'status':'BOUNDED_SESSION_COMPLETE'}
    monkeypatch.setattr(tick.controller,'run',observe)
    monkeypatch.setattr(tick.dispatcher,'run',lambda **kw:dispatched.append(kw) or {'status':'EXECUTOR_RESULT_RECORDED'})
    result=tick.run_loop(source_commit='c'*40,execute_approved=True,max_ticks=10,interval_seconds=2)
    assert result['status']=='RESEARCH_FEEDBACK_DELIVERED' and len(result['ticks'])==4
    assert len(dispatched)==2 and waits==[2,2,2] and len(launches)==1


@pytest.mark.parametrize('status,expected_ticks', [('WAITING_QUERY_INTERVAL',2),('RESIDENT_BUSY',2),
                                               ('INPUTS_UNRESOLVED',1)])
def test_original_query_pacing_waits_without_new_submission(monkeypatch,status,expected_ticks):
    from tools import gold_strategy_tick as tick
    observations=iter([
        dict(status='DECISION_RETAINED',decision={'status':'RECOVERY_REQUIRED'},
             recovery_observation=dict(status=status,new_order_submission_enabled=False)),
        dict(status='DECISION_RETAINED',decision={'status':'NO_ENTRY_SIGNAL'})])
    def session(**kw):
        for _ in range(kw['ticks']):
            if not kw['on_decision'](next(observations)): break
        return {'status':'BOUNDED_SESSION_COMPLETE'}
    monkeypatch.setattr(tick.controller,'run',session)
    monkeypatch.setattr(tick.dispatcher,'run',lambda **kw:pytest.fail('new send while reconciling'))
    result=tick.run_loop(source_commit='c'*40,execute_approved=True,max_ticks=2)
    assert len(result['ticks'])==expected_ticks


def test_session_failure_preserves_prior_handoff_without_replaying_it(monkeypatch):
    from tools import gold_strategy_tick as tick
    sends=[]
    def session(**kw):
        assert kw['on_decision'](dict(status='DECISION_RETAINED',
                                    decision={'status':'DECISION_REQUIRES_APPROVAL',
                                        'execution_constraints':{'valid_until_ms':2**63-1}}))
        return dict(status='INPUTS_UNRESOLVED', phase='STRATEGY_OBSERVATION')
    monkeypatch.setattr(tick.controller,'run',session)
    monkeypatch.setattr(tick.dispatcher,'run',lambda **kw:sends.append(kw) or {'status':'QUERY_ONLY'})
    result=tick.run_loop(source_commit='c'*40,execute_approved=True,max_ticks=3)
    assert result['status']=='OBSERVATION_REQUIRES_ATTENTION'
    assert len(sends)==1 and len(result['ticks'])==2
    assert result['ticks'][0]['execution']['status']=='QUERY_ONLY'


@pytest.mark.parametrize('outcome', ['approved', 'expired', 'lost_response'])
def test_waits_for_same_decision_only_before_original_deadline(monkeypatch, outcome):
    from tools import gold_strategy_tick as tick
    from types import SimpleNamespace
    clock=[1000]
    observed=dict(status='DECISION_RETAINED',decision={'status':'DECISION_REQUIRES_APPROVAL',
        'execution_constraints':{'valid_until_ms':1250}})
    calls=[];waits=[]
    def dispatch(**kw):
        assert clock[0] < 1250
        calls.append(kw)
        if outcome=='lost_response': return {'status':'INPUTS_UNRESOLVED'}
        if outcome=='approved' and len(calls)==2: return {'status':'EXECUTOR_RESULT_RECORDED'}
        return dict(status='WAITING_INDEPENDENT_APPROVAL',valid_until_ms=1250)
    def sleep(seconds):
        waits.append(seconds);clock[0]+=round(seconds*1000)
    monkeypatch.setattr(tick,'time',SimpleNamespace(monotonic=lambda:clock[0]/1000,
        time_ns=lambda:clock[0]*1000000,sleep=sleep))
    monkeypatch.setattr(tick.dispatcher,'run',dispatch)
    result=tick.handle_observation(observed,source_commit='c'*40,execute_approved=True)
    assert all(c==dict(source_commit='c'*40,execute=True,use_approved_policy=True,
                      result_sha256=_digest(observed)) for c in calls)
    if outcome=='approved':
        assert len(calls)==2 and result['status']=='APPROVED_TASK_HANDOFF_COMPLETE'
    elif outcome=='expired':
        assert clock[0]==1250 and result['status']=='OBSERVED_ACTION_REQUIRES_INDEPENDENT_APPROVAL'
    else:
        assert len(calls)==1 and waits==[] and result['status']=='DISPATCH_REQUIRES_ATTENTION'


@pytest.mark.parametrize('flat',[False,True])
def test_pilot_stop_reports_remaining_position_as_attention(monkeypatch,flat):
    from tools import gold_strategy_tick as tick
    decision=dict(status='PILOT_STOPPED',position_flat=flat,pilot_control=dict(
        status='STOPPED',configuration_sha256='a'*64,submission_enabled=False))
    monkeypatch.setattr(tick.controller,'run',lambda **kw:dict(status='DECISION_RETAINED',decision=decision))
    monkeypatch.setattr(tick.dispatcher,'run',lambda **kw:pytest.fail('unresolved stopped pilot dispatched'))
    result=tick.run(source_commit='c'*40,execute_approved=True,require_pilot=True)
    assert result['status']==('PILOT_STOPPED' if flat else 'PILOT_EXIT_REQUIRES_ATTENTION')


@pytest.mark.parametrize('action,allowed', [('REDUCE_LONG', True), ('REDUCE_SHORT', True),
                                           ('OPEN_LONG', False), ('OPEN_SHORT', False)])
def test_stopped_pilot_handoff_accepts_reduction_direction_only(monkeypatch, action, allowed):
    from tools import gold_strategy_tick as tick
    observation = dict(status='DECISION_RETAINED', decision=dict(status='DECISION_REQUIRES_APPROVAL',
        action=action, position_flat=False, pilot_control=dict(status='STOPPED',
        configuration_sha256='a'*64, submission_enabled=False)))
    calls = []
    monkeypatch.setattr(tick, 'dispatch_retained', lambda *a, **kw: calls.append(1) or {'status': 'QUERY_ONLY'})
    result = tick.handle_observation(observation, source_commit='c'*40, execute_approved=True, require_pilot=True)
    assert calls == ([1] if allowed else [])
    assert result['status'] == ('APPROVED_TASK_HANDOFF_COMPLETE' if allowed else 'PILOT_EXIT_REQUIRES_ATTENTION')
