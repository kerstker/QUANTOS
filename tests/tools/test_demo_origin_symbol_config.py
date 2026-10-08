"""Account caps must survive capture and bound each possible partial batch."""
import json
from copy import deepcopy

import pytest

from tools import binance_demo_execution_origin as tool
from tests.tools.test_binance_demo_execution_origin import collect, transport, credentials, NOW
from services.research.demo_origin_contract import contract_from_origin


def configs(raw):
    return [r for r in raw['reads'] if r['kind'].startswith('symbols')]


def legacy(raw, schema='DEMO_EXECUTION_ORIGIN_V3'):
    raw=deepcopy(raw);raw['schema']=schema
    for r in configs(raw):r.pop('target_config',None)
    return raw


def test_same_sixteen_reads_retain_both_exact_symbol_caps_and_modes(tmp_path):
    raw,calls=collect(tmp_path)
    assert raw['schema']=='DEMO_EXECUTION_ORIGIN_V4' and len(calls)==16
    before,after=configs(raw)
    assert before['target_config']==after['target_config']=={
        'symbol':'BTCUSDC','marginType':'CROSSED','leverage':20,
        'maxNotionalValue':'10000','isAutoAddMargin':False}
    assert set(after['data'][-1])=={'symbol','marginType','leverage'}
    r=tool.assess(raw,as_of_ms=NOW)
    assert r['target_configuration']['status']=='MATCHED_OBSERVATIONS'
    assert r['target_configuration']['after']==after['target_config']
    old=legacy(raw)
    assert tool.validate(old)==old
    assert 'target_configuration' not in tool.assess(old,as_of_ms=NOW)


@pytest.mark.parametrize('change,reason',[
    ('cap_change','TARGET_CONFIG_CHANGED'),('auto','TARGET_AUTO_MARGIN_UNSUPPORTED'),
    ('zero_cap','TARGET_NOTIONAL_CAP_UNAVAILABLE')])
def test_new_account_limits_do_not_hide_behind_old_equal_leverage(tmp_path,change,reason):
    raw,_=collect(tmp_path)
    if change=='cap_change':configs(raw)[1]['target_config']['maxNotionalValue']='120'
    else:
        for r in configs(raw):r['target_config']['isAutoAddMargin' if change=='auto' else 'maxNotionalValue']=True if change=='auto' else '0'
    r=tool.assess(raw,as_of_ms=NOW)
    assert r['status']=='UNRESOLVED' and reason in r['issues']


@pytest.mark.parametrize('change',['missing','number_cap','leverage_mismatch','symbol_mismatch','extra'])
def test_missing_or_unbound_config_is_not_accepted_as_v4(tmp_path,change):
    raw,_=collect(tmp_path);r=configs(raw)[0]
    if change=='missing':r.pop('target_config',None)
    elif change=='number_cap':r['target_config']['maxNotionalValue']=10000
    elif change=='leverage_mismatch':r['target_config']['leverage']=3
    elif change=='symbol_mismatch':r['target_config']['symbol']='BTCUSDT'
    else:r['target_config']['unexpected']=True
    with pytest.raises(ValueError):tool.validate(raw)


def test_missing_target_is_retained_as_unknown_without_losing_other_symbols(tmp_path):
    get,_=transport()
    def missing(path,**kw):
        data=get(path,**kw)
        if path=='/fapi/v1/symbolConfig':
            return json.dumps([s for s in json.loads(data) if s['symbol']!='BTCUSDC']).encode()
        return data
    raw=tool.collect(credential_file=credentials(tmp_path),symbol='BTCUSDC',operator_read=True,get=missing,clock_ms=lambda:NOW)
    assert raw['status']=='CAPTURED' and len(raw['reads'])==16
    assert all(r['target_config'] is None for r in configs(raw))
    r=tool.assess(raw,as_of_ms=NOW)
    assert r['target_configuration']['status']=='INCOMPLETE' and 'TARGET_CONFIG_MISSING' in r['issues']


def test_contract_uses_lower_symbol_cap_and_old_evidence_does_not_acquire_it(tmp_path):
    raw,_=collect(tmp_path)
    for r in configs(raw):r['target_config']['maxNotionalValue']='120.000000000000000001'
    spec,facts=contract_from_origin(raw)
    assert spec['new_risk_notional_cap']=='120.000000000000000001'
    assert facts['symbol_config_notional_cap']=='120.000000000000000001'
    assert facts['symbol_config_cap_observed'] is True and facts['source_verified'] is False
    old_spec,old_facts=contract_from_origin(legacy(raw))
    assert old_spec['new_risk_notional_cap']=='10000'
    assert old_facts['schema']=='DEMO_ORIGIN_CONTRACT_PROJECTION_V1'
    assert old_facts['symbol_config_cap_verified'] is False


def test_opposite_orders_cannot_net_out_the_venue_cap_before_filling(tmp_path,monkeypatch):
    from live.execution import demo_pre_submit as submit
    from tests.live.test_demo_pre_submit import setup,stores
    _,_,kw=setup(tmp_path,monkeypatch);ds,rs,oms=stores(tmp_path)
    try:
        # Individual notionals 120 and 60; one-sided partial fills can reach 180.
        for r in configs(kw['origin']):r['target_config']['maxNotionalValue']='179.999999999999999999'
        order=kw['orders'][0];order['quantity']='0.002'
        kw['orders']=[order,dict(order,newClientOrderId='QDtest000002',quantity='0.001'),
            dict(order,newClientOrderId='QDtest000003',side='SELL')]
        with pytest.raises(submit.DemoPreparationRejected,match='DEMO_VENUE_NOTIONAL_CAP'):
            submit.proposal(**kw,dispatch=ds,recovery=rs,oms=oms)
        assert ds.snapshot()==[] and rs.latest_records()==() and oms.orders()==[]
    finally:
        for s in (ds,rs,oms):s.close()


def test_old_symbol_cap_is_required_for_new_proposals_without_rewriting_history(tmp_path,monkeypatch):
    from live.execution import demo_pre_submit as submit
    from tests.live.test_demo_pre_submit import setup,stores
    _,_,kw=setup(tmp_path,monkeypatch);kw['origin']=legacy(kw['origin']);ds,rs,oms=stores(tmp_path)
    try:
        with pytest.raises(submit.DemoPreparationRejected,match='DEMO_VENUE_ACCOUNT_TERMS_REQUIRED'):
            submit.proposal(**kw,dispatch=ds,recovery=rs,oms=oms)
        assert ds.snapshot()==[] and oms.orders()==[]
    finally:
        for s in (ds,rs,oms):s.close()


def test_unusable_margin_table_cannot_supply_a_plausible_positive_cap(tmp_path,monkeypatch):
    from live.execution import demo_pre_submit as submit
    from tests.live.test_demo_pre_submit import setup,stores
    _,_,kw=setup(tmp_path,monkeypatch);ds,rs,oms=stores(tmp_path)
    first=next(r['data'][0]['brackets'][0] for r in kw['origin']['reads'] if r['kind']=='brackets')
    first['notionalFloor']='1'
    try:
        with pytest.raises(submit.DemoPreparationRejected,match='DEMO_VENUE_ACCOUNT_TERMS_UNSUPPORTED'):
            submit.proposal(**kw,dispatch=ds,recovery=rs,oms=oms)
        assert ds.snapshot()==[] and oms.orders()==[]
    finally:
        for s in (ds,rs,oms):s.close()


def test_exact_boundary_and_mark_valuation_are_not_rounded_to_a_pass(tmp_path,monkeypatch):
    from live.execution import demo_pre_submit as submit
    from tests.live.test_demo_pre_submit import setup
    _,_,kw=setup(tmp_path,monkeypatch)
    for r in configs(kw['origin']):r['target_config']['maxNotionalValue']='120'
    submit._venue_account_cap(kw['orders'],kw['origin'])
    for r in configs(kw['origin']):r['target_config']['maxNotionalValue']='119.999999999999999999'
    with pytest.raises(submit.DemoPreparationRejected,match='DEMO_VENUE_NOTIONAL_CAP'):
        submit._venue_account_cap(kw['orders'],kw['origin'])
    for r in configs(kw['origin']):r['target_config']['maxNotionalValue']='120'
    next(r['data'] for r in kw['origin']['reads'] if r['kind']=='mark')['markPrice']='60001'
    with pytest.raises(submit.DemoPreparationRejected,match='DEMO_VENUE_NOTIONAL_CAP'):
        submit._venue_account_cap(kw['orders'],kw['origin'])
