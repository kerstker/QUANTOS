"""Large public exchangeInfo has a bounded decoded body and no clock waiver."""
import gzip
import io

import pytest

from tools import binance_demo_execution_origin as tool


def response(monkeypatch,body,encoding):
    calls=[]
    class Reply(io.BytesIO):
        status=200
        headers={} if encoding is None else {'Content-Encoding':encoding}
    class Opener:
        def open(self,request,timeout):
            calls.append((request,timeout));return Reply(body)
    monkeypatch.setattr(tool.urllib.request,'build_opener',lambda *a:Opener())
    return calls


def test_public_rules_request_compression_and_hashable_bytes_are_decoded(monkeypatch):
    native=b'{"symbols":[]}'
    calls=response(monkeypatch,gzip.compress(native),'gzip')
    assert tool.get_response('/fapi/v1/exchangeInfo',params='',headers={})==native
    request,timeout=calls[0]
    assert request.get_header('Accept-encoding')=='gzip' and request.method=='GET' and timeout==10
    assert 'X-mbx-apikey' not in request.headers


@pytest.mark.parametrize('encoding,body,reason',[
    ('br',b'not-gzip','RESPONSE_INVALID'),('gzip',b'broken','RESPONSE_INVALID'),
    ('gzip',gzip.compress(b'x'*2_000_001),'RESPONSE_TOO_LARGE'),
    (None,b'x'*2_000_001,'RESPONSE_TOO_LARGE')],ids=['unsupported_encoding','broken_gzip','decoded_limit','wire_limit'])
def test_both_encoded_and_decoded_bodies_are_bounded(monkeypatch,encoding,body,reason):
    response(monkeypatch,body,encoding)
    with pytest.raises(tool.ProbeFailure,match=reason):
        tool.get_response('/fapi/v1/exchangeInfo',params='',headers={})


def test_uncompressed_response_works_and_clock_request_does_not_change(monkeypatch):
    calls=response(monkeypatch,b'{"serverTime":1}',None)
    assert tool.get_response('/fapi/v1/time',params='',headers={})==b'{"serverTime":1}'
    assert calls[0][0].get_header('Accept-encoding') is None


def test_large_all_symbol_account_response_uses_same_bounded_decoder(monkeypatch):
    body=b'[{"symbol":"BTCUSDC"}]';calls=response(monkeypatch,gzip.compress(body),'gzip')
    assert tool.get_response('/fapi/v1/symbolConfig',params='timestamp=1',headers={'X-MBX-APIKEY':'isolated-fixture'})==body
    assert calls[0][0].get_header('Accept-encoding')=='gzip'
    assert calls[0][0].get_header('X-mbx-apikey')=='isolated-fixture'
