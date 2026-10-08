"""Real project producers, unsigned export and original signed-review queue."""
import copy
import json
import time

import pytest

from quantos.contracts.research_evidence_json import canonical, digest
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments


@pytest.mark.parametrize('kind,key', [
    ('registered_review_request', 'native_invocation_id'),
    ('registered_review', 'review_request_id'),
    ('registered_review_recover', 'review_request_id')])
@pytest.mark.parametrize('damage', ['missing', 'path', 'uppercase', 'clock', 'trust'])
def test_invalid_identifiers_cannot_enter_the_review_queue(kind, key, damage):
    from services.research_runner.run_kinds import validate_params
    params = {key: 'a' * 64}
    if damage == 'missing':
        params = {}
    elif damage == 'path':
        params[key] = '../elsewhere'
    elif damage == 'uppercase':
        params[key] = 'A' * 64
    else:
        params[damage] = 0 if damage == 'clock' else '/tmp/self-trust'
    with pytest.raises(ValueError):
        validate_params(kind, params)


def build_originals(tmp_path, monkeypatch, arguments):
    from tools import registered_research_job as producer
    from tools import registered_technical_research_run as native
    from tools.perpetual_replay_research import source_identity
    from services.research.experiment_registry.store import SqliteExperimentStore
    from services.research.experiment_registry.registry import ExperimentRegistry
    from tests.validation import test_project_research_statistics as statistics
    from tests.validation.test_project_research_statistical_evidence import build_case
    from tests.tools.test_registered_risk_replay import policy as risk_policy
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    project, database = producer.workspace_paths()
    database.parent.mkdir(parents=True, mode=0o700)
    with producer._registry(database, initialize=True):
        pass
    store = SqliteExperimentStore(str(database), create=False)
    now = time.time_ns() // 1_000_000
    f = dict(root=database.parent, clock=[now], run=producer.common,
        store=store, registry=ExperimentRegistry(store))
    original_policy = statistics.policy
    captured = {}

    def declared(*args, **kwargs):
        value = original_policy(*args, **kwargs)
        value['study_scope']['registered_at_ms'] = now
        captured['policy'] = value
        return value

    monkeypatch.setattr(statistics, 'policy', declared)
    original_prepare = native.prepare_technical

    def prepare(args, **kwargs):
        # These are original fixture inputs, bound before the original plan.
        # The real preparation boundary retains their policy and risk plan.
        with monkeypatch.context() as temporary:
            temporary.setattr(native, 'prepare_technical', original_prepare)
            return producer.prepare_run(args, search_id=kwargs['search_id'],
                dataset_version=kwargs['dataset_version'], registered_at_ms=kwargs['registered_at_ms'],
                invocation_id=kwargs['invocation_id'], evaluation_core=kwargs['evaluation_core'],
                risk_review_policy=risk_policy(kwargs['evaluation_core']), statistical_policy=captured['policy'])

    monkeypatch.setattr(native, 'prepare_technical', prepare)
    monkeypatch.setattr(native, 'source_identity', source_identity)
    args = copy.deepcopy(arguments)
    args['release_identity'] = source_identity()
    try:
        case = build_case(f, args, monkeypatch)
    finally:
        store.close()
    return case


def test_new_database_keeps_original_provenance_capability_and_prior_database_is_not_migrated(tmp_path):
    from tools import registered_research_job as producer
    from services.research.experiment_registry.store import SqliteExperimentStore
    new = tmp_path/'new'
    new.mkdir(mode=0o700)
    with producer._registry(new/'registry.db', initialize=True) as registry:
        assert registry._store._schema_version() == 5
        assert registry._store._evidence_has_provenance()
    prior = tmp_path/'prior'
    prior.mkdir(mode=0o700)
    path = prior/'registry.db'
    store = SqliteExperimentStore(str(path), create=True)
    store.close()
    path.chmod(0o600)
    before = path.read_bytes()
    with producer._registry(path, initialize=True) as registry:
        assert registry._store._schema_version() == 2
    assert path.read_bytes() == before


def independent_response(packet, material, trust, *, expired=False):
    """Synthetic external key. Production code has no signer or installer."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from services.validation import project_research_evidence_authority as authority
    from tests.validation.test_project_research_evidence_authority import b64
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    now = time.time_ns() // 1_000_000
    issued = now - 100_000 if expired else now
    key = dict(authority_id='EXTERNAL_TEST_REVIEW', key_id='fixture', principal_id='INDEPENDENT_TEST',
        algorithm='Ed25519', public_key_base64url=b64(public), public_key_sha256=digest(public),
        status='ACTIVE', purposes=['SOURCE_ATTESTATION', 'RESULT_REVIEW'], evidence_kinds=['BACKTEST'],
        markets=['XAUUSDT'], not_before_ms=issued - 1000, not_after_ms=now + 7_200_000)
    registry = dict(schema='QUANTOS_RESEARCH_EVIDENCE_TRUST_V1', generation=1,
        producer_ids=['REGISTERED_TECHNICAL_RUNNER_V1'], keys={'fixture': key})
    trust.parent.mkdir(mode=0o700)
    trust.write_bytes(canonical(registry))
    trust.chmod(0o600)
    common = dict(subject_sha256=digest(packet['subject']),
        materials_sha256=authority.material_hashes(packet['project_cut'], packet['source_manifest'],
            statistical_evidence=packet['statistical_evidence']), issued_at_ms=issued,
        expires_at_ms=now - 1 if expired else now + 3_600_000,
        revocation_checked_at_ms=issued, revocation_status='GOOD')

    def sign(purpose, statement):
        unsigned = dict(schema=f'QUANTOS_PROJECT_RESEARCH_{authority.KINDS[purpose][0]}_ENVELOPE_V3',
            purpose=purpose, authority_id=key['authority_id'], key_id=key['key_id'], statement=statement)
        return dict(unsigned, signature_base64url=b64(private.sign(authority.signing_payload(unsigned))))

    source_checks = {name:dict(status='PASS', evidence_roles=['source_manifest']) for name in authority.SOURCE_CHECKS}
    source_checks['STATISTICAL_POLICY_PREREGISTRATION'] = dict(status='PASS', evidence_roles=['project_cut', 'statistical_evidence'])
    source = sign('SOURCE_ATTESTATION', dict(common, schema='QUANTOS_PROJECT_RESEARCH_SOURCE_STATEMENT_V3', checks=source_checks))
    result_checks = {name:dict(status='PASS', evidence_roles=['project_cut']) for name in authority.RESULT_CHECKS}
    result_checks['STATISTICAL_REVIEW_REPLAY'] = dict(status='PASS', evidence_roles=['project_cut', 'statistical_evidence'])
    result = sign('RESULT_REVIEW', dict(common, schema='QUANTOS_PROJECT_RESEARCH_RESULT_STATEMENT_V3',
        source_envelope_sha256=digest(source), projection_sha256=digest(packet['projection']), review_verdict='PASS', checks=result_checks))
    material.mkdir(parents=True, mode=0o700)
    for key in ('subject', 'source_manifest', 'statistical_evidence'):
        path = material/(key+'.json')
        path.write_bytes(canonical(packet[key]))
        path.chmod(0o600)
    for key, value in (('source_envelope', source), ('result_envelope', result)):
        path = material/(key+'.json')
        path.write_bytes(canonical(value))
        path.chmod(0o600)
    return digest(canonical(registry))


def test_actual_queue_exports_complete_material_and_consumes_original_review(tmp_path, monkeypatch, technical_arguments):
    from tools import registered_research_review_job as review
    from tools import registered_research_job as producer
    from tests.research.test_registered_research_job import queued
    from services.research.experiment_registry.store import SqliteExperimentStore
    case = build_originals(tmp_path, monkeypatch, technical_arguments)
    _, database = producer.workspace_paths()
    registry, queue = queued(tmp_path)
    from fastapi.testclient import TestClient
    from services.research_runner import main
    from services.console.main import app
    from services.console.api import research_run_views as views
    monkeypatch.setattr(main, '_queue', queue)
    monkeypatch.setattr(views, '_runs_registry', lambda: registry)
    monkeypatch.setattr(views, 'runtime_state_root', lambda: str(tmp_path))
    http, console = TestClient(main.app), TestClient(app)
    headers = {'X-Operator':'ISOLATED_RESEARCH_TEST', 'X-Runner-Token':main._RUNNER_TOKEN,
        'Idempotency-Key':'http-project-request'}
    invocation = case['plans'][0]['invocation_id']
    body = dict(kind='registered_review_request', params={'native_invocation_id':invocation})
    assert http.post('/run', json=body).status_code == 401
    response = http.post('/run', json=body, headers=headers)
    assert response.status_code == 200, response.text
    uid = response.json()['run_id']
    queue.join()
    run = registry.get(uid)
    assert run.status == 'done', run.error
    report = json.loads(run.metrics['registered_review_report_json'])
    request_id = report['review_request_id']
    packet = review.read_request(request_id)
    assert len(packet['project_cut']['attempts']) == 3
    assert packet['statistical_evidence']['report']['statistical_decision'] == 'PASS'
    assert packet['source_files']['files']
    assert packet['request']['economic_authority'] is packet['request']['submission_enabled'] is False
    exported = console.get('/api/v1/console/registered-review-request', params={'review_request_id':request_id}).json()
    assert exported['read_only'] is True and exported['authoritative'] is False
    assert exported['snapshot_status'] == 'REAL' and exported['material_status'] == 'VALID'
    assert exported['review_request'] == packet
    assert console.get('/api/v1/console/technical-replay', params={'run_id':uid}).json()['technical_replay']['registered_review_report'] == report
    assert http.post('/run', json=body, headers=headers).json()['run_id'] == uid
    before = database.read_bytes()
    for name in ('QUANTOS_PROJECT_REVIEW_TRUST_FILE', 'QUANTOS_PROJECT_REVIEW_TRUST_SHA256', 'QUANTOS_PROJECT_REVIEW_MIN_GENERATION'):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match='TRUST_NOT_CONFIGURED'):
        review.consume_review({'review_request_id': request_id})
    assert database.read_bytes() == before
    material = review.response_directory(request_id)
    trust = tmp_path/'independent-governance/registry.json'
    pin = independent_response(packet, material, trust)
    trust.chmod(0o444)  # Read-only public pin, not a process-owned secret file.
    monkeypatch.setenv('QUANTOS_PROJECT_REVIEW_TRUST_FILE', str(trust))
    monkeypatch.setenv('QUANTOS_PROJECT_REVIEW_TRUST_SHA256', pin)
    monkeypatch.setenv('QUANTOS_PROJECT_REVIEW_MIN_GENERATION', '1')
    review_id = queue.submit('registered_review', {'review_request_id': request_id},
        time.time_ns() // 1_000_000, invocation_id='review-1')
    queue.join()
    reviewed = registry.get(review_id)
    assert reviewed.status == 'done', reviewed.error
    accepted = json.loads(reviewed.metrics['registered_review_report_json'])
    assert accepted['authentication'] == 'VERIFIED_SOURCE_AND_RESULT'
    assert accepted['qualification'] == 'REJECTED'  # Original risk rejection wins over fixture signer PASS.
    assert accepted['statistical_decision'] == 'PASS'
    assert accepted['promotion_approved'] is accepted['economic_authority'] is accepted['submission_enabled'] is False
    store = SqliteExperimentStore(str(database), create=False, read_only=True)
    try:
        assert store._conn.execute('SELECT count(*) FROM evaluations').fetchone()[0] == 1
        assert store._conn.execute('SELECT count(*) FROM evaluation_audit_outbox').fetchone()[0] == 1
        assert store._conn.execute('SELECT count(*) FROM search_evaluations').fetchone()[0] == 0
    finally:
        store.close()
    recovered = review.recover_review({'review_request_id': request_id})
    assert recovered['review_id'] == accepted['review_id']
    assert recovered['qualification'] == 'REJECTED'
    assert queue.submit('registered_review', {'review_request_id': request_id}, 1,
        invocation_id='review-1') == review_id
    # Corruption is also refused by the real read-only download route.
    path = database.parent/'review_requests'/request_id/'source_files.json'
    path.write_bytes(path.read_bytes()+b' ')
    corrupt = console.get('/api/v1/console/registered-review-request', params={'review_request_id':request_id}).json()
    assert corrupt['material_status'] == 'INVALID' and corrupt['review_request'] is None
