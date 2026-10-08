"""Isolated public-key contracts over genuine producer records; no installed trust."""
import base64
import copy

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.validation.test_research_result_projection import (
    digest, encode, native_materials as native_materials,
)


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def signed_case(f):
    from services.validation import research_evidence_authority as auth
    from services.validation.research_result_projection import project_result
    private = Ed25519PrivateKey.generate()  # isolated contract identity, never production
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    now = f['now_ms']
    key = dict(authority_id='isolated-independent', key_id='test-key', principal_id='offline-reviewer',
        algorithm='Ed25519', public_key_base64url=_b64(public), public_key_sha256=digest(public),
        status='ACTIVE', purposes=['SOURCE_ATTESTATION', 'RESULT_REVIEW'],
        evidence_kinds=['BACKTEST', 'FORWARD'], markets=['XAUUSDT'],
        not_before_ms=now-1000, not_after_ms=now+10000)
    trust = dict(schema='QUANTOS_RESEARCH_EVIDENCE_TRUST_V1', generation=1,
        producer_ids=['isolated-research'], keys={'isolated': key})
    hashes = {k: None if raw is None else digest(raw) for k, raw in f['originals'].items()}
    common = dict(subject_sha256=digest(f['subject']), materials_sha256=hashes,
        issued_at_ms=now-100, expires_at_ms=now+1000, revocation_checked_at_ms=now-100,
        revocation_status='GOOD')

    def sign(purpose, statement):
        which = 'SOURCE' if purpose == 'SOURCE_ATTESTATION' else 'RESULT'
        unsigned = dict(schema=f'QUANTOS_RESEARCH_{which}_ENVELOPE_V1', purpose=purpose,
            authority_id=key['authority_id'], key_id=key['key_id'], statement=statement)
        return dict(unsigned, signature_base64url=_b64(private.sign(auth.signing_payload(unsigned))))

    source = sign('SOURCE_ATTESTATION', dict(common, schema='QUANTOS_RESEARCH_SOURCE_STATEMENT_V1',
        checks={name: dict(status='PASS', evidence_roles=['source_identity']) for name in auth.SOURCE_CHECKS}))
    projection = project_result(subject=f['subject'], originals=f['originals'])
    result = sign('RESULT_REVIEW', dict(common, schema='QUANTOS_RESEARCH_RESULT_STATEMENT_V1',
        source_envelope_sha256=digest(source), projection_sha256=digest(projection), review_verdict='PASS',
        checks={name: dict(status='PASS', evidence_roles=['record']) for name in auth.RESULT_CHECKS}))
    return dict(schema='QUANTOS_RESEARCH_EVIDENCE_SNAPSHOT_V1', subject=f['subject'],
        originals=f['originals'], source_envelope=source, result_envelope=result,
        trust_registry=trust, expected_registry_sha256=digest(trust), read_identity_sha256=digest('fixed-fixture')), sign


def verify(snapshot, now):
    from services.validation.research_evidence_authority import verify_evidence
    return verify_evidence(**{k: v for k, v in snapshot.items()
        if k not in ('schema', 'read_identity_sha256')}, now_ms=now)


def test_real_originals_same_independent_key_explicit_two_roles_remain_insufficient(native_materials):
    s, _ = signed_case(native_materials)
    result = verify(s, native_materials['now_ms'])
    assert result['authentication'] == 'VERIFIED_SOURCE_AND_RESULT'
    assert result['qualification'] == 'INSUFFICIENT'
    assert result['promotion_approved'] is result['submission_enabled'] is False
    assert 'RISK_REPLAY_ADAPTER_UNAVAILABLE' in result['reasons']


@pytest.mark.parametrize('change', ['empty', 'producer', 'role', 'revoked', 'domain', 'key', 'bool', 'stale'])
def test_trust_purpose_type_and_expiry_rejections(native_materials, change):
    s, _ = signed_case(native_materials)
    key = s['trust_registry']['keys']['isolated']
    now = native_materials['now_ms']
    if change == 'empty':
        s['trust_registry']['keys'] = {}
    elif change == 'producer':
        key['principal_id'] = 'isolated-research'
    elif change == 'role':
        key['purposes'] = ['SOURCE_ATTESTATION']
    elif change == 'revoked':
        key['status'] = 'REVOKED'
    elif change == 'domain':
        s['result_envelope']['signature_base64url'] = s['source_envelope']['signature_base64url']
    elif change == 'key':
        s['result_envelope']['key_id'] = 'supplied-key'
    elif change == 'bool':
        s['trust_registry']['generation'] = True
    else:
        now += 1001
    s['expected_registry_sha256'] = digest(s['trust_registry'])
    with pytest.raises(ValueError):
        verify(s, now)


def test_consumption_rereads_actual_bytes_and_checks_final_time(native_materials, tmp_path):
    from services.validation.research_evidence_authority import consume_evidence
    s, _ = signed_case(native_materials)
    path = tmp_path / 'retained-record.json'
    path.write_bytes(s['originals']['record'])
    reads = []

    def read():
        current = copy.deepcopy(s)
        current['originals']['record'] = path.read_bytes()
        reads.append(1)
        if len(reads) == 1:
            path.write_bytes(path.read_bytes() + b' ')
        return current

    with pytest.raises(ValueError):
        consume_evidence(read_snapshot=read, expected_subject=s['subject'],
            clock_ms=lambda: native_materials['now_ms'])
    assert len(reads) == 2
    times = iter((native_materials['now_ms'], native_materials['now_ms'] + 1001))
    with pytest.raises(ValueError):
        consume_evidence(read_snapshot=lambda: copy.deepcopy(s), expected_subject=s['subject'], clock_ms=lambda: next(times))


def test_historical_assessment_is_not_permanent_or_numeric_alias(native_materials):
    from services.validation.research_evidence_authority import consume_evidence, validate_assessment
    s, _ = signed_case(native_materials)
    now = native_materials['now_ms']
    a = consume_evidence(read_snapshot=lambda: copy.deepcopy(s), expected_subject=s['subject'], clock_ms=lambda: now)
    assert validate_assessment(a, read_snapshot=lambda: copy.deepcopy(s), expected_subject=s['subject'], clock_ms=lambda: now+1)['checked_at_ms'] == now+1
    a['submission_enabled'] = 0
    with pytest.raises(ValueError):
        validate_assessment(a, read_snapshot=lambda: copy.deepcopy(s), expected_subject=s['subject'], clock_ms=lambda: now)


def test_old_engineering_receipt_cannot_be_reinterpreted(native_materials):
    from tests.governance.test_artifact_receipt_auth import _signed_envelope
    s, _ = signed_case(native_materials)
    envelope, _ = _signed_envelope()
    s['source_envelope'] = envelope.model_dump(mode='json')
    with pytest.raises(ValueError):
        verify(s, native_materials['now_ms'])


@pytest.mark.parametrize('change', ['subject', 'materials', 'projection', 'source-envelope', 'float-time',
                                  'lifetime', 'future-revocation', 'missing-risk-role'])
def test_even_resigned_statements_require_exact_originals_and_strict_claims(native_materials, change):
    s, sign = signed_case(native_materials)
    statement = copy.deepcopy(s['result_envelope']['statement'])
    if change == 'subject':
        statement['subject_sha256'] = 'a' * 64
    elif change == 'materials':
        statement['materials_sha256']['input'] = 'b' * 64
    elif change == 'projection':
        statement['projection_sha256'] = 'c' * 64
    elif change == 'source-envelope':
        statement['source_envelope_sha256'] = 'd' * 64
    elif change == 'float-time':
        statement['issued_at_ms'] = float(statement['issued_at_ms'])
    elif change == 'lifetime':
        statement['expires_at_ms'] = statement['issued_at_ms'] + 86400001
    elif change == 'future-revocation':
        statement['revocation_checked_at_ms'] = native_materials['now_ms'] + 1
    else:
        statement['checks']['RISK_HISTORY_REPLAY']['evidence_roles'] = ['risk_replay']
    with pytest.raises(ValueError):
        s['result_envelope'] = sign('RESULT_REVIEW', statement)
        verify(s, native_materials['now_ms'])


@pytest.mark.parametrize('status', ['FAIL', 'UNKNOWN'])
def test_external_review_outcomes_preserve_rejection_or_insufficiency(native_materials, status):
    s, sign = signed_case(native_materials)
    statement = copy.deepcopy(s['result_envelope']['statement'])
    statement['checks']['RESULT_RECONCILIATION']['status'] = status
    s['result_envelope'] = sign('RESULT_REVIEW', statement)
    result = verify(s, native_materials['now_ms'])
    assert result['qualification'] == ('REJECTED' if status == 'FAIL' else 'INSUFFICIENT')


def test_risk_boolean_document_cannot_enable_unimplemented_replay(native_materials):
    f = copy.deepcopy(native_materials)
    f['originals']['risk_replay'] = encode({'risk_replayed': True, 'authoritative': True})
    s, _ = signed_case(f)
    result = verify(s, f['now_ms'])
    assert result['qualification'] == 'INSUFFICIENT'
    assert 'RISK_REPLAY_ADAPTER_UNAVAILABLE' in result['reasons']


@pytest.mark.parametrize('change', ['clock', 'identity', 'trust'])
def test_second_read_cannot_hide_clock_identity_or_revocation_change(native_materials, change):
    from services.validation.research_evidence_authority import consume_evidence
    s, _ = signed_case(native_materials)
    count = []

    def read():
        current = copy.deepcopy(s)
        count.append(1)
        if len(count) == 2:
            if change == 'identity':
                current['read_identity_sha256'] = 'a' * 64
            elif change == 'trust':
                current['trust_registry']['keys']['isolated']['status'] = 'REVOKED'
                current['expected_registry_sha256'] = digest(current['trust_registry'])
        return current

    clocks = iter((native_materials['now_ms'], native_materials['now_ms'] - (change == 'clock')))
    with pytest.raises(ValueError):
        consume_evidence(read_snapshot=read, expected_subject=s['subject'], clock_ms=lambda: next(clocks))


def test_final_clock_is_sampled_after_native_reprojection(native_materials, monkeypatch):
    from services.validation import research_evidence_authority as auth
    s, _ = signed_case(native_materials)
    original = auth.projection.project_result
    calls, now = [], [native_materials['now_ms']]

    def project(**kwargs):
        value = original(**kwargs)
        calls.append(1)
        if len(calls) == 2:
            now[0] += 1001
        return value

    monkeypatch.setattr(auth.projection, 'project_result', project)
    with pytest.raises(ValueError, match='TIME'):
        auth.consume_evidence(read_snapshot=lambda: copy.deepcopy(s), expected_subject=s['subject'], clock_ms=lambda: now[0])


def test_signed_wrapper_uses_its_own_64k_bound(native_materials):
    from services.validation.research_evidence_authority import signing_payload
    s, _ = signed_case(native_materials)
    unsigned = {k:v for k,v in s['result_envelope'].items() if k != 'signature_base64url'}
    unsigned['statement']['padding'] = 'x' * 65536
    with pytest.raises(ValueError, match='BYTES'):
        signing_payload(unsigned)
