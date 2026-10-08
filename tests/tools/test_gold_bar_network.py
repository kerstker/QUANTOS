import urllib.request

import pytest


@pytest.mark.parametrize('proxy', [None, 'http://127.0.0.1:8888'])
def test_public_history_route_is_fixed_get_without_credentials(monkeypatch, proxy):
    from tools import gold_bar_network as network
    from live.exchange import demo_network
    calls = []
    tls = object()
    monkeypatch.setattr(demo_network, 'proxy_url', lambda: proxy)
    monkeypatch.setattr(demo_network, 'tls_context', lambda: tls)
    class Inner:
        def open(self, request, timeout):
            calls.append(request)
            return 'public response'
    handlers = []
    monkeypatch.setattr(network.urllib.request, 'build_opener', lambda *args: handlers.extend(args) or Inner())
    monkeypatch.setattr(network.urllib.request, 'HTTPSHandler', lambda **kw: ('TLS', kw['context']))
    opener = network.opener()
    assert ('TLS', tls) in handlers
    route = handlers[0]
    if proxy is None:
        assert route.proxies == {}
    url = 'https://fapi.binance.com/fapi/v1/klines?symbol=XAUUSDT&interval=15m&limit=1500&endTime=899999'
    request = urllib.request.Request(url, headers={'Accept':'application/json', 'Accept-Encoding':'gzip'}, method='GET')
    assert opener.open(request, timeout=20) == 'public response'
    if proxy:
        route.proxy_open(request, proxy, 'https')
        assert request._tunnel_host == 'fapi.binance.com'
        assert request.host == '127.0.0.1:8888'
    for bad in (urllib.request.Request(url, method='POST'),
                urllib.request.Request(url, headers={'X-MBX-APIKEY':'synthetic'}),
                urllib.request.Request(url.replace('klines?', 'order?')),
                urllib.request.Request(url+'&signature=synthetic'),
                urllib.request.Request(url.replace('fapi.binance.com', 'elsewhere.invalid'))):
        with pytest.raises(ValueError, match='PUBLIC_HISTORY_REQUEST'):
            opener.open(bad, timeout=20)
    assert len(calls) == 1
