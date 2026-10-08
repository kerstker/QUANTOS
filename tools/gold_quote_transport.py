"""One verified HTTPS pool for fixed, keyless gold market-data GETs."""
import re
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import urllib3

HOST = 'https://fapi.binance.com'
MAX_WIRE_BYTES = 10_000_000
PATHS = re.compile(
    r'/fapi/v1/(?:exchangeInfo|premiumIndex\?symbol=XAUUSDT|'
    r'depth\?symbol=XAUUSDT&limit=5|'
    r'fundingRate\?symbol=XAUUSDT(?:&startTime=[0-9]{1,19}&endTime=[0-9]{1,19})?&limit=(?:1|1000)|'
    r'klines\?symbol=XAUUSDT&interval=1d&startTime=[0-9]{1,19}&endTime=[0-9]{1,19}&limit=30)\Z'
)


class PublicQuotePool:
    def __init__(self):
        options = dict(num_pools=1, maxsize=1, block=True,
                       ssl_context=ssl.create_default_context())
        proxies = urllib.request.getproxies()
        proxy = None if urllib.request.proxy_bypass('fapi.binance.com') else proxies.get('https')
        if proxy is None:
            self.manager = urllib3.PoolManager(**options)
        else:
            parsed = urlsplit(proxy)
            if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
                raise ValueError('PUBLIC_QUOTE_PROXY_CONFIG')
            self.manager = urllib3.ProxyManager(proxy, **options)
        self.requests = self.responses = self.failures = self.wire_bytes = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True
        self.manager.clear()

    def fetch(self, path):
        if self.closed or type(path) is not str or PATHS.fullmatch(path) is None:
            raise ValueError('PUBLIC_QUOTE_FIXED_ENDPOINT_REQUIRED')
        response = None
        complete = False
        self.requests += 1
        try:
            response = self.manager.request('GET', HOST + path,
                headers={'Accept': 'application/json', 'Accept-Encoding': 'gzip'},
                redirect=False, retries=False, preload_content=False, decode_content=False,
                timeout=urllib3.Timeout(total=5, connect=5, read=5), pool_timeout=5)
            if response.status != 200:
                raise urllib.error.HTTPError(HOST + path, response.status,
                                             'public market-data status rejected', {}, None)
            wire = response.read(MAX_WIRE_BYTES + 1, decode_content=False)
            if len(wire) > MAX_WIRE_BYTES:
                raise ValueError('public response exceeds size budget')
            encoding = response.headers.get('Content-Encoding', 'identity').lower()
            self.responses += 1
            self.wire_bytes += len(wire)
            complete = True
            return wire, encoding
        except urllib.error.URLError:
            self.failures += 1
            raise
        except (urllib3.exceptions.HTTPError, OSError) as exc:
            self.failures += 1
            reason = exc.reason if isinstance(exc, urllib3.exceptions.MaxRetryError) else exc
            if isinstance(reason, urllib3.exceptions.ProxyError):
                reason = reason.original_error
            if isinstance(reason, urllib3.exceptions.SSLError):
                reason = next((arg for arg in reason.args if isinstance(arg, ssl.SSLError)), reason)
            elif isinstance(reason, urllib3.exceptions.NewConnectionError):
                reason = ConnectionError('public connection failed')
            elif isinstance(reason, (urllib3.exceptions.ReadTimeoutError,
                                     urllib3.exceptions.ConnectTimeoutError)):
                reason = TimeoutError('public request timed out')
            elif isinstance(reason, urllib3.exceptions.ProtocolError):
                reason = ConnectionError('public connection interrupted')
            raise urllib.error.URLError(reason) from exc
        finally:
            if response is not None:
                if not complete:
                    response.close()
                response.release_conn()
                if complete:
                    response.close()

    def metrics(self):
        if self.closed:
            raise ValueError('PUBLIC_QUOTE_POOL_CLOSED')
        return dict(schema='GOLD_PUBLIC_QUOTE_TRANSPORT_METRICS_V1', requests=self.requests,
                    responses=self.responses, failures=self.failures, wire_bytes=self.wire_bytes,
                    connections_created=self.manager.connection_from_url(HOST).num_connections)
