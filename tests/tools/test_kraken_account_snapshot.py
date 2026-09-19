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
            assert endpoint=='QueryOrders' and params['txid']=='ORDER-1'
            return self.response.encode()
    reader=Reader()
    first=tool.track_orders(reader,tmp_path,[{'order_id':'ORDER-1'}],clock_ms=lambda:NOW)
    assert first['status']=='ORDERS_OBSERVED'
    reader.response=body('canceled')
    second=tool.track_orders(reader,tmp_path,[],clock_ms=lambda:NOW+1000)
    assert second['order_ids']==['ORDER-1'] and second['status']=='ORDERS_OBSERVED'
    assert second['cash_reconciled'] is False and second['next_cycle_allowed'] is False
    reader.response='{"error":[],"result":{}}'
    failed=tool.track_orders(reader,tmp_path,[],clock_ms=lambda:NOW+2000)
    assert failed['status']=='UNRESOLVED' and failed['order_ids']==['ORDER-1']
    assert json.loads((tmp_path/'order-query-latest.json').read_text())['orders'][0]['status']=='canceled'


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
    monkeypatch.setattr(tool,'track_orders',lambda *_:{'status':'UNRESOLVED'})
    result=tool.run_session(Reader(),tmp_path,cycles=5,poll_seconds=60,track_known_orders=True,
        collect_fn=lambda _:report,sleep_fn=lambda _:pytest.fail('must stop'))
    assert result['cycles_completed']==1 and result['non_stable_cycles']==1
    assert result['stop_reason']=='ORDER_QUERY_UNRESOLVED'
