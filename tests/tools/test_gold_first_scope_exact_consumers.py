"""Cold signed Scope V2 survives both exact-policy installation steps.

Original setup, BTC issuer history, Scope signature, market replay, retained
review and installer execute. Host identity/custody, TTY and venue transport
use the upstream isolated fixture; no successful signature is fabricated.
"""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tests.live.test_gold_first_scope_exact_continuity import (
    first_scope_archive as first_scope_archive,
    cold_scope_local as cold_scope_local,
    installed_first_scope as installed_first_scope,
    native_scope as native_scope,
    exact_case as exact_case,
)


@pytest.fixture
def retained_exact(exact_case, monkeypatch):
    from tools import gold_strategy_controller as controller
    from tools import gold_strategy_policy_install as installer
    from tools import gold_strategy_approval_request as approval
    from tools import gold_bar_worker_inputs, demo_resident_recovery
    from live.execution.demo_pre_submit import _retain
    from tools.demo_request_prepare import destination

    f = exact_case
    # This lazily reached alias still has the real-host UID reader. Reuse the
    # upstream exact custody check with only its synthetic owner mapping.
    monkeypatch.setattr(gold_bar_worker_inputs, '_check_path', demo_resident_recovery._check_path)
    local = f.installed['local']
    runtime = local['parts']['runtime']
    _retain(destination(local['ctl'], 'strategy-decisions'), f.result)
    request = controller.retain_approval_request(f.result, bundle=runtime,
        policy=f.scope, registry=f.registry)
    assert request is not None
    candidate = approval.review_retained(source_commit=f.scope['executor_release_commit'],
        stored_request_sha256=_digest(request), prepare_candidate=True)
    # Preserve the actual local_operator and confirmation functions. Only the
    # terminal/sudo identity is synthetic, like the cold installation fixture.
    monkeypatch.setattr(installer, 'sys', SimpleNamespace(
        stdin=SimpleNamespace(isatty=lambda: True),
        stdout=SimpleNamespace(isatty=lambda: True)))
    monkeypatch.setenv('SUDO_UID', '1000')
    monkeypatch.setenv('SUDO_USER', 'isolated-exact-operator')
    monkeypatch.setattr(installer.pwd, 'getpwuid', lambda uid:
        SimpleNamespace(pw_name='isolated-exact-operator'))
    return f, runtime, request, candidate


def install_request(f, request):
    from tools.gold_strategy_policy_install import install_approved
    return install_approved(source_commit=f.scope['executor_release_commit'],
        stored_request_sha256=_digest(request))


def test_first_and_next_native_exact_install_preserve_original_signed_scope(retained_exact):
    from tools import gold_strategy_controller as controller
    from tools import gold_strategy_approval_request as approval
    from tools.gold_strategy_policy_install import validate_candidate

    f, runtime, request, candidate = retained_exact
    local = f.installed['local']
    path = local['cfg']/'policy.json'
    prefix = f.installed['history']['issuer_store'].snapshot()
    assert candidate['policy_candidate']['schema'] == 'DEMO_GOLD_STRATEGY_BUDGET_POLICY_V2'
    assert candidate['policy_candidate']['original_scope'] == f.scope
    first = install_request(f, request)
    policy = json.loads(path.read_bytes())
    assert first['installed'] is True and first['orders_submitted'] is False
    assert policy == candidate['policy_candidate']

    # Same still-fresh native observation, now under the installed exact policy.
    # A second human confirmation changes only the exact decision identity.
    next_request = controller.retain_approval_request(f.result, bundle=runtime,
        policy=policy, registry=f.registry)
    assert next_request is not None, 'V2 exact policy must retain the next approval request'
    next_candidate = approval.review_retained(source_commit=f.scope['executor_release_commit'],
        stored_request_sha256=_digest(next_request), prepare_candidate=True)
    validate_candidate(next_candidate, policy=policy, registry=f.registry, now_ms=f.now_ms)
    assert next_candidate['policy_candidate']['original_scope'] == f.scope
    second = install_request(f, next_request)
    installed = json.loads(path.read_bytes())
    assert second['installed'] is True and second['orders_submitted'] is False
    assert installed == next_candidate['policy_candidate']
    assert installed['original_scope'] == policy['original_scope'] == f.scope
    assert installed['budget_group'] == policy['budget_group'] == f.scope['budget_group']
    assert f.installed['history']['issuer_store'].snapshot() == prefix
    for before, current in ((f.scope, candidate), (policy, next_candidate)):
        stage = local['cfg']/'strategy-approvals'/_digest(current)
        assert json.loads((stage/'previous-policy.json').read_bytes()) == before
        assert json.loads((stage/'candidate.json').read_bytes()) == current
        assert json.loads((stage/'operator-approval.json').read_bytes())['candidate_sha256'] == _digest(current)


def test_v2_pilot_configuration_keeps_original_full_day_gate(retained_exact):
    from tools import gold_pilot_install as pilot

    f, _, request, _ = retained_exact
    install_request(f, request)
    source = f.scope['executor_release_commit']
    registry, policy = pilot._configuration(source, False)
    assert policy['schema'] == 'DEMO_GOLD_STRATEGY_BUDGET_POLICY_V2'
    assert policy['original_scope'] == f.scope and registry == f.registry
    f.installed['local']['clock'][0] = policy['valid_until_ms']-pilot.DAY_MS+1
    with pytest.raises(ValueError, match='PILOT_INSTALL_AUTHORIZATION_WINDOW'):
        pilot._configuration(source, False)


def test_following_v2_candidate_keeps_exact_installer_validation(retained_exact):
    from tools import gold_strategy_approval_request as approval
    from tools.gold_strategy_policy_install import validate_candidate

    f, runtime, _, first = retained_exact
    policy = first['policy_candidate']
    request = approval.prepare(f.result, runtime=runtime, policy=policy,
        registry=f.registry, now_ms=f.now_ms)
    candidate = approval.policy_candidate(request, result=f.result, runtime=runtime,
        policy=policy, registry=f.registry, now_ms=f.now_ms)
    candidate['stored_request_sha256'] = _digest(request)
    assert candidate['policy_candidate']['original_scope'] == f.scope
    assert validate_candidate(candidate, policy=policy, registry=f.registry,
        now_ms=f.now_ms) == candidate


def test_rehashed_exact_candidate_cannot_drop_or_change_original_scope(retained_exact):
    from tools.gold_strategy_policy_install import validate_candidate

    f, _, _, candidate = retained_exact
    assert candidate['policy_candidate']['schema'] == 'DEMO_GOLD_STRATEGY_BUDGET_POLICY_V2'
    for damage in ('drop', 'downgrade', 'changed_prefix'):
        changed = deepcopy(candidate)
        proposed = changed['policy_candidate']
        if damage in ('drop', 'downgrade'):
            proposed.pop('original_scope')
            if damage == 'downgrade':
                proposed['schema'] = 'DEMO_GOLD_STRATEGY_BUDGET_POLICY_V1'
        else:
            proposed['original_scope']['scope_enrollment']['original_issuer_history']['history_sha256'] = 'f'*64
        changed['policy_candidate_sha256'] = _digest(proposed)
        with pytest.raises(ValueError):
            validate_candidate(changed, policy=f.scope, registry=f.registry, now_ms=f.now_ms)


def test_expired_confirmation_does_not_replace_signed_scope(retained_exact, monkeypatch):
    from tools import gold_strategy_policy_install as installer

    f, _, request, candidate = retained_exact
    local = f.installed['local']
    path = local['cfg']/'policy.json'
    before = path.read_bytes()
    native_confirmation = installer.operator_confirmation
    confirmed = []
    def late_confirmation(value, operator):
        result = native_confirmation(value, operator)
        confirmed.append(result)
        local['clock'][0] = candidate['decision_valid_until_ms']
        return result
    monkeypatch.setattr(installer, 'operator_confirmation', late_confirmation)
    with pytest.raises(ValueError):
        install_request(f, request)
    assert len(confirmed) == 1
    assert path.read_bytes() == before
    assert not (local['cfg']/'strategy-approvals').exists()
