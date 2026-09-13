"""Real endpoint shapes; closed candles and observed volume, no invented caps."""
from copy import deepcopy
import hashlib
import json
import math
from decimal import Decimal,localcontext

import pytest

from tools import binance_demo_market_inputs as tool
from services.research import demo_market_inputs as model

NOW=1800000010000
END=NOW//60000*60000-1


def material():
    bars=[]
    for i in range(61):
        start=END+1-(61-i)*60000;price=str(60000+i)
        bars.append([start,price,price,price,price,'2',start+59999,'120000',3,'1','60000','0'])
    return {'ticker':{'symbol':'BTCUSDC','openTime':NOW-86400000,'closeTime':NOW,'volume':'123.456','quoteVolume':'7407360'},
        'klines':bars}


def capture(change=None):
    data=material()
    if change:change(data)
    calls=[];ticks=iter(range(NOW,NOW+100))
    def get(kind,*,symbol,end_ms=None):
        calls.append((kind,symbol,end_ms))
        value={'serverTime':NOW+(6 if kind=='clock_after' else 0)} if kind.startswith('clock') else data[kind]
        return json.dumps(value,separators=(',',':')).encode()
    raw=tool.collect(symbol='BTCUSDC',get=get,clock_ms=lambda:next(ticks))
    return raw,calls


def test_four_public_reads_produce_only_completed_window_with_exact_base_volume(tmp_path):
    raw,calls=capture()
    assert raw['status']=='CAPTURED' and [c[0] for c in calls]==list(model.KINDS)
    assert calls[2][2]==END
    result=model.assess(raw,as_of_ms=NOW+10)
    assert result['status']=='OBSERVED' and result['daily_volume_base']=='123.456'
    expected=math.sqrt(sum(math.log((60001+i)/(60000+i))**2 for i in range(60)))
    assert math.isclose(float(result['instrument_vol']),expected,rel_tol=1e-10)
    assert result['volatility_policy']==model.VOLATILITY_POLICY and result['closed_bars']==61
    assert result['last_close_ms']==END and result['available_at_ms']==raw['completed_at_ms']
    model.save(tmp_path/'data',raw);assert model.load_latest(tmp_path/'data')==raw
    with localcontext() as context:
        context.prec=5
        assert model.assess(raw,as_of_ms=NOW+10)==result


@pytest.mark.parametrize('bad',['unclosed','gap','duplicate','missing','bad_ohlc','negative_volume','wrong_quote','old_ticker','short_day','zero_volume'])
def test_bad_market_inputs_remain_visible_and_never_return_risk_values(bad):
    def change(data):
        if bad=='unclosed':data['klines'][-1][6]=END+60000
        elif bad=='gap':data['klines'][30][0]+=60000
        elif bad=='duplicate':data['klines'][30]=deepcopy(data['klines'][29])
        elif bad=='missing':data['klines'].pop()
        elif bad=='bad_ohlc':data['klines'][30][2]='1'
        elif bad=='negative_volume':data['klines'][30][5]='-1'
        elif bad=='wrong_quote':data['ticker']['symbol']='BTCUSDT'
        elif bad=='old_ticker':data['ticker']['closeTime']-=120000
        elif bad=='short_day':data['ticker']['openTime']=NOW-3600000
        elif bad=='zero_volume':data['ticker']['volume']='0'
    raw,calls=capture(change)
    assert raw['status']=='FAILED' and calls
    result=model.assess(raw,as_of_ms=NOW+10)
    assert result['status']=='INPUTS_UNRESOLVED' and result['daily_volume_base'] is None and result['instrument_vol'] is None


def test_raw_response_tampering_future_use_and_old_inputs_are_rejected():
    raw,_=capture();changed=deepcopy(raw);changed['reads'][1]['body']+=' '
    with pytest.raises(ValueError):model.validate(changed)
    assert model.assess(raw,as_of_ms=NOW)['status']=='FUTURE_INPUT'
    assert model.assess(raw,as_of_ms=NOW+30008)['status']=='STALE'
    assert model.assess(raw,as_of_ms=NOW+30007)['status']=='OBSERVED'


def test_public_transport_has_no_credentials_redirect_or_host_override(monkeypatch):
    requests=[]
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,maximum):return b'{}'
    class Opener:
        def open(self,request,*,timeout):requests.append(request);return Response()
    assert tool.get_response('ticker',symbol='BTCUSDC',opener=Opener())==b'{}'
    request=requests[0]
    assert request.full_url=='https://demo-fapi.binance.com/fapi/v1/ticker/24hr?symbol=BTCUSDC'
    assert request.method=='GET' and not request.headers
    for kind,symbol in [('orders','BTCUSDC'),('ticker','BTCUSDC&signature=oops')]:
        with pytest.raises(ValueError):tool.get_response(kind,symbol=symbol,opener=Opener())
    assert len(requests)==1


def test_a_failed_request_is_retained_without_retry_or_error_body():
    calls=[]
    def get(*a,**kw):calls.append(1);raise OSError('PRIVATE_OR_UNTRUSTED_DETAIL')
    raw=tool.collect(symbol='BTCUSDC',get=get,clock_ms=lambda:NOW)
    assert calls==[1] and raw['status']=='FAILED'
    assert 'PRIVATE_OR_UNTRUSTED_DETAIL' not in json.dumps(raw)


def test_observed_minute_aligned_rolling_day_does_not_become_a_shorter_window():
    raw,_=capture(lambda d:d['ticker'].update(openTime=(NOW-86400000)//60000*60000))
    assert raw['status']=='CAPTURED'
    assert model.assess(raw,as_of_ms=NOW+10)['daily_volume_base']=='123.456'


def test_computed_volatility_is_accepted_by_the_existing_risk_decimal_contract():
    from quantos.contracts.binance_demo_account import _decimal
    raw,_=capture();value=model.assess(raw,as_of_ms=NOW+10)['instrument_vol']
    assert _decimal(value)==value and len(value.split('.')[1])<=18


def test_cli_worker_timeout_retains_a_failed_attempt_without_claiming_observations(tmp_path,monkeypatch,capsys):
    import subprocess
    def blocked(*args,**kwargs):
        assert kwargs['timeout']==35
        raise subprocess.TimeoutExpired(args[0],35)
    monkeypatch.setattr(tool.subprocess,'run',blocked)
    assert tool.main(['--symbol','BTCUSDC','--output-dir',str(tmp_path/'observations')])==3
    raw=model.load_latest(tmp_path/'observations')
    assert raw['status']=='FAILED' and raw['reads']==[] and raw['failure']['reason']=='CAPTURE_BUDGET_EXHAUSTED'
    assert json.loads(capsys.readouterr().out)['status']=='FAILED'
