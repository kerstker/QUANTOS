import base64
import json

import pytest

from tools import kraken_account_snapshot as tool
from tests.data.test_kraken_account import observations, NOW


def test_only_readonly_signed_requests_and_monotonic_nonce_survive_restart(tmp_path):
    calls=[]
    def transport(url,body,headers):calls.append((url,body,headers));return b'{"error":[],"result":{}}'
    key=base64.b64encode(b'public fixture only').decode();secret=base64.b64encode(b'non-secret fixture only').decode()
    client=tool.ReadOnlyClient(key,secret,tmp_path,transport=transport,clock_ms=lambda:NOW)
    for endpoint in ('BalanceEx','OpenOrders'):client.read(endpoint,{'rebase_multiplier':'base'})
    restarted=tool.ReadOnlyClient(key,secret,tmp_path,transport=transport,clock_ms=lambda:NOW-100)
    restarted.read('BalanceEx',{'rebase_multiplier':'base'})
    from urllib.parse import parse_qs
    nonces=[int(parse_qs(body.decode())['nonce'][0]) for _,body,_ in calls]
    assert nonces==[NOW,NOW+1,NOW+2]
    for endpoint,params in [('AddOrder',{}),('Withdraw',{}),('BalanceEx',{'rebase_multiplier':'base','pair':'AAPLxUSD'})]:
        with pytest.raises(ValueError):client.read(endpoint,params)
    assert len(calls)==3 and all('/0/private/' in url for url,_,_ in calls)
    for p in tmp_path.iterdir():
        if p.is_file():assert key not in p.read_text() and secret not in p.read_text()


def test_collect_and_export_only_allowlisted_account_fields(tmp_path):
    reads=observations();sequence=iter(reads);ticks=iter(range(NOW,NOW+100))
    class Reader:
        account_ref_sha256='a'*64
        def read(self,endpoint,params):
            row=next(sequence);assert endpoint==row['endpoint'];return row['body'].encode()
    result=tool.collect(Reader(),clock_ms=lambda:next(ticks))
    assert result['status']=='OBSERVED_STABLE' and len(result['open_orders'])==1
    tool.save(tmp_path,result)
    content=(tmp_path/'latest.json').read_text()
    assert 'API-Key' not in content and 'API-Sign' not in content and '"body"' not in content
    assert json.loads(content)['cost_basis_status']=='UNKNOWN'
    assert (tmp_path/'latest.json').stat().st_mode & 0o777==0o600


def test_failure_exports_no_transport_details_or_secrets(tmp_path):
    class Reader:
        account_ref_sha256='a'*64
        def read(self,*_):raise OSError('sensitive transport detail')
    result=tool.collect(Reader(),clock_ms=lambda:NOW)
    assert result['status']=='FAILED' and 'sensitive' not in json.dumps(result)
    tool.save(tmp_path,result)
    assert json.loads((tmp_path/'latest.json').read_text())['status']=='FAILED'


def test_main_refuses_unattended_credential_prompt(monkeypatch):
    monkeypatch.setattr(tool.sys.stdin,'isatty',lambda:False)
    with pytest.raises(ValueError,match='INTERACTIVE_OPERATOR_REQUIRED'):tool.main(['--state-root','/unused'])


def test_api_rejection_stops_before_repeating_authenticated_requests():
    class Reader:
        account_ref_sha256='a'*64
        calls=0
        def read(self,*_):
            self.calls+=1
            return b'{"error":["EAPI:Invalid nonce"],"result":{}}'
    client=Reader();report=tool.collect(client,clock_ms=lambda:NOW)
    assert client.calls==1 and report['status']=='FAILED'
    assert report['failure']['endpoint']=='BalanceEx'


def test_bounded_session_reuses_client_and_refreshes_snapshot_without_reprompt(tmp_path):
    from services.data.kraken_account import assess
    class Reader:account_ref_sha256='a'*64
    reader=Reader();sleeps=[];events=[];calls=[]
    def collect(client):
        assert client is reader
        reads=observations();offset=len(calls)*60000
        for r in reads:
            r['requested_at_ms']+=offset;r['received_at_ms']+=offset
        report=assess(reads,as_of_ms=NOW+offset+500)
        report['result_hash']=tool.digest(report);calls.append(client)
        return report
    result=tool.run_session(reader,tmp_path,cycles=3,poll_seconds=60,collect_fn=collect,
                            sleep_fn=sleeps.append,on_report=events.append)
    assert result['cycles_completed']==3 and result['stop_reason']=='CYCLE_LIMIT'
    assert len(events)==3 and sleeps==[60,60]
    assert len(list((tmp_path/'observations').glob('*.json')))==3
    assert json.loads((tmp_path/'latest.json').read_text())['observed_at_ms']==NOW+120350


def test_session_stops_on_first_private_api_failure(tmp_path):
    class Reader:
        account_ref_sha256='a'*64
        calls=0
        def read(self,*_):
            self.calls+=1
            return b'{"error":["EAPI:Invalid key"],"result":{}}'
    reader=Reader()
    result=tool.run_session(reader,tmp_path,cycles=10,poll_seconds=60,
                            sleep_fn=lambda _:pytest.fail('must not sleep or retry'))
    assert result['cycles_completed']==1 and reader.calls==1
    assert result['stop_reason']=='ACCOUNT_READ_FAILED'
    assert json.loads((tmp_path/'latest.json').read_text())['status']=='FAILED'


def test_session_rejects_account_switch_or_concurrent_session_before_network(tmp_path):
    import fcntl
    class Reader:account_ref_sha256='a'*64
    def forbidden(_):pytest.fail('network must not be reached')
    (tmp_path/'account-reference.json').write_text(json.dumps({'account_ref_sha256':'b'*64}))
    with pytest.raises(ValueError):tool.run_session(Reader(),tmp_path,cycles=1,poll_seconds=60,collect_fn=forbidden)
    (tmp_path/'account-reference.json').unlink()
    with (tmp_path/'.session.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):tool.run_session(Reader(),tmp_path,cycles=1,poll_seconds=60,collect_fn=forbidden)


def test_failure_at_same_timestamp_supersedes_success_and_cannot_be_hidden(tmp_path):
    from services.data.kraken_account import assess
    stable=assess(observations(),as_of_ms=NOW+500);stable['result_hash']=tool.digest(stable)
    tool.save(tmp_path,stable)
    class Reader:
        account_ref_sha256='a'*64
        def read(self,*_):raise OSError('failed')
    failed=tool.collect(Reader(),clock_ms=lambda:stable['observed_at_ms'])
    tool.save(tmp_path,failed)
    assert json.loads((tmp_path/'latest.json').read_text())['status']=='FAILED'
    tool.save(tmp_path,stable)
    assert json.loads((tmp_path/'latest.json').read_text())['status']=='FAILED'


def test_order_tracking_survives_restart_and_missing_order_never_becomes_complete(tmp_path):
    from tests.data.test_kraken_order_query import body
    class Reader:
        account_ref_sha256='a'*64
        response=body()
        def read(self,endpoint,params):
            if endpoint=='QueryTrades':
                from tests.data.test_kraken_trade_query import trade_body
                return json.dumps(trade_body()).encode()
            assert endpoint=='QueryOrders' and params['txid']=='ORDER-1'
            return self.response.encode()
    reader=Reader()
    first=tool.track_orders(reader,tmp_path,[{'order_id':'ORDER-1'}],clock_ms=lambda:NOW)
    assert first['status']=='ORDERS_OBSERVED'
    reader.response=body('canceled')
    second=tool.track_orders(reader,tmp_path,[],clock_ms=lambda:NOW+1000)
    assert second['order_ids']==['ORDER-1'] and second['status']=='ORDERS_OBSERVED'
    assert second['trade_status']=='TRADE_TOTALS_MATCHED'
    assert len(list((tmp_path/'trade-observations').glob('*.json')))==1
    assert second['cash_reconciled'] is False and second['next_cycle_allowed'] is False
    reader.response='{"error":[],"result":{}}'
    failed=tool.track_orders(reader,tmp_path,[],clock_ms=lambda:NOW+2000)
    assert failed['status']=='UNRESOLVED' and failed['order_ids']==['ORDER-1']
    assert json.loads((tmp_path/'order-query-latest.json').read_text())['orders'][0]['status']=='canceled'


@pytest.mark.parametrize('interrupted_endpoint',['QueryOrders','QueryTrades'])
def test_interrupted_refresh_invalidates_old_success_and_can_resume(tmp_path,interrupted_endpoint):
    from tests.data.test_kraken_order_query import body
    from tests.data.test_kraken_trade_query import trade_body
    class Reader:
        account_ref_sha256='a'*64
        interrupt=False
        def read(self,endpoint,params):
            if self.interrupt and endpoint==interrupted_endpoint:
                raise KeyboardInterrupt()
            return body().encode() if endpoint=='QueryOrders' else json.dumps(trade_body()).encode()
    reader=Reader()
    first=tool.track_orders(reader,tmp_path,[{'order_id':'ORDER-1'}],clock_ms=lambda:NOW)
    assert first['status']=='ORDERS_OBSERVED'
    reader.interrupt=True
    with pytest.raises(KeyboardInterrupt):
        tool.track_orders(reader,tmp_path,[],clock_ms=lambda:NOW+1000)
    pending=json.loads((tmp_path/'order-recovery.json').read_text())
    assert pending['status']=='UNRESOLVED'
    assert pending['failure']=={'error_type':'REFRESH_IN_PROGRESS'}
    assert pending['order_ids']==['ORDER-1'] and pending['query_hash'] is None
    assert pending['result_hash']==tool.digest({k:v for k,v in pending.items() if k!='result_hash'})
    reader.interrupt=False
    resumed=tool.track_orders(reader,tmp_path,[],clock_ms=lambda:NOW+2000)
    assert resumed['status']=='ORDERS_OBSERVED' and resumed['failure'] is None
    assert resumed['result_hash']==tool.digest({k:v for k,v in resumed.items() if k!='result_hash'})


def test_interruption_between_recovery_and_tracking_write_retains_new_ids(tmp_path,monkeypatch):
    from tests.data.test_kraken_order_query import body
    from tests.data.test_kraken_trade_query import trade_body
    class Reader:
        account_ref_sha256='a'*64
        def read(self,endpoint,params):
            if endpoint=='QueryOrders':
                assert params['txid']=='ORDER-1'
                return body().encode()
            return json.dumps(trade_body()).encode()
    original=tool.atomic_json
    def interrupted(path,value):
        if path.name=='order-tracking.json':raise KeyboardInterrupt()
        original(path,value)
    monkeypatch.setattr(tool,'atomic_json',interrupted)
    with pytest.raises(KeyboardInterrupt):
        tool.track_orders(Reader(),tmp_path,[{'order_id':'ORDER-1'}],clock_ms=lambda:NOW)
    assert not (tmp_path/'order-tracking.json').exists()
    monkeypatch.setattr(tool,'atomic_json',original)
    resumed=tool.track_orders(Reader(),tmp_path,[],clock_ms=lambda:NOW+1000)
    assert resumed['status']=='ORDERS_OBSERVED' and resumed['order_ids']==['ORDER-1']


def test_query_orders_signer_accepts_only_bounded_read_scope(tmp_path):
    calls=[]
    key=base64.b64encode(b'public fixture only').decode();secret=base64.b64encode(b'non-secret fixture only').decode()
    client=tool.ReadOnlyClient(key,secret,tmp_path,transport=lambda *args:calls.append(args),clock_ms=lambda:NOW)
    client.read('QueryOrders',{'txid':'ORDER-1','trades':'true','rebase_multiplier':'base'})
    for ids in ['','ORDER-1,ORDER-1',','.join('ORDER-'+str(i) for i in range(51))]:
        with pytest.raises(ValueError):client.read('QueryOrders',{'txid':ids,'trades':'true','rebase_multiplier':'base'})
    assert len(calls)==1


def test_enabled_session_stops_after_unresolved_order_query(tmp_path,monkeypatch):
    from services.data.kraken_account import assess
    class Reader:account_ref_sha256='a'*64
    report=assess(observations(),as_of_ms=NOW+500)
    report['result_hash']=tool.digest(report)
    monkeypatch.setattr(tool,'track_orders',lambda *_,**kwargs:{'status':'UNRESOLVED'})
    result=tool.run_session(Reader(),tmp_path,cycles=5,poll_seconds=60,track_known_orders=True,
        collect_fn=lambda _:report,sleep_fn=lambda _:pytest.fail('must stop'))
    assert result['cycles_completed']==1 and result['non_stable_cycles']==1
    assert result['stop_reason']=='ORDER_QUERY_UNRESOLVED'


def test_trade_replay_is_idempotent_and_changed_trade_cannot_overwrite(tmp_path):
    from tests.data.test_kraken_trade_query import trade_body
    from tests.data.test_kraken_order_query import read
    from services.data.kraken_order_query import assess
    class Reader:
        account_ref_sha256='a'*64
        response=trade_body()
        def read(self,endpoint,params):
            assert endpoint=='QueryTrades' and params['trades']=='false'
            return json.dumps(self.response).encode()
    reader=Reader();orders=assess(read(),as_of_ms=NOW+30)
    tool.query_trades(reader,tmp_path,orders,clock_ms=lambda:NOW+50)
    tool.query_trades(reader,tmp_path,orders,clock_ms=lambda:NOW+60)
    path=tmp_path/'trade-observations'/'TRADE-1.json';before=path.read_bytes()
    assert len(list(path.parent.glob('*.json')))==1
    reader.response['result']['TRADE-1']['price']='100.01'
    with pytest.raises(ValueError,match='REPLAY_CONFLICT'):
        tool.query_trades(reader,tmp_path,orders,clock_ms=lambda:NOW+70)
    assert path.read_bytes()==before


def test_trade_query_signing_has_separate_twenty_id_limit(tmp_path):
    calls=[]
    key=base64.b64encode(b'public fixture only').decode();secret=base64.b64encode(b'non-secret fixture only').decode()
    client=tool.ReadOnlyClient(key,secret,tmp_path,transport=lambda *args:calls.append(args),clock_ms=lambda:NOW)
    client.read('QueryTrades',{'txid':'TRADE-1','trades':'false','rebase_multiplier':'base'})
    with pytest.raises(ValueError):client.read('QueryTrades',{
        'txid':','.join('T-'+str(i) for i in range(21)),'trades':'false','rebase_multiplier':'base'})
    assert len(calls)==1


def test_bounded_ledger_collection_includes_both_asset_classes(tmp_path):
    from tests.data.test_kraken_cash_reconciliation import samples
    opening,closing,pages=samples()
    for item in (opening,closing):item['result_hash']=tool.digest(item)
    calls=[]
    class Reader:
        account_ref_sha256='a'*64
        def read(self,endpoint,params):
            calls.append((endpoint,params))
            return next(p['body'].encode() for p in pages if p['params']['aclass']==params['aclass'])
    result=tool.collect_cash_bridge(Reader(),tmp_path,opening,closing,clock_ms=lambda:NOW+63000)
    assert result['status']=='BALANCE_MOVEMENTS_MATCHED'
    assert [p['aclass'] for _,p in calls]==['currency','tokenized_asset']
    assert result['next_cycle_allowed'] is False
    assert json.loads((tmp_path/'cash-reconciliation.json').read_text())['closing_account_hash']==closing['result_hash']


def test_session_reconciles_every_cycle_against_same_checkpoint(tmp_path):
    from copy import deepcopy
    from tests.data.test_kraken_cash_reconciliation import samples
    opening,closing,pages=samples()
    opening['result_hash']=tool.digest(opening)
    calls=[];events=[];counter=[0];clock=[NOW+63000]
    class Reader:
        account_ref_sha256='a'*64
        def read(self,endpoint,params):
            assert endpoint=='Ledgers'
            calls.append(params)
            return next(p['body'].encode() for p in pages if p['params']['aclass']==params['aclass'])
    def collect(_):
        row=deepcopy(closing);offset=counter[0]*60000;counter[0]+=1
        row['observed_at_ms']+=offset
        for read in row['reads']:
            read['requested_at_ms']+=offset;read['received_at_ms']+=offset
        row['result_hash']=tool.digest(row);clock[0]=NOW+63000+offset
        return row
    result=tool.run_session(Reader(),tmp_path,cycles=2,collect_fn=collect,sleep_fn=lambda _:None,
        ledger_opening=opening,clock_ms=lambda:clock[0],on_report=events.append)
    assert result['non_stable_cycles']==0 and len(calls)==4
    assert {p['start'] for p in calls}=={NOW//1000}
    assert [e['balance_bridge_status'] for e in events]==['BALANCE_MOVEMENTS_MATCHED']*2
    assert len(list((tmp_path/'cash-windows').glob('*.json')))==2


def test_session_stops_when_automatic_cash_query_fails(tmp_path):
    from tests.data.test_kraken_cash_reconciliation import samples
    opening,closing,_=samples()
    for row in (opening,closing):row['result_hash']=tool.digest(row)
    class Reader:
        account_ref_sha256='a'*64
        calls=0
        def read(self,*_):self.calls+=1;raise OSError('private transport detail')
    reader=Reader();events=[]
    result=tool.run_session(reader,tmp_path,cycles=3,collect_fn=lambda _:closing,
        sleep_fn=lambda _:pytest.fail('failure must stop'),ledger_opening=opening,
        clock_ms=lambda:NOW+63000,on_report=events.append)
    assert result['stop_reason']=='CASH_QUERY_UNRESOLVED' and result['cycles_completed']==1
    assert result['non_stable_cycles']==1 and reader.calls==1
    assert events[0]['balance_bridge_status']=='UNRESOLVED'
    report=json.loads((tmp_path/'cash-reconciliation.json').read_text())
    assert report['status']=='UNRESOLVED' and 'private transport detail' not in json.dumps(report)


def test_cash_checkpoint_expiry_stops_before_remote_read(tmp_path):
    from tests.data.test_kraken_cash_reconciliation import samples
    opening,closing,_=samples()
    closing['observed_at_ms']+=86400000
    for row in (opening,closing):row['result_hash']=tool.digest(row)
    class Reader:
        account_ref_sha256='a'*64
        def read(self,*_):pytest.fail('expired checkpoint must not query')
    result=tool.run_session(Reader(),tmp_path,ledger_opening=opening,collect_fn=lambda _:closing,
        clock_ms=lambda:NOW+86463000)
    assert result['stop_reason']=='CASH_QUERY_UNRESOLVED'


def test_cash_interruption_leaves_unresolved_current_pointer(tmp_path):
    from tests.data.test_kraken_cash_reconciliation import samples
    opening,closing,pages=samples()
    for row in (opening,closing):row['result_hash']=tool.digest(row)
    class Reader:
        account_ref_sha256='a'*64
        interrupt=False
        def read(self,endpoint,params):
            if self.interrupt:raise KeyboardInterrupt()
            return next(p['body'].encode() for p in pages if p['params']['aclass']==params['aclass'])
    reader=Reader()
    tool.collect_cash_bridge(reader,tmp_path,opening,closing,clock_ms=lambda:NOW+63000)
    reader.interrupt=True
    with pytest.raises(KeyboardInterrupt):
        tool.collect_cash_bridge(reader,tmp_path,opening,closing,clock_ms=lambda:NOW+64000)
    report=json.loads((tmp_path/'cash-reconciliation.json').read_text())
    assert report['status']=='UNRESOLVED' and report['failure']['error_type']=='REFRESH_IN_PROGRESS'
    assert report['result_hash']==tool.digest({k:v for k,v in report.items() if k!='result_hash'})


def test_auto_cash_bootstrap_then_restart_reuses_checkpoint(tmp_path):
    from tests.data.test_kraken_cash_reconciliation import samples
    from tests.data.test_kraken_order_query import body
    from tests.data.test_kraken_trade_query import trade_body
    opening,closing,pages=samples();opening['open_orders']=[];closing['open_orders']=[]
    for row in (opening,closing):row['result_hash']=tool.digest(row)
    class Reader:
        account_ref_sha256='a'*64
        calls=0
        def read(self,endpoint,params):
            self.calls+=1
            if endpoint=='QueryOrders':return body('canceled').encode()
            if endpoint=='QueryTrades':
                row=trade_body();row['result']['TRADE-1']['time']=str(NOW//1000+30)
                return json.dumps(row).encode()
            assert endpoint=='Ledgers' and params['start']==NOW//1000
            return next(p['body'].encode() for p in pages if p['params']['aclass']==params['aclass'])
    reader=Reader()
    first=tool.run_session(reader,tmp_path,auto_cash=True,collect_fn=lambda _:opening,clock_ms=lambda:NOW+1000)
    assert first['balance_bridge_status']=='BASELINE_ONLY' and reader.calls==0
    baseline=(tmp_path/'cash-baseline.json').read_bytes()
    second=tool.run_session(reader,tmp_path,auto_cash=True,collect_fn=lambda _:closing,clock_ms=lambda:NOW+63000)
    assert second['balance_bridge_status']=='BALANCE_MOVEMENTS_MATCHED' and reader.calls==5
    assert json.loads((tmp_path/'order-recovery.json').read_text())['order_ids']==['ORDER-1']
    assert (tmp_path/'cash-baseline.json').read_bytes()==baseline
    (tmp_path/'cash-baseline.json').unlink()
    with pytest.raises(ValueError,match='BASELINE_MISSING'):
        tool.run_session(reader,tmp_path,auto_cash=True,collect_fn=lambda _:pytest.fail('do not reset baseline'))


def test_auto_cash_does_not_establish_checkpoint_over_existing_orders(tmp_path):
    from tests.data.test_kraken_cash_reconciliation import samples
    opening,_,_=samples();opening['result_hash']=tool.digest(opening)
    class Reader:account_ref_sha256='a'*64
    result=tool.run_session(Reader(),tmp_path,auto_cash=True,cycles=2,collect_fn=lambda _:opening,
        sleep_fn=lambda _:pytest.fail('must stop'))
    assert result['stop_reason']=='CASH_BASELINE_REQUIRES_SETTLED_ACCOUNT'
    assert result['non_stable_cycles']==1 and not (tmp_path/'cash-baseline.json').exists()


@pytest.mark.parametrize('case',['modified','mode_disabled','explicit_opening'])
def test_auto_cash_checkpoint_cannot_be_modified_or_bypassed(tmp_path,case):
    from tests.data.test_kraken_cash_reconciliation import samples
    opening,_,_=samples();opening['open_orders']=[];opening['result_hash']=tool.digest(opening)
    class Reader:account_ref_sha256='a'*64
    tool.run_session(Reader(),tmp_path,auto_cash=True,collect_fn=lambda _:opening)
    kwargs={'auto_cash':True}
    if case=='modified':
        changed=dict(opening,observed_at_ms=NOW+600)
        (tmp_path/'cash-baseline.json').write_text(json.dumps(changed))
    if case=='mode_disabled':kwargs['auto_cash']=False
    if case=='explicit_opening':kwargs['ledger_opening']=opening
    with pytest.raises(ValueError):
        tool.run_session(Reader(),tmp_path,collect_fn=lambda _:pytest.fail('must reject before reads'),**kwargs)
