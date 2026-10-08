"""Installed non-executing templates at controller read boundaries.

Custody and accepted release identity are mapped to this temporary directory.
Policy/key validation remains native; these tests do not establish the separate
human installation, continuity certificate or future decision admission.
"""
from copy import deepcopy
from contextlib import nullcontext
import json
import os
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tests.live.test_gold_execution_delegation import fixture
from tests.live.test_gold_strategy_constraints import case as case, NOW
from tools import demo_supervisor as supervisor
from tools import gold_strategy_controller as controller
from tools.demo_resident_recovery import _check_path


@pytest.fixture
def installed_template(case, tmp_path, monkeypatch):
    _, _, registry, delegation = fixture(case)
    original = deepcopy(delegation['policy_template'])
    original['schema'] = 'DEMO_GOLD_STRATEGY_BUDGET_POLICY_V1'
    original['budget_group'].pop('exit_reserve')
    template = deepcopy(delegation['policy_template'])
    template['schema'] = 'DEMO_GOLD_FUTURE_EXIT_BUDGET_TEMPLATE_V1'
    template['template_id'] = template.pop('policy_id')
    template['bindings'].pop('execution_constraints_sha256')
    pilot = delegation['terms']['pilot_configuration']
    template.update(
        origin=dict(kind='BUDGET', document_sha256=_digest(original),
                    profile_sha256='a'*64, setup_sha256='b'*64, spec_sha256='d'*64),
        pilot=dict(request_sha256=delegation['terms']['pilot_request_sha256'],
                   configuration_sha256=_digest(pilot),
                   opening_origin_sha256=pilot['opening_origin_sha256'],
                   started_at_ms=pilot['started_at_ms'], ends_at_ms=pilot['ends_at_ms']),
        executable=False, submission_enabled=False, economic_authority=False,
    )
    state = tmp_path/'controller'
    state.mkdir(mode=0o700)
    for name in ('requests', 'claims', 'results'):
        (state/name).mkdir(mode=0o700)
    monkeypatch.setattr(supervisor, 'STATE', state)
    virtual_root = SimpleNamespace(**{**vars(os), 'geteuid': lambda: 0})
    monkeypatch.setattr(supervisor, 'os', virtual_root)
    monkeypatch.setattr(controller, 'os', virtual_root)
    verified = []

    def verify(source_commit):
        assert source_commit == template['executor_release_commit']
        verified.append(source_commit)

    def read(path):
        assert path in (supervisor.EXECUTOR_REGISTRY, supervisor.ISSUER_REGISTRY, supervisor.ISSUER_POLICY)
        return json.dumps(template if path == supervisor.ISSUER_POLICY else registry).encode()

    def directory(path):
        path.relative_to(tmp_path)
        _check_path(path, os.getuid(), directory=True)

    monkeypatch.setattr(supervisor, 'verify_source', verify)
    monkeypatch.setattr(supervisor, '_read_root_owned_json', read)
    monkeypatch.setattr(supervisor, '_root_private_directory', directory)
    monkeypatch.setattr(supervisor, 'now_ms', lambda: NOW+1)
    return template, registry, state, verified


def test_current_controller_configuration_reads_template_without_exact_constraints(installed_template):
    template, registry, state, verified = installed_template
    before = sorted(str(path.relative_to(state)) for path in state.rglob('*'))
    assert supervisor.configuration(template['executor_release_commit']) == (registry, template)
    assert verified == [template['executor_release_commit']]
    assert 'execution_constraints_sha256' not in template['bindings']
    assert before == sorted(str(path.relative_to(state)) for path in state.rglob('*'))


def test_expired_template_is_readable_only_by_observation_configuration(installed_template, monkeypatch):
    template, registry, _, _ = installed_template
    monkeypatch.setattr(supervisor, 'now_ms', lambda: template['valid_until_ms']+1)
    assert controller.observation_configuration(template['executor_release_commit']) == (registry, template)
    with pytest.raises(ValueError):
        supervisor.configuration(template['executor_release_commit'])


@pytest.mark.parametrize('damage', ['constraints', 'executable', 'source', 'key_revoked'])
def test_current_template_loader_keeps_original_identity_and_nonexecution_gates(installed_template, damage):
    template, registry, _, _ = installed_template
    source = template['executor_release_commit']
    if damage == 'constraints':
        template['bindings']['execution_constraints_sha256'] = 'f'*64
    elif damage == 'executable':
        template['executable'] = True
    elif damage == 'source':
        registry['deployment_sha256'] = 'e'*64
    else:
        registry['keys'][0]['status'] = 'REVOKED'
    with pytest.raises(ValueError):
        supervisor.configuration(source)


def test_administrative_public_materials_do_not_change_private_role_isolation():
    registry = {'executor_uid': 2001, 'issuer_uid': 2002}
    for role in ('executor', 'issuer'):
        command = supervisor.child_command(role, source_commit='c'*40, registry=registry,
            purpose='submission', unit='quantos-demo-'+role+'-isolated1234')
        properties = [command[index+1] for index, value in enumerate(command[:-1]) if value == '-p']
        read_paths = next(value for value in properties if value.startswith('ReadOnlyPaths='))
        assert ('-/etc/quantos/demo-gold-continuity' in read_paths) == (role == 'issuer')
        if role == 'issuer':
            assert 'InaccessiblePaths=/etc/quantos/demo-submission /var/lib/quantos-demo-executor' in properties
            assert 'PrivateNetwork=yes' in properties
        else:
            assert 'InaccessiblePaths=/etc/quantos/demo-issuer /var/lib/quantos-demo-issuer' in properties
    with pytest.raises(ValueError):
        supervisor.child_command('issuer', source_commit='c'*40, registry=registry,
            purpose='GROUP_CONTINUITY', unit='quantos-demo-issuer-isolated1234')


@pytest.mark.parametrize('schema',['DEMO_EXECUTION_SESSION_INPUT_V1','DEMO_GOLD_STRATEGY_SESSION_INPUT_V1'])
def test_template_cannot_send_an_ordinary_session_without_delegated_proof(installed_template,monkeypatch,schema):
    template,_,state,_=installed_template
    # Isolate lease acquisition for this pre-claim input rejection; the real
    # source/key/template loader remains exercised above and here.
    monkeypatch.setattr(supervisor,'controller_lease',lambda:nullcontext())
    monkeypatch.setattr(supervisor,'read_request',lambda identity:json.dumps(dict(schema=schema,orders=[])).encode())
    monkeypatch.setattr(supervisor,'claim',lambda *a,**kw:pytest.fail('nonexecuting template claimed a send'))
    monkeypatch.setattr(supervisor,'child_command',lambda *a,**kw:pytest.fail('nonexecuting template launched a role'))
    result=supervisor.run(source_commit=template['executor_release_commit'],request_sha256='f'*64,purpose='submission')
    assert result['claim_retained'] is False
    assert result['phase']=='CONFIGURATION'
    assert list((state/'claims').iterdir())==[]
