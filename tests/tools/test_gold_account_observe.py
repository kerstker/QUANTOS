import json
from pathlib import Path

import pytest

from tools.gold_account_observe import observe, main
from tests.research.test_gold_account_journal import account_event


class Clock:
    value=1000
    def now(self):return self.value
    def mono(self):return self.value/1000


class Stream:
    def __init__(self,clock,events):self.clock,self.events=clock,iter(events)
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def recv(self,timeout):
        self.clock.value+=1000
        item=next(self.events,TimeoutError())
        if isinstance(item,BaseException):raise item
        return item
    def ping(self):
        class Pong:
            def wait(self,timeout):return True
        return Pong()


def baseline(path,**kwargs):
    (path/'capture.json').write_text(json.dumps({'account_ref':'a'*64}))
    return {'status':'STABLE_OBSERVATIONS'}


def test_records_events_and_snapshot_gap_without_claiming_history(tmp_path):
    clock=Clock()
    r=observe(tmp_path,api_key='fake',api_secret='fake',seconds=3,
        capture=baseline,open_stream=lambda key:Stream(clock,[json.dumps(account_event())]),
        clock_ms=clock.now,monotonic=clock.mono)
    assert r['status']=='STOPPED' and r['event_count']==1
    assert r['gap_count']==1 and r['history_complete'] is False
    assert r['reason']=='DURATION_COMPLETE'
    assert (tmp_path/'journal.db').exists()


def test_failed_preflight_never_opens_stream(tmp_path):
    def forbidden(key):raise AssertionError('stream opened')
    r=observe(tmp_path,api_key='fake',api_secret='fake',seconds=3,
        capture=lambda *a,**kw:{'status':'INCOMPLETE'},open_stream=forbidden)
    assert r['reason']=='PREFLIGHT_INCOMPLETE'
    assert not (tmp_path/'journal.db').exists()


def test_disconnect_keeps_prefix_and_does_not_leak_exception(tmp_path):
    clock=Clock()
    r=observe(tmp_path,api_key='fake',api_secret='fake',seconds=5,
        capture=baseline,open_stream=lambda key:Stream(clock,[json.dumps(account_event()),RuntimeError('secret-url')]),
        clock_ms=clock.now,monotonic=clock.mono)
    assert r['reason']=='STREAM_FAILED' and r['event_count']==1
    assert r['gap_count']==2
    assert 'secret-url' not in json.dumps(r)
    assert b'secret-url' not in (tmp_path/'journal.db').read_bytes()


def test_unknown_event_stops_with_gap(tmp_path):
    clock=Clock()
    r=observe(tmp_path,api_key='fake',api_secret='fake',seconds=5,capture=baseline,
        open_stream=lambda key:Stream(clock,['{"e":"UNKNOWN","E":1000,"secret":"no"}']),
        clock_ms=clock.now,monotonic=clock.mono)
    assert r['reason']=='EVENT_UNSUPPORTED_OR_INVALID' and r['gap_count']==2


def test_default_command_offline(capsys):
    assert main([])==0
    assert json.loads(capsys.readouterr().out)['network_used'] is False


def test_duration_and_nonempty_destination_rejected(tmp_path):
    with pytest.raises(ValueError):observe(tmp_path,api_key='fake',api_secret='fake',seconds=1801)
    (tmp_path/'existing').touch()
    with pytest.raises(ValueError):observe(tmp_path,api_key='fake',api_secret='fake',seconds=30)


def test_transport_only_creates_lease_and_uses_fixed_private_websocket(monkeypatch):
    from tools.gold_account_observe import open_stream
    import urllib.request
    import websockets.sync.client
    calls=[]
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,n):return b'{"listenKey":"fake-lease-token-12345"}'
    class Opener:
        def open(self,request,timeout):
            assert request.full_url=='https://fapi.binance.com/fapi/v1/listenKey'
            assert request.method=='POST' and timeout==10
            calls.append('lease');return Response()
    def connect(url,**kwargs):
        assert url=='wss://fstream.binance.com/private/ws/fake-lease-token-12345'
        assert kwargs['logger'].disabled and not kwargs['logger'].propagate
        calls.append('websocket');return Stream(Clock(),[])
    monkeypatch.setattr(urllib.request,'build_opener',lambda *a:Opener())
    monkeypatch.setattr(websockets.sync.client,'connect',connect)
    with open_stream('fake-api-key'):pass
    assert calls==['lease','websocket']


def test_lost_pong_stops_and_retains_gap(tmp_path):
    clock=Clock()
    class LostPong(Stream):
        def ping(self):
            class Pong:
                def wait(self,timeout):return False
            return Pong()
    r=observe(tmp_path,api_key='fake',api_secret='fake',seconds=5,capture=baseline,
        open_stream=lambda key:LostPong(clock,[]),clock_ms=clock.now,monotonic=clock.mono)
    assert r['reason']=='STREAM_FAILED' and r['gap_count']==2
