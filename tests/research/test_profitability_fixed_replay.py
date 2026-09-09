from decimal import Decimal as D
import pytest
from tests.execution.test_perpetual_simulation import run,frame,T
from services.execution.perpetual_simulation import OrderIntent,CancelOrder
from services.research.profitability_fixed_replay import fixed_action_strategy,normalized_trajectory


def test_delayed_cancel_can_lose_to_fill_without_changing_client_action_identity():
    frames=(frame(0),frame(5,quotes=()),frame(10,size='.1'),frame(15,size='.1'),frame(20))
    schedule=[(T,OrderIntent('a','BTCUSDT',D('.2'))),(T+5,CancelOrder('a'))]
    immediate=fixed_action_strategy(schedule,frames,cancel_latency_ms=0)
    delayed=fixed_action_strategy(schedule,frames,cancel_latency_ms=10)
    assert immediate.action_hash==delayed.action_hash
    a=run(frames,immediate);b=run(frames,delayed)
    ta=normalized_trajectory(a,schedule,immediate.action_hash)
    tb=normalized_trajectory(b,schedule,delayed.action_hash)
    assert ta['orders'][0]['events'][-1]['state']=='CANCELLED'
    assert ta['orders'][0]['events'][-1]['filled']=='0'
    assert tb['orders'][0]['events'][-1]['state']=='FILLED'
    assert tb['orders'][0]['events'][-1]['filled']=='0.2'
    assert any(e['state']=='PARTIAL' for e in tb['orders'][0]['events'])
    assert b['reconciliation']['residual']=='0'


def test_fixed_actions_reject_silent_loss_and_unknown_cancel():
    frames=(frame(0),frame(10))
    with pytest.raises(ValueError):fixed_action_strategy([(T+11,OrderIntent('a','BTCUSDT',D('.01')))],frames)
    with pytest.raises(ValueError):fixed_action_strategy([(T,CancelOrder('absent'))],frames)


def test_replay_wrapper_binds_latency_and_reuses_canonical_engine(monkeypatch):
    from services.research import profitability_fixed_replay as module
    captured=[]
    def engine(**kw):
        captured.append(kw['strategy_identity'])
        return run(kw['frames'],kw['strategy'])
    monkeypatch.setattr(module,'run_linear_perpetual_simulation',engine)
    frames=(frame(0),frame(5,quotes=()),frame(10),frame(20))
    result=module.run_fixed_actions(schedule=[(T,OrderIntent('a','BTCUSDT',D('.2'))),
        (T+5,CancelOrder('a'))],frames=frames,cancel_latency_ms=10)
    assert captured[0]['cancel_latency_ms']==10
    assert result['economic_eligible'] is False
    assert result['trajectory']['source']=='CANONICAL_SIMULATOR_NOT_VENUE'
    assert result['trajectory']['cancel_responses']


def test_normalized_trace_preserves_client_cancel_before_delayed_delivery():
    from services.research.profitability_trajectory import compare_trajectories
    frames=(frame(0),frame(5,quotes=()),frame(10,size='.1'),frame(15,size='.1'),frame(20))
    schedule=[(T,OrderIntent('a','BTCUSDT',D('.2'))),(T+5,CancelOrder('a'))]
    strategy=fixed_action_strategy(schedule,frames,cancel_latency_ms=10)
    trace=normalized_trajectory(run(frames,strategy),schedule,strategy.action_hash)
    assert [{'at_ms':T+5,'state':'CANCEL_SENT','filled':'0'}]==[e for e in trace['orders'][0]['events'] if e['state']=='CANCEL_SENT']
    assert compare_trajectories(trace,trace)['unmatched_events']==0
