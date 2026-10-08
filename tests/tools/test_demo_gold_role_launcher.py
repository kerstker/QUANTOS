"""Fixed-role launcher scope and rejection; no host manager operations."""
import importlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.tools.test_demo_gold_role_handoff import role_case as role_case, issue
from tests.tools.test_demo_gold_research_handoff import (
    request_case as request_case, configured as configured, fixed_public as fixed_public,
    book as book, env as env,
)


def module():
    return importlib.import_module('tools.demo_gold_role_launcher')


@pytest.mark.parametrize('namespace', ['executor', 'issuer', 'controller'])
def test_fixed_command_isolated_role_and_exact_runtime(role_case, namespace):
    f = role_case
    api = module()
    with issue(f, namespace) as request:
        doc = request.document()
        argv = api.command(request)
        properties = dict(item.removeprefix('--property=').split('=', 1)
            for item in argv if item.startswith('--property='))
        assert api.unit_for(request).endswith('-' + namespace + '.service')
        assert properties['ReadWritePaths'] == '"' + doc['workspace'] + '"'
        assert properties['ProtectSystem'] == 'strict' and properties['PrivateNetwork'] == 'yes'
        assert properties['NoNewPrivileges'] == 'yes' and properties['ProtectHome'] == 'tmpfs'
        assert properties['RestrictAddressFamilies'] == 'AF_UNIX'
        assert properties['CapabilityBoundingSet'] == 'CAP_SETUID CAP_SETGID CAP_SETPCAP'
        assert properties['AmbientCapabilities'] == properties['SupplementaryGroups'] == ''
        assert doc['role_root'] in properties['BindReadOnlyPaths']
        for other, root in f['roots'].items():
            if other != namespace:
                assert str(root) in properties['InaccessiblePaths']
        for row in f['handle']._document['research_roots']:
            assert row['root'] in properties['InaccessiblePaths']
        assert '/run/credentials' in properties['InaccessiblePaths']
        assert '/etc/ssh' in properties['InaccessiblePaths']
        assert '--reuid=' + str(doc['uid']) in argv and '--regid=' + str(doc['uid']) in argv
        assert '--clear-groups' in argv and '--bounding-set=-all' in argv
        assert 'ALLOW_ORDER_SUBMISSION=false' in argv and 'REQUIRE_HUMAN_APPROVAL=true' in argv
        assert argv[-4:] == ['-m', 'tools.demo_gold_role_capture_worker', '--control-fd', '0']
        assert '/opt/quantos/venvs/' + doc['source_commit'] + '/bin/python' in argv


def test_unaccepted_role_release_fails_before_process_or_workspace(role_case, monkeypatch):
    api = module()
    def reject(commit):
        raise ValueError('UNACCEPTED_RELEASE')
    monkeypatch.setattr(api, 'verify_source', reject)
    monkeypatch.setattr(api.subprocess, 'Popen', lambda *a, **kw: pytest.fail('unaccepted release launch'))
    with issue(role_case) as request:
        with pytest.raises(ValueError, match='UNACCEPTED_RELEASE'):
            with api.launch(request):
                pytest.fail('unaccepted release admitted')
        assert not Path(request.document()['workspace']).exists()


@pytest.mark.parametrize('unit', ['quantos-gold-pilot.service', 'quantos-research-runner.service',
    'quantos-gold-research-' + 'a' * 32 + '-1001.service', 'quantos-gold-role-' + 'a' * 32 + '-research.service'])
def test_arbitrary_or_research_units_rejected_before_manager_call(unit, monkeypatch):
    api = module()
    monkeypatch.setattr(api.subprocess, 'run', lambda *a, **kw: pytest.fail('unowned unit lookup'))
    with pytest.raises(ValueError, match='WORKER_UNIT'):
        api.show(unit)


def test_foreign_role_unit_cannot_be_stopped(role_case, monkeypatch):
    api = module()
    with issue(role_case, 'controller') as request:
        monkeypatch.setattr(api, 'show', lambda unit: dict(LoadState='loaded', Description='foreign'))
        monkeypatch.setattr(api.subprocess, 'run', lambda *a, **kw: pytest.fail('foreign stop'))
        with pytest.raises(ValueError, match='WORKER_OWNERSHIP'):
            api.stop_owned(request)


def test_expired_request_can_only_stop_original_role_unit(role_case, monkeypatch):
    api = module()
    calls = []
    with issue(role_case, 'controller') as request:
        unit = api.unit_for(request)
        monkeypatch.setattr(api, 'show', lambda _: dict(LoadState='loaded',
            Description=api.description(request.request_sha256), ControlGroup='/system.slice/' + unit))
        monkeypatch.setattr(api.subprocess, 'run', lambda argv, **kw: calls.append(argv))
        with monkeypatch.context() as expired:
            expired.setattr(api.handoff.time, 'monotonic_ns', lambda: request.document()['expires_monotonic_ns'])
            with pytest.raises(ValueError, match='EXPIRED'):
                request.recheck()
            api.stop_owned(request)
        assert calls == [['/usr/bin/systemctl', 'stop', unit]]


@pytest.mark.parametrize('damage', ['populated', 'missing_events', 'changed_owner'])
def test_role_drain_requires_stable_original_empty_cgroup(role_case, tmp_path, monkeypatch, damage):
    api = module()
    with issue(role_case, 'controller') as request:
        unit = api.unit_for(request)
        root = tmp_path / 'cgroup'
        path = root / 'system.slice' / unit / 'cgroup.events'
        path.parent.mkdir(parents=True)
        path.write_text('populated 1\n' if damage == 'populated' else 'populated 0\n')
        if damage == 'missing_events':
            path.unlink()
        value = dict(LoadState='loaded', ActiveState='inactive', SubState='dead', MainPID='0',
            ExecMainPID='123', ExecMainCode='1', ExecMainStatus='0', ControlGroup='/system.slice/' + unit,
            Description=api.description(request.request_sha256))
        calls = []
        def show(unit):
            calls.append(unit)
            return dict(value, Description='foreign' if damage == 'changed_owner' and len(calls) > 1
                else value['Description'])
        monkeypatch.setattr(api, 'show', show)
        monkeypatch.setattr(api, 'CGROUP', root)
        monkeypatch.setattr(api, '_cgroup_owner', lambda info: None)
        if damage == 'populated':
            assert api.drained(request) is False
        else:
            with pytest.raises(ValueError):
                api.drained(request)


@pytest.mark.parametrize('missing', ['executor', 'issuer', 'controller'])
def test_three_role_capture_rejects_missing_source_before_any_launch(role_case, monkeypatch, missing):
    api = module()
    sources = {name: role_case['source'] for name in ('executor', 'issuer', 'controller') if name != missing}
    monkeypatch.setattr(api, 'launch', lambda *a, **kw: pytest.fail('incomplete set launch'))
    with pytest.raises(ValueError, match='COMPLETE_FIXED_ROLE_SCOPE'):
        api.capture_all(verified_layout=role_case['handle'], verified_public=role_case['configured']['handle'],
            source_attachments=sources)


def test_controller_waits_for_same_uid_startup_group_drop_before_admission(role_case, monkeypatch):
    api = module()
    with issue(role_case, 'controller') as request:
        pid, ticks = api.handoff.process_identity(Path('/proc') / str(os.getpid()))
        identity = dict(uid=0, gid=0, groups=[], pid=pid, start_ticks=ticks)
        calls = []
        def process_worker(*args):
            calls.append(args)
            if len(calls) == 1:
                raise ValueError('GOLD_RESEARCH_HANDOFF_WORKER_IDENTITY')
            return identity
        unit = api.unit_for(request)
        monkeypatch.setattr(api, 'process_worker', process_worker)
        monkeypatch.setattr(api, '_worker_sandbox', lambda path: None)
        monkeypatch.setattr(api, '_metadata', lambda path: '0::/system.slice/' + unit + '\n')
        monkeypatch.setattr(api, 'show', lambda unit: dict(LoadState='loaded', ActiveState='active',
            SubState='running', MainPID=str(pid), Description=api.description(request.request_sha256),
            ControlGroup='/system.slice/' + unit))
        assert api._worker(request, SimpleNamespace(poll=lambda: None)) == identity
        assert len(calls) == 2


def test_controller_persistent_startup_groups_remain_rejected(role_case, monkeypatch):
    api = module()
    with issue(role_case, 'controller') as request:
        pid, _ = api.handoff.process_identity(Path('/proc') / str(os.getpid()))
        def reject(*args):
            raise ValueError('GOLD_RESEARCH_HANDOFF_WORKER_IDENTITY')
        unit = api.unit_for(request)
        monkeypatch.setattr(api, 'START_TIMEOUT', 0.01)
        monkeypatch.setattr(api, 'process_worker', reject)
        monkeypatch.setattr(api, 'show', lambda _: dict(LoadState='loaded', ActiveState='active',
            SubState='running', MainPID=str(pid), Description=api.description(request.request_sha256),
            ControlGroup='/system.slice/' + unit))
        with pytest.raises(ValueError, match='WORKER_IDENTITY'):
            api._worker(request, SimpleNamespace(poll=lambda: None))


def test_stop_rejection_still_reaps_owned_wrapper_and_closes_pidfds(role_case, monkeypatch):
    api = module()
    # The child and pidfds are real. Worker admission and service-manager
    # metadata are explicit virtual boundaries in this ordinary-UID case.
    monkeypatch.setattr(api, '_preflight', lambda request: request.document())
    monkeypatch.setattr(api, '_workspace', lambda request: None)
    monkeypatch.setattr(api, 'command', lambda request: [sys.executable, '-I', '-c',
        'import time; time.sleep(60)'])
    children, descriptors = [], []
    popen, pidfd_open = subprocess.Popen, os.pidfd_open
    def start(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child
    def open_pidfd(*args, **kwargs):
        descriptor = pidfd_open(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor
    def worker(request, process):
        pid, ticks = api.handoff.process_identity(Path('/proc') / str(process.pid))
        return dict(pid=pid, start_ticks=ticks, uid=request.document()['uid'],
            gid=request.document()['uid'], groups=[])
    monkeypatch.setattr(api.subprocess, 'Popen', start)
    monkeypatch.setattr(api.os, 'pidfd_open', open_pidfd)
    monkeypatch.setattr(api, '_worker', worker)
    monkeypatch.setattr(api, 'process_worker', lambda pid, ticks, uid:
        dict(pid=pid, start_ticks=ticks, uid=uid, gid=uid, groups=[]))
    monkeypatch.setattr(api.peer, 'connect', lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(api.shared, 'WRAPPER_WAIT_TIMEOUT', 0.05)
    def reject(request):
        raise ValueError('WORKER_OWNERSHIP')
    monkeypatch.setattr(api, 'stop_owned', reject)
    try:
        with issue(role_case, 'controller') as request:
            with pytest.raises(ValueError, match='WORKER_OWNERSHIP'):
                with api.launch(request):
                    raise RuntimeError('capture failed')
        assert children[0].poll() is not None
        assert descriptors
        for descriptor in descriptors:
            with pytest.raises(OSError):
                os.fstat(descriptor)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass
