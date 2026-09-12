"""A protected local CONNECT route cannot change the venue or TLS trust."""
import json
import ssl
import urllib.request

import pytest


def network():
    from live.exchange import demo_network
    return demo_network


@pytest.mark.parametrize('value', [
    'http://remote.example:7890', 'http://localhost:7890',
    'http://user:password@127.0.0.1:7890', 'http://127.0.0.1:7890/',
    'https://127.0.0.1:7890', 'http://127.0.0.1:0',
    'http://127.0.0.1:65536', 'http://127.0.0.1:7890?host=evil',
])
def test_only_explicit_loopback_connect_configuration_is_admitted(value):
    with pytest.raises(ValueError, match='DEMO_NETWORK_CONFIG'):
        network().validate({'schema':'DEMO_NETWORK_V1','https_proxy':value})


def test_missing_optional_route_is_direct_and_never_uses_environment(tmp_path, monkeypatch):
    module=network()
    monkeypatch.setattr(module, '_CONFIG', tmp_path/'missing.json')
    monkeypatch.setattr(module.demo_authority,'load_registry_for_executor',lambda:{})
    monkeypatch.setenv('HTTPS_PROXY','http://untrusted.invalid:8080')
    assert module.proxy_url() is None


def test_bad_present_config_does_not_fall_back_to_direct(tmp_path, monkeypatch):
    module=network();path=tmp_path/'network.json';path.write_text('{}')
    monkeypatch.setattr(module,'_CONFIG',path)
    monkeypatch.setattr(module.demo_authority,'load_registry_for_executor',lambda:{})
    monkeypatch.setattr(module.demo_authority,'_read_root_owned_json',lambda path:b'{}')
    with pytest.raises(ValueError,match='DEMO_NETWORK_CONFIG'):module.proxy_url()


def test_installed_profile_is_read_through_original_root_custody_each_time(tmp_path, monkeypatch):
    module=network();path=tmp_path/'network.json';path.write_text('{}');seen=[]
    monkeypatch.setattr(module,'_CONFIG',path)
    monkeypatch.setattr(module.demo_authority,'load_registry_for_executor',lambda:seen.append('identity'))
    def read(p):
        seen.append(p)
        return json.dumps({'schema':'DEMO_NETWORK_V1','https_proxy':'http://127.0.0.1:7890'}).encode()
    monkeypatch.setattr(module.demo_authority,'_read_root_owned_json',read)
    assert module.proxy_url()==module.proxy_url()=='http://127.0.0.1:7890'
    assert seen==['identity',path,'identity',path]


def test_fixed_root_reader_is_used_and_research_identity_rejected_before_route_read(monkeypatch):
    module=network();seen=[]
    def reject():raise ValueError('WRONG_EXECUTOR')
    monkeypatch.setattr(module.demo_authority,'load_registry_for_executor',reject)
    monkeypatch.setattr(module.demo_authority,'_read_root_owned_json',lambda p:seen.append(p))
    with pytest.raises(ValueError,match='WRONG_EXECUTOR'):module.proxy_url()
    assert not seen


def test_local_route_uses_connect_to_original_tls_host_despite_no_proxy_star(monkeypatch):
    module=network();handlers=[];calls=[]
    monkeypatch.setattr(module,'proxy_url',lambda:'http://127.0.0.1:7890')
    monkeypatch.setenv('NO_PROXY','*')
    monkeypatch.setenv('SSL_CERT_FILE','/untrusted/ca.pem')
    class Open:
        def open(self,request,timeout):calls.append(request);return object()
    monkeypatch.setattr(urllib.request,'build_opener',lambda *h:handlers.extend(h) or Open())
    opener=module.opener()
    request=urllib.request.Request('https://demo-fapi.binance.com/fapi/v1/time')
    opener.open(request,timeout=10)
    proxy=next(h for h in handlers if isinstance(h,urllib.request.ProxyHandler))
    proxy.proxy_open(request,'http://127.0.0.1:7890','https')
    assert request.host=='127.0.0.1:7890'
    assert request._tunnel_host=='demo-fapi.binance.com'
    tls=next(h for h in handlers if isinstance(h,urllib.request.HTTPSHandler))._context
    assert tls.check_hostname and tls.verify_mode==ssl.CERT_REQUIRED
    for url in ('http://demo-fapi.binance.com/fapi/v1/time',
                'https://fapi.binance.com/fapi/v1/time',
                'https://demo-fapi.binance.com.evil/fapi/v1/time'):
        with pytest.raises(ValueError,match='DEMO_NETWORK_ENDPOINT'):
            opener.open(urllib.request.Request(url),timeout=10)
    assert len(calls)==1


def test_redirect_is_rejected_and_proxy_route_never_uses_authentication(monkeypatch):
    module=network()
    monkeypatch.setattr(module,'proxy_url',lambda:'http://127.0.0.1:7890')
    handlers=[]
    monkeypatch.setattr(urllib.request,'build_opener',lambda *h:handlers.extend(h) or object())
    module.opener()
    redirect=next(h for h in handlers if isinstance(h,urllib.request.HTTPRedirectHandler))
    assert redirect.redirect_request(None,None,302,'',{},'https://other.invalid') is None
    request=urllib.request.Request('https://demo-fapi.binance.com/fapi/v1/time')
    next(h for h in handlers if isinstance(h,urllib.request.ProxyHandler)).proxy_open(request,'http://127.0.0.1:7890','https')
    assert not request.has_header('Proxy-authorization')
