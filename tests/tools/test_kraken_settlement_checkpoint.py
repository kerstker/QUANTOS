import json

import pytest

from tools import kraken_account_snapshot as account
from tools import kraken_settlement_checkpoint as checkpoint
from tests.tools.test_kraken_cash_discovery import setup, NOW
from tests.tools.test_kraken_public_capture import capture


def settled(root):
    reader,cash,opening,closing=setup(root)
    account.atomic_json(root/'cash-baseline.json',opening)
    account.save(root,closing)
    recovery=account.track_orders(reader,root,[],cash=cash,clock_ms=lambda:NOW+63000)
    market=root/'market';market.mkdir()
    checkpoint.initialise(root,opening,market_root=market)
    return reader,cash,recovery,closing


def test_settled_checkpoint_retires_orders_and_restarts_from_closing_balance(tmp_path):
    reader,cash,recovery,closing=settled(tmp_path)
    original=(tmp_path/'cash-baseline.json').read_bytes()
    saved=checkpoint.advance(tmp_path,cash,recovery,[capture()[0]['instrument']],as_of_ms=NOW+64000)
    assert saved['generation']==1 and saved['submission_enabled'] is False
    state=checkpoint.load(tmp_path,reader.account_ref_sha256)
    assert state['opening']==closing and state['retired_ids']==['ORDER-1']
    reader.calls=[]
    recovered=account.track_orders(reader,tmp_path,[],clock_ms=lambda:NOW+65000)
    assert recovered['status']=='NO_TRACKED_ORDERS' and reader.calls==[]
    assert (tmp_path/'cash-baseline.json').read_bytes()==original
    assert len(list((tmp_path/'trade-observations').glob('*.json')))==1


def test_checkpoint_cannot_advance_with_unresolved_or_tampered_settlement(tmp_path):
    _,cash,recovery,_=settled(tmp_path)
    path=tmp_path/'cash-windows'/(cash['evidence_hash']+'.json')
    value=json.loads(path.read_text());value['report']['assets']['ZUSD']['fees']='0'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        checkpoint.advance(tmp_path,cash,recovery,[capture()[0]['instrument']],as_of_ms=NOW+64000)
    assert checkpoint.load(tmp_path,'a'*64)['generation']==0


def test_checkpoint_database_deletion_cannot_reset_settled_history(tmp_path):
    reader,cash,recovery,_=settled(tmp_path)
    checkpoint.advance(tmp_path,cash,recovery,[capture()[0]['instrument']],as_of_ms=NOW+64000)
    (tmp_path/'cash-checkpoints.sqlite').unlink()
    with pytest.raises(ValueError,match='MISSING'):
        account.track_orders(reader,tmp_path,[],clock_ms=lambda:NOW+65000)


def test_automatic_session_advances_and_next_session_uses_new_window(tmp_path):
    from copy import deepcopy
    from tests.tools.test_kraken_inventory_report import fixture
    reader,_,_,closing=settled(tmp_path)
    fixture(tmp_path)
    first=account.run_session(reader,tmp_path,auto_cash=True,collect_fn=lambda _:closing,
        clock_ms=lambda:NOW+64000)
    assert first['non_stable_cycles']==0
    assert checkpoint.load(tmp_path,'a'*64)['generation']==1
    later=deepcopy(closing);later.pop('result_hash');later['observed_at_ms']+=60000
    for read in later['reads']:
        read['requested_at_ms']+=60000;read['received_at_ms']+=60000
    later['result_hash']=account.digest(later)
    calls=[]
    class Reader:
        account_ref_sha256='a'*64
        def read(self,endpoint,params):
            calls.append((endpoint,params))
            assert endpoint=='Ledgers'
            assert params['start']==closing['reads'][0]['requested_at_ms']//1000
            return b'{"error":[],"result":{"count":0,"ledger":{}}}'
    next_cycle=account.run_session(Reader(),tmp_path,auto_cash=True,collect_fn=lambda _:later,
        clock_ms=lambda:NOW+124000)
    assert next_cycle['non_stable_cycles']==0 and len(calls)==2
    assert json.loads((tmp_path/'order-recovery.json').read_text())['order_ids']==[]
    assert len(list((tmp_path/'trade-observations').glob('*.json')))==1


def test_failed_checkpoint_transaction_rolls_back_and_can_retry(tmp_path,monkeypatch):
    import sqlite3
    _,cash,recovery,_=settled(tmp_path)
    connect=sqlite3.connect
    class Interrupted(sqlite3.Connection):
        def execute(self,sql,*args,**kwargs):
            result=super().execute(sql,*args,**kwargs)
            if sql.startswith('INSERT INTO checkpoints'):raise KeyboardInterrupt
            return result
    with monkeypatch.context() as patch:
        patch.setattr(checkpoint.sqlite3,'connect',lambda *a,**k:connect(*a,**k,factory=Interrupted))
        with pytest.raises(KeyboardInterrupt):
            checkpoint.advance(tmp_path,cash,recovery,[capture()[0]['instrument']],as_of_ms=NOW+64000)
    assert checkpoint.load(tmp_path,'a'*64)['generation']==0
    assert checkpoint.advance(tmp_path,cash,recovery,[capture()[0]['instrument']],as_of_ms=NOW+64000)['generation']==1


def test_retired_order_reappearing_in_open_orders_is_rejected(tmp_path):
    reader,cash,recovery,_=settled(tmp_path)
    checkpoint.advance(tmp_path,cash,recovery,[capture()[0]['instrument']],as_of_ms=NOW+64000)
    reader.calls=[]
    with pytest.raises(ValueError,match='REAPPEARED'):
        account.track_orders(reader,tmp_path,[{'order_id':'ORDER-1'}],clock_ms=lambda:NOW+65000)
    assert reader.calls==[]


def test_deleted_committed_tail_is_detected_by_head_marker(tmp_path):
    import sqlite3
    reader,cash,recovery,_=settled(tmp_path)
    checkpoint.advance(tmp_path,cash,recovery,[capture()[0]['instrument']],as_of_ms=NOW+64000)
    with sqlite3.connect(tmp_path/'cash-checkpoints.sqlite') as conn:
        conn.execute('DELETE FROM checkpoints WHERE generation=1')
    with pytest.raises(ValueError,match='HEAD_MISMATCH'):
        checkpoint.load(tmp_path,reader.account_ref_sha256)


def test_interruption_after_commit_recovers_once_and_keeps_cash_blocked(tmp_path,monkeypatch):
    from tests.tools.test_kraken_inventory_report import fixture
    reader,cash,recovery,closing=settled(tmp_path);fixture(tmp_path)
    write=checkpoint.atomic_json
    def interrupt(path,value):
        if path.name=='cash-checkpoints-required.json' and value.get('generation')==1:raise KeyboardInterrupt
        write(path,value)
    with monkeypatch.context() as patch:
        patch.setattr(checkpoint,'atomic_json',interrupt)
        with pytest.raises(KeyboardInterrupt):
            checkpoint.refresh(tmp_path,cash,recovery,market_root=tmp_path/'market',as_of_ms=NOW+64000)
    state=checkpoint.load(tmp_path,'a'*64)
    assert state['generation']==1 and state['opening']==closing
    assert json.loads((tmp_path/'cash-reconciliation.json').read_text())['status']=='UNRESOLVED'
    reader.calls=[]
    assert account.track_orders(reader,tmp_path,[],clock_ms=lambda:NOW+65000)['order_ids']==[]
    assert reader.calls==[]


def test_cycle_waits_for_cash_window_after_checkpoint_advance(tmp_path):
    from tests.tools.test_kraken_portfolio_cycle import setup as cycle_setup, inputs, cycle
    _,cash,recovery,_=settled(tmp_path)
    checkpoint.advance(tmp_path,cash,recovery,[capture()[0]['instrument']],as_of_ms=NOW+64000)
    args=cycle_setup(tmp_path);args['account_path']=tmp_path/'latest.json';args['clock_ms']=lambda:NOW+65000
    args['policy_path'].write_text(json.dumps(inputs()[1]))
    report=cycle.run_once(**args)
    assert report['status']=='BLOCKED' and 'CHECKPOINT_REFRESH_REQUIRED' in report['blockers']
    assert report['execution_status']=='DISABLED'


def test_continuous_empty_account_crosses_twenty_four_hours_without_reset(tmp_path):
    from services.data.kraken_account import assess
    from tests.data.test_kraken_account import observations
    from tests.data.test_kraken_units import source
    from tests.data.test_kraken_quotes import source as quotes
    from tools import kraken_portfolio_observer as observer
    market=tmp_path/'market';market.mkdir()
    config={'schema':'KRAKEN_OBSERVER_CONFIG_V1','interval':1,'poll_seconds':60,
            'instruments':[{'pair':'AAPLx/USD','asset_class':'tokenized_asset'}]}
    starts=[]
    class Reader:
        account_ref_sha256='a'*64
        def read(self,endpoint,params):
            assert endpoint=='Ledgers';starts.append(params['start'])
            return b'{"error":[],"result":{"count":0,"ledger":{}}}'
    initial_hash=None
    for index,hours in enumerate((0,13,26)):
        at=NOW+hours*3600000;reads=observations()
        for read in reads:
            read['requested_at_ms']+=hours*3600000;read['received_at_ms']+=hours*3600000
            if read['endpoint']=='OpenOrders':read['body']='{"error":[],"result":{"open":{}}}'
        report=assess(reads,as_of_ms=at+500);report['result_hash']=account.digest(report)
        observer.run_once(market,config,collect=lambda **_:capture(now=at)[0],
            metadata_get=lambda *_:json.dumps(source()).encode(),
            quote_get=lambda *_:json.dumps(quotes()).encode(),clock_ms=lambda:at+600)
        result=account.run_session(Reader(),tmp_path,auto_cash=True,settlement_market_root=market,
            collect_fn=lambda _:report,clock_ms=lambda:at+64000)
        assert result['non_stable_cycles']==0
        state=checkpoint.load(tmp_path,'a'*64)
        assert state['generation']==index and state['opening']==report
        original=json.loads((tmp_path/'cash-baseline.json').read_text())['result_hash']
        if index==0:initial_hash=original
        assert original==initial_hash
    assert starts==[NOW//1000]*2+[(NOW+13*3600000)//1000]*2


def test_initialisation_transaction_interruption_can_resume(tmp_path,monkeypatch):
    import sqlite3
    _,_,opening,_=setup(tmp_path)
    account.atomic_json(tmp_path/'cash-baseline.json',opening)
    market=tmp_path/'market';market.mkdir()
    connect=sqlite3.connect
    class Interrupted(sqlite3.Connection):
        def execute(self,sql,*args,**kwargs):
            result=super().execute(sql,*args,**kwargs)
            if sql.startswith('CREATE TABLE checkpoints'):raise KeyboardInterrupt
            return result
    with monkeypatch.context() as patch:
        patch.setattr(checkpoint.sqlite3,'connect',lambda *a,**k:connect(*a,**k,factory=Interrupted))
        with pytest.raises(KeyboardInterrupt):checkpoint.initialise(tmp_path,opening,market_root=market)
    with pytest.raises(ValueError,match='MARKER_MISSING'):checkpoint.load(tmp_path,'a'*64)
    state=checkpoint.initialise(tmp_path,opening,market_root=market)
    assert state['generation']==0 and state['opening']==opening


def test_refresh_cannot_replace_a_newer_cash_pointer(tmp_path):
    _,cash,recovery,_=settled(tmp_path)
    newer={**cash,'status':'UNRESOLVED'};newer.pop('result_hash');newer['result_hash']=account.digest(newer)
    account.atomic_json(tmp_path/'cash-reconciliation.json',newer)
    with pytest.raises(ValueError,match='SOURCE_CHANGED'):
        checkpoint.refresh(tmp_path,cash,recovery,market_root=tmp_path/'market',as_of_ms=NOW+64000)
    assert json.loads((tmp_path/'cash-reconciliation.json').read_text())==newer
    assert checkpoint.load(tmp_path,'a'*64)['generation']==0


def test_process_exit_mid_transaction_recovers_before_account_reads(tmp_path):
    import subprocess
    import sys
    reader,_,_,closing=settled(tmp_path)
    script='''
import os, sqlite3, sys
conn=sqlite3.connect(sys.argv[1])
conn.execute('PRAGMA synchronous=FULL')
conn.execute('PRAGMA cache_size=1')
conn.execute('BEGIN IMMEDIATE')
conn.execute('INSERT INTO checkpoints VALUES (?,?)',(1,'x'*200000))
os._exit(42)
'''
    result=subprocess.run([sys.executable,'-B','-c',script,str(tmp_path/'cash-checkpoints.sqlite')],check=False)
    assert result.returncode==42 and (tmp_path/'cash-checkpoints.sqlite-journal').exists()
    def stop(_):raise RuntimeError('STOP_AFTER_RECOVERY')
    with pytest.raises(RuntimeError,match='STOP_AFTER_RECOVERY'):
        account.run_session(reader,tmp_path,auto_cash=True,collect_fn=stop,clock_ms=lambda:NOW+64000)
    assert checkpoint.load(tmp_path,'a'*64)['generation']==0


def test_catalog_identity_can_be_fresh_while_trading_candles_are_old(tmp_path):
    from tests.data.test_kraken_units import source
    from tests.data.test_kraken_quotes import source as quotes
    from tools import kraken_portfolio_observer as observer
    at=NOW+13*3600000
    config={'schema':'KRAKEN_OBSERVER_CONFIG_V1','interval':1,'poll_seconds':60,
            'instruments':[{'pair':'AAPLx/USD','asset_class':'tokenized_asset'}]}
    observed=observer.run_once(tmp_path,config,collect=lambda **_:capture(now=at)[0],
        metadata_get=lambda *_:json.dumps(source()).encode(),
        quote_get=lambda *_:json.dumps(quotes()).encode(),clock_ms=lambda:at+600)
    assert observed['status']=='PARTIAL' and observed['instruments'][0]['status']=='STALE'
    assert checkpoint.catalog_specs(tmp_path,as_of_ms=at+64000)==[capture()[0]['instrument']]
    with pytest.raises(ValueError,match='STALE'):
        checkpoint.catalog_specs(tmp_path,as_of_ms=at+121000)


@pytest.mark.parametrize('kind',['undiscovered','nonterminal'])
def test_cash_balance_match_alone_cannot_advance_checkpoint(tmp_path,kind):
    from tests.data.test_kraken_order_query import body
    reader,cash,opening,closing=setup(tmp_path)
    account.atomic_json(tmp_path/'cash-baseline.json',opening);account.save(tmp_path,closing)
    market=tmp_path/'market';market.mkdir();checkpoint.initialise(tmp_path,opening,market_root=market)
    class Nonterminal:
        account_ref_sha256='a'*64
        def read(self,endpoint,params):
            return body('open').encode() if endpoint=='QueryOrders' else reader.read(endpoint,params)
    recovery=account.track_orders(Nonterminal(),tmp_path,[],clock_ms=lambda:NOW+63000,
                                  cash=cash if kind=='nonterminal' else None)
    with pytest.raises(ValueError):
        checkpoint.advance(tmp_path,cash,recovery,[capture()[0]['instrument']],as_of_ms=NOW+64000)
    assert checkpoint.load(tmp_path,'a'*64)['generation']==0


@pytest.mark.parametrize('reuse_ledger_id',[False,True])
def test_next_filled_order_settles_without_requerying_or_recounting_retired_order(tmp_path,reuse_ledger_id):
    from tests.data.test_kraken_order_query import body
    from tests.data.test_kraken_trade_query import trade_body
    from tests.data.test_kraken_units import source
    from tests.data.test_kraken_quotes import source as quotes
    from tools import kraken_portfolio_observer as observer
    _,cash,recovery,closing=settled(tmp_path)
    checkpoint.advance(tmp_path,cash,recovery,[capture()[0]['instrument']],as_of_ms=NOW+64000)
    # Account observations store read metadata rather than raw payloads. Build
    # the next complete observation from the same explicit API fixtures.
    from tests.data.test_kraken_cash_reconciliation import samples
    from tests.data.test_kraken_account import observations
    reads=observations()
    for read in reads:
        read['requested_at_ms']+=120000;read['received_at_ms']+=120000
        value=json.loads(read['body'])
        if read['endpoint']=='OpenOrders':value['result']['open']={}
        else:
            value['result']['ZUSD']['balance']='981.93800001'
            value['result']['AAPLx.T']['balance']='2.323456789012345678'
        read['body']=json.dumps(value)
    later=account.model.assess(reads,as_of_ms=NOW+120500);later['result_hash']=account.digest(later)
    pages=samples()[2]
    for page in pages:
        value=json.loads(page['body']);row=next(iter(value['result']['ledger'].values()))
        row.update(refid='TRADE-2',time=str(NOW//1000+90))
        if row['aclass']=='currency':row.update(amount='22',fee='0.022',balance='981.93800001')
        else:row.update(amount='-0.2',fee='0',balance='2.323456789012345678')
        if not reuse_ledger_id:value['result']['ledger']={'L2-'+row['aclass']:row}
        page['body']=json.dumps(value)
    config={'schema':'KRAKEN_OBSERVER_CONFIG_V1','interval':1,'poll_seconds':60,
            'instruments':[{'pair':'AAPLx/USD','asset_class':'tokenized_asset'}]}
    observer.run_once(tmp_path/'market',config,collect=lambda **_:capture(now=NOW+120000)[0],
        metadata_get=lambda *_:json.dumps(source()).encode(),
        quote_get=lambda *_:json.dumps(quotes()).encode(),clock_ms=lambda:NOW+120600)
    queried=[]
    class Reader:
        account_ref_sha256='a'*64
        def read(self,endpoint,params):
            if endpoint=='Ledgers':return next(p['body'].encode() for p in pages if p['params']['aclass']==params['aclass'])
            queried.append((endpoint,params['txid']))
            if endpoint=='QueryOrders':
                row=json.loads(body('canceled'))['result']['ORDER-1']
                row.update(vol_exec='0.2',cost='22',fee='0.022',trades=['TRADE-2']);row['descr']['type']='sell'
                return json.dumps({'error':[],'result':{'ORDER-2':row}}).encode()
            row=trade_body()['result']['TRADE-1']
            row.update(ordertxid='ORDER-2',type='sell',time=str(NOW//1000+90),vol='0.2',price='110',cost='22',fee='0.022')
            return json.dumps({'error':[],'result':{'TRADE-2':row}}).encode()
    result=account.run_session(Reader(),tmp_path,auto_cash=True,collect_fn=lambda _:later,clock_ms=lambda:NOW+124000)
    if reuse_ledger_id:
        assert result['stop_reason']=='SETTLEMENT_CHECKPOINT_UNRESOLVED'
        assert checkpoint.load(tmp_path,'a'*64)['generation']==1
        return
    assert result['non_stable_cycles']==0
    state=checkpoint.load(tmp_path,'a'*64)
    assert state['generation']==2 and state['retired_ids']==['ORDER-1','ORDER-2']
    assert state['opening']['balances']['ZUSD']['balance']=='981.93800001'
    assert all('ORDER-1' not in ids and 'TRADE-1' not in ids for _,ids in queried)
    assert len(list((tmp_path/'trade-observations').glob('*.json')))==2
