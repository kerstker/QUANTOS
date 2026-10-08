"""Persisted control state only; no order sender or alternative cash ledger."""
import json

import pytest


@pytest.mark.parametrize('trigger', ['loss','expiry'])
def test_stop_survives_recovery_restart_and_configuration_change(tmp_path,monkeypatch,trigger):
    from live.execution import demo_gold_pilot as pilot
    from live.execution import demo_position_context as position
    monkeypatch.setattr(position,'_ROOT',tmp_path)
    monkeypatch.setattr(position,'_custody',lambda *a,**k:None)
    config=dict(schema='GOLD_DEMO_PILOT_CONTROL_V1',environment='BINANCE_USDM_DEMO',symbol='XAUUSDT',
        account_ref_sha256='a'*64,opening_origin_sha256='b'*64,initial_equity='5000',
        started_at_ms=1000,ends_at_ms=86401000,max_loss_fraction='0.05',submission_enabled=False)
    area=tmp_path/'gold-pilot';area.mkdir()
    (area/'configuration.json').write_text(json.dumps(config))
    account=dict(account_ref_sha256='a'*64,equity='5000')
    assert pilot.observe(account,now_ms=2000,origin_sha256='c'*64)['status']=='ACTIVE'
    if trigger=='loss':account['equity']='4750'
    at=3000 if trigger=='loss' else config['ends_at_ms']
    result=pilot.observe(account,now_ms=at,origin_sha256='d'*64)
    assert result['status']=='STOPPED' and result['reason']==('LOSS_LIMIT' if trigger=='loss' else 'DURATION_COMPLETE')
    stopped=(area/'stopped.json').read_bytes()
    account['equity']='5500'
    assert pilot.observe(account,now_ms=at+1,origin_sha256='e'*64)==result
    assert (area/'stopped.json').read_bytes()==stopped
    with pytest.raises(ValueError,match='PILOT_STOPPED'):
        pilot.require_opening_allowed([dict(reduceOnly=False)],result)
    pilot.require_opening_allowed([dict(reduceOnly=True)],result)
    config['initial_equity']='5500'
    (area/'configuration.json').write_text(json.dumps(config))
    with pytest.raises(ValueError,match='CONFIGURATION_CHANGED'):
        pilot.observe(account,now_ms=at+2,origin_sha256='f'*64)


def test_missing_partial_configuration_does_not_fall_back_to_legacy(tmp_path,monkeypatch):
    from live.execution import demo_gold_pilot as pilot
    from live.execution import demo_position_context as position
    monkeypatch.setattr(position,'_ROOT',tmp_path)
    monkeypatch.setattr(position,'_custody',lambda *a,**k:None)
    assert pilot.observe({},now_ms=1000,origin_sha256='a'*64) is None
    (tmp_path/'gold-pilot').mkdir()
    with pytest.raises((ValueError,FileNotFoundError)):
        pilot.observe({},now_ms=1000,origin_sha256='a'*64)


@pytest.mark.parametrize('damage',['expiry','missing_directory'])
def test_prepared_order_expiring_pilot_does_not_claim_or_access_credentials(tmp_path,monkeypatch,damage):
    from live.execution import demo_position_context as position
    from live.execution import demo_pre_submit as prep, demo_prepared_execution as executor
    from live.execution.demo_batch_risk import _digest
    from live.exchange import binance_demo_exact as transport
    from tests.live.test_gold_demo_preparation import setup
    from tests.live.test_demo_pre_submit import stores,signed,NOW
    private,envelope,values=setup(tmp_path,monkeypatch)
    dispatch,recovery,oms=stores(tmp_path)
    monkeypatch.setattr(position,'_ROOT',tmp_path)
    monkeypatch.setattr(position,'_custody',lambda *a,**k:None)
    area=tmp_path/'gold-pilot';area.mkdir()
    config=dict(schema='GOLD_DEMO_PILOT_CONTROL_V1',environment='BINANCE_USDM_DEMO',symbol='XAUUSDT',
        account_ref_sha256=values['origin']['account_ref_sha256'],opening_origin_sha256=_digest(values['origin']),
        initial_equity=values['risk_inputs']['account']['equity'],started_at_ms=NOW+5-86400000,
        ends_at_ms=NOW+5,max_loss_fraction='0.05',submission_enabled=False)
    (area/'configuration.json').write_text(json.dumps(config))
    kwargs=dict(values,dispatch=dispatch,recovery=recovery,oms=oms)
    try:
        proposal=prep.proposal(**kwargs)
        batch=prep.prepare(signed(private,envelope,proposal),evidence_directory=tmp_path/'evidence',**kwargs)
        assert len(dispatch.snapshot())==1 and not (area/'stopped.json').exists()
        monkeypatch.setattr(executor,'_require_state_custody',lambda *a,**k:None)
        if damage=='missing_directory':area.rename(tmp_path/'saved-pilot')
        monkeypatch.setattr(executor,'now_ms',lambda:NOW+6 if damage=='expiry' else NOW+1)
        monkeypatch.setattr(transport,'_credentials',lambda *a:pytest.fail('expired pilot accessed credentials'))
        monkeypatch.setattr(transport,'_http_once',lambda *a:pytest.fail('expired pilot sent order'))
        args={k:v for k,v in kwargs.items() if k not in ('orders','origin','now_ms')}
        with pytest.raises(executor.DemoExecutionStopped):executor.execute(batch,**args)
        if damage=='expiry':assert json.loads((area/'stopped.json').read_bytes())['reason']=='DURATION_COMPLETE'
        assert dispatch.wire_snapshot()==[] and len(dispatch.snapshot())==1
        assert len(recovery.pending())==1 and not oms.fills()
    finally:
        for store in (dispatch,recovery,oms):store.close()
