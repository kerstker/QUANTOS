"""Controlled fixed-role capture launch, bounded exit and owned-unit drain.

Starting this launcher is an operator action. It cannot install releases, change
resident services, claim writer exclusion or enable trading. Only an original
sealed request chooses the role, source, read-only roots and private spool.
"""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import os
from pathlib import Path
import re
import select
import stat
import subprocess
import time

from tools import demo_gold_role_handoff as handoff
from tools import demo_gold_role_capture_peer as peer
from tools import demo_gold_research_launcher as shared
from tools.demo_gold_research_capture_peer import process_worker
from tools.demo_supervisor import verify_source
from tools.demo_maintenance_processes import _metadata

require = handoff.require
CGROUP = Path('/sys/fs/cgroup')
START_TIMEOUT = 30
EXIT_TIMEOUT = 30
_TOKEN = object()
PROPERTIES = 'LoadState,ActiveState,SubState,MainPID,ExecMainPID,ExecMainCode,ExecMainStatus,ControlGroup,Description'


def _unit(value):
    require(type(value) is str and re.fullmatch(
        r'quantos-gold-role-[a-f0-9]{32}-(executor|issuer|controller)\.service', value), 'WORKER_UNIT')
    return value


def _request(value):
    require(type(value) is handoff.RoleRequest, 'CAPTURED_ROLE_REQUEST_REQUIRED')
    value.recheck()
    return value.document()


def unit_for(request):
    doc = _request(request)
    return _unit('quantos-gold-role-' + request.request_sha256[:32] + '-' + doc['namespace'] + '.service')


def description(digest):
    handoff.native._hash(digest)
    return 'QUANTOS fixed-role capture ' + digest


_property_path = shared._property_path
_worker_sandbox = shared._worker_sandbox
_eof = shared._eof
_reap = shared._reap


def command(request):
    doc = _request(request)
    commit, uid = doc['source_commit'], doc['uid']
    source = '/opt/quantos/releases/' + commit
    venv = '/opt/quantos/venvs/' + commit
    roots = {doc['role_root'], source}
    for row in handoff.native.source_attachments(doc['source_attachment']):
        roots.update((row['source_root'], row['bundle_path']))
    captured = handoff.roster.require_captured(request._layout, verified_public=request._public)
    from tools.demo_gold_file_leases import _roots
    other_roots = [path for _, _, path in _roots(captured) if str(path) != doc['role_root']]
    for row in handoff.native.source_attachments(doc['source_attachment']):
        for name in ('source_root', 'bundle_path'):
            path = Path(row[name])
            require(all(not (path.is_relative_to(other) or other.is_relative_to(path))
                for other in other_roots), 'SOURCE_ORIGINAL_SCOPE')
    inaccessible = [*other_roots, Path('/run/credentials'), Path('/run/secrets'),
        Path('/etc/ssh'), Path('/etc/ssl/private')]
    properties = dict(User='0', Group='0', SupplementaryGroups='', UMask='0077',
        Description=description(request.request_sha256), Slice='system.slice',
        NoNewPrivileges='yes', PrivateNetwork='yes', RestrictAddressFamilies='AF_UNIX',
        ProtectSystem='strict', ProtectHome='tmpfs', PrivateTmp='yes', PrivateDevices='yes',
        ProtectKernelTunables='yes', ProtectKernelModules='yes', ProtectKernelLogs='yes',
        ProtectControlGroups='yes', ProtectClock='yes', ProtectHostname='yes',
        RestrictSUIDSGID='yes', LockPersonality='yes', RestrictRealtime='yes', RestrictNamespaces='yes',
        CapabilityBoundingSet='CAP_SETUID CAP_SETGID CAP_SETPCAP', AmbientCapabilities='',
        SystemCallArchitectures='native', WorkingDirectory=doc['workspace'], KillMode='control-group',
        RuntimeMaxSec='1800s', TimeoutStopSec='10s', MemoryMax='4G', TasksMax='32',
        TemporaryFileSystem='/etc/quantos:ro,mode=0755',
        BindReadOnlyPaths=' '.join(_property_path(Path(root)) for root in sorted(roots)),
        ReadWritePaths=_property_path(Path(doc['workspace'])),
        InaccessiblePaths=' '.join('-' + _property_path(path) for path in inaccessible),
        StandardOutput='null', StandardError='null', Restart='no', SuccessExitStatus='')
    environment = ['PATH=' + venv + '/bin:/usr/bin:/bin', 'LANG=C.UTF-8', 'PYTHONPATH=' + source,
        'PYTHONNOUSERSITE=1', 'PYTHONDONTWRITEBYTECODE=1', 'OPENBLAS_NUM_THREADS=1', 'OMP_NUM_THREADS=1',
        'LIVE_TRADING_ENABLED=false', 'REAL_BROKER_ENABLED=false', 'ALLOW_ORDER_SUBMISSION=false',
        'ALLOW_AI_DIRECT_TRADING=false', 'REQUIRE_HUMAN_APPROVAL=true']
    # SupplementaryGroups= does not remove the user's default memberships. The
    # fixed env/setpriv startup binaries drop every capability and group before
    # exec of the accepted Python runtime, in the same PID.
    return ['/usr/bin/systemd-run', '--quiet', '--wait', '--pipe', '--collect', '--no-ask-password',
        '--service-type=exec', '--unit=' + unit_for(request),
        *['--property=' + key + '=' + value for key, value in properties.items()], '--',
        '/usr/bin/env', '-i', *environment, '/usr/bin/setpriv', '--no-new-privs',
        '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all', '--clear-groups',
        '--reuid=' + str(uid), '--regid=' + str(uid), '--', venv + '/bin/python', '-P', '-m',
        'tools.demo_gold_role_capture_worker', '--control-fd', '0']


def show(unit):
    _unit(unit)
    result = subprocess.run(['/usr/bin/systemctl', 'show', unit, '--no-pager', '--property=' + PROPERTIES],
        check=True, capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL,
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
    require(len(result.stdout.encode()) <= 65536, 'UNIT_METADATA_BOUND')
    rows = [row.split('=', 1) for row in result.stdout.splitlines()]
    require(all(len(row) == 2 for row in rows) and len(rows) == len({row[0] for row in rows})
        and {row[0] for row in rows} == set(PROPERTIES.split(',')), 'UNIT_METADATA')
    return dict(rows)


def _owned(unit, digest, value):
    require(value.get('LoadState') == 'loaded'
        and value.get('Description') == description(digest), 'WORKER_OWNERSHIP')
    require(value.get('ControlGroup') in ('', '/system.slice/' + unit), 'WORKER_CGROUP')


def _cgroup_owner(info):
    require(info.st_uid == 0 and not info.st_mode & 0o022, 'WORKER_CGROUP_CUSTODY')


def drained(request):
    unit = unit_for(request)
    return _drained(unit, request.request_sha256)


def _drained(unit, digest):
    _unit(unit)
    before = show(unit)
    require(before.get('LoadState') in ('loaded', 'not-found'), 'WORKER_OWNERSHIP')
    if before['LoadState'] == 'loaded':
        _owned(unit, digest, before)
    if before.get('ActiveState') not in ('inactive', 'failed') or before.get('MainPID') != '0':
        return False
    expected = '/system.slice/' + unit
    require(before.get('ControlGroup') in ('', expected), 'WORKER_CGROUP')
    path = CGROUP / 'system.slice' / unit / 'cgroup.events'
    require(not any(parent.is_symlink() for parent in (path, *path.parents)), 'WORKER_CGROUP')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            _cgroup_owner(info)
            require(stat.S_ISREG(info.st_mode), 'WORKER_CGROUP')
            raw = stream.read(4097)
        rows = [row.split() for row in raw.decode('ascii').splitlines()]
        require(len(raw) <= 4096 and all(len(row) == 2 for row in rows)
            and len(rows) == len({row[0] for row in rows}) and dict(rows).get('populated') in ('0', '1'),
            'WORKER_CGROUP')
        if dict(rows)['populated'] != '0':
            return False
    except FileNotFoundError:
        require(not path.parent.exists(), 'WORKER_CGROUP')
    after = show(unit)
    require(after == before, 'WORKER_OWNERSHIP_CHANGED')
    return True


def stop_owned(request):
    require(os.getresuid() == os.getresgid() == (0, 0, 0), 'ROOT_REQUIRED')
    # Cancellation remains available after expiry or changed original inputs.
    # Immutable original issuance and the root controller still select the unit;
    # this grants no new capture permission and cannot select another service.
    require(type(request) is handoff.RoleRequest, 'CAPTURED_ROLE_REQUEST_REQUIRED')
    doc = handoff._document(request.document())
    require(handoff.canonical(doc) == request._raw, 'REQUEST_CHANGED')
    path = Path('/proc') / str(doc['controller_pid'])
    require(handoff.process_identity(path) == (os.getpid(), doc['controller_start_ticks'])
        and handoff._root_uids(path) == (0, 0, 0, 0), 'CONTROLLER')
    unit = _unit('quantos-gold-role-' + request.request_sha256[:32] + '-' + doc['namespace'] + '.service')
    value = show(unit)
    if value.get('LoadState') == 'not-found':
        require(_drained(unit, request.request_sha256), 'WORKER_NOT_DRAINED')
        return
    _owned(unit, request.request_sha256, value)
    subprocess.run(['/usr/bin/systemctl', 'stop', unit], check=True, timeout=20,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})


def _workspace(request):
    doc = _request(request)
    root = handoff.OUTPUT_ROOT
    handoff.roster._parents(root, (0, 0))
    handoff.kernel.role._node(root, 0, directory=True)
    operation = root / doc['operation_sha256']
    if not operation.exists():
        operation.mkdir(mode=0o755)
        operation.chmod(0o755)
    handoff.kernel.role._node(operation, 0, directory=True)
    path = handoff._workspace(doc['cut_request'], doc['namespace'])
    require(str(path) == doc['workspace'] and not path.exists() and not path.is_symlink(), 'NEW_WORKSPACE')
    path.mkdir(mode=0o700)
    os.chown(path, doc['uid'], doc['uid'])
    require(stat.S_IMODE(handoff.kernel.role._node(path, doc['uid'], directory=True)['mode']) == 0o700, 'WORKSPACE_CUSTODY')


def _preflight(request):
    doc = _request(request)
    verify_source(doc['source_commit'])
    unit = unit_for(request)
    require(show(unit).get('LoadState') == 'not-found' and drained(request), 'WORKER_UNIT_IN_USE')
    return doc


def _worker(request, process):
    deadline = min(time.monotonic() + START_TIMEOUT, request.document()['expires_monotonic_ns'] / 10**9)
    admission_error = None
    while time.monotonic() < deadline:
        _request(request)
        if process.poll() is not None and admission_error is not None:
            raise admission_error
        require(process.poll() is None, 'WORKER_START_FAILED')
        value = show(unit_for(request))
        if value.get('LoadState') == 'loaded':
            _owned(unit_for(request), request.request_sha256, value)
            raw = value.get('MainPID', '')
            require(type(raw) is str and raw.isdigit(), 'WORKER_PID')
            if int(raw) > 0 and value.get('ActiveState') == 'active' and value.get('SubState') == 'running':
                pid = int(raw)
                path = Path('/proc') / str(pid)
                ticks = handoff.process_identity(path)[1]
                # Wait through the fixed privilege-drop binary. No descriptor
                # is sent while this PID has startup privileges or extra groups.
                if handoff._root_uids(path) == (request.document()['uid'],) * 4:
                    try:
                        identity = process_worker(pid, ticks, request.document()['uid'])
                        _worker_sandbox(path)
                    except ValueError as error:
                        if str(error) not in ('GOLD_RESEARCH_HANDOFF_WORKER_IDENTITY',
                                'GOLD_RESEARCH_HANDOFF_WORKER_PRIVILEGES'):
                            raise
                        # UID0 already matches the controller before setpriv
                        # drops groups/caps. Admission stays closed while the
                        # same bounded startup completes; no FD is sent here.
                        admission_error = error
                        time.sleep(0.05)
                        continue
                    rows = _metadata(path / 'cgroup').splitlines()
                    require(rows == ['0::/system.slice/' + unit_for(request)], 'WORKER_CGROUP')
                    require(show(unit_for(request)) == value, 'WORKER_OWNERSHIP_CHANGED')
                    return identity
        time.sleep(0.05)
    if admission_error is not None:
        raise admission_error
    raise ValueError('GOLD_ROLE_HANDOFF_WORKER_START_TIMEOUT')




class ControlledRoleWorker:
    def __init__(self, token, *, request, process, consumer, pidfd):
        require(token is _TOKEN, 'CONTROLLED_WORKER_REQUIRED')
        self.request, self.process, self.peer, self.pidfd = request, process, consumer, pidfd
        self._receipt = None

    def finish(self):
        _request(self.request)
        require(self._receipt is None and self.peer._status is not None
            and self.peer._status['phase'] == 'CLOSED' and self.peer._verified is not None,
            'VERIFIED_CLOSE_REQUIRED')
        remaining = self.request.document()['expires_monotonic_ns'] / 10**9 - time.monotonic()
        require(remaining > 0, 'EXPIRED')
        require(self.process.wait(timeout=min(EXIT_TIMEOUT, remaining)) == 0, 'WORKER_EXIT_STATUS')
        poller = select.poll()
        poller.register(self.pidfd, select.POLLIN | select.POLLHUP)
        require(bool(poller.poll(0)), 'WORKER_STILL_ALIVE')
        require(drained(self.request), 'WORKER_NOT_DRAINED')
        # A zero-length datagram cannot certify EOF while a sender is alive:
        # process death and complete owned cgroup drain were checked first.
        remaining = self.request.document()['expires_monotonic_ns'] / 10**9 - time.monotonic()
        require(remaining > 0, 'EXPIRED')
        _eof(self.peer._channel, min(EXIT_TIMEOUT, remaining))
        _request(self.request)
        identity = deepcopy(self.peer._worker)
        self._receipt = dict(schema='GOLD_FIXED_ROLE_WORKER_EXIT_V1',
            request_sha256=self.request.request_sha256, namespace=self.peer.namespace, uid=self.peer.uid,
            worker_identity=identity, unit=unit_for(self.request),
            archive_manifest_sha256=self.peer._status['archive_manifest_sha256'],
            inventory_sha256=self.peer._status['inventory_sha256'], exit_code=0,
            worker_exit_verified=True, owned_cgroup_drained=True, writer_barrier_verified=False,
            whole_set_verified=False, submission_enabled=False, economic_authority=False)
        return deepcopy(self._receipt)

    def recheck_exit(self):
        _request(self.request)
        require(self._receipt is not None and self.process.poll() == 0 and drained(self.request), 'WORKER_NOT_DRAINED')
        return deepcopy(self._receipt)


@contextmanager
def launch(request):
    _preflight(request)
    argv = command(request)
    _workspace(request)
    parent, inherited = handoff.kernel.channel_pair()
    process = None
    controlled = None
    pidfd = None
    wrapper_pidfd = None
    success = False
    try:
        process = subprocess.Popen(argv, stdin=inherited, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, close_fds=True)
        wrapper_pidfd = os.pidfd_open(process.pid, 0)
        inherited.close()
        identity = _worker(request, process)
        pidfd = os.pidfd_open(identity['pid'], 0)
        require(process_worker(identity['pid'], identity['start_ticks'], identity['uid']) == identity,
            'WORKER_IDENTITY')
        consumer = peer.connect(request, parent, worker_pid=identity['pid'], worker_start_ticks=identity['start_ticks'])
        controlled = ControlledRoleWorker(_TOKEN, request=request, process=process, consumer=consumer, pidfd=pidfd)
        yield controlled
        controlled.recheck_exit()
        success = True
    finally:
        inherited.close()
        parent.close()
        try:
            if not success and process is not None:
                stop_owned(request)
        finally:
            try:
                if process is not None:
                    _reap(process, wrapper_pidfd)
            finally:
                for descriptor in (pidfd, wrapper_pidfd):
                    if descriptor is not None:
                        os.close(descriptor)


def capture_all(*, verified_layout, verified_public, source_attachments, file_leases=None):
    """Capture the original three fixed roles; four-role coordination is separate."""
    captured = handoff.roster.require_captured(verified_layout, verified_public=verified_public)
    names = ('executor', 'issuer', 'controller')
    require(type(source_attachments) is dict and set(source_attachments) == set(names),
        'COMPLETE_FIXED_ROLE_SCOPE')
    if file_leases is not None:
        from tools.demo_gold_file_leases import OriginalFileReadLeases
        require(type(file_leases) is OriginalFileReadLeases, 'LIVE_FILE_LEASES_REQUIRED')
        captured = file_leases.guarded_layout(verified_layout=captured, verified_public=verified_public)
    snapshot = handoff.native._digest(captured.snapshot())
    with ExitStack() as stack:
        requests = {name: stack.enter_context(handoff.issue(verified_layout=captured,
            verified_public=verified_public, namespace=name, source_attachment=source_attachments[name]))
            for name in names}
        workers = {}
        for name in names:
            controlled = stack.enter_context(launch(requests[name]))
            controlled.peer.prepare()
            workers[name] = controlled
        for controlled in workers.values():
            captured.recheck()
            controlled.peer.verify_archive()
        for controlled in workers.values():
            captured.recheck()
            controlled.peer.recheck()
        statuses = {}
        for name, controlled in workers.items():
            captured.recheck()
            statuses[name] = controlled.peer.close_capture()
        exits = {name: controlled.finish() for name, controlled in workers.items()}
        for controlled in workers.values():
            controlled.recheck_exit()
        captured.recheck()
        require(handoff.native._digest(captured.snapshot()) == snapshot, 'ROSTER_CHANGED')
        result = dict(schema='GOLD_FIXED_ROLE_CAPTURE_SET_V1', phase='CLOSED',
            operation_sha256=captured._cut['operation_sha256'], source_commit=captured._public['source_commit'],
            fixed_layout_sha256=snapshot, cut_request_sha256=handoff.native._digest(captured._cut),
            roles=list(names), captures=statuses, worker_exits=exits,
            native_fixed_role_archives_verified=True, worker_exit_verified=True,
            cross_role_business_join_verified=False, writer_barrier_verified=False,
            whole_set_verified=False, submission_enabled=False, economic_authority=False)
        if file_leases is not None:
            report = file_leases.report()
            result.update(original_file_leases_verified=True, original_file_lease_scope_sha256=report['scope_sha256'])
        return result
