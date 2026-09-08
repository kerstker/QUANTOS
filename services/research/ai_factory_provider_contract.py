"""Pure configuration contract for external HTTPS or explicitly local inference."""
import hashlib
import json
import ipaddress
import re


# This offline contract accepts a narrow URI grammar. Actual HTTP parsing and
# transport remain in tools/, so importing research configuration cannot load
# the network package. Internationalized hosts must use their ASCII IDNA form.
_ENDPOINT = re.compile(r'(https?)://(\[[0-9a-f:]+\]|[a-z0-9.-]+)(?::([0-9]{1,5}))?(/[^\s?#\\]*)?', re.IGNORECASE)


def _endpoint_parts(endpoint):
    if any(ord(c)<33 or ord(c)==127 for c in endpoint):return None
    match=_ENDPOINT.fullmatch(endpoint)
    if match is None:return None
    scheme,host,port,path=match.groups()
    if host.startswith('['):
        try:ipaddress.IPv6Address(host[1:-1])
        except ValueError:return None
    elif len(host)>253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label,re.IGNORECASE) for label in host.split('.')):
        return None
    if port is not None and not 1<=int(port)<=65535:return None
    return scheme.lower(),host.lower(),int(port) if port is not None else None,path or ''


def provider_status(env):
    endpoint=env.get('QUANTOS_FACTORY_ENDPOINT','').strip()
    model=env.get('QUANTOS_FACTORY_MODEL','').strip()
    mode=env.get('QUANTOS_FACTORY_TRANSPORT','HTTPS')
    valid=False
    parsed=_endpoint_parts(endpoint)
    if parsed is not None:
        scheme,host,port,path=parsed
        if mode=='HTTPS':valid=scheme=='https'
        elif mode=='LOCAL_HTTP':
            valid=bool(scheme=='http' and host in ('127.0.0.1','[::1]')
                       and port is not None and path=='/v1/chat/completions')
    return {'status':'CONFIGURED_UNVERIFIED' if valid and model else 'NOT_CONFIGURED',
            'model':model if valid else '', 'transport':mode, 'connected':False}


def provider_identity(env):
    return hashlib.sha256(json.dumps([env.get('QUANTOS_FACTORY_ENDPOINT','').strip(),
        env.get('QUANTOS_FACTORY_MODEL','').strip(),env.get('QUANTOS_FACTORY_TRANSPORT','HTTPS')],
        separators=(',',':')).encode()).hexdigest()
