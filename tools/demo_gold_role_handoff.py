"""Root-selected fixed-role capture requests with sealed kernel handoff.

Executor, issuer and controller stay separate from research permissions. The
original roster, registry, fixed roots and live process select every identity;
no caller UID/path or claimed archive success authorizes capture. This module
does not establish a global writer barrier or whole-set recovery.
"""
from contextlib import contextmanager
from copy import deepcopy
import array
import fcntl
import os
from pathlib import Path
import re
import select
import socket
import stat
import time

from services.validation.demo_submission import validate_registry
from tools import demo_gold_research_handoff as kernel
from tools import demo_gold_research_layout as roster
from tools import demo_gold_namespace_checkpoint as native
from tools import demo_gold_file_leases as original_files
from tools.demo_executor_retention_grant import SEALS, boot_id, _controller as _controller_pidfd
from tools.demo_maintenance_processes import _identity as process_identity, _uids as _root_uids, _metadata
from tools.gold_pilot_retention_store import canonical

SCHEMA = 'GOLD_FIXED_ROLE_CAPTURE_REQUEST_V1'
OUTPUT_ROOT = kernel.OUTPUT_ROOT
MAX_REQUEST_BYTES = kernel.MAX_REQUEST_BYTES
MAX_LIFETIME_NS = kernel.MAX_LIFETIME_NS
READY = b'GOLD_FIXED_ROLE_READY_V1'
CAPTURE = b'GOLD_FIXED_ROLE_CAPTURE_V1'
FIELDS = frozenset(('schema action namespace uid source_commit operation_sha256 public_bundle cut_request '
    'role_root role_root_identity role_root_parents fixed_layout_sha256 source_attachment '
    'source_attachment_sha256 workspace controller_pid controller_start_ticks boot_id '
    'issued_monotonic_ns expires_monotonic_ns writer_barrier_verified submission_enabled economic_authority').split())
_TOKEN = object()


def require(ok, reason):
    if not ok:
        raise ValueError('GOLD_ROLE_HANDOFF_' + reason)


def _workspace(cut, namespace):
    require(namespace in ('executor', 'issuer', 'controller'), 'FIXED_ROLE_REQUIRED')
    return OUTPUT_ROOT / cut['operation_sha256'] / namespace


def _root(path, uid):
    parents = roster._parents(path, (uid, uid))
    info = path.lstat()
    require(info.st_uid == info.st_gid == uid and stat.S_ISDIR(info.st_mode)
        and stat.S_IMODE(info.st_mode) == 0o700, 'ROOT_CUSTODY')
    return native._identity(info, directory=True), parents


def _workspace_scope(captured, workspace):
    require(all(not (workspace.is_relative_to(root) or root.is_relative_to(workspace))
        for _, _, root in original_files._roots(captured)), 'WORKSPACE_ORIGINAL_OVERLAP')


def _document(value):
    require(type(value) is dict and set(value) == FIELDS, 'FIELDS')
    require(len(canonical(value)) <= MAX_REQUEST_BYTES and value['schema'] == SCHEMA
        and value['action'] == 'CAPTURE_FIXED_ROLE'
        and all(value[key] is False for key in ('writer_barrier_verified', 'submission_enabled', 'economic_authority')),
        'SCOPE')
    namespace = value['namespace']
    require(type(namespace) is str and namespace in ('executor', 'issuer', 'controller')
        and set(native.ROLE_ROOTS) == {'executor', 'issuer', 'controller'}, 'FIXED_ROLE_REQUIRED')
    public, cut = value['public_bundle'], value['cut_request']
    native._fields(public, 'schema source_commit registry account_ref_sha256 base_runtime current_runtime records')
    native._fields(cut, 'schema operation_sha256 source_commit registry_sha256 public_bundle_sha256 roles '
        'writer_barrier_sha256 opening_at_ms submission_enabled economic_authority')
    registry = validate_registry(public['registry'])
    require(public['schema'] == 'GOLD_CHECKPOINT_PUBLIC_CONTEXT_V1' and type(public['records']) is dict
        and cut['schema'] == 'GOLD_CHECKPOINT_CUT_REQUEST_V1'
        and value['source_commit'] == public['source_commit'] == cut['source_commit']
        and re.fullmatch('[0-9a-f]{40}', value['source_commit'])
        and value['operation_sha256'] == cut['operation_sha256']
        and cut['registry_sha256'] == native._digest(registry)
        and cut['public_bundle_sha256'] == native._digest(public)
        and type(cut['roles']) is list and len(cut['roles']) == 4
        and set(cut['roles']) == {'executor', 'issuer', 'controller', 'research'}
        and cut['submission_enabled'] is cut['economic_authority'] is False, 'BINDING')
    for digest in (cut['operation_sha256'], cut['writer_barrier_sha256'], public['account_ref_sha256'],
            value['fixed_layout_sha256']):
        native._hash(digest)
    kernel._integer(cut['opening_at_ms'], minimum=1)
    uid = kernel._integer(value['uid'], maximum=2**31-1)
    require(uid == native._role_uid(registry, namespace), 'ROLE_IDENTITY')
    root = roster._absolute(value['role_root'])
    require(root == roster._absolute(str(native.ROLE_ROOTS[namespace])), 'ROOT_CHANGED')
    identity = value['role_root_identity']
    native._fields(identity, ' '.join(native.IDENTITY_NAMES[:6]))
    for key in identity:
        kernel._integer(identity[key])
    require(identity['inode'] > 0 and identity['nlink'] > 0 and identity['uid'] == identity['gid'] == uid
        and stat.S_ISDIR(identity['mode']) and stat.S_IMODE(identity['mode']) == 0o700, 'ROOT_IDENTITY')
    parents = value['role_root_parents']
    require(type(parents) is list and len(parents) <= 512, 'PARENTS')
    for parent in parents:
        native._fields(parent, 'path st_dev st_ino st_uid st_gid st_mode')
        require(parent['path'] == '/' or roster._absolute(parent['path']) in root.parents, 'PARENTS')
        require(all(type(parent[key]) is int and parent[key] >= 0 for key in parent if key != 'path'), 'PARENTS')
    workspace = roster._absolute(value['workspace'])
    require(workspace == _workspace(cut, namespace) and all(not (
        workspace.is_relative_to(path) or path.is_relative_to(workspace))
        for path in map(Path, native.ROLE_ROOTS.values())), 'WORKSPACE')
    require(kernel._source_declaration(value['source_attachment'], value['source_commit'], public)
        == value['source_attachment_sha256'], 'SOURCE_BINDING')
    kernel._integer(value['controller_pid'], minimum=1)
    kernel._integer(value['controller_start_ticks'], minimum=1)
    issued = kernel._integer(value['issued_monotonic_ns'])
    expires = kernel._integer(value['expires_monotonic_ns'])
    require(0 < expires-issued <= MAX_LIFETIME_NS and value['boot_id'] == boot_id(), 'LIFETIME')
    return value


def _time(value):
    require(value['issued_monotonic_ns'] <= time.monotonic_ns() < value['expires_monotonic_ns'], 'EXPIRED')


def read_descriptor(fd):
    require(type(fd) is int and fd >= 0, 'DESCRIPTOR')
    info = os.fstat(fd)
    require(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0
        and info.st_nlink == 0 and stat.S_IMODE(info.st_mode) == 0o600
        and 0 < info.st_size <= MAX_REQUEST_BYTES, 'CUSTODY')
    kernel._seals(fd)
    raw = os.pread(fd, MAX_REQUEST_BYTES+1, 0)
    require(len(raw) == info.st_size and native._identity(info) == native._identity(os.fstat(fd)), 'CHANGED')
    value = _document(kernel.decode(raw))
    require(canonical(value) == raw, 'CANONICAL_REQUEST')
    return value, raw


class RoleRequest:
    def __init__(self, token, *, fd, document, verified_layout, verified_public):
        require(token is _TOKEN, 'CAPTURED_ROLE_REQUEST_REQUIRED')
        self.fd, self._document = fd, deepcopy(document)
        self._layout, self._public = verified_layout, verified_public
        self._raw, self._fd_identity = canonical(document), native._identity(os.fstat(fd))
        self._closed = False

    def document(self):
        return deepcopy(self._document)

    @property
    def request_sha256(self):
        return native._digest(self._document)

    def recheck(self):
        require(not self._closed, 'CLOSED')
        require(os.getresuid() == os.getresgid() == (0,) * 3, 'ROOT_REQUIRED')
        value = self._document
        pid = value['controller_pid']
        require(process_identity(Path('/proc') / str(pid)) == (os.getpid(), value['controller_start_ticks'])
            and _root_uids(Path('/proc') / str(pid)) == (0,) * 4, 'CONTROLLER')
        _time(value)
        captured = roster.require_captured(self._layout, verified_public=self._public)
        _workspace_scope(captured, Path(value['workspace']))
        require(native._digest(captured.snapshot()) == value['fixed_layout_sha256']
            and captured._public == value['public_bundle'] and captured._cut == value['cut_request'], 'ROSTER_CHANGED')
        _document(value)
        require(_root(Path(value['role_root']), value['uid'])
            == (value['role_root_identity'], value['role_root_parents']), 'ROOT_CHANGED')
        kernel._seals(self.fd)
        require(native._identity(os.fstat(self.fd)) == self._fd_identity
            and os.pread(self.fd, MAX_REQUEST_BYTES+1, 0) == self._raw, 'CHANGED')
        return self

    def close(self):
        if not self._closed:
            os.close(self.fd)
            self._closed = True


@contextmanager
def issue(*, verified_layout, verified_public, namespace, source_attachment):
    require(os.getresuid() == os.getresgid() == (0,) * 3, 'ROOT_REQUIRED')
    captured = roster.require_captured(verified_layout, verified_public=verified_public)
    require(type(namespace) is str and namespace in ('executor', 'issuer', 'controller'), 'FIXED_ROLE_REQUIRED')
    uid = native._role_uid(captured._public['registry'], namespace)
    _workspace_scope(captured, _workspace(captured._cut, namespace))
    root = roster._absolute(str(native.ROLE_ROOTS[namespace]))
    identity, parents = _root(root, uid)
    pid, ticks = process_identity(Path('/proc') / str(os.getpid()))
    require(_root_uids(Path('/proc') / str(pid)) == (0,) * 4, 'CONTROLLER')
    started = time.monotonic_ns()
    value = dict(schema=SCHEMA, action='CAPTURE_FIXED_ROLE', namespace=namespace, uid=uid,
        source_commit=captured._public['source_commit'], operation_sha256=captured._cut['operation_sha256'],
        public_bundle=deepcopy(captured._public), cut_request=deepcopy(captured._cut),
        role_root=str(root), role_root_identity=identity, role_root_parents=parents,
        fixed_layout_sha256=native._digest(captured.snapshot()), source_attachment=deepcopy(source_attachment),
        source_attachment_sha256=kernel._source_declaration(source_attachment, captured._public['source_commit'], captured._public),
        workspace=str(_workspace(captured._cut, namespace)), controller_pid=pid, controller_start_ticks=ticks,
        boot_id=boot_id(), issued_monotonic_ns=started, expires_monotonic_ns=started+MAX_LIFETIME_NS,
        writer_barrier_verified=False, submission_enabled=False, economic_authority=False)
    _document(value)
    fd = os.memfd_create('quantos-fixed-role-capture', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    request = None
    try:
        os.fchmod(fd, 0o600)
        raw = canonical(value)
        require(os.write(fd, raw) == len(raw), 'WRITE')
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, SEALS)
        request = RoleRequest(_TOKEN, fd=fd, document=value, verified_layout=captured, verified_public=verified_public)
        request.recheck()
        yield request
        request.recheck()
    finally:
        os.close(fd) if request is None else request.close()


def _worker_privileges(path):
    fields = {}
    names = {'CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb', 'NoNewPrivs'}
    for line in _metadata(path / 'status').splitlines():
        key, _, value = line.partition(':')
        if key in names:
            require(key not in fields, 'WORKER_PRIVILEGES')
            fields[key] = value.strip()
    require(set(fields) == names and fields['NoNewPrivs'] == '1'
        and all(re.fullmatch('0+', fields[key]) for key in names - {'NoNewPrivs'}), 'WORKER_PRIVILEGES')


def _role_identity(value):
    uid = value['uid']
    require(os.getresuid() == os.getresgid() == (uid,) * 3
        and set(os.getgroups()) <= {uid}, 'ROLE_IDENTITY')
    _worker_privileges(Path('/proc/self'))


class RoleLease:
    def __init__(self, token, *, fd, value, raw, controller_pidfd):
        require(token is _TOKEN, 'ADMITTED_ROLE_LEASE_REQUIRED')
        self.fd, self._document, self._raw = fd, deepcopy(value), raw
        self._fd_identity = native._identity(os.fstat(fd))
        self.controller_pidfd = controller_pidfd
        self.poller = select.poll()
        self.poller.register(controller_pidfd, select.POLLIN)

    def document(self):
        return deepcopy(self._document)

    @property
    def request_sha256(self):
        return native._digest(self._document)

    def check(self):
        _role_identity(self._document)
        _time(self._document)
        require(not self.poller.poll(0), 'CONTROLLER_EXITED')
        kernel._seals(self.fd)
        require(native._identity(os.fstat(self.fd)) == self._fd_identity
            and os.pread(self.fd, MAX_REQUEST_BYTES+1, 0) == self._raw, 'CHANGED')
        _document(self._document)
        value = self._document
        require(_root(Path(value['role_root']), value['uid'])
            == (value['role_root_identity'], value['role_root_parents']), 'ROOT_CHANGED')
        return self


@contextmanager
def admitted(fd):
    value, raw = read_descriptor(fd)
    _role_identity(value)
    _time(value)
    controller = _controller_pidfd(value)
    try:
        lease = RoleLease(_TOKEN, fd=fd, value=value, raw=raw, controller_pidfd=controller)
        lease.check()
        yield lease
        lease.check()
    finally:
        os.close(controller)


def send_request(channel, request, *, worker_pid, worker_start_ticks):
    require(type(request) is RoleRequest, 'CAPTURED_ROLE_REQUEST_REQUIRED')
    request.recheck()
    kernel._channel(channel)
    kernel._integer(worker_pid, minimum=1)
    kernel._integer(worker_start_ticks, minimum=1)
    uid = request.document()['uid']
    path = Path('/proc') / str(worker_pid)
    require(process_identity(path) == (worker_pid, worker_start_ticks)
        and _root_uids(path) == (uid,) * 4, 'WORKER_IDENTITY')
    _worker_privileges(path)
    credentials, _ = kernel._receive_packet(channel, READY, with_descriptor=False)
    require(credentials == (worker_pid, uid, uid), 'WORKER_CREDENTIALS')
    request.recheck()
    require(channel.sendmsg([CAPTURE], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
        array.array('i', [request.fd]))]) == len(CAPTURE), 'SEND')
    request.recheck()


def receive_request(channel):
    kernel._channel(channel)
    uid = os.getuid()
    kernel._integer(uid, maximum=2**31-1)
    _role_identity(dict(uid=uid))
    require(channel.send(READY) == len(READY), 'SEND')
    credentials, fd = kernel._receive_packet(channel, CAPTURE, with_descriptor=True)
    try:
        value, _ = read_descriptor(fd)
        require(credentials == (value['controller_pid'], 0, 0), 'CONTROLLER_CREDENTIALS')
        _role_identity(value)
        _time(value)
        return fd
    except BaseException:
        os.close(fd)
        raise
