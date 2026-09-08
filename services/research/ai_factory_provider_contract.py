"""Pure configuration contract for external HTTPS or explicitly local inference."""
import hashlib
import json
from urllib.parse import urlsplit


def provider_status(env):
    endpoint=env.get('QUANTOS_FACTORY_ENDPOINT','').strip()
    model=env.get('QUANTOS_FACTORY_MODEL','').strip()
    mode=env.get('QUANTOS_FACTORY_TRANSPORT','HTTPS')
    valid=False
    try:
        parsed=urlsplit(endpoint)
        clean=not (parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
                   or any(c.isspace() for c in endpoint))
        port=parsed.port
        if mode=='HTTPS':valid=bool(clean and parsed.scheme=='https' and parsed.hostname and (port is None or port>0))
        elif mode=='LOCAL_HTTP':
            valid=bool(clean and parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','::1')
                       and port is not None and port>0 and parsed.path=='/v1/chat/completions')
    except ValueError:pass
    return {'status':'CONFIGURED_UNVERIFIED' if valid and model else 'NOT_CONFIGURED',
            'model':model if valid else '', 'transport':mode, 'connected':False}


def provider_identity(env):
    return hashlib.sha256(json.dumps([env.get('QUANTOS_FACTORY_ENDPOINT','').strip(),
        env.get('QUANTOS_FACTORY_MODEL','').strip(),env.get('QUANTOS_FACTORY_TRANSPORT','HTTPS')],
        separators=(',',':')).encode()).hexdigest()
