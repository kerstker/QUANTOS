"""Compile an independently confirmed first Gold Scope, never install or submit.

Original protected reads, source acceptance, operator identity and original
issuer/executor continuity belong to the installer. Every supplied byte is bound
here; the original first-market and archived strategy validators remain active.
"""
from copy import deepcopy
import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from live.execution import demo_gold_budget_scope as scope_contract, demo_gold_first_scope as contract
from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import historical_policy_binding, require
from live.execution.demo_market_partition_runtime import validate_first_gold_review
from live.execution.demo_request_inputs import profile_declarations
from services.execution.perpetual_ledger_v2 import ledger_decimal_context
from services.validation import demo_submission as submission
from tools import demo_operator_setup as setup
from tools.binance_demo_market_inputs import source_hash as market_source_hash
from tools.gold_strategy_budget_enrollment import runtime_check, verify_strategy_parameters

SCOPE_FILES = {'request.json', 'operator-approval.json', 'original-policy.json',
    'original-profile.json', 'original-origin.json'}
DOCUMENTS = SCOPE_FILES | {'registry.json', 'policy.json', 'profile.json', 'signing-key.json'}


def _original_setup(original_setup, policy):
    _fields(original_setup, 'spec result')
    spec=setup.spec_check(original_setup['spec']);result=original_setup['result']
    _fields(result, 'schema status source_commit spec_sha256 enrollment_sha256 task_sha256 orders_submitted economic_authority')
    require(spec['schema']==setup.SCHEMA and spec['symbol']=='BTCUSDC'
        and result['schema']=='DEMO_OPERATOR_SETUP_RESULT_V1' and result['status']=='INSTALLED_PENDING_CYCLE'
        and result['source_commit']==spec['source_commit']==policy['executor_release_commit']
        and spec['account_ref_sha256']==policy['account_ref_sha256']
        and _digest(spec)==result['spec_sha256'] and result['orders_submitted'] is False
        and result['economic_authority'] is False, 'FIRST_SCOPE_ORIGINAL_BTC_SETUP')
    for key in ('spec_sha256', 'enrollment_sha256', 'task_sha256'):submission._digest(result[key])


@ledger_decimal_context()
def prepare_first_scope(*, review, origin, registry, policy, profile, original_setup, issuer_history,
                        definition, runtime, source_commit, stop_sha256, now_ms):
    from live.execution.demo_gold_first_scope_history import validate_anchor
    submission._integer(now_ms);submission._digest(stop_sha256);validate_anchor(issuer_history)
    historical_policy_binding(policy, registry)
    require(policy['schema']=='DEMO_ISSUER_POLICY_V1' and policy['scope']['symbol']=='BTCUSDC',
        'FIRST_SCOPE_ORIGINAL_BTC_POLICY')
    _original_setup(original_setup, policy)
    # Historical BTC declarations retain their original observer identity; the
    # new Gold observer is independently declared below, never renamed in place.
    profile_declarations(profile, policy, {'symbol':'BTCUSDC', 'source_sha256':profile['market_source_sha256']})
    runtime_check(runtime, source_commit)
    validate_first_gold_review(review, origin=origin, registry=registry, source_commit=source_commit, as_of_ms=now_ms)
    require(policy['account_ref_sha256']==origin['account_ref_sha256']
        and review['original_candidate']['binding']['kill_switch_sha256']==stop_sha256,
        'FIRST_SCOPE_ACCOUNT_OR_STOP')
    terms=contract.target_declarations(account_ref=origin['account_ref_sha256'],
        market_source_sha256=market_source_hash(), wallet=review['sealed_usdt_wallet'])
    # Reuse the original parameter validator with only unsigned, already-expired
    # limits. This temporary projection has no approver, strategy or attestation,
    # is never retained and cannot pass an issuer's independent mandate gate.
    limits_only=dict(native_limits=terms['native_limits'], mandate=dict(terms['mandate_limits'],
        mandate_id='UNAPPROVED_FIRST_SCOPE_LIMITS_ONLY', approved_by=[], issued_at=0, expires_at=0))
    verify_strategy_parameters(origin=origin, profile=limits_only, definition=definition, runtime=runtime,
        source_commit=source_commit, scope=terms['scope'])
    request=dict(schema=contract.REQUEST_SCHEMA, status='INDEPENDENT_SCOPE_ENROLLMENT_REQUIRED', purpose=contract.PURPOSE,
        source_commit=source_commit, account_ref_sha256=origin['account_ref_sha256'], registry_sha256=submission.registry_sha256(registry),
        current_policy_sha256=_digest(policy), current_profile_sha256=_digest(profile),
        original_source_commit=policy['executor_release_commit'], original_setup_sha256=_digest(original_setup),
        original_setup=deepcopy(original_setup), original_issuer_history=deepcopy(issuer_history),
        original_cycle_sha256=_digest(original_setup['result']), original_spec_sha256=_digest(original_setup['spec']),
        reviewed_at_ms=now_ms, valid_until_ms=review['valid_until_ms'], original_cycle_review=deepcopy(review),
        runtime_sha256=_digest(runtime), runtime=deepcopy(runtime), strategy_definition_sha256=definition['definition_hash'],
        definition=deepcopy(definition), duration_seconds=scope_contract.DAY_SECONDS,
        installation_allowance_seconds=scope_contract.INSTALLATION_ALLOWANCE_SECONDS,
        proposed_budget=deepcopy(terms['budget']), target_declarations=terms, target_declarations_sha256=_digest(terms),
        stop_image_sha256=stop_sha256, pilot_max_loss_fraction='0.05', new_independent_mandate_required=True,
        exact_decision_approval_required=True, pilot_installation_verified=False, approval_granted=False,
        budget_enrolled=False, submission_enabled=False, economic_authority=False)
    return contract.validate_request(request | {'request_sha256':_digest(request)})


def _inputs(request, *, review, origin, registry, policy, profile, original_setup, issuer_history,
            definition, runtime, stop_image, now_ms):
    contract.validate_request(request);submission._integer(now_ms)
    require(request['reviewed_at_ms']<=now_ms<request['valid_until_ms'], 'FIRST_SCOPE_REQUEST_EXPIRED')
    expected=prepare_first_scope(review=review, origin=origin, registry=registry, policy=policy, profile=profile,
        original_setup=original_setup, issuer_history=issuer_history, definition=definition, runtime=runtime,
        source_commit=request['source_commit'], stop_sha256=hashlib.sha256(stop_image).hexdigest(),
        now_ms=request['reviewed_at_ms'])
    require(_digest(expected)==_digest(request), 'FIRST_SCOPE_REQUEST_CHANGED')
    validate_first_gold_review(review, origin=origin, registry=registry,
        source_commit=request['source_commit'], as_of_ms=now_ms)


def _old_expired(registry, policy, at):
    require(all(key['valid_until_ms']<=at for key in registry['keys'])
        and policy['valid_until_ms']<=at, 'FIRST_SCOPE_OLD_AUTHORITY_STILL_VALID')


def _scope(request, approval, registry, profile):
    identity=_digest(approval);at=approval['approved_at_ms'];key=registry['keys'][-1]
    until=at+(scope_contract.DAY_SECONDS+scope_contract.INSTALLATION_ALLOWANCE_SECONDS)*1000
    terms=request['target_declarations']
    value=dict(schema=scope_contract.SCHEMA_V2, environment='BINANCE_USDM_DEMO', policy_id='gold-first-scope-'+identity[:16],
        deployment_sha256=registry['deployment_sha256'], registry_sha256=submission.registry_sha256(registry),
        executor_release_commit=request['source_commit'], account_ref_sha256=request['account_ref_sha256'],
        authority_id=key['authority_id'], key_id=key['key_id'], valid_from_ms=at, valid_until_ms=until,
        grant_ttl_ms=terms['grant_ttl_ms'], **deepcopy(request['proposed_budget']), scope=deepcopy(terms['scope']),
        bindings=dict(strategy_id=profile['strategy']['strategy_id'], mandate_sha256=_digest(profile['mandate']),
            validation_sha256=_digest(profile['attestations']), native_limits_sha256=_digest(profile['native_limits']),
            strategy_approval_sha256=_digest(profile['strategy'])))
    value['budget_group']=dict(group_id='gold-day-'+identity[:16], strategy_definition_sha256=request['strategy_definition_sha256'],
        valid_from_ms=at, valid_until_ms=until, **deepcopy(request['proposed_budget']))
    value['scope_enrollment']=dict(schema=contract.BINDING_SCHEMA, purpose=contract.PURPOSE,
        request_sha256=request['request_sha256'], operator_approval_sha256=identity,
        original_registry_sha256=request['registry_sha256'], original_policy_sha256=request['current_policy_sha256'],
        original_profile_sha256=request['current_profile_sha256'], original_cycle_sha256=request['original_cycle_sha256'],
        original_cycle_review_sha256=_digest(request['original_cycle_review']),
        full_state_sha256=request['original_cycle_review']['legacy_state_sha256'], runtime_sha256=request['runtime_sha256'],
        strategy_definition_sha256=request['strategy_definition_sha256'], stop_image_sha256=request['stop_image_sha256'],
        confirmed_at_ms=at, duration_seconds=scope_contract.DAY_SECONDS,
        installation_allowance_seconds=scope_contract.INSTALLATION_ALLOWANCE_SECONDS,
        installation_deadline_ms=at+scope_contract.INSTALLATION_ALLOWANCE_SECONDS*1000,
        pilot_max_loss_fraction='0.05', execution_approval_granted=False, pilot_installation_verified=False,
        submission_enabled=False, economic_authority=False, original_source_commit=request['original_source_commit'],
        original_setup_sha256=request['original_setup_sha256'], original_spec_sha256=request['original_spec_sha256'],
        partition_binding_sha256=_digest(request['original_cycle_review']['partition_binding']),
        target_declarations_sha256=request['target_declarations_sha256'],
        original_issuer_history=deepcopy(request['original_issuer_history']))
    return value


def validate_documents(documents, *, registry, existing_registry, source_commit, account_ref, now_ms, stop_image):
    require(type(documents) is dict and set(documents)==DOCUMENTS, 'FIRST_SCOPE_DOCUMENTS')
    request=documents['request.json'];approval=documents['operator-approval.json'];scope=documents['policy.json']
    require(scope.get('schema')==scope_contract.SCHEMA_V2, 'FIRST_SCOPE_DOCUMENT_SCHEMA')
    require(documents['registry.json']==registry and registry=={**existing_registry,
        'keys':existing_registry['keys']+[registry['keys'][-1]]}, 'FIRST_SCOPE_REGISTRY_EXTENSION')
    key=scope_contract.current_scope_key(scope, registry, now_ms)
    at=scope['scope_enrollment']['confirmed_at_ms']
    _inputs(request, review=request['original_cycle_review'], origin=documents['original-origin.json'],
        registry=existing_registry, policy=documents['original-policy.json'], profile=documents['original-profile.json'],
        original_setup=request['original_setup'], issuer_history=request['original_issuer_history'],
        definition=request['definition'], runtime=request['runtime'], stop_image=stop_image, now_ms=at)
    contract.validate_approval(approval, request=request, now_ms=at)
    _old_expired(existing_registry, documents['original-policy.json'], at)
    require(request['source_commit']==source_commit and request['account_ref_sha256']==account_ref,
        'FIRST_SCOPE_SOURCE_OR_ACCOUNT')
    setup.verified_stop_snapshot(stop_image, now_ms=at)
    expected_profile=contract.declared_profile(request, approval);expected_scope=_scope(request, approval, registry, expected_profile)
    require(_digest(documents['profile.json'])==_digest(expected_profile)
        and _digest({k:v for k,v in scope.items() if k!='signature_base64url'})==_digest(expected_scope),
        'FIRST_SCOPE_ORIGINAL_BINDING')
    profile_declarations(expected_profile, scope_contract._declaration_view(scope),
        {'symbol':'XAUUSDT', 'source_sha256':market_source_hash()})
    return key


@ledger_decimal_context()
def assemble(*, request, approval, review, origin, registry, policy, profile, original_setup, issuer_history,
             definition, runtime, private_key, stop_image, now_ms):
    _inputs(request, review=review, origin=origin, registry=registry, policy=policy, profile=profile,
        original_setup=original_setup, issuer_history=issuer_history, definition=definition, runtime=runtime,
        stop_image=stop_image, now_ms=now_ms)
    contract.validate_approval(approval, request=request, now_ms=now_ms)
    _old_expired(registry, policy, approval['approved_at_ms'])
    require(isinstance(private_key, Ed25519PrivateKey), 'FIRST_SCOPE_ED25519_REQUIRED')
    setup.verified_stop_snapshot(stop_image, now_ms=now_ms)
    at=approval['approved_at_ms'];identity=_digest(approval);public=private_key.public_key().public_bytes_raw()
    expanded=deepcopy(registry)
    expanded['keys'].append(dict(authority_id='gold-scope-operator-'+identity[:16], key_id='gold-day-key-'+identity[:16],
        public_key_base64url=setup.encoded(public), public_key_sha256=hashlib.sha256(public).hexdigest(), status='ACTIVE',
        valid_from_ms=at, valid_until_ms=at+(scope_contract.DAY_SECONDS+scope_contract.INSTALLATION_ALLOWANCE_SECONDS)*1000))
    proposed_profile=contract.declared_profile(request, approval)
    scope=_scope(request, approval, expanded, proposed_profile)
    scope['signature_base64url']=setup.encoded(private_key.sign(scope_contract.signed_bytes(scope)))
    documents={'registry.json':expanded, 'policy.json':scope, 'profile.json':proposed_profile,
        'request.json':deepcopy(request), 'operator-approval.json':deepcopy(approval), 'original-policy.json':deepcopy(policy),
        'original-profile.json':deepcopy(profile), 'original-origin.json':deepcopy(origin),
        'signing-key.json':dict(schema='DEMO_ISSUER_KEY_V1', authority_id=expanded['keys'][-1]['authority_id'],
            key_id=expanded['keys'][-1]['key_id'], private_key_base64url=setup.encoded(private_key.private_bytes_raw()))}
    validate_documents(documents, registry=expanded, existing_registry=registry, source_commit=request['source_commit'],
        account_ref=request['account_ref_sha256'], now_ms=now_ms, stop_image=stop_image)
    files={name:setup.canonical(value) for name,value in documents.items()};files['kill-switch.sqlite3']=stop_image
    require(all(len(body)<=(64*1024*1024 if name=='kill-switch.sqlite3' else 65536) for name,body in files.items()),
        'FIRST_SCOPE_ORIGINAL_READER_SIZE_LIMIT')
    manifest=dict(schema=scope_contract.BUNDLE_SCHEMA_V2, source_commit=request['source_commit'],
        expected_registry_sha256=submission.registry_sha256(registry), files={name:hashlib.sha256(raw).hexdigest()
            for name,raw in files.items()}, tasks=[])
    return manifest, files
