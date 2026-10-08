"""Native Scope/baseline/admin/future proof reaches original risk and issuer."""
from copy import deepcopy
import json
import pytest
from live.execution.demo_batch_risk import _digest
from tests.live.test_gold_future_admission import (
    book as book,installed as installed,first as first,materials as materials,
    scope_inputs as scope_inputs,day_materials as day_materials,local as local,
    scope_records as scope_records,native_scope as native_scope,scope_admin_case as scope_admin_case,
    scope_observation_case,
)


def market_at(at):
    from tools import binance_demo_market_inputs as capture
    start=at-10;end=start//60000*60000-1
    bars=[]
    for index in range(61):
        opened=end+1-(61-index)*60000;price=str(4000+index)
        bars.append([opened,price,price,price,price,'2',opened+59999,'8000',3,'1','4000','0'])
    ticks=iter(range(start,start+100));calls=[]
    def get(kind,**kwargs):
        calls.append(kind)
        if kind.startswith('clock'):value={'serverTime':start+(6 if kind=='clock_after' else 0)}
        elif kind=='ticker':value=dict(symbol='XAUUSDT',openTime=start-86400000,closeTime=start,volume='100000',quoteVolume='400000000')
        else:value=bars
        return json.dumps(value).encode()
    market=capture.collect(symbol='XAUUSDT',get=get,clock_ms=lambda:next(ticks))
    assert market['status']=='CAPTURED' and len(calls)==4
    return market


def future_issuer_case(scope_admin_case,native_scope,monkeypatch):
    from live.execution import demo_request_inputs as builder,demo_gold_delegation_enable as enabled
    from live.execution.demo_execution_session import ExecutionSession
    from quantos.contracts.killswitch import KillSwitchState
    f=scope_observation_case(scope_admin_case,native_scope,monkeypatch)
    docs=f.signing['documents'];f.stores=native_scope['local']['value']['stores']
    f.current_enable=deepcopy(f.enable)
    def load(**kwargs):
        assert kwargs.pop('role') in ('executor','issuer')
        return enabled.validate(deepcopy(f.current_enable),**kwargs)
    monkeypatch.setattr(enabled,'load_enabled',load)
    f.market=market_at(f.now_ms)
    f.profile=deepcopy(docs['profile'])
    f.kill_state=KillSwitchState.model_validate_json(json.dumps(docs['dispatcher']['kill_switch']),strict=True)
    decision=f.proof['result']['decision']
    f.built=builder.build(orders=decision['draft']['orders'],origin=f.proof['origin'],market_inputs=f.market,
        profile=f.profile,issuer_policy=f.base_policy,kill_state=f.kill_state,
        execution_constraints=decision['execution_constraints'],delegated_admission=f.proof,
        delegation_context={'registry':f.registry,'enable':f.current_enable},now_ms=f.now_ms,
        **{k:f.stores[k] for k in ('dispatch','recovery','oms')})
    f.session=ExecutionSession(**{k:f.stores[k] for k in ('dispatch','recovery','oms')},evidence_directory=f.root/'future-session-evidence')
    f.handoff=f.session.propose(json.dumps(f.built['request']).encode(),now_ms=f.now_ms)
    f.private=f.admin['private'];f.log=f.admin['store']
    return f


def test_first_native_future_signal_reaches_independent_issuer_without_first_exact_policy(scope_admin_case,native_scope,monkeypatch):
    from live.execution import demo_issuer as issuer
    f=future_issuer_case(scope_admin_case,native_scope,monkeypatch)
    try:
        result=issuer.issue_delegated_submission(f.handoff,policy=f.base_policy,registry=f.registry,
            public_material=f.current_enable,key=f.private,store=f.log,now_ms=f.now_ms,clock_ms=lambda:f.now_ms)
        rows=f.log.snapshot()
        assert [r['kind'] for r in rows]==['GROUP_INITIALIZED','DELEGATION_V2_SIGNED','RESERVED','SIGNED']
        assert rows[-2]['payload']['delegation']['schema']=='DEMO_GOLD_DELEGATED_ISSUER_RESERVATION_V2'
        assert rows[-2]['payload']['delegation']['delegation_sha256']==_digest(f.delegation)
        assert result['proposal_sha256']==f.handoff['proposal_sha256']
        assert f.stores['dispatch'].snapshot()==f.proof['execution']['state']['reservations']
    finally:f.session.close()


@pytest.mark.parametrize('offsets',[(-1,0,1),(100,101,50)])
def test_native_future_signing_rejects_clock_regression_without_refunding_reservation(scope_admin_case,native_scope,monkeypatch,offsets):
    from live.execution import demo_issuer as issuer
    from live.execution.demo_issuer_policy import IssuerRejected
    f=future_issuer_case(scope_admin_case,native_scope,monkeypatch)
    ticks=iter(f.now_ms+offset for offset in offsets)
    try:
        with pytest.raises(IssuerRejected,match='CLOCK|CHANGED_OR_EXPIRED'):
            issuer.issue_delegated_submission(f.handoff,policy=f.base_policy,registry=f.registry,
                public_material=f.current_enable,key=f.private,store=f.log,now_ms=f.now_ms,clock_ms=lambda:next(ticks))
        rows=f.log.snapshot()
        assert [r['kind'] for r in rows]==['GROUP_INITIALIZED','DELEGATION_V2_SIGNED','RESERVED']
        assert rows[-1]['payload']['plan']['proposal_sha256']==f.handoff['proposal_sha256']
        assert f.stores['dispatch'].snapshot()==f.proof['execution']['state']['reservations']
    finally:f.session.close()
