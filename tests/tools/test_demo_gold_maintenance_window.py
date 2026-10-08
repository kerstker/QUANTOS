"""Gold maintenance uses the complete fixed service and research population."""
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.tools.test_demo_gold_research_handoff import request_case as request_case
from tests.tools.test_demo_gold_research_layout import configured as configured
from tests.tools.test_demo_gold_checkpoint_public import fixed_public as fixed_public
from tests.tools.test_demo_market_partition_install import book as book, env as env
from tests.tools.test_demo_maintenance_processes import proc as proc, process
from tests.tools.test_demo_maintenance_window import Runtime
from tools import demo_maintenance_window as window
from tools import demo_maintenance_services as services


@pytest.fixture
def gold_window(request_case, tmp_path, monkeypatch):
    runtime=Runtime()
    runtime.states.update({name:'inactive' for name in services.GOLD_UNITS if name not in runtime.states})
    runtime.states['quantos-gold-pilot.service']=runtime.states['quantos-research-runner.service']='active'
    monkeypatch.setattr(window,'gold_backend',runtime.backend,raising=False)
    monkeypatch.setattr(window.inhibit,'install',runtime.install)
    monkeypatch.setattr(window.inhibit,'remove',runtime.remove)
    monkeypatch.setattr(window,'reload_manager',runtime.reload)
    monkeypatch.setattr(window,'boot_id',lambda:'a'*32)
    private=tmp_path/'maintenance-private'
    private.mkdir(mode=0o700)
    root=private/'gold-maintenance'
    # The ordinary suite models only this root journal's custody. The kernel
    # suite exercises actual root and role owners in an isolated container.
    actual_lstat=Path.lstat
    def root_metadata(path,*args,**kwargs):
        info=actual_lstat(path,*args,**kwargs)
        if path==private or path.is_relative_to(private):
            values={name:getattr(info,name) for name in ('st_dev','st_ino','st_mode','st_uid','st_gid',
                'st_nlink','st_size','st_mtime_ns','st_ctime_ns')}
            values.update(st_uid=0,st_gid=0)
            return SimpleNamespace(**values)
        return info
    monkeypatch.setattr(Path,'lstat',root_metadata)
    from tools import demo_maintenance_operator as operator
    @contextmanager
    def exclusive():
        yield
    monkeypatch.setattr(operator,'exclusive',exclusive)
    window.prepare_gold(root,verified_layout=request_case['handle'],verified_public=request_case['configured']['handle'])
    return dict(root=root,runtime=runtime,f=request_case)


def test_gold_window_inhibits_full_scope_and_restores_only_original_active_units(gold_window):
    f=gold_window
    original=dict(f['runtime'].states)
    assert json.loads((f['root']/'window.json').read_text())['schema']=='GOLD_INHIBITED_SERVICE_WINDOW_V1'
    assert services.inspect_journal(f['root']/'services.json')['schema']=='GOLD_SCOPED_SERVICE_MAINTENANCE_V1'
    window.pause(f['root'])
    assert set(f['runtime'].states.values())=={'inactive'}
    window.restore(f['root'])
    assert f['runtime'].states==original
    assert f['runtime'].calls.index('remove')<min(index for index,value in enumerate(f['runtime'].calls)
        if isinstance(value,tuple) and value[0]=='start')


def test_gold_window_observation_keeps_research_writer_visible_and_not_quiescent(gold_window,proc):
    f=gold_window
    process(proc,uid=f['f']['uid'])
    window.pause(f['root'])
    result=window.check_gold_processes(f['root'],verified_layout=f['f']['handle'],verified_public=f['f']['configured']['handle'])
    assert result['status']=='SCOPED_PROCESSES_PRESENT'
    assert result['quiescence_proven'] is result['writer_barrier_verified'] is False
    assert json.loads((f['root']/'gold-process-check.json').read_text())==result


def test_gold_window_cannot_consume_the_legacy_two_uid_observer(gold_window):
    f=gold_window
    window.pause(f['root'])
    with pytest.raises(ValueError,match='LEGACY_WINDOW_REQUIRED'):
        window.check_processes(f['root'],executor_uid=1001,issuer_uid=1002)


def test_gold_window_cannot_run_legacy_archive_preparation(gold_window):
    f=gold_window
    window.pause(f['root'])
    with pytest.raises(ValueError,match='LEGACY_WINDOW_REQUIRED'):
        window.prepare_evidence(f['root'],source_commit=f['f']['configured']['public']['source_commit'])
    assert not (f['root']/'preparation.json').exists()


@pytest.mark.parametrize('field',['source_commit','fixed_layout_sha256','schema'])
def test_changed_gold_window_binding_rejected_before_service_actions(gold_window,field):
    f=gold_window
    path=f['root']/'window.json'
    value=json.loads(path.read_text())
    value[field]=('d' if value[field][0]!='d' else 'e')+value[field][1:]
    path.write_text(json.dumps(value))
    before=list(f['runtime'].calls)
    with pytest.raises(ValueError,match='WINDOW_'):
        window.pause(f['root'])
    assert f['runtime'].calls==before


def test_partial_gold_pause_remains_restorable_without_the_old_layout_handle(gold_window):
    f=gold_window
    original=dict(f['runtime'].states)
    f['runtime'].failure=('stop','quantos-gold-pilot.service')
    with pytest.raises(RuntimeError,match='service failed'):
        window.pause(f['root'])
    f['runtime'].failure=None
    window.restore(f['root'])
    assert f['runtime'].states==original


def test_observation_rejects_changed_original_layout(gold_window,proc):
    f=gold_window
    window.pause(f['root'])
    config=f['f']['configured']['config']
    config.write_bytes(config.read_bytes())
    with pytest.raises(ValueError,match='CONFIGURATION_CHANGED'):
        window.check_gold_processes(f['root'],verified_layout=f['f']['handle'],verified_public=f['f']['configured']['handle'])


def test_legacy_service_journal_cannot_replace_gold_scope(gold_window):
    f=gold_window
    path=f['root']/'services.json'
    value=json.loads(path.read_text())
    value['schema']='DEMO_SCOPED_SERVICE_MAINTENANCE_V1'
    value['units']={name:row for name,row in value['units'].items() if name in services.UNITS}
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='UNIT_SCOPE_CHANGED'):
        window.pause(f['root'])
    assert not any(isinstance(value,tuple) and value[0]=='stop' for value in f['runtime'].calls)


def test_inflight_gold_research_capture_blocks_service_restore(gold_window,proc):
    f=gold_window
    window.pause(f['root'])
    document=json.loads((f['root']/'window.json').read_text())
    observation=window.check_gold_processes(f['root'],verified_layout=f['f']['handle'],verified_public=f['f']['configured']['handle'])
    record=dict(schema='GOLD_MAINTENANCE_RESEARCH_CAPTURE_V1',window_sha256=document['inhibitor_sha256'],
        source_commit=document['source_commit'],fixed_layout_sha256=document['fixed_layout_sha256'],
        process_scope_sha256=observation['scope_sha256'],phase='RUNNING',worker_exit_verified=False)
    (f['root']/'gold-research-capture.json').write_text(json.dumps(record))
    (f['root']/'gold-research-capture.json').chmod(0o600)
    before=list(f['runtime'].calls)
    with pytest.raises(ValueError,match='WINDOW_GOLD_CAPTURE_RECOVERY_REQUIRED'):
        window.restore(f['root'])
    assert f['runtime'].calls==before


@pytest.mark.parametrize('busy',[False,True])
def test_interrupted_capture_recovery_requires_fresh_complete_uid_clearance(gold_window,proc,busy):
    f=gold_window
    window.pause(f['root'])
    observed=window.check_gold_processes(f['root'],verified_layout=f['f']['handle'],verified_public=f['f']['configured']['handle'])
    window._mark_gold_capture(f['root'],observed,'RUNNING',False)
    if busy:
        process(proc,uid=f['f']['uid'])
        with pytest.raises(ValueError,match='WINDOW_GOLD_CAPTURE_RECOVERY_REQUIRED'):
            window.recover_gold_research(f['root'],verified_layout=f['f']['handle'],verified_public=f['f']['configured']['handle'])
        assert json.loads((f['root']/'gold-research-capture.json').read_text())['phase']=='RUNNING'
        with pytest.raises(ValueError,match='WINDOW_GOLD_CAPTURE_RECOVERY_REQUIRED'):
            window.restore(f['root'])
    else:
        result=window.recover_gold_research(f['root'],verified_layout=f['f']['handle'],verified_public=f['f']['configured']['handle'])
        assert result['status']=='GOLD_CAPTURE_ABORTED_CLEANED'
        assert result['production_data_archived'] is result['worker_exit_verified'] is False
        assert json.loads((f['root']/'gold-research-capture.json').read_text())['phase']=='FAILED_CLEANED'
        window.restore(f['root'])


def test_foreign_capture_record_is_rejected_before_restoring_any_service(gold_window,proc):
    f=gold_window
    window.pause(f['root'])
    observed=window.check_gold_processes(f['root'],verified_layout=f['f']['handle'],verified_public=f['f']['configured']['handle'])
    window._mark_gold_capture(f['root'],observed,'RUNNING',False)
    path=f['root']/'gold-research-capture.json'
    value=json.loads(path.read_text())
    value['window_sha256']='f'*64
    path.write_text(json.dumps(value))
    before=list(f['runtime'].calls)
    with pytest.raises(ValueError,match='WINDOW_GOLD_CAPTURE_RECORD'):
        window.restore(f['root'])
    assert f['runtime'].calls==before
