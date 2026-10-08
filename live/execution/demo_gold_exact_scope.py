"""Mandatory signed first-Scope ancestry for its exact decision policies.

The fixed policy is trusted deployment configuration. This contract preserves
its signed ancestry through normal rotation and issuer history; it does not
authenticate a root administrator who replaces the whole configuration.
"""
from copy import deepcopy

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_common import require

SCHEMA = 'DEMO_GOLD_STRATEGY_BUDGET_POLICY_V2'


def validate(policy):
    from live.execution import demo_gold_budget_scope as scope_contract
    require(type(policy) is dict and policy.get('schema') == SCHEMA,
        'FIRST_SCOPE_EXACT_POLICY_SCHEMA')
    scope = policy.get('original_scope')
    require(type(scope) is dict and scope.get('schema') == scope_contract.SCHEMA_V2,
        'FIRST_SCOPE_EXACT_ORIGINAL_REQUIRED')
    scope_contract.validate_scope(scope)
    expected = {name: deepcopy(scope[name]) for name in scope_contract.COMMON}
    expected.update(schema=SCHEMA, policy_id=policy['policy_id'],
        budget_group=deepcopy(scope['budget_group']), original_scope=deepcopy(scope))
    expected['bindings']['execution_constraints_sha256'] = policy['bindings']['execution_constraints_sha256']
    require(_digest(policy) == _digest(expected), 'FIRST_SCOPE_EXACT_DECLARATIONS_CHANGED')
    return scope


def current_binding(policy, registry, now_ms):
    from live.execution.demo_gold_budget_scope import current_scope_key
    return current_scope_key(validate(policy), registry, now_ms)


def historical_binding(policy, registry):
    from live.execution.demo_gold_budget_scope import historical_scope_binding
    return historical_scope_binding(validate(policy), registry)


def verify_history(policy, rows):
    """Called with the same issuer connection's verified complete prefix."""
    if policy.get('schema') != SCHEMA:
        return
    from live.execution.demo_gold_first_scope_history import verify_prefix
    scope = validate(policy)
    verify_prefix(scope['scope_enrollment']['original_issuer_history'], rows)
