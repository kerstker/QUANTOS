"""Bounded multipart transport for guarded Gold namespace evidence.

This archives exact DB/WAL bytes, indexes, public evidence and the verified Git
bundle. It neither stops writers nor establishes a complete cross-role cut.
Only the existing role launcher may supply a live CaptureInventory. Restoring
evidence into an isolated directory never activates the resulting files.
"""
from contextlib import ExitStack, contextmanager
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import zipfile

from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_namespace_checkpoint as checkpoint
from tools.demo_retention_transaction import move, sync, write_new

SCHEMA = 'DEMO_GOLD_NAMESPACE_CHECKPOINT_V3'
PART_SCHEMA = 'DEMO_GOLD_NAMESPACE_PART_V1'
MAX_METADATA_BYTES = 32 * 1024**2
MAX_PART_OVERHEAD = MAX_METADATA_BYTES + 32 * 1024**2
MAX_PART_MEMBERS = checkpoint.MAX_FILES
ITEM_FIELDS = {'kind', 'relative', 'ordinal', 'offset', 'bytes', 'sha256'}
require = checkpoint.require


def _hash(value):
    require(type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None,
            'GOLD_TRANSPORT_DIGEST')
    return value


def _integer(value, maximum, *, minimum=0):
    require(type(value) is int and minimum <= value <= maximum, 'GOLD_TRANSPORT_INTEGER')
    return value


def _fields(value, fields):
    require(type(value) is dict and set(value) == set(fields), 'GOLD_TRANSPORT_FIELDS')


def _canonical(value):
    raw = checkpoint.canonical(value)
    require(len(raw) <= MAX_METADATA_BYTES, 'GOLD_TRANSPORT_METADATA_BUDGET')
    return raw


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _private(path, *, directory=False, maximum=None, uid=None):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'GOLD_TRANSPORT_SYMLINK')
    info = path.lstat()
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == (os.geteuid() if uid is None else uid) and not info.st_mode & 0o077,
            'GOLD_TRANSPORT_PRIVATE_CUSTODY')
    if not directory:
        require(info.st_nlink == 1 and (maximum is None or info.st_size <= maximum),
                'GOLD_TRANSPORT_FILE_BUDGET')
    return info


def _destination(path):
    from tools.demo_gold_namespace_checkpoint import ROLE_ROOTS
    path = Path(path).absolute()
    require('..' not in path.parts and path.resolve() == path, 'GOLD_TRANSPORT_CANONICAL_DESTINATION')
    require(not path.exists() and not path.is_symlink(), 'GOLD_TRANSPORT_NEW_DESTINATION')
    require(not any(path == root or root in path.parents
            for root in (*checkpoint.PROTECTED_ROOTS, *ROLE_ROOTS.values())),
            'GOLD_TRANSPORT_ISOLATED_ONLY')
    _private(path.parent, directory=True)
    return path


@contextmanager
def _stable(path, maximum, *, private=False, uid=None):
    path = Path(path)
    if private:
        before = _private(path, maximum=maximum, uid=uid)
    else:
        require(not any(p.is_symlink() for p in (path, *path.parents)), 'GOLD_TRANSPORT_SYMLINK')
        before = path.lstat()
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and not before.st_mode & 0o022 and before.st_size <= maximum,
                'GOLD_TRANSPORT_SOURCE_CUSTODY')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        require(_identity(os.fstat(fd)) == _identity(before), 'GOLD_TRANSPORT_CHANGED')
        yield stream
        require(_identity(os.fstat(fd)) == _identity(before) == _identity(path.lstat()),
                'GOLD_TRANSPORT_CHANGED')


def _freeze(path, target, expected, maximum, *, source_uid=None):
    digest = hashlib.sha256(); total = 0
    with _stable(path, maximum, private=True, uid=source_uid) as source, target.open('xb') as output:
        target.chmod(0o600)
        while block := source.read(1024 * 1024):
            total += len(block); require(total <= maximum, 'GOLD_TRANSPORT_FILE_BUDGET')
            output.write(block); digest.update(block)
        output.flush(); os.fsync(output.fileno())
    require(digest.hexdigest() == _hash(expected), 'GOLD_TRANSPORT_CHANGED')


def _extents(path):
    from tools.demo_gold_namespace_checkpoint import MAX_EXTENT_BYTES
    offset = 0; ordinal = 1
    with _stable(path, checkpoint.MAX_BYTES) as stream:
        while True:
            raw = stream.read(MAX_EXTENT_BYTES)
            if not raw and offset:
                break
            yield dict(ordinal=ordinal, offset=offset, bytes=len(raw),
                       sha256=hashlib.sha256(raw).hexdigest())
            offset += len(raw); ordinal += 1
            if not raw:
                break


def _item(kind, relative, extent):
    return dict(kind=kind, relative=relative, **extent)


def _check_item(item):
    from tools.demo_gold_namespace_checkpoint import MAX_EXTENT_BYTES
    _fields(item, ITEM_FIELDS)
    require(item['kind'] in ('INDEX', 'SOURCE', 'FILE'), 'GOLD_TRANSPORT_KIND')
    checkpoint.safe_relative(item['relative'])
    _integer(item['ordinal'], 2**53 - 1, minimum=1)
    _integer(item['offset'], 2**53 - 1)
    _integer(item['bytes'], MAX_EXTENT_BYTES)
    _hash(item['sha256'])
    return item


def _aux_items(manifest):
    from tools.demo_gold_namespace_checkpoint import MAX_INDEX_BYTES, MAX_EXTENT_BYTES
    for number, index in enumerate(manifest['inventory']['index'], 1):
        require(index['name'] == f'index/{number:06d}.jsonl', 'GOLD_TRANSPORT_INDEX_ORDER')
        yield _item('INDEX', index['name'], dict(ordinal=1, offset=0,
            bytes=_integer(index['bytes'], MAX_INDEX_BYTES, minimum=1), sha256=_hash(index['sha256'])))
    for blob in manifest['source_blobs']:
        offset = 0
        require(len(blob['extents']) == (blob['bytes'] + MAX_EXTENT_BYTES - 1) // MAX_EXTENT_BYTES,
                'GOLD_TRANSPORT_SOURCE_EXTENTS')
        for ordinal, extent in enumerate(blob['extents'], 1):
            _fields(extent, {'ordinal', 'offset', 'bytes', 'sha256'})
            item = _check_item(_item('SOURCE', f"sources/{blob['commit']}.bundle", extent))
            require(item['ordinal'] == ordinal and item['offset'] == offset
                    and item['bytes'] == min(MAX_EXTENT_BYTES, blob['bytes'] - offset),
                    'GOLD_TRANSPORT_SOURCE_EXTENTS')
            offset += item['bytes']
            yield item
        require(offset == blob['bytes'] and offset > 0, 'GOLD_TRANSPORT_SOURCE_SIZE')


def _file_items(entries):
    for row in entries:
        if row['kind'] == 'DIRECTORY':
            continue
        require(row['snapshot_method'] == 'STABLE_FILE_READ'
                and row['original_sha256'] == row['stored_sha256'], 'GOLD_TRANSPORT_RAW_CUT_REQUIRED')
        offset = 0; count = 0
        for ordinal, extent in enumerate(row['extents'], 1):
            item = _check_item(_item('FILE', row['relative'], extent))
            require(item['ordinal'] == ordinal and item['offset'] == offset,
                    'GOLD_TRANSPORT_FILE_EXTENTS')
            require(item['bytes'] > 0 or ordinal == 1 and row['logical_bytes'] == 0,
                    'GOLD_TRANSPORT_EMPTY_EXTENT')
            offset += item['bytes']; count += 1
            yield item
        require(count > 0 and offset == row['logical_bytes'], 'GOLD_TRANSPORT_FILE_SIZE')


def _capture_items(inventory, manifest):
    from tools.demo_gold_namespace_checkpoint import source_attachments
    sources = {f"sources/{row['identity']['commit']}.bundle": Path(row['bundle_path'])
               for row in source_attachments(inventory.source_attachment)}
    for item in _aux_items(manifest):
        path = (inventory.index_directory / Path(item['relative']).name
                if item['kind'] == 'INDEX' else sources[item['relative']])
        yield item, path
    for item in _file_items(inventory.iter_entries()):
        yield item, inventory.payload_path(item['relative'])


def _payload_write(archive, member, item, path):
    digest = hashlib.sha256(); remaining = item['bytes']
    with _stable(path, 2**53 - 1) as source, archive.open(member, 'w', force_zip64=True) as target:
        source.seek(item['offset'])
        while remaining:
            block = source.read(min(1024 * 1024, remaining))
            require(block, 'GOLD_TRANSPORT_TRUNCATED')
            target.write(block); digest.update(block); remaining -= len(block)
    require(digest.hexdigest() == item['sha256'], 'GOLD_TRANSPORT_PAYLOAD_CHANGED')


def _write_parts(inventory, manifest, target, scratch, passphrase):
    parts = []; archive = None; items = []; size = 0; meta_size = 128

    def finish():
        nonlocal archive
        if archive is None:
            return
        value = dict(schema=PART_SCHEMA, ordinal=len(parts) + 1, items=items)
        archive.writestr('part-manifest.json', _canonical(value)); archive.close(); archive = None
        plain = scratch / 'part.zip'
        name = f'part-{len(parts) + 1:06d}.' + ('enc' if manifest['encrypted'] else 'zip')
        path = target / name
        if manifest['encrypted']:
            checkpoint.encrypt_file(str(plain), str(path), passphrase); plain.unlink()
        else:
            os.rename(plain, path)
        path.chmod(0o600)
        with path.open('rb') as stream:
            os.fsync(stream.fileno())
        require(path.stat().st_size <= checkpoint.MAX_BYTES + MAX_PART_OVERHEAD,
                'GOLD_TRANSPORT_PART_BUDGET')
        parts.append(dict(ordinal=len(parts) + 1, name=name, sha256=checkpoint.sha(path),
            archive_bytes=path.stat().st_size, payload_bytes=size, members=len(items)))

    try:
        for item, path in _capture_items(inventory, manifest):
            _check_item(item)
            next_size = len(checkpoint.canonical(item)) + 1
            if items and (len(items) == min(MAX_PART_MEMBERS, checkpoint.MAX_FILES) or size + item['bytes'] > checkpoint.MAX_BYTES
                    or meta_size + next_size > MAX_METADATA_BYTES):
                finish(); items = []; size = 0; meta_size = 128
            if archive is None:
                archive = zipfile.ZipFile(scratch / 'part.zip', 'x', compression=zipfile.ZIP_STORED)
            _payload_write(archive, f'payload/{len(items) + 1:010d}', item, path)
            items.append(item); size += item['bytes']; meta_size += next_size
        finish()
    finally:
        if archive is not None:
            archive.close()
    return parts


def _manifest_check(value, encrypted):
    from tools.demo_gold_namespace_checkpoint import (
        source_attachment_identity, source_current_commit, source_attachments,
        inventory_limits, MAX_INDEX_BYTES, MAX_INDEX_ENTRIES,
    )
    _fields(value, 'schema encrypted inventory role_receipt public_bundle cut_request source_identity '
            'source_blobs parts submission_enabled economic_authority'.split())
    require(value['schema'] == SCHEMA and value['encrypted'] is encrypted
            and value['submission_enabled'] is value['economic_authority'] is False,
            'GOLD_TRANSPORT_MANIFEST')
    _cut_binding(value)
    require(type(value['source_blobs']) is list and 1 <= len(value['source_blobs']) <= 2
            and type(value['parts']) is list and value['parts'], 'GOLD_TRANSPORT_LAYOUT')
    # Reuse the same portable identity validator as capture. Paths are runtime
    # locations only and are never accepted as extraction destinations.
    identity = value['source_identity']
    attachment = _source_runtime(identity)
    require(source_attachment_identity(attachment) == identity, 'GOLD_TRANSPORT_SOURCE_IDENTITY')
    require(source_current_commit(attachment) == value['inventory']['source_commit'],
            'GOLD_TRANSPORT_SOURCE_COMMIT')
    require([row['commit'] for row in value['source_blobs']] == [row['identity']['commit']
            for row in source_attachments(attachment)], 'GOLD_TRANSPORT_SOURCE_BLOBS')
    for blob in value['source_blobs']:
        _fields(blob, {'commit', 'bytes', 'extents'})
        _integer(blob['bytes'], checkpoint.MAX_BYTES, minimum=1)
        require(type(blob['extents']) is list and blob['extents'], 'GOLD_TRANSPORT_SOURCE_EXTENTS')
    limits = inventory_limits(value['inventory'], public_bundle=value['public_bundle'], source_attachment=attachment)
    indexes = value['inventory']['index']
    require(type(indexes) is list and 1 <= len(indexes) <= limits['max_files'] + limits['max_directories'],
            'GOLD_TRANSPORT_INDEX_BUDGET')
    index_bytes = 0; index_entries = 0
    for index in indexes:
        _fields(index, {'ordinal', 'name', 'sha256', 'bytes', 'entries', 'first_key', 'last_key'})
        _integer(index['ordinal'], len(indexes), minimum=1)
        index_bytes += _integer(index['bytes'], MAX_INDEX_BYTES, minimum=1)
        index_entries += _integer(index['entries'], MAX_INDEX_ENTRIES, minimum=1)
    require(index_bytes <= limits['max_index_bytes']
            and index_entries == value['inventory']['files'] + value['inventory']['directories'],
            'GOLD_TRANSPORT_INDEX_BUDGET')
    auxiliary_members = 0
    for item in _aux_items(value):
        _check_item(item); auxiliary_members += 1
    maximum_members = auxiliary_members + limits['max_extent_members']
    require(len(value['parts']) <= maximum_members, 'GOLD_TRANSPORT_PART_COUNT')
    total = 0
    members = 0
    for ordinal, part in enumerate(value['parts'], 1):
        _fields(part, {'ordinal', 'name', 'sha256', 'archive_bytes', 'payload_bytes', 'members'})
        suffix = 'enc' if encrypted else 'zip'
        require(type(part['ordinal']) is int and part['ordinal'] == ordinal
                and part['name'] == f'part-{ordinal:06d}.{suffix}', 'GOLD_TRANSPORT_PART_ORDER')
        _hash(part['sha256']); _integer(part['archive_bytes'], checkpoint.MAX_BYTES + MAX_PART_OVERHEAD, minimum=1)
        _integer(part['payload_bytes'], checkpoint.MAX_BYTES)
        members += _integer(part['members'], checkpoint.MAX_FILES, minimum=1)
        total += part['payload_bytes']
    expected = value['inventory']['payload_bytes'] + sum(row['bytes'] for row in value['source_blobs']) + sum(
        row['bytes'] for row in value['inventory']['index'])
    require(type(expected) is int and total == expected and members <= maximum_members,
            'GOLD_TRANSPORT_TOTAL_BYTES')
    return value


def _cut_binding(value):
    """Verify every retained cut/public byte against the original role receipt."""
    from live.execution.demo_batch_risk import _digest
    cut = value['cut_request']; public = value['public_bundle']; inventory = value['inventory']
    _fields(cut, 'schema operation_sha256 source_commit registry_sha256 public_bundle_sha256 roles '
            'writer_barrier_sha256 opening_at_ms submission_enabled economic_authority'.split())
    _fields(public, 'schema source_commit registry account_ref_sha256 base_runtime current_runtime records'.split())
    require(cut['schema'] == 'GOLD_CHECKPOINT_CUT_REQUEST_V1'
            and public['schema'] == 'GOLD_CHECKPOINT_PUBLIC_CONTEXT_V1'
            and type(public['records']) is dict
            and type(cut['roles']) is list and cut['roles']
            and all(type(role) is str for role in cut['roles'])
            and len(set(cut['roles'])) == len(cut['roles'])
            and set(cut['roles']) <= {'executor', 'controller', 'issuer', 'research'}
            and inventory['namespace'] in cut['roles']
            and cut['submission_enabled'] is cut['economic_authority'] is False,
            'GOLD_TRANSPORT_CUT')
    _integer(cut['opening_at_ms'], 2**53 - 1, minimum=1)
    _hash(cut['writer_barrier_sha256'])
    require(_digest(cut) == _hash(inventory['cut_request_sha256'])
            == value['role_receipt']['cut_request_sha256']
            and cut['operation_sha256'] == _hash(inventory['operation_sha256'])
            == value['role_receipt']['operation_sha256']
            and cut['source_commit'] == inventory['source_commit'] == public['source_commit']
            and _digest(public) == _hash(cut['public_bundle_sha256'])
            and _digest(public['registry']) == _hash(cut['registry_sha256']) == inventory['registry_sha256']
            and public['account_ref_sha256'] == inventory['account_ref_sha256'],
            'GOLD_TRANSPORT_CUT_BINDING')


def _read_json(path):
    require(path.stat().st_size <= MAX_METADATA_BYTES, 'GOLD_TRANSPORT_METADATA_BUDGET')
    raw = path.read_bytes()
    try:
        result = decode(raw)
    except (ValueError, RecursionError) as exc:
        raise checkpoint.CheckpointError('GOLD_TRANSPORT_JSON') from exc
    require(_canonical(result) == raw, 'GOLD_TRANSPORT_CANONICAL')
    return result


def _source_runtime(identity, root=None):
    require(type(identity) is dict, 'GOLD_TRANSPORT_SOURCE_IDENTITY')
    grouped = identity.get('schema') == 'GOLD_CHECKPOINT_SOURCE_SET_IDENTITY_V1'
    _fields(identity, {'schema', 'current_commit', 'sources'} if grouped else {'schema', 'bundle_sha256', 'identity'})
    if grouped:
        rows = identity['sources']
        require(type(rows) is list and 1 <= len(rows) <= 2, 'GOLD_TRANSPORT_SOURCE_SET')
    else:
        require(identity['schema'] == 'GOLD_CHECKPOINT_SOURCE_IDENTITY_V1', 'GOLD_TRANSPORT_SOURCE_IDENTITY')
        rows = [{key: identity[key] for key in ('bundle_sha256', 'identity')}]
    attachments = []
    for row in rows:
        _fields(row, {'bundle_sha256', 'identity'})
        commit = row['identity']['commit']
        require(type(commit) is str and re.fullmatch('[a-f0-9]{40}', commit) is not None,
                'GOLD_TRANSPORT_SOURCE_COMMIT')
        attachments.append(dict(schema='GOLD_CHECKPOINT_VERIFIED_SOURCE_V1',
            source_root='/unused' if root is None else str(root / 'source' / commit),
            bundle_path='/unused' if root is None else str(root / 'sources' / (commit + '.bundle')),
            bundle_sha256=row['bundle_sha256'], identity=row['identity']))
    if grouped:
        return dict(schema='GOLD_CHECKPOINT_SOURCE_SET_V1', current_commit=identity['current_commit'], attachments=attachments)
    return attachments[0]


def _expected_files(root, manifest):
    from tools.demo_gold_namespace_checkpoint import _index_rows
    research = manifest['inventory']['schema'] == 'GOLD_RESEARCH_INVENTORY_V1'
    entries = list(_index_rows(root, manifest['inventory']['index'], os.geteuid(), original_private=not research))
    if research:
        from tools.demo_gold_research_inventory import check_index_entries
        check_index_entries(manifest['inventory'], entries)
    files = sum(row['kind'] == 'FILE' for row in entries)
    require(files == manifest['inventory']['files'] and len(entries) - files == manifest['inventory']['directories']
            and sum(row.get('logical_bytes', 0) for row in entries) == manifest['inventory']['payload_bytes'],
            'GOLD_TRANSPORT_INDEX_COUNTS')
    state = root / 'state'
    directories = {row['relative'] for row in entries if row['kind'] == 'DIRECTORY'}
    for row in entries:
        name = checkpoint.safe_relative(row['relative'])
        require(all(parent.as_posix() in directories for parent in name.parents
                if parent.as_posix() != '.'), 'GOLD_TRANSPORT_PARENT_MISSING')
        if row['kind'] == 'DIRECTORY':
            (state / name).mkdir(mode=0o700, parents=True, exist_ok=True)
    return _file_items(entries)


def _extract_item(archive, member, item, root):
    name = item['relative']
    target = root / 'state' / name if item['kind'] == 'FILE' else root / name
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    flags |= os.O_CREAT | os.O_EXCL if item['ordinal'] == 1 else os.O_APPEND
    if item['ordinal'] > 1:
        require(target.lstat().st_size == item['offset'], 'GOLD_TRANSPORT_EXTENT_OFFSET')
    fd = os.open(target, flags, 0o600)
    digest = hashlib.sha256(); total = 0
    with os.fdopen(fd, 'wb') as output, archive.open(member) as source:
        while block := source.read(1024 * 1024):
            total += len(block); require(total <= item['bytes'], 'GOLD_TRANSPORT_EXTENT_SIZE')
            output.write(block); digest.update(block)
        output.flush(); os.fsync(output.fileno())
    require(total == item['bytes'] and digest.hexdigest() == item['sha256'], 'GOLD_TRANSPORT_EXTENT_HASH')


def _restore_parts(archive_root, root, scratch, manifest, passphrase, stack):
    from tools.demo_gold_namespace_checkpoint import inventory_limits, verified_source_trees
    expected = iter(_aux_items(manifest)); files_started = False
    checked = None
    for part in manifest['parts']:
        private = scratch / 'part.input'; plain = scratch / 'part.zip'
        _freeze(archive_root / part['name'], private, part['sha256'], part['archive_bytes'])
        require(private.stat().st_size == part['archive_bytes'], 'GOLD_TRANSPORT_PART_BYTES')
        if manifest['encrypted']:
            checkpoint.decrypt_file(str(private), str(plain), passphrase)
        else:
            os.rename(private, plain)
        require(plain.stat().st_size <= checkpoint.MAX_BYTES + MAX_PART_OVERHEAD,
                'GOLD_TRANSPORT_PART_BUDGET')
        with zipfile.ZipFile(plain) as archive:
            members = archive.infolist()
            names = [value.filename for value in members]
            require(len(names) == part['members'] + 1 and len(set(names)) == len(names)
                    and set(names) == {'part-manifest.json'} | {
                        f'payload/{n:010d}' for n in range(1, part['members'] + 1)},
                    'GOLD_TRANSPORT_MEMBER_SET')
            require(archive.getinfo('part-manifest.json').file_size <= MAX_METADATA_BYTES,
                    'GOLD_TRANSPORT_METADATA_BUDGET')
            raw = archive.read('part-manifest.json'); part_manifest = decode(raw)
            _fields(part_manifest, {'schema', 'ordinal', 'items'})
            require(_canonical(part_manifest) == raw and part_manifest['schema'] == PART_SCHEMA
                    and type(part_manifest['ordinal']) is int and part_manifest['ordinal'] == part['ordinal']
                    and type(part_manifest['items']) is list and len(part_manifest['items']) == part['members'],
                    'GOLD_TRANSPORT_PART_MANIFEST')
            total = 0
            for ordinal, item in enumerate(part_manifest['items'], 1):
                _check_item(item)
                original = next(expected, None)
                if original is None and not files_started:
                    source = _source_runtime(manifest['source_identity'], root)
                    checked = stack.enter_context(verified_source_trees(source))
                    limits = inventory_limits(manifest['inventory'], public_bundle=manifest['public_bundle'],
                        source_attachment=checked.source_attachment(), verified_source_trees=checked)
                    require(limits['pending_source_tree_components'] == [], 'GOLD_TRANSPORT_SOURCE_BUDGET_PENDING')
                    expected = iter(_expected_files(root, manifest)); files_started = True
                    original = next(expected, None)
                require(original is not None and checkpoint.canonical(item) == checkpoint.canonical(original),
                        'GOLD_TRANSPORT_COVERAGE')
                member = f'payload/{ordinal:010d}'; info = archive.getinfo(member)
                require(not info.is_dir() and stat.S_IFMT(info.external_attr >> 16) in (0, stat.S_IFREG)
                        and info.file_size == item['bytes'] and info.compress_type == zipfile.ZIP_STORED
                        and info.flag_bits & 1 == 0, 'GOLD_TRANSPORT_MEMBER_TYPE_OR_SIZE')
                total += info.file_size
                _extract_item(archive, member, item, root)
            require(total == part['payload_bytes'], 'GOLD_TRANSPORT_PART_TOTAL')
        plain.unlink()
        if private.exists():
            private.unlink()
    if not files_started:
        # A directory-only native controller has no FILE item to trigger the
        # source/index transition. Exhaust the auxiliary sequence first, then
        # perform the same original source, budget and full index checks. A
        # missing declared file still leaves an expected item and is rejected.
        require(next(expected, None) is None, 'GOLD_TRANSPORT_INCOMPLETE')
        source = _source_runtime(manifest['source_identity'], root)
        checked = stack.enter_context(verified_source_trees(source))
        limits = inventory_limits(manifest['inventory'], public_bundle=manifest['public_bundle'],
            source_attachment=checked.source_attachment(), verified_source_trees=checked)
        require(limits['pending_source_tree_components'] == [], 'GOLD_TRANSPORT_SOURCE_BUDGET_PENDING')
        expected = iter(_expected_files(root, manifest))
        files_started = True
    require(files_started and next(expected, None) is None, 'GOLD_TRANSPORT_INCOMPLETE')
    return checked


def _report(manifest, state):
    return dict(status='RESTORED_ISOLATED_GOLD_NAMESPACE', schema=SCHEMA,
        namespace=manifest['inventory']['namespace'], source_commit=manifest['inventory']['source_commit'],
        source_uid=manifest['inventory']['source_root']['uid'],
        files=manifest['inventory']['files'], parts=len(manifest['parts']), state=state,
        source_verified=True, source_verification_scope='GIT_BUNDLE_AND_NATIVE_IDENTITY_ONLY',
        release_acceptance_verified=False, original_identity_commitment_verified=True,
        current_identity_matches_original=False, production_resumable=False,
        cross_namespace_atomic=False, complete_gold_set_verified=False,
        submission_enabled=False, economic_authority=False, production_restore_performed=False)


def restore_checkpoint(archive, destination, *, passphrase, expected_sha256):
    from tools.demo_gold_namespace_checkpoint import verify_restored_namespace
    archive = Path(archive).absolute(); _private(archive, directory=True)
    destination = _destination(destination); _hash(expected_sha256)
    encrypted = (archive / 'checkpoint-manifest.enc').exists()
    leaf = 'checkpoint-manifest.' + ('enc' if encrypted else 'json')
    with tempfile.TemporaryDirectory(prefix='.gold-restore-', dir=destination.parent) as temporary:
        stage = Path(temporary); scratch = stage / 'scratch'; scratch.mkdir(mode=0o700)
        root = stage / 'restored'; root.mkdir(mode=0o700); (root / 'state').mkdir(mode=0o700)
        frozen = scratch / 'manifest.input'; plain = scratch / 'manifest.json'
        _freeze(archive / leaf, frozen, expected_sha256, MAX_METADATA_BYTES + 1024 * 1024)
        if encrypted:
            checkpoint.decrypt_file(str(frozen), str(plain), passphrase)
        else:
            os.rename(frozen, plain)
        manifest = _manifest_check(_read_json(plain), encrypted)
        require({p.name for p in archive.iterdir()} == {leaf} | {p['name'] for p in manifest['parts']},
                'GOLD_TRANSPORT_ARCHIVE_SET')
        with ExitStack() as source_trees:
            checked = _restore_parts(archive, root, scratch, manifest, passphrase, source_trees)
            state = verify_restored_namespace(root / 'state', inventory=manifest['inventory'],
                role_receipt=manifest['role_receipt'], public_bundle=manifest['public_bundle'],
                source_attachment=checked.source_attachment(), verified_source_trees=checked,
                cut_request=manifest['cut_request'])
        write_new(root / 'checkpoint-manifest.json', _canonical(manifest))
        report = _report(manifest, state)
        for directory in sorted((p for p in root.rglob('*') if p.is_dir()),
                                key=lambda p: len(p.parts), reverse=True):
            sync(directory)
        sync(root)
        move(root, destination)
    return report


def _recheck_capture(archive, before, uid):
    require(_identity(_private(archive, directory=True, uid=uid)) == before['root']
            and {p.name for p in archive.iterdir()} == set(before['files']), 'GOLD_TRANSPORT_CAPTURE_CHANGED')
    for name, original in before['files'].items():
        require(_identity(_private(archive / name, uid=uid)) == original,
                'GOLD_TRANSPORT_CAPTURE_CHANGED')


def _copy_capture(archive, target, expected_sha256, uid):
    """Copy fixed manifest/parts from the expected role owner, then freeze IO."""
    before = dict(root=_identity(_private(archive, directory=True, uid=uid)), files={})
    leaf = 'checkpoint-manifest.json'
    before['files'][leaf] = _identity(_private(archive / leaf, uid=uid))
    _freeze(archive / leaf, target / leaf, expected_sha256, MAX_METADATA_BYTES, source_uid=uid)
    manifest = _manifest_check(_read_json(target / leaf), False)
    require(manifest['inventory']['source_root']['uid'] == uid
            == manifest['role_receipt']['worker_identity']['uid'], 'GOLD_TRANSPORT_CAPTURE_OWNER')
    require({p.name for p in archive.iterdir()} == {leaf} | {p['name'] for p in manifest['parts']},
            'GOLD_TRANSPORT_ARCHIVE_SET')
    for part in manifest['parts']:
        name = part['name']
        before['files'][name] = _identity(_private(archive / name, uid=uid))
        _freeze(archive / name, target / name, part['sha256'], part['archive_bytes'], source_uid=uid)
        require((target / name).stat().st_size == part['archive_bytes'], 'GOLD_TRANSPORT_PART_BYTES')
    _recheck_capture(archive, before, uid)
    return manifest, before


def _encrypt_file(source, target, passphrase, maximum):
    checkpoint.encrypt_file(str(source), str(target), passphrase)
    target.chmod(0o600)
    with target.open('rb') as stream:
        os.fsync(stream.fileno())
    require(target.stat().st_size <= maximum, 'GOLD_TRANSPORT_FILE_BUDGET')
    return checkpoint.sha(target)


def seal_checkpoint(archive, destination, *, passphrase, expected_sha256, expected_uid):
    """Seal a completed role capture in a separate process; never read live state."""
    checkpoint._check_passphrase_strength(passphrase)
    _integer(expected_uid, 2**31 - 1); _hash(expected_sha256)
    archive = Path(archive).absolute(); destination = _destination(destination)
    require(archive != destination and archive not in destination.parents
            and destination not in archive.parents, 'GOLD_TRANSPORT_SEPARATE_ROOTS')
    with tempfile.TemporaryDirectory(prefix='.gold-seal-', dir=destination.parent) as temporary:
        stage = Path(temporary); frozen = stage / 'capture'; target = stage / 'sealed'
        frozen.mkdir(mode=0o700); target.mkdir(mode=0o700)
        manifest, before = _copy_capture(archive, frozen, expected_sha256, expected_uid)
        original = restore_checkpoint(frozen, stage / 'original', passphrase=None, expected_sha256=expected_sha256)
        parts = []
        for part in manifest['parts']:
            name = f"part-{part['ordinal']:06d}.enc"; output = target / name
            digest = _encrypt_file(frozen / part['name'], output, passphrase,
                checkpoint.MAX_BYTES + MAX_PART_OVERHEAD)
            parts.append(dict(part, name=name, sha256=digest, archive_bytes=output.stat().st_size))
        sealed = dict(manifest, encrypted=True, parts=parts)
        _manifest_check(sealed, True)
        plain = stage / 'manifest.json'; write_new(plain, _canonical(sealed))
        digest = _encrypt_file(plain, target / 'checkpoint-manifest.enc', passphrase,
            MAX_METADATA_BYTES + 1024 * 1024)
        checked = restore_checkpoint(target, stage / 'verified', passphrase=passphrase, expected_sha256=digest)
        require(checked == original, 'GOLD_TRANSPORT_SEAL_ROUNDTRIP')
        sync(target)
        _recheck_capture(archive, before, expected_uid)
        move(target, destination)
    return dict(checked, status='VERIFIED_ENCRYPTED_GOLD_NAMESPACE_ARCHIVE', encrypted=True,
        sha256=digest, capture_sha256=expected_sha256, restore_verified=True)


def create_checkpoint(inventory, destination, *, passphrase, capture_only=False):
    from tools.demo_gold_namespace_checkpoint import CaptureInventory, source_attachment_identity, source_attachments
    from tools.demo_gold_research_inventory import ResearchCaptureInventory
    require(type(inventory) in (CaptureInventory, ResearchCaptureInventory) and not inventory.closed,
            'GOLD_TRANSPORT_LIVE_CAPTURE_REQUIRED')
    require(type(capture_only) is bool, 'GOLD_TRANSPORT_CAPTURE_MODE')
    if not capture_only:
        checkpoint._check_passphrase_strength(passphrase)
    destination = _destination(destination)
    require(inventory.root not in destination.parents, 'GOLD_TRANSPORT_SOURCE_DESTINATION')
    inventory.recheck()
    manifest = dict(schema=SCHEMA, encrypted=not capture_only, inventory=inventory.manifest(),
        role_receipt=inventory.recheck(), public_bundle=inventory.public, cut_request=inventory.cut,
        source_identity=source_attachment_identity(inventory.source_attachment),
        source_blobs=[dict(commit=row['identity']['commit'], bytes=Path(row['bundle_path']).stat().st_size,
            extents=list(_extents(Path(row['bundle_path'])))) for row in source_attachments(inventory.source_attachment)],
        parts=[], submission_enabled=False, economic_authority=False)
    with tempfile.TemporaryDirectory(prefix='.gold-capture-', dir=destination.parent) as temporary:
        stage = Path(temporary); target = stage / 'archive'; scratch = stage / 'scratch'
        target.mkdir(mode=0o700); scratch.mkdir(mode=0o700)
        manifest['parts'] = _write_parts(inventory, manifest, target, scratch, passphrase)
        manifest['role_receipt'] = inventory.recheck()
        _manifest_check(manifest, not capture_only)
        plain = scratch / 'manifest.json'; write_new(plain, _canonical(manifest))
        leaf = 'checkpoint-manifest.' + ('json' if capture_only else 'enc')
        if capture_only:
            os.rename(plain, target / leaf)
        else:
            checkpoint.encrypt_file(str(plain), str(target / leaf), passphrase)
            (target / leaf).chmod(0o600)
        with (target / leaf).open('rb') as stream:
            os.fsync(stream.fileno())
        digest = checkpoint.sha(target / leaf)
        report = restore_checkpoint(target, stage / 'verified', passphrase=passphrase, expected_sha256=digest)
        require(report['state'] == inventory.semantic_view(), 'GOLD_TRANSPORT_ROUNDTRIP')
        sync(target); inventory.recheck(); move(target, destination)
    return dict(report, status='VERIFIED_GOLD_NAMESPACE_ARCHIVE', encrypted=not capture_only,
                sha256=digest, restore_verified=True)
