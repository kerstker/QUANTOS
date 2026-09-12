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
            if fail_after and wallets==2:raise RuntimeError('RAW_SECRET_MUST_NOT_APPEAR')
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
