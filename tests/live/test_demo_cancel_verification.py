"""Cancellation grants are purpose-bound; test keys stay in isolated fixtures."""
from copy import deepcopy
import json
import pytest
from services.validation import demo_cancellation as cancel
from services.validation import demo_submission as submit
from tests.live.test_demo_submission_verification import material,encoded,NOW


def cancellation_material():
    private,registry,orders,_,submission=material()
    commands=[{'symbol':orders[0]['symbol'],'origClientOrderId':orders[0]['newClientOrderId'],'orderId':'9007199254740993'}]
    context={k:'d'*64 for k in cancel.CONTEXT_HASHES}
    context.update(deployment_sha256=registry['deployment_sha256'],registry_sha256=submit.registry_sha256(registry),
        cancel_batch_sha256=cancel.batch_sha256(commands))
    envelope={'schema':'DEMO_SIGNED_CANCELLATION_V1','authority_id':'operator-demo','key_id':'test-isolated',
        'algorithm':'Ed25519','payload':{'schema':'DEMO_CANCELLATION_GRANT_V1','environment':'BINANCE_USDM_DEMO',
            'approval_id':'e'*64,'issued_at_ms':NOW-1,'expires_at_ms':NOW+30000,'context':context},'signature_base64url':''}
    envelope['signature_base64url']=encoded(private.sign(cancel.signing_payload(envelope)))
    return private,registry,commands,context,envelope,submission


def test_cancellation_binds_original_ids_and_cannot_be_used_for_new_order_submission():
    _,registry,commands,context,envelope,submission=cancellation_material()
    parsed=cancel.parse_envelope(json.dumps(envelope).encode())
    result=cancel.verify(parsed,registry=registry,expected_context=context,commands=commands,now_ms=NOW)
    assert result['signature_verified'] is True and result['one_time_consumed'] is False
    assert result['new_order_submission_enabled'] is False and result['economic_authority'] is False
    with pytest.raises(submit.DemoSubmissionRejected):submit.parse_envelope(json.dumps(envelope).encode())
    with pytest.raises(submit.DemoSubmissionRejected):cancel.parse_envelope(json.dumps(submission).encode())


@pytest.mark.parametrize('field',cancel.CONTEXT_HASHES)
def test_every_cancel_context_dimension_is_bound(field):
    _,registry,commands,context,envelope,_=cancellation_material();context[field]='f'*64
    with pytest.raises(submit.DemoSubmissionRejected):cancel.verify(envelope,registry=registry,expected_context=context,commands=commands,now_ms=NOW)


@pytest.mark.parametrize('failure',['wrong_purpose_signature','signature','revoked','expired','future','duplicate','mainnet','numeric_id','extra','changed_client','changed_venue','empty','mixed_quote'])
def test_cancel_rejects_untrusted_or_nonexact_scope(failure):
    private,registry,commands,context,envelope,_=cancellation_material();at=NOW
    if failure=='wrong_purpose_signature':
        # Submission's strict protocol selector now rejects a cancellation
        # envelope before signing. Still test a malicious cross-domain signature.
        with pytest.raises(submit.DemoSubmissionRejected):submit.signing_payload(envelope)
        body=submit._canonical({'payload_type':submit._DOMAIN,
            **{k:v for k,v in envelope.items() if k!='signature_base64url'}})
        envelope['signature_base64url']=encoded(private.sign(body))
    elif failure=='signature':envelope['signature_base64url']=encoded(b'\0'*64)
    elif failure=='revoked':registry['keys'][0]['status']='REVOKED'
    elif failure=='expired':at=NOW+30000
    elif failure=='future':at=NOW-2
    elif failure=='duplicate':commands.append(deepcopy(commands[0]))
    elif failure=='mainnet':envelope['payload']['environment']='BINANCE_USDM_MAINNET'
    elif failure=='numeric_id':commands[0]['orderId']=9007199254740993
    elif failure=='extra':commands[0]['quantity']='10'
    elif failure=='changed_client':commands[0]['origClientOrderId']='QDdifferent0001'
    elif failure=='changed_venue':commands[0]['orderId']='9007199254740994'
    elif failure=='empty':commands.clear()
    else:commands.append({'symbol':'BTCUSDT','origClientOrderId':'QDother00001','orderId':'5'})
    with pytest.raises(submit.DemoSubmissionRejected):cancel.verify(envelope,registry=registry,expected_context=context,commands=commands,now_ms=at)


def test_cancel_json_rejects_duplicate_members_floats_and_oversize():
    _,_,_,_,envelope,_=cancellation_material();raw=json.dumps(envelope)
    for body in (raw.replace('"algorithm": "Ed25519"','"algorithm":"Ed25519","algorithm":"Ed25519"').encode(),
        raw.replace(str(NOW-1),'1.5').encode(),b'x'*65537):
        with pytest.raises(submit.DemoSubmissionRejected):cancel.parse_envelope(body)
