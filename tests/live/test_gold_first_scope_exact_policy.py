"""Exact first-Scope contracts retain independently signed declarations."""
from copy import deepcopy

import pytest

from tests.tools.test_gold_first_scope_compiled_consumers import (
    book as book, first_scope_archive as first_scope_archive,
    original_issuer as original_issuer, first_scope_inputs as first_scope_inputs,
    compiled_scope as compiled_scope,
)
from live.execution import demo_issuer_policy as policy_contract
from tools.gold_strategy_approval_request import candidate_policy


@pytest.fixture
def exact_policy(compiled_scope):
    values, _, _, docs = compiled_scope
    scope, registry = docs['policy.json'], docs['registry.json']
    policy = candidate_policy(scope, registry=registry, request_sha256='a'*64,
        constraints_sha256='b'*64, now_ms=values['now_ms'])
    return policy, scope, registry, values['now_ms']


def test_exact_rotations_keep_signed_scope_and_same_group(exact_policy):
    from live.execution.demo_gold_budget_scope import budget_binding
    from services.validation.demo_submission import _canonical
    policy, scope, registry, now = exact_policy
    assert policy['schema'] == policy_contract.GOLD_STRATEGY_BUDGET_POLICY_SCHEMA_V2
    assert policy['original_scope'] == scope
    assert policy_contract.budget_group_binding(policy) == budget_binding(scope)
    assert policy_contract.current_key(policy, registry, now) == registry['keys'][-1]
    changed = candidate_policy(policy, registry=registry, request_sha256='c'*64,
        constraints_sha256='d'*64, now_ms=now+1)
    assert changed['policy_id'] != policy['policy_id']
    assert changed['bindings']['execution_constraints_sha256'] == 'd'*64
    assert changed['original_scope'] == scope
    assert policy_contract.budget_group_binding(changed) == budget_binding(scope)
    assert len(_canonical(changed)) < 65536  # Existing fixed policy read budget.


@pytest.mark.parametrize('damage', ['missing_scope', 'old_scope', 'signature', 'original_anchor',
    'source', 'account', 'quantity', 'budget', 'deadline', 'bindings', 'extra'])
def test_exact_policy_rejects_missing_or_substituted_signed_declarations(exact_policy, damage):
    original, _, registry, now = exact_policy
    policy = deepcopy(original)
    if damage == 'missing_scope': policy.pop('original_scope')
    elif damage == 'old_scope': policy['original_scope']['schema'] = 'DEMO_GOLD_STRATEGY_BUDGET_SCOPE_V1'
    elif damage == 'signature':
        value = policy['original_scope']['signature_base64url']
        policy['original_scope']['signature_base64url'] = ('B' if value[0] != 'B' else 'C')+value[1:]
    elif damage == 'original_anchor':
        policy['original_scope']['scope_enrollment']['original_issuer_history']['history_sha256'] = 'f'*64
    elif damage == 'source': policy['executor_release_commit'] = 'f'*40
    elif damage == 'account': policy['account_ref_sha256'] = 'f'*64
    elif damage == 'quantity': policy['scope']['max_quantity_per_order'] = '0.009'
    elif damage == 'budget': policy['max_total_notional'] = '749'
    elif damage == 'deadline': policy['valid_until_ms'] -= 1
    elif damage == 'bindings': policy['bindings']['mandate_sha256'] = 'f'*64
    else: policy['optional_original_scope'] = policy['original_scope']
    with pytest.raises(ValueError):
        policy_contract.current_key(policy, registry, now)


def test_expiry_preserves_historical_signature_check_without_new_authority(exact_policy):
    policy, _, registry, _ = exact_policy
    with pytest.raises(ValueError):
        policy_contract.current_key(policy, registry, policy['valid_until_ms'])
    policy_contract.historical_policy_binding(policy, registry)
    changed = deepcopy(policy)
    changed['original_scope']['scope_enrollment']['original_issuer_history']['head_sha256'] = 'e'*64
    with pytest.raises(ValueError):
        policy_contract.historical_policy_binding(changed, registry)
