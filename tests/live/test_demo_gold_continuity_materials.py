"""Real descriptors/locks/bytes; only root ownership and role IDs are mapped."""
from copy import deepcopy
import hashlib
import json
import os
from types import SimpleNamespace

import pytest


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def fixture(tmp_path, monkeypatch):
    from live.execution import demo_gold_continuity_materials as tool
    root = tmp_path/'public'; root.mkdir(mode=0o755)
    monkeypatch.setattr(tool, 'PUBLIC_ROOT', root)
    uid = os.getuid()
    native_meta, native_parent = tool._metadata, tool._parent_metadata
    def mapped(info):
        assert info.st_uid in (0, uid)
        return SimpleNamespace(**{n: getattr(info, n) for n in ('st_mode','st_nlink','st_size')}, st_uid=0, st_gid=0)
    monkeypatch.setattr(tool, '_metadata', lambda info, **kw: native_meta(mapped(info), **kw))
    def parent(info):
        # The task output's existing ancestors are developer-owned/writable;
        # map that deployment-only property, never public root/file metadata.
        value=mapped(info); value.st_mode = (value.st_mode & ~0o022) | 0o111
        return native_parent(value)
    monkeypatch.setattr(tool, '_parent_metadata', parent)
    monkeypatch.setattr(tool.os, 'geteuid', lambda: 0)
    # No real keys or accounts. This reader validates custody/hash binding only;
    # native issuer authorization independently checks the semantic documents.
    documents = {name: {'test_public_record': name} for name in tool.DOCUMENTS['GROUP_CONTINUITY']}
    documents['definition'] = {'test_public_record': 'x'*70000}
    operation = dict(subject=dict(source_commit='c'*40, purpose='GROUP_CONTINUITY',
        documents_sha256={name: tool.digest(doc) for name, doc in documents.items()}),
        confirmation=dict(subject_sha256='a'*64))
    identity = tool.digest(operation); area = root/identity; area.mkdir(mode=0o755)
    files = {'operation.json': canonical(operation), **{k.replace('_','-')+'.json': canonical(v) for k,v in documents.items()}}
    manifest = dict(schema=tool.MANIFEST_SCHEMA, source_commit='c'*40, purpose='GROUP_CONTINUITY',
        operation_sha256=identity, files={k:hashlib.sha256(v).hexdigest() for k,v in files.items()},
        submission_enabled=False, economic_authority=False)
    complete = dict(schema=tool.COMPLETE_SCHEMA, source_commit='c'*40, purpose='GROUP_CONTINUITY',
        operation_sha256=identity, manifest_sha256=tool.digest(manifest), submission_enabled=False, economic_authority=False)
    files.update({'manifest.json':canonical(manifest),'complete.json':canonical(complete)})
    for name,body in files.items(): (area/name).write_bytes(body); (area/name).chmod(0o444)
    return tool, area, identity, documents, operation


def test_bounded_large_public_document_and_exact_bytes_are_read_without_mutation(tmp_path, monkeypatch):
    tool, area, identity, documents, operation = fixture(tmp_path, monkeypatch)
    before = {p.name:(p.read_bytes(),p.stat().st_mtime_ns,p.stat().st_ino) for p in area.iterdir()}
    result = tool.load_operation(identity, source_commit='c'*40, purpose='GROUP_CONTINUITY', registry={}, role='controller')
    assert result['documents'] == documents and result['operation'] == operation
    assert before == {p.name:(p.read_bytes(),p.stat().st_mtime_ns,p.stat().st_ino) for p in area.iterdir()}


@pytest.mark.parametrize('damage', ['missing_complete','extra','changed','symlink','hardlink','writable','source','purpose','role'])
def test_public_materials_fail_closed(tmp_path, monkeypatch, damage):
    tool, area, identity, documents, operation = fixture(tmp_path, monkeypatch)
    source, purpose = 'c'*40, 'GROUP_CONTINUITY'
    if damage == 'missing_complete': (area/'complete.json').unlink()
    elif damage == 'extra': (area/'failure.json').write_text('{}')
    elif damage == 'changed':
        path=area/'profile.json'; path.chmod(0o600); path.write_text('{}'); path.chmod(0o444)
    elif damage == 'symlink':
        target=area/'profile.json'; target.unlink(); target.symlink_to(area/'registry.json')
    elif damage == 'hardlink': os.link(area/'profile.json', tmp_path/'other.json')
    elif damage == 'writable': (area/'profile.json').chmod(0o644)
    elif damage == 'source': source='d'*40
    elif damage == 'purpose': purpose='DELEGATION_V2_SIGN'
    else: monkeypatch.setattr(tool.os, 'geteuid', lambda: 12345)
    with pytest.raises((ValueError, OSError)):
        tool.load_operation(identity, source_commit=source, purpose=purpose, registry={}, role='controller')


def test_shared_reader_rejects_exclusive_publication_lease(tmp_path, monkeypatch):
    tool, area, identity, documents, operation = fixture(tmp_path, monkeypatch)
    with tool.public_lease(exclusive=True):
        with pytest.raises(ValueError, match='BUSY'):
            tool.load_operation(identity, source_commit='c'*40, purpose='GROUP_CONTINUITY', registry={}, role='controller')
