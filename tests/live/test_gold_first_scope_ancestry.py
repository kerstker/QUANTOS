"""Strict public ancestry over a genuinely compiled first-Scope signature.

Installation metadata is an explicit pure-contract fixture here. The independent
installer integration separately supplies actual retained stage and CAS outputs.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from live.execution import demo_gold_scope_ancestry as ancestry
from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_first_scope_materials import (book as book, first_scope_archive as first_scope_archive,
    original_issuer as original_issuer, first_scope_inputs as first_scope_inputs, assembled, PARENT)
from tools.demo_operator_setup import canonical

SCHEMA = 'DEMO_GOLD_FIRST_SCOPE_INSTALLATION_ANCESTRY_V1'


def records_for_materials(args, manifest, files):
    docs={name:json.loads(files[name]) for name in ancestry.PUBLIC_DOCUMENTS}
    scope=docs['policy.json'];request=args['request'];approval=args['approval'];identity=_digest(approval)
    spec=dict(schema='DEMO_GOLD_FIRST_SCOPE_INSTALL_SPEC_V1', source_commit=PARENT,
        account_ref_sha256=request['account_ref_sha256'], request_sha256=request['request_sha256'],
        original_cycle_sha256=request['original_cycle_sha256'], original_spec_sha256=request['original_spec_sha256'],
        original_source_commit=request['original_source_commit'], original_setup_sha256=request['original_setup_sha256'],
        orders_submitted=False, economic_authority=False)
    assembly=dict(schema='DEMO_GOLD_FIRST_SCOPE_ASSEMBLY_INPUTS_V1', assembled_at_ms=args['now_ms'],
        inputs={key:args[key] for key in ('request','review','origin','registry','policy','profile',
            'original_setup','definition','runtime','issuer_history')})
    seeds={'spec.json':canonical(spec), 'operator-enrollment.json':canonical(approval),
        'registry-preimage.json':canonical(args['registry']), 'assembly.json':canonical(assembly),
        'seed-signing-key.json':files['signing-key.json'], 'seed-stop.sqlite3':args['stop_image']}
    intent=dict(schema='DEMO_GOLD_FIRST_SCOPE_INSTALL_INTENT_V1', source_commit=PARENT,
        enrollment_sha256=identity, spec_sha256=_digest(spec), original_cycle_sha256=request['original_cycle_sha256'],
        seed_files={name:hashlib.sha256(raw).hexdigest() for name,raw in seeds.items()},
        manifest_sha256=_digest(manifest), orders_submitted=False, economic_authority=False)
    result=dict(schema='DEMO_OPERATOR_SETUP_RESULT_V1', status='INSTALLED_SCOPE_ONLY', source_commit=PARENT,
        spec_sha256=_digest(spec), enrollment_sha256=identity, task_sha256=None, orders_submitted=False, economic_authority=False)
    records=dict(schema=SCHEMA, active_pointer=dict(schema='DEMO_ACTIVE_ENROLLMENT_V1',
        enrollment_sha256=identity, result_sha256=_digest(result)),
        activation=dict(schema='DEMO_PENDING_ENROLLMENT_V1', enrollment_sha256=identity),
        result=result, spec=spec, intent=intent, manifest=manifest, operator_enrollment=approval,
        registry_preimage=args['registry'], original_setup=args['original_setup'], public_documents=docs)
    return records, scope, docs['registry.json']


@pytest.fixture
def first_ancestry(first_scope_inputs):
    return records_for_materials(*assembled(first_scope_inputs))


def test_compiled_first_scope_ancestry_keeps_original_btc_source_and_issuer_prefix(first_ancestry):
    records, scope, registry=first_ancestry;before=deepcopy(records)
    value=ancestry.validate_scope_ancestry(records, scope=scope, registry=registry, source_commit=PARENT)
    assert value['original_cycle_sha256']==_digest(records['original_setup']['result'])
    assert value['original_spec_sha256']==_digest(records['original_setup']['spec'])
    assert records['original_setup']['result']['source_commit']!=PARENT
    assert scope['scope_enrollment']['original_issuer_history']['records']>=2
    assert value['execution_approval_granted'] is False and records==before


def test_authorization_ancestry_dispatches_v2_without_policy_parser_or_current_expiry(first_ancestry):
    records, scope, registry=first_ancestry
    assert ancestry.validate_authorization_ancestry(records, authorization=scope, registry=registry,
        source_commit=PARENT)['scope_sha256']==_digest(scope)


@pytest.mark.parametrize('damage', ['old_ancestry_schema','old_bundle_schema','old_spec_schema','old_intent_schema',
    'new_source_in_old_result','new_source_in_old_spec','other_original_setup','old_profile','gold_initial_cash',
    'issuer_anchor','first_review','old_confirmation','task','extra_public','seed_stop','seed_key','active_result',
    'activation','policy_registry','spec_original_source','spec_original_setup'])
def test_v2_ancestry_cannot_relabel_rehash_or_drop_originals(first_ancestry, damage):
    original, scope, registry=first_ancestry;records=deepcopy(original);docs=records['public_documents']
    if damage=='old_ancestry_schema':records['schema']=ancestry.SCHEMA
    elif damage=='old_bundle_schema':records['manifest']['schema']='DEMO_GOLD_BUDGET_SCOPE_BUNDLE_V1'
    elif damage=='old_spec_schema':records['spec']['schema']='DEMO_GOLD_BUDGET_SCOPE_INSTALL_SPEC_V1'
    elif damage=='old_intent_schema':records['intent']['schema']='DEMO_GOLD_BUDGET_SCOPE_INSTALL_INTENT_V1'
    elif damage=='new_source_in_old_result':records['original_setup']['result']['source_commit']=PARENT
    elif damage=='new_source_in_old_spec':records['original_setup']['spec']['source_commit']=PARENT
    elif damage=='other_original_setup':records['original_setup']['result']['enrollment_sha256']='e'*64
    elif damage=='old_profile':docs['original-profile.json']['initial_day_pnl']='10'
    elif damage=='gold_initial_cash':docs['profile.json']['initial_peak_equity']='5000'
    elif damage=='issuer_anchor':docs['request.json']['original_issuer_history']['head_sha256']='e'*64
    elif damage=='first_review':docs['request.json']['original_cycle_review']['legacy_risk']['canonical_wallet']='5000'
    elif damage=='old_confirmation':records['operator_enrollment']['schema']='DEMO_GOLD_BUDGET_SCOPE_OPERATOR_V1'
    elif damage=='task':records['manifest']['tasks']=['e'*64]
    elif damage=='extra_public':docs['unapproved.json']={}
    elif damage=='seed_stop':records['intent']['seed_files']['seed-stop.sqlite3']='e'*64
    elif damage=='seed_key':records['intent']['seed_files']['seed-signing-key.json']='e'*64
    elif damage=='active_result':records['active_pointer']['result_sha256']='e'*64
    elif damage=='activation':records['activation']['enrollment_sha256']='e'*64
    elif damage=='policy_registry':docs['registry.json']['keys'].pop(0)
    elif damage=='spec_original_source':records['spec']['original_source_commit']=PARENT
    else:records['spec']['original_setup_sha256']='e'*64
    # Recompute unsigned packaging to prove the original signed request/binding,
    # not just an unchecked manifest digest, prevents changed public originals.
    if damage in ('old_profile','gold_initial_cash','issuer_anchor','first_review'):
        for name,value in docs.items():records['manifest']['files'][name]=_digest(value)
        records['intent']['manifest_sha256']=_digest(records['manifest'])
    with pytest.raises(ValueError):ancestry.validate_scope_ancestry(records,
        scope=scope, registry=registry, source_commit=PARENT)


@pytest.mark.parametrize('damage', ['budget', 'quantity', 'ttl', 'group_id'])
def test_even_valid_signature_cannot_change_the_independently_confirmed_declarations(first_scope_inputs, damage):
    from live.execution import demo_gold_budget_scope as scope_contract
    from tools.demo_operator_setup import encoded
    args, manifest, files=assembled(first_scope_inputs)
    records, scope, registry=records_for_materials(args, manifest, files)
    if damage=='budget':
        scope['max_grants']+=1;scope['budget_group']['max_grants']+=1
    elif damage=='quantity':scope['scope']['max_quantity_per_order']='1'
    elif damage=='ttl':scope['grant_ttl_ms']=10000
    else:scope['budget_group']['group_id']='unconfirmed-second-group'
    scope['signature_base64url']=encoded(args['private_key'].sign(scope_contract.signed_bytes(scope)))
    scope_contract.historical_scope_binding(scope, registry)
    records['manifest']['files']['policy.json']=_digest(scope)
    records['intent']['manifest_sha256']=_digest(records['manifest'])
    with pytest.raises(ValueError, match='FIRST_SCOPE_ANCESTRY_CONFIRMED_TERMS'):
        ancestry.validate_scope_ancestry(records, scope=scope, registry=registry, source_commit=PARENT)


def test_exact_policy_ancestry_can_retain_its_actual_v2_scope_predecessor(first_ancestry):
    """Synthetic decision/installation metadata, genuine Scope and exact compiler.

    This checks historical document routing; it is not current signal review or
    a substitute for the independent operator/executor installation integration.
    """
    from live.execution import demo_gold_budget_scope as scope_contract
    from tools.gold_strategy_request import constraints_from_draft
    from tools.gold_strategy_approval_request import candidate_policy
    from quantos.contracts.killswitch import KillSwitchState
    records, scope, registry=first_ancestry;at=scope['valid_from_ms']+1
    orders=[dict(symbol='XAUUSDT', side='BUY', type='LIMIT', timeInForce='IOC', quantity='0.002',
        price='60000.1', reduceOnly=False, positionSide='BOTH', newOrderRespType='ACK',
        newClientOrderId='QDfirstScopeAncestry')]
    draft=dict(schema='GOLD_DEMO_STRATEGY_ENTRY_DRAFT_V1', status='STRATEGY_ENTRY_DRAFT',
        environment='BINANCE_USDM_DEMO', strategy_definition_sha256=scope['budget_group']['strategy_definition_sha256'],
        observed_at_ms=at, valid_until_ms=at+5000, orders=orders,
        submission_enabled=False, economic_authority=False, risk_status='NOT_RUN')
    draft['result_hash']=_digest(draft)
    constraints=constraints_from_draft(draft, now_ms=at)
    request_sha=_digest({'decision':draft,'constraints':constraints})
    proposed=candidate_policy(scope, registry=registry, request_sha256=request_sha,
        constraints_sha256=_digest(constraints), now_ms=at)
    dispatch=dict(schema='GOLD_STRATEGY_APPROVED_POLICY_DISPATCH_V1', source_commit=PARENT,
        account_ref_sha256=scope['account_ref_sha256'], budget_group_binding_sha256=scope_contract.budget_binding(scope),
        kill_switch=KillSwitchState().model_dump(mode='json'))
    candidate=dict(schema='GOLD_STRATEGY_POLICY_CANDIDATE_V2', status='POLICY_CANDIDATE_REQUIRES_INDEPENDENT_APPROVAL',
        request_sha256=request_sha, result_sha256=_digest(draft), stored_request_sha256=_digest({'request_sha256':request_sha}),
        replaces_policy_sha256=_digest(scope), policy_candidate=proposed, policy_candidate_sha256=_digest(proposed),
        budget_group_binding_sha256=scope_contract.budget_binding(scope), decision_valid_until_ms=at+5000,
        approval_granted=False, submission_enabled=False, economic_authority=False, dispatcher_configuration=dispatch)
    approval=dict(schema='GOLD_STRATEGY_LOCAL_OPERATOR_APPROVAL_V1', candidate_sha256=_digest(candidate),
        operator_id='isolated-exact-operator', approved_at_ms=at+1, economic_authority=False)
    result=dict(schema='GOLD_STRATEGY_POLICY_INSTALLATION_V1', status='POLICY_INSTALLED_REQUIRES_ORIGINAL_GATES',
        source_commit=PARENT, stored_request_sha256=candidate['stored_request_sha256'], candidate_sha256=_digest(candidate),
        previous_policy_sha256=_digest(scope), installed_policy_sha256=_digest(proposed),
        budget_group_binding_sha256=scope_contract.budget_binding(scope), operator_approval_sha256=_digest(approval),
        installed=True, orders_submitted=False, economic_authority=False)
    installation=dict(stage_sha256=_digest(candidate), previous_policy=scope, candidate=candidate,
        operator_approval=approval, result=result, previous_dispatcher=None, dispatcher_candidate=dispatch)
    value=dict(schema='DEMO_GOLD_SCOPE_BUDGET_INSTALLATION_ANCESTRY_V1', result=records['result'], spec=records['spec'],
        scope_ancestry=records, policy_installation=installation)
    checked=ancestry.validate_authorization_ancestry(value, authorization=proposed, registry=registry, source_commit=PARENT)
    assert checked['scope_sha256']==_digest(scope) and checked['exact_policy_installation_sha256']==_digest(result)
    changed=deepcopy(value);changed['policy_installation']['previous_policy']['schema']='DEMO_GOLD_STRATEGY_BUDGET_SCOPE_V3'
    with pytest.raises(ValueError):ancestry.validate_authorization_ancestry(changed,
        authorization=proposed, registry=registry, source_commit=PARENT)
