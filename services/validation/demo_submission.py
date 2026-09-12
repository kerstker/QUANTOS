"""Verify an externally issued, exact Demo batch grant; no signer or dispatch.

The execution boundary must load the registry from its independent deployment,
compute the context from actual inputs, and consume the approval durably before
network submission. Caller-supplied public keys are never grant fields. A valid
signature alone neither consumes an approval nor grants economic/live authority.
"""
from __future__ import annotations

import base64
from decimal import Decimal
import hashlib
import json
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from services.governance.json_integrity import load_json_without_duplicate_keys

CONTEXT_HASHES = ('deployment_sha256','executor_boot_sha256','account_ref_sha256','origin_sha256',
                  'market_sha256','risk_sha256','recovery_sha256','batch_sha256','mandate_sha256',
                  'validation_sha256','registry_sha256')
_DOMAIN = 'application/vnd.quantos.demo-submission-grant.v1+json'
_MAX_BYTES = 65536


class DemoSubmissionRejected(ValueError):
    """A bounded reason code, never a response body, key, or caller exception."""


def _fields(value, names):
    if type(value) is not dict or set(value)!=set(names.split()):
        raise DemoSubmissionRejected('DEMO_AUTHORITY_FIELDS')


def _digest(value):
    if not isinstance(value,str) or re.fullmatch('[a-f0-9]{64}',value) is None:
        raise DemoSubmissionRejected('DEMO_AUTHORITY_DIGEST')


def _integer(value):
    if type(value) is not int or not 0<=value<=9007199254740991:
        raise DemoSubmissionRejected('DEMO_AUTHORITY_INTEGER')
    return value


def _identifier(value):
    if not isinstance(value,str) or re.fullmatch('[A-Za-z0-9_.-]{1,80}',value) is None:
        raise DemoSubmissionRejected('DEMO_AUTHORITY_IDENTIFIER')


def _canonical(value):
    try:
        raw=json.dumps(value,ensure_ascii=True,allow_nan=False,sort_keys=True,separators=(',',':')).encode()
    except (TypeError,ValueError):
        raise DemoSubmissionRejected('DEMO_AUTHORITY_JSON') from None
    if len(raw)>_MAX_BYTES:
        raise DemoSubmissionRejected('DEMO_AUTHORITY_SIZE')
    return raw


def _decode(value, size):
    if not isinstance(value,str) or re.fullmatch('[A-Za-z0-9_-]+',value) is None:
        raise DemoSubmissionRejected('DEMO_AUTHORITY_BASE64')
    try:
        data=base64.urlsafe_b64decode(value+'='*((4-len(value)%4)%4))
    except (ValueError,base64.binascii.Error):
        raise DemoSubmissionRejected('DEMO_AUTHORITY_BASE64') from None
    if len(data)!=size or base64.urlsafe_b64encode(data).rstrip(b'=').decode()!=value:
        raise DemoSubmissionRejected('DEMO_AUTHORITY_BASE64')
    return data


def validate_orders(orders):
    if type(orders) is not list or not 1<=len(orders)<=20:
        raise DemoSubmissionRejected('DEMO_BATCH_BOUNDS')
    seen, symbols = set(), set()
    for order in orders:
        _fields(order,'symbol side type timeInForce quantity price reduceOnly positionSide newClientOrderId newOrderRespType')
        if order['symbol'] not in ('BTCUSDC','BTCUSDT') or order['side'] not in ('BUY','SELL') or order['type']!='LIMIT' or order['timeInForce'] not in ('GTX','IOC'):
            raise DemoSubmissionRejected('DEMO_BATCH_ORDER_SCOPE')
        if type(order['reduceOnly']) is not bool or order['positionSide']!='BOTH' or order['newOrderRespType']!='ACK':
            raise DemoSubmissionRejected('DEMO_BATCH_ORDER_SCOPE')
        for field in ('quantity','price'):
            value=order[field]
            if not isinstance(value,str) or re.fullmatch(r'\d{1,20}(\.\d{1,18})?',value) is None or Decimal(value)<=0:
                raise DemoSubmissionRejected('DEMO_BATCH_EXACT_DECIMAL')
        cid=order['newClientOrderId']
        if not isinstance(cid,str) or re.fullmatch('QD[A-Za-z0-9_-]{8,34}',cid) is None or cid in seen:
            raise DemoSubmissionRejected('DEMO_BATCH_ORDER_IDENTITY')
        seen.add(cid);symbols.add(order['symbol'])
    if len(symbols)!=1:
        raise DemoSubmissionRejected('DEMO_BATCH_MIXED_QUOTE')
    return orders


def batch_sha256(orders):
    return hashlib.sha256(_canonical({'schema':'DEMO_EXACT_ORDER_BATCH_V1','orders':validate_orders(orders)})).hexdigest()


def validate_registry(registry):
    _fields(registry,'schema deployment_sha256 executor_uid issuer_uid research_uids keys')
    if registry['schema']!='DEMO_AUTHORITY_REGISTRY_V1':
        raise DemoSubmissionRejected('DEMO_REGISTRY_SCHEMA')
    _digest(registry['deployment_sha256'])
    executor,issuer=_integer(registry['executor_uid']),_integer(registry['issuer_uid'])
    researchers=registry['research_uids']
    if type(researchers) is not list or not 1<=len(researchers)<=16 or any(_integer(uid)==0 for uid in researchers):
        raise DemoSubmissionRejected('DEMO_REGISTRY_PROCESS_ISOLATION')
    if executor==0 or issuer==0 or executor==issuer or executor in researchers or issuer in researchers or len(set(researchers))!=len(researchers):
        raise DemoSubmissionRejected('DEMO_REGISTRY_PROCESS_ISOLATION')
    keys=registry['keys']
    if type(keys) is not list or len(keys)>32:
        raise DemoSubmissionRejected('DEMO_REGISTRY_KEYS')
    seen=set()
    for key in keys:
        _fields(key,'authority_id key_id public_key_base64url public_key_sha256 status valid_from_ms valid_until_ms')
        _identifier(key['authority_id']);_identifier(key['key_id']);_digest(key['public_key_sha256'])
        identity=key['authority_id'],key['key_id']
        if identity in seen or key['status'] not in ('ACTIVE','REVOKED'):
            raise DemoSubmissionRejected('DEMO_REGISTRY_KEYS')
        seen.add(identity)
        if not _integer(key['valid_from_ms'])<_integer(key['valid_until_ms']):
            raise DemoSubmissionRejected('DEMO_REGISTRY_KEY_TIME')
        if hashlib.sha256(_decode(key['public_key_base64url'],32)).hexdigest()!=key['public_key_sha256']:
            raise DemoSubmissionRejected('DEMO_REGISTRY_KEY_HASH')
    _canonical(registry)
    return registry


def registry_sha256(registry):
    return hashlib.sha256(_canonical(validate_registry(registry))).hexdigest()


def validate_context(context):
    _fields(context,' '.join(CONTEXT_HASHES))
    for key in CONTEXT_HASHES:_digest(context[key])
    return context


def signing_payload(envelope):
    """Canonical bytes for the independent issuer; deliberately contains no signer."""
    return _canonical({'payload_type':_DOMAIN,**{k:v for k,v in envelope.items() if k!='signature_base64url'}})


def _validate_envelope(envelope):
    _fields(envelope,'schema authority_id key_id algorithm payload signature_base64url')
    if envelope['schema']!='DEMO_SIGNED_SUBMISSION_V1' or envelope['algorithm']!='Ed25519':
        raise DemoSubmissionRejected('DEMO_AUTHORITY_SCHEMA')
    _identifier(envelope['authority_id']);_identifier(envelope['key_id'])
    _decode(envelope['signature_base64url'],64)
    p=envelope['payload']
    _fields(p,'schema environment approval_id issued_at_ms expires_at_ms context')
    if p['schema']!='DEMO_SUBMISSION_GRANT_V1' or p['environment']!='BINANCE_USDM_DEMO':
        raise DemoSubmissionRejected('DEMO_AUTHORITY_ENVIRONMENT')
    _digest(p['approval_id']);validate_context(p['context'])
    issued,expiry=_integer(p['issued_at_ms']),_integer(p['expires_at_ms'])
    if not 0<expiry-issued<=60000:
        raise DemoSubmissionRejected('DEMO_AUTHORITY_LIFETIME')
    _canonical(envelope)
    return envelope


def parse_envelope(raw):
    if not isinstance(raw,bytes) or len(raw)>_MAX_BYTES:
        raise DemoSubmissionRejected('DEMO_AUTHORITY_SIZE')
    try:
        return _validate_envelope(load_json_without_duplicate_keys(raw))
    except DemoSubmissionRejected:
        raise
    except Exception:
        raise DemoSubmissionRejected('DEMO_AUTHORITY_JSON') from None


def verify(envelope, *, registry, expected_context, orders, now_ms):
    """Verify against deployment-supplied public material and actual batch inputs.

    This pure verifier does not establish filesystem custody or one-time use.
    The ordinary execution boundary must establish both; research APIs must not
    expose this function as an admission operation or accept its result as proof.
    """
    _validate_envelope(envelope);validate_registry(registry);validate_context(expected_context)
    now=_integer(now_ms);p=envelope['payload'];context=p['context']
    if not p['issued_at_ms']<=now<p['expires_at_ms']:
        raise DemoSubmissionRejected('DEMO_AUTHORITY_STALE')
    if context!=expected_context or context['batch_sha256']!=batch_sha256(orders):
        raise DemoSubmissionRejected('DEMO_AUTHORITY_CONTEXT_MISMATCH')
    if context['registry_sha256']!=registry_sha256(registry) or context['deployment_sha256']!=registry['deployment_sha256']:
        raise DemoSubmissionRejected('DEMO_AUTHORITY_DEPLOYMENT_MISMATCH')
    key=next((k for k in registry['keys'] if (k['authority_id'],k['key_id'])==(envelope['authority_id'],envelope['key_id'])),None)
    if key is None or key['status']!='ACTIVE':
        raise DemoSubmissionRejected('DEMO_AUTHORITY_KEY_UNAVAILABLE')
    if not key['valid_from_ms']<=p['issued_at_ms']<=now<key['valid_until_ms'] or p['expires_at_ms']>key['valid_until_ms']:
        raise DemoSubmissionRejected('DEMO_AUTHORITY_KEY_STALE')
    try:
        Ed25519PublicKey.from_public_bytes(_decode(key['public_key_base64url'],32)).verify(
            _decode(envelope['signature_base64url'],64),signing_payload(envelope))
    except (InvalidSignature,ValueError):
        raise DemoSubmissionRejected('DEMO_AUTHORITY_SIGNATURE_INVALID') from None
    return {'schema':'DEMO_VERIFIED_SIGNATURE_V1','environment':'BINANCE_USDM_DEMO',
            'approval_id':p['approval_id'],'envelope_sha256':hashlib.sha256(_canonical(envelope)).hexdigest(),
            'registry_sha256':context['registry_sha256'],'context':dict(context),
            'authority_id':envelope['authority_id'],'key_id':envelope['key_id'],
            'issued_at_ms':p['issued_at_ms'],'expires_at_ms':p['expires_at_ms'],
            'signature_verified':True,'one_time_consumed':False,'economic_authority':False}
