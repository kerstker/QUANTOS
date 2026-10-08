"""Nonexecuting future-signal scope; only admission replays decision authority.

An exact projection below validates constraints, but cannot establish their
provenance. Callers must independently complete the signed delegation, ENABLE,
original-history and full per-decision proof checks before using its output.
"""

from copy import deepcopy

from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_common import (
    check_budget_group,
    check_common_fields,
    group_binding_fields,
    match_current_key,
    match_historical_binding,
)
from live.execution.demo_issuer_policy import require
from services.validation import demo_submission as submission

SCHEMA = 'DEMO_GOLD_FUTURE_EXIT_BUDGET_TEMPLATE_V1'
PERMISSION_FIELDS = (
    'environment deployment_sha256 registry_sha256 executor_release_commit account_ref_sha256 '
    'authority_id key_id valid_from_ms valid_until_ms grant_ttl_ms max_grants max_orders '
    'max_total_notional max_cancel_grants max_cancel_orders scope bindings budget_group'
)
BINDINGS = 'strategy_id mandate_sha256 validation_sha256 native_limits_sha256 strategy_approval_sha256'


def validate_template(value):
    _fields(value, 'schema template_id ' + PERMISSION_FIELDS
        + ' origin pilot executable submission_enabled economic_authority')
    require(value['schema'] == SCHEMA and all(value[k] is False for k in
        ('executable', 'submission_enabled', 'economic_authority')), 'GOLD_FUTURE_TEMPLATE_SCOPE')
    _fields(value['bindings'], BINDINGS)
    check_common_fields(value, identifier_field='template_id', gold_only=True)
    check_budget_group(value, reserved=True)
    origin = value['origin']
    _fields(origin, 'kind document_sha256 profile_sha256 setup_sha256 spec_sha256')
    require(origin['kind'] in ('SCOPE', 'BUDGET'), 'GOLD_FUTURE_TEMPLATE_ORIGIN')
    for name in ('document_sha256', 'profile_sha256', 'setup_sha256', 'spec_sha256'):
        submission._digest(origin[name])
    pilot = value['pilot']
    _fields(pilot, 'request_sha256 configuration_sha256 opening_origin_sha256 started_at_ms ends_at_ms')
    for name in ('request_sha256', 'configuration_sha256', 'opening_origin_sha256'):
        submission._digest(pilot[name])
    start, end = (submission._integer(pilot[k]) for k in ('started_at_ms', 'ends_at_ms'))
    require(value['valid_from_ms'] <= start < end <= value['valid_until_ms']
        and end - start == 86400000, 'GOLD_FUTURE_TEMPLATE_ORIGINAL_WINDOW')
    submission._canonical(value)
    return value


def current_template_key(value, registry, now_ms):
    validate_template(value)
    return match_current_key(value, registry, now_ms)


def historical_template_binding(value, registry):
    """Read-only identity verification, with no current execution permission."""
    validate_template(value)
    match_historical_binding(value, registry)


def template_group_binding(value):
    validate_template(value)
    return _digest(group_binding_fields(value))


def derive_exact_policy(value, *, proof_sha256, constraints, orders, now_ms):
    """Project already-admitted real constraints; this is not proof admission."""
    from live.execution.demo_issuer_policy import (
        GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA, budget_group_binding, validate_policy,
    )
    from services.validation.demo_strategy_constraints import validate

    validate_template(value)
    submission._digest(proof_sha256)
    validate(constraints, orders=orders, now_ms=now_ms)
    require(value['valid_from_ms'] <= constraints['not_before_ms'] <= now_ms < value['valid_until_ms']
        and constraints['strategy_definition_sha256'] == value['budget_group']['strategy_definition_sha256'],
        'GOLD_FUTURE_TEMPLATE_CONSTRAINTS')
    policy = {k: deepcopy(value[k]) for k in PERMISSION_FIELDS.split()}
    policy.update(schema=GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA, policy_id='gold-delegated-' + proof_sha256)
    policy['bindings']['execution_constraints_sha256'] = _digest(constraints)
    validate_policy(policy)
    require(budget_group_binding(policy) == template_group_binding(value), 'GOLD_FUTURE_TEMPLATE_GROUP_REBIND')
    return policy
