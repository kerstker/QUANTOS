"""A first-day scope bundle cannot stand in for an exact order approval.

Original V2 stores and archived definitions are used. Account, operator and
Ed25519 keys are explicit isolated fixtures, never a deployed authorization.
"""
from copy import deepcopy
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from live.execution.demo_batch_risk import _digest
from tests.live.test_demo_market_partition import book as book
from tests.live.test_demo_market_partition_runtime import installed as installed, shift
from tests.live.test_gold_first_enrollment_review import first as first
from tests.tools.test_gold_first_enrollment_materials import materials as materials
from tests.tools.test_gold_budget_scope_preparation import scope_inputs as scope_inputs, arguments


@pytest.fixture
def day_materials(scope_inputs, materials):
    from tools.gold_strategy_budget_enrollment import prepare
    args = arguments(scope_inputs)
    args['origin'] = shift(args['origin'], 3601000)
    args['now_ms'] += 3601000
    value = scope_inputs[0]
    args['review'] = value['runtime'].gold_budget_scope_review(state=value['state'], origin=args['origin'],
        as_of_ms=args['now_ms'])
    request = prepare(**args)
    approval = dict(schema='DEMO_GOLD_BUDGET_SCOPE_OPERATOR_V1', purpose=request['purpose'],
        request_sha256=request['request_sha256'], original_cycle_sha256='a'*64,
        operator_id='isolated-operator', approved_at_ms=args['now_ms'],
        duration_seconds=86400, installation_allowance_seconds=300,
        execution_approval_granted=False, economic_authority=False)
    return dict(request=request, approval=approval, private_key=Ed25519PrivateKey.generate(),
        stop_image=materials[4], **args)


def checked(parts):
    from tools import gold_strategy_budget_materials as tool, demo_operating_bundle as bundle
    manifest, files = tool.assemble(**parts)
    native = bundle.validate_materials(manifest=manifest, files=files, existing_registry=parts['registry'],
        source_commit=parts['policy']['executor_release_commit'],
        account_ref=parts['policy']['account_ref_sha256'], now_ms=parts['now_ms'])
    return manifest, files, native


def test_first_day_scope_uses_original_bundle_reader_without_executable_policy(day_materials):
    from live.execution.demo_issuer_policy import validate_policy, current_key
    before = deepcopy({k:v for k,v in day_materials.items() if k not in ('private_key',)})
    manifest, files, native = checked(day_materials)
    assert manifest['schema'] == 'DEMO_GOLD_BUDGET_SCOPE_BUNDLE_V1' and manifest['tasks'] == []
    assert native['tasks'] == [] and native['scope_only'] is True
    scope = native['policy']
    assert scope['schema'] == 'DEMO_GOLD_STRATEGY_BUDGET_SCOPE_V1'
    assert 'execution_constraints_sha256' not in json.dumps(scope)
    assert native['registry']['keys'][:-1] == day_materials['registry']['keys']
    assert scope['valid_until_ms']-scope['valid_from_ms'] == (86400+300)*1000
    assert scope['scope_enrollment']['execution_approval_granted'] is False
    assert native['profile']['native_limits'] == day_materials['profile']['native_limits']
    assert native['profile']['initial_peak_equity'] == '4200'
    assert files['kill-switch.sqlite3'] == day_materials['stop_image']
    assert day_materials['request']['original_cycle_review']['legacy_risk']['canonical_wallet'] == '4999.6801'
    with pytest.raises(ValueError): validate_policy(scope)
    with pytest.raises(ValueError): current_key(scope, native['registry'], day_materials['now_ms'])
    assert before == {k:v for k,v in day_materials.items() if k not in ('private_key',)}


def test_operator_must_explicitly_confirm_the_bounded_installation_allowance(day_materials):
    from tools import gold_strategy_budget_materials as tool
    changed = deepcopy(day_materials['approval']);changed.pop('installation_allowance_seconds')
    with pytest.raises(ValueError): tool.assemble(**(day_materials | {'approval':changed}))
    changed = deepcopy(day_materials['approval']);changed['installation_allowance_seconds'] = 86400
    with pytest.raises(ValueError): tool.assemble(**(day_materials | {'approval':changed}))


def test_scope_permits_one_day_start_window_without_renewing_its_end(day_materials):
    from live.execution.demo_gold_budget_scope import pilot_window
    _, _, native = checked(day_materials);scope = native['policy'];at = day_materials['now_ms']
    value = pilot_window(scope, registry=native['registry'], now_ms=at+299999)
    assert value['ends_at_ms']-value['started_at_ms'] == 86400000
    assert value['ends_at_ms'] < scope['valid_until_ms']
    with pytest.raises(ValueError): pilot_window(scope, registry=native['registry'], now_ms=at+300001)
    assert scope['valid_from_ms'] == at


def test_retained_bundle_review_uses_the_original_confirmation_time(day_materials):
    from tools import demo_operating_bundle as bundle
    manifest, files, native = checked(day_materials)
    later = bundle.validate_materials(manifest=manifest, files=files, existing_registry=day_materials['registry'],
        source_commit=day_materials['policy']['executor_release_commit'],
        account_ref=day_materials['policy']['account_ref_sha256'], now_ms=day_materials['now_ms']+90000)
    assert later['policy'] == native['policy']
    assert later['profile'] == native['profile']


def test_original_issuer_rejects_scope_before_reserving_any_grant(day_materials, tmp_path):
    from live.execution.demo_issuer import issue_submission
    from live.execution.demo_issuer_store import IssuerStore
    _, _, native = checked(day_materials)
    store = IssuerStore(tmp_path/'scope-not-grant.db', create=True)
    try:
        with pytest.raises(ValueError):
            issue_submission({}, policy=native['policy'], registry=native['registry'],
                key=day_materials['private_key'], store=store, now_ms=day_materials['now_ms'])
        assert store.snapshot() == []
    finally: store.close()


@pytest.mark.parametrize('damage', ['early_operator', 'expired_request', 'wrong_operator', 'wrong_purpose',
    'bool_duration', 'execution_permission', 'cycle_path', 'request_budget', 'request_loss',
    'original_wallet', 'original_risk', 'original_source', 'active_old_key', 'reused_signing_key'])
def test_supplied_approval_cannot_change_the_original_scope(day_materials, materials, damage):
    from tools import gold_strategy_budget_materials as tool
    args = dict(day_materials)
    args['approval'] = deepcopy(args['approval']);args['request'] = deepcopy(args['request'])
    if damage == 'early_operator': args['approval']['approved_at_ms'] -= 1
    elif damage == 'expired_request': args['now_ms'] += 30001
    elif damage == 'wrong_operator': args['approval']['operator_id'] = 'AUTO'
    elif damage == 'wrong_purpose': args['approval']['purpose'] = 'AUTOMATIC_TRADING_APPROVAL'
    elif damage == 'bool_duration': args['approval']['duration_seconds'] = True
    elif damage == 'execution_permission': args['approval']['execution_approval_granted'] = True
    elif damage == 'cycle_path': args['approval']['original_cycle_sha256'] = '../../other'
    elif damage == 'request_budget': args['request']['proposed_budget']['max_orders'] += 1
    elif damage == 'request_loss': args['request']['original_cycle_review']['legacy_risk']['canonical_wallet'] = '5000'
    elif damage == 'original_wallet':
        args['origin'] = deepcopy(args['origin'])
        for row in args['origin']['reads']:
            if row['kind'].startswith('account'):
                next(item for item in row['data']['assets'] if item['asset'] == 'USDT')['walletBalance'] = '5000'
    elif damage == 'original_risk':
        args['profile'] = deepcopy(args['profile']);args['profile']['native_limits']['max_order_notional'] = 100000
    elif damage == 'original_source':
        args['policy'] = deepcopy(args['policy']);args['policy']['executor_release_commit'] = 'd'*40
    elif damage == 'active_old_key':
        args['registry'] = deepcopy(args['registry']);args['registry']['keys'][-1]['valid_until_ms'] += 3601000
    else:
        # This is the actual last original registered fixture key, never a broker credential.
        args['private_key'] = materials[5]
    with pytest.raises(ValueError): tool.assemble(**args)


@pytest.mark.parametrize('damage', ['signature', 'group_count', 'scope_quantity', 'installation_allowance',
    'permission', 'profile_loss', 'original_profile', 'private_key', 'original_cycle', 'task', 'executable_schema', 'stop_image'])
def test_original_bundle_reader_rejects_substitution_even_with_recomputed_file_hashes(day_materials, damage):
    from tools import demo_operating_bundle as bundle
    manifest, files, _ = checked(day_materials)
    manifest = deepcopy(manifest);files = dict(files)
    target = 'policy.json';value = json.loads(files[target])
    if damage == 'signature': value['signature_base64url'] = 'A'*86
    elif damage == 'group_count': value['budget_group']['max_orders'] += 1
    elif damage == 'scope_quantity': value['scope']['max_quantity_per_order'] = '1'
    elif damage == 'installation_allowance': value['scope_enrollment']['installation_allowance_seconds'] = 86400
    elif damage == 'permission': value['scope_enrollment']['execution_approval_granted'] = True
    elif damage == 'profile_loss':
        target = 'profile.json';value = json.loads(files[target]);value['initial_day_pnl'] = '0.1'
    elif damage == 'original_profile':
        target = 'original-profile.json';value = json.loads(files[target]);value['native_limits']['daily_loss_limit'] = 1000
    elif damage == 'private_key':
        from tools.demo_operator_setup import encoded
        target = 'signing-key.json';value = json.loads(files[target])
        value['private_key_base64url'] = encoded(Ed25519PrivateKey.generate().private_bytes_raw())
    elif damage == 'original_cycle':
        target = 'operator-approval.json';value = json.loads(files[target]);value['original_cycle_sha256'] = 'b'*64
    elif damage == 'stop_image':
        # SQLite can still verify its logical history with an unused appended
        # page. Signed material custody must retain the exact original image.
        files['kill-switch.sqlite3'] += b'\x00'*4096
    elif damage == 'task': manifest['tasks'] = ['c'*64]
    else: value['schema'] = 'DEMO_GOLD_STRATEGY_BUDGET_POLICY_V1'
    files[target] = bundle.canonical(value)
    manifest['files'] = {name:bundle.sha(body) for name,body in files.items()}
    with pytest.raises(ValueError):
        bundle.validate_materials(manifest=manifest, files=files, existing_registry=day_materials['registry'],
            source_commit=day_materials['policy']['executor_release_commit'],
            account_ref=day_materials['policy']['account_ref_sha256'], now_ms=day_materials['now_ms'])


def test_stricter_original_grant_ttl_and_inherited_risk_baselines_are_kept(day_materials):
    from tools.gold_strategy_budget_enrollment import prepare
    args = dict(day_materials);args['policy'] = deepcopy(args['policy']);args['profile'] = deepcopy(args['profile'])
    args['policy']['grant_ttl_ms'] = 5000
    args['profile'].update(initial_day_pnl='-2', initial_peak_equity='4500', minimum_plan_turnover=.1)
    base = {key:args[key] for key in ('review', 'origin', 'registry', 'policy', 'profile', 'definition', 'runtime', 'now_ms')}
    args['request'] = prepare(**base)
    args['approval'] = deepcopy(args['approval']);args['approval']['request_sha256'] = args['request']['request_sha256']
    _, _, native = checked(args)
    assert native['policy']['grant_ttl_ms'] == 5000
    assert native['profile']['initial_day_pnl'] == '-2'
    assert native['profile']['initial_peak_equity'] == '4500'
    assert native['profile']['minimum_plan_turnover'] == .1


def test_scope_assembly_retains_real_native_gold_history(scope_inputs, day_materials, materials, tmp_path, monkeypatch):
    from tests.live.test_gold_budget_scope_review import terminal_gold_history
    from tools.gold_strategy_budget_enrollment import prepare
    from live.execution import demo_position_context as position
    # The first-enrollment fixture replaces its temporary registered key.
    # Use the key from that actual synthetic enrollment, rather than its prior fixture key.
    installed_value = dict(scope_inputs[0], key=deepcopy(day_materials['registry']['keys'][-1]), private=materials[5])
    value, state, observed, _ = terminal_gold_history(installed_value, tmp_path, monkeypatch)
    args = dict(day_materials);args['origin'] = shift(observed, 3601000)
    args['now_ms'] = args['origin']['completed_at_ms']+1
    args['review'] = value['runtime'].gold_budget_scope_review(state=state, origin=args['origin'], as_of_ms=args['now_ms'])
    base = {key:args[key] for key in ('review', 'origin', 'registry', 'policy', 'profile', 'definition', 'runtime', 'now_ms')}
    args['request'] = prepare(**base);args['approval'] = deepcopy(args['approval'])
    args['approval'].update(request_sha256=args['request']['request_sha256'], approved_at_ms=args['now_ms'])
    context = args['review']['gold_position_context'];assert context is not None
    account = position.validate_origin(args['origin'], context, as_of_ms=args['now_ms'])
    _, _, native = checked(args)
    assert native['profile']['initial_day_pnl'] == account['day_pnl']
    assert native['profile']['initial_peak_equity'] == context['initial_equity'] == '4200'
    assert native['profile']['minimum_plan_turnover'] >= position.continuation_risk(
        {'account':account, 'plan_turnover':0}, context)['plan_turnover']
    assert args['request']['original_cycle_review']['full_state_sha256'] == _digest(state)
    assert state['reservations'][:len(value['state']['reservations'])] == value['state']['reservations']
