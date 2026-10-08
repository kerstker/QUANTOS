"""Controlled root-to-research capture launch, bounded exit and owned-unit drain.

Starting this launcher is an operator action. It cannot install releases, change
resident services, claim writer exclusion or enable trading. Only an original
sealed request chooses the role, source, read-only roots and private spool.
"""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import array
import os
from pathlib import Path
import re
import select
import signal
import socket
import stat
import subprocess
import time

from tools import demo_gold_research_handoff as handoff
from tools import demo_gold_research_capture_peer as peer
from tools.demo_supervisor import verify_source
from tools.demo_maintenance_processes import _metadata

require = handoff.require
CGROUP = Path('/sys/fs/cgroup')
START_TIMEOUT = 30
EXIT_TIMEOUT = 30
WRAPPER_WAIT_TIMEOUT = 20
_TOKEN = object()
PROPERTIES = 'LoadState,ActiveState,SubState,MainPID,ExecMainPID,ExecMainCode,ExecMainStatus,ControlGroup,Description'


def _unit(value):
    require(type(value) is str and re.fullmatch(r'quantos-gold-research-[a-f0-9]{32}-[1-9][0-9]{0,9}\.service', value),
        'WORKER_UNIT')
    uid = int(value.rsplit('-', 1)[1].removesuffix('.service'))
    handoff._integer(uid, minimum=1, maximum=2**31-1)
    return value


def _request(value):
    require(type(value) is handoff.ResearchRequest, 'CAPTURED_REQUEST_REQUIRED')
    value.recheck()
    return value.document()


def unit_for(request):
    doc = _request(request)
    return _unit('quantos-gold-research-' + request.request_sha256[:32] + '-' + str(doc['uid']) + '.service')


def description(digest):
    handoff.native._hash(digest)
    return 'QUANTOS research capture ' + digest


def _property_path(path):
    value = str(path)
    path = handoff.roster._absolute(value)
    require(all(ord(char) >= 32 and ord(char) != 127 for char in value) and ':' not in value, 'UNIT_PATH')
    # systemd has its own word parser and specifiers; this is never shell text.
    return '"' + str(path).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def command(request):
    doc = _request(request)
    commit, uid = doc['source_commit'], doc['uid']
    source = '/opt/quantos/releases/' + commit
    venv = '/opt/quantos/venvs/' + commit
    roots = {doc['research_root'], source}
    for row in handoff.native.source_attachments(doc['source_attachment']):
        roots.update((row['source_root'], row['bundle_path']))
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
        InaccessiblePaths='-/var/lib/quantos-demo-executor -/var/lib/quantos-demo-issuer '
            '-/var/lib/quantos-demo-controller -/run/credentials',
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
        'tools.demo_gold_research_capture_worker', '--control-fd', '0']


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
    # Cancellation remains available after expiry or changed research inputs.
    # Immutable original issuance and the root controller still select the unit;
    # this grants no new capture permission and cannot select another service.
    require(type(request) is handoff.ResearchRequest, 'CAPTURED_REQUEST_REQUIRED')
    doc = handoff._document(request.document())
    require(handoff.canonical(doc) == request._raw, 'REQUEST_CHANGED')
    path = Path('/proc') / str(doc['controller_pid'])
    require(handoff.process_identity(path) == (os.getpid(), doc['controller_start_ticks'])
        and handoff._root_uids(path) == (0, 0, 0, 0), 'CONTROLLER')
    unit = _unit('quantos-gold-research-' + request.request_sha256[:32] + '-' + str(doc['uid']) + '.service')
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
    handoff.role._node(root, 0, directory=True)
    operation = root / doc['operation_sha256']
    if not operation.exists():
        operation.mkdir(mode=0o755)
        operation.chmod(0o755)
    handoff.role._node(operation, 0, directory=True)
    path = handoff._workspace(doc['cut_request'], doc['uid'])
    require(str(path) == doc['workspace'] and not path.exists() and not path.is_symlink(), 'NEW_WORKSPACE')
    path.mkdir(mode=0o700)
    os.chown(path, doc['uid'], doc['uid'])
    require(stat.S_IMODE(handoff.role._node(path, doc['uid'], directory=True)['mode']) == 0o700, 'WORKSPACE_CUSTODY')


def _preflight(request):
    doc = _request(request)
    verify_source(doc['source_commit'])
    unit = unit_for(request)
    require(show(unit).get('LoadState') == 'not-found' and drained(request), 'WORKER_UNIT_IN_USE')
    return doc


def _worker(request, process):
    deadline = min(time.monotonic() + START_TIMEOUT, request.document()['expires_monotonic_ns'] / 10**9)
    while time.monotonic() < deadline:
        _request(request)
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
                    identity = peer.process_worker(pid, ticks, request.document()['uid'])
                    _worker_sandbox(path)
                    rows = _metadata(path / 'cgroup').splitlines()
                    require(rows == ['0::/system.slice/' + unit_for(request)], 'WORKER_CGROUP')
                    require(show(unit_for(request)) == value, 'WORKER_OWNERSHIP_CHANGED')
                    return identity
        time.sleep(0.05)
    raise ValueError('GOLD_RESEARCH_HANDOFF_WORKER_START_TIMEOUT')


def _worker_sandbox(path):
    rows = {}
    for line in _metadata(path / 'status').splitlines():
        key, _, value = line.partition(':')
        if key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb', 'NoNewPrivs'):
            require(key not in rows, 'WORKER_PRIVILEGES')
            rows[key] = value.strip()
    require(set(rows) == {'CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb', 'NoNewPrivs'}
        and rows['NoNewPrivs'] == '1' and all(re.fullmatch('0+', rows[key])
            for key in rows if key != 'NoNewPrivs'), 'WORKER_PRIVILEGES')


def _eof(channel, timeout):
    handoff._channel(channel, timeout=timeout)
    raw, controls, flags, _ = channel.recvmsg(1, socket.CMSG_SPACE(32), socket.MSG_CMSG_CLOEXEC)
    # Unexpected descriptors must also be reclaimed on the rejection path.
    for level, kind, data in controls:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            descriptors = array.array('i')
            descriptors.frombytes(data[:len(data) - len(data) % descriptors.itemsize])
            for descriptor in descriptors:
                os.close(descriptor)
    require(not raw and not controls and not flags & ~socket.MSG_CMSG_CLOEXEC, 'WORKER_EXTRA_OUTPUT')


def _reap(process, pidfd):
    try:
        process.wait(timeout=WRAPPER_WAIT_TIMEOUT)
        return
    except subprocess.TimeoutExpired:
        require(pidfd is not None, 'WRAPPER_IDENTITY')
    # The held kernel handle denotes only our Popen wrapper, never a service
    # PID obtained from a replaced manager record or a reused numerical PID.
    for signum in (signal.SIGTERM, signal.SIGKILL):
        try:
            signal.pidfd_send_signal(pidfd, signum, None, 0)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue
    raise ValueError('GOLD_RESEARCH_HANDOFF_WRAPPER_NOT_REAPED')


class ControlledResearchWorker:
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
        self._receipt = dict(schema='GOLD_RESEARCH_WORKER_EXIT_V1',
            request_sha256=self.request.request_sha256, uid=self.peer.uid,
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
    parent, inherited = handoff.channel_pair()
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
        require(peer.process_worker(identity['pid'], identity['start_ticks'], identity['uid']) == identity,
            'WORKER_IDENTITY')
        consumer = peer.connect(request, parent, worker_pid=identity['pid'], worker_start_ticks=identity['start_ticks'])
        controlled = ControlledResearchWorker(_TOKEN, request=request, process=process, consumer=consumer, pidfd=pidfd)
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
    """Capture the complete configured research portion, then verify every exit."""
    captured = handoff.roster.require_captured(verified_layout, verified_public=verified_public)
    if file_leases is not None:
        from tools.demo_gold_file_leases import OriginalFileReadLeases
        require(type(file_leases) is OriginalFileReadLeases, 'LIVE_FILE_LEASES_REQUIRED')
        captured = file_leases.guarded_layout(verified_layout=captured, verified_public=verified_public)
    group = peer.ResearchCaptureSet(verified_layout=captured, verified_public=verified_public)
    require(type(source_attachments) is dict and all(type(uid) is int for uid in source_attachments)
        and set(source_attachments) == set(group.uids), 'COMPLETE_RESEARCH_ROSTER')
    with ExitStack() as stack:
        workers = {}
        for uid in group.uids:
            request = stack.enter_context(handoff.issue(verified_layout=captured, verified_public=verified_public,
                uid=uid, source_attachment=source_attachments[uid]))
            controlled = stack.enter_context(launch(request))
            controlled.peer.prepare()
            group.add(controlled.peer)
            workers[uid] = controlled
        group.verify_archives()
        group.recheck_all()
        result = group.close_all()
        receipts = {str(uid): controlled.finish() for uid, controlled in workers.items()}
        for controlled in workers.values():
            controlled.recheck_exit()
        captured.recheck()
        result = dict(result, worker_exit_verified=True, worker_exits=receipts)
        if file_leases is not None:
            report = file_leases.report()
            result.update(original_file_leases_verified=True, original_file_lease_scope_sha256=report['scope_sha256'])
        return result
