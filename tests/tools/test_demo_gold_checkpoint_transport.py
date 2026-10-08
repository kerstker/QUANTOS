"""Real Gold namespace archives retain source and raw database/WAL bytes."""
import json
import os
from pathlib import Path
import shutil
import sqlite3

import pytest

from tools import demo_namespace_checkpoint as checkpoint
from tests.tools import test_demo_gold_namespace_checkpoint as native

capture = native.capture
hashes = native.hashes
gold_namespace = native.gold_namespace
bar_archive_namespace = native.bar_archive_namespace
private_files = native.private_files
source_attachment = native.source_attachment

SECRET = 'isolated-gold-checkpoint-test-passphrase'


def test_transport_passes_original_cut_preimage_to_native_verifier(gold_namespace, monkeypatch, tmp_path):
    from tools import demo_gold_namespace_checkpoint as namespace
    with capture(gold_namespace, monkeypatch) as inventory:
        original_cut = json.loads(json.dumps(inventory.cut))
        saved = checkpoint.create_gold_namespace_checkpoint(inventory, tmp_path/'archive',
            passphrase=None, capture_only=True)
    verify = namespace.verify_restored_namespace
    observed = []
    def exact_cut(*args, **kwargs):
        assert kwargs.get('cut_request') == original_cut
        observed.append(kwargs['cut_request'])
        return verify(*args, **kwargs)
    monkeypatch.setattr(namespace, 'verify_restored_namespace', exact_cut)
    checkpoint.restore_gold_namespace_checkpoint(tmp_path/'archive', tmp_path/'restored',
        passphrase=None, expected_sha256=saved['sha256'])
    assert observed == [original_cut]


@pytest.mark.parametrize('encrypted', [False, True])
def test_native_gold_archive_restores_after_original_source_is_removed(
        gold_namespace, monkeypatch, tmp_path, encrypted):
    f = gold_namespace
    original = hashes(f.root)
    archive = tmp_path / 'archive'
    with capture(f, monkeypatch) as inventory:
        report = checkpoint.create_gold_namespace_checkpoint(inventory, archive,
            passphrase=SECRET if encrypted else None, capture_only=not encrypted)
        expected = inventory.semantic_view()
    shutil.rmtree(f.attachment['source_root'])
    Path(f.attachment['bundle_path']).unlink()
    restored = tmp_path / 'restored'
    result = checkpoint.restore_gold_namespace_checkpoint(archive, restored,
        passphrase=SECRET if encrypted else None, expected_sha256=report['sha256'])
    assert result['state'] == expected
    assert hashes(restored / 'state') == original == hashes(f.root)
    assert result['source_verified'] is True
    assert result['production_resumable'] is False
    assert result['cross_namespace_atomic'] is False
    assert result['submission_enabled'] is result['economic_authority'] is False
    assert result['production_restore_performed'] is False


def test_nonempty_original_wal_survives_byte_for_byte(gold_namespace, monkeypatch, tmp_path):
    f = gold_namespace
    database = sqlite3.connect(f.root / 'dispatch.sqlite3')
    try:
        database.execute('PRAGMA journal_mode=WAL')
        database.execute('PRAGMA wal_autocheckpoint=0')
        database.execute('PRAGMA user_version=0')
        database.commit()
        database.execute('BEGIN')
        database.execute('SELECT * FROM sqlite_master').fetchall()
        wal = f.root / 'dispatch.sqlite3-wal'
        assert wal.stat().st_size > 0
        before = hashes(f.root)
        with capture(f, monkeypatch) as inventory:
            saved = checkpoint.create_gold_namespace_checkpoint(inventory, tmp_path / 'archive',
                passphrase=None, capture_only=True)
        checkpoint.restore_gold_namespace_checkpoint(tmp_path / 'archive', tmp_path / 'restored',
            passphrase=None, expected_sha256=saved['sha256'])
        assert hashes(f.root) == before == hashes(tmp_path / 'restored/state')
    finally:
        database.close()


def test_corrupt_part_cannot_publish_restored_state(gold_namespace, monkeypatch, tmp_path):
    f = gold_namespace
    with capture(f, monkeypatch) as inventory:
        saved = checkpoint.create_gold_namespace_checkpoint(inventory, tmp_path / 'archive',
            passphrase=SECRET)
    part = next((tmp_path / 'archive').glob('part-*.enc'))
    raw = bytearray(part.read_bytes()); raw[-1] ^= 1; part.write_bytes(raw)
    with pytest.raises(ValueError):
        checkpoint.restore_gold_namespace_checkpoint(tmp_path / 'archive', tmp_path / 'restored',
            passphrase=SECRET, expected_sha256=saved['sha256'])
    assert not (tmp_path / 'restored').exists()


def test_original_change_before_final_publication_rejects(gold_namespace, monkeypatch, tmp_path):
    f = gold_namespace
    with capture(f, monkeypatch) as inventory:
        original = inventory.recheck
        def changed():
            value = original()
            target = f.root / 'gold-pilot/configuration.json'
            raw = target.read_bytes(); target.unlink(); target.write_bytes(raw)
            return value
        monkeypatch.setattr(inventory, 'recheck', changed)
        with pytest.raises(ValueError):
            checkpoint.create_gold_namespace_checkpoint(inventory, tmp_path / 'archive',
                passphrase=None, capture_only=True)
        assert not (tmp_path / 'archive').exists()
        monkeypatch.setattr(inventory, 'recheck', lambda: None)


def test_last_archive_sync_is_followed_by_original_namespace_recheck(gold_namespace, monkeypatch, tmp_path):
    from tools import demo_gold_checkpoint_transport as transport
    f = gold_namespace; original = transport.sync; changed = []
    def sync(path):
        result = original(path)
        if Path(path).name == 'archive' and not changed:
            target = f.root / 'gold-pilot/configuration.json'
            inode = target.stat().st_ino
            replacement = target.with_name('replacement.tmp')
            replacement.write_bytes(target.read_bytes()); os.replace(replacement, target)
            assert target.stat().st_ino != inode
            changed.append(True)
        return result
    monkeypatch.setattr(transport, 'sync', sync)
    with capture(f, monkeypatch) as inventory:
        with pytest.raises(ValueError, match='GOLD_CHECKPOINT_CUT_CHANGED'):
            checkpoint.create_gold_namespace_checkpoint(inventory, tmp_path / 'archive',
                passphrase=None, capture_only=True)
        assert changed and not (tmp_path / 'archive').exists()
        monkeypatch.setattr(inventory, 'recheck', lambda: None)


def test_multiple_parts_keep_exact_order_and_source_and_native_bytes(gold_namespace, monkeypatch, tmp_path):
    from tools import demo_gold_checkpoint_transport as transport
    f = gold_namespace; before = hashes(f.root)
    monkeypatch.setattr(transport, 'MAX_PART_MEMBERS', 2)
    with capture(f, monkeypatch) as inventory:
        saved = checkpoint.create_gold_namespace_checkpoint(inventory, tmp_path / 'archive',
            passphrase=None, capture_only=True)
    manifest = json.loads((tmp_path / 'archive/checkpoint-manifest.json').read_bytes())
    assert len(manifest['parts']) > 2
    assert all(part['members'] <= 2 for part in manifest['parts'])
    checkpoint.restore_gold_namespace_checkpoint(tmp_path / 'archive', tmp_path / 'restored',
        passphrase=None, expected_sha256=saved['sha256'])
    assert before == hashes(f.root) == hashes(tmp_path / 'restored/state')


def test_fixed_source_set_is_self_contained_and_deduplicates_current_release(gold_namespace, monkeypatch, tmp_path):
    f = gold_namespace
    attachment = dict(schema='GOLD_CHECKPOINT_SOURCE_SET_V1', current_commit=f.commit,
        attachments=[f.attachment])
    with capture(f, monkeypatch, source_attachment=attachment) as inventory:
        saved = checkpoint.create_gold_namespace_checkpoint(inventory, tmp_path / 'archive',
            passphrase=None, capture_only=True)
    shutil.rmtree(f.attachment['source_root']); Path(f.attachment['bundle_path']).unlink()
    restored = checkpoint.restore_gold_namespace_checkpoint(tmp_path / 'archive', tmp_path / 'restored',
        passphrase=None, expected_sha256=saved['sha256'])
    assert restored['source_verified'] and restored['source_commit'] == f.commit
    assert len(list((tmp_path / 'restored/sources').glob('*.bundle'))) == 1


def test_actual_bar_archive_and_two_source_releases_restore_without_original_bundles(
        bar_archive_namespace, monkeypatch, tmp_path):
    f = bar_archive_namespace; before = hashes(f.root)
    with capture(f, monkeypatch) as inventory:
        saved = checkpoint.create_gold_namespace_checkpoint(inventory, tmp_path / 'archive',
            passphrase=SECRET)
        manifest = inventory.manifest()
        expected = {row['relative']: row['stored_sha256'] for row in inventory.iter_entries()
                    if row['kind'] == 'FILE'}
    for source in f.attachment['attachments']:
        shutil.rmtree(source['source_root']); Path(source['bundle_path']).unlink()
    result = checkpoint.restore_gold_namespace_checkpoint(tmp_path / 'archive', tmp_path / 'restored',
        passphrase=SECRET, expected_sha256=saved['sha256'])
    assert result['state']['bar_inputs']['definition_sha256'] == f.definition['definition_hash']
    assert hashes(tmp_path / 'restored/state') == expected
    assert hashes(f.root) == before
    assert len(list((tmp_path / 'restored/sources').glob('*.bundle'))) == 2
    assert manifest['source_dependencies'][0]['commit'] == f.runtime['archive_source_commit']
    assert not (tmp_path / 'restored/state/strategy-bar-sources' / f.runtime['archive_source_commit'] / '.git').exists()
    assert result['complete_gold_set_verified'] is result['production_resumable'] is False


@pytest.mark.parametrize('damage', ['missing', 'extra', 'symlink', 'hardlink', 'part_reorder', 'part_bool'])
def test_archive_custody_and_complete_part_coverage(gold_namespace, monkeypatch, tmp_path, damage):
    from tools import demo_gold_checkpoint_transport as transport
    import os
    f = gold_namespace; monkeypatch.setattr(transport, 'MAX_PART_MEMBERS', 2)
    with capture(f, monkeypatch) as inventory:
        saved = checkpoint.create_gold_namespace_checkpoint(inventory, tmp_path / 'archive',
            passphrase=None, capture_only=True)
    archive = tmp_path / 'archive'; leaf = archive / 'checkpoint-manifest.json'
    manifest = json.loads(leaf.read_bytes()); part = archive / manifest['parts'][0]['name']
    if damage == 'missing':
        part.unlink()
    elif damage == 'extra':
        (archive / 'unlisted.zip').write_bytes(b'extra')
    elif damage == 'symlink':
        target = tmp_path / 'outside.zip'; part.rename(target); part.symlink_to(target)
    elif damage == 'hardlink':
        os.link(part, tmp_path / 'alias.zip')
    else:
        if damage == 'part_reorder':
            manifest['parts'] = list(reversed(manifest['parts']))
        else:
            manifest['parts'][0]['ordinal'] = True
        leaf.write_bytes(checkpoint.canonical(manifest)); saved['sha256'] = checkpoint.sha(leaf)
    with pytest.raises((ValueError, OSError)):
        checkpoint.restore_gold_namespace_checkpoint(archive, tmp_path / 'restored',
            passphrase=None, expected_sha256=saved['sha256'])
    assert not (tmp_path / 'restored').exists()


@pytest.mark.parametrize('mutation', ['cut', 'public_records'])
def test_all_retained_cut_and_public_bytes_keep_original_internal_commitments(
        gold_namespace, monkeypatch, tmp_path, mutation):
    f = gold_namespace; before = hashes(f.root)
    with capture(f, monkeypatch) as inventory:
        checkpoint.create_gold_namespace_checkpoint(inventory, tmp_path / 'archive',
            passphrase=None, capture_only=True)
    leaf = tmp_path / 'archive/checkpoint-manifest.json'
    value = json.loads(leaf.read_bytes())
    if mutation == 'cut':
        value['cut_request']['writer_barrier_sha256'] = '1' * 64
    else:
        value['public_bundle']['records'] = {'unbound_evidence': {'status': 'CHANGED'}}
    leaf.write_bytes(checkpoint.canonical(value))
    # A new outer pin does not erase the original internal commitments.
    with pytest.raises(ValueError, match='GOLD_TRANSPORT_CUT_BINDING'):
        checkpoint.restore_gold_namespace_checkpoint(tmp_path / 'archive', tmp_path / 'restored',
            passphrase=None, expected_sha256=checkpoint.sha(leaf))
    assert before == hashes(f.root) and not (tmp_path / 'restored').exists()


@pytest.mark.parametrize('mutation', ['invented_quota', 'boolean_count', 'oversized_index'])
def test_invalid_aggregate_budget_is_rejected_before_any_payload_is_extracted(
        gold_namespace, monkeypatch, tmp_path, mutation):
    from tools import demo_gold_checkpoint_transport as transport
    f = gold_namespace
    with capture(f, monkeypatch) as inventory:
        checkpoint.create_gold_namespace_checkpoint(inventory, tmp_path / 'archive',
            passphrase=None, capture_only=True)
    leaf = tmp_path / 'archive/checkpoint-manifest.json'; value = json.loads(leaf.read_bytes())
    if mutation == 'invented_quota':
        value['inventory']['component_bounds']['ordinary']['max_bytes'] *= 2
    elif mutation == 'boolean_count':
        value['inventory']['files'] = True
    else:
        value['inventory']['index'][0]['bytes'] = 8 * 1024**2 + 1
    leaf.write_bytes(checkpoint.canonical(value)); called = []
    monkeypatch.setattr(transport, '_restore_parts', lambda *args: called.append(True))
    with pytest.raises(ValueError):
        checkpoint.restore_gold_namespace_checkpoint(tmp_path / 'archive', tmp_path / 'restored',
            passphrase=None, expected_sha256=checkpoint.sha(leaf))
    assert not called and not (tmp_path / 'restored').exists()


@pytest.mark.parametrize('bound', ['expanded_bytes', 'tree_entries'])
def test_actual_bundle_tree_limits_are_checked_before_any_native_file_is_written(
        gold_namespace, monkeypatch, tmp_path, bound):
    from tools import demo_gold_checkpoint_transport as transport
    from tools import demo_gold_namespace_checkpoint as native_checkpoint
    f = gold_namespace
    with capture(f, monkeypatch) as inventory:
        saved = checkpoint.create_gold_namespace_checkpoint(inventory, tmp_path / 'archive',
            passphrase=None, capture_only=True)
    if bound == 'expanded_bytes':
        bundle_size = Path(f.attachment['bundle_path']).stat().st_size
        source = Path(f.attachment['source_root'])
        expanded = sum(p.stat().st_size for p in source.rglob('*')
            if p.is_file() and '.git' not in p.relative_to(source).parts)
        assert expanded > bundle_size + 1
        # Keep the compressed input within budget; reject its actual Git tree.
        monkeypatch.setattr(native_checkpoint, 'SOURCE_MAX_EXPANDED_BYTES', bundle_size + 1)
    else:
        monkeypatch.setattr(native_checkpoint, 'SOURCE_MAX_ENTRIES', 1)
    written = []; original = transport._extract_item
    def extract(archive, member, item, root):
        if item['kind'] == 'FILE':
            written.append(item['relative'])
        return original(archive, member, item, root)
    monkeypatch.setattr(transport, '_extract_item', extract)
    with pytest.raises(ValueError, match='GOLD_CHECKPOINT_SOURCE_TREE_BUDGET'):
        checkpoint.restore_gold_namespace_checkpoint(tmp_path / 'archive', tmp_path / 'restored',
            passphrase=None, expected_sha256=saved['sha256'])
    assert written == []
    assert not (tmp_path / 'restored').exists()
