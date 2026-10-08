"""Backup display preserves partial coverage and cannot confer trading authority."""
from copy import deepcopy
import json

import pytest

from services.research import demo_backup_status as model

NOW = 1800000000000


def report():
    summary = dict(schema='DEMO_CHECKPOINT_CYCLE_STATUS_V1', namespace='research', observed_at_ms=NOW,
        status='CURRENT', recovery_point_at_ms=NOW-120000, last_verified_at_ms=NOW-60000, rpo_seconds=86400,
        source_capture_age_ms=120000, capture_slots_remaining=509, archive_bytes_reverified=False,
        automatic_history_deletion=False, verified_cycles=1, pending_cycles=1, failed_cycles=1,
        interrupted_cycles=0, seal_failures=1, source_verified=False, cross_namespace_atomic=False,
        submission_enabled=False, economic_authority=False, production_restore_performed=False)
    rows = [dict(namespace=name, status='OBSERVED' if name=='research' else 'NO_SPOOL',
                 summary=summary if name=='research' else None,
                 capture_timer='ACTIVE' if name=='research' else 'NOT_INSTALLED',
                 seal_timer='ACTIVE' if name=='research' else 'NOT_INSTALLED') for name in model.NAMESPACES]
    return dict(schema=model.SCHEMA, environment='BINANCE_USDM_DEMO', scope='TECHNICAL_VALIDATION_ONLY',
        source_commit='c'*40, observed_at_ms=NOW, phase='PARTIAL_RECOVERY_POINTS', components=rows,
        source_verified=False, submission_enabled=False, economic_authority=False, cross_namespace_atomic=False,
        production_restore_performed=False)


def test_roundtrip_preserves_failure_and_pending_counts_without_private_fields(tmp_path):
    raw=report();path=model.save(tmp_path/'views',raw)
    assert model.load_latest(tmp_path/'views')==raw
    assert raw['components'][0]['summary']['failed_cycles']==1
    assert not any(key in path.read_text() for key in ('private_key','archive_path','account_ref_sha256'))


@pytest.mark.parametrize('change', ['authority','future','rpo','count','phase','private','timer','duplicates','null','unverified_age'])
def test_inconsistent_or_privileged_backup_display_is_rejected(change):
    raw=report();summary=raw['components'][0]['summary']
    if change=='authority':raw['submission_enabled']=True
    elif change=='future':summary['last_verified_at_ms']=NOW+1
    elif change=='rpo':summary['status']='STALE'
    elif change=='count':summary['capture_slots_remaining']=512
    elif change=='phase':raw['phase']='ALL_RECOVERY_POINTS_CURRENT'
    elif change=='private':summary['archive_path']='/secret'
    elif change=='timer':raw['components'][0]['seal_timer']='APPROVED'
    elif change=='duplicates':raw['components'][1]=deepcopy(raw['components'][0])
    elif change=='null':raw['components'][0]['status']='NO_SPOOL'
    else:summary['verified_cycles']=0
    with pytest.raises(ValueError):model.validate(raw)


def test_empty_and_expired_recovery_points_do_not_become_current():
    raw=report()
    for row in raw['components']:
        row.update(status='NO_SPOOL',summary=None,capture_timer='NOT_INSTALLED',seal_timer='NOT_INSTALLED')
    raw['phase']='NO_CURRENT_RECOVERY_POINTS'
    assert model.validate(raw)==raw
    raw=report();summary=raw['components'][0]['summary']
    summary.update(status='STALE',recovery_point_at_ms=NOW-86400001,source_capture_age_ms=86400001)
    raw['phase']='NO_CURRENT_RECOVERY_POINTS'
    assert model.validate(raw)==raw


def test_display_digest_corruption_never_exposes_unknown_fields(tmp_path):
    path=model.save(tmp_path/'views',report());path.write_text(json.dumps({'private_key':'never display'}))
    with pytest.raises(ValueError):model.load_latest(tmp_path/'views')
