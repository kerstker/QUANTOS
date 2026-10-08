"""A native directory-only controller still needs full source/index validation."""
import os

import pytest

from tests.tools.test_demo_gold_namespace_checkpoint import (
    gold_namespace as gold_namespace, source_attachment as source_attachment, private_files as private_files,
)


@pytest.mark.parametrize('encrypted', [False, True])
def test_directory_only_native_controller_roundtrip(gold_namespace, tmp_path, monkeypatch, encrypted):
    from tools import demo_gold_namespace_checkpoint as native
    from tools import demo_gold_checkpoint_transport as transport
    from tools.demo_namespace_checkpoint import CONTROLLER_REQUIRED_DIRECTORIES
    f = gold_namespace
    root = tmp_path / 'controller'
    root.mkdir(mode=0o700)
    for name in CONTROLLER_REQUIRED_DIRECTORIES:
        (root / name).mkdir(mode=0o700)
    monkeypatch.setitem(native.ROLE_ROOTS, 'controller', root)
    # Only the controller role identity is virtual, as in existing native
    # controller tests; actual file, bundle and index validation stays original.
    monkeypatch.setattr(native, '_role_uid', lambda registry, namespace: os.getuid())
    monkeypatch.setattr(native.os, 'getgroups', lambda: [os.getuid()])
    secret = 'isolated-empty-controller-passphrase' if encrypted else None
    with native.capture_inventory(namespace='controller', source_commit=f.commit,
            operation_sha256=f.cut['operation_sha256'], cut_request=f.cut, public_bundle=f.public,
            source_attachment=f.attachment, guard=lambda: None) as inventory:
        assert inventory.manifest()['files'] == 0
        assert inventory.manifest()['directories'] == len(CONTROLLER_REQUIRED_DIRECTORIES)
        expected = inventory.semantic_view()
        saved = transport.create_checkpoint(inventory, tmp_path / 'archive',
            passphrase=secret, capture_only=not encrypted)
    restored = tmp_path / 'restored'
    report = transport.restore_checkpoint(tmp_path / 'archive', restored,
        passphrase=secret, expected_sha256=saved['sha256'])
    assert report['state'] == expected
    assert report['source_verified'] is True
    assert {p.name for p in (restored / 'state').iterdir()} == CONTROLLER_REQUIRED_DIRECTORIES
    assert not any(p.is_file() for p in (restored / 'state').rglob('*'))
    assert report['complete_gold_set_verified'] is report['economic_authority'] is False
