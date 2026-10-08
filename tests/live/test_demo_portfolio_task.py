from copy import deepcopy

import pytest

from live.execution.demo_batch_risk import _digest
from live.execution import demo_portfolio_task as bridge
from services.research.ai_factory import HOUR
from services.research.ai_factory_forward import forecast_document
from services.research.ai_factory_store import digest
from tests.live.test_demo_request_inputs import inputs


def declarations(kwargs,prediction):
    now=kwargs['now_ms'];slot=(now-300000)//HOUR*HOUR
    forecast=forecast_document(slot=slot,available=slot+1,issued=slot+2,activated=slot-HOUR,
        model_hash='a'*64,input_hash='b'*64,features=[0.,0.,0.,.001,0.],
        prediction=prediction,threshold_bps=0,decision_policy='DISCRETE_COST_RISK_V1')
    forecast['result_hash']=digest(forecast)
    allocation={'schema':'DEMO_PORTFOLIO_ALLOCATION_V1','source_commit':'c'*40,
        'symbol':'BTCUSDC','account_ref_sha256':kwargs['origin']['account_ref_sha256'],
        'model_hash':'a'*64,'capital':'500','fee_bps':4,'slip_bps':5,'ioc_offset_bps':5}
    profile=kwargs['profile'];policy=kwargs['issuer_policy'];sid=bridge.strategy_id(allocation)
    old=profile['strategy']['strategy_id'];profile['strategy']['strategy_id']=sid
    profile['attestations'][sid]=profile['attestations'].pop(old)
    profile['attestations'][sid]['strategy_id']=sid
    policy['bindings'].update(strategy_id=sid,strategy_approval_sha256=_digest(profile['strategy']),
                              validation_sha256=_digest(profile['attestations']))
    funding=[{'time':slot-i*8*HOUR,'available_at_ms':slot-i*8*HOUR+1,'rate':'0'} for i in (3,2,1)]
    return allocation,forecast,funding


@pytest.mark.parametrize('prediction,side',[(.01,'BUY'),(-.01,'SELL'),(0.,None)])
def test_frozen_portfolio_target_reaches_original_task_and_risk_gate(tmp_path,monkeypatch,prediction,side):
    kwargs,opened,log=inputs(tmp_path,monkeypatch)
    try:
        kwargs.pop('orders')
        allocation,forecast,funding=declarations(kwargs,prediction)
        profile=kwargs['profile']
        result=bridge.build(allocation=allocation,forecast=forecast,funding=funding,**kwargs)
        assert not result['submission_enabled'] and not result['economic_authority']
        if side is None:
            assert result['task'] is None and result['status']=='NO_CHANGE'
        else:
            assert result['task']['schema']=='DEMO_REQUEST_TASK_V1'
            assert result['task']['orders'][0]['side']==side
            assert result['request_build']['precheck_status']=='RISK_CHECKS_PASSED'
            assert result['task']['profile']==profile
        changed=deepcopy(allocation);changed['capital']='600'
        with pytest.raises(ValueError,match='PORTFOLIO_ALLOCATION_NOT_APPROVED'):
            bridge.build(allocation=changed,forecast=forecast,funding=funding,**kwargs)
        assert opened[0].snapshot()==[] and opened[2].orders()==[] and log.snapshot()==[]
    finally:
        log.close()
        for store in opened:store.close()


@pytest.mark.parametrize('closing',[False,True])
def test_reversal_reduces_original_held_position_before_new_opening(tmp_path,monkeypatch,closing):
    from tests.live.test_demo_position_context import completed,held_inputs,close
    from tests.live.test_demo_issuer import policy_for
    from tests.live.test_demo_request_inputs import profile
    from tests.tools.test_binance_demo_market_inputs import capture,NOW
    from live.execution import demo_authority,demo_pre_submit
    batch,values,stream,events,_=completed(tmp_path,monkeypatch)
    try:
        held=held_inputs(batch,values);stores={k:values[k] for k in ('dispatch','recovery','oms')}
        original=demo_pre_submit.proposal(**held,**stores)
        policy=policy_for(original,demo_authority.load_registry_for_executor())
        market,_=capture()
        kwargs=dict(origin=held['origin'],market_inputs=market,profile=profile(held,market),
            issuer_policy=policy,kill_state=held['risk_inputs']['kill_switch'].state,now_ms=NOW+10,**stores)
        allocation,forecast,funding=declarations(kwargs,-.01)
        before=demo_pre_submit._state(**stores)
        result=(bridge.build_close(allocation=allocation,index=1,**kwargs) if closing else
            bridge.build(allocation=allocation,forecast=forecast,funding=funding,**kwargs))
        assert result['status']==('CLOSE_TASK_PREPARED' if closing else 'REDUCE_BEFORE_REVERSE')
        orders=result['task']['orders']
        assert len(orders)==1 and orders[0]['reduceOnly'] is True
        assert orders[0]['side']=='SELL' and orders[0]['quantity']=='0.002'
        assert result['request_build']['request']['risk']['account']['day_pnl']=='-0.054'
        assert demo_pre_submit._state(**stores)==before
    finally:
        events.close();close(values,stream)
