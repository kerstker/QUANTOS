"""Fixed-path native execution and recovery for the existing research queue.

Preparation is a trusted offline input boundary. A queued request supplies only
an invocation digest; it cannot choose code, paths, a clock or a trust provider.
All original registration, producer and recovery semantics remain in their owners.
"""
from contextlib import contextmanager
import copy
import os
from pathlib import Path
import stat
import time

from quantos.contracts import registered_research_job as contract
from quantos.contracts import registered_research_run as run_contract
from quantos.contracts.research_evaluation_plan import core_status
from quantos.runtime_paths import runtime_state_root
from services.execution.research_risk_replay import decode_technical_input
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from tools import registered_technical_research_run as native

common = native.common


def workspace_paths():
    base = runtime_state_root() / 'data/cache/registered_research'
    common.require(base.absolute() == base.resolve(), 'JOB_WORKSPACE_PATH')
    return base / 'project', base / 'registry.db'


def _database_identity(path):
    common.require(path.absolute() == path.resolve(), 'JOB_DATABASE_PATH')
    value = path.lstat()
    common.require(stat.S_ISREG(value.st_mode) and value.st_uid == os.getuid()
        and value.st_nlink == 1 and stat.S_IMODE(value.st_mode) == 0o600, 'JOB_DATABASE_CUSTODY')
    return value.st_dev, value.st_ino, value.st_uid, value.st_gid, value.st_mode, value.st_nlink


@contextmanager
def _registry(database, *, initialize=False):
    # This shared lock serializes the original registry across native run dirs.
    # It does not establish a formal deployment writer barrier.
    common._directory(database.parent)
    with common._locked(database):
        created = False
        if initialize and not database.exists():
            fd = os.open(database, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            common._sync(database.parent)
            created = True
        identity = _database_identity(database)
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar = Path(str(database) + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                _database_identity(sidecar)
        from services.research.project_result_material_reader import ProjectResultMaterialReader
        store = SqliteExperimentStore(str(database), create=created,
            project_result_reader=ProjectResultMaterialReader(database.parent/'project'))
        try:
            common.require(_database_identity(database) == identity, 'JOB_DATABASE_CHANGED')
            if created:
                # Select the original provenance-capable catalog before the
                # one-way evidence upgrade. Existing databases are untouched.
                store._ensure_search_schema(require_provenance=True)
            population = store.research_evidence_population()
            if created:
                store.upgrade_research_evidence_schema(expected_version=store._schema_version(),
                    expected_population_sha256=population['sha256'])
            yield ExperimentRegistry(store)
            common.require(_database_identity(database) == identity, 'JOB_DATABASE_CHANGED')
        finally:
            store.close()


def prepare_run(arguments, *, search_id, dataset_version, registered_at_ms,
                invocation_id, evaluation_core, risk_review_policy, statistical_policy=None):
    """Freeze a new native request, including its evaluation and risk policy.

    Callers are original offline input producers, never transported callbacks.
    Existing invocations cannot be amended; changes require a new identity.
    """
    source = native.source_identity()
    raw = common.canonical(native.technical_input(arguments))
    common.require(common.canonical(source) == common.canonical(arguments['release_identity']), 'SOURCE')
    common._time(lambda: time.time_ns() // 1_000_000, registered_at_ms)
    plan = common._plan(adapter=run_contract.TECHNICAL, input_raw=raw, source=source,
        candidates=[native._candidate(arguments)], search_id=search_id,
        producer_id='REGISTERED_TECHNICAL_RUNNER_V1', actor_id='AUTOMATED_RESEARCH',
        dataset_version=dataset_version, registered_at_ms=registered_at_ms,
        invocation_id=invocation_id, evaluation_core=evaluation_core,
        risk_review_policy=risk_review_policy)
    common.require(plan['schema'] == run_contract.RISK_EVALUATED_SCHEMA, 'JOB_PREREGISTRATION')
    if statistical_policy is not None:
        from quantos.contracts.research_statistical_review import validate_statistical_policy
        statistical_policy = validate_statistical_policy(statistical_policy)
        common.require(common.plan_digest(statistical_policy) == evaluation_core['selection']['validation_policy_sha256']
            and statistical_policy['source_identity_sha256'] == common.plan_digest(source)
            and statistical_policy['dataset_manifest_sha256'] == common.plan_digest(arguments['dataset_identity'])
            and statistical_policy['study_scope']['registered_at_ms'] <= registered_at_ms
            and common.canonical(statistical_policy['sample']) == common.canonical(evaluation_core['sample'])
            and common.canonical({k:statistical_policy['benchmark_policy'][k] for k in
                ('id', 'definition_sha256', 'input_sha256')}) == common.canonical(evaluation_core['benchmark']),
            'JOB_STATISTICAL_POLICY_BINDING')
    project, database = workspace_paths()
    database.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    common._directory(database.parent)
    with _registry(database, initialize=True):
        if statistical_policy is not None:
            policies = common._mkdir(database.parent / 'statistical_policies')
            policy_file = policies / (common.plan_digest(statistical_policy) + '.json')
            if (project / 'runs' / invocation_id / 'plan.json').exists():
                common.require(policy_file.exists(), 'STATISTICAL_POLICY_NOT_RETAINED_BEFORE_PLAN')
            common._put(policy_file, statistical_policy)
        return native.prepare_technical(arguments, project_root=project, search_id=search_id,
            producer_id=plan['search']['source'], actor_id=plan['actor_id'], dataset_version=dataset_version,
            registered_at_ms=registered_at_ms, invocation_id=invocation_id,
            evaluation_core=evaluation_core, risk_review_policy=risk_review_policy)


def _request(params, *, recovering):
    clean = contract.validate_params(params)
    project, database = workspace_paths()
    common._directory(project.parent)
    common._directory(project)
    common._directory(project / 'runs')
    directory = common._directory(project / 'runs' / clean['native_invocation_id'])
    plan = run_contract.validate_plan(common._load(directory / 'plan.json', maximum=run_contract.MAX_PLAN_BYTES))
    common.require(plan['invocation_id'] == clean['native_invocation_id']
        and plan['adapter'] == run_contract.TECHNICAL
        and plan['schema'] == run_contract.RISK_EVALUATED_SCHEMA, 'JOB_PREREGISTRATION')
    raw = common._read(directory / 'input.json', maximum=run_contract.MAX_INPUT_BYTES)
    common.require(common.plan_digest(raw) == plan['input_sha256'], 'INPUT_CHANGED')
    common.require(common.canonical(plan['source_identity']) == common.canonical(native.source_identity()), 'SOURCE_CHANGED')
    document = common._load(directory / 'input.json', maximum=run_contract.MAX_INPUT_BYTES)
    common.require(common.canonical(document) == raw, 'INPUT_CHANGED')
    arguments = decode_technical_input(document)
    common.require(common.canonical(native.technical_input(arguments)) == raw, 'TECHNICAL_INPUT_CHANGED')
    if recovering:
        common.require((directory / 'invocation.json').exists(), 'RECOVERY_NOT_STARTED')
    return plan, arguments, directory, database


def _run(params, *, recovering):
    plan, arguments, directory, database = _request(params, recovering=recovering)
    with _registry(database) as registry:
        result = native.run_technical(arguments, destination=directory, registry=registry)
        common.require(result['authoritative'] is False
            and result['detail']['evidence_status'] == 'TECHNICAL_VALIDATION_ONLY', 'JOB_NATIVE_AUTHORITY')
        risk = common._load(directory / 'archive/risk-replay.json')
        report = dict(schema=contract.SCHEMA, status='TECHNICAL_VALIDATION_ONLY',
            plan=copy.deepcopy(plan), plan_sha256=common.plan_digest(plan),
            native_trial_id=plan['trials'][0]['native_trial_id'],
            result_hash=result['detail']['result_hash'],
            account=copy.deepcopy(result['detail']['result_hash_payload']['reconciliation']),
            research_status=dict(oos=core_status(plan['evaluation_core'])['oos_status'],
                statistical_review='NOT_REVIEWED', risk_replay=risk['replay']['status'],
                independent_review='INDEPENDENT_REVIEW_REQUIRED', candidate_admission='NOT_APPROVED'),
            submission_enabled=False, economic_authority=False, promotion_approved=False)
        return contract.validate_report(report)


def run_research(params):
    return _run(params, recovering=False)


def recover_research(params):
    return _run(params, recovering=True)
