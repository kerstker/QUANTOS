"""Actual Root restorations and peers; manager/public/release are fixtures."""
from contextlib import contextmanager
import os

import pytest

from tests.tools.test_demo_gold_all_role_capture_kernel import (
    manager_boundary as manager_boundary, role_roster as role_roster, native_roster as native_roster,
    source_attachment as source_attachment, registered_case as registered_case,
    native_research as native_research, private_files as private_files,
    fixed_public as fixed_public, book as book, env as env, sources,
)
from tools import demo_gold_all_role_capture as coordinator
from tools import demo_gold_file_leases as leases
from tools import demo_gold_restored_joins as joins


@pytest.mark.skipif(os.getuid() != 0, reason='requires isolated Root/UID kernel container')
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
def test_actual_native_restore_views_are_bound_before_all_workers_close(manager_boundary):
    b = manager_boundary
    f = b['f']
    with leases.hold(verified_layout=f['roster'], verified_public=f['public']) as held:
        result = coordinator.capture_all(verified_layout=f['roster'], verified_public=f['public'],
            source_attachments=sources(f), file_leases=held)
    report = result['restored_role_joins']
    assert report['schema'] == 'GOLD_RESTORED_ROLE_JOINS_V3'
    for key in ('source_commit', 'operation_sha256', 'fixed_layout_sha256',
            'public_bundle_sha256', 'cut_request_sha256'):
        assert report[key] == result[key]
    for name, binding in report['archives'].items():
        assert binding['archive_manifest_sha256'] == result['captures'][name]['archive_manifest_sha256']
        assert binding['inventory_sha256'] == result['captures'][name]['inventory_sha256']
    assert report['research_restore_continuity_verified'] is True
    assert set(report['research_archives']) == {'10001', '10002'}
    for uid, binding in report['research_archives'].items():
        assert binding['archive_manifest_sha256'] == result['captures']['research'][uid]['archive_manifest_sha256']
        assert binding['inventory_sha256'] == result['captures']['research'][uid]['inventory_sha256']
    assert report['issuer_executor_join_verified'] is report['retention_pair_checked'] is True
    assert report['issuer_executor']['issuer_record_count'] == 0
    assert report['issuer_executor']['executor_reservation_count'] == 0
    assert report['retention']['status'] == 'ABSENCE_VERIFIED'
    assert report['retention_pair_verified'] is False
    assert all(report[key] is False for key in ('cross_role_business_join_verified', 'writer_barrier_verified',
        'whole_set_verified', 'cash_reconciled', 'mutation_permitted', 'submission_enabled', 'economic_authority'))
    assert len(b['children']) == 5 and all(child.poll() == 0 for child in b['children'].values())


@pytest.mark.skipif(os.getuid() != 0, reason='requires isolated Root/UID kernel container')
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
def test_restored_inode_replacement_after_join_open_blocks_every_close(manager_boundary, monkeypatch):
    b = manager_boundary
    f = b['f']
    calls = []
    original = joins._grants
    close = coordinator.research.peer.ResearchPeer.close_capture
    @contextmanager
    def replaced(executor, issuer, public):
        with original(executor, issuer, public) as signed:
            path = issuer / 'issuance.sqlite3'
            replacement = path.with_suffix('.replacement')
            replacement.write_bytes(path.read_bytes())
            replacement.chmod(0o600)
            replacement.replace(path)
            yield signed
    def recorded(consumer):
        calls.append(consumer.uid)
        return close(consumer)
    monkeypatch.setattr(joins, '_grants', replaced)
    monkeypatch.setattr(coordinator.research.peer.ResearchPeer, 'close_capture', recorded)
    with pytest.raises(ValueError, match='ORIGINAL_CHANGED'):
        with leases.hold(verified_layout=f['roster'], verified_public=f['public']) as held:
            coordinator.capture_all(verified_layout=f['roster'], verified_public=f['public'],
                source_attachments=sources(f), file_leases=held)
    assert calls == []
    assert len(b['children']) == 5 and b['stopped']
    assert all(child.poll() is not None for child in b['children'].values())


@pytest.mark.skipif(os.getuid() != 0, reason='requires isolated Root/UID kernel container')
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
def test_last_research_uid_restored_inode_replacement_blocks_every_close(manager_boundary, monkeypatch):
    b = manager_boundary
    f = b['f']
    calls = []
    changed = []
    original = joins._grants
    close = coordinator.research.peer.ResearchPeer.close_capture

    @contextmanager
    def replaced(executor, issuer, public):
        with original(executor, issuer, public) as signed:
            uid = max(public['registry']['research_uids'])
            restored = executor.parents[2] / ('root-' + str(uid)) / 'restored' / 'state'
            path = next(restored.rglob('input.json'))
            before = path.stat().st_ino
            replacement = path.with_suffix('.replacement')
            replacement.write_bytes(path.read_bytes())
            replacement.chmod(path.stat().st_mode & 0o777)
            replacement.replace(path)
            assert path.stat().st_ino != before
            changed.append(uid)
            yield signed

    def recorded(consumer):
        calls.append(consumer.uid)
        return close(consumer)

    monkeypatch.setattr(joins, '_grants', replaced)
    monkeypatch.setattr(coordinator.research.peer.ResearchPeer, 'close_capture', recorded)
    with pytest.raises(ValueError, match='RESTORE_ORIGINAL_CHANGED'):
        with leases.hold(verified_layout=f['roster'], verified_public=f['public']) as held:
            coordinator.capture_all(verified_layout=f['roster'], verified_public=f['public'],
                source_attachments=sources(f), file_leases=held)
    assert changed == [max(f['roster']._public['registry']['research_uids'])]
    assert calls == []
    assert len(b['children']) == 5 and b['stopped']
    assert all(child.poll() is not None for child in b['children'].values())


@pytest.mark.skipif(os.getuid() != 0, reason='requires isolated Root/UID kernel container')
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
def test_misbound_research_peer_cannot_close_complete_uid_roster(manager_boundary, monkeypatch):
    b = manager_boundary
    f = b['f']
    calls = []
    original = joins.open_join
    close = coordinator.research.peer.ResearchPeer.close_capture

    @contextmanager
    def wrong_slot(**kwargs):
        peers = dict(kwargs['research_peers'])
        first, last = min(peers), max(peers)
        assert first != last and peers[last].uid == last
        peers[last] = peers[first]
        with original(**dict(kwargs, research_peers=peers)) as joined:
            yield joined

    def recorded(consumer):
        calls.append(consumer.uid)
        return close(consumer)

    monkeypatch.setattr(joins, 'open_join', wrong_slot)
    monkeypatch.setattr(coordinator.research.peer.ResearchPeer, 'close_capture', recorded)
    with pytest.raises(ValueError, match='COMPLETE_RESEARCH_ROLE_SCOPE'):
        with leases.hold(verified_layout=f['roster'], verified_public=f['public']) as held:
            coordinator.capture_all(verified_layout=f['roster'], verified_public=f['public'],
                source_attachments=sources(f), file_leases=held)
    assert calls == []
    assert len(b['children']) == 5 and b['stopped']
    assert all(child.poll() is not None for child in b['children'].values())
