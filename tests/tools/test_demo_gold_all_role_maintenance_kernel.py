"""Actual complete-role window/lock/leases/native exits; isolated manager only."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import select
import subprocess
import sys

import pytest

from tests.tools.test_demo_gold_all_role_capture_kernel import (
 manager_boundary as manager_boundary, role_roster as role_roster, native_roster as native_roster,
 source_attachment as source_attachment, registered_case as registered_case, native_research as native_research,
 private_files as private_files, fixed_public as fixed_public, book as book, env as env, sources,
)
from tests.tools.test_demo_maintenance_window import Runtime
from tools import demo_gold_all_role_maintenance as api
from tools import demo_maintenance_window as window
from tools import demo_maintenance_services as services
from tests.tools.test_demo_gold_role_capture_kernel import restricted

@pytest.fixture
def kernel_window(manager_boundary,tmp_path,monkeypatch):
    runtime=Runtime()
    runtime.states.update({name:'inactive' for name in services.GOLD_UNITS if name not in runtime.states})
    runtime.states['quantos-gold-pilot.service']=runtime.states['quantos-research-runner.service']='active'
    original=dict(runtime.states)
    system=tmp_path/'systemd-fixture'
    system.mkdir(mode=0o755)
    monkeypatch.setattr(window.inhibit,'SYSTEM',system)
    monkeypatch.setattr(window.inhibit,'CONTROL',tmp_path/'inhibitors')
    real_install,real_remove=window.inhibit.install,window.inhibit.remove
    def install(token,*,units=services.UNITS):
        result=real_install(token,units=units)
        runtime.install(token,units=units)
        return result
    def remove(token,*,units=services.UNITS):
        result=real_remove(token,units=units)
        runtime.remove(token,units=units)
        return result
    monkeypatch.setattr(window.inhibit,'install',install)
    monkeypatch.setattr(window.inhibit,'remove',remove)
    monkeypatch.setattr(window,'reload_manager',runtime.reload)
    monkeypatch.setattr(window,'gold_backend',runtime.backend)
    private=tmp_path/'maintenance-private'
    private.mkdir(mode=0o700)
    return dict(f=manager_boundary['f'],root=private/'window',runtime=runtime,original=original,system=system)




def arguments(f):
    return dict(verified_layout=f['f']['roster'], verified_public=f['f']['public'])


def journal(f):
    return json.loads((f['root']/'gold-all-role-capture.json').read_text())


def start_idle(f):
    uid = f['f']['values'][1]['layout']['uid']
    child = subprocess.Popen([sys.executable,'-I','-c',
        'import sys; print("ready",flush=True); sys.stdin.read(1)'], user=uid,group=uid,extra_groups=[],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    assert select.select([child.stdout],[],[],5)[0]
    assert child.stdout.readline().strip() == 'ready'
    return child


def exit_idle(child):
    child.stdin.write('x')
    child.stdin.close()
    child.wait(timeout=10)
    assert child.returncode == 0


def rewrite_registry(f, manager_boundary):
    value = f['f']['values'][0]
    path = Path(value['root']) / 'registry.db'
    before = path.stat().st_mtime_ns
    program = 'import sys;from pathlib import Path;p=Path(sys.argv[1]);p.write_bytes(p.read_bytes())'
    child = manager_boundary['actual_popen'](restricted(value['layout']['uid'], program, str(path)),
        cwd='/code', env=dict(PATH='/usr/bin:/bin'), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = child.communicate(timeout=10)
    assert child.returncode == 0, (stdout, stderr)
    assert path.stat().st_mtime_ns != before


@pytest.mark.skipif(os.getuid()!=0,reason='requires isolated root and actual research UID with CAP_LEASE/SETPCAP')
@pytest.mark.parametrize('fixed_public',['absent'],indirect=True)
def test_actual_shutdown_rewrite_is_cut_after_pause_using_only_installed_scope(
        kernel_window, manager_boundary, monkeypatch):
    from tools import demo_gold_research_layout as roster
    from tools import demo_supervisor as supervisor
    f = kernel_window
    original = f['f']['roster']
    scope = roster.capture_installed_scope(verified_public=f['f']['public'],
        public_bundle=original._public, cut_request=original._cut, guard=original._guard)
    factory = window.gold_backend
    observed = []
    stopped_at = f['f']['public']._snapshot['captured_at_ms'] + 1

    def backend(*args, **kwargs):
        runtime = factory(*args, **kwargs)
        change = runtime.change
        def flush(action, name):
            change(action, name)
            if action == 'stop' and name == 'quantos-research-runner.service' and not observed:
                rewrite_registry(f, manager_boundary)
                observed.append(name)
                monkeypatch.setattr(supervisor, 'now_ms', lambda: stopped_at)
        runtime.change = flush
        return runtime

    monkeypatch.setattr(window, 'gold_backend', backend)
    result = api.capture(f['root'], verified_scope=scope, verified_public=f['f']['public'],
        source_attachments=sources(f['f']))
    assert observed == ['quantos-research-runner.service']
    with pytest.raises(ValueError, match='ORIGINAL_CHANGED'):
        original.recheck()
    assert result['data_snapshot_after_service_pause'] is True
    assert result['cut_opening_at_ms'] == stopped_at
    assert result['native_four_role_archives_verified'] is result['original_file_leases_verified'] is True
    assert result['writer_barrier_verified'] is result['whole_set_verified'] is False
    assert len(manager_boundary['children']) == 5
    for request in manager_boundary['requests'].values():
        document = request.document()
        assert document['cut_request']['opening_at_ms'] == stopped_at
        assert document['public_bundle']['records']['fixed_public']['captured_at_ms'] == stopped_at
    assert f['runtime'].states == f['original']


@pytest.mark.skipif(os.getuid()!=0,reason='requires isolated root, actual original UIDs and CAP_LEASE/SETPCAP')
@pytest.mark.parametrize('fixed_public',['absent'],indirect=True)
def test_full_maintenance_captures_all_five_native_workers_then_restores_original_services(kernel_window,manager_boundary):
    f = kernel_window
    result = api.capture(f['root'], **arguments(f), source_attachments=sources(f['f']))
    assert result['roles'] == ['executor','issuer','controller','research']
    assert result['research_uids'] == [10001,10002]
    assert result['maintenance_window_restored'] is result['native_four_role_archives_verified'] is True
    assert result['worker_exit_verified'] is result['original_file_leases_verified'] is True
    assert result['writer_barrier_verified'] is result['whole_set_verified'] is False
    assert result['cross_role_business_join_verified'] is result['economic_authority'] is result['submission_enabled'] is False
    assert journal(f)['phase'] == 'COMPLETE' and journal(f)['capture_result_sha256'] is not None
    assert len(manager_boundary['children']) == 5
    assert all(child.poll()==0 for child in manager_boundary['children'].values())
    assert f['runtime'].states == f['original']
    assert not list(f['system'].glob('*/90-quantos-maintenance-inhibit.conf'))
    lock = window.inhibit.CONTROL/'operator.lock'
    assert lock.stat().st_uid == 0 and lock.stat().st_mode&0o777 == 0o600


@pytest.mark.skipif(os.getuid()!=0,reason='requires isolated root and actual research UID')
@pytest.mark.parametrize('fixed_public',['absent'],indirect=True)
def test_actual_idle_uid_blocks_full_capture_recovery_and_original_restore(kernel_window):
    f = kernel_window
    window.prepare_gold(f['root'], **arguments(f))
    window.pause(f['root'])
    observed = window.check_gold_processes(f['root'], **arguments(f))
    api._mark(f['root'],observed,f['f']['roster'],'RUNNING')
    child = start_idle(f)
    try:
        with pytest.raises(ValueError,match='WINDOW_GOLD_ALL_ROLE_RECOVERY_REQUIRED'):
            api.recover(f['root'], **arguments(f))
        with pytest.raises(ValueError,match='WINDOW_GOLD_ALL_ROLE_RECOVERY_REQUIRED'):
            window.restore(f['root'])
        assert journal(f)['phase'] == 'RUNNING'
        assert not any(isinstance(row,tuple) and row[0]=='start' for row in f['runtime'].calls)
    finally:
        exit_idle(child)
    result = api.recover(f['root'], **arguments(f))
    assert result['worker_exit_verified'] is result['native_four_role_archives_verified'] is False
    assert journal(f)['phase'] == 'FAILED_CLEANED'
    window.restore(f['root'])
    assert f['runtime'].states == f['original']


@pytest.mark.skipif(os.getuid()!=0,reason='requires isolated root and CAP_LEASE/SETPCAP')
@pytest.mark.parametrize('fixed_public',['absent'],indirect=True)
def test_last_actual_native_recheck_failure_reaps_all_five_before_failed_cleanup(kernel_window,manager_boundary,monkeypatch):
    from tools import demo_gold_research_capture_peer as peer
    f = kernel_window
    original = peer.ResearchPeer.recheck
    def interrupted(consumer):
        original(consumer)
        if type(consumer) is peer.ResearchPeer and consumer.uid == 10002:
            raise ValueError('INJECTED_AFTER_LAST_NATIVE_RECHECK')
    monkeypatch.setattr(peer.ResearchPeer,'recheck',interrupted)
    with pytest.raises(ValueError,match='INJECTED_AFTER_LAST_NATIVE_RECHECK'):
        api.capture(f['root'], **arguments(f), source_attachments=sources(f['f']))
    assert len(manager_boundary['children']) == 5
    assert all(child.poll() is not None for child in manager_boundary['children'].values())
    assert journal(f)['phase'] == 'FAILED_CLEANED'
    assert journal(f)['worker_exit_verified'] is journal(f)['native_four_role_archives_verified'] is False
    assert journal(f)['capture_result_sha256'] is None
    assert f['runtime'].states == f['original']


@pytest.mark.skipif(os.getuid()!=0,reason='requires isolated root and actual original UIDs with CAP_LEASE/SETPCAP')
@pytest.mark.parametrize('fixed_public',['absent'],indirect=True)
def test_actual_uid_appearing_after_lease_release_blocks_success_and_restore(kernel_window,manager_boundary,monkeypatch):
    f = kernel_window
    original = api.leases.hold
    children = []
    @contextmanager
    def release_then_idle(**kw):
        with original(**kw) as held:
            yield held
        children.append(start_idle(f))
    monkeypatch.setattr(api.leases,'hold',release_then_idle)
    try:
        with pytest.raises(ValueError,match='WINDOW_GOLD_ALL_ROLE_RECOVERY_REQUIRED'):
            api.capture(f['root'], **arguments(f), source_attachments=sources(f['f']))
        assert len(manager_boundary['children']) == 5
        assert all(child.poll()==0 for child in manager_boundary['children'].values())
        assert journal(f)['phase'] == 'RUNNING'
        assert journal(f)['capture_result_sha256'] is None
        assert not any(isinstance(row,tuple) and row[0]=='start' for row in f['runtime'].calls)
    finally:
        for child in children:
            exit_idle(child)
    original_record = journal(f)
    rewrite_registry(f, manager_boundary)
    with pytest.raises(ValueError, match='ORIGINAL_CHANGED'):
        f['f']['roster'].recheck()
    recovered = api.recover(f['root'], **arguments(f))
    assert recovered['production_data_archived'] is recovered['worker_exit_verified'] is False
    assert all(journal(f)[key] == original_record[key] for key in (
        'window_sha256', 'source_commit', 'fixed_layout_sha256', 'cut_request_sha256', 'public_bundle_sha256'))
    assert journal(f)['phase'] == 'FAILED_CLEANED'
    window.restore(f['root'])
    assert f['runtime'].states == f['original']
