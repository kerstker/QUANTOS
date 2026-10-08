"""External Demo batch signatures: isolated test keys never enter deployment state."""
import base64
from copy import deepcopy
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services.validation import demo_submission as auth

NOW=1789180000000


def encoded(raw):return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def material():
    private=Ed25519PrivateKey.generate()
    public=private.public_key().public_bytes_raw()
    registry={'schema':'DEMO_AUTHORITY_REGISTRY_V1','deployment_sha256':'a'*64,
              'executor_uid':2001,'issuer_uid':2002,'research_uids':[1000],
              'keys':[{'authority_id':'operator-demo','key_id':'test-isolated',
                       'public_key_base64url':encoded(public),'public_key_sha256':hashlib.sha256(public).hexdigest(),
                       'status':'ACTIVE','valid_from_ms':NOW-10000,'valid_until_ms':NOW+100000}]}
    orders=[{'symbol':'BTCUSDC','side':'BUY','type':'LIMIT','timeInForce':'GTX','quantity':'0.002',
             'price':'60000','reduceOnly':False,'positionSide':'BOTH','newClientOrderId':'QDtest000001','newOrderRespType':'ACK'}]
    context={key:'b'*64 for key in auth.CONTEXT_HASHES}
    context.update(deployment_sha256='a'*64,batch_sha256=auth.batch_sha256(orders),
                   registry_sha256=auth.registry_sha256(registry))
    payload={'schema':'DEMO_SUBMISSION_GRANT_V1','environment':'BINANCE_USDM_DEMO','approval_id':'c'*64,
             'issued_at_ms':NOW-1000,'expires_at_ms':NOW+30000,'context':context}
    envelope={'schema':'DEMO_SIGNED_SUBMISSION_V1','authority_id':'operator-demo','key_id':'test-isolated',
              'algorithm':'Ed25519','payload':payload,'signature_base64url':''}
    envelope['signature_base64url']=encoded(private.sign(auth.signing_payload(envelope)))
    return private,registry,orders,context,envelope


def test_exact_external_signature_binds_actual_orders_without_consuming_or_enabling_live():
    _,registry,orders,context,envelope=material()
    result=auth.verify(envelope,registry=registry,expected_context=context,orders=orders,now_ms=NOW)
    assert result['signature_verified'] is True
    assert result['approval_id']=='c'*64 and result['one_time_consumed'] is False
    assert result['environment']=='BINANCE_USDM_DEMO' and result['economic_authority'] is False


@pytest.mark.parametrize('field',['deployment_sha256','executor_boot_sha256','account_ref_sha256','origin_sha256',
    'market_sha256','risk_sha256','recovery_sha256','batch_sha256','mandate_sha256','validation_sha256','registry_sha256'])
def test_each_exact_context_dimension_is_bound(field):
    _,registry,orders,context,envelope=material();context=deepcopy(context);context[field]='f'*64
    with pytest.raises(auth.DemoSubmissionRejected):
        auth.verify(envelope,registry=registry,expected_context=context,orders=orders,now_ms=NOW)


@pytest.mark.parametrize('change',['signature','revoked','key','key_hash','expired','future','ttl','domain','extra','order','duplicate','float','empty_registry','uid'])
def test_untrusted_or_stale_inputs_never_verify(change):
    private,registry,orders,context,envelope=material()
    at=NOW
    if change=='signature':envelope['signature_base64url']=encoded(b'\0'*64)
    elif change=='revoked':registry['keys'][0]['status']='REVOKED'
    elif change=='key':registry['keys'][0]['public_key_base64url']=encoded(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
    elif change=='key_hash':registry['keys'][0]['public_key_sha256']='f'*64
    elif change=='expired':at=NOW+30000
    elif change=='future':at=NOW-1001
    elif change=='ttl':envelope['payload']['expires_at_ms']=NOW+60000
    elif change=='domain':envelope['payload']['environment']='BINANCE_USDM_MAINNET'
    elif change=='extra':envelope['trusted_public_key']=registry['keys'][0]
    elif change=='order':orders[0]['price']='60001'
    elif change=='duplicate':orders.append(deepcopy(orders[0]))
    elif change=='float':orders[0]['quantity']=.002
    elif change=='empty_registry':registry['keys']=[]
    elif change=='uid':registry['executor_uid']=1000
    if change in ('ttl','domain'):envelope['signature_base64url']=encoded(private.sign(auth.signing_payload(envelope)))
    with pytest.raises(auth.DemoSubmissionRejected):
        auth.verify(envelope,registry=registry,expected_context=context,orders=orders,now_ms=at)


def test_batch_order_sequence_and_exact_decimal_bytes_are_bound():
    _,registry,orders,context,envelope=material()
    changed=deepcopy(orders);changed[0]['quantity']='0.0020'
    assert auth.batch_sha256(changed)!=auth.batch_sha256(orders)
    with pytest.raises(auth.DemoSubmissionRejected):
        auth.verify(envelope,registry=registry,expected_context=context,orders=changed,now_ms=NOW)


def test_signed_json_rejects_duplicate_keys_and_secret_like_extra_fields():
    _,_,_,_,envelope=material()
    raw=json.dumps(envelope)
    assert auth.parse_envelope(raw.encode())==envelope
    with pytest.raises(auth.DemoSubmissionRejected):auth.parse_envelope(raw.replace('"algorithm": "Ed25519"','"algorithm":"Ed25519","algorithm":"Ed25519"').encode())
    with pytest.raises(auth.DemoSubmissionRejected):auth.parse_envelope(b'x'*65537)


@pytest.mark.parametrize('change',['revoked','not_yet_valid','no_longer_valid','grant_outlives_key'])
def test_current_registry_binding_still_enforces_revocation_and_key_lifetime(change):
    private,registry,orders,context,envelope=material();key=registry['keys'][0]
    if change=='revoked':key['status']='REVOKED'
    elif change=='not_yet_valid':key['valid_from_ms']=NOW+1
    elif change=='no_longer_valid':key['valid_until_ms']=NOW
    elif change=='grant_outlives_key':key['valid_until_ms']=NOW+29999
    context['registry_sha256']=auth.registry_sha256(registry)
    envelope['signature_base64url']=encoded(private.sign(auth.signing_payload(envelope)))
    with pytest.raises(auth.DemoSubmissionRejected):
        auth.verify(envelope,registry=registry,expected_context=context,orders=orders,now_ms=NOW)
