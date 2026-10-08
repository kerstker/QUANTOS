"""Consumer compatibility uses original records, never an asserted readiness flag.

The functional fixture compiles the original exact policy and runs the original
controller with isolated account/worker/order stand-ins. It is not a live cycle.
"""
from copy import deepcopy
import json

import pytest

from scripts.render_demo_feedback_units import render
from tools import demo_functional_cycle as cycle
from tools.demo_operator_setup import digest
from tests.tools.test_demo_functional_cycle import runtime_fixture


def candidates(source='c'*40, executor_uid=2001):
    files = {name: value.encode() for name, value in render(
        release_commit=source, research_state_root='/srv/research/state',
        executor_uid=executor_uid, research_uid=2003, research_gid=2004).items()}
    return dict(files=files, preimages={name: None for name in files}, release_commit=source)


@pytest.fixture
def functional(tmp_path, monkeypatch):
    state, calls = runtime_fixture(tmp_path, monkeypatch, symbol='XAUUSDT', research=True,
                                   delivery_fail=True)
    assert cycle.main(['--source-commit', 'c'*40]) == 2
    registry, policy = cycle.supervisor.configuration('c'*40)
    installation = json.loads((cycle.SETUP/'result.json').read_bytes())
    area = state/'functional-cycle'
    def records(name):
        directory = area/name
        return {p.name: json.loads(p.read_bytes()) for p in directory.iterdir()} if directory.exists() else {}
    evidence = dict(schema='DEMO_FEEDBACK_CONSUMER_EVIDENCE_V1', consumer='functional',
        executor_registry=registry, issuer_registry=deepcopy(registry), policy=policy,
        installation=installation, spec=json.loads((cycle.SETUP/'spec.json').read_bytes()),
        task=json.loads((state/'tasks'/(installation['task_sha256']+'.json')).read_bytes()),
        claim=json.loads((area/'claim.json').read_bytes()),
        initial_result=json.loads((area/'result.json').read_bytes()),
        recovery_results=records('recovery-results'), feedback_results=records('feedback-results'))
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    calls.clear()
    return dict(evidence=evidence, args=candidates(executor_uid=registry['executor_uid']),
                now_ms=policy['valid_until_ms']+1, state=state, area=area,
                calls=calls, before=before, root=tmp_path)


def check(f, **changes):
    from tools.demo_feedback_upgrade_preflight import preflight
    return preflight(**f['args'], consumer_evidence=changes.get('evidence', f['evidence']),
                     now_ms=f['now_ms'])


def test_completed_gold_functional_after_expiry_is_pending_without_io(functional, monkeypatch):
    from tools import demo_feedback_publish as publisher
    from live.execution import demo_authority
    def forbidden(*args, **kwargs):
        pytest.fail('Pure preflight must not read protected state or execute a consumer')
    monkeypatch.setattr(demo_authority, '_read_root_owned_json', forbidden)
    monkeypatch.setattr(publisher, 'run', forbidden)
    monkeypatch.setattr(cycle.demo_task, 'run', forbidden)
    result = check(functional)
    assert result['status'] == 'COMPATIBLE_FUNCTIONAL_PENDING'
    assert result['timer_compatible'] is True and result['pending'] is True
    assert result['consumer_evidence_sha256'] == digest(functional['evidence'])
    assert all(result[name] is False for name in ('custody_verified', 'activation_authorized',
        'installed', 'source_verified', 'submission_enabled', 'economic_authority', 'feedback_delivery_verified'))
    assert functional['calls'] == []
    assert {str(p.relative_to(functional['root'])): p.read_bytes()
            for p in functional['root'].rglob('*') if p.is_file()} == functional['before']


@pytest.mark.parametrize('damage', ['source', 'installation', 'task', 'claim', 'registry',
                                   'uid', 'no_identity', 'incomplete', 'future', 'self_asserted'])
def test_timer_rejects_incompatible_or_incomplete_originals(functional, damage):
    e = deepcopy(functional['evidence'])
    if damage == 'source':
        functional['args'] = candidates(source='d'*40, executor_uid=e['executor_registry']['executor_uid'])
    elif damage == 'installation': e['installation']['status'] = 'INSTALLED_SCOPE_ONLY'
    elif damage == 'task': e['task']['profile']['strategy']['strategy_id'] = 'other'
    elif damage == 'claim': e['claim']['cycle_sha256'] = 'f'*64
    elif damage == 'registry': e['issuer_registry']['deployment_sha256'] = 'e'*64
    elif damage == 'uid': functional['args'] = candidates(executor_uid=2999)
    elif damage == 'no_identity': e['spec'].pop('research_identity')
    elif damage == 'incomplete': e['initial_result']['cash_reconciled'] = False
    elif damage == 'future': e['initial_result']['observed_at_ms'] = functional['now_ms']+1
    else: e['timer_compatible'] = True
    with pytest.raises(ValueError): check(functional, evidence=e)
    assert functional['calls'] == []


def test_latest_recovery_and_latest_matching_receipt_are_reused(functional):
    e = deepcopy(functional['evidence'])
    original = e['initial_result']
    recovered = dict(original, observed_at_ms=original['observed_at_ms']+1,
                     mode='RECOVERY_ONLY', original_source_commit='c'*40)
    e['recovery_results'][digest(recovered)+'.json'] = recovered
    original.update(status='RECOVERY_REQUIRED', cash_reconciled=False)
    assert check(functional, evidence=e)['pending'] is True
    receipt = dict(status='RESEARCH_FEEDBACK_DELIVERED', feedback_sha256='a'*64,
        cycle_sha256=recovered['cycle_sha256'], source_commit='c'*40,
        completion_sha256=cycle.completion_digest(recovered),
        observed_at_ms=recovered['observed_at_ms']+1, source_verified=False,
        economic_authority=False, submission_enabled=False)
    e['feedback_results'][digest(receipt)+'.json'] = receipt
    value = check(functional, evidence=e)
    assert value['status'] == 'COMPATIBLE_FUNCTIONAL_ALREADY_DELIVERED'
    assert value['timer_compatible'] is True and value['pending'] is False
    late = dict(receipt, status='RESEARCH_FEEDBACK_DELIVERY_FAILED', observed_at_ms=receipt['observed_at_ms']+1)
    e['feedback_results'][digest(late)+'.json'] = late
    assert check(functional, evidence=e)['pending'] is True
    bad = dict(recovered, status='RECOVERY_REQUIRED', cash_reconciled=False,
               observed_at_ms=recovered['observed_at_ms']+2)
    e['recovery_results'][digest(bad)+'.json'] = bad
    with pytest.raises(ValueError, match='COMPLETION_REQUIRED'): check(functional, evidence=e)


@pytest.mark.parametrize('delivered', [False, True])
def test_full_receipt_directory_only_allows_original_already_delivered_exit(functional, delivered):
    e = deepcopy(functional['evidence'])
    e['feedback_results'] = {}
    for index in range(64):
        row = dict(status='RESEARCH_FEEDBACK_DELIVERED' if delivered else 'RESEARCH_FEEDBACK_DELIVERY_FAILED',
            feedback_sha256='a'*64, cycle_sha256=digest(e['installation']), source_commit='c'*40,
            completion_sha256=cycle.completion_digest(e['initial_result']),
            observed_at_ms=e['initial_result']['observed_at_ms']+index+1,
            source_verified=False, economic_authority=False, submission_enabled=False)
        e['feedback_results'][digest(row)+'.json'] = row
    if delivered:
        assert check(functional, evidence=e)['pending'] is False
    else:
        with pytest.raises(ValueError, match='HISTORY_BUDGET'): check(functional, evidence=e)


def test_filesystem_selector_still_rejects_null_initial_file_with_valid_recovery(functional):
    area = functional['area']
    result = functional['evidence']['initial_result']
    cycle._retain(area/'recovery-results', result)
    (area/'result.json').write_text('null')
    with pytest.raises(ValueError, match='CYCLE_BINDING'):
        cycle.feedback_completion(area, cycle=digest(functional['evidence']['installation']),
            source_commit='c'*40, now_ms=functional['now_ms'])
    # Actual absence retains the original recovery-only path.
    (area/'result.json').unlink()
    assert cycle.feedback_completion(area, cycle=digest(functional['evidence']['installation']),
        source_commit='c'*40, now_ms=functional['now_ms']) == result


@pytest.mark.parametrize('damage', ['name', 'tie', 'truncated_source', 'budget'])
def test_full_original_recovery_history_cannot_select_an_older_pass(functional, damage):
    e = deepcopy(functional['evidence']); original = e['initial_result']
    recovered = dict(original, mode='RECOVERY_ONLY', original_source_commit='c'*40)
    if damage == 'tie': recovered['status'] = 'RECOVERY_REQUIRED'
    if damage == 'truncated_source': recovered['original_source_commit'] = 'd'*40
    e['recovery_results'][('a'*64 if damage == 'name' else digest(recovered))+'.json'] = recovered
    if damage == 'budget':
        e['recovery_results'] = {}
        for index in range(33):
            row = dict(recovered, observed_at_ms=original['observed_at_ms']+index+1)
            e['recovery_results'][digest(row)+'.json'] = row
    with pytest.raises(ValueError): check(functional, evidence=e)


def direct_evidence(consumer):
    from tests.tools.test_gold_bar_feedback_publish import document
    from tests.research.test_gold_feedback_trigger import feedback
    raw = document()
    if consumer == 'strategy':
        raw = raw['cash_feedback']
        identity = feedback()['research_identity']
        raw.update(research_identity=identity, strategy_id=identity['strategy_id'],
                   candidate_attribution='CONTENT_BOUND_TO_SIGNED_STRATEGY')
        raw['result_hash'] = digest({k: v for k, v in raw.items() if k != 'result_hash'})
    exported = dict(status='BAR_STRATEGY_FEEDBACK_RETAINED' if consumer == 'bar' else 'STRATEGY_FEEDBACK_RETAINED',
        artifact_sha256=digest(raw), feedback_sha256=raw['result_hash'],
        submission_enabled=False, economic_authority=False)
    return dict(schema='DEMO_FEEDBACK_CONSUMER_EVIDENCE_V1', consumer=consumer,
        source_commit='c'*40, executor_uid=2001, cycle_sha256='a'*64, exported=exported, artifact=raw)


@pytest.mark.parametrize('consumer', ['strategy', 'bar'])
def test_gold_direct_publication_does_not_claim_functional_retry(consumer):
    from tools.demo_feedback_upgrade_preflight import preflight
    evidence = direct_evidence(consumer)
    result = preflight(**candidates(), consumer_evidence=evidence, now_ms=3000)
    assert result['status'] == 'DIRECT_PUBLICATION_ONLY'
    assert result['publication_configuration_compatible'] is True
    assert result['timer_compatible'] is False and result['pending'] is None
    assert result['source_namespace'] == ('strategy-bar-feedback' if consumer == 'bar' else 'strategy-feedback')
    with pytest.raises(ValueError):
        preflight(**candidates(source='d'*40), consumer_evidence=evidence, now_ms=3000)
    evidence['artifact']['source_verified'] = True
    with pytest.raises(ValueError):
        preflight(**candidates(), consumer_evidence=evidence, now_ms=3000)


def test_exact_renderer_bytes_remain_mandatory(functional):
    functional['args']['files']['quantos-demo-feedback.service'] = functional['args']['files'][
        'quantos-demo-feedback.service'].replace(b'--feedback-pending', b'--resume')
    with pytest.raises(ValueError, match='MATERIAL_MISMATCH'): check(functional)


def test_plain_btc_completed_cycle_is_not_gold_research_feedback(tmp_path, monkeypatch):
    state, _ = runtime_fixture(tmp_path, monkeypatch)
    assert cycle.main(['--source-commit', 'c'*40]) == 0
    from tools.demo_feedback_upgrade_preflight import preflight
    registry, policy = cycle.supervisor.configuration('c'*40)
    installation = json.loads((cycle.SETUP/'result.json').read_bytes())
    area = state/'functional-cycle'
    evidence = dict(schema='DEMO_FEEDBACK_CONSUMER_EVIDENCE_V1', consumer='functional',
        executor_registry=registry, issuer_registry=registry, policy=policy,
        spec=json.loads((cycle.SETUP/'spec.json').read_bytes()), installation=installation,
        task=json.loads((state/'tasks'/(installation['task_sha256']+'.json')).read_bytes()),
        claim=json.loads((area/'claim.json').read_bytes()),
        initial_result=json.loads((area/'result.json').read_bytes()), recovery_results={}, feedback_results={})
    with pytest.raises(ValueError, match='SETUP_RESEARCH_SCOPE'):
        preflight(**candidates(executor_uid=registry['executor_uid']), consumer_evidence=evidence,
                  now_ms=policy['valid_until_ms']+1)
