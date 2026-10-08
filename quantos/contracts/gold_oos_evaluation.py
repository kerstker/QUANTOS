"""Holdout economic policy committed before the original run, without authority."""
from copy import deepcopy
from decimal import Decimal

from quantos.contracts import research_statistical_review as common

SCHEMA = 'QUANTOS_GOLD_OOS_ECONOMIC_POLICY_V1'
METHOD = 'ORIGINAL_FIXED_CANDIDATE_NET_HOLDOUT_ECONOMICS_V1'
FIELDS = 'schema method profile economic_checks return_rule missing_evidence economic_authority submission_enabled'
ECONOMIC_FIELDS = 'minimum_net_return minimum_benchmark_excess maximum_drawdown minimum_observations minimum_duration_ms'


def validate(value):
    common.canonical(value, maximum=65_536)
    common.fields(value, FIELDS)
    common.require(value['schema'] == SCHEMA and value['method'] == METHOD
        and value['profile'] == 'ROBUST_RESEARCH_SIGNIFICANCE_V1', 'GOLD_OOS_POLICY')
    common.require(value['return_rule'] == 'STRICTLY_POSITIVE_AND_AT_LEAST_DECLARED_MINIMUM'
        and value['missing_evidence'] == 'INSUFFICIENT_RETAIN_KNOWN_FAILURES'
        and value['economic_authority'] is False and value['submission_enabled'] is False,
        'GOLD_OOS_AUTHORITY')
    checks = value['economic_checks']
    common.fields(checks, ECONOMIC_FIELDS)
    for name in ('minimum_net_return', 'minimum_benchmark_excess'):
        common.number(checks[name], minimum=0)
    common.number(checks['maximum_drawdown'], minimum=0, maximum=Decimal('0.05'))
    common.integer(checks['minimum_observations'], minimum=30, maximum=common.MAX_POINTS-1)
    common.integer(checks['minimum_duration_ms'], minimum=30*86_400_000)
    return deepcopy(value)


def bind_statistical_policy(value, policy):
    """An explicit holdout cannot relax the original declared economic floors."""
    value = validate(value)
    holdout, original = value['economic_checks'], policy['economic_checks']
    for name in ('minimum_net_return', 'minimum_benchmark_excess', 'minimum_observations', 'minimum_duration_ms'):
        common.require(Decimal(holdout[name]) >= Decimal(original[name]), 'GOLD_OOS_RELAXED_FLOOR')
    common.require(Decimal(holdout['maximum_drawdown']) <= Decimal(original['maximum_drawdown']),
        'GOLD_OOS_RELAXED_DRAWDOWN')
    return value
