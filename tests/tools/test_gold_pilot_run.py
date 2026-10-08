"""Finite supervisor consumes original observations and independently approved tasks."""
import pytest


def observed(status='OBSERVATION_COMPLETE',stop=False,flat=True):
    return dict(status=status,ticks=[dict(status=status,observation=dict(status='DECISION_RETAINED',
        decision=dict(status='NO_ENTRY_SIGNAL',position_flat=flat,execution_history_empty=True,pilot_control=dict(
            status='STOPPED' if stop else 'ACTIVE',configuration_sha256='a'*64,submission_enabled=False))))])


def test_no_signal_keeps_observing_until_executor_pilot_stops(monkeypatch):
    from tools import gold_pilot_run as runner
    results=iter([observed(),observed('OBSERVED_ACTION_REQUIRES_INDEPENDENT_APPROVAL'),observed('PILOT_STOPPED',True)])
    calls=[];clock=[0.0]
    monkeypatch.setattr(runner.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(runner.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    monkeypatch.setattr(runner.tick,'run_loop',lambda **kw:calls.append(kw) or next(results))
    result=runner.run(source_commit='c'*40,execute_approved=True,max_seconds=60,interval_seconds=2)
    assert result['status']=='PILOT_STOPPED' and result['cycles']==3
    assert all(c['require_pilot'] is True and c['max_ticks']==1 for c in calls)
    assert clock[0]==4


@pytest.mark.parametrize('status',['PILOT_EXIT_REQUIRES_ATTENTION','RECOVERY_REQUIRED','DISPATCH_REQUIRES_ATTENTION','PILOT_CONFIGURATION_REQUIRED'])
def test_supervisor_preserves_original_attention_without_retry(monkeypatch,status):
    from tools import gold_pilot_run as runner
    calls=[]
    monkeypatch.setattr(runner.tick,'run_loop',lambda **kw:calls.append(kw) or observed(status))
    monkeypatch.setattr(runner.time,'sleep',lambda _:pytest.fail('retry after original attention'))
    assert runner.run(source_commit='c'*40)['status']==status
    assert len(calls)==1


def test_process_time_budget_is_not_a_claim_of_flatness(monkeypatch):
    from tools import gold_pilot_run as runner
    clock=[0.0]
    monkeypatch.setattr(runner.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(runner.time,'sleep',lambda s:clock.__setitem__(0,clock[0]+s))
    monkeypatch.setattr(runner.tick,'run_loop',lambda **kw:observed(flat=False))
    result=runner.run(source_commit='c'*40,max_seconds=3,interval_seconds=2)
    assert result['status']=='SUPERVISOR_BUDGET_EXHAUSTED' and result['cycles']==2
    assert result['pilot_complete'] is False


def test_flat_stop_with_prior_execution_requires_cash_feedback(monkeypatch):
    from tools import gold_pilot_run as runner
    value=observed('PILOT_STOPPED',True)
    value['ticks'][0]['observation']['decision']['execution_history_empty']=False
    monkeypatch.setattr(runner.tick,'run_loop',lambda **kw:value)
    result=runner.run(source_commit='c'*40)
    assert result['status']=='PILOT_FEEDBACK_REQUIRES_ATTENTION' and not result['pilot_complete']


def test_supervisor_uses_original_tick_and_never_dispatches_no_signal(monkeypatch):
    from tools import gold_pilot_run as runner
    rows=iter([observed(),observed('PILOT_STOPPED',True)])
    calls=[]
    def controller(**kwargs):
        row=next(rows)['ticks'][0]['observation']
        row['decision']['status']='PILOT_STOPPED' if row['decision']['pilot_control']['status']=='STOPPED' else 'NO_ENTRY_SIGNAL'
        calls.append(row)
        kwargs['on_decision'](row)
        return row
    monkeypatch.setattr(runner.tick.controller,'run',controller)
    monkeypatch.setattr(runner.tick.dispatcher,'run',lambda **kw:pytest.fail('no signal dispatched'))
    monkeypatch.setattr(runner.time,'sleep',lambda _:None)
    result=runner.run(source_commit='c'*40,execute_approved=True,max_seconds=5)
    assert result['pilot_complete'] and len(calls)==2
