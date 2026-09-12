"""Verify exact, independently issued cancellation scope; no signing or I/O."""
import hashlib
import json
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from services.validation.demo_submission import (
    DemoSubmissionRejected,_fields,_integer,_digest,_identifier,_canonical,_decode,
    validate_registry,registry_sha256,
)

CONTEXT_HASHES=('deployment_sha256','executor_boot_sha256','account_ref_sha256','registry_sha256',
    'reservation_sha256','original_batch_sha256','cancel_batch_sha256','query_snapshot_sha256')
_DOMAIN='application/vnd.quantos.demo-cancellation-grant.v1+json'


def validate_commands(commands):
    if type(commands) is not list or not 1<=len(commands)<=20:raise DemoSubmissionRejected('DEMO_CANCEL_BOUNDS')
    clients=set();venues=set();symbols=set()
    for command in commands:
        _fields(command,'symbol origClientOrderId orderId')
        symbol,client,venue=(command[k] for k in ('symbol','origClientOrderId','orderId'))
        if (symbol not in ('BTCUSDC','BTCUSDT') or type(client) is not str or
            re.fullmatch('QD[A-Za-z0-9_-]{8,34}',client) is None or client in clients or
            type(venue) is not str or re.fullmatch('[1-9][0-9]{0,18}',venue) is None or int(venue)>2**63-1 or venue in venues):
            raise DemoSubmissionRejected('DEMO_CANCEL_EXACT_IDENTITY')
        clients.add(client);venues.add(venue);symbols.add(symbol)
    if len(symbols)!=1:raise DemoSubmissionRejected('DEMO_CANCEL_MIXED_QUOTE')
    return commands


def batch_sha256(commands):
    return hashlib.sha256(_canonical({'schema':'DEMO_EXACT_CANCEL_BATCH_V1','commands':validate_commands(commands)})).hexdigest()


def validate_context(context):
    _fields(context,' '.join(CONTEXT_HASHES))
    for k in CONTEXT_HASHES:_digest(context[k])
    return context


def signing_payload(envelope):
    return _canonical({'payload_type':_DOMAIN,**{k:v for k,v in envelope.items() if k!='signature_base64url'}})


def _validate(envelope):
    _fields(envelope,'schema authority_id key_id algorithm payload signature_base64url')
    if envelope['schema']!='DEMO_SIGNED_CANCELLATION_V1' or envelope['algorithm']!='Ed25519':
        raise DemoSubmissionRejected('DEMO_CANCEL_AUTHORITY_SCHEMA')
    _identifier(envelope['authority_id']);_identifier(envelope['key_id']);_decode(envelope['signature_base64url'],64)
    p=envelope['payload'];_fields(p,'schema environment approval_id issued_at_ms expires_at_ms context')
    if p['schema']!='DEMO_CANCELLATION_GRANT_V1' or p['environment']!='BINANCE_USDM_DEMO':
        raise DemoSubmissionRejected('DEMO_CANCEL_AUTHORITY_ENVIRONMENT')
    _digest(p['approval_id']);validate_context(p['context'])
    if not 0<_integer(p['expires_at_ms'])-_integer(p['issued_at_ms'])<=60000:
        raise DemoSubmissionRejected('DEMO_CANCEL_AUTHORITY_LIFETIME')
    _canonical(envelope)
    return envelope


def parse_envelope(raw):
    if type(raw) is not bytes or len(raw)>65536:raise DemoSubmissionRejected('DEMO_CANCEL_AUTHORITY_SIZE')
    try:
        def unique(pairs):
            value={}
            for key,item in pairs:
                if key in value:raise DemoSubmissionRejected('DEMO_CANCEL_AUTHORITY_JSON')
                value[key]=item
            return value
        def inexact(_):raise DemoSubmissionRejected('DEMO_CANCEL_AUTHORITY_JSON')
        return _validate(json.loads(raw.decode('utf-8'),object_pairs_hook=unique,parse_float=inexact,parse_constant=inexact))
    except DemoSubmissionRejected:raise
    except Exception:raise DemoSubmissionRejected('DEMO_CANCEL_AUTHORITY_JSON') from None


def verify(envelope,*,registry,expected_context,commands,now_ms):
    _validate(envelope);validate_registry(registry);validate_context(expected_context)
    now=_integer(now_ms);p=envelope['payload'];context=p['context']
    if not p['issued_at_ms']<=now<p['expires_at_ms']:raise DemoSubmissionRejected('DEMO_CANCEL_AUTHORITY_STALE')
    if context!=expected_context or context['cancel_batch_sha256']!=batch_sha256(commands):
        raise DemoSubmissionRejected('DEMO_CANCEL_AUTHORITY_CONTEXT')
    if context['registry_sha256']!=registry_sha256(registry) or context['deployment_sha256']!=registry['deployment_sha256']:
        raise DemoSubmissionRejected('DEMO_CANCEL_AUTHORITY_DEPLOYMENT')
    key=next((k for k in registry['keys'] if (k['authority_id'],k['key_id'])==(envelope['authority_id'],envelope['key_id'])),None)
    if key is None or key['status']!='ACTIVE':raise DemoSubmissionRejected('DEMO_CANCEL_AUTHORITY_KEY')
    if not key['valid_from_ms']<=p['issued_at_ms']<=now<key['valid_until_ms'] or p['expires_at_ms']>key['valid_until_ms']:
        raise DemoSubmissionRejected('DEMO_CANCEL_AUTHORITY_KEY_STALE')
    try:
        Ed25519PublicKey.from_public_bytes(_decode(key['public_key_base64url'],32)).verify(
            _decode(envelope['signature_base64url'],64),signing_payload(envelope))
    except (InvalidSignature,ValueError):raise DemoSubmissionRejected('DEMO_CANCEL_AUTHORITY_SIGNATURE') from None
    return {'schema':'DEMO_VERIFIED_CANCEL_SIGNATURE_V1','environment':'BINANCE_USDM_DEMO',
        'approval_id':p['approval_id'],'envelope_sha256':hashlib.sha256(_canonical(envelope)).hexdigest(),
        'context':dict(context),'issued_at_ms':p['issued_at_ms'],'expires_at_ms':p['expires_at_ms'],
        'signature_verified':True,'one_time_consumed':False,'new_order_submission_enabled':False,'economic_authority':False}
