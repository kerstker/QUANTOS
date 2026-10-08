import json
from decimal import Decimal as D

import pytest

from tests.tools.test_gold_session_prefix import inputs
from tools.gold_session_prefix import snapshot


def setup(tmp_path,monkeypatch):
    quotes,bars,capture,_=inputs(tmp_path,monkeypatch)
    snapshot(quotes,bars,tmp_path/'full')
    # First snapshot uses the real first closed bar and its following fill quote.
    records=sorted(quotes.glob('receipt-*.json'))
    later={p.name:p.read_bytes() for p in records[11:]}
    for p in records[11:]:
        p.unlink()
    snapshot(quotes,bars,tmp_path/'first')
    for name,raw in later.items():
        (quotes/name).write_bytes(raw)
    args=dict(exchange_after=capture['exchange_after'],release_identity={'fixture':True},
        quantities={k:D('.1') for k in ('channel','trend','reversion')},initial_collateral=D('1000'),
        fee_rate=D('.001'),max_notional=D('100'))
    return args


def test_account_chain_preserves_filled_account_prefix_and_initial_equity(tmp_path,monkeypatch):
    from tools.gold_account_chain import advance
    args=setup(tmp_path,monkeypatch)
    first=advance(tmp_path/'chain',tmp_path/'first',**args)
    second=advance(tmp_path/'chain',tmp_path/'full',**args)
    assert second['previous_transition_hash']==first['transition_hash']
    assert second['sequence']==2
    a=json.loads((tmp_path/'chain/000001/report.json').read_text())
    b=json.loads((tmp_path/'chain/000002/report.json').read_text())
    assert a['simulation']['orders'][0]['filled_at'] is not None
    assert b['simulation']['equity_history'][:len(a['simulation']['equity_history'])]==a['simulation']['equity_history']
    assert b['simulation']['reconciliation']['initial_equity']=='1000'
    assert b['simulation']['reconciliation']['residual']=='0'
    with pytest.raises(ValueError,match='NOT_EXTENDED'):
        advance(tmp_path/'chain',tmp_path/'full',**args)


def test_account_chain_rejects_parameter_reset(tmp_path,monkeypatch):
    from tools.gold_account_chain import advance
    args=setup(tmp_path,monkeypatch)
    advance(tmp_path/'chain',tmp_path/'first',**args)
    args['initial_collateral']=D('2000')
    with pytest.raises(ValueError,match='CONFIGURATION_CHANGED'):
        advance(tmp_path/'chain',tmp_path/'full',**args)
    assert not (tmp_path/'chain/000002').exists()


def test_changed_ledger_prefix_keeps_last_good_transition(tmp_path,monkeypatch):
    from tools import gold_account_chain as tool
    args=setup(tmp_path,monkeypatch)
    tool.advance(tmp_path/'chain',tmp_path/'first',**args)
    previous=(tmp_path/'chain/000001/transition.json').read_bytes()
    original=tool.replay
    def changed(*a,**kw):
        result=original(*a,**kw)
        result['simulation']['equity_history'][0]['equity']='2000'
        return result
    monkeypatch.setattr(tool,'replay',changed)
    with pytest.raises(ValueError,match='LEDGER_PREFIX_CHANGED'):
        tool.advance(tmp_path/'chain',tmp_path/'full',**args)
    assert (tmp_path/'chain/000001/transition.json').read_bytes()==previous
    assert not (tmp_path/'chain/000002').exists()
    assert len(list((tmp_path/'chain').glob('attempt-*/failure.json')))==1


def test_fixed_settlement_configuration_extends_through_actual_receipt(tmp_path,monkeypatch):
    from copy import deepcopy
    from tools.gold_account_chain import advance
    from tests.execution.test_perpetual_public_replay import START, sealed
    quotes,bars,capture,_=inputs(tmp_path,monkeypatch)
    nominal=START+2500
    capture['bootstrap']['funding']['payload'][0]['fundingTime']=nominal-14400000
    for sample in capture['samples']:
        sample['mark']['payload']['nextFundingTime']=(nominal if sample['mark']['received_at']<nominal else nominal+14400000)
    capture=sealed(capture)
    records=[capture['bootstrap'][k] for k in ('exchange','funding','candles')]
    for sample in capture['samples']:
        records.extend((sample['mark'],sample['depth']))
    for index,row in enumerate(records):
        (quotes/f'receipt-{index:06d}.json').write_text(json.dumps(row)+'\n')
    snapshot(quotes,bars,tmp_path/'funded')
    for index in range(9,len(records)):
        (quotes/f'receipt-{index:06d}.json').unlink()
    snapshot(quotes,bars,tmp_path/'before')
    args=dict(exchange_after=capture['exchange_after'],release_identity={'fixture':True},
        quantities={k:D('.1') for k in ('channel','trend','reversion')},initial_collateral=D('1000'),
        fee_rate=D('.001'),max_notional=D('100'),settlement_window_ms=1000)
    first=advance(tmp_path/'chain',tmp_path/'before',**args)
    with pytest.raises(ValueError,match='funding'):
        advance(tmp_path/'chain',tmp_path/'funded',**args)
    assert not (tmp_path/'chain/000002').exists()
    record=deepcopy(capture['bootstrap']['funding'])
    record.update(requested_at=START+3500,received_at=START+3600,
        payload=[dict(symbol='XAUUSDT',fundingTime=nominal+2,fundingRate='0.01',markPrice='100')])
    record=sealed({'record':record})['record']
    second=advance(tmp_path/'chain',tmp_path/'funded',observed_funding_records=[record],**args)
    assert second['previous_transition_hash']==first['transition_hash']
    result=json.loads((tmp_path/'chain/000002/report.json').read_text())
    assert D(result['simulation']['reconciliation']['funding'])!=0
    assert result['simulation']['reconciliation']['residual']=='0'
    assert result['simulation']['funding_availability_policy']=='CANCEL_AND_PAUSE_UNTIL_RECEIPT_V1'
