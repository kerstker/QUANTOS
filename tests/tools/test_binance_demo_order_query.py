import io
import json
import subprocess
import urllib.error
import urllib.parse

import pytest

from tools import binance_demo_order_query as query
from tests.research.test_demo_order_recovery import fixture, found, capture
from services.research.demo_order_recovery import build_query_plan


def test_default_never_reads_credentials_or_calls_transport(tmp_path):
    class Unreadable(dict):
        def get(self,*args): raise AssertionError('credentials touched')
    with fixture(tmp_path) as journal: plan=build_query_plan(journal)
    result=query.collect(plan,get=lambda *a,**k:pytest.fail('network touched'),environ=Unreadable(),clock_ms=lambda:200)
    assert result.queries[0].outcome=='NOT_REQUESTED'


def test_explicit_mode_uses_exact_symbol_and_client_id_and_only_signed_order_get(tmp_path):
    with fixture(tmp_path) as journal: plan=build_query_plan(journal)
    calls=[]
    def get(path,**kwargs):
        calls.append((path,kwargs))
        return {'serverTime':200} if path.endswith('time') else found()
    result=query.collect(plan,get=get,clock_ms=lambda:200,account_read=True,
        environ={'BINANCE_TESTNET_API_KEY':'fixture-key','BINANCE_TESTNET_API_SECRET':'fixture-secret'})
    assert result.queries[0].outcome=='FOUND'
    assert calls[0]==('/fapi/v1/time',{'params':'','headers':{}})
    assert calls[1][0]=='/fapi/v1/order'
    params=urllib.parse.parse_qs(calls[1][1]['params'])
    assert params['symbol']==['BTCUSDC'] and params['origClientOrderId']==['client-1']
    assert len(params['signature'][0])==64
    assert 'fixture-key' not in result.model_dump_json() and 'fixture-secret' not in result.model_dump_json()


@pytest.mark.parametrize('patch',[{'clientOrderId':'different'},{'orderId':124},{'executedQty':0.4},
    {'symbol':'BTCUSDT'},{'executedQty':'2'},{'updateTime':300},{'price':'98'},{'reduceOnly':True}])
def test_mismatched_or_incomplete_orders_are_unknown(tmp_path,patch):
    with fixture(tmp_path) as journal: plan=build_query_plan(journal)
    result=capture(plan,raw=found()|patch)
    assert result.queries[0].outcome=='UNKNOWN'
    assert result.queries[0].observation is None


def test_not_found_is_never_confirmation_of_absence(monkeypatch):
    class Opener:
        def open(self,*a,**k):
            raise urllib.error.HTTPError('https://demo-fapi.binance.com/fapi/v1/order',400,'bad',{},
                                         io.BytesIO(b'{"code":-2013,"msg":"untrusted private details"}'))
    monkeypatch.setattr(query.urllib.request,'build_opener',lambda *args:Opener())
    with pytest.raises(query.ProbeFailure,match='^ORDER_NOT_FOUND_UNCONFIRMED$'):
        query.get_json('/fapi/v1/order',params='',headers={})


def test_process_timeout_retains_unknown_attempt_without_leaking_child_output(tmp_path,monkeypatch):
    with fixture(tmp_path) as journal: plan=build_query_plan(journal)
    def run(command,**kwargs):
        assert kwargs['timeout']==35
        assert 'PYTHONPATH' not in kwargs['env']
        raise subprocess.TimeoutExpired(command,35,output=b'private-child-output')
    monkeypatch.setattr(query.subprocess,'run',run)
    result=query.collect_bounded(plan,account_read=True)
    assert result.queries[0].reason=='REQUEST_TIMEOUT'
    assert 'private-child-output' not in result.model_dump_json()


def test_transport_is_fixed_host_get_and_bounded(monkeypatch):
    calls=[]
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):
            assert size==16385
            return b'{"serverTime":200}'
    class Opener:
        def open(self,request,timeout):
            calls.append(request)
            assert timeout==10
            return Response()
    monkeypatch.setattr(query.urllib.request,'build_opener',lambda *args:Opener())
    query.get_json('/fapi/v1/time',params='',headers={})
    assert calls[0].method=='GET'
    assert calls[0].full_url=='https://demo-fapi.binance.com/fapi/v1/time'
    with pytest.raises(ValueError):query.get_json('/fapi/v1/income',params='',headers={})


def test_failed_query_stops_following_requests_and_keeps_each_outcome(tmp_path):
    with fixture(tmp_path) as journal: plan=build_query_plan(journal)
    second=plan.orders[0].model_copy(update={'order_id':'second','client_order_id':'client-2','venue_order_id':'124'})
    plan=plan.model_copy(update={'orders':[*plan.orders,second]})
    calls=[]
    def get(path,**kwargs):
        calls.append(path)
        if path.endswith('time'): return {'serverTime':200}
        raise query.ProbeFailure('RATE_LIMITED')
    result=query.collect(plan,get=get,clock_ms=lambda:200,account_read=True,
        environ={'BINANCE_TESTNET_API_KEY':'fixture-key','BINANCE_TESTNET_API_SECRET':'fixture-secret'})
    assert calls==['/fapi/v1/time','/fapi/v1/order']
    assert [q.outcome for q in result.queries]==['UNKNOWN','SKIPPED']


def test_query_attempts_remain_available_when_latest_pointer_changes(tmp_path):
    with fixture(tmp_path) as journal: plan=build_query_plan(journal)
    directory=tmp_path/'queries'
    first=capture(plan,clock=200)
    first_id=query.save_capture(directory,first)
    original=(directory/(first_id+'.json')).read_bytes()
    second=query.failure_capture(plan,'REQUEST_TIMEOUT',started=210,completed=210)
    query.save_capture(directory,second)
    assert query.load_capture(directory)==second
    assert (directory/(first_id+'.json')).read_bytes()==original
