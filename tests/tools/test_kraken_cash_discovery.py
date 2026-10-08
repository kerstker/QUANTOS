import json

import pytest

from tools import kraken_account_snapshot as tool
from tests.data.test_kraken_cash_reconciliation import samples, NOW
from tests.data.test_kraken_order_query import body
from tests.data.test_kraken_trade_query import trade_body


def setup(tmp_path):
    opening,closing,pages=samples()
    for row in (opening,closing):
        row['open_orders']=[];row['result_hash']=tool.digest(row)
    class Reader:
        account_ref_sha256='a'*64
        mode='normal'
        calls=[]
        def read(self,endpoint,params):
            self.calls.append(endpoint)
            if endpoint=='Ledgers':
                return next(p['body'].encode() for p in pages if p['params']['aclass']==params['aclass'])
            if endpoint=='QueryOrders':
                if self.mode=='interrupt_order':raise KeyboardInterrupt
                return body('canceled').encode()
            assert endpoint=='QueryTrades'
            if self.mode=='transport':raise OSError('private transport detail')
            if self.mode=='interrupt_trade':raise KeyboardInterrupt
            value=trade_body();value['result']['TRADE-1']['time']=str(NOW//1000+30)
            if self.mode=='missing':value['result']={}
            if self.mode=='bad_id':value['result']['TRADE-1']['ordertxid']='bad/id'
            if self.mode=='wrong_order':value['result']['TRADE-1']['ordertxid']='OTHER-ORDER'
            return json.dumps(value).encode()
    reader=Reader()
    cash=tool.collect_cash_bridge(reader,tmp_path,opening,closing,clock_ms=lambda:NOW+63000)
    reader.calls=[]
    return reader,cash,opening,closing


@pytest.mark.parametrize('mode',['missing','bad_id','wrong_order','transport'])
def test_discovery_failure_cannot_restore_previous_success(tmp_path,mode):
    reader,cash,_,_=setup(tmp_path)
    initial=tool.track_orders(reader,tmp_path,[],clock_ms=lambda:NOW+63000,cash=cash)
    assert initial['status']=='ORDERS_OBSERVED'
    reader.mode=mode
    failed=tool.track_orders(reader,tmp_path,[],clock_ms=lambda:NOW+64000,cash=cash)
    assert failed['status']=='UNRESOLVED' and failed['order_ids']
    raw=(tmp_path/'order-recovery.json').read_text()
    assert 'private transport detail' not in raw
    assert json.loads(raw)==failed
    assert failed['next_cycle_allowed'] is False


@pytest.mark.parametrize('mode',['interrupt_trade','interrupt_order'])
def test_discovery_interruption_stays_unresolved_and_resumes(tmp_path,mode):
    reader,cash,_,_=setup(tmp_path);reader.mode=mode
    with pytest.raises(KeyboardInterrupt):
        tool.track_orders(reader,tmp_path,[],clock_ms=lambda:NOW+63000,cash=cash)
    pending=json.loads((tmp_path/'order-recovery.json').read_text())
    assert pending['status']=='UNRESOLVED'
    if mode=='interrupt_order':assert pending['order_ids']==['ORDER-1']
    reader.mode='normal'
    restored=tool.track_orders(reader,tmp_path,[],clock_ms=lambda:NOW+64000,
                               cash=cash if mode=='interrupt_trade' else None)
    assert restored['status']=='ORDERS_OBSERVED' and restored['order_ids']==['ORDER-1']
    assert len(list((tmp_path/'trade-observations').glob('*.json')))==1
    assert restored['full_history_observed'] is False


def test_corrupt_cash_source_is_rejected_before_discovery_network(tmp_path):
    reader,cash,_,_=setup(tmp_path)
    path=tmp_path/'cash-windows'/(cash['evidence_hash']+'.json')
    value=json.loads(path.read_text());value['report']['ledger_entries']=[]
    path.write_text(json.dumps(value))
    result=tool.track_orders(reader,tmp_path,[],clock_ms=lambda:NOW+63000,cash=cash)
    assert result['status']=='UNRESOLVED' and reader.calls==[]


def test_auto_cash_session_stops_on_missing_discovery_trade(tmp_path):
    reader,_,opening,closing=setup(tmp_path)
    # This session has an existing valid baseline, as on a process restart.
    tool.atomic_json(tmp_path/'cash-baseline.json',opening)
    reader.mode='missing'
    result=tool.run_session(reader,tmp_path,auto_cash=True,cycles=2,
        collect_fn=lambda _:closing,clock_ms=lambda:NOW+63000,
        sleep_fn=lambda _:pytest.fail('must stop on unresolved discovery'))
    assert result['cycles_completed']==1 and result['stop_reason']=='ORDER_QUERY_UNRESOLVED'
    assert result['non_stable_cycles']==1


def test_stale_cash_cannot_start_discovery_requests(tmp_path):
    reader,cash,_,_=setup(tmp_path)
    result=tool.track_orders(reader,tmp_path,[],clock_ms=lambda:NOW+200000,cash=cash)
    assert result['status']=='UNRESOLVED' and reader.calls==[]


def test_cash_order_discovery_is_independent_of_callers_decimal_precision(tmp_path):
    from decimal import localcontext
    reader,cash,_,_=setup(tmp_path)
    with localcontext() as context:
        context.prec=6
        recovered=tool.track_orders(reader,tmp_path,[],clock_ms=lambda:NOW+63000,cash=cash)
    assert recovered['status']=='ORDERS_OBSERVED' and recovered['order_ids']==['ORDER-1']


def test_empty_cash_window_needs_no_trade_or_order_lookup(tmp_path):
    from copy import deepcopy
    _,_,opening,_=setup(tmp_path)
    closing=deepcopy(opening);closing.pop('result_hash')
    closing['observed_at_ms']+=60000
    for read in closing['reads']:
        read['requested_at_ms']+=60000;read['received_at_ms']+=60000
    closing['result_hash']=tool.digest(closing)
    class EmptyReader:
        account_ref_sha256='a'*64
        calls=[]
        def read(self,endpoint,params):
            self.calls.append(endpoint)
            assert endpoint=='Ledgers'
            return b'{"error":[],"result":{"count":0,"ledger":{}}}'
    reader=EmptyReader()
    cash=tool.collect_cash_bridge(reader,tmp_path,opening,closing,clock_ms=lambda:NOW+63000)
    result=tool.track_orders(reader,tmp_path,[],clock_ms=lambda:NOW+63000,cash=cash)
    assert result['status']=='NO_TRACKED_ORDERS' and reader.calls==['Ledgers','Ledgers']
