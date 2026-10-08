"""Pure historical Scope installation ancestry; no private reader or signer.

The original Scope signature authenticates its bindings. Installation records
also require the caller's fixed, independently protected public custody. This
helper neither grants execution nor decides whether authorization is current.
"""
import hashlib
import json
from copy import deepcopy

from live.execution import demo_gold_budget_scope as contract
from live.execution.demo_batch_risk import _fields
from live.execution.demo_issuer_policy import require, validate_policy
from services.validation.demo_submission import _digest as check_digest, validate_registry, registry_sha256

SCHEMA = 'DEMO_GOLD_SCOPE_INSTALLATION_ANCESTRY_V1'
SCHEMA_V2 = 'DEMO_GOLD_FIRST_SCOPE_INSTALLATION_ANCESTRY_V1'
PUBLIC_DOCUMENTS = frozenset(('registry.json', 'policy.json', 'profile.json', 'request.json',
    'operator-approval.json', 'original-policy.json', 'original-profile.json'))
SEEDS = frozenset(('spec.json','operator-enrollment.json','registry-preimage.json','assembly.json',
    'seed-signing-key.json','seed-stop.sqlite3'))
BUNDLE_FILES = PUBLIC_DOCUMENTS | {'original-origin.json','signing-key.json','kill-switch.sqlite3'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _result(value, *, source_commit, status):
    _fields(value, 'schema status spec_sha256 enrollment_sha256 source_commit task_sha256 orders_submitted economic_authority')
    require(value['schema']=='DEMO_OPERATOR_SETUP_RESULT_V1' and value['source_commit']==source_commit
        and value['status']==status and value['orders_submitted'] is False
        and value['economic_authority'] is False, 'GOLD_SCOPE_ANCESTRY_RESULT')
    for key in ('spec_sha256','enrollment_sha256'): check_digest(value[key])


def validate_scope_ancestry(records, *, scope, registry, source_commit):
    first=type(scope) is dict and scope.get('schema')==contract.SCHEMA_V2
    _fields(records, 'schema active_pointer activation result spec intent manifest operator_enrollment '
        'registry_preimage original_setup public_documents')
    require(records['schema']==(SCHEMA_V2 if first else SCHEMA)
        and len(canonical(records))<=131072, 'GOLD_SCOPE_ANCESTRY_SCHEMA')
    contract.historical_scope_binding(scope, registry)
    require(scope['executor_release_commit']==source_commit, 'GOLD_SCOPE_ANCESTRY_SOURCE')
    signed=scope['scope_enrollment']; docs=records['public_documents']
    require(type(docs) is dict and set(docs)==PUBLIC_DOCUMENTS, 'GOLD_SCOPE_ANCESTRY_PUBLIC_SCOPE')
    require(docs['policy.json']==scope and docs['registry.json']==registry, 'GOLD_SCOPE_ANCESTRY_ORIGINAL_SCOPE')
    manifest=records['manifest']
    _fields(manifest, 'schema source_commit expected_registry_sha256 files tasks')
    require(manifest['schema']==(contract.BUNDLE_SCHEMA_V2 if first else contract.BUNDLE_SCHEMA)
        and manifest['source_commit']==source_commit
        and manifest['tasks']==[] and type(manifest['files']) is dict and set(manifest['files'])==BUNDLE_FILES,
        'GOLD_SCOPE_ANCESTRY_MANIFEST')
    for value in manifest['files'].values(): check_digest(value)
    require(all(digest(value)==manifest['files'][name] for name,value in docs.items()), 'GOLD_SCOPE_ANCESTRY_BUNDLE_CHANGED')
    prior=validate_registry(records['registry_preimage'])
    require(registry=={**prior,'keys':prior['keys']+[registry['keys'][-1]]}
        and registry_sha256(prior)==signed['original_registry_sha256']==manifest['expected_registry_sha256'],
        'GOLD_SCOPE_ANCESTRY_REGISTRY')
    original_policy=validate_policy(docs['original-policy.json'])
    require(digest(original_policy)==signed['original_policy_sha256']
        and digest(docs['original-profile.json'])==signed['original_profile_sha256'], 'GOLD_SCOPE_ANCESTRY_PRIOR')
    _profile_binding(docs['profile.json'],scope)
    approval=records['operator_enrollment']; request=docs['request.json']; identity=digest(approval)
    require(approval==docs['operator-approval.json'] and identity==signed['operator_approval_sha256']
        and approval['request_sha256']==request['request_sha256']==signed['request_sha256']
        and digest({k:v for k,v in request.items() if k!='request_sha256'})==request['request_sha256']
        and approval['original_cycle_sha256']==signed['original_cycle_sha256']
        and approval['approved_at_ms']==signed['confirmed_at_ms']
        and approval['execution_approval_granted'] is False and approval['economic_authority'] is False,
        'GOLD_SCOPE_ANCESTRY_CONFIRMATION')
    require(request['source_commit']==source_commit and request['account_ref_sha256']==scope['account_ref_sha256']
        and digest(request['runtime'])==signed['runtime_sha256']
        and request['definition']['definition_hash']==signed['strategy_definition_sha256']
        and digest(request['original_cycle_review'])==signed['original_cycle_review_sha256']
        and request['original_cycle_review']['legacy_state_sha256' if first else 'full_state_sha256']==signed['full_state_sha256'],
        'GOLD_SCOPE_ANCESTRY_REQUEST')
    spec=records['spec']; intent=records['intent']; result=records['result']
    _fields(spec, 'schema source_commit account_ref_sha256 request_sha256 original_cycle_sha256 '
        'original_spec_sha256 orders_submitted economic_authority'+(' original_source_commit original_setup_sha256' if first else ''))
    _fields(intent, 'schema source_commit enrollment_sha256 spec_sha256 original_cycle_sha256 '
        'seed_files manifest_sha256 orders_submitted economic_authority')
    require(spec['schema']==('DEMO_GOLD_FIRST_SCOPE_INSTALL_SPEC_V1' if first else 'DEMO_GOLD_BUDGET_SCOPE_INSTALL_SPEC_V1')
        and intent['schema']==('DEMO_GOLD_FIRST_SCOPE_INSTALL_INTENT_V1' if first else 'DEMO_GOLD_BUDGET_SCOPE_INSTALL_INTENT_V1')
        and spec['source_commit']==intent['source_commit']==source_commit
        and spec['account_ref_sha256']==scope['account_ref_sha256']
        and spec['request_sha256']==signed['request_sha256']
        and spec['original_cycle_sha256']==intent['original_cycle_sha256']==signed['original_cycle_sha256']
        and intent['enrollment_sha256']==identity and intent['spec_sha256']==digest(spec)
        and intent['manifest_sha256']==digest(manifest)
        and all(value[name] is False for value in (spec,intent) for name in ('orders_submitted','economic_authority')),
        'GOLD_SCOPE_ANCESTRY_INTENT')
    require(type(intent['seed_files']) is dict and set(intent['seed_files'])==SEEDS, 'GOLD_SCOPE_ANCESTRY_SEED_HASHES')
    for value in intent['seed_files'].values(): check_digest(value)
    require(intent['seed_files']['spec.json']==digest(spec)
        and intent['seed_files']['operator-enrollment.json']==identity
        and intent['seed_files']['registry-preimage.json']==digest(prior)
        and intent['seed_files']['seed-stop.sqlite3']==signed['stop_image_sha256']==manifest['files']['kill-switch.sqlite3']
        and intent['seed_files']['seed-signing-key.json']==manifest['files']['signing-key.json'],
        'GOLD_SCOPE_ANCESTRY_RETAINED_HASHES')
    _result(result, source_commit=source_commit, status='INSTALLED_SCOPE_ONLY')
    require(result['task_sha256'] is None and result['spec_sha256']==digest(spec)
        and result['enrollment_sha256']==identity
        and records['activation']==dict(schema='DEMO_PENDING_ENROLLMENT_V1',enrollment_sha256=identity)
        and records['active_pointer']==dict(schema='DEMO_ACTIVE_ENROLLMENT_V1',enrollment_sha256=identity,result_sha256=digest(result)),
        'GOLD_SCOPE_ANCESTRY_ACTIVATION')
    old=records['original_setup']; _fields(old, 'result spec')
    _result(old['result'], source_commit=signed['original_source_commit'] if first else source_commit,
        status='INSTALLED_PENDING_CYCLE')
    require(digest(old['result'])==signed['original_cycle_sha256']
        and digest(old['spec'])==old['result']['spec_sha256']==spec['original_spec_sha256'], 'GOLD_SCOPE_ANCESTRY_ORIGINAL_SETUP')
    if first:_first_scope_binding(records, scope=scope, prior=prior)
    return dict(scope_sha256=digest(scope), original_cycle_sha256=signed['original_cycle_sha256'],
        original_spec_sha256=spec['original_spec_sha256'], enrollment_sha256=identity, execution_approval_granted=False)


def _first_scope_binding(records, *, scope, prior):
    """Pure first-market ancestry; independently fixed custody remains required."""
    from live.execution import demo_gold_first_scope as first_contract
    from live.execution.demo_issuer_policy import historical_policy_binding
    from live.execution.demo_market_partition import _candidate
    from live.execution.demo_market_partition_runtime import _FIRST_GOLD_REVIEW_FIELDS
    from live.execution.demo_request_inputs import profile_declarations
    signed=scope['scope_enrollment'];docs=records['public_documents'];request=docs['request.json']
    approval=records['operator_enrollment'];at=signed['confirmed_at_ms'];old=records['original_setup']
    first_contract.validate_request(request)
    first_contract.validate_approval(approval, request=request, now_ms=at)
    policy=docs['original-policy.json'];profile=docs['original-profile.json']
    historical_policy_binding(policy, prior)
    require(policy['schema']=='DEMO_ISSUER_POLICY_V1' and policy['scope']['symbol']=='BTCUSDC'
        and policy['executor_release_commit']==signed['original_source_commit']
        and policy['account_ref_sha256']==scope['account_ref_sha256']
        and policy['valid_until_ms']<=at, 'FIRST_SCOPE_ANCESTRY_ORIGINAL_POLICY')
    profile_declarations(profile, policy, {'symbol':'BTCUSDC', 'source_sha256':profile['market_source_sha256']})
    require(old==request['original_setup'] and digest(old)==signed['original_setup_sha256']
        and records['spec']['original_setup_sha256']==signed['original_setup_sha256']
        and records['spec']['original_source_commit']==signed['original_source_commit']
        and old['spec']['schema']=='DEMO_FUNCTIONAL_SETUP_SPEC_V1' and old['spec']['symbol']=='BTCUSDC'
        and old['spec']['source_commit']==signed['original_source_commit']
        and old['spec']['account_ref_sha256']==scope['account_ref_sha256'], 'FIRST_SCOPE_ANCESTRY_ORIGINAL_SETUP')
    check_digest(old['result']['task_sha256'])
    for name in ('original_source_commit','original_setup_sha256','original_spec_sha256','original_cycle_sha256',
                 'target_declarations_sha256','stop_image_sha256','original_issuer_history'):
        require(request[name]==signed[name], 'FIRST_SCOPE_ANCESTRY_SIGNED_REQUEST')
    require(request['registry_sha256']==registry_sha256(prior)
        and request['current_policy_sha256']==digest(policy)
        and request['current_profile_sha256']==digest(profile), 'FIRST_SCOPE_ANCESTRY_ORIGINAL_DECLARATIONS')
    require(digest(first_contract.declared_profile(request, approval))==digest(docs['profile.json']),
        'FIRST_SCOPE_ANCESTRY_TARGET_PROFILE')
    terms=request['target_declarations'];identity=digest(approval)
    require(digest(scope['scope'])==digest(terms['scope'])
        and digest({name:scope[name] for name in contract.BUDGET})==digest(request['proposed_budget'])
        and scope['grant_ttl_ms']==terms['grant_ttl_ms']
        and scope['policy_id']=='gold-first-scope-'+identity[:16]
        and scope['budget_group']['group_id']=='gold-day-'+identity[:16]
        and scope['authority_id']=='gold-scope-operator-'+identity[:16]
        and scope['key_id']=='gold-day-key-'+identity[:16], 'FIRST_SCOPE_ANCESTRY_CONFIRMED_TERMS')
    review=request['original_cycle_review'];_fields(review, ' '.join(_FIRST_GOLD_REVIEW_FIELDS))
    require(review['schema']=='DEMO_FIRST_GOLD_ENROLLMENT_REVIEW_V1'
        and review['status']=='FIRST_GOLD_ENROLLMENT_REVIEW_REQUIRED'
        and review['source_commit']==scope['executor_release_commit']
        and review['account_ref_sha256']==scope['account_ref_sha256']
        and review['registry_sha256']==registry_sha256(prior)
        and review['gold_position_context'] is None and review['partition_installed'] is True
        and review['collateral_conversion']=='NOT_PERFORMED'
        and all(review[name] is False for name in ('gold_enrollment_installed','approval_granted',
            'source_verified','submission_enabled','economic_authority')), 'FIRST_SCOPE_ANCESTRY_REVIEW')
    original=review['original_candidate'];cut=_candidate(original, at);binding=review['partition_binding']
    _fields(binding,'schema source_commit installed_record_sha256 candidate_sha256 binding_sha256')
    require(binding['schema']=='DEMO_MARKET_PARTITION_RUNTIME_BINDING_V1'
        and binding['source_commit']==cut['source_commit']==scope['executor_release_commit']
        and binding['candidate_sha256']==digest(original) and binding['binding_sha256']==original['binding_sha256']
        and digest(binding)==signed['partition_binding_sha256']
        and cut['registry_sha256']==registry_sha256(prior) and cut['account_ref_sha256']==scope['account_ref_sha256']
        and cut['legacy_state_sha256']==review['legacy_state_sha256']==signed['full_state_sha256']
        and cut['legacy_context_sha256']==review['legacy_context_sha256']
        and cut['kill_switch_sha256']==signed['stop_image_sha256']
        and cut['gold_initial_wallet']==review['sealed_usdt_wallet']==review['observed_usdt_wallet'],
        'FIRST_SCOPE_ANCESTRY_ORIGINAL_CUT')
    check_digest(binding['installed_record_sha256'])
    require(docs['request.json']['original_cycle_review']['origin_sha256']==records['manifest']['files']['original-origin.json'],
        'FIRST_SCOPE_ANCESTRY_ORIGINAL_ORIGIN')
    old_risk=original['review']['legacy_book']
    fields=set('canonical_wallet initial_equity initial_day_pnl initial_plan_turnover peak_equity_floor '
        'capacity_consumed day_id cash_flow_policy quantity'.split())
    fields|={name for name in ('day_checkpoint_sha256','day_open_equity') if name in old_risk}
    require(type(review['legacy_risk']) is dict and set(review['legacy_risk'])==fields
        and digest(review['legacy_risk'])==digest({name:old_risk[name] for name in fields})
        and review['legacy_risk']['quantity']=='0', 'FIRST_SCOPE_ANCESTRY_LEGACY_RISK')
    for name in ('original_cut_at_ms','reviewed_at_ms','valid_until_ms'):
        require(type(review[name]) is int, 'FIRST_SCOPE_ANCESTRY_REVIEW_TIME')
    require(cut['recorded_at_ms']==review['original_cut_at_ms']<=review['reviewed_at_ms']
        <=request['reviewed_at_ms']<=at<request['valid_until_ms']==review['valid_until_ms']<=review['reviewed_at_ms']+30000,
        'FIRST_SCOPE_ANCESTRY_REVIEW_TIME')


def _profile_binding(profile, authorization):
    expected={'mandate_sha256':'mandate','validation_sha256':'attestations',
        'native_limits_sha256':'native_limits','strategy_approval_sha256':'strategy'}
    require(all(digest(profile[value])==authorization['bindings'][key] for key,value in expected.items())
        and profile['strategy']['strategy_id']==authorization['bindings']['strategy_id'], 'GOLD_SCOPE_ANCESTRY_PROFILE')


def _scope_budget_installation(records, *, authorization, registry, source_commit):
    """Historical root-confirmed exact policy, descended from the signed Scope.

    The old signal is checked at its recorded approval, never reauthorized at
    the current clock. This supplies ancestry only; the issuer still replays
    actual consumed and unsigned group reservations for migration.
    """
    from live.execution.demo_issuer_policy import (
        GOLD_STRATEGY_BUDGET_POLICY_SCHEMA, GOLD_STRATEGY_BUDGET_POLICY_SCHEMA_V2, budget_group_binding)
    from quantos.contracts.killswitch import KillSwitchState
    _fields(records,'schema result spec scope_ancestry policy_installation')
    require(records['schema']=='DEMO_GOLD_SCOPE_BUDGET_INSTALLATION_ANCESTRY_V1'
        and len(canonical(records))<=131072,'GOLD_SCOPE_BUDGET_ANCESTRY_SCHEMA')
    original=records['scope_ancestry'];scope=original['public_documents']['policy.json']
    verified=validate_scope_ancestry(original,scope=scope,registry=registry,source_commit=source_commit)
    first=scope['schema']==contract.SCHEMA_V2
    expected_schema=GOLD_STRATEGY_BUDGET_POLICY_SCHEMA_V2 if first else GOLD_STRATEGY_BUDGET_POLICY_SCHEMA
    require(authorization['schema']==expected_schema and (not first or authorization['original_scope']==scope),
        'GOLD_SCOPE_BUDGET_ORIGINAL_SCOPE')
    require(records['result']==original['result'] and records['spec']==original['spec'],
        'GOLD_SCOPE_BUDGET_ACTIVE_SETUP')
    install=records['policy_installation']
    _fields(install,'stage_sha256 previous_policy candidate operator_approval result previous_dispatcher dispatcher_candidate')
    previous=install['previous_policy'];candidate=install['candidate'];approval=install['operator_approval'];result=install['result']
    scoped=contract.is_scope(previous)
    if scoped:require(previous==scope,'GOLD_SCOPE_BUDGET_PREVIOUS_SCOPE')
    else:
        validate_policy(previous)
        require(previous['schema']==expected_schema and (not first or previous['original_scope']==scope),
            'GOLD_SCOPE_BUDGET_PREVIOUS_POLICY')
    def scope_projection(value):
        out={key:deepcopy(value[key]) for key in contract.COMMON if key not in ('schema','policy_id')}
        out['bindings'].pop('execution_constraints_sha256',None)
        return out|{'budget_group':value['budget_group']}
    require(scope_projection(previous)==scope_projection(scope)
        and scope_projection(authorization)==scope_projection(scope),'GOLD_SCOPE_BUDGET_PERMISSION_CHANGED')
    fields=set(('schema status request_sha256 result_sha256 replaces_policy_sha256 policy_candidate '
        'policy_candidate_sha256 budget_group_binding_sha256 decision_valid_until_ms approval_granted '
        'submission_enabled economic_authority stored_request_sha256').split())
    if scoped:fields.add('dispatcher_configuration')
    require(type(candidate) is dict and set(candidate) in (fields,fields|{'bar_prefix_review'})
        and candidate['schema']==('GOLD_STRATEGY_POLICY_CANDIDATE_V2' if scoped else 'GOLD_STRATEGY_POLICY_CANDIDATE_V1')
        and candidate['status']=='POLICY_CANDIDATE_REQUIRES_INDEPENDENT_APPROVAL'
        and all(candidate[key] is False for key in ('approval_granted','submission_enabled','economic_authority')),
        'GOLD_SCOPE_BUDGET_CANDIDATE')
    for name in ('request_sha256','result_sha256','stored_request_sha256','policy_candidate_sha256',
        'replaces_policy_sha256','budget_group_binding_sha256'):check_digest(candidate[name])
    proposed={key:deepcopy(previous[key]) for key in contract.COMMON} if scoped else deepcopy(previous)
    proposed.update(schema=expected_schema,budget_group=deepcopy(scope['budget_group']),
        policy_id='gold-decision-'+candidate['request_sha256'])
    if first:proposed['original_scope']=deepcopy(scope)
    proposed['bindings']['execution_constraints_sha256']=authorization['bindings']['execution_constraints_sha256']
    require(candidate['policy_candidate']==authorization==proposed
        and candidate['policy_candidate_sha256']==digest(authorization)
        and candidate['replaces_policy_sha256']==digest(previous)
        and candidate['budget_group_binding_sha256']==contract.budget_binding(scope)==budget_group_binding(authorization)
        and install['stage_sha256']==digest(candidate),'GOLD_SCOPE_BUDGET_INSTALL_BINDING')
    _fields(approval,'schema candidate_sha256 operator_id approved_at_ms economic_authority')
    require(approval['schema']=='GOLD_STRATEGY_LOCAL_OPERATOR_APPROVAL_V1'
        and approval['candidate_sha256']==digest(candidate) and approval['economic_authority'] is False
        and type(approval['operator_id']) is str and 0<len(approval['operator_id'])<=128
        and type(approval['approved_at_ms']) is int and type(candidate['decision_valid_until_ms']) is int
        and scope['valid_from_ms']<=approval['approved_at_ms']<candidate['decision_valid_until_ms']<=scope['valid_until_ms'],
        'GOLD_SCOPE_BUDGET_INDEPENDENT_CONFIRMATION')
    require(result==dict(schema='GOLD_STRATEGY_POLICY_INSTALLATION_V1',status='POLICY_INSTALLED_REQUIRES_ORIGINAL_GATES',
        source_commit=source_commit,stored_request_sha256=candidate['stored_request_sha256'],candidate_sha256=digest(candidate),
        previous_policy_sha256=digest(previous),installed_policy_sha256=digest(authorization),
        budget_group_binding_sha256=contract.budget_binding(scope),operator_approval_sha256=digest(approval),
        installed=True,orders_submitted=False,economic_authority=False),'GOLD_SCOPE_BUDGET_INSTALL_RESULT')
    if scoped:
        dispatch=install['dispatcher_candidate']
        require(dispatch==candidate['dispatcher_configuration'],'GOLD_SCOPE_BUDGET_DISPATCH')
        _fields(dispatch,'schema source_commit account_ref_sha256 budget_group_binding_sha256 kill_switch')
        require(dispatch['schema']=='GOLD_STRATEGY_APPROVED_POLICY_DISPATCH_V1'
            and dispatch['source_commit']==source_commit and dispatch['account_ref_sha256']==scope['account_ref_sha256']
            and dispatch['budget_group_binding_sha256']==contract.budget_binding(scope)
            and (install['previous_dispatcher'] is None or install['previous_dispatcher']==dispatch),
            'GOLD_SCOPE_BUDGET_DISPATCH')
        require(type(dispatch['kill_switch']) is dict and set(dispatch['kill_switch'])==set(KillSwitchState.model_fields),
            'GOLD_SCOPE_BUDGET_STOP')
        stop=KillSwitchState.model_validate_json(json.dumps(dispatch['kill_switch']),strict=True)
        require(not stop.tripped and max(stop.armed_at,stop.reset_at)<=approval['approved_at_ms'],'GOLD_SCOPE_BUDGET_STOP')
    else:require(install['previous_dispatcher'] is None and install['dispatcher_candidate'] is None,'GOLD_SCOPE_BUDGET_DISPATCH')
    return verified|{'exact_policy_installation_sha256':digest(result)}


def validate_authorization_ancestry(records, *, authorization, registry, source_commit):
    if contract.is_scope(authorization):
        return validate_scope_ancestry(records,scope=authorization,registry=registry,source_commit=source_commit)
    from live.execution.demo_issuer_policy import (
        GOLD_BUDGET_POLICY_SCHEMAS, GOLD_STRATEGY_BUDGET_POLICY_SCHEMA_V2, historical_policy_binding)
    validate_policy(authorization)
    require(authorization['schema'] in GOLD_BUDGET_POLICY_SCHEMAS
        and authorization['executor_release_commit']==source_commit, 'GOLD_BUDGET_ANCESTRY_POLICY')
    historical_policy_binding(authorization,registry)
    if records.get('schema')=='DEMO_GOLD_SCOPE_BUDGET_INSTALLATION_ANCESTRY_V1':
        return _scope_budget_installation(records,authorization=authorization,registry=registry,source_commit=source_commit)
    require(authorization['schema']!=GOLD_STRATEGY_BUDGET_POLICY_SCHEMA_V2,'GOLD_SCOPE_BUDGET_ANCESTRY_REQUIRED')
    _fields(records,'schema active_pointer activation result spec policy profile')
    require(records['schema']=='DEMO_GOLD_BUDGET_INSTALLATION_ANCESTRY_V1'
        and records['policy']==authorization, 'GOLD_BUDGET_ANCESTRY_SCHEMA')
    result=records['result'];spec=records['spec']
    _result(result,source_commit=source_commit,status='INSTALLED_PENDING_CYCLE')
    require(digest(spec)==result['spec_sha256'] and spec['source_commit']==source_commit
        and spec['account_ref_sha256']==authorization['account_ref_sha256'] and spec['symbol']=='XAUUSDT',
        'GOLD_BUDGET_ANCESTRY_SETUP')
    if records['active_pointer'] is None:
        require(records['activation'] is None,'GOLD_BUDGET_ANCESTRY_ACTIVATION')
    else:
        require(records['active_pointer']==dict(schema='DEMO_ACTIVE_ENROLLMENT_V1',
            enrollment_sha256=result['enrollment_sha256'],result_sha256=digest(result))
            and records['activation']==dict(schema='DEMO_PENDING_ENROLLMENT_V1',enrollment_sha256=result['enrollment_sha256']),
            'GOLD_BUDGET_ANCESTRY_ACTIVATION')
    _profile_binding(records['profile'],authorization)
    return dict(original_cycle_sha256=digest(result),original_spec_sha256=digest(spec),
        enrollment_sha256=result['enrollment_sha256'],execution_approval_granted=False)
