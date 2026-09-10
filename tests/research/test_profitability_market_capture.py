from tools.profitability_market_capture import capture_basis


def test_network_adapter_has_total_process_deadline(monkeypatch):
    import subprocess
    import pytest
    from tools import profitability_market_capture as module
    def timeout(args,**kwargs):
        assert kwargs['timeout']==45
        raise subprocess.TimeoutExpired(args,45)
    monkeypatch.setattr(module.subprocess,'run',timeout)
    with pytest.raises(subprocess.TimeoutExpired):module.bounded_public_json('https://api.binance.com/api/v3/klines?symbol=BTCUSDC&interval=1m&limit=3')


def test_capture_uses_shared_completed_bar_and_real_receive_time(tmp_path):
    def fetch(url):
        price=101 if '/fapi/v1/klines' in url else 100
        return [[0,'100','100','100',str(price),'1',59999],
                [60000,'100','100','100',str(price),'1',119999]]
    result=capture_basis(tmp_path/'capture',fetch=fetch,clock=lambda:90000)
    assert result['status']=='INPUT_CONTRACT_VALIDATED'
    assert len(result['quotes'])==2
    for row in result['quotes']:
        assert row['feature']['event_ms']==59999
        assert row['feature']['decision_ms']==90000
    assert not result['historical_pit_claim']


def test_capture_failure_does_not_replace_previous_success(tmp_path):
    def fail(url):raise OSError('offline')
    result=capture_basis(tmp_path/'failed',fetch=fail,clock=lambda:90000)
    assert result['status']=='WAITING_VALID_INPUT'
    assert len(result['failures'])==6 and result['quotes']==[]


def test_public_error_is_structured_without_echoing_transport_details(monkeypatch):
    import json
    import subprocess
    import pytest
    from tools import profitability_market_capture as module
    def response(*args,**kwargs):
        return subprocess.CompletedProcess(args,0,json.dumps({'ok':False,'category':'HTTP_ERROR','http_status':429,'retry_after_seconds':3}), 'secret proxy details')
    monkeypatch.setattr(module.subprocess,'run',response)
    with pytest.raises(module.PublicRequestError) as error:
        module.bounded_public_json('https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDC',timeout_seconds=7)
    assert error.value.http_status==429 and error.value.retry_after_seconds==3
    assert 'secret' not in str(error.value)


def test_public_worker_rejects_redirects_and_oversized_responses(monkeypatch):
    import io
    import urllib.error
    from tools import profitability_public_http as module
    class Response(io.BytesIO):
        pass
    class Opener:
        def open(self,*args,**kwargs):return Response(b' '* (module.MAX_RESPONSE_BYTES+1))
    monkeypatch.setattr(module.urllib.request,'build_opener',lambda *a:Opener())
    assert module.request_once('https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDC',1)['category']=='RESPONSE_TOO_LARGE'
    assert module.NoRedirect().redirect_request(None,None,302,'',{},'https://example.com') is None


def test_public_worker_preserves_retry_after_but_not_error_body(monkeypatch):
    import urllib.error
    from tools import profitability_public_http as module
    class Opener:
        def open(self,*args,**kwargs):raise urllib.error.HTTPError('url',429,'private details',{'Retry-After':'9'},None)
    monkeypatch.setattr(module.urllib.request,'build_opener',lambda *a:Opener())
    assert module.request_once('https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDC',1)=={
        'ok':False,'category':'HTTP_ERROR','http_status':429,'retry_after_seconds':9}


def test_existing_forward_fetch_retains_bounded_transport_retry(monkeypatch):
    import json
    import subprocess
    from tools import profitability_market_capture as module
    calls=[]
    def response(args,**kwargs):
        calls.append(kwargs['timeout'])
        value={'ok':False,'category':'TRANSPORT_ERROR'} if len(calls)==1 else {'ok':True,'payload':{'symbol':'BTCUSDC'}}
        return subprocess.CompletedProcess(args,0,json.dumps(value),'')
    monkeypatch.setattr(module.subprocess,'run',response)
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    assert module.bounded_public_json('https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDC')=={'symbol':'BTCUSDC'}
    assert len(calls)==2 and 0<calls[1]<=calls[0]<=45


def test_public_depth_capture_can_preserve_exact_response_bytes_without_changing_default(monkeypatch):
    import io
    from tools import profitability_public_http as module
    raw=b'{"lastUpdateId": 3, "bids": [["99.000000000000000001", "2"]], "asks": [["101", "3"]]}\n'
    class Opener:
        def open(self,*args,**kwargs):return io.BytesIO(raw)
    monkeypatch.setattr(module.urllib.request,'build_opener',lambda *a:Opener())
    url='https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDC&limit=1000'
    detailed=module.request_once(url,1,include_raw=True)
    assert detailed['raw_utf8'].encode()==raw
    assert detailed['payload']['bids'][0][0]=='99.000000000000000001'
    assert set(module.request_once(url,1))=={'ok','payload'}


def test_public_raw_capture_still_rejects_unregistered_order_route():
    import pytest
    from tools import profitability_public_http as module
    with pytest.raises(ValueError,match='unregistered'):
        module.request_once('https://fapi.binance.com/fapi/v1/order?symbol=BTCUSDC',1,include_raw=True)


def test_exchange_rule_metadata_has_a_separate_finite_response_budget(monkeypatch):
    import io
    import pytest
    from tools import profitability_public_http as module
    raw=b'{"symbols": [], "padding":"'+b'x'*300000+b'"}'
    class Opener:
        def open(self,*args,**kwargs):return io.BytesIO(raw)
    monkeypatch.setattr(module.urllib.request,'build_opener',lambda *a:Opener())
    assert module.request_once('https://fapi.binance.com/fapi/v1/exchangeInfo',1,include_raw=True)['raw_utf8'].encode()==raw
    assert module.request_once('https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT',1)['category']=='RESPONSE_TOO_LARGE'
    for url in ('https://fapi.binance.com/fapi/v1/exchangeInfo/private','https://fapi.binance.com/fapi/v2/account'):
        with pytest.raises(ValueError):module.request_once(url,1)
    raw=b' '*2000001
    assert module.request_once('https://fapi.binance.com/fapi/v1/exchangeInfo',1)['category']=='RESPONSE_TOO_LARGE'
