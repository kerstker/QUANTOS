"""Explicit original Gold invocation and cold recovery through the guarded queue.

Only an invocation SHA is transported. Input, paths, budgets, source and producer
are frozen by the trusted offline preparer. The original registry owns trials.
"""
import time

from quantos.contracts import registered_research_run as native
from quantos.contracts import registered_gold_chunked_job as shape
from tools import registered_research_run as original
from tools import registered_research_job as workspace


def prepare_run(arguments, *, search_id, dataset_version, registered_at_ms, invocation_id,
                result_storage, forward_design=None, history_storage=None):
    source = original.gold.source_identity()
    original.require(original.canonical(source) == original.canonical(arguments['release_identity']), 'SOURCE')
    original._time(lambda: time.time_ns()//1_000_000, registered_at_ms)
    from quantos.contracts.gold_chunked_result import storage
    storage(result_storage)
    if history_storage is not None:
        native.validate_history_storage(history_storage)
    project, database = workspace.workspace_paths()
    database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    original._directory(database.parent)
    with workspace._registry(database, initialize=True):
        return original.prepare_gold(arguments, project_root=project, search_id=search_id,
            producer_id='REGISTERED_GOLD_CHUNKED_RUNNER_V1', actor_id='AUTOMATED_RESEARCH',
            dataset_version=dataset_version, registered_at_ms=registered_at_ms,
            invocation_id=invocation_id, result_storage=result_storage, forward_design=forward_design,
            history_storage=history_storage)


def _request(params, recovering):
    clean = shape.validate_params(params)
    project, database = workspace.workspace_paths()
    original._directory(project.parent)
    original._directory(project)
    original._directory(project/'runs')
    root = original._directory(project/'runs'/clean['native_invocation_id'])
    plan = native.validate_plan(original._load(root/'plan.json', maximum=native.MAX_PLAN_BYTES))
    original.require(plan['schema'] in native.CHUNKED_GOLD_SCHEMAS and plan['adapter'] == native.GOLD
        and plan['invocation_id'] == root.name, 'GOLD_CHUNKED_JOB_PLAN')
    original.require(original.canonical(plan['source_identity']) == original.canonical(original.gold.source_identity()),
        'SOURCE_CHANGED')
    raw = original._read(root/'input.json', maximum=native.MAX_INPUT_BYTES)
    original.require(original.plan_digest(raw) == plan['input_sha256'], 'INPUT_CHANGED')
    arguments = original.gold._decode(original._load(root/'input.json', maximum=native.MAX_INPUT_BYTES))
    original.require(original._gold_input(arguments) == raw, 'INPUT_CHANGED')
    if recovering:
        original.require((root/'invocation.json').exists(), 'RECOVERY_NOT_STARTED')
    return plan, arguments, root, database


def prepare_source_run(directory, *, search_id, dataset_version, registered_at_ms, invocation_id,
                       result_storage, history_storage, source_storage, forward_design=None):
    from tools.gold_native_source_run import prepare
    from quantos.contracts.gold_native_source import storage
    storage(source_storage)
    native.validate_history_storage(history_storage)
    original._time(lambda: time.time_ns()//1_000_000, registered_at_ms)
    project, database = workspace.workspace_paths()
    database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    original._directory(database.parent)
    with workspace._registry(database, initialize=True):
        return prepare(directory, project_root=project, search_id=search_id, dataset_version=dataset_version,
            registered_at_ms=registered_at_ms, invocation_id=invocation_id, result_storage=result_storage,
            history_storage=history_storage, source_storage=source_storage, forward_design=forward_design,
            producer_id='REGISTERED_GOLD_COLLECTOR_V1' if forward_design is not None
                else 'REGISTERED_GOLD_CHUNKED_RUNNER_V1', actor_id='AUTOMATED_RESEARCH')


def prepare_source_reanalysis(directory, *, collection_source_identity, search_id, dataset_version,
        registered_at_ms, invocation_id, result_storage, history_storage, source_storage, forward_design=None,
        evaluation_core=None, statistical_policy=None):
    """Trusted offline replay preparation; source pins never enter queue parameters."""
    from tools.gold_native_source_run import prepare
    from quantos.contracts.gold_native_source import storage, source_identity as validate_collection_source
    original.require(forward_design is None, 'REANALYSIS_NOT_FORWARD_DECLARED')
    validate_collection_source(collection_source_identity)
    storage(source_storage)
    native.validate_history_storage(history_storage)
    original._time(lambda: time.time_ns()//1_000_000, registered_at_ms)
    if statistical_policy is not None:
        from quantos.contracts.gold_reanalysis_statistics import bind_core
        statistical_policy = bind_core(statistical_policy, evaluation_core)
    project, database = workspace.workspace_paths()
    database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    original._directory(database.parent)
    with workspace._registry(database, initialize=True):
        if statistical_policy is not None:
            policies = original._mkdir(database.parent/'statistical_policies')
            policy_file = policies/(original.plan_digest(statistical_policy)+'.json')
            if (project/'runs'/invocation_id/'plan.json').exists():
                original.require(policy_file.exists(), 'STATISTICAL_POLICY_NOT_RETAINED_BEFORE_PLAN')
            original._put(policy_file, statistical_policy)
        return prepare(directory, project_root=project, search_id=search_id, dataset_version=dataset_version,
            registered_at_ms=registered_at_ms, invocation_id=invocation_id, result_storage=result_storage,
            history_storage=history_storage, source_storage=source_storage,
            collection_source_identity=collection_source_identity, evaluation_core=evaluation_core,
            producer_id='REGISTERED_GOLD_REANALYSIS_RUNNER_V1', actor_id='AUTOMATED_RESEARCH')


def _run(params, recovering):
    plan, arguments, root, database = _request(params, recovering)
    with workspace._registry(database) as registry:
        result = original.run_gold(arguments, destination=root, registry=registry)
        with original._chunked_gold_report(root, plan) as report:
            accounts = {t['candidate']:dict(t['simulation']['reconciliation']) for t in report['trials']}
            if any(t['candidate_id'] == 'net_portfolio' for t in plan['trials']):
                accounts['net_portfolio'] = dict(report['portfolio']['simulation']['reconciliation'])
            mirror = dict(schema=shape.SCHEMA, status='TECHNICAL_VALIDATION_ONLY', plan=plan,
                plan_sha256=original.plan_digest(plan), result_hash=result['result_hash'], accounts=accounts,
                research_status=dict(shape.STATUS), submission_enabled=False, economic_authority=False,
                promotion_approved=False)
            original.require(report['result_hash'] == result['result_hash'], 'GOLD_RESULT_CHANGED')
            return shape.validate_report(mirror)


def run_research(params):
    return _run(params, False)


def recover_research(params):
    return _run(params, True)
