"""Root-selected research capture requests, carried by sealed kernel descriptors.

Only a live captured fixed roster can select the original root/layout. This is
permission to attempt a read-only capture, not source verification, a writer
barrier, trading authority or archive acceptance. The worker still runs the
original bundle, native population and transport validators.
"""
from contextlib import contextmanager
from copy import deepcopy
import array
import fcntl
import math
import os
from pathlib import Path
import select
import socket
import stat
import struct
import time

from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_gold_research_layout as roster
from tools import demo_gold_research_inventory as role
from tools import demo_gold_namespace_checkpoint as native
from tools.demo_executor_retention_grant import SEALS, boot_id, _controller as _controller_pidfd
from tools.demo_maintenance_processes import _identity as process_identity, _uids as _root_uids
from tools.gold_pilot_retention_store import canonical

SCHEMA = 'GOLD_RESEARCH_CAPTURE_REQUEST_V1'
OUTPUT_ROOT = Path('/var/lib/quantos-gold-checkpoint-spool')
MAX_REQUEST_BYTES = 16 * 1024**2
MAX_LIFETIME_NS = 1800 * 10**9
CHANNEL_TIMEOUT = 30
READY = b'GOLD_RESEARCH_READY_V1'
CAPTURE = b'GOLD_RESEARCH_CAPTURE_V1'
FIELDS = frozenset(('schema action uid source_commit operation_sha256 public_bundle cut_request '
    'research_root research_root_identity research_layout fixed_layout_sha256 source_attachment '
    'source_attachment_sha256 workspace controller_pid controller_start_ticks boot_id '
    'issued_monotonic_ns expires_monotonic_ns writer_barrier_verified submission_enabled economic_authority').split())
_TOKEN = object()


def require(ok, reason):
    if not ok:
        raise ValueError('GOLD_RESEARCH_HANDOFF_' + reason)


def _integer(value, *, minimum=0, maximum=2**63-1):
    require(type(value) is int and minimum <= value <= maximum, 'INTEGER')
    return value


def _workspace(cut, uid):
    return OUTPUT_ROOT / cut['operation_sha256'] / str(uid)


def _source_declaration(value, source, public):
    # The worker verifies the actual bundle. Issuance binds the finite request,
    # without claiming that a declared attachment has already passed that check.
    native._source_scope(value, source, public)
    for row in native.source_attachments(value):
        for key in ('source_root', 'bundle_path'):
            roster._absolute(row[key])
    return native._digest(native.source_attachment_identity(value))


def _document(value):
    require(type(value) is dict and set(value) == FIELDS, 'FIELDS')
    require(len(canonical(value)) <= MAX_REQUEST_BYTES, 'REQUEST_BOUND')
    require(value['schema'] == SCHEMA and value['action'] == 'CAPTURE_RESEARCH'
        and all(value[key] is False for key in ('writer_barrier_verified', 'submission_enabled', 'economic_authority')),
        'SCOPE')
    uid = _integer(value['uid'], minimum=1, maximum=2**31-1)
    layout = role._scope(value['research_layout'], value['public_bundle'], value['cut_request'])
    cut = value['cut_request']
    require(layout['uid'] == uid and value['source_commit'] == layout['source_commit']
        and value['operation_sha256'] == cut['operation_sha256']
        and set(cut['roles']) == {'executor', 'issuer', 'controller', 'research'}, 'BINDING')
    native._hash(value['fixed_layout_sha256'])
    root = roster._absolute(value['research_root'])
    identity = value['research_root_identity']
    native._fields(identity, ' '.join(native.IDENTITY_NAMES[:6]))
    for key in identity:
        _integer(identity[key])
    require(identity['inode'] > 0 and identity['uid'] == identity['gid'] == uid
        and identity['nlink'] > 0 and stat.S_ISDIR(identity['mode'])
        and not identity['mode'] & 0o022, 'ROOT_IDENTITY')
    workspace = roster._absolute(value['workspace'])
    require(workspace == _workspace(cut, uid) and not (
        workspace == root or workspace.is_relative_to(root) or root.is_relative_to(workspace)), 'WORKSPACE')
    require(_source_declaration(value['source_attachment'], value['source_commit'], value['public_bundle'])
        == value['source_attachment_sha256'], 'SOURCE_BINDING')
    _integer(value['controller_pid'], minimum=1)
    _integer(value['controller_start_ticks'], minimum=1)
    issued = _integer(value['issued_monotonic_ns'])
    expires = _integer(value['expires_monotonic_ns'])
    require(0 < expires-issued <= MAX_LIFETIME_NS and value['boot_id'] == boot_id(), 'LIFETIME')
    return value


def _time(value):
    require(value['issued_monotonic_ns'] <= time.monotonic_ns() < value['expires_monotonic_ns'], 'EXPIRED')


def _seals(fd):
    try:
        seals = fcntl.fcntl(fd, fcntl.F_GET_SEALS)
    except OSError as exc:
        raise ValueError('GOLD_RESEARCH_HANDOFF_UNSEALED') from exc
    require(seals & SEALS == SEALS, 'UNSEALED')


def read_descriptor(fd):
    require(type(fd) is int and fd >= 0, 'DESCRIPTOR')
    before = os.fstat(fd)
    require(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0
        and before.st_nlink == 0 and stat.S_IMODE(before.st_mode) == 0o600
        and 0 < before.st_size <= MAX_REQUEST_BYTES, 'CUSTODY')
    _seals(fd)
    raw = os.pread(fd, MAX_REQUEST_BYTES+1, 0)
    require(len(raw) == before.st_size and native._identity(before) == native._identity(os.fstat(fd)), 'CHANGED')
    value = _document(decode(raw))
    require(canonical(value) == raw, 'CANONICAL_REQUEST')
    return value, raw


class ResearchRequest:
    __slots__ = ('fd', '_document', '_layout', '_public', '_raw', '_fd_identity', '_closed')

    def __init__(self, token, *, fd, document, verified_layout, verified_public):
        require(token is _TOKEN, 'CAPTURED_REQUEST_REQUIRED')
        self.fd = fd
        self._document = deepcopy(document)
        self._layout = verified_layout
        self._public = verified_public
        self._raw = canonical(document)
        self._fd_identity = native._identity(os.fstat(fd))
        self._closed = False

    def document(self):
        return deepcopy(self._document)

    @property
    def request_sha256(self):
        return native._digest(self._document)

    def recheck(self):
        require(not self._closed, 'CLOSED')
        require(os.getresuid() == os.getresgid() == (0, 0, 0), 'ROOT_REQUIRED')
        pid = self._document['controller_pid']
        require(process_identity(Path('/proc') / str(pid)) == (os.getpid(), self._document['controller_start_ticks'])
            and _root_uids(Path('/proc') / str(pid)) == (0, 0, 0, 0), 'CONTROLLER')
        _time(self._document)
        captured = roster.require_captured(self._layout, verified_public=self._public)
        require(native._digest(captured.snapshot()) == self._document['fixed_layout_sha256']
            and str(captured.root_for_uid(self._document['uid'])) == self._document['research_root']
            and captured.layout_for_uid(self._document['uid']) == self._document['research_layout'], 'ROSTER_CHANGED')
        _seals(self.fd)
        require(native._identity(os.fstat(self.fd)) == self._fd_identity
            and os.pread(self.fd, MAX_REQUEST_BYTES+1, 0) == self._raw, 'CHANGED')
        return self

    def close(self):
        if not self._closed:
            os.close(self.fd)
            self._closed = True


@contextmanager
def issue(*, verified_layout, verified_public, uid, source_attachment):
    require(os.getresuid() == os.getresgid() == (0, 0, 0), 'ROOT_REQUIRED')
    captured = roster.require_captured(verified_layout, verified_public=verified_public)
    uid = _integer(uid, minimum=1, maximum=2**31-1)
    root = captured.root_for_uid(uid)
    layout = captured.layout_for_uid(uid)
    snapshot = captured.snapshot()
    original = snapshot['observed']['nodes'][str(root)]['identity']
    root_identity = {name: original[key] for name, key in zip(native.IDENTITY_NAMES[:6], native.IDENTITY[:6])}
    pid, ticks = process_identity(Path('/proc') / str(os.getpid()))
    require(_root_uids(Path('/proc') / str(pid)) == (0, 0, 0, 0), 'CONTROLLER')
    started = time.monotonic_ns()
    value = dict(schema=SCHEMA, action='CAPTURE_RESEARCH', uid=uid, source_commit=layout['source_commit'],
        operation_sha256=captured._cut['operation_sha256'], public_bundle=deepcopy(captured._public),
        cut_request=deepcopy(captured._cut), research_root=str(root), research_root_identity=root_identity,
        research_layout=layout, fixed_layout_sha256=native._digest(snapshot),
        source_attachment=deepcopy(source_attachment),
        source_attachment_sha256=_source_declaration(source_attachment, layout['source_commit'], captured._public),
        workspace=str(_workspace(captured._cut, uid)), controller_pid=pid, controller_start_ticks=ticks,
        boot_id=boot_id(), issued_monotonic_ns=started, expires_monotonic_ns=started+MAX_LIFETIME_NS,
        writer_barrier_verified=False, submission_enabled=False, economic_authority=False)
    _document(value)
    raw = canonical(value)
    fd = os.memfd_create('quantos-research-capture', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    request = None
    try:
        os.fchmod(fd, 0o600)
        require(os.write(fd, raw) == len(raw), 'WRITE')
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, SEALS)
        request = ResearchRequest(_TOKEN, fd=fd, document=value,
            verified_layout=captured, verified_public=verified_public)
        request.recheck()
        yield request
        request.recheck()
    finally:
        if request is None:
            os.close(fd)
        else:
            request.close()


def _role_identity(value):
    uid = value['uid']
    require(os.getresuid() == (uid,) * 3 and os.getresgid() == (uid,) * 3
        and set(os.getgroups()) <= {uid}, 'ROLE_IDENTITY')


class ResearchLease:
    def __init__(self, *, fd, value, raw, controller_pidfd):
        self.fd = fd
        self._document = deepcopy(value)
        self._raw = raw
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
        _seals(self.fd)
        info = os.fstat(self.fd)
        require(info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_nlink == 0 and info.st_size == len(self._raw), 'CUSTODY')
        require(native._identity(info) == self._fd_identity
            and os.pread(self.fd, MAX_REQUEST_BYTES+1, 0) == self._raw, 'CHANGED')
        root = Path(self._document['research_root'])
        require(role._node(root, self._document['uid'], directory=True)
            == self._document['research_root_identity'], 'ROOT_CHANGED')
        return self


@contextmanager
def admitted(fd):
    value, raw = read_descriptor(fd)
    _role_identity(value)
    _time(value)
    controller = _controller_pidfd(value)
    try:
        lease = ResearchLease(fd=fd, value=value, raw=raw, controller_pidfd=controller)
        lease.check()
        yield lease
        lease.check()
    finally:
        os.close(controller)


def _channel(channel, *, timeout=CHANNEL_TIMEOUT):
    require(isinstance(channel, socket.socket) and channel.family == socket.AF_UNIX
        and channel.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_SEQPACKET, 'CHANNEL')
    require(channel.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) == 1, 'CHANNEL_CREDENTIALS')
    require(type(timeout) in (int, float) and math.isfinite(timeout)
        and 0 < timeout <= MAX_LIFETIME_NS / 10**9, 'CHANNEL_TIMEOUT')
    channel.settimeout(timeout)


def channel_pair():
    """Enable per-message credentials before a controlled child inherits its end."""
    channels = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
    try:
        for channel in channels:
            channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            _channel(channel)
        return channels
    except BaseException:
        for channel in channels:
            channel.close()
        raise


def _receive_message(channel, *, maximum, with_descriptor, expected=None, timeout=CHANNEL_TIMEOUT):
    _integer(maximum, minimum=1, maximum=65536)
    _channel(channel, timeout=timeout)
    descriptors = []
    credentials = []
    bad_control = False
    try:
        body, ancillary, flags, _ = channel.recvmsg(maximum,
            socket.CMSG_SPACE(8 * array.array('i').itemsize) + socket.CMSG_SPACE(struct.calcsize('3i')),
            socket.MSG_CMSG_CLOEXEC)
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                numbers = array.array('i')
                usable = len(data) - len(data) % numbers.itemsize
                numbers.frombytes(data[:usable])
                descriptors.extend(numbers)
                bad_control |= usable != len(data)
            elif level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS:
                if len(data) == struct.calcsize('3i'):
                    credentials.append(struct.unpack('3i', data))
                else:
                    bad_control = True
            else:
                bad_control = True
        require(not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) and bool(body)
            and (expected is None or body == expected), 'PACKET')
        require(not bad_control and len(credentials) == 1
            and len(descriptors) == (1 if with_descriptor else 0), 'PACKET_CONTROL')
        result = descriptors[0] if with_descriptor else None
        descriptors.clear()
        return body, credentials[0], result
    finally:
        for fd in descriptors:
            os.close(fd)


def _receive_packet(channel, expected, *, with_descriptor):
    _, credentials, descriptor = _receive_message(channel, maximum=len(expected),
        with_descriptor=with_descriptor, expected=expected)
    return credentials, descriptor


def receive_body(channel, *, maximum, timeout):
    """A bounded credentialled status datagram; descriptors are never accepted."""
    body, credentials, _ = _receive_message(channel, maximum=maximum,
        with_descriptor=False, timeout=timeout)
    return body, credentials


def send_request(channel, request, *, worker_pid, worker_start_ticks):
    """Root sends only after the launched original UID proves its live identity."""
    require(isinstance(request, ResearchRequest), 'CAPTURED_REQUEST_REQUIRED')
    request.recheck()
    _channel(channel)
    _integer(worker_pid, minimum=1)
    _integer(worker_start_ticks, minimum=1)
    uid = request.document()['uid']
    path = Path('/proc') / str(worker_pid)
    require(process_identity(path) == (worker_pid, worker_start_ticks)
        and _root_uids(path) == (uid,) * 4, 'WORKER_IDENTITY')
    credential, _ = _receive_packet(channel, READY, with_descriptor=False)
    require(credential == (worker_pid, uid, uid), 'WORKER_CREDENTIALS')
    request.recheck()
    require(channel.sendmsg([CAPTURE], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
        array.array('i', [request.fd]))]) == len(CAPTURE), 'SEND')
    request.recheck()


def receive_request(channel):
    """Receive one sealed descriptor from the actual live root controller."""
    _channel(channel)
    uid = os.getuid()
    _integer(uid, minimum=1, maximum=2**31-1)
    require(os.getresuid() == os.getresgid() == (uid,) * 3
        and set(os.getgroups()) <= {uid}, 'ROLE_IDENTITY')
    require(channel.send(READY) == len(READY), 'SEND')
    credential, fd = _receive_packet(channel, CAPTURE, with_descriptor=True)
    try:
        value, _ = read_descriptor(fd)
        require(credential == (value['controller_pid'], 0, 0), 'CONTROLLER_CREDENTIALS')
        _role_identity(value)
        _time(value)
        return fd
    except BaseException:
        os.close(fd)
        raise
