"""Deployment-owned optional loopback CONNECT route for the fixed Demo venue.

No environment proxy, caller-selected host, credential, TLS override or retry.
The local proxy transports TLS bytes; the original venue certificate is checked.
"""
from pathlib import Path
import re

from live.execution import demo_authority
from live.exchange import binance_futures as network_adapter
from services.governance.json_integrity import load_json_without_duplicate_keys

_CONFIG=Path('/etc/quantos/demo-submission/network.json')


def validate(raw):
    if type(raw) is not dict or set(raw)!={'schema','https_proxy'} or raw['schema']!='DEMO_NETWORK_V1':
        raise ValueError('DEMO_NETWORK_CONFIG')
    proxy=raw['https_proxy']
    if proxy is not None:
        match=re.fullmatch(r'http://127\.0\.0\.1:([1-9][0-9]{0,4})',proxy) if type(proxy) is str else None
        if match is None or int(match[1])>65535:raise ValueError('DEMO_NETWORK_CONFIG')
    return raw


def proxy_url():
    demo_authority.load_registry_for_executor()
    try:_CONFIG.lstat()
    except FileNotFoundError:return None
    # Existing invalid/unreadable/symlink profiles never turn into direct fallback.
    return validate(load_json_without_duplicate_keys(demo_authority._read_root_owned_json(_CONFIG)))['https_proxy']


def tls_context():
    return network_adapter._demo_tls_context()


def opener():
    return network_adapter._demo_network_opener(proxy_url())


def source_bytes():
    return Path(__file__).read_bytes()+Path(network_adapter.__file__).read_bytes()
