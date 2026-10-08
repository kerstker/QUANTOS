"""Read original cross-role evidence from Root's native isolated restorations.

These checks establish signed consumption and retained mirror consistency. They
do not certify a writer barrier, complete cash reconciliation or trading rights.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import tempfile

from tools import demo_gold_role_capture_peer as roles
from tools import demo_gold_research_capture_peer as research
from tools import demo_gold_checkpoint_components as components
from tools import demo_gold_issuer_executor_join as grants
from tools import demo_gold_historical_position as historical
from tools import gold_pilot_closure as closure
from tools import gold_pilot_reobservation as reobservation
from tools import gold_pilot_retention as retained
from tools.gold_pilot_retention_store import Retention, active, present

native, transport, roster = roles.native, roles.transport, roles.handoff.roster
NAMES = ('executor', 'issuer', 'controller')
_TOKEN = object()


def require(ok, reason):
    if not ok:
        raise ValueError('GOLD_RESTORED_JOINS_' + reason)


@contextmanager
def _grants(executor, issuer, public):
    cancellation = executor / 'cancellations.sqlite3'
    with grants.open_join(issuer_database=issuer / 'issuance.sqlite3',
            executor_database=executor / 'dispatch.sqlite3', evidence_directory=executor / 'evidence',
            account_ref_sha256=public['account_ref_sha256'],
            deployment_sha256=public['registry']['deployment_sha256'],
            cancellation_database=cancellation if present(cancellation) else None) as handle:
        yield handle


@contextmanager
def _retained(executor, controller, *, public, original):
    source_active, controller_active = active(executor), active(controller)
    require(source_active or not controller_active, 'CONTROLLER_WITHOUT_EXECUTOR_RETENTION')
    if not source_active:
        yield dict(report=dict(status='ABSENCE_VERIFIED', verified_partial=[], mutation_permitted=False),
            stores=(), checkpoints=())
        return
    require(original is not None and original['configuration'] is not None, 'ORIGINAL_ENROLLMENT_REQUIRED')
    base, current = public['base_runtime'], public['current_runtime']
    require(base is not None and current is not None, 'RUNTIME_REQUIRED')
    if 'rotation' in public['records']:
        _, _, history = components.rotation(public, original)
    else:
        require(base == current, 'ROTATION_HISTORY_REQUIRED')
        history = [base]
    with ExitStack() as stack:
        source = stack.enter_context(Retention(executor, original=original, base_runtime=base,
            uid=os.getuid(), role='executor', read_only=True, verify_history=True))
        closing_view = successor = None
        if reobservation.present_reobservation(executor):
            successor = reobservation.read_reobservation(executor, retention=source, base_runtime=base,
                source_commit=public['source_commit'], account=public['account_ref_sha256'],
                executor_uid=os.getuid(), expected_runtime=current)
        elif present(executor / 'gold-pilot' / closure.INTENT):
            closing_view = closure.read_intent(executor, retention=source, runtime=current,
                source_commit=public['source_commit'], account=public['account_ref_sha256'],
                executor_uid=os.getuid())
        if controller_active:
            mirror = stack.enter_context(Retention(controller, original=original, base_runtime=base,
                uid=os.getuid(), role='controller', read_only=True, verify_history=True))
            report = retained.verify_retained_pair(source, mirror, history=history,
                closure=closing_view, reobservation=successor)
            stores = (source, mirror)
        else:
            # Absence is observable; this read never initializes or repairs a
            # controller. Complete pair validation remains explicitly pending.
            report = dict(status='CONTROLLER_RETENTION_NOT_INITIALIZED',
                executor_checkpoint=source.metadata(), controller_checkpoint=None,
                verified_partial=[dict(status='CONTROLLER_RETENTION_NOT_INITIALIZED')],
                mutation_permitted=False)
            stores = (source,)
        checkpoints = tuple(store.metadata() for store in stores)
        yield dict(report=report, stores=stores, checkpoints=checkpoints)
        require(tuple(store.metadata() for store in stores) == checkpoints, 'RETENTION_CHECKPOINT_CHANGED')


@contextmanager
def _cash_source(executor, *, public, original_entries, source_attachment):
    """Recreate verified Git controls only in the original private read shadow."""
    if original_entries is None:
        require(source_attachment is None, 'RESTORED_SOURCE_INDEX_REQUIRED')
        yield dict(root=executor, trees=None, source_attachment=None)
        return
    require(source_attachment is not None, 'RESTORED_SOURCE_ATTACHMENT_REQUIRED')
    native._source_scope(source_attachment, public['source_commit'], public)
    dependencies = native._source_dependencies(public, 'executor', source_attachment)
    with native.verified_source_trees(source_attachment) as trees:
        with tempfile.TemporaryDirectory(prefix='gold-restored-cash-read-') as directory:
            shadow = Path(directory)
            native._semantic_shadow(executor, shadow, original_entries)
            native._prepare_source_shadow(shadow, dependencies, source_attachment, trees)
            yield dict(root=shadow, trees=trees, source_attachment=source_attachment)
            trees.recheck(source_attachment)


@contextmanager
def _cash(executor, *, public, original, retention, verified_public,
          original_root, original_entries, as_of_ms, source_attachment=None):
    """Consume the original reducer and cash components from a held restore."""
    report = dict(schema='GOLD_RESTORED_CASH_COMPONENTS_V1', source_commit=public['source_commit'],
        account_ref_sha256=public['account_ref_sha256'], as_of_ms=as_of_ms,
        status='NOT_STARTED', episodes=[], partial=[], technical_cash_components_verified=False,
        reported_cash_replay_verified=False,
        cash_reconciled=False, writer_barrier_verified=False, whole_set_verified=False,
        mutation_permitted=False, submission_enabled=False, economic_authority=False)
    if not components.active(executor) or public['base_runtime'] is None:
        yield dict(report=report, book=None)
        return
    with historical.open_history(executor, public=public, uid=os.getuid(), retention=retention,
            verified_public=verified_public, original_root=original_root,
            original_entries=original_entries) as book:
        if not book.current['reservations']:
            report['status'] = 'NO_EXECUTION_HISTORY'
            yield dict(report=report, book=book)
            return
        with book.open_execution(as_of_ms=as_of_ms) as replay:
            if replay['execution']['context']['quantity'] != '0':
                report['status'] = 'POSITION_OPEN'
                yield dict(report=report, book=book)
                return
            with _cash_source(executor, public=public, original_entries=original_entries,
                    source_attachment=source_attachment) as source:
                checked = components.verify(source['root'], namespace='executor', public=public,
                    original=original, uid=os.getuid(), retention=retention, historical=book,
                    cash_as_of_ms=as_of_ms, **{key: replay[key] for key in
                        ('readonly_stores', 'execution', 'market_partition')})
                report['episodes'] = [{key: deepcopy(row[key]) for key in
                    ('bundle_sha256', 'cash', 'reported_capture', 'feedback')} for row in checked['episodes']]
                report['partial'] = deepcopy(checked['partial'])
                complete = bool(report['episodes']) and not report['partial'] and all(
                    all(type(row[key]) is dict and row[key].get('status') == 'COMPLETE'
                        for key in ('cash', 'reported_capture', 'feedback')) for row in report['episodes'])
                reported = bool(report['episodes']) and not report['partial'] and all(
                    all(type(row[key]) is dict and row[key].get('status') == 'COMPLETE'
                        for key in ('reported_capture', 'feedback')) for row in report['episodes'])
                report.update(status='COMPLETE' if complete else 'PARTIAL',
                    technical_cash_components_verified=complete, reported_cash_replay_verified=reported)
                yield dict(report=report, book=book, **source)


def require_cash_report(value, *, public, as_of_ms):
    """Check the technical report's exact scope and preserved unknown states."""
    fields = set(('schema source_commit account_ref_sha256 as_of_ms status episodes partial '
        'technical_cash_components_verified reported_cash_replay_verified cash_reconciled '
        'writer_barrier_verified whole_set_verified mutation_permitted submission_enabled economic_authority').split())
    require(type(value) is dict and set(value) == fields
        and value['schema'] == 'GOLD_RESTORED_CASH_COMPONENTS_V1'
        and value['source_commit'] == public['source_commit']
        and value['account_ref_sha256'] == public['account_ref_sha256']
        and type(value['as_of_ms']) is int and value['as_of_ms'] == as_of_ms
        and value['status'] in ('NOT_STARTED', 'NO_EXECUTION_HISTORY', 'POSITION_OPEN', 'PARTIAL', 'COMPLETE')
        and type(value['episodes']) is list and type(value['partial']) is list
        and all(value[key] is False for key in ('cash_reconciled', 'writer_barrier_verified',
            'whole_set_verified', 'mutation_permitted', 'submission_enabled', 'economic_authority')),
        'CASH_REPORT_BINDING')
    for row in value['episodes']:
        require(type(row) is dict and set(row) == {'bundle_sha256', 'cash', 'reported_capture', 'feedback'}
            and type(row['bundle_sha256']) is str and components.HASH.fullmatch(row['bundle_sha256'])
            and all(row[key] is None or type(row[key]) is dict for key in ('cash', 'reported_capture', 'feedback')),
            'CASH_REPORT_EPISODE')
    complete = bool(value['episodes']) and not value['partial'] and all(
        all(type(row[key]) is dict and row[key].get('status') == 'COMPLETE'
            for key in ('cash', 'reported_capture', 'feedback')) for row in value['episodes'])
    reported = bool(value['episodes']) and not value['partial'] and all(
        all(type(row[key]) is dict and row[key].get('status') == 'COMPLETE'
            for key in ('reported_capture', 'feedback')) for row in value['episodes'])
    require(value['technical_cash_components_verified'] is complete
        and value['reported_cash_replay_verified'] is reported
        and (value['status'] == 'COMPLETE') is complete
        and (value['status'] in ('PARTIAL', 'COMPLETE')) is bool(value['episodes'])
        and (bool(value['episodes']) or not value['partial']),
        'CASH_REPORT_COMPLETENESS')
    return value


class _Restoration:
    def __init__(self, consumer, namespace, captured, public):
        is_research = namespace == 'research'
        require(is_research or namespace in NAMES, 'ORIGINAL_ROLE_SCOPE')
        peer_type = research.ResearchPeer if is_research else roles.RolePeer
        request_type = research.handoff.ResearchRequest if is_research else roles.handoff.RoleRequest
        require(type(consumer) is peer_type and type(consumer._request) is request_type,
            'ORIGINAL_ROLE_PEER_REQUIRED')
        self.peer, self.namespace = consumer, namespace
        self.layout, self.public = captured, public
        consumer._live()
        doc = consumer._request.document()
        uid = consumer.uid
        expected_uid = uid if is_research else native._role_uid(captured._public['registry'], namespace)
        require(consumer._request._layout is captured and consumer._request._public is public
            and doc.get('namespace', 'research') == namespace and type(uid) is int and uid == expected_uid
            and (not is_research or uid in captured._public['registry']['research_uids'])
            and consumer._status is not None and consumer._status['phase'] == 'PREPARED'
            and consumer._verified is not None and consumer._manifest is not None,
            'ORIGINAL_PREPARED_RESTORATION_REQUIRED')
        parent = research.handoff.OUTPUT_ROOT if is_research else roles.handoff.OUTPUT_ROOT
        leaf = str(uid) if is_research else namespace
        root = parent / doc['operation_sha256'] / ('root-' + leaf) / 'restored'
        require(is_research or isinstance(consumer._restored, Path) and consumer._restored == root,
            'ORIGINAL_RESTORE_PATH_REQUIRED')
        self.root, self.state = root, root / 'state'
        self.manifest = deepcopy(consumer._manifest)
        self.verified = deepcopy(consumer._verified)
        self.archive_sha = consumer._status['archive_manifest_sha256']
        self.inventory_sha = consumer._status['inventory_sha256']
        require(self.manifest['public_bundle'] == captured._public
            and self.manifest['cut_request'] == captured._cut
            and native._digest(self.manifest['inventory']) == self.inventory_sha
            and self.manifest['inventory']['namespace'] == namespace
            and self.verified['namespace'] == namespace and self.verified['source_uid'] == consumer.uid
            and self.verified['source_commit'] == captured._public['source_commit']
            and self.verified['state'] == self.manifest['inventory']['semantic_view']
            and self.verified['original_identity_commitment_verified'] is True
            and self.verified['source_verified'] is True, 'NATIVE_RESTORATION_BINDING')
        self.directories = {path: transport._identity(transport._private(path, directory=True))
            for path in (self.root, self.state, self.root / 'index')}
        self.manifest_identity = transport._identity(transport._private(root / 'checkpoint-manifest.json'))
        self.entries = list(native._index_rows(root, self.manifest['inventory']['index'], os.getuid()))
        self.expected = {row['relative']: row for row in self.entries}
        require(len(self.expected) == len(self.entries), 'INDEX_DUPLICATE')
        self.index_identities = {root / row['name']: transport._identity(transport._private(root / row['name']))
            for row in self.manifest['inventory']['index']}
        self.snapshot = native._restored_snapshot(self.state, self.expected, os.getuid())
        self.recheck()

    def recheck(self):
        consumer = self.peer
        consumer._request.recheck()
        if not consumer._closed:
            consumer._live()
        require(consumer._request._layout is self.layout and consumer._request._public is self.public
            and (self.namespace == 'research' or consumer._restored == self.root)
            and consumer._manifest == self.manifest
            and consumer._verified == self.verified and consumer._status['phase'] in ('PREPARED', 'RECHECKED', 'CLOSED')
            and consumer._closed == (consumer._status['phase'] == 'CLOSED')
            and consumer._status['archive_manifest_sha256'] == self.archive_sha
            and consumer._status['inventory_sha256'] == self.inventory_sha, 'ORIGINAL_PEER_CHANGED')
        for path, before in self.directories.items():
            require(transport._identity(transport._private(path, directory=True)) == before, 'RESTORE_DIRECTORY_CHANGED')
        path = self.root / 'checkpoint-manifest.json'
        raw = native._read(path, os.getuid(), transport.MAX_METADATA_BYTES)
        require(transport._identity(transport._private(path)) == self.manifest_identity
            and raw == transport._canonical(self.manifest) and hashlib.sha256(raw).hexdigest() == self.archive_sha,
            'RESTORE_MANIFEST_CHANGED')
        require(list(native._index_rows(self.root, self.manifest['inventory']['index'], os.getuid())) == self.entries,
            'RESTORE_INDEX_CHANGED')
        for path, before in self.index_identities.items():
            require(transport._identity(transport._private(path)) == before, 'RESTORE_INDEX_CHANGED')
        require(native._restored_snapshot(self.state, self.expected, os.getuid()) == self.snapshot,
            'RESTORE_ORIGINAL_CHANGED')
        transport._recheck_capture(consumer._archive(), consumer._archive_before, consumer.uid)


class RestoredRoleJoins:
    def __init__(self, token, restorations, signed, mirror, cash, report):
        require(token is _TOKEN, 'ORIGINAL_JOIN_REQUIRED')
        self._restorations, self._signed, self._mirror = restorations, signed, mirror
        self._cash = cash
        self._report, self._closed = deepcopy(report), False

    def recheck(self):
        require(not self._closed, 'JOIN_CLOSED')
        for restored in self._restorations.values():
            restored.recheck()
        self._signed.recheck()
        if self._cash['book'] is not None:
            self._cash['book'].recheck()
        if self._cash.get('trees') is not None:
            self._cash['trees'].recheck(self._cash['source_attachment'])
        require(tuple(store.metadata() for store in self._mirror['stores']) == self._mirror['checkpoints'],
            'RETENTION_CHECKPOINT_CHANGED')

    def report(self):
        self.recheck()
        return deepcopy(self._report)


@contextmanager
def open_join(*, peers, verified_layout, verified_public, research_peers=None):
    """Hold original restorations, including the complete supplied research roster."""
    require(os.getresuid() == (0, 0, 0) and os.getresgid() == (0, 0, 0), 'ROOT_CONTROLLER_REQUIRED')
    captured = roster.require_captured(verified_layout, verified_public=verified_public)
    require(type(peers) is dict and set(peers) == set(NAMES), 'COMPLETE_FIXED_ROLE_SCOPE')
    restorations = {name: _Restoration(peers[name], name, captured, verified_public) for name in NAMES}
    research_restorations = {}
    if research_peers is not None:
        require(type(research_peers) is dict and all(type(uid) is int for uid in research_peers)
            and set(research_peers) == set(captured._public['registry']['research_uids'])
            and all(type(peer) is research.ResearchPeer and peer.uid == uid
                for uid, peer in research_peers.items()),
            'COMPLETE_RESEARCH_ROLE_SCOPE')
        research_restorations = {uid: _Restoration(research_peers[uid], 'research', captured, verified_public)
            for uid in sorted(research_peers)}
    executor, issuer, controller = (restorations[name].state for name in NAMES)
    _, original = native._enrollment(executor, os.getuid(), captured._public)
    _, public_original = native._public_pilot(captured._public)
    if active(controller):
        require(original is not None and original == public_original, 'PUBLIC_ENROLLMENT_BINDING')
    with ExitStack() as stack:
        signed = stack.enter_context(_grants(executor, issuer, captured._public))
        mirror = stack.enter_context(_retained(executor, controller, public=captured._public, original=original))
        cash = stack.enter_context(_cash(executor, public=captured._public, original=original,
            retention=mirror['stores'][0] if mirror['stores'] else None, verified_public=verified_public,
            original_root=Path(restorations['executor'].manifest['inventory']['source_root']['path']),
            original_entries=restorations['executor'].entries, as_of_ms=captured._cut['opening_at_ms'],
            source_attachment=restorations['executor'].peer._request.document()['source_attachment']))
        require_cash_report(cash['report'], public=captured._public, as_of_ms=captured._cut['opening_at_ms'])
        report = dict(schema='GOLD_RESTORED_ROLE_JOINS_V2', source_commit=captured._public['source_commit'],
            operation_sha256=captured._cut['operation_sha256'], fixed_layout_sha256=native._digest(captured.snapshot()),
            public_bundle_sha256=native._digest(captured._public), cut_request_sha256=native._digest(captured._cut),
            archives={name: dict(archive_manifest_sha256=value.archive_sha, inventory_sha256=value.inventory_sha)
                for name, value in restorations.items()},
            issuer_executor=signed.report(), retention=deepcopy(mirror['report']),
            cash_components=deepcopy(cash['report']),
            issuer_executor_join_verified=True, retention_pair_checked=True,
            retention_pair_verified=mirror['report'].get('schema') == 'GOLD_RETAINED_PAIR_VIEW_V1',
            cross_role_business_join_verified=False, writer_barrier_verified=False,
            whole_set_verified=False, cash_reconciled=False, mutation_permitted=False,
            submission_enabled=False, economic_authority=False)
        if research_peers is not None:
            report.update(schema='GOLD_RESTORED_ROLE_JOINS_V3',
                research_archives={str(uid): dict(archive_manifest_sha256=value.archive_sha,
                    inventory_sha256=value.inventory_sha) for uid, value in research_restorations.items()},
                research_restore_continuity_verified=True)
        held_restorations = dict(restorations)
        held_restorations.update({('research', uid): value for uid, value in research_restorations.items()})
        handle = RestoredRoleJoins(_TOKEN, held_restorations, signed, mirror, cash, report)
        try:
            handle.recheck()
            yield handle
            handle.recheck()
        finally:
            handle._closed = True
