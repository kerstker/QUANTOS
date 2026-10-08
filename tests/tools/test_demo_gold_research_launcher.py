"""Root capture launch scope and manager boundaries, no host service mutations."""
import importlib
import array
import os
from pathlib import Path
import subprocess
import socket
import sys
from types import SimpleNamespace

import pytest

from tests.tools.test_demo_gold_research_handoff import request_case as request_case, issue
from tests.tools.test_demo_gold_research_layout import configured as configured
from tests.tools.test_demo_gold_checkpoint_public import fixed_public as fixed_public
from tests.tools.test_demo_market_partition_install import book as book, env as env


def module():
    return importlib.import_module('tools.demo_gold_research_launcher')


def test_default_command_only_writes_selected_spool_and_keeps_approved_runtime(request_case):
    api = module()
    f = request_case
    with issue(f) as request:
        command = api.command(request)
        properties = dict(row.removeprefix('--property=').split('=', 1)
            for row in command if row.startswith('--property='))
        # Only the fixed setpriv binary runs with these startup capabilities;
        # Python runs after the UID/GID, group and capability drop.
        assert properties['User'] == properties['Group'] == '0'
        assert properties['ReadWritePaths'] == '"' + request.document()['workspace'] + '"'
        assert properties['ProtectSystem'] == 'strict'
        assert properties['ProtectHome'] == 'tmpfs'
        assert properties['PrivateNetwork'] == 'yes'
        assert properties['RestrictAddressFamilies'] == 'AF_UNIX'
        assert properties['CapabilityBoundingSet'] == 'CAP_SETUID CAP_SETGID CAP_SETPCAP'
        assert properties['AmbientCapabilities'] == ''
        assert '--clear-groups' in command and '--bounding-set=-all' in command
        assert '--reuid=' + str(f['uid']) in command and '--regid=' + str(f['uid']) in command
        assert properties['SupplementaryGroups'] == ''
        assert properties['KillMode'] == 'control-group'
        assert properties['TemporaryFileSystem'] == '/etc/quantos:ro,mode=0755'
        assert request.document()['research_root'] in properties['BindReadOnlyPaths']
        assert f['source']['source_root'] in properties['BindReadOnlyPaths']
        assert f['source']['bundle_path'] in properties['BindReadOnlyPaths']
        assert '--pipe' in command and '--wait' in command and '--collect' in command
        assert command[-4:] == ['-m', 'tools.demo_gold_research_capture_worker', '--control-fd', '0']
        assert 'ALLOW_ORDER_SUBMISSION=false' in command and 'REQUIRE_HUMAN_APPROVAL=true' in command
        assert 'PYTHONPATH=/opt/quantos/releases/' + request.document()['source_commit'] in command
    with pytest.raises(ValueError):
        api.command(request.document())


def test_unaccepted_release_rejects_before_workspace_or_process_creation(request_case, monkeypatch):
    api = module()
    f = request_case
    calls = []
    def reject(source):
        calls.append(source)
        raise ValueError('UNACCEPTED_RELEASE')
    monkeypatch.setattr(api, 'verify_source', reject)
    monkeypatch.setattr(api.subprocess, 'Popen', lambda *a, **k: pytest.fail('no unaccepted source process'))
    with issue(f) as request:
        with pytest.raises(ValueError, match='UNACCEPTED_RELEASE'):
            with api.launch(request):
                pytest.fail('unaccepted release was launched')
        assert not Path(request.document()['workspace']).exists()
        assert calls == [request.document()['source_commit']]


@pytest.mark.parametrize('bad', ['dictionary', 'other_unit', 'boolean_uid'])
def test_launcher_cannot_accept_caller_units_or_roles(request_case, bad):
    api = module()
    f = request_case
    with issue(f) as request:
        if bad == 'dictionary':
            with pytest.raises(ValueError):
                api.command(request.document())
        elif bad == 'other_unit':
            with pytest.raises(ValueError):
                api.show('quantos-gold-account-monitor.service')
        else:
            with pytest.raises(ValueError):
                api._unit('quantos-gold-research-' + 'a' * 32 + '-True.service')


@pytest.mark.parametrize('damage', ['live_descendant', 'missing_events', 'replaced_unit', 'foreign_group'])
def test_finish_requires_original_empty_cgroup_not_just_main_process_exit(request_case, monkeypatch, tmp_path, damage):
    api = module()
    with issue(request_case) as request:
        unit = api.unit_for(request)
        root = tmp_path / 'cgroup'
        directory = root / 'system.slice' / unit
        directory.mkdir(parents=True)
        path = directory / 'cgroup.events'
        path.write_text('populated 1\n' if damage == 'live_descendant' else 'populated 0\n')
        if damage == 'missing_events':
            path.unlink()
        original = dict(LoadState='loaded', ActiveState='inactive', SubState='dead', MainPID='0',
            ExecMainPID='123', ExecMainCode='1', ExecMainStatus='0',
            ControlGroup='/system.slice/' + unit, Description=api.description(request.request_sha256))
        if damage == 'foreign_group':
            original['ControlGroup'] = '/system.slice/other.service'
        calls = []
        def show(_):
            calls.append(1)
            return dict(original, Description='foreign' if damage == 'replaced_unit' and len(calls) > 1
                else original['Description'])
        monkeypatch.setattr(api, 'CGROUP', root)
        monkeypatch.setattr(api, 'show', show)
        monkeypatch.setattr(api, '_cgroup_owner', lambda info: None)  # virtual root-owned cgroup boundary
        if damage == 'live_descendant':
            assert api.drained(request) is False
        else:
            with pytest.raises(ValueError):
                api.drained(request)


def test_foreign_job_is_never_stopped(request_case, monkeypatch):
    api = module()
    with issue(request_case) as request:
        monkeypatch.setattr(api, 'show', lambda _: dict(LoadState='loaded', Description='foreign'))
        monkeypatch.setattr(api.subprocess, 'run', lambda *a, **k: pytest.fail('foreign unit must survive'))
        with pytest.raises(ValueError, match='WORKER_OWNERSHIP'):
            api.stop_owned(request)


def test_collected_unit_requires_cgroup_absence_and_stable_manager_identity(request_case, monkeypatch, tmp_path):
    api = module()
    with issue(request_case) as request:
        unit = api.unit_for(request)
        monkeypatch.setattr(api, 'CGROUP', tmp_path)
        monkeypatch.setattr(api, 'show', lambda _: dict(LoadState='not-found', ActiveState='inactive', SubState='dead',
            MainPID='0', ExecMainPID='0', ExecMainCode='0', ExecMainStatus='0', ControlGroup='', Description=unit))
        assert api.drained(request) is True
        (tmp_path / 'system.slice' / unit).mkdir(parents=True)
        with pytest.raises(ValueError, match='CGROUP'):
            api.drained(request)


def test_unit_spelling_never_expands_a_path_specifier_or_argument(request_case):
    api = module()
    assert api._property_path(Path('/home/research space/100%/data')) == '"/home/research space/100%%/data"'
    for text in ('/home/research\nother', '/home/research:other', '/home/../etc'):
        with pytest.raises(ValueError):
            api._property_path(Path(text))


def test_expired_capture_can_stop_its_original_unit_without_new_capture_permission(request_case, monkeypatch):
    api = module()
    calls = []
    with issue(request_case) as request:
        unit = api.unit_for(request)
        monkeypatch.setattr(api, 'show', lambda name: dict(LoadState='loaded',
            Description=api.description(request.request_sha256), ControlGroup='/system.slice/' + unit))
        monkeypatch.setattr(api.subprocess, 'run', lambda argv, **kwargs: calls.append(argv))
        with monkeypatch.context() as expired:
            expired.setattr(api.handoff.time, 'monotonic_ns', lambda: request.document()['expires_monotonic_ns'])
            with pytest.raises(ValueError, match='EXPIRED'):
                request.recheck()
            api.stop_owned(request)
        assert calls == [['/usr/bin/systemctl', 'stop', unit]]


def test_stop_rejection_still_reaps_owned_wrapper_and_closes_pidfds(request_case, monkeypatch):
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
    monkeypatch.setattr(api.peer, 'process_worker', lambda pid, ticks, uid:
        dict(pid=pid, start_ticks=ticks, uid=uid, gid=uid, groups=[]))
    monkeypatch.setattr(api.peer, 'connect', lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(api, 'WRAPPER_WAIT_TIMEOUT', 0.05, raising=False)
    def reject(request):
        raise ValueError('WORKER_OWNERSHIP')
    monkeypatch.setattr(api, 'stop_owned', reject)
    try:
        with issue(request_case) as request:
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


def test_actual_closed_seqpacket_endpoint_is_accepted_as_eof():
    api = module()
    parent, inherited = api.handoff.channel_pair()
    inherited.close()
    try:
        api._eof(parent, 1)
    finally:
        parent.close()


def test_trailing_descriptor_is_rejected_and_reclaimed():
    api = module()
    parent, inherited = api.handoff.channel_pair()
    descriptor = os.memfd_create('isolated-trailing-fd', os.MFD_CLOEXEC)
    try:
        inherited.sendmsg([b'x'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
            array.array('i', [descriptor]).tobytes())])
        inherited.close()
        before = set(Path('/proc/self/fd').iterdir())
        with pytest.raises(ValueError, match='WORKER_EXTRA_OUTPUT'):
            api._eof(parent, 1)
        assert set(Path('/proc/self/fd').iterdir()) == before
    finally:
        inherited.close()
        parent.close()
        os.close(descriptor)


@pytest.mark.parametrize('file_leases', [True, {}, {'original_file_leases_verified': True}])
def test_capture_cannot_replace_live_file_leases_with_claimed_status(request_case, monkeypatch, file_leases):
    api = module()
    f = request_case
    monkeypatch.setattr(api, 'launch', lambda *args, **kwargs: pytest.fail('claimed status must not launch'))
    with pytest.raises(ValueError, match='LIVE_FILE_LEASES_REQUIRED'):
        api.capture_all(verified_layout=f['handle'], verified_public=f['configured']['handle'],
            source_attachments={}, file_leases=file_leases)
