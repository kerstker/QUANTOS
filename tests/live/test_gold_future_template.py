"""A future template has no exact trade until real bounded constraints exist."""

from copy import deepcopy
import importlib

import pytest

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import budget_group_binding, validate_policy
from tests.live.test_gold_strategy_constraints import case as case, NOW
from tests.live.test_gold_execution_delegation import fixture


def template_case(case):
    """Synthetic public declarations only; no installation or human approval."""
    _, private, registry, delegation = fixture(case)
    exact = deepcopy(delegation['policy_template'])
    value = {k: deepcopy(v) for k, v in exact.items() if k != 'policy_id'}
    value['schema'] = 'DEMO_GOLD_FUTURE_EXIT_BUDGET_TEMPLATE_V1'
    value['template_id'] = 'original-pilot-future-template'
    value['bindings'].pop('execution_constraints_sha256')
    config = delegation['terms']['pilot_configuration']
    value.update(
        origin=dict(kind='BUDGET', document_sha256='1'*64, profile_sha256='2'*64,
                    setup_sha256='3'*64, spec_sha256='4'*64),
        pilot=dict(request_sha256=delegation['terms']['pilot_request_sha256'],
                   configuration_sha256=_digest(config), opening_origin_sha256=config['opening_origin_sha256'],
                   started_at_ms=config['started_at_ms'], ends_at_ms=config['ends_at_ms']),
        executable=False, submission_enabled=False, economic_authority=False,
    )
    orders = deepcopy(case[1]['orders'])
    constraints = dict(schema='DEMO_GOLD_STRATEGY_CONSTRAINTS_V1',
        strategy_definition_sha256=value['budget_group']['strategy_definition_sha256'],
        decision_sha256='b'*64, orders_sha256=_digest(orders), not_before_ms=NOW, valid_until_ms=NOW+100)
    return value, registry, private, delegation, exact, constraints, orders


def module():
    return importlib.import_module('live.execution.demo_gold_future_template')


def test_template_is_strict_nonexecuting_and_never_an_old_exact_policy(case):
    value, registry, _, _, exact, _, _ = template_case(case)
    contract = module()
    assert contract.validate_template(value) is value
    assert contract.current_template_key(value, registry, NOW+1) == registry['keys'][0]
    assert contract.historical_template_binding(value, registry) is None
    assert contract.template_group_binding(value) == budget_group_binding(exact)
    with pytest.raises(ValueError):
        validate_policy(value)
    with pytest.raises(ValueError):
        contract.validate_template(exact)
    assert case[-1].snapshot() == []


@pytest.mark.parametrize('damage', [
    'policy_id', 'constraints', 'extra_binding', 'missing_reserve', 'zero_reserve',
    'executable', 'submission', 'authority', 'pilot_start', 'pilot_end',
    'pilot_hash', 'origin_kind', 'bool_time', 'missing_origin', 'non_gold',
])
def test_template_rejects_placeholder_authority_or_changed_original_shape(case, damage):
    value, *_ = template_case(case)
    if damage == 'policy_id': value['policy_id'] = 'invented-exact'
    elif damage == 'constraints': value['bindings']['execution_constraints_sha256'] = '0'*64
    elif damage == 'extra_binding': value['bindings']['approval_fallback'] = True
    elif damage == 'missing_reserve': value['budget_group'].pop('exit_reserve')
    elif damage == 'zero_reserve': value['budget_group']['exit_reserve']['grants'] = 0
    elif damage == 'executable': value['executable'] = True
    elif damage == 'submission': value['submission_enabled'] = True
    elif damage == 'authority': value['economic_authority'] = True
    elif damage == 'pilot_start': value['pilot']['started_at_ms'] = value['valid_from_ms']-1
    elif damage == 'pilot_end': value['pilot']['ends_at_ms'] = value['valid_until_ms']+1
    elif damage == 'pilot_hash': value['pilot']['configuration_sha256'] = 'invalid'
    elif damage == 'origin_kind': value['origin']['kind'] = 'EMPTY_REPLACEMENT'
    elif damage == 'bool_time': value['pilot']['started_at_ms'] = True
    elif damage == 'missing_origin': value.pop('origin')
    else: value['scope']['symbol'] = 'BTCUSDT'
    with pytest.raises(ValueError):
        module().validate_template(value)


def test_projection_uses_actual_constraints_and_retains_all_original_caps(case):
    value, _, _, _, exact, constraints, orders = template_case(case)
    original = deepcopy(value)
    policy = module().derive_exact_policy(value, proof_sha256='f'*64,
        constraints=constraints, orders=orders, now_ms=NOW+1)
    expected = deepcopy(exact)
    expected['policy_id'] = 'gold-delegated-'+'f'*64
    expected['bindings']['execution_constraints_sha256'] = _digest(constraints)
    assert policy == expected
    assert value == original
    assert validate_policy(policy) == policy
    assert budget_group_binding(policy) == module().template_group_binding(value)
    assert case[-1].snapshot() == []


@pytest.mark.parametrize('damage', ['orders', 'expired', 'future', 'definition', 'proof', 'template_window'])
def test_projection_rejects_invalid_or_unbound_exact_constraints(case, damage):
    value, _, _, _, _, constraints, orders = template_case(case)
    now, proof_hash = NOW+1, 'f'*64
    if damage == 'orders': orders[0]['quantity'] = '0.001'
    elif damage == 'expired': now = constraints['valid_until_ms']
    elif damage == 'future': now = constraints['not_before_ms']-1
    elif damage == 'definition': constraints['strategy_definition_sha256'] = 'c'*64
    elif damage == 'proof': proof_hash = 'not-a-digest'
    else: now = value['valid_until_ms']
    with pytest.raises(ValueError):
        module().derive_exact_policy(value, proof_sha256=proof_hash,
            constraints=constraints, orders=orders, now_ms=now)


def test_expired_template_remains_readable_but_cannot_provide_current_permission(case):
    value, registry, *_ = template_case(case)
    assert module().historical_template_binding(value, registry) is None
    with pytest.raises(ValueError):
        module().current_template_key(value, registry, value['valid_until_ms'])
    assert case[-1].snapshot() == []
