"""Explicit future-signal terms are distinct from an executable order policy."""
from copy import deepcopy
import base64

import pytest

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import budget_group_binding, validate_policy
from services.validation.demo_submission import registry_sha256
from tests.live.test_gold_strategy_constraints import case as case, NOW
from tests.live.test_gold_reserved_exit_budget import budget


def fixture(case):
    from live.execution import demo_gold_execution_delegation as contract
    private, _, _, original_registry, _, _ = case
    registry = deepcopy(original_registry)
    key = registry['keys'][0]
    key['valid_from_ms'] = NOW-1000
    key['valid_until_ms'] = NOW+86400000+600000
    policy = budget(case)['issuer_policy']
    policy['valid_from_ms'] = NOW-1000
    policy['valid_until_ms'] = key['valid_until_ms']
    policy['registry_sha256'] = registry_sha256(registry)
    policy['budget_group'].update(valid_from_ms=policy['valid_from_ms'], valid_until_ms=policy['valid_until_ms'])
    control = dict(schema='GOLD_DEMO_PILOT_CONTROL_V1', environment='BINANCE_USDM_DEMO',
        symbol='XAUUSDT', account_ref_sha256=policy['account_ref_sha256'], opening_origin_sha256='1'*64,
        initial_equity='5000', started_at_ms=NOW, ends_at_ms=NOW+86400000,
        max_loss_fraction='0.05', submission_enabled=False)
    body = dict(schema=contract.SCHEMA, policy_template=deepcopy(policy),
        terms=dict(schema='DEMO_GOLD_EXECUTION_DELEGATION_TERMS_V1',
            purpose='FIXED_BAR_RULES_FUTURE_SIGNALS_WITH_INDEPENDENT_PROOF',
            confirmation='AUTHORIZE_BOUNDED_FUTURE_SIGNALS_AND_RESERVED_EXITS',
            operator_approval_sha256='2'*64, confirmed_at_ms=NOW,
            original_budget_group_binding_sha256=budget_group_binding(policy),
            runtime_sha256='3'*64, strategy_source_commit='4'*40,
            trial_manifest_sha256='5'*64, pilot_request_sha256='6'*64,
            pilot_configuration=control, opening_until_ms=NOW+86400000-60000,
            exit_until_ms=policy['valid_until_ms'], max_holding_ms=86400000, entry_timeout_ms=5000),
        automatic_renewal=False, strategy_changes_allowed=False, economic_authority=False,
        submission_enabled=False, signature_base64url='')
    return contract, private, registry, body


def sign(contract, private, body):
    body['signature_base64url'] = base64.urlsafe_b64encode(private.sign(contract.signing_payload(body))).rstrip(b'=').decode()
    return body


def test_new_explicit_terms_verify_but_original_order_policy_rejects_them(case):
    contract, private, registry, body = fixture(case)
    signed = sign(contract, private, body)
    assert contract.verify(signed, registry=registry, source_commit=body['policy_template']['executor_release_commit'],
        account_ref_sha256=body['policy_template']['account_ref_sha256'], now_ms=NOW+1) == signed
    with pytest.raises(ValueError): validate_policy(signed)
    assert case[-1].snapshot() == []


@pytest.mark.parametrize('damage', ['old_scope','confirmation','renewal','tuning','authority','submission',
    'pilot_account','pilot_start','opening_after_end','exit_before_end','exit_after_policy',
    'holding','long_signal','bool_time','future_confirmation','missing_reserve','budget_rebind',
    'wrong_source','bad_runtime','extra'])
def test_even_re_signed_invalid_scope_cannot_become_a_delegation(case, damage):
    contract, private, registry, body = fixture(case)
    terms = body['terms']; at = NOW+1
    if damage=='old_scope':body['schema']='DEMO_GOLD_STRATEGY_BUDGET_SCOPE_V1'
    elif damage=='confirmation':terms['confirmation']='OBSERVATION_ONLY'
    elif damage=='renewal':body['automatic_renewal']=True
    elif damage=='tuning':body['strategy_changes_allowed']=True
    elif damage=='authority':body['economic_authority']=True
    elif damage=='submission':body['submission_enabled']=True
    elif damage=='pilot_account':terms['pilot_configuration']['account_ref_sha256']='f'*64
    elif damage=='pilot_start':terms['pilot_configuration']['started_at_ms']+=1
    elif damage=='opening_after_end':terms['opening_until_ms']=terms['pilot_configuration']['ends_at_ms']
    elif damage=='exit_before_end':terms['exit_until_ms']=terms['pilot_configuration']['ends_at_ms']-1
    elif damage=='exit_after_policy':terms['exit_until_ms']+=1
    elif damage=='holding':terms['max_holding_ms']=86400001
    elif damage=='long_signal':terms['entry_timeout_ms']=5001
    elif damage=='bool_time':terms['confirmed_at_ms']=True
    elif damage=='future_confirmation':terms['confirmed_at_ms']=NOW+2
    elif damage=='missing_reserve':body['policy_template']['budget_group'].pop('exit_reserve')
    elif damage=='budget_rebind':body['policy_template']['budget_group']['max_orders']+=1
    elif damage=='wrong_source':body['policy_template']['executor_release_commit']='f'*40
    elif damage=='bad_runtime':terms['runtime_sha256']='abc'
    else:terms['extend_until_pass']=True
    # Invalid structural declarations cannot use the signing helper; still
    # adversarially sign their raw bytes to test verification independently.
    from services.validation.demo_submission import _canonical
    raw={k:v for k,v in body.items() if k!='signature_base64url'}
    body['signature_base64url']=base64.urlsafe_b64encode(private.sign(contract.DOMAIN+_canonical(raw))).rstrip(b'=').decode()
    with pytest.raises(ValueError):
        contract.verify(body, registry=registry, source_commit='c'*40,
            account_ref_sha256=case[4]['account_ref_sha256'], now_ms=at)
    assert case[-1].snapshot()==[]


@pytest.mark.parametrize('damage', ['revoked','expired','signature','domain','account','source','registry'])
def test_current_independent_identity_and_separate_domain_are_mandatory(case, damage):
    contract, private, registry, body=fixture(case)
    sign(contract,private,body)
    at=NOW+1;source=body['policy_template']['executor_release_commit'];account=body['policy_template']['account_ref_sha256']
    if damage=='revoked':registry['keys'][0]['status']='REVOKED'
    elif damage=='expired':at=body['terms']['exit_until_ms']
    elif damage=='signature':body['terms']['runtime_sha256']='a'*64
    elif damage=='domain':
        from services.validation.demo_submission import _canonical
        raw={k:v for k,v in body.items() if k!='signature_base64url'}
        body['signature_base64url']=base64.urlsafe_b64encode(private.sign(b'QUANTOS_DEMO_GOLD_BUDGET_SCOPE_V1\x00'+_canonical(raw))).rstrip(b'=').decode()
    elif damage=='account':account='f'*64
    elif damage=='source':source='f'*40
    else:registry['deployment_sha256']='f'*64
    with pytest.raises(ValueError):
        contract.verify(body,registry=registry,source_commit=source,account_ref_sha256=account,now_ms=at)
    assert case[-1].snapshot()==[]


def test_signature_verification_preserves_declared_inputs(case):
    contract,private,registry,body=fixture(case)
    original=deepcopy(body)
    sign(contract,private,body)
    contract.verify(body,registry=registry,source_commit='c'*40,
        account_ref_sha256=body['policy_template']['account_ref_sha256'],now_ms=NOW+1)
    assert body['terms']['pilot_configuration']==original['terms']['pilot_configuration']
    assert body['terms']['original_budget_group_binding_sha256']==budget_group_binding(body['policy_template'])
    assert _digest(body['policy_template']['budget_group'])==_digest(original['policy_template']['budget_group'])


def test_declared_exit_cannot_outlive_the_registered_key(case):
    contract,private,registry,body=fixture(case)
    registry['keys'][0]['valid_until_ms']=body['terms']['exit_until_ms']-1
    body['policy_template']['registry_sha256']=registry_sha256(registry)
    body['terms']['original_budget_group_binding_sha256']=budget_group_binding(body['policy_template'])
    sign(contract,private,body)
    with pytest.raises(ValueError,match='GOLD_DELEGATION_KEY_WINDOW'):
        contract.verify(body,registry=registry,source_commit='c'*40,
            account_ref_sha256=body['policy_template']['account_ref_sha256'],now_ms=NOW+1)
    assert case[-1].snapshot()==[]
