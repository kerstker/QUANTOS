import json
import pytest
from tools.gold_account_monitor import monitor
from tests.tools.test_gold_account_observe import Clock,Stream


def baseline(path,**kwargs):
    c={'account_ref':'a'*64,'started_at_ms':1000,'completed_at_ms':1000,'reads':[
        {'kind':'account_after','started_at_ms':1000,'received_at_ms':1000,'data':{'assets':[], 'positions':[]}},
        {'kind':'orders_after','data':[]},{'kind':'algos_after','data':[]}]}
    (path/'capture.json').write_text(json.dumps(c))
    return {'status':'STABLE_OBSERVATIONS'}


def history(*args,**kwargs):return {'status':'TEST_HISTORY','history_complete':False}


def test_rejected_event_retains_private_safe_diagnostic_and_stops(tmp_path):
    from tools.gold_account_monitor import verify_monitor
    clock=Clock()
    raw=json.dumps({'e':'ALGO_UPDATE','E':1000,'T':1000,'o':{
        'aid':1,'s':'XAUUSDT','S':'BUY','ps':'BOTH','X':'NEW','ai':123},
        'listenKey':'private-lease','secret-field':'private-value'})
    result=monitor(tmp_path,api_key='test-key',api_secret='test-secret',seconds=5,
        capture=baseline,connect=lambda key:Stream(clock,[raw]),history=history,
        clock_ms=clock.now,monotonic=clock.mono)
    assert result['reason']=='EVENT_UNSUPPORTED_OR_INVALID'
    assert result['reconnects']==0 and result['event_count']==0
    path=tmp_path/'run-0001/event-error.json'
    report=json.loads(path.read_text())
    assert report['event_type']=='ALGO_UPDATE'
    assert report['reason']=='INVALID_ACTUAL_ORDER_ID'
    assert path.stat().st_mode & 0o777 == 0o600
    assert 'private-' not in path.read_text() and 'XAUUSDT' not in path.read_text()
    assert verify_monitor(tmp_path)['status']=='LOCAL_ARCHIVE_VERIFIED'


@pytest.mark.parametrize('raw',[
    '{', b'private-secret', json.dumps({'e':'private-secret','E':1}),
    json.dumps({'e':{'private-secret':1},'E':1}), 'x'*300000,
], ids=['malformed','binary','unknown','invalid-kind','oversized'])
def test_event_diagnostic_is_bounded_and_never_echoes_unknown_values(raw):
    from tools.gold_account_monitor import rejected_event_diagnostic
    report=rejected_event_diagnostic(raw)
    assert len(json.dumps(report))<512
    assert 'private-secret' not in json.dumps(report)
    assert report['event_type']=='UNKNOWN'


def test_reconnect_backoff_preserves_gaps_and_publishes_progress(tmp_path):
    clock=Clock();connections=[];progress=[]
    def stream(key):
        connections.append(key)
        return Stream(clock,[RuntimeError('secret')]) if len(connections)==1 else Stream(clock,[])
    def sleep(seconds):clock.value+=int(seconds*1000)
    r=monitor(tmp_path,api_key='test-key',api_secret='test-secret',seconds=8,capture=baseline,
        connect=stream,history=history,clock_ms=clock.now,monotonic=clock.mono,pause=sleep,progress=progress.append)
    assert len(connections)==2 and r['reason']=='DURATION_COMPLETE'
    assert r['gap_count']>=2 and r['reconnects']==1
    assert 'CONNECTED' in [p['status'] for p in progress]
    assert json.loads((tmp_path/'account-monitor.json').read_text())['summary']['status']=='STOPPED'
    assert b'test-key' not in (tmp_path/'identity.json').read_bytes()


def test_restart_refuses_changed_key_before_network(tmp_path):
    c=Clock()
    monitor(tmp_path,api_key='test-key',api_secret='test-secret',seconds=1,capture=baseline,
        connect=lambda key:Stream(c,[]),history=history,clock_ms=c.now,monotonic=c.mono)
    def forbidden(*a,**kw):raise AssertionError('network')
    with pytest.raises(ValueError,match='ACCOUNT_BINDING_MISMATCH'):
        monitor(tmp_path,api_key='another-key',api_secret='test-secret',seconds=1,resume=True,capture=forbidden)


def test_history_failure_retains_journal_and_unknown_reconciliation(tmp_path):
    c=Clock()
    def fail(*a,**kw):raise RuntimeError('private-signed-url')
    r=monitor(tmp_path,api_key='test-key',api_secret='test-secret',seconds=1,capture=baseline,
        connect=lambda key:Stream(c,[]),history=fail,clock_ms=c.now,monotonic=c.mono)
    assert r['reason']=='HISTORY_FAILED' and r['history_complete'] is False
    assert 'private-signed-url' not in json.dumps(r)


def test_resume_keeps_identity_and_old_records(tmp_path):
    c=Clock();kw=dict(api_key='test-key',api_secret='test-secret',seconds=1,capture=baseline,
        connect=lambda key:Stream(c,[]),history=history,clock_ms=c.now,monotonic=c.mono)
    first=monitor(tmp_path,**kw)
    second=monitor(tmp_path,**kw,resume=True)
    assert second['records']>first['records'] and second['gap_count']>first['gap_count']
    assert (tmp_path/'run-0002').is_dir()


def test_recovery_checks_conservation_and_retains_history(tmp_path):
    from tools.gold_account_monitor import recover
    from services.research.gold_account_journal import AccountJournal
    initial={'reads':[{'kind':'account_after','started_at_ms':3000,'received_at_ms':3000,
        'data':{'assets':[{'asset':'USDT','walletBalance':'100'}],'positions':[]}},
        {'kind':'orders_after','data':[]},{'kind':'algos_after','data':[]}]}
    def end_capture(path,**kw):
        from copy import deepcopy
        end=deepcopy(initial);end['reads'][0].update(started_at_ms=12000,received_at_ms=12000)
        (path/'capture.json').write_text(json.dumps(end))
        return {'status':'STABLE_OBSERVATIONS'}
    with AccountJournal(tmp_path/'journal.db',account_ref='a'*64) as j:
        j.start(at_ms=3000,source_sha256='b'*64)
        r=recover(tmp_path/'checkpoint',api_key='fake',api_secret='fake',initial=initial,journal=j,
            clock_ms=lambda:20000,capture=end_capture,read=lambda *a:[])
        assert r['status']=='WINDOW_CHECKED' and r['history_complete'] is False
        assert r['inventory']['wallet_status']=='MATCHING_OBSERVED_TOTALS'
        assert (tmp_path/'checkpoint/history.json').exists()


def test_offline_verifier_detects_changed_published_files(tmp_path):
    from tools.gold_account_monitor import verify_monitor
    c=Clock()
    monitor(tmp_path,api_key='test-key',api_secret='test-secret',seconds=1,capture=baseline,
        connect=lambda key:Stream(c,[]),history=history,clock_ms=c.now,monotonic=c.mono)
    assert verify_monitor(tmp_path)['status']=='LOCAL_ARCHIVE_VERIFIED'
    p=tmp_path/'run-0001/report.json';p.write_text('{}')
    with pytest.raises(ValueError):verify_monitor(tmp_path)


def test_repeated_failure_is_bounded(tmp_path):
    c=Clock();calls=[]
    def bad(key):calls.append(key);raise RuntimeError('private')
    def sleep(s):c.value+=int(s*1000)
    r=monitor(tmp_path,api_key='test-key',api_secret='test-secret',seconds=100,capture=baseline,
        connect=bad,history=history,clock_ms=c.now,monotonic=c.mono,pause=sleep)
    assert len(calls)==5 and r['reason']=='RECONNECT_EXHAUSTED'


def test_end_to_end_trade_disconnect_recovery_and_archive(tmp_path):
    from tools.gold_account_monitor import recover,verify_monitor
    from tests.research.test_gold_account_journal import trade_event
    from tests.research.test_gold_trade_observation import native_document
    c=Clock();c.value=10000
    event=trade_event();event.update(E=15001,T=15000);event['o']['T']=15000
    raw=native_document()['fills'][0]
    raw.update(id=7,orderId=42,time=15000,qty='0.01',price='4000',quoteQty='40',commission='0.02')
    def capture(path,**kw):
        is_final=path.name=='snapshot'
        document={'account_ref':'a'*64,'started_at_ms':c.now(),'completed_at_ms':c.now(),'reads':[
            {'kind':'account_after','started_at_ms':c.now(),'received_at_ms':c.now(),
             'data':{'assets':[{'asset':'USDT','walletBalance':'99.98' if is_final else '100'}],
                     'positions':[{'symbol':'XAUUSDT','positionSide':'BOTH','positionAmt':'0.01' if is_final else '0'}]}},
            {'kind':'orders_after','data':[]},{'kind':'algos_after','data':[]}]}
        (path/'capture.json').write_text(json.dumps(document));return {'status':'STABLE_OBSERVATIONS'}
    connections=[]
    def connect(key):
        connections.append(1)
        if len(connections)==1:return Stream(c,[json.dumps(event),RuntimeError('private')])
        return Stream(c,[json.dumps(event)])
    def read(path,params):
        if path.endswith('userTrades'):return [raw]
        if path.endswith('income'):return [{'symbol':'XAUUSDT','incomeType':'COMMISSION','income':'-0.02',
            'asset':'USDT','time':15000,'tranId':1,'tradeId':'7'}]
        return []
    def recovery(path,**kw):
        # Fake end snapshot precedes observation clock, so no real publication wait.
        old=kw['capture']
        def end(path,**args):
            old(path,**args)
            p=path/'capture.json';v=json.loads(p.read_text());v['reads'][0]['received_at_ms']-=3000
            v['reads'][0]['started_at_ms']-=3000;p.write_text(json.dumps(v))
            return {'status':'STABLE_OBSERVATIONS'}
        kw['capture']=end
        return recover(path,**kw,read=read)
    def pause(s):c.value+=int(s*1000)
    r=monitor(tmp_path,api_key='test-key',api_secret='test-secret',seconds=30,capture=capture,
        connect=connect,history=recovery,clock_ms=c.now,monotonic=c.mono,pause=pause)
    assert r['reason']=='DURATION_COMPLETE' and r['history_status']=='WINDOW_CHECKED'
    assert r['unique_trades']==1 and r['duplicate_events']==1 and r['reconnects']==1
    assert verify_monitor(tmp_path)['status']=='LOCAL_ARCHIVE_VERIFIED'


def test_checkpoint_anchor_advances_only_after_matching_window(tmp_path):
    c=Clock();starts=[]
    def recovery(path,**kw):
        starts.append(kw['initial']['completed_at_ms'])
        path.mkdir();snap=path/'snapshot';snap.mkdir()
        value={'completed_at_ms':c.now(),'reads':[{'kind':'account_after','data':{},'started_at_ms':c.now(),'received_at_ms':c.now()}]}
        (snap/'capture.json').write_text(json.dumps(value))
        return {'status':'WINDOW_CHECKED','history_complete':False}
    kw=dict(api_key='test-key',api_secret='test-secret',seconds=1,capture=baseline,
        connect=lambda key:Stream(c,[]),history=recovery,clock_ms=c.now,monotonic=c.mono)
    monitor(tmp_path,**kw);monitor(tmp_path,**kw,resume=True)
    assert starts==[1000,2000]


def test_first_preflight_failure_can_resume_without_inventing_initial_state(tmp_path):
    c=Clock()
    def failed(path,**kwargs):
        (path/'capture.json').write_text(json.dumps({'reads':[]}))
        return {'status':'INCOMPLETE','reason':'NETWORK_UNAVAILABLE'}
    first=monitor(tmp_path,api_key='test-key',api_secret='test-secret',seconds=1,capture=failed,
        clock_ms=c.now,monotonic=c.mono)
    assert first['preflight_reason']=='NETWORK_UNAVAILABLE'
    second=monitor(tmp_path,api_key='test-key',api_secret='test-secret',seconds=1,resume=True,
        capture=baseline,connect=lambda key:Stream(c,[]),history=history,clock_ms=c.now,monotonic=c.mono)
    assert second['reason']=='DURATION_COMPLETE' and second['gap_count']>=2


@pytest.mark.parametrize('payload,shape', [
    ({'e': 'private-secret', 'E': 1}, 'EVENT_OBJECT'),
    ({'data': {'e': 'private-secret'}, 'stream': 'private-secret'}, 'WRAPPED_DATA'),
    ({'result': None, 'id': 'private-secret'}, 'RESULT_OBJECT'),
    ({'code': 1, 'msg': 'private-secret'}, 'ERROR_OBJECT'),
    ({'private-secret': 1}, 'OTHER_OBJECT'),
    (['private-secret'], 'NON_OBJECT'),
])
def test_rejected_diagnostic_identifies_shape_without_account_values(payload, shape):
    from tools.gold_account_monitor import rejected_event_diagnostic
    result = rejected_event_diagnostic(json.dumps(payload))
    assert result['message_shape'] == shape
    assert 'private-secret' not in json.dumps(result)
    assert len(json.dumps(result)) < 512


def test_unknown_event_name_can_be_matched_without_persisting_its_value():
    import hashlib
    from tools.gold_account_monitor import rejected_event_diagnostic
    left = rejected_event_diagnostic(json.dumps({'e': 'private-secret', 'E': 1}))
    right = rejected_event_diagnostic(json.dumps({'e': 'private-secret', 'E': 2}))
    assert left['event_name_sha256'] == right['event_name_sha256'] == hashlib.sha256(b'private-secret').hexdigest()
    assert left['response_sha256'] != right['response_sha256']
    assert 'private-secret' not in json.dumps(left)
    assert rejected_event_diagnostic(json.dumps({'e': {'private-secret': 1}}))['event_name_sha256'] is None
