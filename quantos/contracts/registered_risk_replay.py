"""Pre-registered engineering replay and immutable diagnostic attachment.

This pure contract verifies bindings, never executes a model or grants authority.
The old native report and its explicit false attestations remain unchanged.
"""
import copy

from quantos.contracts import registered_research_run as run
from quantos.contracts.research_risk_replay import validate_report, report_sha256, SCHEMA as REPORT_SCHEMA

POLICY_SCHEMA = 'QUANTOS_REGISTERED_RISK_REVIEW_POLICY_V1'
ARTIFACT_SCHEMA = 'QUANTOS_REGISTERED_RISK_REPLAY_ARTIFACT_V1'
OUTCOME_SCHEMA = 'QUANTOS_REGISTERED_RESEARCH_OUTCOME_V2'
LEAF = 'risk-replay.json'
METHOD = 'ORIGINAL_ENGINE_REPRODUCTION_AND_CANONICAL_LEDGER_COVERAGE'
FAILURE_POLICY = 'RETAIN_DIAGNOSTIC_WITHOUT_ECONOMIC_QUALIFICATION'
POLICY_FIELDS = frozenset('schema adapter input_sha256 source_identity_sha256 evaluation_core_sha256 native_trial_id report_schema method failure_policy submission_enabled economic_authority'.split())
ARTIFACT_FIELDS = frozenset('schema plan_sha256 input_sha256 original_report_sha256 started_at_ms completed_at_ms replay_sha256 replay submission_enabled economic_authority'.split())


def require(condition, reason):
    if not condition:
        raise ValueError('REGISTERED_RISK_'+reason)


def validate_policy(value, *, input_sha256, source_identity_sha256, evaluation_core_sha256, native_trial_id):
    require(type(value) is dict and set(value)==POLICY_FIELDS, 'POLICY_FIELDS')
    run.canonical(value, maximum=run.MAX_PLAN_BYTES)
    require(value['schema']==POLICY_SCHEMA and value['adapter']==run.TECHNICAL
        and value['report_schema']==REPORT_SCHEMA and value['method']==METHOD
        and value['failure_policy']==FAILURE_POLICY, 'POLICY_KIND')
    expected=dict(input_sha256=input_sha256,source_identity_sha256=source_identity_sha256,
        evaluation_core_sha256=evaluation_core_sha256,native_trial_id=native_trial_id)
    require(all(run._hash(v) and value[k]==v for k,v in expected.items()),'POLICY_BINDING')
    require(value['submission_enabled'] is False and value['economic_authority'] is False,'AUTHORITY')
    return copy.deepcopy(value)


def validation_policy_sha256(core, policy):
    from quantos.contracts.research_evaluation_plan import core_sha256
    return run.digest(dict(schema='QUANTOS_REGISTERED_VALIDATION_POLICIES_V1',
        evaluation_core_sha256=core_sha256(core),risk_review_policy_sha256=run.digest(policy)))


def validate_artifact(value, *, plan, original_result, invocation):
    run.validate_plan(plan)
    require(plan['schema']==run.RISK_EVALUATED_SCHEMA,'PLAN_VERSION')
    require(type(value) is dict and set(value)==ARTIFACT_FIELDS,'ARTIFACT_FIELDS')
    run.canonical(value,maximum=run.MAX_OUTPUT_BYTES)
    require(value['schema']==ARTIFACT_SCHEMA and value['submission_enabled'] is False
        and value['economic_authority'] is False,'ARTIFACT_KIND')
    require(value['plan_sha256']==run.digest(plan) and value['input_sha256']==plan['input_sha256']
        and value['original_report_sha256']==run.digest(original_result),'ARTIFACT_BINDING')
    require(type(invocation) is dict and invocation.get('plan_sha256')==run.digest(plan)
        and type(invocation.get('started_at_ms')) is int,'INVOCATION')
    require(type(value['started_at_ms']) is int and type(value['completed_at_ms']) is int
        and invocation['started_at_ms']<=value['started_at_ms']<=value['completed_at_ms']<=2**63-1,'TIME')
    replay=validate_report(value['replay'])
    require(value['replay_sha256']==report_sha256(replay)
        and replay['input_sha256']==plan['input_sha256']
        and replay['original_result_sha256']==value['original_report_sha256']
        and replay['run_id']==original_result.get('run_id')
        and replay['manifest_sha256']==run.digest(original_result['detail']['run_manifest_v2']),'REPLAY_BINDING')
    return copy.deepcopy(value)


def build_artifact(*, plan, original_result, invocation, replay, started_at_ms, completed_at_ms):
    value=dict(schema=ARTIFACT_SCHEMA,plan_sha256=run.digest(plan),input_sha256=plan['input_sha256'],
        original_report_sha256=run.digest(original_result),started_at_ms=started_at_ms,
        completed_at_ms=completed_at_ms,replay_sha256=report_sha256(replay),replay=copy.deepcopy(replay),
        submission_enabled=False,economic_authority=False)
    return validate_artifact(value,plan=plan,original_result=original_result,invocation=invocation)


def verify_lifecycle(*, plan, documents, outcome=None):
    """Validate actual retained leaves; absence cannot synthesize a report."""
    risk=documents.get('archive/'+LEAF)
    if plan['schema']!=run.RISK_EVALUATED_SCHEMA:
        require(risk is None,'LEGACY_ATTACHMENT')
        return None
    report=documents.get('archive/report.json');invocation=documents.get('invocation.json')
    if risk is not None:
        require(report is not None and invocation is not None,'ORIGINALS_MISSING')
        validate_artifact(risk,plan=plan,original_result=report,invocation=invocation)
    if outcome is not None:
        require('risk_replay_sha256' in outcome,'OUTCOME_FIELDS')
        if outcome['status']=='PASSED':
            require(risk is not None and outcome['risk_replay_sha256']==run.digest(risk),'OUTCOME_ATTACHMENT')
            require(type(outcome['completed_at_ms']) is int
                and outcome['completed_at_ms']>=risk['completed_at_ms'],'OUTCOME_TIME')
        else:
            require(risk is None and outcome['risk_replay_sha256'] is None,'FAILED_ATTACHMENT')
    return copy.deepcopy(risk)
