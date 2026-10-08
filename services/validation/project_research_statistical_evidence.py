"""Bind retained statistical declarations to original complete project records.

No supplied equity curve or successful status is trusted. The original kernels
recompute the benchmark and report; OOS and admission remain separate evidence.
"""
import copy

from quantos.contracts import research_statistical_review as contract
from quantos.contracts.gold_forward_evaluation_design import FULL_REVIEW_SCHEMAS
from services.validation import project_research_statistics as statistics

SCHEMA = 'QUANTOS_PROJECT_RESEARCH_STATISTICAL_EVIDENCE_V1'
FIELDS = frozenset(('schema', 'policy', 'cohort', 'report'))


def _report(*, project_cut, policy, cohort, as_of_ms, reports=None):
    from quantos.contracts.project_chunked_results import full_report_population
    full_report_population(project_cut, reports)
    from quantos.contracts import gold_reanalysis_statistics as backtest
    if policy.get('schema') == backtest.SCHEMA:
        from services.validation import gold_project_statistics
        return gold_project_statistics.review_project_statistics(project_cut=project_cut,
            policy=policy, cohort=cohort, as_of_ms=as_of_ms, reports=reports)
    if policy.get('schema') in FULL_REVIEW_SCHEMAS:
        from services.validation import gold_project_statistics
        return gold_project_statistics.review_project_statistics(project_cut=project_cut,
            policy=policy, cohort=cohort, as_of_ms=as_of_ms, reports=reports)
    policy = contract.validate_statistical_policy(policy)
    cohort = contract.validate_cohort(cohort)
    benchmark = statistics.project_native_series(project_cut=project_cut,
        attempt=cohort['benchmark_attempt'], sample=policy['sample'], sample_method=policy['sample_method'])
    return statistics.review_project_statistics(project_cut=project_cut, policy=policy,
        cohort=cohort, benchmark=benchmark, as_of_ms=as_of_ms)


def prepare(*, project_cut, policy, cohort, as_of_ms, reports=None):
    report = _report(project_cut=project_cut, policy=policy, cohort=cohort, as_of_ms=as_of_ms, reports=reports)
    return dict(schema=SCHEMA, policy=copy.deepcopy(policy), cohort=copy.deepcopy(cohort), report=report)


def validate(value, *, project_cut, as_of_ms=None, reports=None):
    contract.canonical(value, maximum=16_000_000)
    contract.fields(value, FIELDS)
    contract.require(value['schema'] == SCHEMA, 'EVIDENCE_SCHEMA')
    report = contract.validate_report(value['report'])
    checked = report['as_of_ms'] if as_of_ms is None else contract.integer(as_of_ms)
    contract.require(report['as_of_ms'] <= checked, 'REVIEW_TIME')
    actual = _report(project_cut=project_cut, policy=value['policy'], cohort=value['cohort'],
        as_of_ms=report['as_of_ms'], reports=reports)
    contract.require(contract.canonical(actual) == contract.canonical(report), 'STATISTICAL_REPORT_CHANGED')
    return copy.deepcopy(value)


def projection(value):
    """Summary of an already recomputed report; original bytes stay in the snapshot."""
    report = value['report']
    names = ('statistical_decision', 'profile', 'policy_sha256', 'cohort_sha256', 'as_of_ms',
        'population', 'selected_attempt', 'benchmark_attempt', 'metrics', 'checks', 'reasons', 'limitations')
    result = dict(evidence_sha256=contract.digest(value), report_sha256=contract.digest(report),
        **{name: copy.deepcopy(report[name]) for name in names})
    if report['schema'] in contract.SCOPED_GOLD_REPORT_SCHEMAS:
        result['comparison'] = copy.deepcopy(report['comparison'])
    return result
