"""Assemble supplied human-confirmed first-day scope materials, never install.

Pure assembly cannot authenticate an operator. A future original sudo/TTY
workflow must establish custody and confirmation before calling this function.
This module has no CLI, capture, approval, dispatch or service entry point.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import re

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from live.execution import demo_gold_budget_scope as scope_contract, demo_position_context as position
from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import require
from live.execution.demo_request_inputs import profile_declarations
from live.execution.demo_session_inputs import _limits, _mandate
from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context
from services.validation.demo_submission import registry_sha256, _integer, _digest as digest_check
from tools import demo_operator_setup as setup
from tools.binance_demo_market_inputs import source_hash as market_source_hash
from tools.gold_strategy_budget_enrollment import prepare

SCOPE_FILES = {'request.json', 'operator-approval.json', 'original-policy.json',
    'original-profile.json', 'original-origin.json'}


def _inputs(request, *, review, origin, registry, policy, profile, definition, runtime, now_ms):
    _integer(now_ms)
    require(type(request) is dict and type(request.get('reviewed_at_ms')) is int
        and type(request.get('valid_until_ms')) is int
        and request['reviewed_at_ms'] <= now_ms < request['valid_until_ms'], 'GOLD_SCOPE_REQUEST_EXPIRED')
    expected = prepare(review=review, origin=origin, registry=registry, policy=policy, profile=profile,
        definition=definition, runtime=runtime, now_ms=request['reviewed_at_ms'])
    require(_digest(request) == _digest(expected), 'GOLD_SCOPE_REQUEST_CHANGED')
    # The original review must still be fresh at assembly, not only at creation.
    from live.execution.demo_market_partition_runtime import validate_gold_budget_review
    validate_gold_budget_review(review, origin=origin, registry=registry,
        source_commit=policy['executor_release_commit'], as_of_ms=now_ms)


def _approval(approval, request, now_ms):
    _fields(approval, 'schema purpose request_sha256 original_cycle_sha256 operator_id approved_at_ms '
        'duration_seconds installation_allowance_seconds execution_approval_granted economic_authority')
    require(approval['schema'] == 'DEMO_GOLD_BUDGET_SCOPE_OPERATOR_V1'
        and approval['purpose'] == request['purpose']
        and approval['request_sha256'] == request['request_sha256'], 'GOLD_SCOPE_OPERATOR_SCOPE')
    digest_check(approval['original_cycle_sha256'])
    name = approval['operator_id']
    require(type(name) is str and re.fullmatch('[A-Za-z0-9_.@-]{1,96}', name)
        and name.upper() not in ('AI', 'AUTO', 'SYSTEM', 'AGENT', 'CODEX'), 'GOLD_SCOPE_OPERATOR_IDENTITY')
    for key in ('approved_at_ms', 'duration_seconds', 'installation_allowance_seconds'): _integer(approval[key])
    require(request['reviewed_at_ms'] <= approval['approved_at_ms'] <= now_ms < request['valid_until_ms']
        and approval['duration_seconds'] == scope_contract.DAY_SECONDS
        and approval['installation_allowance_seconds'] == scope_contract.INSTALLATION_ALLOWANCE_SECONDS
        and approval['execution_approval_granted'] is False and approval['economic_authority'] is False,
        'GOLD_SCOPE_OPERATOR_WINDOW_OR_PERMISSION')


@ledger_decimal_context()
def _profile(request, approval, original, origin, now_ms):
    result = deepcopy(original);identity = _digest(approval);at = approval['approved_at_ms']
    until = at+(scope_contract.DAY_SECONDS+scope_contract.INSTALLATION_ALLOWANCE_SECONDS)*1000
    sid = 'gold-scope-'+identity[:16]
    result['mandate'].update(mandate_id='gold-scope-mandate-'+identity[:16], approved_by=[approval['operator_id']],
        issued_at=at, expires_at=until)
    result['strategy'] = dict(strategy_id=sid, approved=True, approval_id=identity)
    result['attestations'] = {sid:dict(strategy_id=sid, kind='human_override', attested_by=approval['operator_id'],
        attested_at=at, reason='Human-confirmed finite Demo scope only; exact decision approval still required; '
        'no profitability, economic authority or mainnet validation. Enrollment '+identity,
        validation_result_hash='')}
    context = request['original_cycle_review']['gold_position_context']
    if context is not None:
        account = position.validate_origin(origin, context, as_of_ms=now_ms)
        result['initial_day_pnl'] = account['day_pnl']
        result['initial_peak_equity'] = canonical_decimal(max(Decimal(original['initial_peak_equity']),
            Decimal(context['initial_equity']), Decimal(context['peak_equity_floor']), Decimal(account['equity'])))
        result['minimum_plan_turnover'] = max(original['minimum_plan_turnover'],
            position.continuation_risk({'account':account, 'plan_turnover':0}, context)['plan_turnover'])
    _limits(result['native_limits']);_mandate(result['mandate'])
    return result


def _scope(request, approval, registry, profile, original_policy, stop_image):
    identity = _digest(approval);at = approval['approved_at_ms'];key = registry['keys'][-1]
    until = at+(scope_contract.DAY_SECONDS+scope_contract.INSTALLATION_ALLOWANCE_SECONDS)*1000
    result = dict(schema=scope_contract.SCHEMA, environment='BINANCE_USDM_DEMO',
        policy_id='gold-scope-'+identity[:16], deployment_sha256=registry['deployment_sha256'],
        registry_sha256=registry_sha256(registry), executor_release_commit=request['source_commit'],
        account_ref_sha256=request['account_ref_sha256'], authority_id=key['authority_id'], key_id=key['key_id'],
        valid_from_ms=at, valid_until_ms=until, grant_ttl_ms=original_policy['grant_ttl_ms'],
        **deepcopy(request['proposed_budget']), scope=deepcopy(request['original_scope']),
        bindings=dict(strategy_id=profile['strategy']['strategy_id'], mandate_sha256=_digest(profile['mandate']),
            validation_sha256=_digest(profile['attestations']), native_limits_sha256=_digest(profile['native_limits']),
            strategy_approval_sha256=_digest(profile['strategy'])))
    result['budget_group'] = dict(group_id='gold-day-'+identity[:16],
        strategy_definition_sha256=request['strategy_definition_sha256'], valid_from_ms=at,
        valid_until_ms=until, **deepcopy(request['proposed_budget']))
    result['scope_enrollment'] = dict(schema='DEMO_GOLD_BUDGET_SCOPE_BINDING_V1', purpose=request['purpose'],
        request_sha256=request['request_sha256'], operator_approval_sha256=identity,
        original_registry_sha256=request['registry_sha256'], original_policy_sha256=request['current_policy_sha256'],
        original_profile_sha256=request['current_profile_sha256'], original_cycle_sha256=approval['original_cycle_sha256'],
        original_cycle_review_sha256=_digest(request['original_cycle_review']),
        full_state_sha256=request['original_cycle_review']['full_state_sha256'], runtime_sha256=request['runtime_sha256'],
        strategy_definition_sha256=request['strategy_definition_sha256'], stop_image_sha256=hashlib.sha256(stop_image).hexdigest(), confirmed_at_ms=at,
        duration_seconds=scope_contract.DAY_SECONDS, installation_allowance_seconds=scope_contract.INSTALLATION_ALLOWANCE_SECONDS,
        installation_deadline_ms=at+scope_contract.INSTALLATION_ALLOWANCE_SECONDS*1000,
        pilot_max_loss_fraction=request['pilot_max_loss_fraction'], execution_approval_granted=False,
        pilot_installation_verified=False, submission_enabled=False, economic_authority=False)
    return result


def validate_documents(documents, *, registry, existing_registry, source_commit, account_ref, now_ms, stop_image):
    request = documents['request.json'];approval = documents['operator-approval.json']
    original_policy = documents['original-policy.json'];original_profile = documents['original-profile.json']
    origin = documents['original-origin.json'];scope = documents['policy.json'];profile = documents['profile.json']
    key = scope_contract.current_scope_key(scope, registry, now_ms)
    # Recheck immutable evidence at its signed confirmation time. Installing
    # later still needs a fresh original-store review and the fixed start gate.
    confirmed = scope['scope_enrollment']['confirmed_at_ms']
    _inputs(request, review=request['original_cycle_review'], origin=origin, registry=existing_registry,
        policy=original_policy, profile=original_profile, definition=request['definition'], runtime=request['runtime'], now_ms=confirmed)
    _approval(approval, request, confirmed)
    require(request['source_commit'] == source_commit and request['account_ref_sha256'] == account_ref,
        'GOLD_SCOPE_SOURCE_OR_ACCOUNT')
    require(all(key['valid_until_ms'] <= approval['approved_at_ms'] for key in existing_registry['keys']),
        'GOLD_SCOPE_OLD_KEY_STILL_VALID')
    expected_profile = _profile(request, approval, original_profile, origin, confirmed)
    expected_scope = _scope(request, approval, registry, expected_profile, original_policy, stop_image)
    require(_digest(profile) == _digest(expected_profile)
        and _digest({k:v for k,v in scope.items() if k != 'signature_base64url'}) == _digest(expected_scope),
        'GOLD_SCOPE_ORIGINAL_BINDING')
    profile_declarations(profile, scope_contract._declaration_view(scope),
        {'symbol':'XAUUSDT', 'source_sha256':market_source_hash()})
    return key


@ledger_decimal_context()
def assemble(*, request, approval, review, origin, registry, policy, profile, definition, runtime,
             private_key, stop_image, now_ms):
    _inputs(request, review=review, origin=origin, registry=registry, policy=policy, profile=profile,
        definition=definition, runtime=runtime, now_ms=now_ms)
    _approval(approval, request, now_ms)
    require(all(key['valid_until_ms'] <= approval['approved_at_ms'] for key in registry['keys']),
        'GOLD_SCOPE_OLD_KEY_STILL_VALID')
    require(isinstance(private_key, Ed25519PrivateKey), 'GOLD_SCOPE_ED25519_KEY_REQUIRED')
    setup.verified_stop_snapshot(stop_image, now_ms=now_ms)
    at = approval['approved_at_ms'];identity = _digest(approval);public = private_key.public_key().public_bytes_raw()
    expanded = deepcopy(registry)
    expanded['keys'].append(dict(authority_id='gold-scope-operator-'+identity[:16], key_id='gold-day-key-'+identity[:16],
        public_key_base64url=setup.encoded(public), public_key_sha256=hashlib.sha256(public).hexdigest(), status='ACTIVE',
        valid_from_ms=at, valid_until_ms=at+(scope_contract.DAY_SECONDS+scope_contract.INSTALLATION_ALLOWANCE_SECONDS)*1000))
    proposed_profile = _profile(request, approval, profile, origin, approval['approved_at_ms'])
    scope = _scope(request, approval, expanded, proposed_profile, policy, stop_image)
    scope['signature_base64url'] = setup.encoded(private_key.sign(scope_contract.signed_bytes(scope)))
    documents = {'registry.json':expanded, 'policy.json':scope, 'profile.json':proposed_profile,
        'request.json':deepcopy(request), 'operator-approval.json':deepcopy(approval),
        'original-policy.json':deepcopy(policy), 'original-profile.json':deepcopy(profile), 'original-origin.json':deepcopy(origin),
        'signing-key.json':dict(schema='DEMO_ISSUER_KEY_V1', authority_id=expanded['keys'][-1]['authority_id'],
            key_id=expanded['keys'][-1]['key_id'], private_key_base64url=setup.encoded(private_key.private_bytes_raw()))}
    files = {name:setup.canonical(value) for name,value in documents.items()};files['kill-switch.sqlite3'] = stop_image
    require(all(len(body) <= (64*1024*1024 if name == 'kill-switch.sqlite3' else 65536)
        for name,body in files.items()), 'GOLD_SCOPE_ORIGINAL_READER_SIZE_LIMIT')
    manifest = dict(schema=scope_contract.BUNDLE_SCHEMA, source_commit=request['source_commit'],
        expected_registry_sha256=registry_sha256(registry), files={name:hashlib.sha256(body).hexdigest()
            for name,body in files.items()}, tasks=[])
    from tools.demo_operating_bundle import validate_materials
    validate_materials(manifest=manifest, files=files, existing_registry=registry,
        source_commit=request['source_commit'], account_ref=request['account_ref_sha256'], now_ms=now_ms)
    return manifest, files
