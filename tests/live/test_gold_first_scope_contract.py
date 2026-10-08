"""Independent V2 signature domain; historical integrity is never send authority."""
from copy import deepcopy
import json

import pytest

from live.execution import demo_gold_budget_scope as scope
from tests.tools.test_gold_first_scope_materials import (book as book, first_scope_archive as first_scope_archive,
    original_issuer as original_issuer, first_scope_inputs as first_scope_inputs, assembled)


def signed_case(first_scope_inputs):
    values, _, files=assembled(first_scope_inputs)
    return values, json.loads(files['policy.json']), json.loads(files['registry.json'])


def test_finite_classifier_preserves_old_constants_and_rejects_unknown_versions():
    assert scope.SCHEMA=='DEMO_GOLD_STRATEGY_BUDGET_SCOPE_V1'
    assert scope.BUNDLE_SCHEMA=='DEMO_GOLD_BUDGET_SCOPE_BUNDLE_V1'
    assert scope.DOMAIN==b'QUANTOS_DEMO_GOLD_BUDGET_SCOPE_V1\x00'
    assert scope.is_scope({'schema':scope.SCHEMA}) and scope.is_scope({'schema':scope.SCHEMA_V2})
    assert not scope.is_scope({'schema':'DEMO_GOLD_STRATEGY_BUDGET_SCOPE_V3'})
    assert not scope.is_scope(None) and not scope.is_scope({'schema':'DEMO_ISSUER_POLICY_V1'})


def test_signed_v2_checks_current_key_and_original_window_without_execution(first_scope_inputs):
    from live.execution.demo_issuer_policy import validate_policy, current_key
    values, value, registry=signed_case(first_scope_inputs);at=values['now_ms']
    assert scope.current_scope_key(value, registry, at)==registry['keys'][-1]
    assert scope.signed_bytes(value).startswith(scope.DOMAIN_V2)
    assert value['scope_enrollment']['original_source_commit']!=value['executor_release_commit']
    assert value['scope_enrollment']['full_state_sha256']==values['review']['legacy_state_sha256']
    assert value['scope_enrollment']['original_issuer_history']==values['issuer_history']
    with pytest.raises(ValueError):validate_policy(value)
    with pytest.raises(ValueError):current_key(value, registry, at)
    window=scope.pilot_window(value, registry=registry, now_ms=at+300000)
    assert window['ends_at_ms']==value['valid_until_ms']
    with pytest.raises(ValueError):scope.pilot_window(value, registry=registry, now_ms=at+300001)
    assert scope.historical_scope_binding(value, registry)==registry['keys'][-1]
    with pytest.raises(ValueError):scope.current_scope_key(value, registry, value['valid_until_ms'])


@pytest.mark.parametrize('damage', ['old_domain', 'old_binding', 'old_purpose', 'remove_setup', 'issuer_empty',
    'issuer_history', 'old_source', 'duration', 'start_allowance', 'execution', 'scope_limit', 'unknown_schema'])
def test_signature_and_strict_version_binding_reject_substitution(first_scope_inputs, damage):
    from tools.demo_operator_setup import encoded
    from services.validation.demo_submission import _canonical
    values, original, registry=signed_case(first_scope_inputs);value=deepcopy(original)
    e=value['scope_enrollment']
    if damage=='old_domain':
        body={k:v for k,v in value.items() if k!='signature_base64url'}
        value['signature_base64url']=encoded(values['private_key'].sign(scope.DOMAIN+_canonical(body)))
    elif damage=='old_binding':e['schema']='DEMO_GOLD_BUDGET_SCOPE_BINDING_V1'
    elif damage=='old_purpose':e['purpose']='DEMO_GOLD_FIRST_DAY_FUNCTIONAL_PILOT_SCOPE_ONLY'
    elif damage=='remove_setup':e.pop('original_setup_sha256')
    elif damage=='issuer_empty':e['original_issuer_history']['records']=0
    elif damage=='issuer_history':e['original_issuer_history']['head_sha256']='e'*64
    elif damage=='old_source':e['original_source_commit']=value['executor_release_commit']
    elif damage=='duration':e['duration_seconds']=86401
    elif damage=='start_allowance':e['installation_allowance_seconds']=301
    elif damage=='execution':e['execution_approval_granted']=True
    elif damage=='scope_limit':value['scope']['max_quantity_per_order']='1'
    else:value['schema']='DEMO_GOLD_STRATEGY_BUDGET_SCOPE_V3'
    with pytest.raises(ValueError):scope.historical_scope_binding(value, registry)


def test_original_issuer_rejects_actual_scope_before_reserving(first_scope_inputs, tmp_path):
    from live.execution.demo_issuer import issue_submission
    from live.execution.demo_issuer_store import IssuerStore
    values, value, registry=signed_case(first_scope_inputs)
    store=IssuerStore(tmp_path/'no-new-scope-grant.sqlite3', create=True)
    try:
        with pytest.raises(ValueError):issue_submission({}, policy=value, registry=registry,
            key=values['private_key'], store=store, now_ms=values['now_ms'])
        assert store.snapshot()==[]
    finally:store.close()
