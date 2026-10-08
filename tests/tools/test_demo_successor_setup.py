"""Finite renewal retains original risk history and requires a new enrollment."""
from copy import deepcopy
from decimal import Decimal
import json
import pytest

from tools import demo_operator_setup as setup
from tests.tools.test_demo_operator_setup import stop_bytes
from tests.research.test_demo_account_recovery import inputs
from tests.live.test_demo_submission_verification import material
from tests.tools.test_binance_demo_execution_origin import NOW


def fixture(tmp_path):
    value=inputs(tmp_path);origin=value['closing'];private,registry,*_=material()
    registry['keys'][0].update(valid_from_ms=NOW-10000,valid_until_ms=NOW-1)
    from services.research.demo_origin_contract import contract_from_origin
    context=dict(schema='DEMO_CANONICAL_POSITION_CONTEXT_V1',account_ref_sha256=origin['account_ref_sha256'],symbol='BTCUSDC',
        quantity='0',entry_notional='0',canonical_wallet='4999.6801',initial_equity='5000',initial_day_pnl='0',
        initial_plan_turnover=0,peak_equity_floor='5000',available_at_ms=NOW+30000,day_id=NOW//86400000,
        cash_flow_policy='REPORTED_FILLS_ONLY_CONTINUATION_WALLET_MUST_MATCH_V1',source_verified=False,
        ledger_state_hash='f'*64,capacity_consumed={'original':'239.8'},contract=contract_from_origin(value['opening'])[0],
        adv_notional='1000000',participation_rate='0.001',reported_account_prefix={'manual_order_ids':['22']})
    return origin,registry,private,context


def test_new_enrollment_preserves_expired_keys_and_original_risk_floor(tmp_path):
    origin,registry,private,context=fixture(tmp_path);before=deepcopy(registry)
    spec=setup.propose(origin=origin,registry=registry,source_commit='c'*40,now_ms=NOW+60000,position_context=context)
    approval=dict(schema='DEMO_OPERATOR_ENROLLMENT_V1',spec_sha256=setup.digest(spec),operator_id='isolated-operator',
                  approved_at_ms=NOW+60000,purpose=setup.PURPOSE)
    _,files=setup.assemble(spec=spec,approval=approval,origin=origin,registry=registry,private_key=private,
        stop_image=stop_bytes(tmp_path),now_ms=NOW+60000,position_context=context)
    expanded=json.loads(files['registry.json']);profile=json.loads(files['profile.json'])
    assert expanded['keys'][:-1]==before['keys'] and registry==before
    assert profile['initial_day_pnl']=='-0.3199' and profile['initial_peak_equity']=='5000'
    assert Decimal(str(profile['minimum_plan_turnover']))>=Decimal('239.8')/Decimal('4999.6801')
    assert json.loads(files['policy.json'])['max_orders']==3


@pytest.mark.parametrize('damage',['missing_context','active_key','wallet','held','future','old_day'])
def test_continuation_never_bypasses_original_account_or_active_mandate(tmp_path,damage):
    origin,registry,private,context=fixture(tmp_path)
    if damage=='missing_context':context=None
    elif damage=='active_key':registry['keys'][0]['valid_until_ms']=NOW+100000
    elif damage=='wallet':context['canonical_wallet']='5000'
    elif damage=='held':context['quantity']='0.002'
    elif damage=='future':context['available_at_ms']=NOW+100000
    else:context['day_id']-=1
    with pytest.raises(ValueError):setup.propose(origin=origin,registry=registry,source_commit='c'*40,
        now_ms=NOW+60000,position_context=context)


@pytest.mark.parametrize('mode',['same_day','next_day','wrong_day','unresolved'])
def test_successor_uses_protected_day_capture_only_when_needed(monkeypatch,mode):
    from tools import demo_successor_setup as successor,demo_functional_cycle
    calls=[];today=NOW//86400000
    class Runtime:
        def __init__(self,*a):pass
        def invoke(self,operation,**kw):
            calls.append(operation)
            if operation=='context':return dict(status='CANONICAL_CONTEXT',context={'day_id':today if mode=='same_day' else today-1})
            return dict(status='UNRESOLVED' if mode=='unresolved' else 'CANONICAL_CONTEXT',
                context={'day_id':today-1 if mode=='wrong_day' else today})
        def close(self):calls.append('close')
    monkeypatch.setattr(demo_functional_cycle,'Runtime',Runtime)
    monkeypatch.setattr(successor.installer,'now_ms',lambda:NOW)
    if mode in ('wrong_day','unresolved'):
        with pytest.raises(ValueError):successor.context_for('c'*40,{},'a'*64)
    else:assert successor.context_for('c'*40,{},'a'*64)['day_id']==today
    assert calls==(['context','close'] if mode=='same_day' else ['context','day-checkpoint','close'])


@pytest.mark.parametrize('initial_pnl,day_open,expected',[('0','4999.6801','-0.3199'),('10','5002','-2.3199')])
def test_new_day_enrollment_keeps_conservative_daily_and_original_account_loss(tmp_path,initial_pnl,day_open,expected):
    from tests.live.test_demo_day_round_trip import observation
    origin,registry,private,context=fixture(tmp_path)
    at=NOW+86400000+60000
    origin=observation(origin,at,'4999.6801')
    context.update(day_id=at//86400000,day_checkpoint_sha256='d'*64,day_open_equity=day_open,initial_day_pnl=initial_pnl)
    spec=setup.propose(origin=origin,registry=registry,source_commit='c'*40,now_ms=at,position_context=context)
    approval=dict(schema='DEMO_OPERATOR_ENROLLMENT_V1',spec_sha256=setup.digest(spec),operator_id='isolated-operator',
        approved_at_ms=at,purpose=setup.PURPOSE)
    _,files=setup.assemble(spec=spec,approval=approval,origin=origin,registry=registry,private_key=private,
        stop_image=stop_bytes(tmp_path),now_ms=at,position_context=context)
    assert json.loads(files['profile.json'])['initial_day_pnl']==expected
