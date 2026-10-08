"""Fixed endpoint, no redirection/retry, credentials outside research custody."""
import hashlib
import json
import urllib.error
import urllib.request

import pytest

from live.exchange import binance_demo_exact as transport
from live.execution import demo_authority


def test_post_transport_has_one_attempt_and_fixed_tls_endpoint_without_environment_proxy(monkeypatch):
    monkeypatch.setattr(transport.demo_network,'proxy_url',lambda:None)
    calls=[]
    class Opener:
        def open(self,request,timeout):
            calls.append((request,timeout));raise urllib.error.URLError('fixture timeout')
    def build(*handlers):
        assert any(isinstance(h,urllib.request.ProxyHandler) and h.proxies=={} for h in handlers)
        assert any(isinstance(h,transport._NoRedirect) for h in handlers)
        return Opener()
    monkeypatch.setattr(urllib.request,'build_opener',build)
    with pytest.raises(urllib.error.URLError):transport._http_once(b'fixture',{})
    assert len(calls)==1
    request,timeout=calls[0]
    assert request.full_url=='https://demo-fapi.binance.com/fapi/v1/order'
    assert request.get_method()=='POST' and timeout==10
    assert transport._NoRedirect().redirect_request(None,None,302,'',{},'https://example.invalid') is None


def test_credential_loader_checks_namespace_after_fixed_root_custody_without_logging_values(monkeypatch):
    key='K'*32;secret='S'*32
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:{})
    calls=[]
    def read(path):
        calls.append(str(path));return json.dumps({'api_key':key,'api_secret':secret}).encode()
    monkeypatch.setattr(demo_authority,'_read_root_owned_json',read)
    ref=hashlib.sha256(('BINANCE_USDM_DEMO:'+key).encode()).hexdigest()
    assert transport._credentials(ref)==(key,secret)
    with pytest.raises(ValueError) as error:transport._credentials('a'*64)
    assert key not in str(error.value) and secret not in str(error.value)
    assert set(calls)=={'/etc/quantos/demo-submission/credentials.json'}


def test_uninstalled_executor_does_not_read_any_credential_file(monkeypatch):
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:(_ for _ in ()).throw(ValueError('uninstalled fixture')))
    monkeypatch.setattr(demo_authority,'_read_root_owned_json',lambda path:pytest.fail('credentials must not be read'))
    with pytest.raises(ValueError):transport._credentials('a'*64)


def test_cancel_transport_has_one_delete_to_fixed_demo_host_and_bounded_response(monkeypatch):
    monkeypatch.setattr(transport.demo_network,'proxy_url',lambda:None)
    calls=[]
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,n):assert n==65537;return b'{}'
    class Opener:
        def open(self,request,timeout):calls.append((request,timeout));return Response()
    def build(*handlers):
        assert any(isinstance(h,urllib.request.ProxyHandler) and h.proxies=={} for h in handlers)
        assert any(isinstance(h,transport._NoRedirect) for h in handlers)
        return Opener()
    monkeypatch.setattr(urllib.request,'build_opener',build)
    assert transport._http_delete_once(b'origClientOrderId=QDtest000001',{})==(200,b'{}')
    request,timeout=calls[0]
    assert request.get_method()=='DELETE' and request.data is None and timeout==10
    assert request.full_url=='https://demo-fapi.binance.com/fapi/v1/order?origClientOrderId=QDtest000001'
    def fail(*a,**kw):calls.append('failed');raise urllib.error.URLError('isolated timeout')
    monkeypatch.setattr(Opener,'open',fail)
    with pytest.raises(urllib.error.URLError):transport._http_delete_once(b'origClientOrderId=QDtest000001',{})
    assert calls[-1]=='failed' and len(calls)==2
