"""Bind original registered research archives to the original fixed-public cut.

Only accepted release/OS-root inputs are fixture boundaries. Native producers,
bundle, full indexes, original public contents and restored population execute.
"""
import os
from pathlib import Path
import time

import pytest

from tests.tools.test_demo_gold_research_inventory import native_research as native_research, capture
from tests.tools.test_registered_research_run import registered_case as registered_case
from tests.tools.test_demo_gold_namespace_checkpoint import source_attachment as source_attachment, private_files as private_files
from tests.tools.test_demo_gold_checkpoint_public import fixed_public as fixed_public, public_cut
from tests.tools.test_demo_market_partition_install import book as book, env as env
from tools import demo_gold_checkpoint_transport as transport
from tools.gold_pilot_retention_store import canonical

_OWNER = os.geteuid(), os.getegid()


@pytest.fixture
def combined(native_research, fixed_public, monkeypatch):
    research = native_research
    f = fixed_public
    from tools import demo_supervisor as supervisor, gold_bar_definition as definition
    f.now = time.time_ns() // 1_000_000
    monkeypatch.setattr(supervisor, 'now_ms', lambda: time.time_ns() // 1_000_000)
    commit = research['source']['identity']['commit']
    f.args['source_commit'] = commit
    f.policy['executor_release_commit'] = commit
    f.paths['policy'].write_bytes(canonical(f.policy))
    release = f.module.RELEASES / commit
    (release / 'evidence').mkdir(parents=True, mode=0o755)
    release.chmod(0o755)
    (release / 'evidence').chmod(0o755)
    leaf = release / 'evidence/l2_release_evidence_manifest.json'
    leaf.write_bytes(canonical(dict(schema_version=1, readiness='L2_DRY_RUN_ONLY', source_commit=commit)))
    leaf.chmod(0o644)
    monkeypatch.setattr(supervisor, 'verify_source', lambda source: (
        definition.verify_source(Path(research['source']['source_root']), research['source']['identity'])
        if source == commit else pytest.fail('wrong accepted source fixture')))
    snapshot = f.module.capture(**f.args)
    public, cut = public_cut(f, snapshot)
    assert research['layout']['uid'] in public['registry']['research_uids']
    research.update(public=public, cut=cut)
    return dict(f=research, fixed=f, snapshot=snapshot)


@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
def test_original_research_index_can_bind_archived_fixed_public_and_restore(combined, monkeypatch):
    f = combined
    api = f['fixed'].module
    research = f['f']
    with monkeypatch.context() as role:
        role.setattr(os, 'geteuid', lambda: _OWNER[0])
        with capture(research) as held:
            inventory, receipt = held.manifest(), held.recheck()
            entries = list(held.iter_entries())
            saved = transport.create_checkpoint(held, research['output'] / 'archive',
                passphrase=None, capture_only=True)
        handle = api.validate_archived(f['snapshot'], public_bundle=research['public'],
            cut_request=research['cut'], inventory=inventory, role_receipt=receipt,
            index_entries=entries, as_of_ms=receipt['completed_at_ms'])
        assert handle.partition_at(receipt['completed_at_ms']) == {'status': 'VERIFIED_ABSENT'}
        restored = transport.restore_checkpoint(research['output'] / 'archive', research['output'] / 'restored',
            passphrase=None, expected_sha256=saved['sha256'])
        assert restored['state']['research']['components']['registered']['native']['attempt_count'] == 6
    assert inventory['namespace'] == 'research' and inventory['source_root']['uid'] == _OWNER[0]
    assert restored['complete_gold_set_verified'] is restored['production_resumable'] is False


@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
@pytest.mark.parametrize('damage', ['wrong_uid', 'wrong_layout', 'index_owner', 'public_bytes', 'receipt'])
def test_research_archive_cannot_relabel_original_uid_layout_or_fixed_cut(combined, monkeypatch, damage):
    f = combined
    research = f['f']
    with monkeypatch.context() as role:
        role.setattr(os, 'geteuid', lambda: _OWNER[0])
        with capture(research) as held:
            inventory, receipt, entries = held.manifest(), held.recheck(), list(held.iter_entries())
    if damage == 'wrong_uid':
        inventory['source_root']['uid'] += 3
    elif damage == 'wrong_layout':
        inventory['research_layout']['uid'] += 3
        inventory['research_layout']['gid'] += 3
    elif damage == 'index_owner':
        entries[0]['source_identity']['uid'] += 3
    elif damage == 'public_bytes':
        f['snapshot']['files']['policy']['body'] = 'e30='
    else:
        receipt['inventory_sha256'] = 'a' * 64
    with pytest.raises(ValueError):
        f['fixed'].module.validate_archived(f['snapshot'], public_bundle=research['public'],
            cut_request=research['cut'], inventory=inventory, role_receipt=receipt,
            index_entries=entries, as_of_ms=receipt['completed_at_ms'])
