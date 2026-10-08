"""Read-only Gold checkpoint evidence; no installer or production restore API.

The trusted role launcher owns the real writer barrier throughout a capture.
A cut document describes that barrier; it cannot establish one. Original files
are never repaired, enrolled, adopted, signed or consumed by this module.
"""
from contextlib import ExitStack, contextmanager, closing
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time

from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import require
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from services.validation.demo_submission import validate_registry
from tools import demo_namespace_checkpoint as checkpoint
from tools.demo_resident_recovery import _check_path
from tools.gold_pilot_retention_store import canonical, present

ROLE_ROOTS = {
    'executor': Path('/var/lib/quantos-demo-executor'),
    'controller': Path('/var/lib/quantos-demo-controller'),
    'issuer': Path('/var/lib/quantos-demo-issuer'),
}
SCHEMA = 'GOLD_NAMESPACE_INVENTORY_V1'
LEGACY_VIEW_SCHEMA = 'GOLD_NAMESPACE_SEMANTIC_VIEW_V1'
ABSENT_PARTITION_VIEW_SCHEMA = 'GOLD_NAMESPACE_SEMANTIC_VIEW_V2'
VIEW_SCHEMA = 'GOLD_NAMESPACE_SEMANTIC_VIEW_V3'
VIEW_SCHEMAS = (LEGACY_VIEW_SCHEMA, ABSENT_PARTITION_VIEW_SCHEMA, VIEW_SCHEMA)
RECEIPT_SCHEMA = 'GOLD_CHECKPOINT_ROLE_RECEIPT_V1'
MAX_EXTENT_BYTES = 64 * 1024**2
MAX_INDEX_BYTES = 8 * 1024**2
MAX_INDEX_ENTRIES = 8192
IDENTITY = ('st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_nlink',
            'st_size', 'st_mtime_ns', 'st_ctime_ns')
IDENTITY_NAMES = ('device', 'inode', 'uid', 'gid', 'mode', 'nlink', 'size', 'mtime_ns', 'ctime_ns')
SOURCE_MAX_EXPANDED_BYTES = checkpoint.MAX_BYTES
SOURCE_MAX_ENTRIES = 10000
SOURCE_TREE_METADATA_BYTES = SOURCE_MAX_ENTRIES * (4096 + 128)
SOURCE_PROCESS_MEMORY_BYTES = 2 * 1024**3
SOURCE_PROCESS_CPU_SECONDS = 120
SOURCE_PROCESS_WALL_SECONDS = 180
SOURCE_PROCESS_OUTPUT_BYTES = 65536
_LIMITED_EXEC = '''import os,resource,sys
memory,cpu,filesize = (int(value) for value in sys.argv[1:4])
for kind,limit in ((resource.RLIMIT_AS,memory),(resource.RLIMIT_CPU,cpu),
    (resource.RLIMIT_FSIZE,filesize),(resource.RLIMIT_NOFILE,128),(resource.RLIMIT_CORE,0)):
    resource.setrlimit(kind,(limit,limit))
os.execve(sys.argv[4],sys.argv[4:],os.environ)
'''
_VERIFY_SOURCE = '''import json,sys
sys.path.insert(0,sys.argv[1])
from tools.gold_bar_definition import verify_source
print(json.dumps(verify_source(sys.argv[2],json.loads(sys.argv[3])),sort_keys=True))
'''


def _source_process(command, *, output_limit=SOURCE_PROCESS_OUTPUT_BYTES):
    """Bound the complete child process group, including native Git helpers.

    The exec wrapper installs limits before Git can unpack historical objects.
    No preexec_fn runs Python inside a fork of a potentially threaded parent.
    These are additional source-import safeguards, not enlarged pilot quotas.
    """
    environment = dict(PATH='/usr/bin:/bin', LANG='C', GIT_CONFIG_GLOBAL='/dev/null',
        GIT_CONFIG_SYSTEM='/dev/null', GIT_CONFIG_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0',
        GIT_NO_REPLACE_OBJECTS='1', GIT_ATTR_NOSYSTEM='1', PYTHONDONTWRITEBYTECODE='1',
        OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
    args = [sys.executable, '-I', '-c', _LIMITED_EXEC, str(SOURCE_PROCESS_MEMORY_BYTES),
        str(SOURCE_PROCESS_CPU_SECONDS), str(SOURCE_MAX_EXPANDED_BYTES), *command]
    deadline = time.monotonic() + SOURCE_PROCESS_WALL_SECONDS
    child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=environment, start_new_session=True)
    result = {'stdout': bytearray(), 'stderr': bytearray()}
    try:
        with selectors.DefaultSelector() as selector:
            for stream, name in ((child.stdout, 'stdout'), (child.stderr, 'stderr')):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                require(remaining > 0, 'GOLD_CHECKPOINT_SOURCE_PROCESS_TIMEOUT')
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    limit = output_limit if key.data == 'stdout' else SOURCE_PROCESS_OUTPUT_BYTES
                    require(len(result[key.data]) + len(chunk) <= limit,
                        'GOLD_CHECKPOINT_SOURCE_PROCESS_OUTPUT')
                    result[key.data].extend(chunk)
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'GOLD_CHECKPOINT_SOURCE_PROCESS_TIMEOUT')
            try:
                code = child.wait(timeout=remaining)
            except subprocess.TimeoutExpired as exc:
                raise ValueError('GOLD_CHECKPOINT_SOURCE_PROCESS_TIMEOUT') from exc
            require(code == 0, 'GOLD_CHECKPOINT_SOURCE_PROCESS_FAILED')
        return bytes(result['stdout'])
    finally:
        # Kill descendants even if the direct process already exited while a
        # grandchild retained a pipe. Waiting only for Git would leave them live.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()
        child.stdout.close(); child.stderr.close()


def _source_git(arguments, *, output_limit=SOURCE_PROCESS_OUTPUT_BYTES):
    return _source_process(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null',
        '-c', 'core.attributesFile=/dev/null', '-c', 'transfer.unpackLimit=0',
        '-c', 'fetch.unpackLimit=0', '-c', 'gc.auto=0', '-c', 'maintenance.auto=0',
        *map(str, arguments)], output_limit=output_limit)


def _verify_source(root, expected):
    """Run the unchanged native identity validator inside the same limits."""
    raw = _source_process([sys.executable, '-I', '-c', _VERIFY_SOURCE,
        str(Path(__file__).resolve().parents[1]), str(root), canonical(expected).decode()])
    return decode(raw)


def _git_tree(root, commit):
    """Read sizes before checkout, with a bounded native Git tree listing."""
    raw = _source_git(['-C', root, 'ls-tree', '-r', '-l', '-z', '--full-tree', commit],
        output_limit=SOURCE_TREE_METADATA_BYTES)
    result = []; directories = set(); size = 0
    require(raw.endswith(b'\0'), 'GOLD_CHECKPOINT_SOURCE_TREE')
    for line in raw[:-1].split(b'\0'):
        metadata, name = line.split(b'\t', 1)
        mode, kind, blob, count = metadata.split()
        relative = name.decode('utf-8'); path = checkpoint.safe_relative(relative)
        require(len(name) <= 4096 and '.git' not in path.parts and kind == b'blob'
            and mode in (b'100644', b'100755') and re.fullmatch(b'[a-f0-9]{40}', blob)
            and re.fullmatch(b'[0-9]+', count), 'GOLD_CHECKPOINT_SOURCE_TREE')
        length = int(count); size += length
        directories.update(str(parent) for parent in path.parents if str(parent) != '.')
        result.append(dict(relative=relative, mode=mode.decode(), blob_sha1=blob.decode(), bytes=length))
        require(len(result) + len(directories) + 1 <= SOURCE_MAX_ENTRIES
            and length <= SOURCE_MAX_EXPANDED_BYTES and size <= SOURCE_MAX_EXPANDED_BYTES,
            'GOLD_CHECKPOINT_SOURCE_TREE_BUDGET')
    require(len({row['relative'] for row in result}) == len(result), 'GOLD_CHECKPOINT_SOURCE_TREE')
    return sorted(result, key=lambda row: row['relative'])


def _tree_snapshot(root, tree):
    names = set()
    pending = [(root, '')]
    rows = []
    expected = {row['relative']: row for row in tree}
    while pending:
        parent, prefix = pending.pop()
        for path in parent.iterdir():
            relative = prefix + '/' + path.name if prefix else path.name
            if relative == '.git':
                require(path.is_dir() and not path.is_symlink(), 'GOLD_CHECKPOINT_SOURCE_TREE')
                continue
            info = path.lstat()
            require(not path.is_symlink(), 'GOLD_CHECKPOINT_SOURCE_TREE')
            if stat.S_ISDIR(info.st_mode):
                pending.append((path, relative))
            else:
                names.add(relative)
                require(relative in expected and stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    and info.st_size == expected[relative]['bytes'], 'GOLD_CHECKPOINT_SOURCE_TREE_CHANGED')
                # The bundle uses original SHA-1 Git object identities. Hash
                # raw bytes with Git's blob header as well as the archive's
                # SHA-256; checkout attributes must not relabel another blob.
                blob = hashlib.sha1(usedforsecurity=False)
                blob.update(b'blob ' + str(info.st_size).encode('ascii') + b'\0')
                payload = hashlib.sha256(); size = 0
                with path.open('rb') as stream:
                    while chunk := stream.read(1024 * 1024):
                        size += len(chunk)
                        require(size <= expected[relative]['bytes'], 'GOLD_CHECKPOINT_SOURCE_TREE_BUDGET')
                        blob.update(chunk); payload.update(chunk)
                require(size == info.st_size and blob.hexdigest() == expected[relative]['blob_sha1'],
                    'GOLD_CHECKPOINT_SOURCE_TREE_BLOB')
                digest = payload.hexdigest()
                require(_identity(info) == _identity(path.lstat()), 'GOLD_CHECKPOINT_SOURCE_TREE_CHANGED')
                rows.append(dict(expected[relative], sha256=digest))
            require(len(names) + len(pending) <= SOURCE_MAX_ENTRIES, 'GOLD_CHECKPOINT_SOURCE_TREE_BUDGET')
    require(names == set(expected), 'GOLD_CHECKPOINT_SOURCE_TREE_CHANGED')
    return sorted(rows, key=lambda row: row['relative'])


class _VerifiedSourceTrees:
    """Process-local bundle proof with owned temporary checkouts; never JSON."""
    def __init__(self, value):
        self._stack = ExitStack(); self._closed = False
        self._identity = canonical(source_attachment_identity(value))
        self._input = deepcopy(value); self._rows = {}; self._bundles = {}
        self._private = deepcopy(value)
        try:
            scratch = Path(self._stack.enter_context(tempfile.TemporaryDirectory(prefix='gold-checkpoint-tree-')))
            for attachment, private in zip(source_attachments(value), source_attachments(self._private)):
                bundle = Path(attachment['bundle_path']); commit = attachment['identity']['commit']
                require(bundle.is_absolute() and bundle.resolve() == bundle, 'GOLD_CHECKPOINT_SOURCE_PATH')
                info = bundle.lstat()
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not info.st_mode & 0o022,
                    'GOLD_CHECKPOINT_SOURCE_CUSTODY')
                require(info.st_size <= SOURCE_MAX_EXPANDED_BYTES, 'GOLD_CHECKPOINT_SOURCE_TREE_BUDGET')
                require(checkpoint.sha(bundle) == attachment['bundle_sha256'], 'GOLD_CHECKPOINT_SOURCE_BUNDLE_CHANGED')
                self._bundles[commit] = _identity(info)
                target = scratch / commit
                _source_git(['clone', '--quiet', '--no-checkout', bundle, target])
                tree = _git_tree(target, commit)
                _source_git(['-C', target, 'checkout', '--quiet', '--detach', commit])
                _verify_source(target, attachment['identity'])
                private['source_root'] = str(target)
                self._rows[commit] = canonical(_tree_snapshot(target, tree))
            self.recheck(value)
        except BaseException:
            self.close()
            raise

    def recheck(self, attachment):
        require(not self._closed and canonical(source_attachment_identity(attachment)) == self._identity,
            'GOLD_CHECKPOINT_SOURCE_TREE_VERIFICATION')
        for original, private in zip(source_attachments(self._input), source_attachments(self._private)):
            commit = original['identity']['commit']; path = Path(original['bundle_path'])
            require(self._bundles[commit] == _identity(path.lstat())
                and checkpoint.sha(path) == original['bundle_sha256'], 'GOLD_CHECKPOINT_SOURCE_BUNDLE_CHANGED')
            tree = decode(self._rows[commit]); root = Path(private['source_root'])
            _verify_source(root, original['identity'])
            require(canonical(_tree_snapshot(root, tree)) == self._rows[commit],
                'GOLD_CHECKPOINT_SOURCE_TREE_CHANGED')
        return self

    def source_attachment(self):
        self.recheck(self._input)
        return deepcopy(self._private)

    def tree(self, commit):
        self.recheck(self._input)
        require(commit in self._rows, 'GOLD_CHECKPOINT_SOURCE_TREE_VERIFICATION')
        return decode(self._rows[commit])

    def close(self):
        if not self._closed:
            self._stack.close(); self._closed = True

    def __enter__(self):
        return self.recheck(self._input)

    def __exit__(self, *args):
        self.close()


def verified_source_trees(source_attachment):
    """Verify exact bundle trees before expanding any native namespace FILE."""
    return _VerifiedSourceTrees(source_attachment)


def _identity(info, *, directory=False):
    length = 6 if directory else 9
    return {name: getattr(info, key) for name, key in zip(IDENTITY_NAMES[:length], IDENTITY[:length])}


def _hash(value):
    require(type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None,
            'GOLD_CHECKPOINT_HASH')
    return value


def _read(path, uid, maximum):
    _check_path(path, uid, directory=False)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = _identity(os.fstat(stream.fileno()))
        require(before == _identity(path.lstat()) and before['size'] <= maximum,
                'GOLD_CHECKPOINT_FILE_CHANGED_OR_BUDGET')
        raw = stream.read(maximum + 1)
        require(len(raw) == before['size'] and before == _identity(os.fstat(stream.fileno()))
                == _identity(path.lstat()), 'GOLD_CHECKPOINT_FILE_CHANGED_OR_BUDGET')
        return raw




def source_attachments(value):
    """Finite fixed-provider attachments; exact releases, never directory walks."""
    require(type(value) is dict, 'GOLD_CHECKPOINT_SOURCE_ATTACHMENT')
    if value.get('schema') == 'GOLD_CHECKPOINT_SOURCE_SET_V1':
        _fields(value, 'schema current_commit attachments')
        rows = value['attachments']
        require(type(rows) is list and 1 <= len(rows) <= 2, 'GOLD_CHECKPOINT_SOURCE_SET')
        identities = [_single_source_identity(row) for row in rows]
        commits = [row['identity']['commit'] for row in identities]
        require(commits == sorted(set(commits)) and value['current_commit'] in commits,
            'GOLD_CHECKPOINT_SOURCE_SET')
        return tuple(rows)
    _single_source_identity(value)
    return (value,)


def source_current_commit(value):
    rows = source_attachments(value)
    return value['current_commit'] if value['schema'] == 'GOLD_CHECKPOINT_SOURCE_SET_V1' else rows[0]['identity']['commit']


def _source_scope(value, source_commit, public):
    _runtime_context(public, source_commit)
    rows = source_attachments(value)
    expected = {source_commit}
    runtime = public['base_runtime']
    if runtime is not None and 'archive_source_commit' in runtime:
        expected.add(runtime['archive_source_commit'])
    require(source_current_commit(value) == source_commit and
        {row['identity']['commit'] for row in rows} == expected, 'GOLD_CHECKPOINT_SOURCE_COMMIT')


def _runtime_context(public, source_commit):
    """Original runtime grammar, without resolving or advancing an episode."""
    from tools.gold_pilot_closure import _runtime
    base, current = public['base_runtime'], public['current_runtime']
    if base is None or current is None:
        require(base is current is None, 'GOLD_CHECKPOINT_RUNTIME_REQUIRED')
        return
    _runtime(base, source_commit)
    _runtime(current, source_commit)
    ignored = {'episode_id'} if base['schema'] == 'GOLD_BAR_AUTO_EPISODE_INPUT_V1' else set()
    require(canonical({k: v for k, v in base.items() if k not in ignored}) ==
        canonical({k: v for k, v in current.items() if k not in ignored}),
        'GOLD_CHECKPOINT_RUNTIME_CHANGED')


def _source_dependencies(public, namespace, source_attachment):
    """The only omitted control tree is the selected native BAR checkout .git."""
    runtime = public['base_runtime']
    if namespace != 'executor' or runtime is None or 'archive_source_commit' not in runtime:
        return []
    commit = runtime['archive_source_commit']
    attachment = next(row for row in source_attachments(source_attachment) if row['identity']['commit'] == commit)
    return [dict(schema='GOLD_CHECKPOINT_SOURCE_DEPENDENCY_V1',
        relative='strategy-bar-sources/' + commit, commit=commit,
        source_identity_sha256=_digest(_single_source_identity(attachment)),
        omitted_control_directory='.git', reconstruction='PRIVATE_SEMANTIC_CHECKOUT_ONLY')]


def _bar_bounds(extra_bytes=0):
    # Original protected_tree: <=10,000 source members (including directories).
    # Original PreparedBarDefinition: <=256 MiB for Python/lock + five archive
    # files, <=10,000 snapshot files. running.json is the original additional
    # archive registration and has the original gold_cost_review read bound.
    from tools.gold_cost_inputs import MAX_BYTES
    return _bounds(10000 + 6, 10000 + 3, 256 * 1024**2 + MAX_BYTES + extra_bytes)


def _extra_source_bytes(trees, public):
    commit = public['base_runtime']['archive_source_commit']
    return sum(row['bytes'] for row in trees.tree(commit) if not _semantic_source(row['relative']))


def _semantic_source(relative):
    path = Path(relative)
    return relative == 'requirements-ci-linux-aarch64.lock' or (
        len(path.parts) > 1 and path.parts[0] in ('services', 'quantos', 'tools') and path.suffix == '.py')


def _verify_source_payload(root, dependencies, trees, entries):
    """Every extra resident leaf must be the actual blob in the exact bundle."""
    for dependency in dependencies:
        prefix = dependency['relative'] + '/'
        tree = {row['relative']: row for row in trees.tree(dependency['commit'])}
        directories = {str(parent) for name in tree for parent in Path(name).parents if str(parent) != '.'}
        for row in entries:
            if not row['relative'].startswith(prefix):
                continue
            name = row['relative'][len(prefix):]
            if row['kind'] == 'DIRECTORY':
                require(name in directories, 'GOLD_CHECKPOINT_SOURCE_PAYLOAD')
            else:
                require(name in tree and row['logical_bytes'] == tree[name]['bytes']
                    and row['original_sha256'] == tree[name]['sha256'], 'GOLD_CHECKPOINT_SOURCE_PAYLOAD')


def _source_controls(root, dependencies, uid, source_attachment):
    from tools.gold_bar_worker_inputs import protected_tree
    result = []
    for item in dependencies:
        path = root / item['relative'] / '.git'
        _check_path(path, uid, directory=True)
        attachment = next(row for row in source_attachments(source_attachment)
            if row['identity']['commit'] == item['commit'])
        _verify_source(path.parent, attachment['identity'])
        # No config, remote URLs, object contents or hooks enter the checkpoint.
        result.append(dict(relative=path.relative_to(root).as_posix(),
            identity=_identity(path.lstat(), directory=True),
            member_metadata_sha256=_digest(protected_tree(path.parent, uid))))
    return result


def _prepare_source_shadow(shadow, dependencies, source_attachment, trees):
    """Create only Git control metadata in a private, non-resumable read copy."""
    trees.recheck(source_attachment)
    for dependency in dependencies:
        attachment = next(row for row in source_attachments(source_attachment)
            if row['identity']['commit'] == dependency['commit'])
        source = shadow / dependency['relative']
        require(source.is_dir() and not present(source / '.git'), 'GOLD_CHECKPOINT_SOURCE_PAYLOAD')
        private = next(row for row in source_attachments(trees.source_attachment())
            if row['identity']['commit'] == dependency['commit'])
        import shutil
        shutil.copytree(Path(private['source_root']) / '.git', source / '.git')
        for path in (source / '.git', *(source / '.git').rglob('*')):
            require(not path.is_symlink(), 'GOLD_CHECKPOINT_SOURCE_CONTROL')
            path.chmod(0o700 if path.is_dir() else 0o600)
        _verify_source(source, attachment['identity'])


def _bar_archive_view(root, public, uid):
    from tools.gold_strategy_budget_enrollment import load_definition
    runtime = public['base_runtime']
    require({p.name for p in (root / 'strategy-bar-trials').iterdir()} == {runtime['trial_manifest_sha256']}
        and {p.name for p in (root / 'strategy-bar-sources').iterdir()} == {runtime['archive_source_commit']},
        'GOLD_CHECKPOINT_BAR_UNBOUND_INPUTS')
    definition = load_definition(root, runtime, source_commit=public['source_commit'], uid=uid)
    trial = root / 'strategy-bar-trials' / runtime['trial_manifest_sha256']
    if present(trial / 'running.json'):
        # Validate the original retained registration, not merely an extra file.
        from tools.gold_bar_experiment import bound_archive, trial_candidates, _hash
        from quantos.contracts.gold_trial_identity import gold_trial_definition_id
        from tools.gold_cost_review import _read
        manifest, _, args = bound_archive(trial)
        running = decode(_read(trial, 'running.json'))
        _fields(running, 'schema manifest identities')
        expected_manifest = {key: value for key, value in manifest.items() if key not in ('terminal', 'report_sha256')}
        require(running['schema'] == 'GOLD_BAR_RUNNING_ARCHIVE_V1'
            and canonical(running['manifest']) == canonical(expected_manifest), 'GOLD_CHECKPOINT_BAR_REGISTRATION')
        candidates = trial_candidates(args)
        require(type(running['identities']) is list and len(running['identities']) == len(candidates),
            'GOLD_CHECKPOINT_BAR_REGISTRATION')
        for candidate, trial_id, record in zip(candidates, manifest['trial_ids'], running['identities']):
            identity = gold_trial_definition_id(input_sha256=manifest['input_sha256'], candidate=candidate,
                source=manifest['source'])
            require(canonical(record) == canonical(dict(trial_id=trial_id, alpha_id=identity,
                exact_definition_id=identity, family_id=_hash('GOLD_BAR_' + candidate.upper()),
                validation_policy_digest=_hash({'mode': 'TECHNICAL_COMPUTATION_ONLY', 'automatic_selection': False}))),
                'GOLD_CHECKPOINT_BAR_REGISTRATION')
    return dict(definition_sha256=definition['definition_hash'], archive_source_commit=runtime['archive_source_commit'],
        trial_manifest_sha256=runtime['trial_manifest_sha256'], hypothesis_only=True)

def _single_source_identity(value):
    """Portable commitment; runtime file locations are never restore identity."""
    _fields(value, 'schema source_root bundle_path bundle_sha256 identity')
    require(value['schema'] == 'GOLD_CHECKPOINT_VERIFIED_SOURCE_V1', 'GOLD_CHECKPOINT_SOURCE_ATTACHMENT')
    identity = value['identity']
    _fields(identity, 'commit python_source_sha256 dependency_lock_sha256 python_version architecture')
    require(type(identity['commit']) is str and re.fullmatch('[a-f0-9]{40}', identity['commit']) is not None
        and all(type(identity[key]) is str and 0 < len(identity[key]) <= 128
            for key in ('python_version', 'architecture')), 'GOLD_CHECKPOINT_SOURCE_IDENTITY')
    for key in ('python_source_sha256', 'dependency_lock_sha256'):
        _hash(identity[key])
    return dict(schema='GOLD_CHECKPOINT_SOURCE_IDENTITY_V1',
        bundle_sha256=_hash(value['bundle_sha256']), identity=deepcopy(identity))


def source_attachment_identity(value):
    """Portable source set commitment; runtime addresses are never identity."""
    rows = source_attachments(value)
    if value['schema'] != 'GOLD_CHECKPOINT_SOURCE_SET_V1':
        return _single_source_identity(value)
    return dict(schema='GOLD_CHECKPOINT_SOURCE_SET_IDENTITY_V1', current_commit=value['current_commit'],
        sources=[{key: item[key] for key in ('bundle_sha256', 'identity')}
            for item in (_single_source_identity(row) for row in rows)])

def _source_attachment(value, *, verify_bundle=True):
    require(value is not None, 'GOLD_CHECKPOINT_SOURCE_ATTACHMENT_REQUIRED')
    portable = source_attachment_identity(value)
    if value['schema'] == 'GOLD_CHECKPOINT_SOURCE_SET_V1':
        for row in source_attachments(value):
            _source_attachment(row, verify_bundle=verify_bundle)
        return _digest(portable)
    source = Path(value['source_root']); bundle = Path(value['bundle_path'])
    require(source.is_absolute() and source.resolve() == source and bundle.is_absolute()
            and bundle.resolve() == bundle, 'GOLD_CHECKPOINT_SOURCE_PATH')
    info = bundle.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not info.st_mode & 0o022,
            'GOLD_CHECKPOINT_SOURCE_CUSTODY')
    before = _identity(info)
    require(checkpoint.sha(bundle) == _hash(value['bundle_sha256']), 'GOLD_CHECKPOINT_SOURCE_BUNDLE_CHANGED')
    _verify_source(source, value['identity'])
    if verify_bundle:
        try:
            with verified_source_trees(value):
                pass
        except (subprocess.SubprocessError, ValueError, FileNotFoundError) as exc:
            raise ValueError('GOLD_CHECKPOINT_SOURCE_BUNDLE_IDENTITY') from exc
    require(before == _identity(bundle.lstat()), 'GOLD_CHECKPOINT_SOURCE_BUNDLE_CHANGED')
    return _digest(portable)


def _role_uid(registry, namespace):
    return 0 if namespace == 'controller' else registry[namespace + '_uid']


def _original_custody(inventory, public):
    """Check captured custody against the registry, never today's inode."""
    uid = _role_uid(public['registry'], inventory['namespace'])
    identity = inventory['source_root']
    _fields(identity, 'path device inode uid gid mode')
    path = identity['path']
    require(type(path) is str and Path(path).is_absolute()
        and str(Path(path)) == path and '..' not in Path(path).parts
        and all(type(identity[key]) is int and identity[key] >= 0
            for key in ('device', 'inode', 'uid', 'gid', 'mode'))
        and identity['inode'] > 0 and identity['uid'] == identity['gid'] == uid
        and stat.S_ISDIR(identity['mode']) and not identity['mode'] & 0o077,
        'GOLD_CHECKPOINT_ORIGINAL_CUSTODY')
    return uid


def _public(value, cut, source, operation, namespace):
    _fields(value, 'schema source_commit registry account_ref_sha256 base_runtime current_runtime records')
    require(value['schema'] == 'GOLD_CHECKPOINT_PUBLIC_CONTEXT_V1' and value['source_commit'] == source
            and type(value['records']) is dict, 'GOLD_CHECKPOINT_PUBLIC_CONTEXT')
    registry = validate_registry(value['registry'])
    _hash(value['account_ref_sha256']); _hash(operation)
    _fields(cut, 'schema operation_sha256 source_commit registry_sha256 public_bundle_sha256 roles '
                'writer_barrier_sha256 opening_at_ms submission_enabled economic_authority')
    require(cut['schema'] == 'GOLD_CHECKPOINT_CUT_REQUEST_V1' and cut['operation_sha256'] == operation
            and cut['source_commit'] == source and cut['registry_sha256'] == _digest(registry)
            and cut['public_bundle_sha256'] == _digest(value) and type(cut['roles']) is list
            and len(cut['roles']) == len(set(cut['roles'])) and namespace in cut['roles']
            and set(cut['roles']) <= {'executor', 'controller', 'issuer', 'research'}
            and type(cut['opening_at_ms']) is int and cut['opening_at_ms'] > 0
            and cut['submission_enabled'] is cut['economic_authority'] is False,
            'GOLD_CHECKPOINT_CUT_BINDING')
    _hash(cut['writer_barrier_sha256'])
    uid = _role_uid(registry, namespace)
    require(os.getresuid() == (uid, uid, uid) and os.getresgid() == (uid, uid, uid)
            and set(os.getgroups()) <= {uid}, 'GOLD_CHECKPOINT_ROLE_REQUIRED')
    return uid


def _enrollment(root, uid, public):
    from live.execution import demo_gold_pilot as pilot, demo_gold_pilot_enrollment as enrollment
    path = root / 'gold-pilot-request.json'; area = root / 'gold-pilot'
    if not present(path):
        require(not present(area), 'GOLD_CHECKPOINT_ENROLLMENT_MISSING')
        return None, None
    request = decode(_read(path, uid, 32768))
    enrollment._request_check(request, source_commit=public['source_commit'], account=public['account_ref_sha256'])
    if not present(area):
        return dict(status='REQUESTED', request=request, request_sha256=_digest(request), configuration_sha256=None), dict(request=request, configuration=None)
    _check_path(area, uid, directory=True)
    configuration = decode(_read(area / 'configuration.json', uid, 32768))
    receipt = decode(_read(area / 'enrollment.json', uid, 32768))
    intent = decode(_read(area / 'enrollment-intent.json', uid, enrollment.INTENT_MAXIMUM)) if present(area / 'enrollment-intent.json') else None
    config = enrollment.completed_configuration_records(configuration=configuration, receipt=receipt,
        intent=intent, request=request)
    if present(area / 'binding.json'):
        require(decode(_read(area / 'binding.json', uid, 32768)) ==
                dict(schema='GOLD_DEMO_PILOT_BINDING_V1', configuration_sha256=_digest(config)),
                'GOLD_CHECKPOINT_PILOT_BINDING')
    stop = None
    if present(area / 'stopped.json'):
        stop = decode(_read(area / 'stopped.json', uid, 32768))
        pilot.validate_stop(stop, config=config, now_ms=stop['observed_at_ms'])
    if 'pilot' in public['records']:
        require(canonical(public['records']['pilot']) == canonical(dict(request=request,
            configuration=configuration, receipt=receipt, intent=intent, stop=stop)),
            'GOLD_CHECKPOINT_PUBLIC_PILOT_CHANGED')
    return dict(status='ENROLLED', request=request, request_sha256=_digest(request), configuration_sha256=_digest(config),
        receipt_sha256=_digest(receipt), intent_sha256=None if intent is None else _digest(intent),
        configuration=config, stopped=stop), dict(request=request, configuration=config)


def _public_pilot(public):
    """Original public records, never an alternate executor initialization."""
    if 'pilot' not in public['records']:
        return None, None
    from live.execution import demo_gold_pilot_enrollment as enrollment, demo_gold_pilot as pilot
    value = public['records']['pilot']
    _fields(value, 'request configuration intent receipt stop')
    request, receipt, intent = value['request'], value['receipt'], value['intent']
    enrollment._request_check(request, source_commit=public['source_commit'], account=public['account_ref_sha256'])
    config = enrollment.completed_configuration_records(configuration=value['configuration'], receipt=receipt,
        intent=intent, request=request)
    stop = value['stop']
    if stop is not None:
        pilot.validate_stop(stop, config=config, now_ms=stop['observed_at_ms'])
    return dict(status='ENROLLED', request=request, request_sha256=_digest(request),
        configuration_sha256=_digest(config), receipt_sha256=_digest(receipt),
        intent_sha256=None if intent is None else _digest(intent), configuration=config, stopped=stop), \
        dict(request=request, configuration=config)



def _enrollment_limits():
    from live.execution.demo_gold_pilot_enrollment import INTENT_MAXIMUM
    from tools.gold_pilot_closure import MAX_INPUT_BYTES
    from tools.gold_pilot_reobservation import FILES
    return {'configuration.json': 32768, 'enrollment.json': 32768,
        'enrollment-intent.json': INTENT_MAXIMUM, 'binding.json': 32768, 'stopped.json': 32768,
        'closure-intent.json': MAX_INPUT_BYTES, 'closure-origin.json': MAX_INPUT_BYTES,
        'closure-result.json': MAX_INPUT_BYTES, **FILES}


def _bounds(files, directories, size):
    return dict(max_files=files, max_directories=directories, max_bytes=size)


def _worker_identity():
    fields = Path('/proc/self/stat').read_text().rsplit(') ', 1)[1].split()
    return dict(uid=os.geteuid(), gid=os.getegid(), groups=os.getgroups(), pid=os.getpid(),
        start_ticks=int(fields[19]))

def _component(relative, namespace, *, directory):
    path = checkpoint.safe_relative(relative)
    first = path.parts[0]
    files, folders, _ = checkpoint.scope(namespace)
    from tools.demo_gold_checkpoint_components import component
    gold_component = component(relative, namespace, directory=directory)
    if gold_component is not None:
        return gold_component
    if first in folders or relative in files or relative in checkpoint.EPHEMERAL or any(
            relative.endswith(suffix) and relative[:-len(suffix)] in files
            for suffix in ('-wal', '-shm', '-journal')):
        require(directory or relative in checkpoint.EPHEMERAL or checkpoint.allowed(relative, namespace)
                or any(relative.endswith(s) and checkpoint.allowed(relative[:-len(s)], namespace)
                       for s in ('-wal', '-shm', '-journal')), 'GOLD_CHECKPOINT_COMPONENT_UNKNOWN')
        return 'ordinary'
    if namespace in ('executor', 'controller') and first in ('strategy-decisions', 'strategy-feedback-deliveries'):
        require(namespace == 'controller' or first == 'strategy-decisions', 'GOLD_CHECKPOINT_COMPONENT_UNKNOWN')
        require((directory and relative == first) or (not directory and len(path.parts) == 2
            and re.fullmatch('[a-f0-9]{64}\\.json', path.name)), 'GOLD_CHECKPOINT_COMPONENT_UNKNOWN')
        return 'legacy_feedback'
    if namespace == 'executor' and (first == 'gold-pilot' or relative == 'gold-pilot-request.json'):
        limits = _enrollment_limits()
        valid = relative == 'gold-pilot-request.json' or directory and relative in ('gold-pilot', 'gold-pilot/enrollment-reviews')
        valid = valid or len(path.parts) == 2 and path.name in limits
        valid = valid or len(path.parts) == 3 and path.parts[1] == 'enrollment-reviews' and re.fullmatch('[a-f0-9]{64}\\.json', path.name)
        require(valid, 'GOLD_CHECKPOINT_COMPONENT_UNKNOWN')
        return 'enrollment'
    from tools.gold_pilot_retention_store import AREA, AUDIT, MARKER
    if namespace in ('executor', 'controller') and first in (AREA, AUDIT, MARKER):
        return 'retention'
    if namespace == 'executor' and first in ('strategy-bar-trials', 'strategy-bar-sources'):
        if first == 'strategy-bar-trials':
            from tools.gold_bar_worker_inputs import TRIAL_FILES
            require((directory and (len(path.parts) == 1 or len(path.parts) == 2
                and re.fullmatch('[a-f0-9]{64}', path.name))) or (not directory and len(path.parts) == 3
                and re.fullmatch('[a-f0-9]{64}', path.parts[1]) and path.name in (*TRIAL_FILES, 'running.json')),
                'GOLD_CHECKPOINT_BAR_TRIAL_BOUND_REQUIRED')
        else:
            valid = directory and len(path.parts) == 1
            if len(path.parts) >= 2 and re.fullmatch('[a-f0-9]{40}', path.parts[1]):
                valid = (directory and len(path.parts) == 2) or (len(path.parts) >= 3 and '.git' not in path.parts[2:])
            require(valid, 'GOLD_CHECKPOINT_BAR_SOURCE_BOUND_REQUIRED')
        return 'bar_inputs'
    raise ValueError('GOLD_CHECKPOINT_COMPONENT_BOUND_REQUIRED:' + first)


def _scan(root, uid, namespace, bounds, *, source_dependencies=(), source_trees=None):
    pending = [root]; entries = []; used = {name: [0, 0, 0] for name in bounds}
    omitted = {item['relative'] + '/.git' for item in source_dependencies}
    source_files = {}; source_directories = set(); prefixes = []
    for item in source_dependencies:
        require(isinstance(source_trees, _VerifiedSourceTrees), 'GOLD_CHECKPOINT_SOURCE_TREE_VERIFICATION')
        prefix = item['relative'] + '/'; prefixes.append(prefix)
        for leaf in source_trees.tree(item['commit']):
            source_files[prefix + leaf['relative']] = leaf
            source_directories.update(prefix + str(parent) for parent in Path(leaf['relative']).parents
                if str(parent) != '.')
    while pending:
        area = pending.pop()
        for path in sorted(area.iterdir()):
            info = path.lstat(); directory = stat.S_ISDIR(info.st_mode)
            _check_path(path, uid, directory=directory)
            relative = path.relative_to(root).as_posix()
            if relative in omitted:
                require(directory, 'GOLD_CHECKPOINT_SOURCE_CONTROL')
                continue
            if any(relative.startswith(prefix) for prefix in prefixes):
                require(relative in (source_directories if directory else source_files),
                    'GOLD_CHECKPOINT_SOURCE_PAYLOAD')
                if not directory:
                    require(info.st_size == source_files[relative]['bytes'], 'GOLD_CHECKPOINT_SOURCE_PAYLOAD')
            component = _component(relative, namespace, directory=directory)
            require(component in bounds, 'GOLD_CHECKPOINT_COMPONENT_BOUND_REQUIRED')
            position = 1 if directory else 0
            used[component][position] += 1
            limit = 'max_directories' if directory else 'max_files'
            require(used[component][position] <= bounds[component][limit], 'GOLD_CHECKPOINT_FILE_BUDGET')
            row = dict(kind='DIRECTORY' if directory else 'FILE', component=component,
                relative=relative, source_identity=_identity(info, directory=directory))
            if directory:
                pending.append(path)
            else:
                used[component][2] += info.st_size
                require(used[component][2] <= bounds[component]['max_bytes'], 'GOLD_CHECKPOINT_BYTE_BUDGET')
                if relative in checkpoint.EPHEMERAL:
                    require(info.st_size == 0, 'CHECKPOINT_NONEMPTY_RUNTIME_LOCK')
                digest = checkpoint.sha(path)
                require(row['source_identity'] == _identity(path.lstat()), 'GOLD_CHECKPOINT_FILE_CHANGED')
                row.update(original_sha256=digest, stored_sha256=digest, logical_bytes=info.st_size,
                    snapshot_method='STABLE_FILE_READ', extents=[])
            entries.append(row)
    return sorted(entries, key=lambda row: row['relative'])


def _source_digest(entries):
    return _digest([{key: value for key, value in row.items()
                     if key not in ('stored_sha256', 'snapshot_method', 'extents')}
                    for row in entries])


def _extents(path):
    result = []; offset = 0
    with path.open('rb') as stream:
        while True:
            raw = stream.read(MAX_EXTENT_BYTES)
            if not raw and result:
                break
            result.append(dict(ordinal=len(result) + 1, offset=offset, bytes=len(raw),
                               sha256=hashlib.sha256(raw).hexdigest()))
            offset += len(raw)
            if not raw:
                break
    return result


def _write_index(directory, entries):
    directory.mkdir(mode=0o700)
    rows = []; size = 0; shards = []

    def write():
        if not rows:
            return
        name = f'{len(shards) + 1:06d}.jsonl'; raw = b''.join(rows)
        with (directory / name).open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        shards.append(dict(ordinal=len(shards) + 1, name='index/' + name,
            sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), entries=len(rows),
            first_key=decode(rows[0])['relative'], last_key=decode(rows[-1])['relative']))
    for value in entries:
        raw = canonical(value)
        require(len(raw) <= MAX_INDEX_BYTES, 'GOLD_CHECKPOINT_INDEX_ENTRY_BUDGET')
        if rows and (len(rows) == MAX_INDEX_ENTRIES or size + len(raw) > MAX_INDEX_BYTES):
            write(); rows = []; size = 0
        rows.append(raw); size += len(raw)
    write()
    return shards


def _index_rows(parent, descriptors, uid, *, original_private=True):
    require(type(original_private) is bool, 'GOLD_CHECKPOINT_INDEX_CUSTODY')
    require(type(descriptors) is list and descriptors, 'GOLD_CHECKPOINT_INDEX')
    previous = None
    for ordinal, item in enumerate(descriptors, 1):
        _fields(item, 'ordinal name sha256 bytes entries first_key last_key')
        require(type(item['ordinal']) is int and item['ordinal'] == ordinal
                and item['name'] == f'index/{ordinal:06d}.jsonl'
                and type(item['entries']) is int and 1 <= item['entries'] <= MAX_INDEX_ENTRIES
                and type(item['bytes']) is int and 0 < item['bytes'] <= MAX_INDEX_BYTES,
                'GOLD_CHECKPOINT_INDEX')
        raw = _read(parent / item['name'], uid, MAX_INDEX_BYTES)
        require(len(raw) == item['bytes'] and hashlib.sha256(raw).hexdigest() == item['sha256'],
                'GOLD_CHECKPOINT_INDEX_CHANGED')
        lines = raw.splitlines(keepends=True)
        require(len(lines) == item['entries'], 'GOLD_CHECKPOINT_INDEX_COUNT')
        first = last = None
        for line in lines:
            row = decode(line)
            require(line == canonical(row), 'GOLD_CHECKPOINT_INDEX_CANONICAL')
            fields = 'kind component relative source_identity'
            require(row.get('kind') in ('DIRECTORY', 'FILE'), 'GOLD_CHECKPOINT_INDEX_KIND')
            if row['kind'] == 'FILE':
                fields += ' original_sha256 stored_sha256 logical_bytes snapshot_method extents'
            _fields(row, fields)
            name = row['relative']; checkpoint.safe_relative(name)
            require(len(name.encode('utf-8')) <= 4096 and type(row['component']) is str
                and len(row['component']) <= 64, 'GOLD_CHECKPOINT_INDEX_PATH')
            identity = row['source_identity']
            names = IDENTITY_NAMES if row['kind'] == 'FILE' else IDENTITY_NAMES[:6]
            _fields(identity, ' '.join(names))
            require(all(type(identity[key]) is int and -2**63 <= identity[key] < 2**64 for key in names)
                and all(identity[key] >= 0 for key in names if key not in ('mtime_ns', 'ctime_ns')),
                'GOLD_CHECKPOINT_INDEX_IDENTITY')
            require((stat.S_ISDIR(identity['mode']) if row['kind'] == 'DIRECTORY' else stat.S_ISREG(identity['mode']))
                and not identity['mode'] & (0o077 if original_private else 0o022)
                and (row['kind'] == 'DIRECTORY' or identity['nlink'] == 1),
                'GOLD_CHECKPOINT_INDEX_IDENTITY')
            if row['kind'] == 'FILE':
                require(type(row['logical_bytes']) is int and row['logical_bytes'] == identity['size']
                    and row['snapshot_method'] == 'STABLE_FILE_READ'
                    and _hash(row['original_sha256']) == _hash(row['stored_sha256']), 'GOLD_CHECKPOINT_INDEX_FILE')
                require(type(row['extents']) is list and row['extents'], 'GOLD_CHECKPOINT_INDEX_EXTENTS')
                offset = 0
                for number, extent in enumerate(row['extents'], 1):
                    _fields(extent, 'ordinal offset bytes sha256')
                    require(type(extent['ordinal']) is int and extent['ordinal'] == number
                        and type(extent['offset']) is int and extent['offset'] == offset
                        and type(extent['bytes']) is int and 0 <= extent['bytes'] <= MAX_EXTENT_BYTES
                        and (extent['bytes'] > 0 or len(row['extents']) == 1 and row['logical_bytes'] == 0),
                        'GOLD_CHECKPOINT_INDEX_EXTENTS')
                    _hash(extent['sha256']); offset += extent['bytes']
                require(offset == row['logical_bytes'], 'GOLD_CHECKPOINT_INDEX_EXTENTS')
            require(previous is None or previous < name, 'GOLD_CHECKPOINT_INDEX_ORDER')
            first = name if first is None else first; last = previous = name
            yield row
        require(first == item['first_key'] and last == item['last_key'], 'GOLD_CHECKPOINT_INDEX_RANGE')



def _semantic_shadow(root, scratch, entries):
    """Only private database copies are opened by original SQLite readers."""
    for row in entries:
        relative = row['relative']; path = root / relative; target = scratch / relative
        if row['kind'] == 'DIRECTORY':
            target.mkdir(parents=True, mode=0o700, exist_ok=True)
        elif Path(relative).suffix in ('.sqlite3', '.db'):
            target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            frozen, _, _ = checkpoint.snapshot_readonly_sqlite(path, scratch)
            os.rename(frozen, target)
        elif row['component'] in ('ordinary', 'bar_inputs', 'bar_capacity', 'bar_history',
                'bar_observations', 'bar_tails', 'enrollment') and not relative.endswith(('-wal', '-shm', '-journal')):
            target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            checkpoint.copy_database_member(path, target, path.lstat())


def _restored_snapshot(root, expected, uid):
    observed = {}; pending = [root]
    while pending:
        area = pending.pop()
        for path in area.iterdir():
            name = path.relative_to(root).as_posix()
            require(name in expected, 'GOLD_CHECKPOINT_RESTORE_EXTRA')
            row = expected[name]; directory = row['kind'] == 'DIRECTORY'
            _check_path(path, uid, directory=directory)
            observed[name] = _identity(path.lstat(), directory=directory)
            if directory:
                pending.append(path)
            else:
                require(path.stat().st_size == row['logical_bytes'] and checkpoint.sha(path) == row['stored_sha256']
                        and _extents(path) == row['extents'], 'GOLD_CHECKPOINT_RESTORE_CONTENT')
                require(observed[name] == _identity(path.lstat()), 'GOLD_CHECKPOINT_RESTORE_CHANGED')
    require(set(observed) == set(expected), 'GOLD_CHECKPOINT_RESTORE_MISSING')
    return observed


def _terminal_summary(value):
    if value is None:
        return None
    return dict(schema=value.get('schema', 'GOLD_PILOT_CLOSURE_VIEW'), status=value['status'],
        intent_sha256=value['intent_sha256'], view_sha256=_digest(value),
        claim_sha256=None if value['claim'] is None else _digest(value['claim']),
        result_sha256=None if value['result'] is None else _digest(value['result']))


def _legacy_original_scan(root, uid, entries):
    """Build historical metadata only from the authenticated original index.

    Actual restored bytes/custody have already been checked against that index.
    The production reader never receives this historical projection.
    """
    from tools.gold_feedback_legacy import _scan
    actual = _scan(root, 'strategy-decisions', uid)
    rows = {row['relative']: row for row in entries if row['relative'] == 'strategy-decisions'
        or row['relative'].startswith('strategy-decisions/')}
    if actual is None:
        require(not rows, 'GOLD_CHECKPOINT_LEGACY_ORIGINAL_IDENTITY_CHANGED')
        return None
    directory = rows.pop('strategy-decisions', None)
    require(directory is not None and directory['kind'] == 'DIRECTORY', 'GOLD_CHECKPOINT_IDENTITY_CAPTURE_REQUIRED')
    records = {}
    for key, record in actual['records'].items():
        row = rows.pop('strategy-decisions/' + key + '.json', None)
        require(row is not None and row['kind'] == 'FILE' and row['snapshot_method'] == 'STABLE_FILE_READ'
            and row['original_sha256'] == row['stored_sha256'] == hashlib.sha256(record['raw']).hexdigest(),
            'GOLD_CHECKPOINT_LEGACY_ORIGINAL_IDENTITY_CHANGED')
        records[key] = dict(record, identity=tuple(row['source_identity'][name] for name in IDENTITY_NAMES))
    require(not rows, 'GOLD_CHECKPOINT_LEGACY_ORIGINAL_IDENTITY_CHANGED')
    return dict(identity=tuple(directory['source_identity'][name] for name in IDENTITY_NAMES[:6]), records=records)


def _retention_content_view(store, *, closure, reobservation):
    from tools.gold_pilot_retention import validate_retained_result, _feedback_decision
    from tools.gold_pilot_reobservation import validate_expired_result
    checkpoint_before = store.metadata(); digest = hashlib.sha256(); partial = []
    allowed = ('origin', 'decision', 'result') if store.role == 'executor' else ('result', 'approval', 'delivery')
    for ordinal, body in store.conn.execute('SELECT ordinal,claim FROM frames ORDER BY ordinal'):
        claim = decode(body); values = {}
        for kind, identity in store.conn.execute('SELECT kind,identity FROM records WHERE ordinal=? ORDER BY sequence', (ordinal,)):
            require(kind in allowed, 'GOLD_CHECKPOINT_RETENTION_KIND')
            raw = store.read_claim(claim, kind)
            require(raw is not None and _digest(decode(raw)) == identity, 'GOLD_CHECKPOINT_RETENTION_PAYLOAD')
            digest.update(canonical(dict(ordinal=ordinal, kind=kind, identity=identity,
                file_sha256=hashlib.sha256(raw).hexdigest())))
            values[kind] = decode(raw)
        result = values.get('result')
        if result is None:
            if store.role == 'controller':
                require(ordinal == checkpoint_before['frames'] and not values,
                    'GOLD_CHECKPOINT_RETENTION_PARTIAL_UNPROVEN')
                partial.append(dict(status='CONTROLLER_RESULT_MISSING', ordinal=ordinal))
                continue
            permitted = []
            if reobservation is not None:
                permitted = [reobservation['failed_claim'], reobservation['claim']]
            elif closure is not None and closure['status'] == 'PENDING':
                permitted = [closure['claim']]
            require(ordinal == checkpoint_before['frames'] and claim in permitted,
                'GOLD_CHECKPOINT_RETENTION_PARTIAL_UNPROVEN')
            partial.append(dict(status='VERIFIED_TERMINAL_PARTIAL', ordinal=ordinal,
                proof_sha256=(reobservation or closure)['intent_sha256']))
            continue
        if reobservation is not None and claim == reobservation['failed_claim']:
            require(canonical(result) == canonical(reobservation['expired_result']), 'RETENTION_DIAGNOSTIC_CHANGED')
            decision = validate_expired_result(result, intent=reobservation['intent'])
        else:
            decision = validate_retained_result(result, claim, store.binding)
            _feedback_decision(decision, claim, store.binding)
        if store.role == 'executor':
            require(canonical(values.get('decision')) == canonical(decision), 'FEEDBACK_DECISION_CHANGED')
            if 'origin' in values:
                from services.research.demo_execution_origin import validate
                origin = validate(values['origin'])
                require(origin['status'] == 'CAPTURED' and origin['account_ref_sha256'] == store.binding['account_ref_sha256']
                    and decision['origin_sha256'] == _digest(origin), 'RETENTION_ORIGIN_HASH')
    require(store.metadata() == checkpoint_before, 'GOLD_CHECKPOINT_CUT_CHANGED')
    return dict(binding=deepcopy(store.binding), role=store.role, checkpoint=checkpoint_before,
        payload_chain_sha256=digest.hexdigest(), partial=partial)

def _semantic_view(root, data_root, namespace, public, uid, retention=None, *, original_entries=None,
                   historical_original_root=None, historical_entries=None, semantic_schema=VIEW_SCHEMA,
                   verified_public=None):
    require(semantic_schema in VIEW_SCHEMAS, 'GOLD_CHECKPOINT_SEMANTIC_VIEW')
    enrollment, _ = _enrollment(root, uid, public) if namespace == 'executor' else _public_pilot(public)
    closure_view = reobservation_view = None
    execution = checkpoint.verify_state(data_root, namespace)
    if namespace in ('executor', 'research'):
        require(execution['account_ref_sha256'] == public['account_ref_sha256'], 'GOLD_CHECKPOINT_ACCOUNT')
    view = dict(schema=semantic_schema, enrollment=enrollment, execution=execution,
        issuer=None, bar_inputs=None, retention=None if retention is None else dict(binding=retention.binding,
            role=namespace, checkpoint=retention.metadata()), rotation=None, feedback=None,
        closure=None, reobservation=None, identity_commitments={}, verified_partial=[],
        missing_dependencies=[], mutation_permitted=False)
    if semantic_schema != LEGACY_VIEW_SCHEMA and verified_public is not None:
        view['identity_commitments']['fixed_public_sha256'] = verified_public.snapshot_sha256
    if namespace == 'issuer':
        from live.execution.demo_issuer_store import IssuerStore
        with closing(IssuerStore(data_root / 'issuance.sqlite3', read_only=True)) as store:
            rows = store.snapshot()
            view['issuer'] = dict(records=len(rows), history_sha256=_digest(rows),
                head_sha256=None if not rows else rows[-1]['record_hash'])
    if namespace == 'executor' and enrollment is not None:
        from tools import gold_pilot_reobservation as reobservation, gold_pilot_closure as closure
        base = public['base_runtime']; current = public['current_runtime']
        if reobservation.present_reobservation(root):
            require(base is not None and current is not None, 'GOLD_CHECKPOINT_RUNTIME_REQUIRED')
            arguments = dict(retention=retention, base_runtime=base, source_commit=public['source_commit'],
                account=public['account_ref_sha256'], executor_uid=uid, expected_runtime=current)
            if retention is None and original_entries is not None:
                checked = reobservation._read_reobservation_content(root, **arguments)
                commitment = reobservation.verify_legacy_snapshot(checked,
                    _legacy_original_scan(root, uid, original_entries))
            else:
                checked = reobservation.read_reobservation(root, **arguments)
                commitment = checked['intent']['subject']['legacy_snapshot_sha256']
            if commitment is not None:
                view['identity_commitments']['legacy_snapshot_sha256'] = commitment
            reobservation_view = checked
            view['reobservation'] = _terminal_summary(checked)
            if checked['status'] != 'COMPLETE':
                view['verified_partial'].append(dict(component='reobservation', status=checked['status'],
                    intent_sha256=checked['intent_sha256']))
        elif present(root / 'gold-pilot' / closure.INTENT):
            require(current is not None, 'GOLD_CHECKPOINT_RUNTIME_REQUIRED')
            checked = closure.read_intent(root, retention=retention, runtime=current,
                source_commit=public['source_commit'], account=public['account_ref_sha256'], executor_uid=uid)
            closure_view = checked
            view['closure'] = _terminal_summary(checked)
            if checked['status'] != 'COMPLETE':
                view['verified_partial'].append(dict(component='closure', status=checked['status'],
                    intent_sha256=checked['intent_sha256']))
        else:
            require(not any(present(root / 'gold-pilot' / name) for name in
                ('closure-origin.json', 'closure-result.json')), 'GOLD_CHECKPOINT_CLOSURE_INTENT_MISSING')
    if retention is not None:
        view['retention'] = _retention_content_view(retention, closure=closure_view, reobservation=reobservation_view)
    base = public['base_runtime']
    if namespace == 'executor' and base is not None and 'archive_source_commit' in base:
        require((data_root / 'strategy-bar-trials').is_dir() and (data_root / 'strategy-bar-sources').is_dir(),
            'GOLD_CHECKPOINT_BAR_INPUTS_REQUIRED')
        view['bar_inputs'] = _bar_archive_view(data_root, public, uid)
    if namespace in ('executor', 'controller') and base is not None:
        from tools import demo_gold_checkpoint_components as components
        if components.active(root) or 'rotation' in public['records']:
            original = (_enrollment(root, uid, public) if namespace == 'executor' else _public_pilot(public))[1]
            with ExitStack() as history_stack:
                historical = None
                if semantic_schema != LEGACY_VIEW_SCHEMA and namespace == 'executor' and retention is not None and any(data_root.glob('bar-exit-trigger-*.json')):
                    from tools.demo_gold_historical_position import open_history, HistoricalContextUnproven
                    try:
                        historical = history_stack.enter_context(open_history(data_root, public=public, uid=uid,
                            retention=retention, original_root=historical_original_root,
                            original_entries=historical_entries, verified_public=verified_public,
                            installed_partitions=semantic_schema==VIEW_SCHEMA))
                    except HistoricalContextUnproven:
                        # Missing original full-chain coverage remains an explicit
                        # dependency; it is not replaced by the current balances.
                        historical = None
                checked = components.verify(data_root, namespace=namespace, public=public, original=original,
                    uid=uid, retention=retention, historical=historical)
            if view['bar_inputs'] is not None:
                view['bar_inputs']['components'] = checked
            view['rotation'] = checked['rotation']
            view['verified_partial'].extend(checked['partial'])
            view['missing_dependencies'].extend(sorted({row['status'] for row in checked['partial']
                if row['status'] in ('PARTITION_EVIDENCE_REQUIRED', 'EXECUTION_CONTEXT_REQUIRED',
                    'HISTORICAL_POSITION_CONTEXT_REQUIRED', 'RETENTION_EVIDENCE_REQUIRED', 'DISPATCH_PREFIX_REQUIRED',
                    'HISTORICAL_STATE_CUT_UNPROVEN', 'HISTORICAL_REPORTED_EVENT_CUT_UNPROVEN',
                    'HISTORICAL_DISPATCH_CUT_UNPROVEN', 'HISTORICAL_PARTITION_RECORDS_REQUIRED',
                    'HISTORICAL_REGISTRY_PREFIX_REQUIRED')}))
    return view


def _verified_public_handle(value, public, cut):
    if value is None:
        return None
    from tools.demo_gold_checkpoint_public import require_verified
    handle = require_verified(value, source_commit=public['source_commit'],
        account_ref_sha256=public['account_ref_sha256'])
    require(handle.snapshot_sha256 == _digest(public['records'].get('fixed_public'))
        and handle.public_bundle_sha256 == _digest(public)
        and handle.cut_request_sha256 == _digest(cut), 'GOLD_CHECKPOINT_FIXED_PUBLIC_BINDING')
    handle.recheck()
    return handle


class CaptureInventory:
    """A live read window with source checks; never a reusable permission token."""
    def __init__(self, *, namespace, source_commit, operation_sha256, cut_request, public_bundle, source_attachment, guard,
                 verified_public=None):
        require(callable(guard), 'GOLD_CHECKPOINT_GUARD_REQUIRED')
        self.guard = guard; self.guard()
        require(namespace in ROLE_ROOTS, 'GOLD_CHECKPOINT_ROLE_REQUIRED')
        self.uid = _public(public_bundle, cut_request, source_commit, operation_sha256, namespace)
        self.verified_public = _verified_public_handle(verified_public, public_bundle, cut_request)
        self.source_attachment = deepcopy(source_attachment)
        self.attachment_sha256 = _source_attachment(source_attachment, verify_bundle=False)
        _source_scope(source_attachment, source_commit, public_bundle)
        self.root = Path(ROLE_ROOTS[namespace]).absolute(); self.namespace = namespace
        require(self.root.resolve() == self.root, 'GOLD_CHECKPOINT_ROOT')
        _check_path(self.root, self.uid, directory=True)
        self.root_identity = _identity(self.root.lstat(), directory=True)
        self.source = source_commit; self.operation = operation_sha256
        self.public = deepcopy(public_bundle); self.cut = deepcopy(cut_request)
        self.source_dependencies = _source_dependencies(self.public, namespace, source_attachment)
        self.source_controls = _source_controls(self.root, self.source_dependencies, self.uid, source_attachment)
        self.started = time.time_ns() // 1_000_000
        self.stack = ExitStack(); self.closed = False
        try:
            try:
                self.source_trees = self.stack.enter_context(verified_source_trees(source_attachment))
            except (subprocess.SubprocessError, ValueError, FileNotFoundError) as exc:
                raise ValueError('GOLD_CHECKPOINT_SOURCE_BUNDLE_IDENTITY') from exc
            enrollment, self.original = (_enrollment(self.root, self.uid, self.public) if namespace == 'executor'
                else _public_pilot(self.public))
            self.retention = None
            self.bounds = {'ordinary': _bounds(checkpoint.MAX_FILES, 10000, checkpoint.MAX_BYTES)}
            if self.source_dependencies:
                self.bounds['bar_inputs'] = _bar_bounds(_extra_source_bytes(self.source_trees, self.public))
            from tools import demo_gold_checkpoint_components as components
            if namespace in ('executor', 'controller') and (components.active(self.root) or 'rotation' in self.public['records']):
                self.bounds.update(components.bounds(public=self.public, original=self.original, namespace=namespace))
            if enrollment is not None and namespace == 'executor':
                limits = _enrollment_limits()
                # One request, the fixed pilot leaves, and the original maximum
                # 32 enrollment review leaves; two declared directories.
                self.bounds['enrollment'] = _bounds(1 + len(limits) + 32, 2,
                    32768 + sum(limits.values()) + 32 * 32768)
            from tools.gold_pilot_retention_store import Retention, active
            if any(present(self.root / name) for name in ('strategy-decisions', 'strategy-feedback-deliveries')):
                from tools.gold_feedback_legacy import MAX_RECORDS, MAX_TOTAL_BYTES
                count = 1 if namespace == 'executor' else 2
                self.bounds['legacy_feedback'] = _bounds(count * MAX_RECORDS, count, count * MAX_TOTAL_BYTES)
            if active(self.root):
                require(self.original is not None and self.original['configuration'] is not None
                        and self.public['base_runtime'] is not None, 'GOLD_CHECKPOINT_RETENTION_ORIGINAL_REQUIRED')
                self.retention = self.stack.enter_context(Retention(self.root, original=self.original,
                    base_runtime=self.public['base_runtime'], uid=self.uid, role=namespace, read_only=True, verify_history=True))
                b = self.retention.binding
                self.bounds['retention'] = _bounds(b['max_audit_events'] + 5, 3,
                    b['max_database_bytes'] + b['max_audit_bytes'] + b['max_metadata_allocated_bytes'])
            self.entries = _scan(self.root, self.uid, namespace, self.bounds,
                source_dependencies=self.source_dependencies, source_trees=self.source_trees)
            _verify_source_payload(self.root, self.source_dependencies, self.source_trees, self.entries)
            self._file_names = {row['relative'] for row in self.entries if row['kind'] == 'FILE'}
            self.before = _source_digest(self.entries)
            scratch = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='gold-checkpoint-read-')))
            self.scratch = scratch
            _semantic_shadow(self.root, scratch, self.entries)
            _prepare_source_shadow(scratch, self.source_dependencies, source_attachment, self.source_trees)
            self.view = _semantic_view(self.root, scratch, namespace, self.public, self.uid, self.retention,
                historical_original_root=self.root, historical_entries=self.entries,
                verified_public=self.verified_public)
            for row in self.entries:
                if row['kind'] == 'FILE':
                    path = self.payload_path(row['relative'])
                    row['stored_sha256'] = checkpoint.sha(path)
                    row['logical_bytes'] = path.stat().st_size
                    row['extents'] = _extents(path)
            self.index_directory = scratch / 'index'
            self.index = _write_index(self.index_directory, self.entries)
            self.recheck()
        except BaseException:
            self.close()
            raise

    def iter_entries(self):
        require(not self.closed, 'GOLD_CHECKPOINT_SESSION_CLOSED')
        for row in self.entries:
            yield deepcopy(row)

    def semantic_view(self):
        require(not self.closed, 'GOLD_CHECKPOINT_SESSION_CLOSED')
        return deepcopy(self.view)

    def payload_path(self, relative):
        require(not self.closed, 'GOLD_CHECKPOINT_SESSION_CLOSED')
        checkpoint.safe_relative(relative)
        require(relative in self._file_names,
                'GOLD_CHECKPOINT_PAYLOAD_UNKNOWN')
        self.guard()
        return self.root / relative

    def manifest(self):
        require(not self.closed, 'GOLD_CHECKPOINT_SESSION_CLOSED')
        enrolled = self.view['enrollment']
        config = None if enrolled is None else enrolled.get('configuration')
        identity = None if enrolled is None else dict(request_sha256=enrolled['request_sha256'],
            configuration_sha256=enrolled['configuration_sha256'],
            opening_origin_sha256=None if config is None else config['opening_origin_sha256'],
            started_at_ms=None if config is None else config['started_at_ms'],
            ends_at_ms=None if config is None else config['ends_at_ms'])
        return dict(schema=SCHEMA, operation_sha256=self.operation, cut_request_sha256=_digest(self.cut),
            namespace=self.namespace, source_commit=self.source, registry_sha256=_digest(self.public['registry']),
            account_ref_sha256=self.public['account_ref_sha256'], pilot_identity=identity,
            source_root=dict(path=str(self.root), **{k: v for k, v in self.root_identity.items() if k != 'nlink'}),
            index=deepcopy(self.index), semantic_view=self.semantic_view(),
            source_dependencies=deepcopy(self.source_dependencies),
            component_bounds=deepcopy(self.bounds),
            files=sum(row['kind'] == 'FILE' for row in self.entries),
            directories=sum(row['kind'] == 'DIRECTORY' for row in self.entries),
            payload_bytes=sum(row.get('logical_bytes', 0) for row in self.entries),
            source_attachment_sha256=self.attachment_sha256, submission_enabled=False, economic_authority=False)

    def recheck(self):
        require(not self.closed, 'GOLD_CHECKPOINT_SESSION_CLOSED')
        self.guard()
        if self.verified_public is not None:
            _verified_public_handle(self.verified_public, self.public, self.cut)
        self.source_trees.recheck(self.source_attachment)
        require(self.root_identity == _identity(self.root.lstat(), directory=True)
                and _source_attachment(self.source_attachment, verify_bundle=False) == self.attachment_sha256,
                'GOLD_CHECKPOINT_CUT_CHANGED')
        require(_source_controls(self.root, self.source_dependencies, self.uid, self.source_attachment)
            == self.source_controls, 'GOLD_CHECKPOINT_SOURCE_CONTROL_CHANGED')
        after = _source_digest(_scan(self.root, self.uid, self.namespace, self.bounds,
            source_dependencies=self.source_dependencies, source_trees=self.source_trees))
        require(after == self.before, 'GOLD_CHECKPOINT_CUT_CHANGED')
        if self.retention is not None:
            require(self.retention.metadata() == self.view['retention']['checkpoint'], 'GOLD_CHECKPOINT_CUT_CHANGED')
        return dict(schema=RECEIPT_SCHEMA, operation_sha256=self.operation, cut_request_sha256=_digest(self.cut),
            namespace=self.namespace, source_commit=self.source, registry_sha256=_digest(self.public['registry']),
            inventory_sha256=_digest(self.manifest()), semantic_view_sha256=_digest(self.view), source_attachment_sha256=self.attachment_sha256,
            before_sha256=self.before, after_sha256=after, started_at_ms=self.started,
            completed_at_ms=time.time_ns() // 1_000_000, stable=True,
            worker_identity=_worker_identity(),
            submission_enabled=False, economic_authority=False)

    def close(self):
        if not self.closed:
            self.stack.close(); self.closed = True


@contextmanager
def capture_inventory(**kwargs):
    inventory = CaptureInventory(**kwargs)
    try:
        yield inventory
        inventory.recheck()
    finally:
        inventory.close()


def verify_restored_namespace(root, *, inventory, role_receipt, public_bundle, source_attachment,
                              verified_source_trees=None, cut_request=None):
    """Verify exact original content; private source controls are not restored."""
    if verified_source_trees is None:
        with _VerifiedSourceTrees(source_attachment) as trees:
            return verify_restored_namespace(root, inventory=inventory, role_receipt=role_receipt,
                public_bundle=public_bundle, source_attachment=source_attachment, verified_source_trees=trees,
                cut_request=cut_request)
    require(isinstance(verified_source_trees, _VerifiedSourceTrees), 'GOLD_CHECKPOINT_SOURCE_TREE_VERIFICATION')
    verified_source_trees.recheck(source_attachment)
    if inventory.get('schema') == 'GOLD_RESEARCH_INVENTORY_V1':
        from tools.demo_gold_research_inventory import verify_restored
        return verify_restored(root, inventory=inventory, role_receipt=role_receipt,
            public_bundle=public_bundle, source_attachment=source_attachment,
            trees=verified_source_trees, cut_request=cut_request)
    return _verify_restored_namespace(root, inventory=inventory, role_receipt=role_receipt,
        public_bundle=public_bundle, source_attachment=source_attachment, trees=verified_source_trees,
        cut_request=cut_request)


def _verify_restored_namespace(root, *, inventory, role_receipt, public_bundle, source_attachment, trees,
                               cut_request=None):
    """Verify an isolated tree, never treating original inodes as current ones."""
    limits = inventory_limits(inventory, public_bundle=public_bundle, source_attachment=source_attachment,
        verified_source_trees=trees)
    root = Path(root).absolute()
    require(root.resolve() == root and all(root != path and path not in root.parents
            for path in (*checkpoint.PROTECTED_ROOTS, *ROLE_ROOTS.values())), 'GOLD_CHECKPOINT_ISOLATED_RESTORE_ONLY')
    _fields(inventory, 'schema operation_sha256 cut_request_sha256 namespace source_commit registry_sha256 '
        'account_ref_sha256 pilot_identity source_root index semantic_view component_bounds files directories '
        'payload_bytes source_attachment_sha256 source_dependencies submission_enabled economic_authority')
    require(inventory['schema'] == SCHEMA and inventory['namespace'] in ROLE_ROOTS
            and inventory['submission_enabled'] is inventory['economic_authority'] is False,
            'GOLD_CHECKPOINT_INVENTORY')
    _fields(role_receipt, 'schema operation_sha256 cut_request_sha256 namespace source_commit registry_sha256 '
        'inventory_sha256 semantic_view_sha256 source_attachment_sha256 before_sha256 after_sha256 '
        'started_at_ms completed_at_ms stable worker_identity submission_enabled economic_authority')
    worker = role_receipt['worker_identity']
    _fields(worker, 'uid gid groups pid start_ticks')
    require(all(type(worker[key]) is int and worker[key] >= 0 for key in ('uid', 'gid', 'pid', 'start_ticks'))
        and worker['pid'] > 0 and worker['start_ticks'] > 0 and type(worker['groups']) is list
        and all(type(group) is int and group >= 0 for group in worker['groups']),
        'GOLD_CHECKPOINT_ROLE_RECEIPT')
    original_uid = _original_custody(inventory, public_bundle)
    require(worker['uid'] == worker['gid'] == original_uid
        and worker['groups'] == sorted(set(worker['groups']))
        and set(worker['groups']) <= {original_uid}, 'GOLD_CHECKPOINT_ROLE_RECEIPT')
    require(role_receipt['schema'] == RECEIPT_SCHEMA and role_receipt['stable'] is True
        and role_receipt['inventory_sha256'] == _digest(inventory)
        and role_receipt['semantic_view_sha256'] == _digest(inventory['semantic_view'])
        and all(role_receipt[key] == inventory[key] for key in ('namespace', 'operation_sha256',
            'cut_request_sha256', 'source_commit', 'registry_sha256', 'source_attachment_sha256'))
        and _hash(role_receipt['before_sha256']) == role_receipt['after_sha256']
        and type(role_receipt['started_at_ms']) is int and type(role_receipt['completed_at_ms']) is int
        and 0 < role_receipt['started_at_ms'] <= role_receipt['completed_at_ms']
        and role_receipt['submission_enabled'] is role_receipt['economic_authority'] is False,
        'GOLD_CHECKPOINT_ROLE_RECEIPT')
    _source_scope(source_attachment, inventory['source_commit'], public_bundle)
    require(_digest(source_attachment_identity(trees.source_attachment())) == inventory['source_attachment_sha256'],
            'GOLD_CHECKPOINT_SOURCE_CHANGED')
    require(public_bundle['source_commit'] == inventory['source_commit']
            and _digest(validate_registry(public_bundle['registry'])) == inventory['registry_sha256']
            and public_bundle['account_ref_sha256'] == inventory['account_ref_sha256'],
            'GOLD_CHECKPOINT_PUBLIC_CONTEXT')
    original_root = Path(inventory['source_root']['path'])
    require(root != original_root and original_root not in root.parents, 'GOLD_CHECKPOINT_ISOLATED_RESTORE_ONLY')
    uid = os.geteuid(); _check_path(root, uid, directory=True)
    require(type(inventory['index']) is list and len(inventory['index']) <= limits['max_files'] + limits['max_directories']
        and sum(row['bytes'] for row in inventory['index']) <= limits['max_index_bytes'], 'GOLD_CHECKPOINT_INDEX_BUDGET')
    entries = list(_index_rows(root.parent, inventory['index'], uid))
    require(all(row['source_identity']['uid'] == row['source_identity']['gid'] == original_uid
        for row in entries), 'GOLD_CHECKPOINT_ORIGINAL_CUSTODY')
    _verify_source_payload(root, inventory['source_dependencies'], trees, entries)
    require(sum(row['kind'] == 'FILE' for row in entries) == inventory['files']
        and sum(row['kind'] == 'DIRECTORY' for row in entries) == inventory['directories']
        and sum(row.get('logical_bytes', 0) for row in entries) == inventory['payload_bytes'],
        'GOLD_CHECKPOINT_INDEX_COUNT')
    require(_source_digest(entries) == role_receipt['before_sha256'], 'GOLD_CHECKPOINT_ORIGINAL_INDEX_CHANGED')
    if cut_request is not None:
        require(_digest(cut_request) == inventory['cut_request_sha256'], 'GOLD_CHECKPOINT_CUT_BINDING')
    verified_public = None
    fixed_commitment = inventory['semantic_view']['identity_commitments'].get('fixed_public_sha256')
    if inventory['semantic_view']['schema'] != LEGACY_VIEW_SCHEMA and fixed_commitment is not None:
        require(_hash(fixed_commitment) == _digest(public_bundle['records'].get('fixed_public')),
            'GOLD_CHECKPOINT_FIXED_PUBLIC_BINDING')
        require(cut_request is not None, 'GOLD_CHECKPOINT_CUT_PREIMAGE_REQUIRED')
        from tools.demo_gold_checkpoint_public import validate_archived
        verified_public = validate_archived(public_bundle['records']['fixed_public'], public_bundle=public_bundle,
            cut_request=cut_request, inventory=inventory, role_receipt=role_receipt, index_entries=entries,
            as_of_ms=role_receipt['completed_at_ms'])
        _verified_public_handle(verified_public, public_bundle, cut_request)
    expected = {row['relative']: row for row in entries}
    require(len(expected) == len(entries), 'GOLD_CHECKPOINT_INDEX_DUPLICATE')
    observed = _restored_snapshot(root, expected, uid)
    with ExitStack() as stack:
        from tools.gold_pilot_retention_store import Retention, active
        store = None
        if active(root):
            _, original = (_enrollment(root, uid, public_bundle) if inventory['namespace'] == 'executor'
                else _public_pilot(public_bundle))
            require(original is not None, 'GOLD_CHECKPOINT_RETENTION_ORIGINAL_REQUIRED')
            store = stack.enter_context(Retention(root, original=original, base_runtime=public_bundle['base_runtime'],
                uid=uid, role=inventory['namespace'], read_only=True, verify_history=True))
        scratch = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='gold-checkpoint-restore-read-')))
        _semantic_shadow(root, scratch, entries)
        _prepare_source_shadow(scratch, inventory['source_dependencies'], source_attachment, trees)
        view = _semantic_view(root, scratch, inventory['namespace'], public_bundle, uid, store, original_entries=entries,
            historical_original_root=original_root, historical_entries=entries,
            semantic_schema=inventory['semantic_view']['schema'], verified_public=verified_public)
        require(view == inventory['semantic_view'], 'GOLD_CHECKPOINT_RESTORE_SEMANTICS')
    require(_restored_snapshot(root, expected, uid) == observed, 'GOLD_CHECKPOINT_VERIFIER_MUTATED')
    return view


PART_MAX_MEMBERS = checkpoint.MAX_FILES
PART_MAX_BYTES = checkpoint.MAX_BYTES
PART_METADATA_BYTES = 32 * 1024**2


def part_plan(inventories, *, source_attachment):
    """Deterministic bounded FILE extent transport, without packaging or writes.

    Root transport additionally counts exact index/source attachments against
    the same volume limits. This plan never reserves or raises source quotas.
    """
    require(type(inventories) in (list, tuple) and inventories and
        all(isinstance(value, CaptureInventory) for value in inventories), 'GOLD_CHECKPOINT_PLAN_INPUT')
    require(0 < PART_MAX_MEMBERS <= checkpoint.MAX_FILES and 0 < PART_MAX_BYTES <= checkpoint.MAX_BYTES,
        'GOLD_CHECKPOINT_PART_BOUND')
    require(len({value.namespace for value in inventories}) == len(inventories), 'GOLD_CHECKPOINT_PLAN_DUPLICATE_ROLE')
    commitment = _digest(source_attachment_identity(source_attachment))
    parts = []; total_members = 0; component_bounds = {}; max_members = max_bytes = 0
    binding = None
    for inventory in sorted(inventories, key=lambda value: value.namespace):
        receipt = inventory.recheck(); manifest = inventory.manifest()
        current = tuple(manifest[key] for key in ('operation_sha256', 'cut_request_sha256', 'source_commit',
            'registry_sha256', 'account_ref_sha256', 'source_attachment_sha256'))
        require(manifest['source_attachment_sha256'] == commitment and
            (binding is None or current == binding), 'GOLD_CHECKPOINT_PLAN_BINDING')
        binding = current
        require(receipt['inventory_sha256'] == _digest(manifest), 'GOLD_CHECKPOINT_PLAN_CHANGED')
        component_bounds[inventory.namespace] = deepcopy(manifest['component_bounds'])
        for limit in inventory.bounds.values():
            max_bytes += limit['max_bytes']
            max_members += limit['max_files'] + (limit['max_bytes'] + MAX_EXTENT_BYTES - 1) // MAX_EXTENT_BYTES
        members = []; size = 0; metadata = 0
        def finish():
            if not members:
                return
            part = dict(ordinal=len(parts) + 1, namespace=inventory.namespace, members=list(members),
                payload_bytes=size, metadata_bytes=0)
            while True:
                actual = len(canonical(part))
                if actual == part['metadata_bytes']:
                    break
                part['metadata_bytes'] = actual
            require(actual <= PART_METADATA_BYTES, 'GOLD_CHECKPOINT_PART_METADATA_BOUND')
            parts.append(part)
        for row in inventory.iter_entries():
            if row['kind'] != 'FILE':
                continue
            for extent in row['extents']:
                total_members += 1
                member = dict(member=f'payload/{total_members:010d}', relative=row['relative'],
                    extent_ordinal=extent['ordinal'], offset=extent['offset'], bytes=extent['bytes'], sha256=extent['sha256'])
                encoded = len(canonical(member)) + 1
                require(extent['bytes'] <= PART_MAX_BYTES, 'GOLD_CHECKPOINT_PART_EXTENT_BOUND')
                if members and (len(members) == PART_MAX_MEMBERS or size + extent['bytes'] > PART_MAX_BYTES
                        or metadata + encoded + 1024 > PART_METADATA_BYTES):
                    finish(); members = []; size = 0; metadata = 0
                members.append(member); size += extent['bytes']; metadata += encoded
        finish()
        inventory.recheck()
    require(total_members <= max_members, 'GOLD_CHECKPOINT_PLAN_BOUND')
    # At most one bounded extent per part; this deliberately conservative bound
    # follows original file/byte contracts, never a caller-supplied total.
    return dict(schema='GOLD_CHECKPOINT_PART_PLAN_V1', parts=parts, component_bounds=component_bounds,
        max_parts=max_members, max_members=max_members, max_bytes=max_bytes)


def inventory_limits(inventory, *, public_bundle, source_attachment, verified_source_trees=None):
    """Pure pre-extraction hard bounds from original component contracts.

    This rejects invented quota declarations before transport extracts members.
    It does not establish source custody, completeness or cross-role authority.
    """
    if type(inventory) is dict and inventory.get('schema') == 'GOLD_RESEARCH_INVENTORY_V1':
        from tools.demo_gold_research_inventory import inventory_limits as research_limits
        return research_limits(inventory, public_bundle=public_bundle, source_attachment=source_attachment,
            verified_source_trees=verified_source_trees)
    _fields(inventory, 'schema operation_sha256 cut_request_sha256 namespace source_commit registry_sha256 '
        'account_ref_sha256 pilot_identity source_root index semantic_view component_bounds files directories '
        'payload_bytes source_attachment_sha256 source_dependencies submission_enabled economic_authority')
    _fields(public_bundle, 'schema source_commit registry account_ref_sha256 base_runtime current_runtime records')
    if verified_source_trees is not None:
        require(isinstance(verified_source_trees, _VerifiedSourceTrees), 'GOLD_CHECKPOINT_SOURCE_TREE_VERIFICATION')
        verified_source_trees.recheck(source_attachment)
    require(inventory['schema'] == SCHEMA and inventory['namespace'] in ROLE_ROOTS
        and inventory['submission_enabled'] is inventory['economic_authority'] is False,
        'GOLD_CHECKPOINT_INVENTORY')
    require(public_bundle['schema'] == 'GOLD_CHECKPOINT_PUBLIC_CONTEXT_V1'
        and public_bundle['source_commit'] == inventory['source_commit']
        and _digest(validate_registry(public_bundle['registry'])) == inventory['registry_sha256']
        and public_bundle['account_ref_sha256'] == inventory['account_ref_sha256'], 'GOLD_CHECKPOINT_PUBLIC_CONTEXT')
    _original_custody(inventory, public_bundle)
    _source_scope(source_attachment, inventory['source_commit'], public_bundle)
    require(_digest(source_attachment_identity(source_attachment)) == inventory['source_attachment_sha256'],
        'GOLD_CHECKPOINT_SOURCE_CHANGED')
    view = inventory['semantic_view']
    _fields(view, 'schema enrollment execution issuer bar_inputs retention rotation feedback closure reobservation '
        'identity_commitments verified_partial missing_dependencies mutation_permitted')
    require(view['schema'] in VIEW_SCHEMAS and view['mutation_permitted'] is False, 'GOLD_CHECKPOINT_SEMANTIC_VIEW')
    expected = {'ordinary': _bounds(checkpoint.MAX_FILES, 10000, checkpoint.MAX_BYTES)}
    dependencies = _source_dependencies(public_bundle, inventory['namespace'], source_attachment)
    require(canonical(inventory['source_dependencies']) == canonical(dependencies),
        'GOLD_CHECKPOINT_SOURCE_DEPENDENCIES')
    if dependencies:
        extra = SOURCE_MAX_EXPANDED_BYTES if verified_source_trees is None else _extra_source_bytes(
            verified_source_trees, public_bundle)
        expected['bar_inputs'] = _bar_bounds(extra)
        require(type(view['bar_inputs']) is dict, 'GOLD_CHECKPOINT_BAR_INPUTS_REQUIRED')
    else:
        require(view['bar_inputs'] is None, 'GOLD_CHECKPOINT_BAR_UNBOUND_INPUTS')
    original = None
    if view['enrollment'] is not None:
        from live.execution import demo_gold_pilot_enrollment as enrollment, demo_gold_pilot as pilot
        enrolled = view['enrollment']; request = enrolled['request']
        enrollment._request_check(request, source_commit=inventory['source_commit'], account=inventory['account_ref_sha256'])
        require(enrolled['request_sha256'] == _digest(request), 'GOLD_CHECKPOINT_ENROLLMENT_CHANGED')
        if enrolled['status'] == 'ENROLLED':
            config = enrolled['configuration']; pilot.config_check(config)
            require(enrolled['configuration_sha256'] == _digest(config)
                and config['account_ref_sha256'] == request['account_ref_sha256'], 'GOLD_CHECKPOINT_ENROLLMENT_CHANGED')
            original = dict(request=request, configuration=config)
        else:
            require(enrolled['status'] == 'REQUESTED' and enrolled['configuration_sha256'] is None,
                'GOLD_CHECKPOINT_ENROLLMENT_CHANGED')
        if inventory['namespace'] == 'executor':
            leaves = _enrollment_limits()
            expected['enrollment'] = _bounds(1 + len(leaves) + 32, 2, 32768 + sum(leaves.values()) + 32 * 32768)
        else:
            require(canonical(_public_pilot(public_bundle)[0]) == canonical(enrolled),
                'GOLD_CHECKPOINT_PUBLIC_PILOT_CHANGED')
    if view['retention'] is not None:
        from tools.gold_pilot_retention_store import _binding
        require(original is not None and public_bundle['base_runtime'] is not None,
            'GOLD_CHECKPOINT_RETENTION_ORIGINAL_REQUIRED')
        capacity = _binding(original, public_bundle['base_runtime'])
        require(canonical(capacity) == canonical(view['retention']['binding']), 'GOLD_CHECKPOINT_RETENTION_BINDING')
        expected['retention'] = _bounds(capacity['max_audit_events'] + 5, 3,
            capacity['max_database_bytes'] + capacity['max_audit_bytes'] + capacity['max_metadata_allocated_bytes'])
    if view['rotation'] is not None:
        from tools import demo_gold_checkpoint_components as components
        expected.update(components.bounds(public=public_bundle, original=original, namespace=inventory['namespace']))
    declared = inventory['component_bounds']
    require(type(declared) is dict, 'GOLD_CHECKPOINT_COMPONENT_BOUND')
    if 'legacy_feedback' in declared:
        from tools.gold_feedback_legacy import MAX_RECORDS, MAX_TOTAL_BYTES
        require(inventory['namespace'] in ('executor', 'controller'), 'GOLD_CHECKPOINT_COMPONENT_BOUND')
        count = 1 if inventory['namespace'] == 'executor' else 2
        expected['legacy_feedback'] = _bounds(count * MAX_RECORDS, count, count * MAX_TOTAL_BYTES)
    pending = ['bar_inputs'] if dependencies and verified_source_trees is None else []
    if pending:
        proposed = declared.get('bar_inputs')
        _fields(proposed, 'max_files max_directories max_bytes')
        base = _bar_bounds()
        require(proposed['max_files'] == base['max_files'] and proposed['max_directories'] == base['max_directories']
            and type(proposed['max_bytes']) is int and base['max_bytes'] <= proposed['max_bytes']
            <= expected['bar_inputs']['max_bytes'], 'GOLD_CHECKPOINT_COMPONENT_BOUND')
        comparison = dict(expected, bar_inputs=proposed)
    else:
        comparison = expected
    require(canonical(declared) == canonical(comparison), 'GOLD_CHECKPOINT_COMPONENT_BOUND')
    maximum_files = sum(row['max_files'] for row in expected.values())
    maximum_directories = sum(row['max_directories'] for row in expected.values())
    maximum_bytes = sum(row['max_bytes'] for row in expected.values())
    require(all(type(inventory[key]) is int and 0 <= inventory[key] <= limit for key, limit in
        (('files', maximum_files), ('directories', maximum_directories), ('payload_bytes', maximum_bytes))),
        'GOLD_CHECKPOINT_INVENTORY_BUDGET')
    # Original paths are bounded by the Linux path-byte limit. Escaping expands
    # one input byte by at most six bytes; fixed identity/hash fields fit 2 KiB.
    # Extents have a separately fixed schema and at most 256 encoded bytes.
    extents = maximum_files + (maximum_bytes + MAX_EXTENT_BYTES - 1) // MAX_EXTENT_BYTES
    index_bytes = (maximum_files + maximum_directories) * (6 * 4096 + 2048) + extents * 256
    return dict(component_bounds=expected, max_files=maximum_files, max_directories=maximum_directories,
        max_bytes=maximum_bytes, max_extent_members=extents, max_index_bytes=index_bytes,
        pending_source_tree_components=pending)
