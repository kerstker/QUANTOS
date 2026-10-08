"""Read fixed backup metadata and timer state without credentials or activation."""
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import demo_backup_observe as tool
from tests.research.test_demo_backup_status import report,NOW


def config(tmp_path):
    return dict(source_commit='c'*40, research_uid=os.getuid(), executor_uid=2001, issuer_uid=2002,
                research_gid=os.getgid(), research_state_root=str(tmp_path/'research'))


def layout(tmp_path,monkeypatch):
    spool=tmp_path/'spool';backup=tmp_path/'backup';spool.mkdir();backup.mkdir()
    monkeypatch.setattr(tool,'SPOOL',spool);monkeypatch.setattr(tool,'BACKUP',backup)
    monkeypatch.setattr(tool,'volume_available',lambda:True)
    monkeypatch.setattr(tool,'now_ms',lambda:NOW)
    monkeypatch.setattr(tool,'timer_status',lambda namespace,role:'ACTIVE' if namespace=='research' else 'NOT_INSTALLED')
    return spool,backup


def test_missing_spool_and_missing_backup_are_not_verified_and_do_not_create_source(tmp_path,monkeypatch):
    spool,backup=layout(tmp_path,monkeypatch)
    (spool/'research').mkdir()
    raw=tool.observe(config(tmp_path),source_commit='c'*40)
    assert raw['components'][0]['status']=='NO_BACKUP_DIRECTORY'
    assert [row['status'] for row in raw['components'][1:]]==['NO_SPOOL']*3
    assert not (backup/'research').exists() and not (spool/'issuer').exists()


def test_one_bad_namespace_keeps_other_statuses_and_discards_private_error(tmp_path,monkeypatch):
    spool,backup=layout(tmp_path,monkeypatch)
    for name in ('research','executor'):
        (spool/name).mkdir();(backup/name).mkdir()
    summary=report()['components'][0]['summary']
    def summarize(*a,**kw):
        if kw['namespace']=='executor':raise OSError('private archive detail')
        assert kw['expected_uid']==os.getuid()
        return deepcopy(summary)
    monkeypatch.setattr(tool.cycle,'summarize',summarize)
    raw=tool.observe(config(tmp_path),source_commit='c'*40)
    assert raw['components'][0]['summary']==summary
    assert raw['components'][1]['status']=='UNREADABLE' and raw['components'][1]['summary'] is None
    assert raw['phase']=='PARTIAL_RECOVERY_POINTS' and 'private archive detail' not in json.dumps(raw)


def test_unmounted_backup_volume_prevents_metadata_reads(tmp_path,monkeypatch):
    layout(tmp_path,monkeypatch)
    monkeypatch.setattr(tool,'volume_available',lambda:False)
    monkeypatch.setattr(tool.cycle,'summarize',lambda *a,**kw:pytest.fail('read unmounted backup'))
    raw=tool.observe(config(tmp_path),source_commit='c'*40)
    assert all(row['status']=='BACKUP_VOLUME_UNAVAILABLE' for row in raw['components'])


@pytest.mark.parametrize('state,expected', [('LoadState=loaded\nActiveState=active\n','ACTIVE'),
    ('LoadState=loaded\nActiveState=inactive\n','INACTIVE'),('LoadState=not-found\nActiveState=inactive\n','NOT_INSTALLED'),
    ('LoadState=loaded\nActiveState=failed\n','UNKNOWN')])
def test_only_named_timer_status_is_read_and_failures_stay_unknown(monkeypatch,state,expected):
    def run(command,**kwargs):
        assert command[:3]==['/usr/bin/systemctl','show','quantos-demo-checkpoint-research-capture.timer']
        assert kwargs['timeout']==3 and kwargs['check'] is False
        return SimpleNamespace(returncode=0,stdout=state)
    monkeypatch.setattr(tool.subprocess,'run',run)
    assert tool.timer_status('research','capture')==expected


def test_observer_has_no_secret_dependency_and_only_saves_fixed_display(tmp_path,monkeypatch):
    cfg=config(tmp_path);layout(tmp_path,monkeypatch)
    monkeypatch.setattr(tool,'configuration',lambda commit:cfg)
    monkeypatch.setattr(tool,'verify_release',lambda commit:None)
    monkeypatch.setattr(tool.os,'geteuid',lambda:0)
    monkeypatch.setattr(tool,'OUTPUT',tmp_path/'fixed-root')
    monkeypatch.setattr(tool,'protected_output',lambda root:None)
    monkeypatch.setattr(tool.cycle.checkpoint,'resolve_backup_passphrase',lambda **kw:pytest.fail('observer read secret'))
    raw=tool.run(source_commit='c'*40)
    assert raw['source_verified'] is False and raw['phase']=='NO_CURRENT_RECOVERY_POINTS'
    assert tool.model.load_latest(tmp_path/'fixed-root'/'views')==raw


def test_monitor_publication_occurs_after_saved_summary_and_keeps_it_on_delivery_failure(tmp_path,monkeypatch):
    cfg=config(tmp_path);layout(tmp_path,monkeypatch)
    monkeypatch.setattr(tool,'configuration',lambda commit:cfg)
    monkeypatch.setattr(tool,'verify_release',lambda commit:None)
    monkeypatch.setattr(tool.os,'geteuid',lambda:0)
    monkeypatch.setattr(tool,'OUTPUT',tmp_path/'fixed-root')
    monkeypatch.setattr(tool,'protected_output',lambda root:None)
    def publish(**kwargs):
        assert kwargs=={'source_commit':'c'*40,'kind':'backup'}
        assert tool.model.load_latest(tmp_path/'fixed-root'/'views')['phase']=='NO_CURRENT_RECOVERY_POINTS'
        raise ValueError('publication unavailable')
    monkeypatch.setattr(tool,'publish',publish)
    with pytest.raises(ValueError,match='publication unavailable'):
        tool.run(source_commit='c'*40,publish_display=True)


def test_monitor_unit_has_no_backup_key_and_explicit_identity_drop_capabilities():
    from scripts.render_demo_checkpoint_units import render_monitor
    units=render_monitor(release_commit='c'*40,research_state_root='/home/operator/research/state')
    service=units['quantos-demo-checkpoint-monitor.service']
    assert 'LoadCredential=' not in service and '--publish' in service
    assert 'CapabilityBoundingSet=CAP_DAC_READ_SEARCH CAP_SETUID CAP_SETGID' in service
    # DGX's actual launch retained SETGID but lost effective SETUID without
    # an explicit ambient declaration; a bounding set alone was insufficient.
    assert 'AmbientCapabilities=CAP_SETUID\n' in service
    assert 'PrivateNetwork=true' in service and 'ConditionPathIsMountPoint' not in service
    assert 'binance_demo_backup_status' in service
    assert '[Install]' not in service and '[Install]' not in units['quantos-demo-checkpoint-monitor.timer']


def test_only_the_publisher_can_inherit_the_uid_transition_capability():
    from scripts.render_demo_checkpoint_units import render
    for namespace, uid, source in (
        ('research', 1000, '/home/operator/research/state/data/cache'),
        ('executor', 1001, '/var/lib/quantos-demo-executor'),
    ):
        units = render(release_commit='c'*40, namespace=namespace, source_uid=uid, source_dir=source)
        for name, body in units.items():
            if name.endswith('.service'):
                assert 'AmbientCapabilities=\n' in body
                assert 'CAP_SETUID' not in body and 'CAP_SETGID' not in body
