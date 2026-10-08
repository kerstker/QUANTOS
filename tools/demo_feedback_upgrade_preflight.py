"""Pure compatibility review for the existing feedback consumers.

The caller must capture original fixed-path records, complete history directory
contents and custody under the original controller/maintenance locks. These
content checks cannot establish that a caller supplied the complete directory.
No filesystem, service, account, issuer or publication operation runs here.
"""
from live.execution.demo_issuer_policy import require, validate_policy
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.validation.demo_submission import validate_registry, registry_sha256
from tools import demo_feedback_publish as publisher
from tools import demo_feedback_upgrade as upgrade
from tools import demo_functional_cycle as functional
from tools import demo_operator_setup as setup


SCHEMA = 'DEMO_FEEDBACK_UPGRADE_PREFLIGHT_V1'
EVIDENCE_SCHEMA = 'DEMO_FEEDBACK_CONSUMER_EVIDENCE_V1'
_FUNCTIONAL_FIELDS = set(('schema consumer executor_registry issuer_registry policy spec installation '
    'task claim initial_result recovery_results feedback_results').split())
_DIRECT_FIELDS = set(('schema consumer source_commit executor_uid cycle_sha256 exported artifact').split())
# Original root records retain their 64 KiB limit; the publisher has its own
# separate 2 MB artifact limit. No generic reader limit is enlarged.
_RECORD_MAXIMUM = 65536
_EVIDENCE_MAXIMUM = 8 * 1024 * 1024


def _bounded(value, maximum):
    body = setup.canonical(value)
    require(len(body) <= maximum, 'FEEDBACK_PREFLIGHT_RECORD_BUDGET')
    return body


def _functional(evidence, *, source_commit, publication, now_ms):
    require(set(evidence) == _FUNCTIONAL_FIELDS, 'FEEDBACK_PREFLIGHT_EVIDENCE_FIELDS')
    for key in _FUNCTIONAL_FIELDS - {'schema', 'consumer', 'recovery_results', 'feedback_results'}:
        require(type(evidence[key]) is dict or key == 'initial_result' and evidence[key] is None,
                'FEEDBACK_PREFLIGHT_RECORD')
        _bounded(evidence[key], _RECORD_MAXIMUM)
    for name, maximum in (('recovery_results', 32), ('feedback_results', 64)):
        records = evidence[name]
        require(type(records) is dict and len(records) <= maximum, 'FEEDBACK_PREFLIGHT_HISTORY_BUDGET')
        for record in records.values():
            require(type(record) is dict, 'FEEDBACK_PREFLIGHT_RECORD')
            _bounded(record, _RECORD_MAXIMUM)
    registry = validate_registry(evidence['executor_registry'])
    require(evidence['issuer_registry'] == registry, 'FUNCTIONAL_FEEDBACK_REGISTRY_MIRROR')
    policy = validate_policy(evidence['policy'])
    require(policy['registry_sha256'] == registry_sha256(registry)
        and policy['deployment_sha256'] == registry['deployment_sha256']
        and policy['executor_release_commit'] == source_commit, 'FUNCTIONAL_FEEDBACK_REGISTRY')
    require(publication['executor_uid'] == registry['executor_uid'], 'FEEDBACK_PREFLIGHT_EXECUTOR')
    spec = setup.spec_check(evidence['spec'])
    installed = evidence['installation']
    require(installed.get('status') == 'INSTALLED_PENDING_CYCLE'
        and installed.get('source_commit') == spec['source_commit'] == source_commit
        and installed.get('spec_sha256') == setup.digest(spec)
        and policy['account_ref_sha256'] == spec['account_ref_sha256'], 'FUNCTIONAL_FEEDBACK_INSTALLATION')
    identity = setup.research_identity_for(spec, as_of_ms=now_ms)
    cycle = setup.digest(installed)
    claim = evidence['claim']
    require(claim.get('cycle_sha256') == cycle and claim.get('source_commit') == source_commit,
            'FUNCTIONAL_FEEDBACK_CYCLE_BINDING')
    result = functional.feedback_completion_records(evidence['initial_result'], evidence['recovery_results'],
        cycle=cycle, source_commit=source_commit, now_ms=now_ms)
    template = evidence['task']
    profile = template.get('profile', {})
    require(setup.digest(template) == installed.get('task_sha256')
        and type(profile) is dict and type(profile.get('strategy')) is dict
        and profile['strategy'].get('strategy_id') == policy['bindings']['strategy_id'] == identity['strategy_id']
        and profile.get('symbol') == spec['symbol'] == policy['scope']['symbol'], 'FUNCTIONAL_FEEDBACK_TASK')
    completion = functional.completion_digest(result)
    receipt = functional.delivered_feedback_records(evidence['feedback_results'], completion=completion,
        cycle=cycle, source_commit=source_commit, now_ms=now_ms)
    # Pending delivery appends one receipt; the existing worker refuses a full
    # history. An already delivered result exits before that capacity check.
    if receipt is None:
        require(len(evidence['feedback_results']) < 64, 'FUNCTIONAL_FEEDBACK_HISTORY_BUDGET')
    return dict(status='COMPATIBLE_FUNCTIONAL_PENDING' if receipt is None else
        'COMPATIBLE_FUNCTIONAL_ALREADY_DELIVERED', timer_compatible=True, pending=receipt is None,
        publication_configuration_compatible=True, cycle_sha256=cycle, completion_sha256=completion,
        feedback_sha256=None if receipt is None else receipt['feedback_sha256'],
        source_namespace='resident-recovery/functional-cycles')


def _direct(evidence, *, source_commit, publication, now_ms):
    require(set(evidence) == _DIRECT_FIELDS, 'FEEDBACK_PREFLIGHT_EVIDENCE_FIELDS')
    require(evidence['source_commit'] == source_commit, 'FEEDBACK_PREFLIGHT_SOURCE')
    require(type(evidence['executor_uid']) is int
        and evidence['executor_uid'] == publication['executor_uid'], 'FEEDBACK_PREFLIGHT_EXECUTOR')
    publisher.hash_value(evidence['cycle_sha256'])
    exported = evidence['exported']
    bar = evidence['consumer'] == 'bar'
    require(type(exported) is dict
        and exported.get('status') == ('BAR_STRATEGY_FEEDBACK_RETAINED' if bar else 'STRATEGY_FEEDBACK_RETAINED')
        and exported.get('submission_enabled') is False and exported.get('economic_authority') is False,
        'STRATEGY_FEEDBACK_EXPORT')
    _bounded(exported, _RECORD_MAXIMUM)
    raw = evidence['artifact']
    _bounded(raw, publisher.MAXIMUM)
    for name in ('artifact_sha256', 'feedback_sha256'):
        publisher.hash_value(exported.get(name))
    require(setup.digest(raw) == exported['artifact_sha256'], 'FEEDBACK_PUBLICATION_ARTIFACT_CHANGED')
    (publisher.validate_bar_export if bar else publisher.validate_export)(raw,
        source_commit=source_commit, feedback_sha256=exported['feedback_sha256'], now=now_ms)
    return dict(status='DIRECT_PUBLICATION_ONLY', timer_compatible=False, pending=None,
        publication_configuration_compatible=True, cycle_sha256=evidence['cycle_sha256'],
        completion_sha256=None, feedback_sha256=exported['feedback_sha256'],
        source_namespace='strategy-bar-feedback' if bar else 'strategy-feedback')


def preflight(*, files, preimages, release_commit, consumer_evidence, now_ms):
    """Review exact candidate bytes and original records, without granting activation.

    Gold bar/strategy artifacts can match the direct publisher configuration,
    but never the functional timer. Invalid, stale or incomplete original
    records raise ValueError; a caller's precomputed PASS is not accepted.
    """
    require(type(now_ms) is int and 0 < now_ms <= 8640000000000000, 'FEEDBACK_PREFLIGHT_TIME')
    review = upgrade.review(files=files, preimages=preimages, release_commit=release_commit)
    require(type(consumer_evidence) is dict and consumer_evidence.get('schema') == EVIDENCE_SCHEMA
        and consumer_evidence.get('consumer') in ('functional', 'strategy', 'bar'),
        'FEEDBACK_PREFLIGHT_EVIDENCE_SCHEMA')
    # Detach from caller-owned mutable containers, and bind the entire supplied
    # evidence set without emitting account or private record contents.
    evidence = load_json_without_duplicate_keys(_bounded(consumer_evidence, _EVIDENCE_MAXIMUM))
    details = (_functional if evidence['consumer'] == 'functional' else _direct)(evidence,
        source_commit=release_commit, publication=review['publication'], now_ms=now_ms)
    return dict(schema=SCHEMA, release_commit=release_commit, consumer=evidence['consumer'],
        review_sha256=setup.digest(review), consumer_evidence_sha256=setup.digest(evidence),
        observed_at_ms=now_ms, **details, custody_verified=False, activation_authorized=False,
        installed=False, source_verified=False, feedback_delivery_verified=False,
        submission_enabled=False, economic_authority=False)
