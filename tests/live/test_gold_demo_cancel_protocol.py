"""Gold cancellation grants bind original identities and survive journal replay."""
import json

import pytest

from services.validation import demo_cancellation as auth, demo_submission
from live.execution import demo_authority
from live.execution.demo_cancel_store import DemoCancelStore, DemoCancelConflict
from tests.live.test_demo_cancel_verification import cancellation_material
from tests.live.test_demo_submission_verification import encoded, NOW

BATCH = 'DEMO_GOLD_EXACT_CANCEL_BATCH_V1'


def material():
    private, registry, commands, context, envelope, _ = cancellation_material()
    commands[0]['symbol'] = 'XAUUSDT'
    context['cancel_batch_sha256'] = auth.batch_sha256(commands, schema=BATCH)
    context['executor_boot_sha256'] = demo_authority.executor_boot_sha256()
    envelope['schema'] = 'DEMO_GOLD_SIGNED_CANCELLATION_V1'
    envelope['payload']['schema'] = 'DEMO_GOLD_CANCELLATION_GRANT_V1'
    envelope['signature_base64url'] = encoded(private.sign(auth.signing_payload(envelope)))
    return private, registry, commands, context, envelope


def test_gold_cancel_grant_is_purpose_bound_and_exact():
    _, registry, commands, context, envelope = material()
    result = auth.verify(auth.parse_envelope(json.dumps(envelope).encode()), registry=registry,
                         expected_context=context, commands=commands, now_ms=NOW)
    assert result['schema'] == 'DEMO_GOLD_VERIFIED_CANCEL_SIGNATURE_V1'
    assert result['signature_verified'] and not result['new_order_submission_enabled']
    with pytest.raises(demo_submission.DemoSubmissionRejected):
        auth.validate_commands(commands)
    with pytest.raises(demo_submission.DemoSubmissionRejected):
        demo_submission.parse_envelope(json.dumps(envelope).encode())


@pytest.mark.parametrize('change', ['client', 'venue', 'legacy_domain', 'expired', 'revoked'])
def test_gold_cancel_cannot_reinterpret_identity_or_authority(change):
    private, registry, commands, context, envelope = material()
    at = NOW
    if change == 'client':
        commands[0]['origClientOrderId'] = 'QDdifferent0001'
    elif change == 'venue':
        commands[0]['orderId'] = '9'
    elif change == 'legacy_domain':
        body = demo_submission._canonical({'payload_type': auth._DOMAIN,
            **{k: v for k, v in envelope.items() if k != 'signature_base64url'}})
        envelope['signature_base64url'] = encoded(private.sign(body))
    elif change == 'expired':
        at = envelope['payload']['expires_at_ms']
    else:
        registry['keys'][0]['status'] = 'REVOKED'
    with pytest.raises(demo_submission.DemoSubmissionRejected):
        auth.verify(envelope, registry=registry, expected_context=context, commands=commands, now_ms=at)


def test_gold_cancel_claim_survives_restart_without_reclaim(tmp_path, monkeypatch):
    _, registry, commands, context, envelope = material()
    monkeypatch.setattr(demo_authority, '_read_root_owned_json', lambda _: json.dumps(registry).encode())
    monkeypatch.setattr(demo_authority.os, 'geteuid', lambda: 2001)
    path = tmp_path/'cancel.db'
    raw = json.dumps(envelope).encode()
    with DemoCancelStore(path, create=True) as store:
        grant = store.reserve(raw, context=context, commands=commands, now_ms=NOW)
        store.claim(grant['record_hash'], commands[0]['origClientOrderId'],
                    request_sha256='a'*64, now_ms=NOW+1)
        rows = store.snapshot()
    with DemoCancelStore(path) as store:
        assert store.snapshot() == rows
        with pytest.raises(DemoCancelConflict):
            store.claim(grant['record_hash'], commands[0]['origClientOrderId'],
                        request_sha256='a'*64, now_ms=NOW+2)
        assert store.snapshot() == rows


@pytest.mark.parametrize('field', auth.CONTEXT_HASHES)
def test_each_gold_cancel_context_dimension_is_bound(field):
    _, registry, commands, context, envelope = material()
    expected = dict(context, **{field: 'f'*64})
    with pytest.raises(demo_submission.DemoSubmissionRejected):
        auth.verify(envelope, registry=registry, expected_context=expected, commands=commands, now_ms=NOW)


def test_old_cancel_signed_bytes_remain_unchanged():
    _, _, commands, _, envelope, _ = cancellation_material()
    body = demo_submission._canonical({'payload_type': auth._DOMAIN,
        **{k: v for k, v in envelope.items() if k != 'signature_base64url'}})
    assert auth.signing_payload(envelope) == body
    assert auth.validate_commands(commands) == commands
