"""Root consumer of held fixed-role archives using original native restoration.

Per-role status alone cannot close a capture. The root independently copies,
binds and restores the original archive before requesting RECHECK or CLOSE.
Research peers and their non-root permission type remain unchanged.
"""
from copy import deepcopy
from pathlib import Path

from tools import demo_gold_role_handoff as handoff
from tools import demo_gold_role_capture_worker as worker
from tools import demo_gold_research_capture_peer as research
from tools import demo_gold_checkpoint_transport as transport
from tools.gold_pilot_retention_store import canonical

require = handoff.require
native = handoff.native
_TOKEN = object()


def _status(value, *, request, identity, expected_phase, previous=None):
    require(type(request) is handoff.RoleRequest, 'CAPTURED_ROLE_REQUEST_REQUIRED')
    native._fields(value, research.STATUS_FIELDS)
    doc = request.document()
    require(value['schema'] == worker.STATUS_SCHEMA and value['phase'] == expected_phase
        and expected_phase in ('PREPARED', 'RECHECKED', 'CLOSED')
        and value['request_sha256'] == request.request_sha256 and value['namespace'] == doc['namespace']
        and type(value['uid']) is int and value['uid'] == doc['uid'] and value['archive'] == 'archive'
        and all(value[key] is False for key in ('writer_barrier_verified', 'whole_set_verified',
            'submission_enabled', 'economic_authority')), 'STATUS_BINDING')
    receipt = value['role_receipt']
    native._fields(receipt, research.RECEIPT_FIELDS)
    actual = receipt['worker_identity']
    native._fields(actual, 'uid gid groups pid start_ticks')
    require(all(type(actual[key]) is int and actual[key] >= 0 for key in ('uid', 'gid', 'pid', 'start_ticks'))
        and actual['pid'] > 0 and actual['start_ticks'] > 0
        and actual['uid'] == actual['gid'] == doc['uid'] and type(actual['groups']) is list
        and all(type(group) is int for group in actual['groups'])
        and actual['groups'] == sorted(set(actual['groups'])) and set(actual['groups']) <= {doc['uid']}
        and actual == identity, 'STATUS_WORKER')
    require(receipt['schema'] == native.RECEIPT_SCHEMA and receipt['namespace'] == doc['namespace']
        and receipt['operation_sha256'] == doc['operation_sha256']
        and receipt['cut_request_sha256'] == native._digest(doc['cut_request'])
        and receipt['source_commit'] == doc['source_commit']
        and receipt['registry_sha256'] == native._digest(doc['public_bundle']['registry'])
        and receipt['source_attachment_sha256'] == doc['source_attachment_sha256']
        and receipt['inventory_sha256'] == value['inventory_sha256'] and receipt['stable'] is True
        and receipt['submission_enabled'] is receipt['economic_authority'] is False, 'STATUS_RECEIPT')
    for key in ('request_sha256', 'archive_manifest_sha256', 'inventory_sha256'):
        native._hash(value[key])
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
            == {key: item for key, item in previous.items() if key not in ('phase', 'role_receipt')}, 'STATUS_CHANGED')
        research._receipt_continuity(previous['role_receipt'], receipt)
    return deepcopy(value)


class RolePeer(research.ResearchPeer):
    """Reuse the original control ordering; fixed-role admission stays distinct."""
    def __init__(self, token, *, request, channel, identity):
        require(token is _TOKEN and type(request) is handoff.RoleRequest, 'CONTROLLED_ROLE_PEER_REQUIRED')
        super().__init__(research._TOKEN, request=request, channel=channel, identity=identity)
        self._restored = None

    @property
    def namespace(self):
        return self._request.document()['namespace']

    def _live(self):
        super()._live()
        handoff._worker_privileges(Path('/proc') / str(self._worker['pid']))

    def _receive(self, phase):
        self._request.recheck()
        remaining = (self._request.document()['expires_monotonic_ns'] - handoff.time.monotonic_ns()) / 10**9
        require(remaining > 0, 'EXPIRED')
        body, credentials = handoff.kernel.receive_body(self._channel, maximum=worker.MAX_STATUS_BYTES,
            timeout=min(remaining, handoff.MAX_LIFETIME_NS / 10**9))
        require(credentials == (self._worker['pid'], self.uid, self.uid), 'WORKER_CREDENTIALS')
        value = handoff.kernel.decode(body)
        require(canonical(value) == body, 'CANONICAL_STATUS')
        checked = _status(value, request=self._request, identity=self._worker,
            expected_phase=phase, previous=self._status)
        self._request.recheck()
        if phase != 'CLOSED':
            self._live()
        self._status = checked
        return deepcopy(checked)

    def _archive(self):
        return worker._workspace(self._request.document()) / 'archive'

    def _bind_manifest(self, manifest):
        doc = self._request.document()
        inventory = manifest['inventory']
        require(manifest['public_bundle'] == doc['public_bundle'] and manifest['cut_request'] == doc['cut_request']
            and manifest['source_identity'] == native.source_attachment_identity(doc['source_attachment'])
            and inventory['schema'] == native.SCHEMA and inventory['namespace'] == doc['namespace']
            and inventory['source_root']['path'] == doc['role_root']
            and all(inventory['source_root'][key] == doc['role_root_identity'][key]
                for key in ('device', 'inode', 'uid', 'gid', 'mode'))
            and native._digest(inventory) == self._status['inventory_sha256'], 'NATIVE_REQUEST_BINDING')
        _status(dict(self._status, role_receipt=manifest['role_receipt']), request=self._request,
            identity=self._worker, expected_phase='PREPARED')
        research._receipt_continuity(manifest['role_receipt'], self._status['role_receipt'])

    def verify_archive(self):
        self._live()
        require(self._status is not None and self._status['phase'] == 'PREPARED'
            and self._verified is None, 'NATIVE_VERIFICATION_ORDER')
        doc = self._request.document()
        base = handoff.OUTPUT_ROOT / doc['operation_sha256'] / ('root-' + self.namespace)
        handoff.roster._parents(base, (0, 0))
        handoff.kernel.role._node(handoff.OUTPUT_ROOT, 0, directory=True)
        handoff.kernel.role._node(base.parent, 0, directory=True)
        require(not base.exists() and not base.is_symlink(), 'NEW_ROOT_WORKSPACE')
        base.mkdir(mode=0o700)
        target = base / 'archive'
        target.mkdir(mode=0o700)
        archive = self._archive()
        manifest, before = transport._copy_capture(archive, target,
            self._status['archive_manifest_sha256'], self.uid)
        self._bind_manifest(manifest)
        restored = base / 'restored'
        report = transport.restore_checkpoint(target, restored, passphrase=None,
            expected_sha256=self._status['archive_manifest_sha256'])
        require(report['namespace'] == self.namespace and report['source_uid'] == self.uid
            and report['source_commit'] == doc['source_commit']
            and report['original_identity_commitment_verified'] is True and report['source_verified'] is True
            and all(report[key] is False for key in ('production_resumable', 'cross_namespace_atomic',
                'complete_gold_set_verified', 'submission_enabled', 'economic_authority', 'production_restore_performed')),
            'NATIVE_REPORT')
        transport._recheck_capture(archive, before, self.uid)
        self._live()
        self._manifest, self._archive_before, self._verified = manifest, before, report
        self._restored = restored
        return deepcopy(report)

    def _send(self, action):
        self._live()
        require(self._verified is not None, 'NATIVE_VERIFICATION_REQUIRED')
        transport._recheck_capture(self._archive(), self._archive_before, self.uid)
        raw = canonical(worker.control_document(self._request.request_sha256, action))
        handoff.kernel._channel(self._channel)
        require(self._channel.send(raw) == len(raw), 'SEND')


def connect(request, channel, *, worker_pid, worker_start_ticks):
    require(type(request) is handoff.RoleRequest, 'CAPTURED_ROLE_REQUEST_REQUIRED')
    request.recheck()
    identity = research.process_worker(worker_pid, worker_start_ticks, request.document()['uid'])
    handoff.send_request(channel, request, worker_pid=worker_pid, worker_start_ticks=worker_start_ticks)
    require(research.process_worker(worker_pid, worker_start_ticks, request.document()['uid']) == identity,
        'WORKER_IDENTITY')
    return RolePeer(_TOKEN, request=request, channel=channel, identity=identity)
