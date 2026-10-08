"""Verify externally controlled research attestations and reconsume originals.

This module has no trust installation or signing operation. Its caller must use
the separately governed fixed reader for the registry pin, complete originals
and expected subject. Authentication never upgrades the native technical record.
"""
from __future__ import annotations

import base64
import copy

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from services.validation import research_result_projection as projection

MAX_ENVELOPE = 65_536
MAX_LIFETIME_MS = 86_400_000
SOURCE_CHECKS = ('SOURCE_AUTHENTICITY', 'AVAILABLE_TIME', 'ARCHIVED_SOURCE_IDENTITY', 'VENUE_RULE_IDENTITY')
RESULT_CHECKS = ('EXACT_REPRODUCTION', 'TRIAL_HISTORY_COMPLETE', 'EVALUATION_ISOLATION',
    'ACCOUNT_COSTS', 'CAPACITY_LOAD_BEARING', 'RISK_9_OF_9', 'RISK_HISTORY_REPLAY',
    'DEPLOYMENT_RISK_EQUIVALENCE', 'RESULT_RECONCILIATION')
KINDS = {'SOURCE_ATTESTATION': ('SOURCE', 'QUANTOS_RESEARCH_SOURCE_ATTESTATION_V1'),
         'RESULT_REVIEW': ('RESULT', 'QUANTOS_RESEARCH_RESULT_REVIEW_V1')}
SNAPSHOT_FIELDS = frozenset('schema subject originals source_envelope result_envelope trust_registry '
                          'expected_registry_sha256 read_identity_sha256'.split())
ASSESSMENT_FIELDS = frozenset('schema subject subject_sha256 authentication qualification reasons projection '
    'projection_sha256 source_envelope_sha256 result_envelope_sha256 trust_registry_sha256 checked_at_ms '
    'promotion_approved submission_enabled'.split())
_require = projection._require
_fields = projection._fields
_integer = projection._integer
_sha = projection._sha
_text = projection._text
_digest = projection._digest


def _canonical(value):
    return projection._canonical(value, maximum=MAX_ENVELOPE)


def _b64(value, size):
    _require(type(value) is str and len(value) <= 128 and '=' not in value, 'BASE64')
    try:
        raw = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ValueError('RESEARCH_EVIDENCE_BASE64') from exc
    _require(len(raw) == size and base64.urlsafe_b64encode(raw).decode().rstrip('=') == value, 'BASE64')
    return raw


def _list(value, allowed=None, *, maximum=128):
    _require(type(value) is list and 0 < len(value) <= maximum, 'LIST')
    for item in value:
        _text(item)
    _require(len(set(value)) == len(value) and (allowed is None or set(value) <= set(allowed)), 'LIST_SCOPE')


def _registry(value, expected, subject):
    _fields(value, ('schema', 'generation', 'producer_ids', 'keys'), 'TRUST_FIELDS')
    raw = _canonical(value)
    _require(value['schema'] == 'QUANTOS_RESEARCH_EVIDENCE_TRUST_V1' and _digest(raw) == _sha(expected), 'TRUST_PIN')
    _integer(value['generation'], minimum=1)
    _list(value['producer_ids'])
    _require(subject['producer_id'] in value['producer_ids'], 'PRODUCER')
    _require(type(value['keys']) is dict and 0 < len(value['keys']) <= 128, 'TRUST_KEYS')
    seen = set()
    for label, key in value['keys'].items():
        _text(label)
        _fields(key, 'authority_id key_id principal_id algorithm public_key_base64url public_key_sha256 '
            'status purposes evidence_kinds markets not_before_ms not_after_ms'.split(), 'TRUST_KEY')
        for field in ('authority_id', 'key_id', 'principal_id'):
            _text(key[field])
        pair = (key['authority_id'], key['key_id'])
        _require(pair not in seen, 'DUPLICATE_TRUST_KEY')
        seen.add(pair)
        _require(key['algorithm'] == 'Ed25519' and key['status'] in ('ACTIVE', 'REVOKED'), 'KEY_SCOPE')
        _require(key['principal_id'] not in value['producer_ids'], 'INDEPENDENT_PRINCIPAL')
        _require(_digest(_b64(key['public_key_base64url'], 32)) == _sha(key['public_key_sha256']), 'KEY_HASH')
        _list(key['purposes'], KINDS)
        _list(key['evidence_kinds'], ('BACKTEST', 'FORWARD'))
        _list(key['markets'], ('XAUUSDT',))
        _integer(key['not_before_ms']); _integer(key['not_after_ms'])
        _require(key['not_before_ms'] < key['not_after_ms'], 'KEY_WINDOW')
    return value


def signing_payload(unsigned_envelope):
    """Canonical domain-separated bytes; this is not a signing capability."""
    _fields(unsigned_envelope, ('schema', 'purpose', 'authority_id', 'key_id', 'statement'), 'ENVELOPE_FIELDS')
    purpose = unsigned_envelope['purpose']
    _require(type(purpose) is str and purpose in KINDS, 'PURPOSE')
    which, domain = KINDS[purpose]
    _require(unsigned_envelope['schema'] == f'QUANTOS_RESEARCH_{which}_ENVELOPE_V1', 'ENVELOPE_SCHEMA')
    _text(unsigned_envelope['authority_id']); _text(unsigned_envelope['key_id'])
    return domain.encode('ascii') + b'\0' + _canonical(unsigned_envelope)


def _statement(value, purpose, subject, materials, now_ms):
    fields = set('schema subject_sha256 materials_sha256 issued_at_ms expires_at_ms revocation_checked_at_ms '
                 'revocation_status checks'.split())
    if purpose == 'RESULT_REVIEW':
        fields |= {'source_envelope_sha256', 'projection_sha256', 'review_verdict'}
    _fields(value, fields, 'STATEMENT_FIELDS')
    which = KINDS[purpose][0]
    _require(value['schema'] == f'QUANTOS_RESEARCH_{which}_STATEMENT_V1'
        and value['subject_sha256'] == _digest(subject)
        and _canonical(value['materials_sha256']) == _canonical(materials), 'STATEMENT_BINDING')
    for key in ('issued_at_ms', 'expires_at_ms', 'revocation_checked_at_ms'):
        _integer(value[key])
    _require(value['issued_at_ms'] <= now_ms <= value['expires_at_ms']
        and 0 < value['expires_at_ms']-value['issued_at_ms'] <= MAX_LIFETIME_MS, 'STATEMENT_TIME')
    _require(value['revocation_status'] == 'GOOD'
        and 0 <= now_ms-value['revocation_checked_at_ms'] <= MAX_LIFETIME_MS, 'REVOCATION_TIME')
    checks = SOURCE_CHECKS if purpose == 'SOURCE_ATTESTATION' else RESULT_CHECKS
    _fields(value['checks'], checks, 'CHECKS')
    for item in value['checks'].values():
        _fields(item, ('status', 'evidence_roles'), 'CHECK')
        _require(item['status'] in ('PASS', 'FAIL', 'UNKNOWN'), 'CHECK_STATUS')
        roles = item['evidence_roles']
        _require(type(roles) is list and len(roles) <= len(projection.ROLES), 'CHECK_ROLES')
        for role in roles:
            _require(type(role) is str and role in materials and materials[role] is not None, 'CHECK_ROLE')
        _require(len(set(roles)) == len(roles) and (roles or item['status'] == 'UNKNOWN'), 'CHECK_EVIDENCE')
    if purpose == 'RESULT_REVIEW':
        _sha(value['source_envelope_sha256']); _sha(value['projection_sha256'])
        _require(value['review_verdict'] in ('PASS', 'REJECTED', 'INSUFFICIENT'), 'REVIEW_VERDICT')


def _verify(envelope, *, purpose, registry, subject, materials, now_ms):
    _fields(envelope, ('schema', 'purpose', 'authority_id', 'key_id', 'statement', 'signature_base64url'), 'SIGNED_FIELDS')
    _canonical(envelope)
    _require(envelope['purpose'] == purpose, 'SIGNED_PURPOSE')
    unsigned = {k:v for k,v in envelope.items() if k != 'signature_base64url'}
    payload = signing_payload(unsigned)
    keys = [k for k in registry['keys'].values() if
        k['authority_id'] == envelope['authority_id'] and k['key_id'] == envelope['key_id']]
    _require(len(keys) == 1, 'KEY_NOT_TRUSTED')
    key = keys[0]
    _require(key['status'] == 'ACTIVE' and purpose in key['purposes']
        and subject['evidence_kind'] in key['evidence_kinds'] and subject['market'] in key['markets']
        and key['not_before_ms'] <= now_ms <= key['not_after_ms'], 'KEY_NOT_AUTHORIZED')
    _statement(envelope['statement'], purpose, subject, materials, now_ms)
    try:
        Ed25519PublicKey.from_public_bytes(_b64(key['public_key_base64url'], 32)).verify(
            _b64(envelope['signature_base64url'], 64), payload)
    except InvalidSignature as exc:
        raise ValueError('RESEARCH_EVIDENCE_SIGNATURE') from exc


def verify_evidence(*, subject, originals, source_envelope, result_envelope,
                    trust_registry, expected_registry_sha256, now_ms):
    """Authenticate research facts against a caller-pinned, governed registry.

    No current risk-replay adapter exists. Successful signatures therefore still
    produce INSUFFICIENT (or REJECTED), never economic eligibility or approval.
    """
    _integer(now_ms)
    projection._subject(subject)
    registry = _registry(trust_registry, expected_registry_sha256, subject)
    projected = projection.project_result(subject=subject, originals=originals)
    materials = {k: None if raw is None else _digest(raw) for k,raw in originals.items()}
    for purpose, envelope in (('SOURCE_ATTESTATION', source_envelope), ('RESULT_REVIEW', result_envelope)):
        _verify(envelope, purpose=purpose, registry=registry, subject=subject, materials=materials, now_ms=now_ms)
    statement = result_envelope['statement']
    _require(statement['source_envelope_sha256'] == _digest(source_envelope)
        and statement['projection_sha256'] == _digest(projected), 'REVIEW_BINDING')
    reasons = set(projected['limitations'])
    rejected = statement['review_verdict'] == 'REJECTED'
    if statement['review_verdict'] != 'PASS':
        reasons.add('INDEPENDENT_REVIEW_' + statement['review_verdict'])
    for envelope in (source_envelope, result_envelope):
        for name, check in envelope['statement']['checks'].items():
            if check['status'] != 'PASS':
                reasons.add(name + '_' + check['status'])
            rejected |= check['status'] == 'FAIL'
    return dict(schema='QUANTOS_RESEARCH_EVIDENCE_ASSESSMENT_V1', subject=copy.deepcopy(subject),
        subject_sha256=_digest(subject), authentication='VERIFIED_SOURCE_AND_RESULT',
        qualification='REJECTED' if rejected else 'INSUFFICIENT', reasons=sorted(reasons),
        projection=projected, projection_sha256=_digest(projected), source_envelope_sha256=_digest(source_envelope),
        result_envelope_sha256=_digest(result_envelope), trust_registry_sha256=expected_registry_sha256,
        checked_at_ms=now_ms, promotion_approved=False, submission_enabled=False)


def _snapshot(value, expected_subject):
    _fields(value, SNAPSHOT_FIELDS, 'SNAPSHOT_FIELDS')
    _require(value['schema'] == 'QUANTOS_RESEARCH_EVIDENCE_SNAPSHOT_V1'
        and _canonical(value['subject']) == _canonical(expected_subject), 'SNAPSHOT_SUBJECT')
    _sha(value['read_identity_sha256'])
    projection._fields(value['originals'], projection.ROLES, 'ORIGINAL_ROLES')
    _require(sum(len(raw) for raw in value['originals'].values() if type(raw) is bytes)
             <= projection.MAX_TOTAL, 'TOTAL_BYTES')
    hashes = {}
    for role, raw in value['originals'].items():
        _require(type(raw) is bytes or (role == 'risk_replay' and raw is None), 'ORIGINAL_BYTES')
        _require(raw is None or 0 < len(raw) <= projection.MAX_LEAF, 'ORIGINAL_BYTES')
        hashes[role] = None if raw is None else _digest(raw)
    # Compare actual bytes and complete wrapper content, not the reader's claim alone.
    frozen = {k:v for k,v in value.items() if k != 'originals'}
    frozen['originals'] = hashes
    return projection._canonical(frozen, maximum=4*MAX_ENVELOPE)


def consume_evidence(*, read_snapshot, expected_subject, clock_ms):
    projection._subject(expected_subject)
    first = read_snapshot()
    identity = _snapshot(first, expected_subject)
    started = _integer(clock_ms())
    kwargs = {k:v for k,v in first.items() if k not in ('schema', 'read_identity_sha256')}
    verify_evidence(**kwargs, now_ms=started)
    second = read_snapshot()
    _require(_snapshot(second, expected_subject) == identity, 'READ_CHANGED')
    kwargs = {k:v for k,v in second.items() if k not in ('schema', 'read_identity_sha256')}
    result = verify_evidence(**kwargs, now_ms=started)
    # Take the delivery clock after both native projections and signature checks.
    finished = _integer(clock_ms())
    _require(finished >= started, 'CLOCK_ROLLBACK')
    for envelope in (second['source_envelope'], second['result_envelope']):
        key = next(k for k in second['trust_registry']['keys'].values() if
            k['authority_id'] == envelope['authority_id'] and k['key_id'] == envelope['key_id'])
        _require(key['not_before_ms'] <= finished <= key['not_after_ms'], 'KEY_NOT_AUTHORIZED')
        statement = envelope['statement']
        _require(statement['issued_at_ms'] <= finished <= statement['expires_at_ms']
            and 0 <= finished-statement['revocation_checked_at_ms'] <= MAX_LIFETIME_MS, 'STATEMENT_TIME')
    result['checked_at_ms'] = finished
    return result


def validate_assessment(value, *, read_snapshot, expected_subject, clock_ms):
    _fields(value, ASSESSMENT_FIELDS, 'ASSESSMENT_FIELDS')
    _integer(value['checked_at_ms'])
    actual = consume_evidence(read_snapshot=read_snapshot, expected_subject=expected_subject, clock_ms=clock_ms)
    _require(actual['checked_at_ms'] >= value['checked_at_ms'], 'ASSESSMENT_CLOCK')
    old = {k:v for k,v in value.items() if k != 'checked_at_ms'}
    new = {k:v for k,v in actual.items() if k != 'checked_at_ms'}
    _require(_canonical(old) == _canonical(new), 'ASSESSMENT_CHANGED')
    return actual


__all__ = ['signing_payload', 'verify_evidence', 'consume_evidence', 'validate_assessment']
