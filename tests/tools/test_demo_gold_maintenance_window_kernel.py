"""Actual root lock, leases, research identities/archives/exits; isolated manager.

The fixed public/release and service-manager boundaries are synthetic. The other
three private role roots contain real owned fixtures, not their native archives.
"""
import json
import os
import select
import subprocess
import sys

import pytest

from tests.tools.test_demo_gold_file_leases_kernel import (
    lease_case as lease_case,
    native_roster as native_roster,
    source_attachment as source_attachment,
    registered_case as registered_case,
    native_research as native_research,
    private_files as private_files,
    fixed_public as fixed_public,
    book as book,
    env as env,
    manager_boundary as manager_boundary,
)
from tests.tools.test_demo_maintenance_window import Runtime
from tools import demo_maintenance_window as window
from tools import demo_maintenance_services as services


@pytest.fixture
def kernel_window(lease_case,tmp_path,monkeypatch):
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
    return dict(f=lease_case['f'],root=private/'window',runtime=runtime,original=original,system=system)


@pytest.mark.skipif(os.getuid()!=0,reason='requires isolated root with CAP_LEASE and CAP_SETPCAP')
@pytest.mark.parametrize('fixed_public',['absent'],indirect=True)
def test_complete_research_capture_uses_actual_lock_leases_uids_archives_and_exits(kernel_window,manager_boundary):
    f=kernel_window
    assert manager_boundary['f'] is f['f']
    result=window.capture_gold_research(f['root'],verified_layout=f['f']['roster'],verified_public=f['f']['public'],
        source_attachments={value['layout']['uid']:value['source'] for value in f['f']['values']})
    assert result['maintenance_window_restored'] is result['worker_exit_verified'] is result['original_file_leases_verified'] is True
    assert result['writer_barrier_verified'] is result['whole_set_verified'] is False
    assert result['submission_enabled'] is result['economic_authority'] is False
    assert f['runtime'].states==f['original']
    record=json.loads((f['root']/'gold-research-capture.json').read_text())
    assert record['phase']=='COMPLETE' and record['worker_exit_verified'] is True
    assert not list(f['system'].glob('*/90-quantos-maintenance-inhibit.conf'))
    lock=window.inhibit.CONTROL/'operator.lock'
    assert lock.stat().st_uid==0 and lock.stat().st_mode&0o777==0o600


@pytest.mark.skipif(os.getuid()!=0,reason='requires isolated root and actual research identities')
@pytest.mark.parametrize('fixed_public',['absent'],indirect=True)
def test_actual_idle_research_uid_blocks_restore_until_it_really_exits(kernel_window):
    f=kernel_window
    arguments=dict(verified_layout=f['f']['roster'],verified_public=f['f']['public'])
    window.prepare_gold(f['root'],**arguments)
    window.pause(f['root'])
    observed=window.check_gold_processes(f['root'],**arguments)
    assert observed['status']=='NO_SCOPED_PROCESS_OBSERVED'
    window._mark_gold_capture(f['root'],observed,'RUNNING',False)
    uid=f['f']['values'][1]['layout']['uid']
    child=subprocess.Popen([sys.executable,'-I','-c','import sys; print("ready",flush=True); sys.stdin.read(1)'],
        user=uid,group=uid,extra_groups=[],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert select.select([child.stdout],[],[],5)[0]
        assert child.stdout.readline().strip()=='ready'
        observed=window.check_gold_processes(f['root'],**arguments)
        assert observed['status']=='SCOPED_PROCESSES_PRESENT'
        assert any(row['pid']==child.pid and row['reasons']==['DEDICATED_UID'] for row in observed['processes'])
        with pytest.raises(ValueError,match='WINDOW_GOLD_CAPTURE_RECOVERY_REQUIRED'):
            window.restore(f['root'])
        with pytest.raises(ValueError,match='WINDOW_GOLD_CAPTURE_RECOVERY_REQUIRED'):
            window.recover_gold_research(f['root'],**arguments)
        assert not any(isinstance(value,tuple) and value[0]=='start' for value in f['runtime'].calls)
    finally:
        child.stdin.write('x')
        child.stdin.close()
        child.wait(timeout=10)
    assert child.returncode==0
    result=window.recover_gold_research(f['root'],**arguments)
    assert result['worker_exit_verified'] is result['production_data_archived'] is False
    assert json.loads((f['root']/'gold-research-capture.json').read_text())['phase']=='FAILED_CLEANED'
    window.restore(f['root'])
    assert f['runtime'].states==f['original']


@pytest.mark.skipif(os.getuid()!=0,reason='requires isolated root with CAP_LEASE and CAP_SETPCAP')
@pytest.mark.parametrize('fixed_public',['absent'],indirect=True)
def test_failure_after_native_prepare_reclaims_actual_workers_before_service_restore(kernel_window,manager_boundary,monkeypatch):
    from tools import demo_gold_research_launcher as launcher
    f=kernel_window
    original=launcher.peer.ResearchPeer.prepare
    def interrupted(self):
        original(self)
        raise ValueError('SYNTHETIC_FAILURE_AFTER_ACTUAL_NATIVE_PREPARE')
    monkeypatch.setattr(launcher.peer.ResearchPeer,'prepare',interrupted)
    with pytest.raises(ValueError,match='SYNTHETIC_FAILURE_AFTER_ACTUAL_NATIVE_PREPARE'):
        window.capture_gold_research(f['root'],verified_layout=f['f']['roster'],verified_public=f['f']['public'],
            source_attachments={value['layout']['uid']:value['source'] for value in f['f']['values']})
    assert manager_boundary['children'] and all(child.poll() is not None for child in manager_boundary['children'].values())
    assert f['runtime'].states==f['original']
    record=json.loads((f['root']/'gold-research-capture.json').read_text())
    assert record['phase']=='FAILED_CLEANED' and record['worker_exit_verified'] is False
    assert not list(f['system'].glob('*/90-quantos-maintenance-inhibit.conf'))
