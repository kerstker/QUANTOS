"""Versioned gold grants share verification but cannot reuse legacy authority."""
from copy import deepcopy
import hashlib
import json

import pytest

from services.validation import demo_submission as auth
from tests.live.test_demo_submission_verification import material, encoded, NOW

BATCH = 'DEMO_GOLD_EXACT_ORDER_BATCH_V1'
ENVELOPE = 'DEMO_GOLD_SIGNED_SUBMISSION_V1'
GRANT = 'DEMO_GOLD_SUBMISSION_GRANT_V1'


def gold_material():
    private, registry, orders, context, envelope = material()
    orders[0].update(symbol='XAUUSDT', price='4000')
    context['batch_sha256'] = auth.batch_sha256(orders, schema=BATCH)
    envelope['schema'] = ENVELOPE
    envelope['payload']['schema'] = GRANT
    envelope['signature_base64url'] = encoded(private.sign(auth.signing_payload(envelope)))
    return private, registry, orders, context, envelope


def test_gold_signature_binds_exact_batch_without_dispatch_authority():
    _, registry, orders, context, envelope = gold_material()
    before = deepcopy((registry, orders, context, envelope))
    parsed = auth.parse_envelope(json.dumps(envelope).encode())
    result = auth.verify(parsed, registry=registry, expected_context=context, orders=orders, now_ms=NOW)
    assert result['schema'] == 'DEMO_GOLD_VERIFIED_SIGNATURE_V1'
    assert result['signature_verified'] and not result['one_time_consumed']
    assert not result['economic_authority']
    assert (registry, orders, context, envelope) == before


def test_gold_batch_identity_and_legacy_scope_stay_separate():
    _, _, orders, _, _ = gold_material()
    expected = hashlib.sha256(auth._canonical({'schema': BATCH, 'orders': orders})).hexdigest()
    assert auth.batch_sha256(orders, schema=BATCH) == expected
    with pytest.raises(auth.DemoSubmissionRejected, match='ORDER_SCOPE'):
        auth.validate_orders(orders)
    _, _, btc, _, _ = material()
    with pytest.raises(auth.DemoSubmissionRejected, match='ORDER_SCOPE'):
        auth.validate_orders(btc, schema=BATCH)
    with pytest.raises(auth.DemoSubmissionRejected):
        auth.validate_orders(orders, schema='anything')


@pytest.mark.parametrize('damage', ['envelope_version', 'grant_version', 'legacy_signature',
                                    'order', 'expired', 'revoked'])
def test_gold_protocol_rejects_cross_version_or_stale_authority(damage):
    private, registry, orders, context, envelope = gold_material()
    at = NOW
    if damage == 'envelope_version':
        envelope['schema'] = 'DEMO_SIGNED_SUBMISSION_V1'
    elif damage == 'grant_version':
        envelope['payload']['schema'] = 'DEMO_SUBMISSION_GRANT_V1'
    elif damage == 'legacy_signature':
        legacy = deepcopy(envelope)
        legacy['schema'] = 'DEMO_SIGNED_SUBMISSION_V1'
        legacy['payload']['schema'] = 'DEMO_SUBMISSION_GRANT_V1'
        envelope['signature_base64url'] = encoded(private.sign(auth.signing_payload(legacy)))
    elif damage == 'order':
        orders[0]['quantity'] = '0.003'
    elif damage == 'expired':
        at = envelope['payload']['expires_at_ms']
    else:
        registry['keys'][0]['status'] = 'REVOKED'
        context['registry_sha256'] = auth.registry_sha256(registry)
        envelope['signature_base64url'] = encoded(private.sign(auth.signing_payload(envelope)))
    with pytest.raises(auth.DemoSubmissionRejected):
        auth.verify(envelope, registry=registry, expected_context=context, orders=orders, now_ms=at)


@pytest.mark.parametrize('field', auth.CONTEXT_HASHES)
def test_every_gold_context_dimension_is_bound(field):
    _, registry, orders, context, envelope = gold_material()
    expected = dict(context, **{field: 'f'*64})
    with pytest.raises(auth.DemoSubmissionRejected):
        auth.verify(envelope, registry=registry, expected_context=expected, orders=orders, now_ms=NOW)


def test_gold_body_signed_in_legacy_domain_is_invalid():
    private, registry, orders, context, envelope = gold_material()
    unsigned = {k: v for k, v in envelope.items() if k != 'signature_base64url'}
    legacy_bytes = auth._canonical({'payload_type': auth._DOMAIN, **unsigned})
    assert legacy_bytes != auth.signing_payload(envelope)
    envelope['signature_base64url'] = encoded(private.sign(legacy_bytes))
    with pytest.raises(auth.DemoSubmissionRejected, match='SIGNATURE_INVALID'):
        auth.verify(envelope, registry=registry, expected_context=context, orders=orders, now_ms=NOW)


def test_valid_legacy_signature_cannot_authorize_gold_orders():
    private, registry, orders, context, envelope = gold_material()
    envelope['schema'] = 'DEMO_SIGNED_SUBMISSION_V1'
    envelope['payload']['schema'] = 'DEMO_SUBMISSION_GRANT_V1'
    envelope['signature_base64url'] = encoded(private.sign(auth.signing_payload(envelope)))
    with pytest.raises(auth.DemoSubmissionRejected, match='ORDER_SCOPE'):
        auth.verify(envelope, registry=registry, expected_context=context, orders=orders, now_ms=NOW)


def test_legacy_signing_bytes_and_batch_digest_are_unchanged():
    _, _, orders, _, envelope = material()
    expected_bytes = auth._canonical({
        'payload_type': 'application/vnd.quantos.demo-submission-grant.v1+json',
        **{k: v for k, v in envelope.items() if k != 'signature_base64url'}})
    expected_hash = hashlib.sha256(auth._canonical({
        'schema': 'DEMO_EXACT_ORDER_BATCH_V1', 'orders': orders})).hexdigest()
    assert auth.signing_payload(envelope) == expected_bytes
    assert auth.batch_sha256(orders) == expected_hash


def test_dispatch_cannot_persist_legacy_grant_for_gold(tmp_path, monkeypatch):
    from live.execution import demo_authority as boundary
    from live.execution.demo_dispatch_store import DemoDispatchStore
    private, registry, orders, context, envelope = gold_material()
    envelope['schema'] = 'DEMO_SIGNED_SUBMISSION_V1'
    envelope['payload']['schema'] = 'DEMO_SUBMISSION_GRANT_V1'
    context['executor_boot_sha256'] = boundary.executor_boot_sha256()
    envelope['signature_base64url'] = encoded(private.sign(auth.signing_payload(envelope)))
    monkeypatch.setattr(boundary, '_read_root_owned_json', lambda _: json.dumps(registry).encode())
    monkeypatch.setattr(boundary.os, 'geteuid', lambda: 2001)
    with DemoDispatchStore(tmp_path/'dispatch.db', create=True) as store:
        with pytest.raises(auth.DemoSubmissionRejected, match='ORDER_SCOPE'):
            store.reserve(json.dumps(envelope).encode(), expected_context=context, orders=orders, now_ms=NOW)
        assert store.snapshot() == []
