"""Single bounded, public-only request used inside a deadline-controlled child."""
import json
import http.client
import math
import ssl
import sys
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

MAX_RESPONSE_BYTES = 262144
PREFIXES = ('https://api.binance.com/api/v3/klines?', *(
    'https://fapi.binance.com/fapi/v1/' + endpoint + '?'
    for endpoint in ('klines', 'markPriceKlines', 'fundingRate', 'openInterest', 'indexPriceKlines', 'depth')))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def retry_after(value):
    if not value:
        return None
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0, math.ceil(seconds)) if math.isfinite(seconds) else None


def request_once(url, timeout, *, include_raw=False):
    if not isinstance(url, str) or not url.startswith(PREFIXES):
        raise ValueError('unregistered public source')
    if type(include_raw) is not bool:
        raise ValueError('raw response selection must be boolean')
    request = urllib.request.Request(url, headers={
        'User-Agent': 'quantos-public-research', 'Accept': 'application/json'}, method='GET')
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            return {'ok': False, 'category': 'RESPONSE_TOO_LARGE'}
        result = {'ok': True, 'payload': json.loads(raw)}
        if include_raw:
            result['raw_utf8'] = raw.decode('utf-8')
        return result
    except urllib.error.HTTPError as exc:
        return {'ok': False, 'category': 'HTTP_ERROR', 'http_status': exc.code,
                'retry_after_seconds': retry_after(exc.headers.get('Retry-After')) if exc.headers else None}
    except (ssl.SSLCertVerificationError,):
        return {'ok': False, 'category': 'TLS_VERIFICATION_FAILED'}
    except urllib.error.URLError as exc:
        return {'ok': False, 'category': 'TLS_VERIFICATION_FAILED' if isinstance(
            exc.reason, ssl.SSLCertVerificationError) else 'TRANSPORT_ERROR'}
    except TimeoutError:
        return {'ok': False, 'category': 'TIMEOUT'}
    except (ValueError, UnicodeError):
        return {'ok': False, 'category': 'INVALID_RESPONSE'}
    except (OSError,http.client.HTTPException):
        return {'ok': False, 'category': 'TRANSPORT_ERROR'}


if __name__ == '__main__':
    if len(sys.argv) not in (3, 4) or len(sys.argv) == 4 and sys.argv[3] != '--include-raw':
        raise ValueError('public URL, timeout and optional --include-raw required')
    print(json.dumps(request_once(sys.argv[1], float(sys.argv[2]),
                                  include_raw=len(sys.argv) == 4), allow_nan=False))
