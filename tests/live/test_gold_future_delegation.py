"""V2 future declarations have a distinct signature and real event prerequisite."""

import base64
from copy import deepcopy

import pytest

from live.execution import demo_gold_execution_delegation as contract
from live.execution.demo_batch_risk import _digest
from services.validation import demo_submission as submission
from tests.live.test_gold_future_template import template_case
from tests.live.test_gold_strategy_constraints import case as case, NOW
from tests.live.test_gold_execution_delegation import sign


def declaration_case(case):
    """Structurally valid public declaration, deliberately no committed event."""
    template, registry, private, old, *_ = template_case(case)
    terms = deepcopy(old['terms'])
    terms.update(schema='DEMO_GOLD_EXECUTION_DELEGATION_TERMS_V2',
                 original_budget_group_binding_sha256='c'*64,
                 active_budget_group_binding_sha256=old['terms']['original_budget_group_binding_sha256'])
    value = dict(schema='DEMO_GOLD_EXECUTION_DELEGATION_V2', future_template=template,
                 continuity_event_sha256='d'*64, terms=terms, automatic_renewal=False,
                 strategy_changes_allowed=False, economic_authority=False,
                 submission_enabled=False, signature_base64url='')
    return value, registry, private, old


def test_v2_signing_payload_is_distinct_and_does_not_invent_constraints(case):
    value, _, _, old = declaration_case(case)
    payload = contract.signing_payload(value)
    assert payload == b'QUANTOS_DEMO_GOLD_EXECUTION_DELEGATION_V2\x00' + submission._canonical(
        {k: v for k, v in value.items() if k != 'signature_base64url'})
    assert contract.delegation_base(value) == value['future_template']
    assert 'execution_constraints_sha256' not in value['future_template']['bindings']
    assert contract.delegation_base(old) == old['policy_template']
    assert case[-1].snapshot() == []


def test_signature_and_event_digest_alone_never_establish_v2_continuity(case):
    value, registry, private, _ = declaration_case(case)
    sign(contract, private, value)
    with pytest.raises(ValueError, match='GOLD_DELEGATION_CONTINUITY_REQUIRED'):
        contract.verify(value, registry=registry, source_commit='c'*40,
            account_ref_sha256=value['future_template']['account_ref_sha256'], now_ms=NOW+1)
    assert case[-1].snapshot() == []


@pytest.mark.parametrize('damage', [
    'exact_base', 'old_terms', 'constraints', 'active_binding', 'pilot_request',
    'pilot_config', 'pilot_origin', 'renewal', 'event_hash', 'extra',
])
def test_v2_structure_rejects_exact_fallback_and_mismatched_pilot(case, damage):
    value, *_ = declaration_case(case)
    if damage == 'exact_base': value['policy_template'] = value.pop('future_template')
    elif damage == 'old_terms': value['terms']['schema'] = 'DEMO_GOLD_EXECUTION_DELEGATION_TERMS_V1'
    elif damage == 'constraints': value['future_template']['bindings']['execution_constraints_sha256'] = '0'*64
    elif damage == 'active_binding': value['terms']['active_budget_group_binding_sha256'] = 'a'*64
    elif damage == 'pilot_request': value['terms']['pilot_request_sha256'] = 'a'*64
    elif damage == 'pilot_config': value['future_template']['pilot']['configuration_sha256'] = 'a'*64
    elif damage == 'pilot_origin': value['future_template']['pilot']['opening_origin_sha256'] = 'a'*64
    elif damage == 'renewal': value['automatic_renewal'] = True
    elif damage == 'event_hash': value['continuity_event_sha256'] = ''
    else: value['continuity_verified'] = True
    with pytest.raises(ValueError):
        contract.signing_payload(value)


def test_old_domain_cannot_sign_a_v2_body_even_with_real_shape(case):
    value, _, private, _ = declaration_case(case)
    raw = submission._canonical({k: v for k, v in value.items() if k != 'signature_base64url'})
    value['signature_base64url'] = base64.urlsafe_b64encode(private.sign(contract.DOMAIN + raw)).rstrip(b'=').decode()
    assert contract.signing_payload(value) != contract.DOMAIN + raw


def test_v1_signed_artifacts_remain_byte_identical_and_verified(case):
    _, registry, private, old = declaration_case(case)
    expected = contract.DOMAIN + submission._canonical({k: v for k, v in old.items() if k != 'signature_base64url'})
    assert contract.signing_payload(old) == expected
    sign(contract, private, old)
    assert contract.verify(old, registry=registry, source_commit='c'*40,
        account_ref_sha256=old['policy_template']['account_ref_sha256'], now_ms=NOW+1) is old


@pytest.mark.parametrize('damage', ['old_purpose', 'authority', 'subject', 'bool_time', 'extra', 'missing_event'])
def test_unsigned_builder_never_upgrades_a_scope_confirmation(case, damage):
    value, *_ = declaration_case(case)
    intent = {k: deepcopy(value['terms'][k]) for k in contract.INTENT_FIELDS.split()}
    confirmation = dict(schema='GOLD_FUTURE_BUDGET_OPERATOR_CONFIRMATION_V1', purpose='DELEGATION_V2_SIGN',
        subject_sha256='e'*64, operator_id='isolated-operator', approved_at_ms=NOW+1,
        consumer_enabled=False, submission_enabled=False, economic_authority=False)
    event = None
    if damage == 'old_purpose': confirmation['purpose'] = 'GROUP_CONTINUITY'
    elif damage == 'authority': confirmation['economic_authority'] = True
    elif damage == 'subject': confirmation['subject_sha256'] = ''
    elif damage == 'bool_time': confirmation['approved_at_ms'] = True
    elif damage == 'extra': confirmation['auto_approve'] = True
    with pytest.raises(ValueError):
        contract.build_delegation_v2(intent, future_template=value['future_template'],
            confirmation=confirmation, continuity_event=event)


def historical_delegation_case(case, tmp_path, monkeypatch):
    """Real signed/replayed history; deliberately not a protected APPLY test."""
    from tests.live.test_demo_issuer_group_continuity import retained_budget, signed_group_row, append_historical
    from live.execution.demo_gold_future_template import template_group_binding
    from live.execution.demo_issuer_policy import budget_group_binding

    f = retained_budget(case, tmp_path, monkeypatch)
    template = f['template']
    config = dict(schema='GOLD_DEMO_PILOT_CONTROL_V1', environment='BINANCE_USDM_DEMO',
        symbol='XAUUSDT', account_ref_sha256=template['account_ref_sha256'],
        opening_origin_sha256=template['pilot']['opening_origin_sha256'], initial_equity='5000',
        started_at_ms=template['pilot']['started_at_ms'], ends_at_ms=template['pilot']['ends_at_ms'],
        max_loss_fraction='0.05', submission_enabled=False)
    template['pilot']['configuration_sha256'] = _digest(config)
    event = signed_group_row(f)
    append_historical(f['store'], event)
    intent = dict(original_budget_group_binding_sha256=budget_group_binding(f['previous']),
        active_budget_group_binding_sha256=template_group_binding(template), runtime_sha256='a'*64,
        strategy_source_commit='b'*40, trial_manifest_sha256='c'*64,
        pilot_request_sha256=template['pilot']['request_sha256'], pilot_configuration=config,
        opening_until_ms=config['ends_at_ms']-60000, exit_until_ms=template['valid_until_ms'],
        max_holding_ms=86400000, entry_timeout_ms=5000)
    confirmation = dict(schema='GOLD_FUTURE_BUDGET_OPERATOR_CONFIRMATION_V1', purpose='DELEGATION_V2_SIGN',
        subject_sha256=_digest(dict(intent=intent, template=template, continuity_event=event)),
        operator_id='isolated-signature-test-operator', approved_at_ms=NOW+15,
        consumer_enabled=False, submission_enabled=False, economic_authority=False)
    value = contract.build_delegation_v2(intent, future_template=template,
        confirmation=confirmation, continuity_event=event)
    sign(contract, f['private'], value)
    return f, event, value


def test_actual_signed_native_event_binds_v2_and_remains_historically_readable(case, tmp_path, monkeypatch):
    f, event, value = historical_delegation_case(case, tmp_path, monkeypatch)
    before = f['store'].snapshot()
    assert [row['kind'] for row in before] == ['RESERVED', 'GROUP_EXIT_RESERVE_MIGRATED']
    # Its administrative operation has expired, while the original pilot/key
    # window is still active. Reopening history must not renew or destroy either.
    now = event['payload']['operation']['subject']['expires_at_ms']+1
    assert contract.verify(value, registry=f['registry'], source_commit='c'*40,
        account_ref_sha256=f['template']['account_ref_sha256'], now_ms=now,
        continuity_event=event) is value
    assert f['store'].snapshot() == before
    assert value['terms']['original_budget_group_binding_sha256'] != value['terms']['active_budget_group_binding_sha256']


@pytest.mark.parametrize('damage', ['row_hash', 'old_domain', 'from_binding', 'different_template', 'event_time', 'expired_key'])
def test_v2_rejects_re_signed_or_rehashed_changes_outside_committed_event(case, tmp_path, monkeypatch, damage):
    from live.execution.demo_gold_future_template import template_group_binding
    f, event, value = historical_delegation_case(case, tmp_path, monkeypatch)
    now = NOW+16
    if damage == 'row_hash':
        event = deepcopy(event)
        event['seq'] += 1
    elif damage == 'old_domain':
        raw = submission._canonical({k: v for k, v in value.items() if k != 'signature_base64url'})
        value['signature_base64url'] = base64.urlsafe_b64encode(f['private'].sign(contract.DOMAIN + raw)).rstrip(b'=').decode()
    elif damage == 'from_binding':
        value['terms']['original_budget_group_binding_sha256'] = 'e'*64
        sign(contract, f['private'], value)
    elif damage == 'different_template':
        value['future_template']['template_id'] = 'another-declaration'
        value['terms']['active_budget_group_binding_sha256'] = template_group_binding(value['future_template'])
        sign(contract, f['private'], value)
    elif damage == 'event_time':
        value['terms']['confirmed_at_ms'] = event['at_ms']-1
        sign(contract, f['private'], value)
    else:
        now = f['registry']['keys'][0]['valid_until_ms']
    with pytest.raises(ValueError):
        contract.verify(value, registry=f['registry'], source_commit='c'*40,
            account_ref_sha256=f['template']['account_ref_sha256'], now_ms=now, continuity_event=event)
