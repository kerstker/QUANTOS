"""Prepare the first unsigned day budget from protected original Gold inputs.

This is scope review, not an issuer policy. No placeholder decision digest,
key, mandate, pilot baseline, allowance, execution task or service is created.
Future independent enrollment and exact-decision approval remain required.
"""
import argparse
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import re

from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import GOLD_POLICY_SCHEMA, historical_policy_binding, require
from live.execution.demo_market_partition_runtime import validate_gold_budget_review
from live.execution.demo_request_inputs import profile_declarations
from live.execution.demo_session_inputs import _limits, _mandate
from services.execution.backtest_authority import exact_identity_hash
from services.execution.perpetual_ledger_v2 import ledger_decimal_context
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_operator_setup as setup, demo_successor_setup as successor
from tools import demo_operating_bundle as installer, demo_supervisor as supervisor
from tools.binance_demo_market_inputs import source_hash as market_source_hash
from tools.gold_bar_worker_inputs import TRIAL_FILES, protected_tree
from tools.gold_bar_definition import PreparedBarDefinition
from tools.gold_cost_review import _read
from tools.demo_resident_recovery import _check_path

DAY_SECONDS = 86400


def runtime_check(runtime, source_commit):
    require(type(runtime) is dict, 'BUDGET_SCOPE_RUNTIME')
    schema = runtime.get('schema')
    extra = {'GOLD_BAR_RETAINED_EPISODE_INPUT_V1': 'observation_sha256 archive_source_commit entry_decision_ms',
        'GOLD_BAR_FRESH_EPISODE_INPUT_V1': 'archive_source_commit candidate seconds history_sha256',
        'GOLD_BAR_AUTO_EPISODE_INPUT_V1': 'archive_source_commit candidate seconds episode_id'}
    require(schema in extra, 'BUDGET_SCOPE_BAR_RUNTIME_REQUIRED')
    _fields(runtime, 'schema source_commit trial_manifest_sha256 '+extra[schema])
    require(runtime['source_commit'] == source_commit, 'BUDGET_SCOPE_RUNTIME_SOURCE')
    for key in ('trial_manifest_sha256', 'archive_source_commit', 'observation_sha256', 'history_sha256', 'episode_id'):
        if key in runtime:
            size = 40 if key == 'archive_source_commit' else 64
            require(type(runtime[key]) is str and re.fullmatch('[a-f0-9]{'+str(size)+'}', runtime[key]),
                'BUDGET_SCOPE_RUNTIME_IDENTITY')
    if schema == 'GOLD_BAR_RETAINED_EPISODE_INPUT_V1':
        require(type(runtime['entry_decision_ms']) is int and 0 <= runtime['entry_decision_ms'] <= 2**63-1,
            'BUDGET_SCOPE_RUNTIME_TIME')
    else:
        require(runtime['candidate'] in ('channel', 'trend', 'reversion')
            and type(runtime['seconds']) is int and 1 <= runtime['seconds'] <= 900, 'BUDGET_SCOPE_RUNTIME_PLAN')


class DefinitionReadSession:
    """One trusted operation's prepared math; every use still reads original bytes.

    Hold only inside the existing controller lease. This is a process-local
    capability, never a persisted review, approval or cross-operation cache.
    Runtime, custody, archive/source bytes and live policy evidence are checked
    by the original callers at every existing boundary.
    """
    def __init__(self):
        self._binding = None
        self._prepared = None

    def definition(self, trial, *, candidate, source_directory, source_commit, uid):
        key = (str(trial), str(source_directory), candidate, source_commit, uid)
        require(self._binding is None or self._binding == key, 'BUDGET_SCOPE_DEFINITION_SESSION_CHANGED')
        if self._prepared is None:
            prepared = PreparedBarDefinition(trial, candidate=candidate, source_directory=source_directory)
            self._binding = key
            self._prepared = prepared
        return self._prepared.definition(trial, candidate=candidate, source_directory=source_directory)


def load_definition(root, runtime, *, source_commit, uid, definition_session=None):
    """Use original frozen archives at fixed locations; never observe a new bar."""
    runtime_check(runtime, source_commit)
    require(definition_session is None or type(definition_session) is DefinitionReadSession,
            'BUDGET_SCOPE_DEFINITION_SESSION')
    if runtime['schema'] == 'GOLD_BAR_RETAINED_EPISODE_INPUT_V1':
        from tools.gold_bar_worker_inputs import load
        engine, _ = load(root, runtime, source_commit=source_commit, uid=uid)
        return engine.verified_definition()
    trial = root/'strategy-bar-trials'/runtime['trial_manifest_sha256']
    source = root/'strategy-bar-sources'/runtime['archive_source_commit']
    for path in (root, trial.parent, trial, source.parent):
        _check_path(path, uid, directory=True)
    for name in TRIAL_FILES:
        _check_path(trial/name, uid, directory=False)
    manifest = _read(trial, 'manifest.json')
    require(hashlib.sha256(manifest).hexdigest() == runtime['trial_manifest_sha256']
        and decode(manifest)['source']['commit'] == runtime['archive_source_commit'], 'BUDGET_SCOPE_ARCHIVE')
    facts = protected_tree(source, uid)
    if definition_session is None:
        prepared = PreparedBarDefinition(trial, candidate=runtime['candidate'], source_directory=source)
        definition = prepared.definition(trial, candidate=runtime['candidate'], source_directory=source)
    else:
        definition = definition_session.definition(trial, candidate=runtime['candidate'], source_directory=source,
            source_commit=source_commit, uid=uid)
    require(_read(trial, 'manifest.json') == manifest and protected_tree(source, uid) == facts,
        'BUDGET_SCOPE_ARCHIVE_CHANGED')
    return definition


@ledger_decimal_context()
def verify_strategy_parameters(*, origin, profile, definition, runtime, source_commit, scope):
    """Original finite definition/venue/risk checks for read-only installation."""
    runtime_check(runtime, source_commit)
    limits = _limits(profile['native_limits']);mandate = _mandate(profile['mandate'])
    require(type(definition) is dict and definition.get('schema') == 'GOLD_BAR_RESEARCH_DEFINITION_V1'
        and definition.get('symbol') == 'XAUUSDT' and definition.get('candidate') in ('channel', 'trend', 'reversion')
        and definition.get('sides') == ['BUY', 'SELL']
        and all(definition.get(key) is False for key in ('selected_for_execution', 'authoritative',
            'economic_eligible', 'submission_enabled')), 'BUDGET_SCOPE_UNQUALIFIED_DEFINITION')
    body = {key: value for key, value in definition.items() if key != 'definition_hash'}
    require(definition['definition_hash'] == exact_identity_hash(body, name='gold_bar_research_definition')
        and definition['archive_files']['manifest.json'] == runtime['trial_manifest_sha256']
        and definition['source']['commit'] == runtime['archive_source_commit']
        and (runtime['schema'] == 'GOLD_BAR_RETAINED_EPISODE_INPUT_V1'
            or definition['candidate'] == runtime['candidate']), 'BUDGET_SCOPE_DEFINITION_BINDING')
    timing = definition['size_and_timing']
    _fields(timing, 'quantity entry_timeout_ms max_quote_age_ms')
    require(type(timing['quantity']) is str and type(timing['entry_timeout_ms']) is int
        and timing['entry_timeout_ms'] == 5000 and type(timing['max_quote_age_ms']) is int
        and timing['max_quote_age_ms'] == 30000 and definition['risk']['max_loss_fraction'] == '0.05',
        'BUDGET_SCOPE_STRATEGY_PARAMETERS')
    quantity = Decimal(timing['quantity'])
    require(quantity.is_finite() and quantity > 0, 'BUDGET_SCOPE_STRATEGY_QUANTITY')
    require(scope['allow_opening'] is True and set(definition['sides']) <= set(scope['sides'])
        and 'IOC' in scope['time_in_force'] and 'XAUUSDT' in limits.allowed_instruments
        and origin['account_ref_sha256'] in limits.allowed_accounts and 'XAUUSDT' in mandate.allowed_instruments,
        'BUDGET_SCOPE_ORIGINAL_PERMISSION_SCOPE')
    cap = dict(limits.max_position_qty).get('XAUUSDT', limits.default_max_position_qty)
    require(quantity <= min(Decimal(scope['max_quantity_per_order']), Decimal(str(cap)),
        Decimal(str(limits.fat_finger_qty))), 'BUDGET_SCOPE_QUANTITY_EXCEEDS_ORIGINAL_LIMIT')
    data = {row['kind']: row['data'] for row in origin['reads']}
    low, high = Decimal(data['book']['bidPrice']), Decimal(data['book']['askPrice'])
    terms = {item['filterType']: item for item in data['rules']['terms']['filters']}
    lot = terms['LOT_SIZE']
    require(Decimal(lot['minQty']) <= quantity <= Decimal(lot['maxQty'])
        and (quantity-Decimal(lot['minQty'])) % Decimal(lot['stepSize']) == 0
        and quantity*low >= Decimal(terms['MIN_NOTIONAL']['notional']), 'BUDGET_SCOPE_VENUE_QUANTITY')
    require(quantity*high <= min(Decimal(scope['max_notional_per_order']), Decimal(scope['max_notional_per_grant']),
        Decimal(str(limits.max_order_notional)), Decimal(str(limits.max_gross_exposure)),
        Decimal(str(limits.max_net_exposure)), Decimal(str(mandate.max_single_order_notional)),
        Decimal(str(dict(mandate.per_instrument_max_notional).get('XAUUSDT', 0)))),
        'BUDGET_SCOPE_NOTIONAL_EXCEEDS_ORIGINAL_LIMIT')


@ledger_decimal_context()
def prepare(*, review, origin, registry, policy, profile, definition, runtime, now_ms):
    """Pure unsigned scope proposal; copied risk limits are never enlarged."""
    historical_policy_binding(policy, registry)
    require(policy['schema'] == GOLD_POLICY_SCHEMA and policy['scope']['symbol'] == 'XAUUSDT',
        'BUDGET_SCOPE_FIRST_FUNCTIONAL_GOLD_REQUIRED')
    source = policy['executor_release_commit']
    runtime_check(runtime, source)
    validate_gold_budget_review(review, origin=origin, registry=registry, source_commit=source, as_of_ms=now_ms)
    require(policy['account_ref_sha256'] == review['account_ref_sha256']
        and any((key['authority_id'], key['key_id']) == (policy['authority_id'], policy['key_id'])
            for key in registry['keys'][len(review['legacy_registry']['keys']):]), 'BUDGET_SCOPE_POST_CUT_IDENTITY')
    profile_declarations(profile, policy, {'symbol': 'XAUUSDT', 'source_sha256': market_source_hash()})
    scope = policy['scope']
    verify_strategy_parameters(origin=origin, profile=profile, definition=definition, runtime=runtime,
        source_commit=source, scope=scope)
    budget = {key: deepcopy(policy[key]) for key in ('max_grants', 'max_orders', 'max_total_notional',
        'max_cancel_grants', 'max_cancel_orders')}
    request = dict(schema='GOLD_DEMO_BUDGET_SCOPE_REQUEST_V1', status='INDEPENDENT_SCOPE_ENROLLMENT_REQUIRED',
        purpose='DEMO_GOLD_FIRST_DAY_FUNCTIONAL_PILOT_SCOPE_ONLY', source_commit=source,
        account_ref_sha256=policy['account_ref_sha256'], registry_sha256=review['registry_sha256'],
        current_policy_sha256=_digest(policy), current_profile_sha256=_digest(profile),
        original_policy_valid_from_ms=policy['valid_from_ms'], original_policy_valid_until_ms=policy['valid_until_ms'],
        reviewed_at_ms=now_ms, valid_until_ms=review['valid_until_ms'], original_cycle_review=deepcopy(review),
        runtime_sha256=_digest(runtime), runtime=deepcopy(runtime), strategy_definition_sha256=definition['definition_hash'],
        definition=deepcopy(definition), duration_seconds=DAY_SECONDS, proposed_budget=budget,
        original_scope=deepcopy(scope), native_limits=deepcopy(profile['native_limits']),
        native_limits_sha256=_digest(profile['native_limits']), pilot_max_loss_fraction='0.05',
        new_independent_mandate_required=True, exact_decision_approval_required=True,
        pilot_installation_verified=False, approval_granted=False, budget_enrolled=False,
        submission_enabled=False, economic_authority=False)
    return request | {'request_sha256': _digest(request)}


def capture_original_inputs(*, source_commit):
    """Caller holds the original maintenance lease; all root/source checks remain."""
    from tools.demo_functional_cycle import Runtime
    from tools.gold_strategy_controller import observation_configuration
    registry, policy = observation_configuration(source_commit)
    require(policy['schema'] == GOLD_POLICY_SCHEMA and policy['scope']['symbol'] == 'XAUUSDT',
        'BUDGET_SCOPE_FIRST_FUNCTIONAL_GOLD_REQUIRED')
    runtime_raw = supervisor._read_root_owned_json(supervisor.STATE/'strategy-runtime.json')
    profile_raw = supervisor._read_root_owned_json(installer.CONFIG/'profile.json')
    runtime = decode(runtime_raw);profile = decode(profile_raw)
    stage = successor.active_setup();original = successor.read(stage/'result.json')
    spec = setup.spec_check(successor.read(stage/'spec.json'))
    require(spec['symbol'] == 'XAUUSDT' and setup.digest(spec) == original['spec_sha256']
        and spec['source_commit'] == original['source_commit'] == source_commit, 'BUDGET_SCOPE_ORIGINAL_ENROLLMENT')
    definition = load_definition(installer.EXECUTOR, runtime, source_commit=source_commit, uid=registry['executor_uid'])
    current, origin = setup._runtime_inputs(source_commit, continuation=True, symbol='XAUUSDT')
    require(current == registry, 'BUDGET_SCOPE_CAPTURE_REGISTRY_CHANGED')
    identity = hashlib.sha256((json.dumps(origin, sort_keys=True, indent=2)+'\n').encode()).hexdigest()
    channel = Runtime(source_commit, registry, setup.digest(original))
    try:
        result = channel.invoke('context', payload={'gold_budget_scope_origin_sha256': identity}, timeout=60)
    finally:
        channel.close()
    require(result.get('status') == 'GOLD_BUDGET_SCOPE_REVIEW'
        and result.get('submission_enabled') is False and result.get('economic_authority') is False,
        'BUDGET_SCOPE_WORKER_UNAVAILABLE')
    require(observation_configuration(source_commit) == (registry, policy)
        and supervisor._read_root_owned_json(supervisor.STATE/'strategy-runtime.json') == runtime_raw
        and supervisor._read_root_owned_json(installer.CONFIG/'profile.json') == profile_raw
        and successor.active_setup() == stage and successor.read(stage/'result.json') == original
        and successor.read(stage/'spec.json') == spec
        and _digest(load_definition(installer.EXECUTOR, runtime, source_commit=source_commit,
            uid=registry['executor_uid'])) == _digest(definition), 'BUDGET_SCOPE_INPUTS_CHANGED')
    inputs = dict(review=result['review'], origin=origin, registry=registry, policy=policy,
        profile=profile, definition=definition, runtime=runtime, now_ms=installer.now_ms())
    return dict(request=prepare(**inputs), original_cycle_sha256=setup.digest(original), **inputs)


def prepare_original(*, source_commit):
    """Root-owned review via the original private role and existing cycle ID."""
    with supervisor.controller_lease(maintenance=True):
        return capture_original_inputs(source_commit=source_commit)['request']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit', required=True)
    args = parser.parse_args(argv)
    try:
        request = prepare_original(source_commit=args.source_commit)
        print(json.dumps(request, sort_keys=True, allow_nan=False));return 0
    except Exception as error:
        print(json.dumps(dict(status='GOLD_BUDGET_SCOPE_PREPARATION_REJECTED', reason=str(error),
            source_commit=args.source_commit, approval_granted=False, budget_enrolled=False,
            submission_enabled=False, economic_authority=False), sort_keys=True));return 3


if __name__ == '__main__':
    raise SystemExit(main())
