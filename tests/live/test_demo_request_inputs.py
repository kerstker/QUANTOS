"""Observed request construction feeds original gates without resetting state."""
from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from live.execution import demo_request_inputs as builder,demo_pre_submit
from live.execution.demo_batch_risk import _json_value
from live.execution.demo_session_inputs import decode
from services.research import demo_market_inputs as market_model
from tests.live.test_demo_issuer import fixture
from tests.tools.test_binance_demo_market_inputs import capture,NOW


def profile(values,market):
    risk=values['risk_inputs']
    return {'schema':'DEMO_REQUEST_PROFILE_V1','environment':'BINANCE_USDM_DEMO','symbol':'BTCUSDC',
        'market_source_sha256':market['source_sha256'],'volatility_policy':market_model.VOLATILITY_POLICY,
        'native_limits':_json_value(asdict(risk['native_limits'])),'strategy':risk['strategy'].model_dump(mode='json'),
        'mandate':_json_value(asdict(values['mandate'])),
        'attestations':_json_value({sid:asdict(a) for sid,a in values['attestations'].items()}),
        'initial_day_pnl':risk['account']['day_pnl'],'initial_peak_equity':risk['account']['peak_equity'],
        'minimum_plan_turnover':0}


def inputs(tmp_path,monkeypatch):
    _,values,opened,_,_,registry,policy,log=fixture(tmp_path,monkeypatch)
    market,_=capture()
    kwargs={'orders':values['orders'],'origin':values['origin'],'market_inputs':market,
        'profile':profile(values,market),'issuer_policy':policy,'kill_state':values['risk_inputs']['kill_switch'].state,
        'dispatch':opened[0],'recovery':opened[1],'oms':opened[2],'now_ms':NOW+10}
    return kwargs,opened,log


def test_observed_request_runs_original_risk_proposal_without_order_or_cash_writes(tmp_path,monkeypatch):
    kwargs,opened,log=inputs(tmp_path,monkeypatch)
    try:
        result=builder.build(**kwargs)
        decoded=decode(json.dumps(result['request']).encode(),now_ms=NOW+10)
        proposal=demo_pre_submit.proposal(**decoded,dispatch=opened[0],recovery=opened[1],oms=opened[2])
        assert proposal['risk']['status']=='RISK_CHECKS_PASSED'
        assert proposal['risk']['input_snapshot']['market']['daily_volume_base']=='123.456'
        assert result['market_inputs_sha256'] and result['request_sha256']
        assert result['request']['mandate']==kwargs['profile']['mandate']
        assert result['request']['attestations']==kwargs['profile']['attestations']
        assert opened[0].snapshot()==[] and opened[2].orders()==[] and log.snapshot()==[]
        assert not result['submission_enabled'] and not result['economic_authority']
    finally:
        log.close()
        for s in opened:s.close()


@pytest.mark.parametrize('bad',['stale_market','wrong_symbol','source','method','limits','approval','mandate','validation','account','expired','peak'])
def test_incomplete_or_unapproved_inputs_do_not_generate_an_execution_request(tmp_path,monkeypatch,bad):
    kwargs,opened,log=inputs(tmp_path,monkeypatch)
    if bad=='stale_market':kwargs['now_ms']+=30001
    elif bad=='wrong_symbol':kwargs['profile']['symbol']='BTCUSDT'
    elif bad=='source':kwargs['profile']['market_source_sha256']='f'*64
    elif bad=='method':kwargs['profile']['volatility_policy']='FORECAST_OR_ANNUALIZED'
    elif bad=='limits':kwargs['profile']['native_limits']['max_leverage']=100
    elif bad=='approval':kwargs['profile']['strategy']['approved']=False
    elif bad=='mandate':kwargs['profile']['mandate']['approved_by']=['AUTO']
    elif bad=='validation':kwargs['profile']['attestations']={}
    elif bad=='account':kwargs['issuer_policy']['account_ref_sha256']='f'*64
    elif bad=='expired':kwargs['issuer_policy']['valid_until_ms']=NOW
    elif bad=='peak':kwargs['profile']['initial_peak_equity']='-1'
    try:
        with pytest.raises(ValueError):builder.build(**kwargs)
        assert opened[0].snapshot()==[] and opened[2].orders()==[]
    finally:
        log.close()
        for s in opened:s.close()


def test_existing_pending_order_prevents_preparation_without_changing_its_identity(tmp_path,monkeypatch):
    from quantos.contracts.trading import OrderSide
    kwargs,opened,log=inputs(tmp_path,monkeypatch)
    try:
        opened[2].record_submission('QDpending0001','BTCUSDC',OrderSide.BUY,.001,
            strategy_id='existing',submitted_at=NOW-1000,status='NEW',order_type='LIMIT',limit_price=60000)
        before=opened[2].orders()
        with pytest.raises(ValueError):builder.build(**kwargs)
        assert opened[2].orders()==before and opened[0].snapshot()==[]
    finally:
        log.close()
        for s in opened:s.close()


def test_held_request_preserves_original_loss_basis_and_peak_instead_of_profile_defaults(tmp_path,monkeypatch):
    from tests.live.test_demo_position_context import completed,held_inputs,close
    from tests.live.test_demo_issuer import policy_for
    from live.execution import demo_authority
    batch,values,stream,events,_=completed(tmp_path,monkeypatch)
    try:
        held=held_inputs(batch,values);stores={k:values[k] for k in ('dispatch','recovery','oms')}
        original=demo_pre_submit.proposal(**held,**stores)
        policy=policy_for(original,demo_authority.load_registry_for_executor())
        market,_=capture();declared=profile(held,market)
        declared.update(initial_day_pnl='999',initial_peak_equity='999999')
        before=demo_pre_submit._state(**stores)
        result=builder.build(orders=held['orders'],origin=held['origin'],market_inputs=market,
            profile=declared,issuer_policy=policy,kill_state=held['risk_inputs']['kill_switch'].state,
            now_ms=NOW+10,**stores)
        account=result['request']['risk']['account']
        assert account['cash']=='4993.946' and account['equity']=='4999.946'
        assert account['day_pnl']=='-0.054' and account['peak_equity']=='5000'
        assert account['positions']==[{'symbol':'BTCUSDC','quantity':'0.002','mark_price':'60000'}]
        assert result['position_context_sha256'] and result['request']['orders']==held['orders']
        assert demo_pre_submit._state(**stores)==before
    finally:
        events.close();close(values,stream)
