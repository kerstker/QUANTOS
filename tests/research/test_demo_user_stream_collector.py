import asyncio
import json

import pytest

from services.research.demo_user_stream import UserStreamJournal
from tools.binance_demo_user_stream import CapturePolicy, capture, main, lease_request
from tests.research.test_demo_user_stream import account_event, NOW
from tests.research.test_binance_order_receipts import message


class Socket:
    def __init__(self, events=()):
        self.events=asyncio.Queue()
        for event in events:self.events.put_nowait(event)
        self.closed=False
    async def __aenter__(self):return self
    async def __aexit__(self,*_):self.closed=True
    async def recv(self):
        value=await self.events.get()
        if isinstance(value,Exception):raise value
        return value
    async def ping(self):
        future=asyncio.get_running_loop().create_future();future.set_result(None);return future


def test_default_command_does_not_load_credentials_or_write(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr('tools.binance_demo_user_stream.load_credentials',lambda *a:pytest.fail('credential read'))
    path=tmp_path/'no.db'
    assert main(['--journal',str(path)])==0
    assert 'NOT_REQUESTED' in capsys.readouterr().out
    assert not path.exists()


@pytest.mark.parametrize('method',['GET','DELETE','PATCH'])
def test_lease_transport_has_no_order_or_shared_lease_delete_surface(method):
    with pytest.raises(ValueError):lease_request(method,'key')


def test_continuous_capture_records_duplicates_partial_accounts_and_clean_stop(tmp_path):
    async def scenario():
        stop=asyncio.Event();socket=Socket([json.dumps(account_event()),json.dumps(message(NOW,'FILLED','1','1',fee='0.1'))]*2)
        async def lease(method):assert method=='POST';return 'x'*64
        with UserStreamJournal(tmp_path/'stream.db',account_ref_sha256='a'*64) as store:
            result=await capture(store,source_sha256='b'*64,lease=lease,connect=lambda token:socket,
                stop=stop,policy=CapturePolicy(seconds=1,heartbeat_seconds=.01,max_messages=4),clock_ms=lambda:NOW+100)
            snapshot=store.snapshot(as_of_ms=NOW+100)
        assert result=='BUDGET_EXHAUSTED' and socket.closed
        assert snapshot['messages']==4 and snapshot['unique_messages']==2
        assert snapshot['event_counts']=={'ACCOUNT_UPDATE':2,'ORDER_TRADE_UPDATE':2}
        assert snapshot['state']=='STOPPED' and snapshot['coverage_verified'] is False
    asyncio.run(scenario())


def test_disconnect_and_expired_lease_are_retained_then_reconnected(tmp_path):
    async def scenario():
        sockets=[Socket([OSError('secret-token')]),Socket([json.dumps({'e':'listenKeyExpired','E':NOW,'listenKey':'z'*64})]),Socket([json.dumps(account_event())])]
        used=[];methods=[]
        def connect(token):socket=sockets[len(used)];used.append(socket);return socket
        async def lease(method):methods.append(method);return 'x'*64
        with UserStreamJournal(tmp_path/'stream.db',account_ref_sha256='a'*64) as store:
            await capture(store,source_sha256='b'*64,lease=lease,connect=connect,stop=asyncio.Event(),
                policy=CapturePolicy(seconds=1,heartbeat_seconds=.01,backoff_seconds=.001,max_messages=2),clock_ms=lambda:NOW+100)
            report=store.snapshot(as_of_ms=NOW+100)
            assert report['connections']==3 and report['gaps']==2
            assert report['messages']==2
            assert 'z'*64 not in str(list(store.db.execute('SELECT body FROM stream_records')))
        assert methods==['POST']*3 and all(s.closed for s in used)
    asyncio.run(scenario())


def test_stop_cancels_hung_lease_without_waiting_for_duration(tmp_path):
    async def scenario():
        stop=asyncio.Event();started=asyncio.Event();cancelled=asyncio.Event()
        async def lease(method):
            started.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        with UserStreamJournal(tmp_path/'stream.db',account_ref_sha256='a'*64) as store:
            task=asyncio.create_task(capture(store,source_sha256='b'*64,lease=lease,connect=lambda token:pytest.fail('socket'),
                stop=stop,policy=CapturePolicy(seconds=60),clock_ms=lambda:NOW))
            await started.wait();stop.set()
            assert await asyncio.wait_for(task,1)=='SIGNAL'
            assert cancelled.is_set()
            assert store.snapshot(as_of_ms=NOW)['state']=='STOPPED'
    asyncio.run(scenario())


def test_bad_payload_and_out_of_order_same_type_do_not_disappear(tmp_path):
    async def scenario():
        earlier=account_event();earlier['E']-=1;earlier['T']-=1
        socket=Socket([json.dumps(account_event()),'{"secret":"hidden"}',json.dumps(earlier)])
        async def lease(method):return 'x'*64
        with UserStreamJournal(tmp_path/'stream.db',account_ref_sha256='a'*64) as store:
            await capture(store,source_sha256='b'*64,lease=lease,connect=lambda token:socket,stop=asyncio.Event(),
                policy=CapturePolicy(seconds=1,max_messages=3),clock_ms=lambda:NOW+100)
            r=store.snapshot(as_of_ms=NOW+100)
            assert r['messages']==2 and r['gaps']==2
            assert 'hidden' not in str(list(store.db.execute('SELECT body FROM stream_records')))
    asyncio.run(scenario())


def test_idle_connection_renews_lease_and_retains_heartbeats_without_fake_events(tmp_path):
    async def scenario():
        calls=[];stop=asyncio.Event();socket=Socket()
        async def lease(method):
            calls.append(method)
            if method=='PUT':stop.set()
            return 'x'*64
        with UserStreamJournal(tmp_path/'stream.db',account_ref_sha256='a'*64) as store:
            assert await capture(store,source_sha256='b'*64,lease=lease,connect=lambda token:socket,stop=stop,
                policy=CapturePolicy(seconds=1,heartbeat_seconds=.01,keepalive_seconds=.02),clock_ms=lambda:NOW)=='SIGNAL'
            r=store.snapshot(as_of_ms=NOW)
            assert calls==['POST','PUT'] and r['messages']==0 and r['heartbeats']>=1
            assert any(row['kind']=='LEASE_RENEWED' for row in r['recent'])
    asyncio.run(scenario())


def test_second_writer_cannot_take_active_journal_lock(tmp_path):
    from tools.binance_demo_user_stream import writer_lock
    with writer_lock(tmp_path/'stream.lock'):
        with pytest.raises(ValueError,match='WRITER_ALREADY_RUNNING'):
            with writer_lock(tmp_path/'stream.lock'):pytest.fail('second writer')


def test_child_deadline_kills_hung_process_and_never_leaks_its_output(monkeypatch):
    from tools import binance_demo_user_stream as module
    async def scenario():
        class Child:
            returncode=None
            killed=False
            async def communicate(self):await asyncio.Event().wait()
            def kill(self):self.killed=True
            async def wait(self):self.returncode=-9
        child=Child()
        async def spawn(*args,**kwargs):return child
        async def timeout(task,*args,**kwargs):task.close();raise TimeoutError('secret')
        monkeypatch.setattr(module.asyncio,'create_subprocess_exec',spawn)
        monkeypatch.setattr(module.asyncio,'wait_for',timeout)
        with pytest.raises(module.ProbeFailure,match='^REQUEST_FAILED$'):
            await module.lease_child('POST','/some/file')
        assert child.killed and child.returncode==-9
    asyncio.run(scenario())
