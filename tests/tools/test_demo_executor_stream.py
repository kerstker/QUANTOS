"""Protected stream capture uses the original journal and no order transport."""
import asyncio
import json

import pytest

from tools import demo_executor_stream as worker
from services.research.demo_user_stream import UserStreamJournal
from tests.research.test_demo_user_stream_collector import Socket
from tests.research.test_demo_user_stream import account_event, NOW


def test_missing_executor_custody_stops_before_credentials_or_state_creation(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(worker,'require_custody',lambda:(_ for _ in ()).throw(ValueError('PRIVATE_DETAIL')))
    monkeypatch.setattr(worker.transport,'_credentials',lambda *_:pytest.fail('credentials'))
    assert worker.main(['--source-commit','a'*40]) == 1
    output=capsys.readouterr().out
    assert 'PRIVATE_DETAIL' not in output and 'PROTECTED_STREAM_UNAVAILABLE' in output
    assert list(tmp_path.iterdir()) == []


def test_existing_protected_journal_receives_actual_capture_facts_and_restart_gap(tmp_path, monkeypatch):
    path=tmp_path/'user-stream.sqlite3'
    with UserStreamJournal(path,account_ref_sha256='a'*64):pass
    monkeypatch.setattr(worker,'require_custody',lambda:tmp_path)
    monkeypatch.setattr(worker,'_check_path',lambda *a,**k:None)
    monkeypatch.setattr(worker,'now_ms',lambda:NOW+100)
    sockets=[];calls=[]
    async def lease(method):calls.append(method);return 'x'*64
    def connect(token):
        socket=Socket([json.dumps(account_event())]);sockets.append(socket);return socket
    monkeypatch.setattr(worker,'lease_child',lease)
    monkeypatch.setattr(worker,'connect_socket',connect)
    policy=worker.CapturePolicy(seconds=1,max_messages=1)
    first=asyncio.run(worker.run(source_commit='b'*40,stop=asyncio.Event(),policy=policy))
    second=asyncio.run(worker.run(source_commit='b'*40,stop=asyncio.Event(),policy=policy))
    assert first['reason']==second['reason']=='BUDGET_EXHAUSTED'
    with UserStreamJournal(path,read_only=True) as store:
        snapshot=store.snapshot(as_of_ms=NOW+100)
        assert snapshot['messages']==2 and snapshot['unique_messages']==1
        assert snapshot['gaps']>=1 and snapshot['coverage_verified'] is False
    assert calls==['POST','POST'] and all(s.closed for s in sockets)
    assert first['new_order_submission_enabled'] is False


def test_fixed_worker_loads_only_bound_account_and_lease_method(tmp_path, monkeypatch):
    path=tmp_path/'user-stream.sqlite3'
    with UserStreamJournal(path,account_ref_sha256='a'*64):pass
    monkeypatch.setattr(worker,'require_custody',lambda:tmp_path)
    seen=[]
    opener=object()
    monkeypatch.setattr(worker.demo_network,'opener',lambda:opener)
    monkeypatch.setattr(worker.transport,'_credentials',lambda ref:seen.append(ref) or ('fixture-key','fixture-secret'))
    def lease(method,key,**kwargs):
        assert kwargs=={'opener':opener}
        seen.append((method,key));return 'x'*64
    monkeypatch.setattr(worker,'lease_request',lease)
    assert worker.lease_worker('POST')=='x'*64
    assert seen==['a'*64,('POST','fixture-key')]
    with pytest.raises(ValueError):worker.lease_worker('DELETE')
    assert len(seen)==2


def test_existing_input_budget_is_checked_before_lease_or_append(tmp_path, monkeypatch):
    path=tmp_path/'user-stream.sqlite3'
    with UserStreamJournal(path,account_ref_sha256='a'*64) as store:
        store.start_session(source_sha256='b'*64,at_ms=NOW)
    monkeypatch.setattr(worker,'require_custody',lambda:tmp_path)
    monkeypatch.setattr(worker,'MAX_RECORDS',1)
    monkeypatch.setattr(worker,'lease_child',lambda *_:pytest.fail('lease'))
    original=path.read_bytes()
    with pytest.raises(ValueError,match='PROTECTED_STREAM_RECORD_BUDGET'):
        asyncio.run(worker.run(source_commit='b'*40,stop=asyncio.Event(),policy=worker.CapturePolicy(seconds=.01)))
    assert path.read_bytes()==original


@pytest.mark.parametrize('failure',['timeout','cancel','oversized','untrusted','auth'])
def test_lease_child_has_clean_environment_and_bounded_private_output(monkeypatch, failure):
    monkeypatch.setenv('HTTP_PROXY','http://untrusted.invalid')
    monkeypatch.setenv('SSL_CERT_FILE','/untrusted/ca')
    monkeypatch.setenv('BINANCE_API_KEY','UNRELATED_PRIVATE_VALUE')
    class Child:
        returncode=None; killed=False
        async def communicate(self):
            self.returncode=0
            if failure=='oversized':return b'x'*4097,b''
            if failure=='untrusted':return b'{"secret":"PRIVATE_RESPONSE"}',b''
            return b'{"reason":"AUTHENTICATION_REJECTED"}',b''
        def kill(self):self.killed=True
        async def wait(self):self.returncode=-9
    child=Child()
    async def spawn(*argv,**kwargs):
        assert 'HTTP_PROXY' not in kwargs['env'] and 'SSL_CERT_FILE' not in kwargs['env']
        assert 'BINANCE_API_KEY' not in kwargs['env'] and kwargs['env']['NO_PROXY']=='*'
        assert '--credential-file' not in argv and '--lease-worker' in argv
        return child
    async def interrupted(task,*a,**k):
        task.close()
        if failure=='cancel':raise asyncio.CancelledError()
        raise TimeoutError('PRIVATE_RESPONSE')
    monkeypatch.setattr(worker.asyncio,'create_subprocess_exec',spawn)
    if failure in ('timeout','cancel'):monkeypatch.setattr(worker.asyncio,'wait_for',interrupted)
    expected=asyncio.CancelledError if failure=='cancel' else worker.ProbeFailure
    with pytest.raises(expected) as caught:asyncio.run(worker.lease_child('POST'))
    assert 'PRIVATE_RESPONSE' not in str(caught.value)
    if failure in ('timeout','cancel'):assert child.killed and child.returncode==-9
    if failure=='auth':assert str(caught.value)=='AUTHENTICATION_REJECTED'


def test_protected_socket_uses_fixed_demo_url_without_proxy_or_secret_logs(monkeypatch):
    import websockets.asyncio.client
    captured={}
    monkeypatch.setattr(worker.demo_network,'proxy_url',lambda:None)
    def connect(url,**kwargs):captured.update(url=url,**kwargs);return object()
    monkeypatch.setattr(websockets.asyncio.client,'connect',connect)
    worker.connect_socket('x'*64)
    assert captured['url']==worker.DEMO_WEBSOCKET_BASE+'/private/ws/'+'x'*64
    assert captured['proxy'] is None and captured['logger'].propagate is False
    with pytest.raises(worker.ProbeFailure):worker.connect_socket('http://different.invalid')


def test_socket_uses_only_deployment_route_and_verified_tls(monkeypatch):
    import ssl
    import websockets.asyncio.client
    captured={}
    monkeypatch.setenv('HTTPS_PROXY','http://untrusted.invalid')
    monkeypatch.setenv('SSL_CERT_FILE','/untrusted/cert')
    monkeypatch.setattr(worker.demo_network,'proxy_url',lambda:'http://127.0.0.1:7890')
    monkeypatch.setattr(websockets.asyncio.client,'connect',lambda url,**kwargs:captured.update(url=url,**kwargs))
    worker.connect_socket('x'*64)
    assert captured['proxy']=='http://127.0.0.1:7890'
    assert captured['ssl'].check_hostname and captured['ssl'].verify_mode==ssl.CERT_REQUIRED
