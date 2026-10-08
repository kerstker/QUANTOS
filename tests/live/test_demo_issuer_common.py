"""Shared value checks preserve the exact-policy boundary and native errors."""
from copy import deepcopy
import importlib
import pytest
from live.execution.demo_issuer_policy import validate_policy, budget_group_binding
from tests.live.test_gold_strategy_constraints import case as case, NOW
from tests.live.test_gold_execution_delegation import fixture


def common():
    return importlib.import_module('live.execution.demo_issuer_common')


def test_common_projection_matches_native_binding_without_fake_exact_constraints(case):
    _, _, registry, delegation = fixture(case)
    value = deepcopy(delegation['policy_template'])
    exact_binding = budget_group_binding(value)
    value['template_id'] = value.pop('policy_id')
    value['schema'] = 'DEMO_GOLD_FUTURE_EXIT_BUDGET_TEMPLATE_V1'
    value['bindings'].pop('execution_constraints_sha256')
    module = common()
    assert module.check_common_fields(value, identifier_field='template_id', gold_only=True) is None
    assert module.check_budget_group(value, reserved=True) is None
    from live.execution.demo_batch_risk import _digest
    assert _digest(module.group_binding_fields(value)) == exact_binding
    assert module.match_current_key(value, registry, NOW) == registry['keys'][0]
    assert module.match_historical_binding(value, registry) is None
    with pytest.raises(ValueError):
        validate_policy(value)
    assert case[-1].snapshot() == []


@pytest.mark.parametrize('damage,reason', [
    ('time','ISSUER_POLICY_TIME'), ('count','ISSUER_POLICY_BUDGET'),
    ('amount','ISSUER_EXACT_AMOUNT'), ('reserve','ISSUER_EXIT_RESERVE_LIMIT'),
    ('group_window','ISSUER_GROUP_TIME'), ('bool_count','DEMO_AUTH_INTEGER'),
])
def test_extraction_preserves_native_legacy_rejections(case, damage, reason):
    _, _, _, delegation = fixture(case)
    value = deepcopy(delegation['policy_template'])
    if damage == 'time': value['grant_ttl_ms'] = 0
    elif damage == 'count': value['max_grants'] = 0
    elif damage == 'amount': value['max_total_notional'] = 3.0
    elif damage == 'reserve': value['budget_group']['exit_reserve']['orders'] = 0
    elif damage == 'group_window': value['budget_group']['valid_until_ms'] = value['valid_until_ms']-1
    else: value['max_orders'] = True
    common()
    with pytest.raises(ValueError) as caught:
        validate_policy(value)
    # The canonical validator defines integer spelling; the others are issuer errors.
    assert reason in str(caught.value) if damage != 'bool_count' else 'INTEGER' in str(caught.value)
