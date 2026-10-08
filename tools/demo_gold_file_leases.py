"""Kernel read leases on every original file in the fixed four-role roster.

Only a single-threaded root coordinator may hold this live handle. No caller
paths, original writes, service operations, capture grants or trading permission.
Leases reject existing writers and detect subsequent break attempts; directory
membership and original identities remain mandatory. This is a file component
of writer exclusion, never a complete service/manual writer barrier by itself.
"""
from contextlib import contextmanager
import fcntl
import itertools
import os
from pathlib import Path
import resource
import signal
import stat
import threading

from services.research.research_evidence_reader import _open
from tools import demo_gold_research_layout as roster
from tools import demo_gold_namespace_checkpoint as native
from tools.demo_maintenance_processes import _identity as process_identity

SCHEMA = 'GOLD_ORIGINAL_FILE_READ_LEASE_SET_V1'
MAX_NODES = 400_000
FD_RESERVE = 64
_TOKEN = object()
_HELD = False


def require(ok, reason):
    if not ok:
        raise ValueError('GOLD_FILE_LEASE_' + reason)


def _controller():
    require(os.getresuid() == os.getresgid() == (0, 0, 0), 'ROOT_REQUIRED')
    require(threading.current_thread() is threading.main_thread()
        and len(list(Path('/proc/self/task').iterdir())) == 1, 'SINGLE_THREAD_REQUIRED')
    return process_identity(Path('/proc') / str(os.getpid()))


def _roots(captured):
    require(set(native.ROLE_ROOTS) == {'executor', 'issuer', 'controller'}, 'FIXED_ROLE_SCOPE')
    registry = captured._public['registry']
    roots = [(name, native._role_uid(registry, name), roster._absolute(str(path)))
        for name, path in sorted(native.ROLE_ROOTS.items())]
    roots.extend(('research', row['layout']['uid'], roster._absolute(row['root']))
        for row in captured._document['research_roots'])
    paths = [row[2] for row in roots]
    require(all(not (path.is_relative_to(other) or other.is_relative_to(path))
        for index, path in enumerate(paths) for other in paths[index + 1:]), 'ROOT_OVERLAP')
    return roots


def _identity(info):
    return {name: getattr(info, name) for name in roster._STAT}


def _secret(name):
    lowered = name.lower()
    require(lowered not in ('key', 'keys', '.ssh', '.env')
        and not lowered.endswith(('.pem', '.key'))
        and not any(word in lowered for word in ('credential', 'api_key', 'token')), 'SECRET_SCOPE')


def _custody(info, uid):
    require(info.st_uid == info.st_gid == uid and not info.st_mode & 0o022
        and (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) and info.st_nlink == 1), 'CUSTODY')


def _members(fd, maximum):
    with os.scandir(fd) as entries:
        names = [entry.name for entry in itertools.islice(entries, maximum + 1)]
    require(len(names) <= maximum, 'NODE_BUDGET')
    return sorted(names)


class OriginalFileReadLeases:
    __slots__ = ('_captured', '_controller', '_roots', '_nodes', '_inodes', '_budget', '_closed', '_digest')

    def __init__(self, token, captured):
        require(token is _TOKEN, 'LIVE_HANDLE_REQUIRED')
        self._captured = captured
        self._controller = _controller()
        self._roots = _roots(captured)
        self._nodes, self._inodes = {}, {}
        self._closed = False
        self._digest = None
        limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        available = MAX_NODES if limit == resource.RLIM_INFINITY else limit - len(list(Path('/proc/self/fd').iterdir())) - FD_RESERVE
        self._budget = min(MAX_NODES, available)
        require(self._budget > 0, 'FD_BUDGET')

    def _record(self, path, uid):
        require(len(self._nodes) < self._budget, 'NODE_BUDGET')
        _secret(path.name)
        fd = _open(path, os.O_RDONLY)
        try:
            info = os.fstat(fd)
            _custody(info, uid)
            identity = _identity(info)
            key = info.st_dev, info.st_ino
            require(key not in self._inodes, 'PATH_ALIAS')
            directory = stat.S_ISDIR(info.st_mode)
            members = _members(fd, self._budget - len(self._nodes)) if directory else None
            parents = roster._parents(path, (uid, uid))
            value = dict(fd=fd, identity=identity, uid=uid, directory=directory, members=members,
                parents=parents, leased=False)
            self._nodes[path] = value
            self._inodes[key] = path
            fd = None
            if not directory:
                fcntl.fcntl(value['fd'], fcntl.F_SETOWN, os.getpid())
                try:
                    fcntl.fcntl(value['fd'], fcntl.F_SETLEASE, fcntl.F_RDLCK)
                except OSError as error:
                    raise ValueError('GOLD_FILE_LEASE_LEASE_UNAVAILABLE') from error
                value['leased'] = True
            require(_identity(os.fstat(value['fd'])) == identity, 'ORIGINAL_CHANGED')
            if directory:
                for name in members:
                    self._record(path / name, uid)
        finally:
            if fd is not None:
                os.close(fd)

    def _acquire(self):
        try:
            for _, uid, path in self._roots:
                self._record(path, uid)
            self.recheck()
            self._digest = native._digest({str(path): {name: value[name]
                for name in ('identity', 'uid', 'directory', 'members', 'parents')}
                for path, value in sorted(self._nodes.items())})
            return self
        except BaseException:
            self.close()
            raise

    def recheck(self):
        require(not self._closed and _controller() == self._controller, 'CONTROLLER_OR_CLOSED')
        require(signal.SIGIO in signal.pthread_sigmask(signal.SIG_BLOCK, set()), 'SIGNAL_MASK_CHANGED')
        require(signal.SIGIO not in signal.sigpending(), 'LEASE_BROKEN')
        self._captured.recheck()
        for path, value in self._nodes.items():
            fd = value['fd']
            if value['leased']:
                require(fcntl.fcntl(fd, fcntl.F_GETLEASE) == fcntl.F_RDLCK, 'LEASE_BROKEN')
            require(_identity(os.fstat(fd)) == value['identity'], 'ORIGINAL_CHANGED')
            current = _open(path, os.O_RDONLY)
            try:
                require(_identity(os.fstat(current)) == value['identity'], 'ORIGINAL_CHANGED')
            finally:
                os.close(current)
            require(roster._parents(path, (value['uid'], value['uid'])) == value['parents'], 'ORIGINAL_CHANGED')
            if value['directory']:
                require(_members(fd, self._budget) == value['members'], 'ORIGINAL_CHANGED')
        require(signal.SIGIO not in signal.sigpending(), 'LEASE_BROKEN')
        return self

    def report(self):
        self.recheck()
        require(self._digest is not None, 'LIVE_HANDLE_REQUIRED')
        return dict(schema=SCHEMA, roles=sorted({row[0] for row in self._roots}),
            research_uids=sorted(row[1] for row in self._roots if row[0] == 'research'),
            operation_sha256=self._captured._cut['operation_sha256'],
            fixed_layout_sha256=native._digest(self._captured.snapshot()),
            source_commit=self._captured._public['source_commit'],
            scope_sha256=self._digest, files=sum(value['leased'] for value in self._nodes.values()),
            directories=sum(value['directory'] for value in self._nodes.values()),
            original_file_leases_verified=True, writer_barrier_verified=False, whole_set_verified=False,
            submission_enabled=False, economic_authority=False)

    def guarded_layout(self, *, verified_layout, verified_public):
        """Use the original roster reader with this additional live guard."""
        self.recheck()
        captured = roster.require_captured(verified_layout, verified_public=verified_public)
        require(captured is self._captured, 'CAPTURED_CONTEXT')
        guarded = roster.capture(verified_public=verified_public, public_bundle=captured._public,
            cut_request=captured._cut, guard=self.recheck)
        require(guarded.snapshot() == captured.snapshot(), 'ORIGINAL_CHANGED')
        return guarded

    def close(self):
        if self._closed:
            return
        self._closed = True
        failed = False
        for value in reversed(list(self._nodes.values())):
            try:
                if value['leased']:
                    fcntl.fcntl(value['fd'], fcntl.F_SETLEASE, fcntl.F_UNLCK)
            except OSError:
                failed = True
            finally:
                try:
                    os.close(value['fd'])
                except OSError:
                    failed = True
        require(not failed, 'CLEANUP_FAILED')


@contextmanager
def hold(*, verified_layout, verified_public):
    """Acquire the complete fixed role file set, retain and recheck it live."""
    global _HELD
    require(not _HELD, 'ALREADY_HELD')
    _controller()
    captured = roster.require_captured(verified_layout, verified_public=verified_public)
    old = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGIO})
    leases = None
    owns_notifications = False
    try:
        require(signal.SIGIO not in old and signal.SIGIO not in signal.sigpending(), 'SIGNAL_SCOPE')
        owns_notifications = True
        _HELD = True
        leases = OriginalFileReadLeases(_TOKEN, captured)._acquire()
        yield leases
        leases.recheck()
    finally:
        try:
            if leases is not None:
                leases.close()
        finally:
            # All our leases are gone. Drain their pending notifications before
            # restoring the original mask; default SIGIO would terminate us.
            if owns_notifications:
                while signal.sigtimedwait({signal.SIGIO}, 0) is not None:
                    pass
            _HELD = False
            signal.pthread_sigmask(signal.SIG_SETMASK, old)
