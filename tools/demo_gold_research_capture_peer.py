"""Root consumer of held research captures and their original native archives.

Status packets carry no verification authority. The root consumer independently
copies the role-owned archive and runs the existing bundle/index/native restore
validators before it can request final closure. No global writer barrier,
whole-set recovery, release acceptance or trading permission is established.
"""
from copy import deepcopy
from pathlib import Path

from tools import demo_gold_research_handoff as handoff
from tools import demo_gold_research_capture_worker as worker
from tools import demo_gold_checkpoint_transport as transport
from tools.demo_maintenance_processes import _metadata
from tools.gold_pilot_retention_store import canonical

require = handoff.require
native = handoff.native
_TOKEN = object()
STATUS_FIELDS = ('schema phase request_sha256 namespace uid archive archive_manifest_sha256 '
    'inventory_sha256 role_receipt writer_barrier_verified whole_set_verified submission_enabled '
    'economic_authority')
RECEIPT_FIELDS = ('schema operation_sha256 cut_request_sha256 namespace source_commit registry_sha256 '
    'inventory_sha256 semantic_view_sha256 source_attachment_sha256 before_sha256 after_sha256 '
    'started_at_ms completed_at_ms stable worker_identity submission_enabled economic_authority')


def _status(value, *, request, worker, expected_phase, previous=None):
    """Check the finite wire surface; native archive acceptance is separate."""
    require(type(request) is handoff.ResearchRequest, 'CAPTURED_REQUEST_REQUIRED')
    native._fields(value, STATUS_FIELDS)
    doc = request.document()
    require(value['schema'] == 'GOLD_RESEARCH_CAPTURE_STATUS_V1'
        and value['phase'] == expected_phase and expected_phase in ('PREPARED', 'RECHECKED', 'CLOSED')
        and value['request_sha256'] == request.request_sha256 and value['namespace'] == 'research'
        and type(value['uid']) is int and value['uid'] == doc['uid'] and value['archive'] == 'archive'
        and all(value[key] is False for key in ('writer_barrier_verified', 'whole_set_verified',
            'submission_enabled', 'economic_authority')), 'STATUS_BINDING')
    for key in ('request_sha256', 'archive_manifest_sha256', 'inventory_sha256'):
        native._hash(value[key])
    receipt = value['role_receipt']
    native._fields(receipt, RECEIPT_FIELDS)
    identity = receipt['worker_identity']
    native._fields(identity, 'uid gid groups pid start_ticks')
    require(all(type(identity[key]) is int and identity[key] > 0 for key in ('uid', 'gid', 'pid', 'start_ticks'))
        and identity['uid'] == identity['gid'] == doc['uid'] and type(identity['groups']) is list
        and all(type(group) is int for group in identity['groups'])
        and identity['groups'] == sorted(set(identity['groups']))
        and set(identity['groups']) <= {doc['uid']} and identity == worker, 'STATUS_WORKER')
    require(receipt['schema'] == native.RECEIPT_SCHEMA and receipt['namespace'] == 'research'
        and receipt['operation_sha256'] == doc['operation_sha256']
        and receipt['cut_request_sha256'] == native._digest(doc['cut_request'])
        and receipt['source_commit'] == doc['source_commit']
        and receipt['registry_sha256'] == native._digest(doc['public_bundle']['registry'])
        and receipt['source_attachment_sha256'] == doc['source_attachment_sha256']
        and receipt['inventory_sha256'] == value['inventory_sha256'] and receipt['stable'] is True
        and receipt['submission_enabled'] is receipt['economic_authority'] is False, 'STATUS_RECEIPT')
    for key in ('operation_sha256', 'cut_request_sha256', 'registry_sha256', 'inventory_sha256',
            'semantic_view_sha256', 'source_attachment_sha256', 'before_sha256', 'after_sha256'):
        native._hash(receipt[key])
    require(receipt['before_sha256'] == receipt['after_sha256']
        and type(receipt['started_at_ms']) is int and type(receipt['completed_at_ms']) is int
        and 0 < doc['cut_request']['opening_at_ms'] <= receipt['started_at_ms'] <= receipt['completed_at_ms'],
        'STATUS_TIME_OR_CHANGED')
    if expected_phase == 'PREPARED':
        require(previous is None, 'STATUS_ORDER')
    else:
        require(type(previous) is dict and previous.get('phase') ==
            {'RECHECKED': 'PREPARED', 'CLOSED': 'RECHECKED'}[expected_phase], 'STATUS_ORDER')
        require({key: item for key, item in value.items() if key not in ('phase', 'role_receipt')}
            == {key: item for key, item in previous.items() if key not in ('phase', 'role_receipt')},
            'STATUS_CHANGED')
        _receipt_continuity(previous['role_receipt'], receipt)
    return deepcopy(value)


def _receipt_continuity(original, latest):
    require({key: item for key, item in original.items() if key != 'completed_at_ms'}
        == {key: item for key, item in latest.items() if key != 'completed_at_ms'}
        and original['completed_at_ms'] <= latest['completed_at_ms'], 'STATUS_CHANGED')


def process_worker(pid, ticks, uid):
    """Bind real UID/GID triples, supplementary groups and PID incarnation."""
    handoff._integer(pid, minimum=1)
    handoff._integer(ticks, minimum=1)
    path = Path('/proc') / str(pid)
    require(handoff.process_identity(path) == (pid, ticks), 'WORKER_IDENTITY')
    fields = {}
    for line in _metadata(path / 'status').splitlines():
        key, _, text = line.partition(':')
        if key in ('Uid', 'Gid', 'Groups'):
            require(key not in fields, 'WORKER_IDENTITY')
            fields[key] = text.split()
    require(set(fields) == {'Uid', 'Gid', 'Groups'}
        and all(item.isdigit() for values in fields.values() for item in values)
        and tuple(map(int, fields['Uid'])) == tuple(map(int, fields['Gid'])) == (uid,) * 4
        and set(map(int, fields['Groups'])) <= {uid}, 'WORKER_IDENTITY')
    require(handoff.process_identity(path) == (pid, ticks), 'WORKER_IDENTITY')
    return dict(uid=uid, gid=uid, groups=sorted(set(map(int, fields['Groups']))), pid=pid, start_ticks=ticks)


class ResearchPeer:
    def __init__(self, token, *, request, channel, identity):
        require(token is _TOKEN, 'CONTROLLED_PEER_REQUIRED')
        self._request = request
        self._channel = channel
        self._worker = deepcopy(identity)
        self._status = None
        self._manifest = None
        self._archive_before = None
        self._verified = None
        self._closed = False

    @property
    def uid(self):
        return self._request.document()['uid']

    def _live(self):
        require(not self._closed, 'PEER_CLOSED')
        self._request.recheck()
        require(process_worker(self._worker['pid'], self._worker['start_ticks'], self.uid) == self._worker,
            'WORKER_IDENTITY')

    def _receive(self, phase):
        self._request.recheck()
        remaining = (self._request.document()['expires_monotonic_ns'] - handoff.time.monotonic_ns()) / 10**9
        require(remaining > 0, 'EXPIRED')
        body, credentials = handoff.receive_body(self._channel, maximum=worker.MAX_STATUS_BYTES,
            timeout=min(remaining, handoff.MAX_LIFETIME_NS / 10**9))
        require(credentials == (self._worker['pid'], self.uid, self.uid), 'WORKER_CREDENTIALS')
        value = handoff.decode(body)
        require(canonical(value) == body, 'CANONICAL_STATUS')
        checked = _status(value, request=self._request, worker=self._worker,
            expected_phase=phase, previous=self._status)
        self._request.recheck()
        if phase != 'CLOSED':
            self._live()
        self._status = checked
        return deepcopy(checked)

    def prepare(self):
        self._live()
        require(self._status is None, 'STATUS_ORDER')
        return self._receive('PREPARED')

    def _archive(self):
        doc = self._request.document()
        return worker._workspace(doc) / 'archive'

    def verify_archive(self):
        self._live()
        require(self._status is not None and self._status['phase'] == 'PREPARED'
            and self._verified is None, 'NATIVE_VERIFICATION_ORDER')
        doc = self._request.document()
        base = handoff.OUTPUT_ROOT / doc['operation_sha256'] / ('root-' + str(self.uid))
        handoff.roster._parents(base, (0, 0))
        handoff.role._node(handoff.OUTPUT_ROOT, 0, directory=True)
        handoff.role._node(base.parent, 0, directory=True)
        require(not base.exists() and not base.is_symlink(), 'NEW_ROOT_WORKSPACE')
        base.mkdir(mode=0o700)
        target = base / 'archive'
        target.mkdir(mode=0o700)
        archive = self._archive()
        manifest, before = transport._copy_capture(archive, target,
            self._status['archive_manifest_sha256'], self.uid)
        self._bind_manifest(manifest)
        report = transport.restore_checkpoint(target, base / 'restored', passphrase=None,
            expected_sha256=self._status['archive_manifest_sha256'])
        require(report['namespace'] == 'research' and report['source_uid'] == self.uid
            and report['source_commit'] == doc['source_commit']
            and report['original_identity_commitment_verified'] is True
            and report['source_verified'] is True and all(report[key] is False for key in (
                'production_resumable', 'cross_namespace_atomic', 'complete_gold_set_verified',
                'submission_enabled', 'economic_authority', 'production_restore_performed')),
            'NATIVE_REPORT')
        transport._recheck_capture(archive, before, self.uid)
        self._live()
        self._manifest, self._archive_before, self._verified = manifest, before, report
        return deepcopy(report)

    def _bind_manifest(self, manifest):
        doc = self._request.document()
        inventory = manifest['inventory']
        require(manifest['public_bundle'] == doc['public_bundle'] and manifest['cut_request'] == doc['cut_request']
            and manifest['source_identity'] == native.source_attachment_identity(doc['source_attachment'])
            and inventory['research_layout'] == doc['research_layout']
            and inventory['source_root']['path'] == doc['research_root']
            and all(inventory['source_root'][key] == doc['research_root_identity'][key]
                for key in ('device', 'inode', 'uid', 'gid', 'mode'))
            and native._digest(inventory) == self._status['inventory_sha256'], 'NATIVE_REQUEST_BINDING')
        _status(dict(self._status, role_receipt=manifest['role_receipt']), request=self._request,
            worker=self._worker, expected_phase='PREPARED')
        _receipt_continuity(manifest['role_receipt'], self._status['role_receipt'])

    def _send(self, action):
        self._live()
        require(self._verified is not None, 'NATIVE_VERIFICATION_REQUIRED')
        transport._recheck_capture(self._archive(), self._archive_before, self.uid)
        raw = canonical(worker.control_document(self._request.request_sha256, action))
        handoff._channel(self._channel)
        require(self._channel.send(raw) == len(raw), 'SEND')

    def recheck(self):
        require(self._status is not None and self._status['phase'] == 'PREPARED', 'STATUS_ORDER')
        self._send('RECHECK')
        status = self._receive('RECHECKED')
        transport._recheck_capture(self._archive(), self._archive_before, self.uid)
        return status

    def close_capture(self):
        require(self._status is not None and self._status['phase'] == 'RECHECKED', 'STATUS_ORDER')
        self._send('CLOSE')
        status = self._receive('CLOSED')
        transport._recheck_capture(self._archive(), self._archive_before, self.uid)
        self._closed = True
        return status

    def disconnect(self):
        self._channel.close()
        self._closed = True


def connect(request, channel, *, worker_pid, worker_start_ticks):
    """Authenticate a selected live worker; this does not accept its archive."""
    require(type(request) is handoff.ResearchRequest, 'CAPTURED_REQUEST_REQUIRED')
    request.recheck()
    identity = process_worker(worker_pid, worker_start_ticks, request.document()['uid'])
    handoff.send_request(channel, request, worker_pid=worker_pid, worker_start_ticks=worker_start_ticks)
    require(process_worker(worker_pid, worker_start_ticks, request.document()['uid']) == identity, 'WORKER_IDENTITY')
    return ResearchPeer(_TOKEN, request=request, channel=channel, identity=identity)


class ResearchCaptureSet:
    """Hold every configured research UID through native validation and recheck.

This consumes only the research portion of the original four-role cut. It never
claims global writer exclusion, four-role atomicity or production restoration.
The controlled launcher must independently verify worker exit and service drain.
"""
    def __init__(self, *, verified_layout, verified_public):
        self._layout = handoff.roster.require_captured(verified_layout, verified_public=verified_public)
        self._public = verified_public
        self._uids = tuple(sorted(self._layout._public['registry']['research_uids']))
        self._peers = {}
        self._phase = 'COLLECTING'

    @property
    def uids(self):
        return self._uids

    def _guard(self):
        handoff.roster.require_captured(self._layout, verified_public=self._public)

    def add(self, consumer):
        self._guard()
        require(self._phase == 'COLLECTING' and type(consumer) is ResearchPeer,
            'CONTROLLED_PEER_REQUIRED')
        require(consumer.uid in self._uids and consumer.uid not in self._peers
            and consumer._request._layout is self._layout and consumer._request._public is self._public
            and consumer._status is not None and consumer._status['phase'] == 'PREPARED'
            and not consumer._closed, 'ORIGINAL_RESEARCH_PEER')
        consumer._live()
        self._peers[consumer.uid] = consumer

    def _complete(self):
        self._guard()
        require(set(self._peers) == set(self._uids), 'COMPLETE_RESEARCH_ROSTER')

    def verify_archives(self):
        require(self._phase == 'COLLECTING', 'SET_ORDER')
        self._complete()
        reports = {}
        for uid in self._uids:
            self._guard()
            reports[uid] = self._peers[uid].verify_archive()
        self._guard()
        self._phase = 'VERIFIED'
        return deepcopy(reports)

    def recheck_all(self):
        require(self._phase == 'VERIFIED', 'SET_ORDER')
        self._complete()
        for uid in self._uids:
            self._guard()
            self._peers[uid].recheck()
        self._guard()
        self._phase = 'RECHECKED'

    def close_all(self):
        require(self._phase == 'RECHECKED', 'SET_ORDER')
        self._complete()
        statuses = {}
        for uid in self._uids:
            self._guard()
            statuses[str(uid)] = self._peers[uid].close_capture()
        self._guard()
        self._phase = 'CLOSED'
        return dict(schema='GOLD_RESEARCH_CAPTURE_SET_V1', phase='CLOSED',
            operation_sha256=self._layout._cut['operation_sha256'],
            source_commit=self._layout._public['source_commit'],
            fixed_layout_sha256=native._digest(self._layout.snapshot()),
            cut_request_sha256=native._digest(self._layout._cut),
            research_uids=list(self._uids), captures=statuses,
            native_research_archives_verified=True, configured_research_uids_verified=True,
            worker_exit_verified=False, writer_barrier_verified=False, whole_set_verified=False,
            submission_enabled=False, economic_authority=False)
