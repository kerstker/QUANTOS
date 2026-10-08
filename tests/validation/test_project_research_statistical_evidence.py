"""Original native populations, recomputed statistics and independent test keys."""
import copy
import importlib

import pytest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from quantos.contracts.research_evidence_json import digest
from tests.tools.test_registered_research_run import registered_case as registered_case
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments
from tests.research.test_project_risk_replay_reader import gold
from tests.validation import test_project_research_statistics as native
from tests.validation.test_project_research_evidence_authority import b64


def api():
    return importlib.import_module('services.validation.project_research_statistical_evidence')


def build_case(f, arguments, monkeypatch, *, sign=1, profile=None):
    original_arguments, original_policy = native.long_arguments, native.policy
    # Change test inputs before native preparation, signing or immutable writes.
    monkeypatch.setattr(native, 'long_arguments', lambda *a, **k: gold(original_arguments(*a, **k)))
    def declared(*a, **k):
        value = original_policy(*a, **k)
        value['market_scope'] = ['XAUUSDT']
        if profile is not None:
            value['profile'] = profile
        return value
    monkeypatch.setattr(native, 'policy', declared)
    return native.build_statistics(f, arguments, sign=sign)


@pytest.fixture
def statistical_case(registered_case, technical_arguments, monkeypatch):
    return build_case(registered_case, technical_arguments, monkeypatch)


def prepare(c):
    return api().prepare(project_cut=c['cut'], policy=c['policy'], cohort=c['cohort'],
        as_of_ms=c['f']['clock'][0])


def signed_statistics(c, value, *, source_manifest=None):
    from services.validation import project_research_evidence_authority as authority
    selected = c['cohort']['selected_attempt']
    subject = authority.project_subject(c['cut'], invocation_id=selected['invocation_id'],
        native_trial_id=selected['native_trial_id'])
    plan = c['cut']['runs'][selected['invocation_id']]['plan']
    manifest = source_manifest or dict(schema='QUANTOS_RESEARCH_SOURCE_MANIFEST_V1',
        source_identity_sha256=digest(plan['source_identity']), files={'tools/native.py': 'a' * 64})
    private = Ed25519PrivateKey.generate()  # Test only; never installed trust.
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    now = c['f']['clock'][0]
    key = dict(authority_id='independent-statistics-test', key_id='test', principal_id='independent-reviewer',
        algorithm='Ed25519', public_key_base64url=b64(public), public_key_sha256=digest(public), status='ACTIVE',
        purposes=['SOURCE_ATTESTATION', 'RESULT_REVIEW'], evidence_kinds=[subject['evidence_kind']], markets=['XAUUSDT'],
        not_before_ms=now - 1000, not_after_ms=now + 10000)
    trust = dict(schema='QUANTOS_RESEARCH_EVIDENCE_TRUST_V1', generation=1,
        producer_ids=[plan['search']['source']], keys={'test': key})
    materials = authority.material_hashes(c['cut'], manifest, statistical_evidence=value)
    common = dict(subject_sha256=digest(subject), materials_sha256=materials, issued_at_ms=now,
        expires_at_ms=now + 1000, revocation_checked_at_ms=now, revocation_status='GOOD')
    def sign(purpose, statement):
        which = authority.KINDS[purpose][0]
        unsigned = dict(schema=f'QUANTOS_PROJECT_RESEARCH_{which}_ENVELOPE_V3', purpose=purpose,
            authority_id=key['authority_id'], key_id=key['key_id'], statement=statement)
        return dict(unsigned, signature_base64url=b64(private.sign(authority.signing_payload(unsigned))))
    source_checks = {name: dict(status='PASS', evidence_roles=['source_manifest'])
        for name in authority.SOURCE_CHECKS}
    source_checks['STATISTICAL_POLICY_PREREGISTRATION'] = dict(status='PASS',
        evidence_roles=['project_cut', 'statistical_evidence'])
    source = sign('SOURCE_ATTESTATION', dict(common, schema='QUANTOS_PROJECT_RESEARCH_SOURCE_STATEMENT_V3',
        checks=source_checks))
    facts = authority.project_facts(c['cut'], subject, statistical_evidence=value)
    result_checks = {name: dict(status='PASS', evidence_roles=['project_cut'])
        for name in authority.RESULT_CHECKS}
    result_checks['STATISTICAL_REVIEW_REPLAY'] = dict(status='PASS',
        evidence_roles=['project_cut', 'statistical_evidence'])
    result = sign('RESULT_REVIEW', dict(common, schema='QUANTOS_PROJECT_RESEARCH_RESULT_STATEMENT_V3',
        source_envelope_sha256=digest(source), projection_sha256=digest(facts), review_verdict='PASS',
        checks=result_checks))
    snapshot = dict(schema='QUANTOS_PROJECT_RESEARCH_EVIDENCE_SNAPSHOT_V3', subject=subject,
        project_cut=c['cut'], statistical_evidence=value, source_manifest=manifest, source_envelope=source,
        result_envelope=result, trust_registry=trust, expected_registry_sha256=digest(trust),
        read_identity_sha256=digest('isolated-statistical-read'))
    return snapshot, sign


def test_native_statistics_enter_signed_projection_without_oos_or_admission(statistical_case):
    from services.validation import project_research_evidence_authority as authority
    from services.validation import project_research_evidence_artifact as artifact
    c = statistical_case
    value = prepare(c)
    assert value['report']['statistical_decision'] == 'PASS'
    snapshot, _ = signed_statistics(c, value)
    result = authority.verify_project_evidence(snapshot, now_ms=c['f']['clock'][0])
    assert result['projection']['statistical_review']['report_sha256'] == digest(value['report'])
    assert result['projection']['statistical_review']['statistical_decision'] == 'PASS'
    assert 'MULTIPLE_TESTING_AND_BENCHMARK_REVIEW_NOT_COMPLETED' not in result['reasons']
    assert 'NO_INDEPENDENT_OOS_PROOF' in result['reasons']
    assert result['projection']['statistical_review']['profile'] == 'ROBUST_RESEARCH_SIGNIFICANCE_V1'
    assert result['qualification'] == 'INSUFFICIENT'
    assert result['promotion_approved'] is result['submission_enabled'] is False
    saved = artifact.prepare(snapshot=snapshot, assessment=result)
    assert saved['schema'] == 'QUANTOS_PROJECT_RESEARCH_EVIDENCE_ARTIFACT_V3'
    assert artifact.validate_row(artifact.row_fields(saved)) == saved


def test_prescreen_pass_keeps_its_original_limit_and_does_not_grant_admission(
        registered_case, technical_arguments, monkeypatch):
    from services.validation import project_research_evidence_authority as authority
    c = build_case(registered_case, technical_arguments, monkeypatch, profile='RESEARCH_PRESCREEN_V1')
    snapshot, _ = signed_statistics(c, prepare(c))
    result = authority.verify_project_evidence(snapshot, now_ms=c['f']['clock'][0])
    assert result['projection']['statistical_review']['statistical_decision'] == 'PASS'
    assert 'PRESCREEN_PASS_IS_NOT_RESEARCH_SIGNIFICANCE' in result['reasons']
    assert result['qualification'] == 'INSUFFICIENT'
    assert result['promotion_approved'] is result['submission_enabled'] is False


def test_actual_negative_statistics_override_independent_test_pass(registered_case, technical_arguments, monkeypatch):
    from services.validation import project_research_evidence_authority as authority
    c = build_case(registered_case, technical_arguments, monkeypatch, sign=-1)
    value = prepare(c)
    assert value['report']['statistical_decision'] == 'FAIL'
    snapshot, _ = signed_statistics(c, value)
    result = authority.verify_project_evidence(snapshot, now_ms=c['f']['clock'][0])
    assert result['qualification'] == 'REJECTED'
    assert result['projection']['statistical_review']['statistical_decision'] == 'FAIL'
    assert result['promotion_approved'] is result['submission_enabled'] is False


@pytest.mark.parametrize('damage', ['report', 'policy', 'cohort', 'authority', 'future'])
def test_statistics_attachment_is_recomputed_against_original_population(statistical_case, damage):
    c = statistical_case
    value = copy.deepcopy(prepare(c))
    if damage == 'report':
        value['report']['metrics']['dsr'] = .123
    elif damage == 'policy':
        value['policy']['selection_policy']['definition_sha256'] = '0' * 64
    elif damage == 'cohort':
        value['cohort']['attempts'] = value['cohort']['attempts'][:-1]
    elif damage == 'authority':
        value['report']['promotion_approved'] = True
    else:
        value['report']['as_of_ms'] += 1
    with pytest.raises(ValueError):
        api().validate(value, project_cut=c['cut'], as_of_ms=c['f']['clock'][0])


def test_signed_statistics_cannot_select_a_different_original_attempt(statistical_case):
    from services.validation import project_research_evidence_authority as authority
    c = statistical_case
    value = prepare(c)
    other = c['cohort']['attempts'][1]
    subject = authority.project_subject(c['cut'], invocation_id=other['invocation_id'],
        native_trial_id=other['native_trial_id'])
    with pytest.raises(ValueError, match='STATISTICAL_SUBJECT_BINDING'):
        authority.project_facts(c['cut'], subject, statistical_evidence=value)


def test_legacy_envelopes_cannot_authenticate_statistical_material(statistical_case):
    from services.validation import project_research_evidence_authority as authority
    c = statistical_case
    snapshot, _ = signed_statistics(c, prepare(c))
    snapshot['source_envelope']['schema'] = 'QUANTOS_PROJECT_RESEARCH_SOURCE_ENVELOPE_V2'
    with pytest.raises(ValueError):
        authority.verify_project_evidence(snapshot, now_ms=c['f']['clock'][0])


def test_valid_test_signatures_cannot_authenticate_changed_statistics(statistical_case):
    from services.validation import project_research_evidence_authority as authority
    c = statistical_case
    original, sign = signed_statistics(c, prepare(c))
    for damage in ('metrics', 'population', 'future'):
        snapshot = copy.deepcopy(original)
        evidence = snapshot['statistical_evidence']
        if damage == 'metrics':
            evidence['report']['metrics']['dsr'] = .123
        elif damage == 'population':
            evidence['cohort']['attempts'] = evidence['cohort']['attempts'][:-1]
        else:
            evidence['report']['as_of_ms'] += 1
        materials = authority.material_hashes(c['cut'], snapshot['source_manifest'], statistical_evidence=evidence)
        source = snapshot['source_envelope']['statement']
        source['materials_sha256'] = materials
        snapshot['source_envelope'] = sign('SOURCE_ATTESTATION', source)
        result = snapshot['result_envelope']['statement']
        result['materials_sha256'] = materials
        result['source_envelope_sha256'] = digest(snapshot['source_envelope'])
        snapshot['result_envelope'] = sign('RESULT_REVIEW', result)
        with pytest.raises(ValueError):
            authority.verify_project_evidence(snapshot, now_ms=c['f']['clock'][0])
