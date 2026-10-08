"""Fixed credential-free public warmup GET using deployment-owned transport."""
import re
import urllib.parse
import urllib.request

from live.exchange import demo_network
from tools.perpetual_public_capture import _NoRedirect

_PATTERN = (r'https://fapi\.binance\.com/fapi/v1/klines\?symbol=XAUUSDT'
            r'&interval=15m&limit=1500&endTime=[0-9]{1,19}')


class _LocalConnect(urllib.request.ProxyHandler):
    def __init__(self, proxy):
        self.address = urllib.parse.urlsplit(proxy).netloc
        super().__init__({'https':proxy})

    def proxy_open(self, request, proxy, type):
        if request.type != 'https' or request.host != 'fapi.binance.com':
            raise ValueError('PUBLIC_HISTORY_REQUEST')
        # Preserve venue TLS/SNI; do not consult inherited NO_PROXY.
        request.set_proxy(self.address, 'http')
        return None


class _PublicHistory:
    def __init__(self, inner):
        self.inner = inner

    def open(self, request, *, timeout):
        if (not isinstance(request, urllib.request.Request) or request.get_method() != 'GET'
                or request.data is not None or re.fullmatch(_PATTERN, request.full_url) is None
                or any(key.lower() not in ('accept', 'accept-encoding') for key, _ in request.header_items())):
            raise ValueError('PUBLIC_HISTORY_REQUEST')
        return self.inner.open(request, timeout=timeout)


def opener():
    proxy = demo_network.proxy_url()
    route = urllib.request.ProxyHandler({}) if proxy is None else _LocalConnect(proxy)
    return _PublicHistory(urllib.request.build_opener(route, _NoRedirect(),
        urllib.request.HTTPSHandler(context=demo_network.tls_context())))
