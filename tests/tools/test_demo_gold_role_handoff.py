"""Fixed role selection from an original roster; virtual custody in unit cases."""
from copy import deepcopy
import importlib
import os
from pathlib import Path

import pytest

from tests.tools.test_demo_gold_research_handoff import (
    request_case as request_case, configured as configured, fixed_public as fixed_public,
    book as book, env as env, virtual_root_fd,
)


@pytest.fixture
def role_case(request_case, tmp_path, monkeypatch):
    f = request_case
    api = importlib.import_module('tools.demo_gold_role_handoff')
    roots = {}
    for name in ('executor', 'issuer', 'controller'):
        path = tmp_path / ('original-' + name)
        path.mkdir(mode=0o700)
        roots[name] = path
    monkeypatch.setattr(api.native, 'ROLE_ROOTS', roots)
    monkeypatch.setattr(api, 'OUTPUT_ROOT', tmp_path / 'role-spool')
    monkeypatch.setattr(api, '_root_uids', lambda path: (0,) * 4)
    def custody(path, uid):
        # Only UID/GID and parents are virtual; inode/mode/link remain actual.
        info = api.native._identity(path.lstat(), directory=True)
        info.update(uid=uid, gid=uid)
        return info, []
    monkeypatch.setattr(api, '_root', custody)
    return dict(f, api=api, roots=roots)


def issue(f, namespace='executor', **changes):
    args = dict(verified_layout=f['handle'], verified_public=f['configured']['handle'],
        namespace=namespace, source_attachment=f['source'])
    args.update(changes)
    return f['api'].issue(**args)


@pytest.mark.parametrize('namespace', ['executor', 'issuer', 'controller'])
def test_fixed_role_request_selects_original_registry_and_inode(role_case, monkeypatch, namespace):
    f = role_case
    with issue(f, namespace) as request:
        value = request.document()
        expected = 0 if namespace == 'controller' else f['handle']._public['registry'][namespace + '_uid']
        assert value['namespace'] == namespace and value['uid'] == expected
        assert value['role_root'] == str(f['roots'][namespace])
        assert value['role_root_identity']['inode'] == f['roots'][namespace].stat().st_ino
        assert value['fixed_layout_sha256'] == f['api'].native._digest(f['handle'].snapshot())
        assert value['workspace'] == str(f['api'].OUTPUT_ROOT / value['operation_sha256'] / namespace)
        assert value['writer_barrier_verified'] is value['submission_enabled'] is value['economic_authority'] is False
        with monkeypatch.context() as context:
            virtual_root_fd(f['api'], context, request.fd)
            assert f['api'].read_descriptor(request.fd)[0] == value
        value['namespace'] = 'research'
        assert request.document()['namespace'] == namespace
    with pytest.raises(ValueError, match='CLOSED'):
        request.recheck()


@pytest.mark.parametrize('damage', ['research', 'arbitrary', 'dictionary', 'source', 'secret'])
def test_unbound_role_selection_rejects_before_descriptor(role_case, monkeypatch, damage):
    f = role_case
    changes = {}
    namespace = 'executor'
    if damage in ('research', 'arbitrary'):
        namespace = damage
    elif damage == 'dictionary':
        changes['verified_layout'] = f['handle'].snapshot()
    else:
        source = deepcopy(f['source'])
        if damage == 'source':
            source['identity']['commit'] = 'f' * 40
        else:
            source['bundle_path'] = str(Path(source['bundle_path']).parent / 'key' / 'secret.bundle')
        changes['source_attachment'] = source
    monkeypatch.setattr(os, 'memfd_create', lambda *a, **kw: pytest.fail('invalid selection reached descriptor'))
    with pytest.raises(ValueError):
        with issue(f, namespace, **changes):
            pytest.fail('invalid selection admitted')


@pytest.mark.parametrize('damage', ['replacement', 'mapping', 'roster'])
def test_original_replacement_or_roster_drift_invalidates_live_request(role_case, damage):
    f = role_case
    with pytest.raises(ValueError, match='CHANGED'):
        with issue(f) as request:
            path = f['roots']['executor']
            if damage == 'replacement':
                path.rename(path.with_name('old-executor'))
                path.mkdir(mode=0o700)
            elif damage == 'mapping':
                other = path.with_name('other-executor')
                other.mkdir(mode=0o700)
                f['roots']['executor'] = other
            else:
                config = f['configured']['config']
                raw = config.read_bytes()
                config.rename(config.with_name('old-layout'))
                config.write_bytes(raw)
                config.chmod(0o644)
            request.recheck()


def test_research_request_is_not_fixed_role_permission(role_case):
    f = role_case
    from tests.tools.test_demo_gold_research_handoff import issue as research_issue
    from tools import demo_gold_research_handoff as research
    with research_issue(dict(f, api=research)) as request:
        with pytest.raises(ValueError, match='CAPTURED_ROLE_REQUEST_REQUIRED'):
            f['api'].send_request(None, request, worker_pid=1, worker_start_ticks=1)


def test_role_spool_cannot_write_inside_any_original_research_root(role_case, monkeypatch):
    f = role_case
    monkeypatch.setattr(f['api'], 'OUTPUT_ROOT', f['handle'].root_for_uid(f['uid']) / 'spool')
    with pytest.raises(ValueError, match='WORKSPACE_ORIGINAL_OVERLAP'):
        with issue(f):
            pytest.fail('spool admitted inside original research state')


@pytest.mark.parametrize('namespace', ['executor', 'issuer', 'controller'])
@pytest.mark.parametrize('damage', ['wrong_uid', 'extra_group', 'capability', 'expired'])
def test_fixed_role_admission_enforces_identity_privileges_and_expiry(role_case, monkeypatch, namespace, damage):
    f = role_case
    api = f['api']
    with issue(f, namespace) as request:
        uid = request.document()['uid']
        with monkeypatch.context() as context:
            virtual_root_fd(api, context, request.fd)
            context.setattr(os, 'getresuid', lambda: (uid + 1 if damage == 'wrong_uid' else uid,) * 3)
            context.setattr(os, 'getresgid', lambda: (uid,) * 3)
            context.setattr(os, 'getgroups', lambda: [uid, uid + 1] if damage == 'extra_group' else [])
            def privileges(path):
                if damage == 'capability':
                    raise ValueError('GOLD_ROLE_HANDOFF_WORKER_PRIVILEGES')
            context.setattr(api, '_worker_privileges', privileges)
            if damage == 'expired':
                context.setattr(api.time, 'monotonic_ns', lambda: request.document()['expires_monotonic_ns'])
            context.setattr(api, '_controller_pidfd', lambda doc: pytest.fail('must fail before controller lookup'))
            with pytest.raises(ValueError, match='ROLE_IDENTITY|WORKER_PRIVILEGES|EXPIRED'):
                with api.admitted(request.fd):
                    pytest.fail('invalid worker admitted')
