"""The first day scope keeps native limits and binds a real archived definition."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from live.execution.demo_batch_risk import _digest
from tests.live.test_demo_market_partition import NOW, book as book
from tests.live.test_demo_market_partition_runtime import installed as installed
from tests.live.test_gold_first_enrollment_review import first as first
from tests.tools.test_gold_first_enrollment_materials import materials as materials, assemble
from tests.tools.test_gold_bar_cost_replay import inputs
from tools import gold_bar_experiment
from tools.gold_bar_definition import define
from tools.gold_bar_review import review_request
from tools.perpetual_replay_research import source_identity


@pytest.fixture
def scope_inputs(materials, tmp_path):
    value = materials[0]
    _, files = assemble(materials)
    value['registry'] = json.loads(files['registry.json'])
    policy = json.loads(files['policy.json']);profile = json.loads(files['profile.json'])
    args = inputs();args['quantity'] = Decimal('.002');args['release_identity'] = source_identity()
    # Explicit small synthetic archived hypothesis. No production parameter change.
    archive = tmp_path/'bar-archive';gold_bar_experiment.archive(args, archive);review_request(archive)
    value['scope_archive'] = archive
    definition = define(archive, candidate='channel', source_directory=Path(__file__).resolve().parents[2])
    manifest = (archive/'manifest.json').read_bytes()
    runtime = dict(schema='GOLD_BAR_FRESH_EPISODE_INPUT_V1', source_commit='c'*40,
        trial_manifest_sha256=hashlib.sha256(manifest).hexdigest(), archive_source_commit=definition['source']['commit'],
        history_sha256='e'*64, candidate='channel', seconds=300)
    return value, policy, profile, definition, runtime


def arguments(parts):
    value, policy, profile, definition, runtime = parts
    report = value['runtime'].gold_budget_scope_review(state=value['state'], origin=value['origin'],
        as_of_ms=NOW + 140000)
    return dict(review=report, origin=value['origin'], registry=value['registry'], policy=policy,
        profile=profile, definition=definition, runtime=runtime, now_ms=NOW + 140000)


def test_first_day_preparation_is_unsigned_and_preserves_risk_and_prior_scope(scope_inputs):
    from tools import gold_strategy_budget_enrollment as preparation
    args = arguments(scope_inputs);before = deepcopy(args)
    request = preparation.prepare(**args)
    assert request['schema'] == 'GOLD_DEMO_BUDGET_SCOPE_REQUEST_V1'
    assert request['duration_seconds'] == 86400
    assert request['native_limits'] == args['profile']['native_limits']
    assert request['native_limits_sha256'] == _digest(args['profile']['native_limits'])
    assert request['strategy_definition_sha256'] == args['definition']['definition_hash']
    assert request['proposed_budget']['max_orders'] == 3
    assert request['proposed_budget']['max_total_notional'] == '750'
    assert request['original_scope']['max_quantity_per_order'] == '0.01'
    assert request['pilot_max_loss_fraction'] == '0.05'
    assert 'execution_constraints_sha256' not in json.dumps(request)
    assert all(request[key] is False for key in ('approval_granted', 'budget_enrolled',
        'submission_enabled', 'economic_authority'))
    assert args == before


@pytest.mark.parametrize('damage', ['quantity', 'nonfinite', 'definition_hash', 'candidate', 'archive',
    'runtime_path', 'runtime_extra', 'runtime_bool', 'runtime_source', 'native_limits', 'mandate',
    'strategy_qualified', 'legacy_loss', 'gold_wallet', 'authority_flag', 'expired_review', 'existing_budget'])
def test_scope_rejects_incompatible_or_substituted_materials(scope_inputs, damage):
    from tools import gold_strategy_budget_enrollment as preparation
    from services.execution.backtest_authority import exact_identity_hash
    args = deepcopy(arguments(scope_inputs))
    if damage in ('quantity', 'nonfinite'):
        args['definition']['size_and_timing']['quantity'] = '.1' if damage == 'quantity' else 'NaN'
        body = {key:value for key,value in args['definition'].items() if key != 'definition_hash'}
        args['definition']['definition_hash'] = exact_identity_hash(body, name='gold_bar_research_definition')
    elif damage == 'definition_hash': args['definition']['definition_hash'] = 'f'*64
    elif damage == 'candidate': args['runtime']['candidate'] = 'trend'
    elif damage == 'archive': args['runtime']['trial_manifest_sha256'] = 'f'*64
    elif damage == 'runtime_path': args['runtime']['history_sha256'] = '../../other'
    elif damage == 'runtime_extra': args['runtime']['command'] = 'untrusted'
    elif damage == 'runtime_bool': args['runtime']['seconds'] = True
    elif damage == 'runtime_source': args['runtime']['source_commit'] = 'd'*40
    elif damage == 'native_limits': args['profile']['native_limits']['fat_finger_qty'] = 100
    elif damage == 'mandate': args['profile']['mandate']['max_single_order_notional'] = 100000
    elif damage == 'strategy_qualified': args['definition']['economic_eligible'] = True
    elif damage == 'legacy_loss': args['review']['legacy_risk']['canonical_wallet'] = '5000'
    elif damage == 'gold_wallet': args['review']['observed_usdt_wallet'] = '4999.6801'
    elif damage == 'authority_flag': args['review']['authorization_current_verified'] = True
    elif damage == 'expired_review': args['now_ms'] += 31000
    else: args['policy']['schema'] = 'DEMO_GOLD_STRATEGY_BUDGET_POLICY_V1'
    before = deepcopy(args)
    with pytest.raises(ValueError): preparation.prepare(**args)
    assert args == before


def test_expired_original_functional_window_is_not_extended_by_day_scope(scope_inputs):
    from tools import gold_strategy_budget_enrollment as preparation
    from tests.live.test_demo_market_partition_runtime import shift
    value, policy, profile, definition, runtime = scope_inputs
    at = policy['valid_until_ms'] + 1
    origin = shift(value['origin'], at-(NOW+140000))
    report = value['runtime'].gold_budget_scope_review(state=value['state'], origin=origin, as_of_ms=at)
    before = deepcopy(policy), deepcopy(value['registry']), deepcopy(profile)
    request = preparation.prepare(review=report, origin=origin, registry=value['registry'], policy=policy,
        profile=profile, definition=definition, runtime=runtime, now_ms=at)
    assert request['original_policy_valid_until_ms'] < request['reviewed_at_ms']
    assert request['duration_seconds'] == 86400 and request['new_independent_mandate_required'] is True
    assert request['valid_until_ms']-at <= 30000
    assert before == (policy, value['registry'], profile)


def test_valid_existing_shared_group_cannot_be_replaced_by_first_day_preparation(scope_inputs):
    from tools import gold_strategy_budget_enrollment as preparation
    from live.execution.demo_issuer_policy import validate_policy
    args = arguments(scope_inputs);policy = deepcopy(args['policy'])
    policy['schema'] = 'DEMO_GOLD_STRATEGY_BUDGET_POLICY_V1'
    # Structural existing-policy fixture, never submitted or used for a grant.
    policy['bindings']['execution_constraints_sha256'] = _digest({'synthetic_existing_scope_guard':
        args['definition']['definition_hash']})
    policy['budget_group'] = dict(group_id='synthetic-existing-day-scope',
        strategy_definition_sha256=args['definition']['definition_hash'],
        valid_from_ms=policy['valid_from_ms'], valid_until_ms=policy['valid_until_ms'],
        **{key:policy[key] for key in ('max_grants', 'max_orders', 'max_total_notional',
            'max_cancel_grants', 'max_cancel_orders')})
    validate_policy(policy)
    with pytest.raises(ValueError, match='BUDGET_SCOPE_FIRST_FUNCTIONAL_GOLD_REQUIRED'):
        preparation.prepare(**(args | {'policy':policy}))


def test_bound_stricter_native_limit_is_preserved_instead_of_expanded(scope_inputs):
    from tools import gold_strategy_budget_enrollment as preparation
    args = arguments(scope_inputs);profile = deepcopy(args['profile']);policy = deepcopy(args['policy'])
    profile['native_limits']['fat_finger_qty'] = .001
    policy['bindings']['native_limits_sha256'] = _digest(profile['native_limits'])
    before = deepcopy(profile), deepcopy(policy)
    with pytest.raises(ValueError, match='BUDGET_SCOPE_QUANTITY_EXCEEDS_ORIGINAL_LIMIT'):
        preparation.prepare(**(args | {'profile':profile, 'policy':policy}))
    assert before == (profile, policy)
