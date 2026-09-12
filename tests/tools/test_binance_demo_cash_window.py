import json
from urllib.parse import parse_qs

import pytest

from tools import binance_demo_cash_window as tool
from tests.tools.test_binance_demo_private_check import credentials


class Clock:
    def __init__(self): self.ms=1789170000000
    def now(self): return self.ms
    def sleep(self, seconds): self.ms+=round(seconds*1000)


def transport(clock, calls, *, fail_after=False, saturated=False):
    wallets=0
    def get(path, *, params, headers):
        nonlocal wallets
        q=parse_qs(params);calls.append((path,q,headers));at=clock.ms;clock.ms+=100
        if path=='/fapi/v1/time':data={'serverTime':at+50}
        elif path=='/fapi/v3/account':
            wallets+=1
            if fail_after and wallets>=2:raise RuntimeError('RAW_SECRET_MUST_NOT_APPEAR')
            data={'assets':[{'asset':'USDT','walletBalance':'1000','updateTime':at-1000}], 'secret':'NEVER_SAVE'}
        elif path=='/fapi/v1/income':
            data=([{'symbol':'','incomeType':'TRANSFER','income':'0','asset':'USDT',
                    'time':int(q['startTime'][0])+30000,'tranId':i,'tradeId':'','info':'NEVER_SAVE'}
                   for i in range(1000)] if saturated else [])
        else:raise AssertionError(path)
        return json.dumps(data).encode()
    return get


def test_default_is_offline_and_does_not_load_credentials(monkeypatch,tmp_path):
    monkeypatch.setattr(tool,'load_credentials',lambda *a:pytest.fail('credential read'))
    raw=tool.collect(credential_file=tmp_path/'absent',observe_seconds=60,
                     get=lambda *a,**kw:pytest.fail('network'))
    assert raw['status']=='NOT_REQUESTED' and raw['reads']==[]
    tool.validate(raw)


def test_bounded_observations_use_one_identity_and_signed_private_gets(tmp_path):
    clock=Clock();calls=[]
    raw=tool.collect(credential_file=credentials(tmp_path),observe_seconds=60,operator_read=True,
                     get=transport(clock,calls),clock_ms=clock.now,sleep=clock.sleep)
    assert raw['status']=='CAPTURED' and len(calls)==6
    assert raw['completed_at_ms']-raw['observed_at_ms']>=90000
    for path,params,headers in calls:
        if path=='/fapi/v1/time':assert not params and not headers
        else:assert 'signature' in params and len(headers)==1
    assert len({r['account_ref_sha256'] for r in raw['reads'] if not r['kind'].startswith('clock')})==1
    assert 'NEVER_SAVE' not in json.dumps(raw)
    summary=tool.assess(raw)
    assert summary['status']=='MATCHED_REPORTED_WINDOW' and summary['income_count']==0
    assert summary['strategy_pnl'] is None


def test_partial_network_failure_keeps_opening_wallet_and_redacts_exception(tmp_path):
    clock=Clock()
    raw=tool.collect(credential_file=credentials(tmp_path),observe_seconds=60,operator_read=True,
                     get=transport(clock,[],fail_after=True),clock_ms=clock.now,sleep=clock.sleep)
    assert raw['status']=='PARTIAL' and len(raw['reads'])==2
    assert raw['failures'][0]['kind']=='wallet_after'
    assert 'RAW_SECRET' not in json.dumps(raw)
    assert tool.assess(raw)['status']=='UNRESOLVED'


def test_pagination_budget_is_finite_and_saturation_not_passed(tmp_path):
    clock=Clock();calls=[]
    raw=tool.collect(credential_file=credentials(tmp_path),observe_seconds=60,operator_read=True,
                     get=transport(clock,calls,saturated=True),clock_ms=clock.now,sleep=clock.sleep)
    assert len([p for p,_,_ in calls if p=='/fapi/v1/income'])==2*tool.MAX_PAGES
    assert 'INCOME_PAGE_LIMIT' in tool.assess(raw)['issues']


@pytest.mark.parametrize('path',['/fapi/v1/order','/fapi/v1/listenKey','https://fapi.binance.com/fapi/v3/account'])
def test_transport_has_no_order_or_mainnet_path(path):
    with pytest.raises(tool.ProbeFailure):tool.get_response(path,params='',headers={})


def test_child_timeout_is_saved_as_failure_not_zero_profit(tmp_path,monkeypatch):
    import subprocess
    def timeout(*a,**kw):
        assert kw['timeout']==290
        raise subprocess.TimeoutExpired('redacted',290)
    monkeypatch.setattr(tool.subprocess,'run',timeout)
    output=tmp_path/'output'
    assert tool.main(['--credential-file',str(tmp_path/'not-read'), '--output-dir',str(output),
                      '--observe-seconds','60','--operator-read'])==2
    saved=tool.load_capture(output)
    assert saved['failures'][0]['reason']=='CAPTURE_BUDGET_EXHAUSTED'
    assert tool.assess(saved)['assets']==[]


def test_transient_get_failure_recovers_with_fresh_signature_and_retained_attempt(tmp_path):
    clock=Clock();calls=[];ordinary=transport(clock,calls);failed=False
    def get(path, **kwargs):
        nonlocal failed
        if path=='/fapi/v3/account' and not failed:
            failed=True
            calls.append((path,parse_qs(kwargs['params']),kwargs['headers']))
            clock.sleep(1)
            raise tool.ProbeFailure('REQUEST_FAILED')
        return ordinary(path,**kwargs)
    raw=tool.collect(credential_file=credentials(tmp_path),observe_seconds=60,operator_read=True,
                     get=get,clock_ms=clock.now,sleep=clock.sleep)
    assert raw['status']=='CAPTURED'
    assert raw['schema']=='DEMO_CASH_WINDOW_V2'
    private=[q for p,q,h in calls if p=='/fapi/v3/account']
    assert int(private[1]['timestamp'][0])>int(private[0]['timestamp'][0])
    assert private[1]['signature']!=private[0]['signature']
    attempts=raw['attempts']
    assert len(attempts)==7 and attempts[1]['reason']=='REQUEST_FAILED'
    assert attempts[2]['attempt']==2 and attempts[2]['outcome']=='SUCCESS'
    assert tool.assess(raw)['attempts']==attempts
    assert tool.assess(raw)['status']=='MATCHED_REPORTED_WINDOW'


@pytest.mark.parametrize('reason',['REQUEST_FAILED','AUTHENTICATION_REJECTED','RATE_LIMITED','RESPONSE_INVALID','CLOCK_UNRELIABLE'])
def test_get_retry_is_bounded_and_does_not_retry_nontransport_rejections(tmp_path,reason):
    clock=Clock();calls=[];ordinary=transport(clock,calls);failures=[]
    def get(path,**kwargs):
        if path=='/fapi/v3/account':
            failures.append(path);clock.sleep(.1);raise tool.ProbeFailure(reason)
        return ordinary(path,**kwargs)
    raw=tool.collect(credential_file=credentials(tmp_path),observe_seconds=60,operator_read=True,
                     get=get,clock_ms=clock.now,sleep=clock.sleep)
    expected=3 if reason=='REQUEST_FAILED' else 1
    assert len(failures)==expected and raw['status']=='PARTIAL'
    assert [a['reason'] for a in raw['attempts'][1:]]==[reason]*expected
    assert raw['failures'][0]['reason']==reason
    assert tool.assess(raw)['status']=='UNRESOLVED'


def test_failed_clock_retains_candidate_timing_without_relaxing_clock_rule(tmp_path):
    clock=Clock();calls=[];ordinary=transport(clock,calls)
    def get(path,**kwargs):
        raw=ordinary(path,**kwargs)
        if path=='/fapi/v1/time' and len(calls)>1:
            value=json.loads(raw);value['serverTime']+=501
            return json.dumps(value).encode()
        return raw
    raw=tool.collect(credential_file=credentials(tmp_path),observe_seconds=60,operator_read=True,
                     get=get,clock_ms=clock.now,sleep=clock.sleep)
    assert raw['status']=='PARTIAL' and len(calls)==4
    last=raw['attempts'][-1]
    assert last['reason']=='CLOCK_UNRELIABLE' and last['server_time_ms']>last['received_at_ms']
    assert len(last['response_sha256'])==64
    assert len(raw['reads'])==3 and tool.assess(raw)['assets'][0]['residual'] is None


@pytest.mark.parametrize('status,expected',[(400,'RESPONSE_INVALID'),(404,'RESPONSE_INVALID'),(302,'RESPONSE_INVALID'),
    (401,'AUTHENTICATION_REJECTED'),(403,'AUTHENTICATION_REJECTED'),(418,'RATE_LIMITED'),
    (429,'RATE_LIMITED'),(408,'REQUEST_FAILED'),(503,'REQUEST_FAILED')])
def test_http_rejections_are_not_all_treated_as_retryable_network_errors(monkeypatch,status,expected):
    from urllib.error import HTTPError
    class Opener:
        def open(self,*a,**kw):raise HTTPError('redacted',status,'must not retain',{},None)
    monkeypatch.setattr(tool.urllib.request,'build_opener',lambda *a:Opener())
    with pytest.raises(tool.ProbeFailure,match=expected):
        tool.get_response('/fapi/v1/time',params='',headers={})


def test_worker_budget_stops_retries_and_retains_the_failed_attempt(tmp_path,monkeypatch):
    clock=Clock();calls=[];ordinary=transport(clock,calls)
    monotonic=[100]
    monkeypatch.setattr(tool.time,'monotonic',lambda:monotonic[0])
    def get(path,**kwargs):
        if path=='/fapi/v3/account':
            monotonic[0]+=275;clock.sleep(1);raise tool.ProbeFailure('REQUEST_FAILED')
        return ordinary(path,**kwargs)
    raw=tool.collect(credential_file=credentials(tmp_path),observe_seconds=60,operator_read=True,
                     get=get,clock_ms=clock.now,sleep=clock.sleep)
    assert raw['status']=='PARTIAL'
    assert [a['reason'] for a in raw['attempts']]==[None,'REQUEST_FAILED','CAPTURE_BUDGET_EXHAUSTED']
    assert raw['failures'][0]['reason']=='CAPTURE_BUDGET_EXHAUSTED'
