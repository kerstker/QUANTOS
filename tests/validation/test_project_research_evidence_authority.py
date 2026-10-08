"""Independent test signatures over complete original multi-ledger projects."""
import base64
import copy

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from quantos.contracts.research_evidence_json import digest
from tests.tools.test_registered_research_run import registered_case as registered_case, execute


def b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def signed_project(cut, invocation_id, *, now, source_manifest=None, reports=None):
    from services.validation import project_research_evidence_authority as auth
    plan = cut['runs'][invocation_id]['plan']
    trial = plan['trials'][0]
    subject = auth.project_subject(cut, invocation_id=invocation_id,
                                   native_trial_id=trial['native_trial_id'])
    manifest = source_manifest or dict(schema='QUANTOS_RESEARCH_SOURCE_MANIFEST_V1',
        source_identity_sha256=digest(plan['source_identity']), files={'tools/native.py': 'a'*64})
    private = Ed25519PrivateKey.generate()  # Isolated tests; never an installed trust root.
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key = dict(authority_id='independent-test', key_id='test', principal_id='independent-reviewer',
        algorithm='Ed25519', public_key_base64url=b64(public), public_key_sha256=digest(public),
        status='ACTIVE', purposes=['SOURCE_ATTESTATION', 'RESULT_REVIEW'],
        evidence_kinds=['BACKTEST', 'FORWARD'], markets=['XAUUSDT'],
        not_before_ms=now-1000, not_after_ms=now+10000)
    trust = dict(schema='QUANTOS_RESEARCH_EVIDENCE_TRUST_V1', generation=1,
                 producer_ids=[plan['search']['source']], keys={'test': key})
    materials = auth.material_hashes(cut, manifest)
    common = dict(subject_sha256=digest(subject), materials_sha256=materials,
        issued_at_ms=now, expires_at_ms=now+1000,
        revocation_checked_at_ms=now-100, revocation_status='GOOD')
    def sign(purpose, statement):
        which = auth.KINDS[purpose][0]
        unsigned = dict(schema=f'QUANTOS_PROJECT_RESEARCH_{which}_ENVELOPE_V2',
            purpose=purpose, authority_id=key['authority_id'], key_id=key['key_id'], statement=statement)
        return dict(unsigned, signature_base64url=b64(private.sign(auth.signing_payload(unsigned))))
    source = sign('SOURCE_ATTESTATION', dict(common,
        schema='QUANTOS_PROJECT_RESEARCH_SOURCE_STATEMENT_V2',
        checks={name: dict(status='PASS', evidence_roles=['source_manifest']) for name in auth.SOURCE_CHECKS}))
    projected = auth.project_facts(cut, subject, reports=reports)
    result = sign('RESULT_REVIEW', dict(common,
        schema='QUANTOS_PROJECT_RESEARCH_RESULT_STATEMENT_V2',
        source_envelope_sha256=digest(source), projection_sha256=digest(projected), review_verdict='PASS',
        checks={name: dict(status='PASS', evidence_roles=['project_cut']) for name in auth.RESULT_CHECKS}))
    return dict(schema=auth.SNAPSHOT_SCHEMA, subject=subject, project_cut=cut,
        source_manifest=manifest, source_envelope=source, result_envelope=result,
        trust_registry=trust, expected_registry_sha256=digest(trust),
        read_identity_sha256=digest('isolated-read')), sign


@pytest.fixture
def project_review(registered_case):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    f = registered_case
    plans = [f['prepare'](name) for name in ('one', 'two')]
    for name in ('one', 'two'):
        execute(f, name)
    # A third real pre-execution plan remains visible in the denominator.
    f['prepare']('pending')
    reader = ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db')
    snapshot, sign = signed_project(reader(), plans[0]['invocation_id'], now=f['clock'][0])
    return f, reader, snapshot, sign


def verify(snapshot, now):
    from services.validation.project_research_evidence_authority import verify_project_evidence
    return verify_project_evidence(snapshot, now_ms=now)


def test_two_real_ledgers_and_pending_attempts_cannot_be_relabelled_eligible(project_review):
    f, _, snapshot, _ = project_review
    result = verify(snapshot, f['clock'][0])
    assert result['authentication'] == 'VERIFIED_SOURCE_AND_RESULT'
    assert result['qualification'] == 'INSUFFICIENT'
    assert result['projection']['attempt_count'] == 9
    assert result['projection']['unique_definition_count'] == 3
    assert result['projection']['unfinished_attempt_count'] == 3
    assert 'EVALUATION_PLAN_NOT_PREREGISTERED' in result['reasons']
    assert 'PROJECT_RUNS_UNFINISHED' in result['reasons']
    assert result['promotion_approved'] is result['submission_enabled'] is False


@pytest.mark.parametrize('damage', ['drop-run', 'count', 'select-other', 'source', 'domain', 'revoked', 'expiry'])
def test_original_population_source_selection_and_signature_changes_reject(project_review, damage):
    f, _, original, _ = project_review
    snapshot = copy.deepcopy(original)
    now = f['clock'][0]
    if damage == 'drop-run':
        del snapshot['project_cut']['runs'][next(iter(snapshot['project_cut']['runs']))]
    elif damage == 'count':
        snapshot['project_cut']['attempt_count'] = 1
    elif damage == 'select-other':
        snapshot['subject']['native_trial_id'] = 'f'*64
    elif damage == 'source':
        snapshot['source_manifest']['files']['tools/native.py'] = 'b'*64
    elif damage == 'domain':
        snapshot['result_envelope']['signature_base64url'] = snapshot['source_envelope']['signature_base64url']
    elif damage == 'revoked':
        snapshot['trust_registry']['keys']['test']['status'] = 'REVOKED'
        snapshot['expected_registry_sha256'] = digest(snapshot['trust_registry'])
    else:
        now += 1001
    with pytest.raises(ValueError):
        verify(snapshot, now)


def test_new_real_attempt_between_reads_rejects_before_delivery(project_review):
    from services.validation.project_research_evidence_authority import consume_project_evidence
    f, reader, original, _ = project_review
    calls = []
    def read():
        value = copy.deepcopy(original)
        value['project_cut'] = reader()
        calls.append(1)
        if len(calls) == 1:
            f['prepare']('later')
        return value
    with pytest.raises(ValueError):
        consume_project_evidence(read_snapshot=read, expected_subject=original['subject'], clock_ms=lambda:f['clock'][0])
    assert len(calls) == 2


def test_expiry_during_verification_and_clock_rollback_reject(project_review):
    from services.validation.project_research_evidence_authority import consume_project_evidence
    f, _, snapshot, _ = project_review
    for finish in (f['clock'][0]-1, f['clock'][0]+1001):
        clock = iter((f['clock'][0], finish))
        with pytest.raises(ValueError):
            consume_project_evidence(read_snapshot=lambda:copy.deepcopy(snapshot),
                expected_subject=snapshot['subject'], clock_ms=lambda:next(clock))
