"""Compiled first-only Scope reaches native shared consumers, without installing.

The upstream fixture retains native BTC issue/recovery and archive validation;
its installed metadata and host custody are explicit synthetic inputs. Actual
cold installation requires the separate first Scope delivery regression.
"""
from copy import deepcopy
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from tests.tools.test_gold_first_scope_materials import (
    book as book, first_scope_archive as first_scope_archive,
    original_issuer as original_issuer, first_scope_inputs as first_scope_inputs, assembled,
)


@pytest.fixture
def compiled_scope(first_scope_inputs):
    values, manifest, files = assembled(first_scope_inputs)
    docs = {name:json.loads(body) for name,body in files.items() if name.endswith('.json')}
    return values, manifest, files, docs


def test_compiled_scope_reaches_importer_admin_candidate_and_disabled_pilot(compiled_scope):
    from live.execution import demo_gold_budget_scope as scope
    from live.execution.demo_issuer_group_continuity import authorization_binding
    from live.execution.demo_issuer_policy import current_key, budget_group_binding
    from tools.demo_issuer_group_admin import current_authorization
    from tools.demo_operating_bundle import validate_materials
    from tools.gold_strategy_approval_request import candidate_policy, scope_runtime
    from tools.gold_scope_pilot_materials import assemble

    values, manifest, files, docs = compiled_scope
    policy = docs['policy.json']; registry = docs['registry.json']; now = values['now_ms']
    result = validate_materials(manifest=manifest, files=files, existing_registry=values['registry'],
        source_commit=policy['executor_release_commit'], account_ref=policy['account_ref_sha256'], now_ms=now)
    assert result['scope_only'] is True and result['tasks'] == []
    assert current_authorization(policy, registry, now) == registry['keys'][-1]
    assert authorization_binding(policy) == scope.budget_binding(policy)
    with pytest.raises(ValueError):
        current_key(policy, registry, now)
    scope_runtime(policy, registry, values['runtime'], None, now)
    candidate = candidate_policy(policy, registry=registry, request_sha256='a'*64,
        constraints_sha256='b'*64, now_ms=now)
    assert candidate['bindings']['execution_constraints_sha256'] == 'b'*64
    assert candidate['budget_group'] == policy['budget_group']
    assert budget_group_binding(candidate) == scope.budget_binding(policy)
    pilot, retained = assemble(scope=policy, registry=registry, source_commit=policy['executor_release_commit'],
        research_state_root='/home/isolated-research/state', execute_approved=False, now_ms=now)
    assert pilot['installed'] is pilot['started'] is pilot['execution_requested'] is False
    assert b'--execute-approved' not in retained['quantos-gold-pilot.service']
    request = json.loads(retained['gold-pilot-request.json'])
    assert request['budget_scope'] == policy and request['scope_registry'] == registry
    assert request['submission_enabled'] is False


def test_expired_first_scope_remains_observable_but_cannot_sign(compiled_scope, tmp_path, monkeypatch):
    from tools import gold_strategy_controller as controller
    from tools.demo_issuer_group_admin import current_authorization
    values, _, files, docs = compiled_scope
    policy = docs['policy.json']; registry = docs['registry.json']
    public = tmp_path/'registry.json'; mirror = tmp_path/'authority.json'; installed = tmp_path/'policy.json'
    public.write_bytes(files['registry.json']); mirror.write_bytes(files['registry.json'])
    installed.write_bytes(files['policy.json'])
    monkeypatch.setattr(controller, 'os', SimpleNamespace(**(vars(os)|{'geteuid':lambda:0})))
    for name,path in (('EXECUTOR_REGISTRY',public), ('ISSUER_REGISTRY',mirror), ('ISSUER_POLICY',installed), ('STATE',tmp_path)):
        monkeypatch.setattr(controller.supervisor, name, path)
    monkeypatch.setattr(controller.supervisor, '_read_root_owned_json', lambda path:path.read_bytes())
    monkeypatch.setattr(controller.supervisor, '_root_private_directory', lambda path:None)
    monkeypatch.setattr(controller.supervisor, 'verify_source', lambda source:
        source == policy['executor_release_commit'] or pytest.fail('wrong accepted source fixture'))
    monkeypatch.setattr(controller.supervisor, 'now_ms', lambda:policy['valid_until_ms']+1)
    assert controller.observation_configuration(policy['executor_release_commit']) == (registry, policy)
    with pytest.raises(ValueError, match='GOLD_SCOPE_EXPIRED'):
        current_authorization(policy, registry, policy['valid_until_ms']+1)
    assert values['request']['original_source_commit'] != policy['executor_release_commit']


@pytest.mark.parametrize('damage', ['old_bundle', 'unknown_bundle', 'issuer_prefix'])
def test_importer_rejects_version_or_original_prefix_substitution(compiled_scope, damage):
    from tools.demo_operating_bundle import validate_materials, canonical
    values, source_manifest, source_files, source_docs = compiled_scope
    manifest = deepcopy(source_manifest); files = deepcopy(source_files); docs = deepcopy(source_docs)
    if damage == 'old_bundle':
        manifest['schema'] = 'DEMO_GOLD_BUDGET_SCOPE_BUNDLE_V1'
    elif damage == 'unknown_bundle':
        manifest['schema'] = 'DEMO_GOLD_BUDGET_SCOPE_BUNDLE_V3'
    else:
        request = docs['request.json']
        request['original_issuer_history']['history_sha256'] = '0'*64
        from live.execution.demo_batch_risk import _digest
        request['request_sha256'] = _digest({key:value for key,value in request.items() if key != 'request_sha256'})
        files['request.json'] = canonical(request)
        manifest['files']['request.json'] = hashlib.sha256(files['request.json']).hexdigest()
    with pytest.raises(ValueError):
        validate_materials(manifest=manifest, files=files, existing_registry=values['registry'],
            source_commit=docs['policy.json']['executor_release_commit'],
            account_ref=docs['policy.json']['account_ref_sha256'], now_ms=values['now_ms'])
