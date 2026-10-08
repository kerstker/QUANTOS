"""Historical position replay from original chain cuts and technical receipts.

This is a read-only checkpoint verifier. It creates only disposable native OMS
projections; financial accounting remains the original position-context reducer.
No returned view grants permission or makes a restored namespace resumable.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
from itertools import islice
from pathlib import Path
import tempfile

from live.execution.demo_batch_risk import _digest
from live.execution import demo_position_context as position
from live.execution.demo_pre_submit import _json_value
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.demo_gold_checkpoint_components import _read, require
from tools.demo_resident_recovery import _check_path

CORE = ('reservations', 'recovery', 'oms', 'wire')
SCHEMA = 'GOLD_HISTORICAL_POSITION_REPLAY_V1'


class HistoricalContextUnproven(ValueError):
    """The original retained evidence does not prove a complete historical cut."""


def _unproven(reason):
    raise HistoricalContextUnproven(reason)


def _same(a, b):
    return _digest(a) == _digest(b)


def _stat(path):
    info = path.lstat()
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
        info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class _Evidence(position._PositionEvidence):
    def __init__(self, book, expected):
        self.book = book
        self.expected = expected
        self.selected = None if expected is None else {row['plan_sha256'] for row in expected['sources']}
        self.total_bytes = 0

    def records(self):
        maps = self.book.maps
        if self.selected is None:
            # The original installed candidate fixes the entire legacy state
            # and context digest. Its original reducer selects the completion
            # records; the resulting full context must match that fixed digest.
            return maps
        require(self.selected <= maps['plans'].keys(), 'HISTORICAL_REFERENCED_PLAN_MISSING')
        # The exact original context commitment chooses plans, not today's
        # latest plan nor a timestamp-filtered subset of the current database.
        return dict(maps, plans={key:maps['plans'][key] for key in self.selected})

    def open_journal(self, plan, imported, stack):
        from services.research.demo_execution_journal import DemoExecutionJournal
        relative = 'receipt-reconciliation/receipts/journals/'+plan['manifest_hash']+'.sqlite3'
        saved = str(self.book.original_root/relative)
        require(plan['journal_path'] == imported['journal_path'] == saved,
            'HISTORICAL_JOURNAL_PATH_BINDING')
        if self.book.original_entries is not None:
            row = self.book.original_entries.get(relative)
            require(type(row) is dict and row['kind'] == 'FILE', 'HISTORICAL_JOURNAL_INDEX_REQUIRED')
        for suffix in ('','-wal','-shm','-journal'):
            member = Path(str(self.book.root/relative)+suffix)
            if member.exists():
                self.total_bytes += member.lstat().st_size
        require(self.total_bytes <= 64*1024*1024, 'HISTORICAL_JOURNAL_BYTE_BUDGET')
        path = self.book.snapshot_database(relative)
        return stack.enter_context(DemoExecutionJournal(path, read_only=True))

    def recheck(self):
        self.book.recheck()


class HistoricalBook:
    def __init__(self, root, *, public, uid, retention, original_root, original_entries, stack,
                 verified_public=None, installed_partitions=True):
        self.root = Path(root).absolute()
        self.original_root = self.root if original_root is None else Path(original_root)
        require(self.original_root.is_absolute() and '..' not in self.original_root.parts,
            'HISTORICAL_ORIGINAL_ROOT')
        require(original_root is None or original_entries is not None, 'HISTORICAL_CAPTURE_INDEX_REQUIRED')
        self.original_entries = None if original_entries is None else {row['relative']:row for row in original_entries}
        require(original_entries is None or len(self.original_entries) == len(original_entries),
            'HISTORICAL_CAPTURE_INDEX_DUPLICATE')
        self.public = deepcopy(public); self.uid = uid; self.retention = retention
        self.verified_public = verified_public
        require(type(installed_partitions) is bool, 'HISTORICAL_PARTITION_VERSION')
        self.installed_partitions=installed_partitions
        if verified_public is not None:
            from tools.demo_gold_checkpoint_public import require_verified
            require_verified(verified_public, source_commit=public['source_commit'],
                account_ref_sha256=public['account_ref_sha256'])
            require(_digest(public['records'].get('fixed_public')) == verified_public.snapshot_sha256
                and _digest(public) == verified_public.public_bundle_sha256,
                'HISTORICAL_FIXED_PUBLIC_BINDING')
        self.stack = stack; self.facts = {}; self.directories = {}; self.snapshots = {}; self.results = {}
        _check_path(self.root, uid, directory=True)
        self.remember_directory(self.root)
        self.scratch = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='gold-historical-context-')))
        self.maps = {}
        for name, relative in (('proofs','evidence'),('plans','receipt-reconciliation/plans'),
                ('outcomes','receipt-reconciliation/outcomes'),('imports','receipt-reconciliation/receipts/imports'),
                ('days','receipt-reconciliation/day-checkpoints')):
            path = self.root/relative
            self.remember_directory(path)
            from live.execution.demo_resident_recovery import _records
            self.maps[name] = {leaf.stem:value for leaf,value in
                _records(path, count_limit=1000, byte_limit=64*1024*1024)}
            for leaf in path.glob('*.json') if path.exists() else ():
                self.remember_file(leaf)
        self.triggers = {}
        from tools.demo_namespace_checkpoint import MAX_FILES
        trigger_paths = list(islice(self.root.glob('bar-exit-trigger-*.json'),MAX_FILES+1))
        require(len(trigger_paths)<=MAX_FILES,'HISTORICAL_TRIGGER_BUDGET')
        for path in trigger_paths:
            value = _read(path, uid, 32768); self.remember_file(path)
            require(path.name == 'bar-exit-trigger-'+_digest(value['binding'])+'.json',
                'HISTORICAL_TRIGGER_PATH')
            require(value['record_hash'] not in self.triggers, 'HISTORICAL_TRIGGER_DUPLICATE')
            self.triggers[value['record_hash']] = value
        self.current = self._native_chains()

    def remember_directory(self, path):
        if path.exists():
            _check_path(path, self.uid, directory=True)
            self.directories[path] = (_stat(path)[:6], tuple(sorted(p.name for p in path.iterdir())))
        else:
            self.directories[path] = None

    def remember_file(self, path):
        _check_path(path, self.uid, directory=False)
        fact = _stat(path)
        prior = self.facts.setdefault(path, fact)
        require(prior == fact, 'HISTORICAL_SOURCE_CHANGED')

    def snapshot_database(self, relative):
        from tools.demo_namespace_checkpoint import snapshot_readonly_sqlite
        if relative in self.snapshots:
            return self.snapshots[relative]
        source = self.root/relative
        for suffix in ('','-wal','-shm','-journal'):
            path = Path(str(source)+suffix)
            if path.exists() or path.is_symlink():
                self.remember_file(path)
            else:
                self.facts[path] = None
        path, _, _ = snapshot_readonly_sqlite(source, self.scratch)
        self.snapshots[relative] = path
        return path

    def _native_chains(self):
        from live.execution.demo_dispatch_store import DemoDispatchStore
        from live.execution.order_recovery import SqliteOrderRecoveryStore
        from live.execution.order_event_store import SqliteOrderEventStore
        from live.execution.position_ledger import LiveLedger
        from contextlib import closing
        result = {}
        for name, cls in (('dispatch',DemoDispatchStore),('recovery',SqliteOrderRecoveryStore),
                ('oms',LiveLedger),('order-events',SqliteOrderEventStore)):
            path = self.root/(name+'.sqlite3')
            if not path.exists():
                _unproven('HISTORICAL_EXECUTION_STORE_REQUIRED:'+name)
            with closing(cls(str(self.snapshot_database(name+'.sqlite3')), read_only=True)) as store:
                if name == 'dispatch':
                    result.update(store.evidence_snapshot())
                elif name == 'recovery':
                    result['recovery'] = [_json_value(asdict(row)) for row in store.verified_history()]
                elif name == 'order-events':
                    result['events'] = store.events()
                else:
                    result['orders'] = store.orders(); result['fills'] = store.fills()
        orders, fills = self._project(result['events'])
        if not _same(orders, result['orders']) or not _same(fills, result['fills']):
            _unproven('HISTORICAL_EVENT_COVERAGE_REQUIRED')
        return result

    @contextmanager
    def open_execution(self, *, as_of_ms):
        """Read the whole original book for component replay at this fixed cut.

        Native stores use private snapshots. Saved journal paths stay bound to
        the captured original root and index; only their indexed copies open.
        The original partition and V2 reducer keep BTC and Gold cash separate.
        This view is not cash reconciliation or permission to resume execution.
        """
        from contextlib import closing
        from live.execution.demo_dispatch_store import DemoDispatchStore
        from live.execution.order_recovery import SqliteOrderRecoveryStore
        from live.execution.position_ledger import LiveLedger
        from live.execution.demo_pre_submit import _state
        from services.research.demo_user_stream import UserStreamJournal

        require(type(as_of_ms) is int and as_of_ms > 0, 'HISTORICAL_EXECUTION_TIME')
        if self.verified_public is None:
            _unproven('HISTORICAL_PARTITION_RECORDS_REQUIRED')
        self.recheck()
        with ExitStack() as stack:
            stores = {}
            for name, relative, cls in (
                    ('dispatch', 'dispatch.sqlite3', DemoDispatchStore),
                    ('recovery', 'recovery.sqlite3', SqliteOrderRecoveryStore),
                    ('oms', 'oms.sqlite3', LiveLedger)):
                stores[name] = stack.enter_context(closing(cls(
                    str(self.snapshot_database(relative)), read_only=True)))
            stores['stream'] = stack.enter_context(UserStreamJournal(
                self.snapshot_database('user-stream.sqlite3'), read_only=True))
            state = _state(stores['dispatch'], stores['recovery'], stores['oms'])
            require(_same(state['reservations'], self.current['reservations'])
                and _same(state['wire'], self.current['wire'])
                and _same(state['oms'], self.current['orders']), 'HISTORICAL_EXECUTION_CHANGED')
            for reservation in state['reservations']:
                self._verify_grant(reservation)

            evidence = _Evidence(self, None)
            def replay(part, *, symbol, account_ref, as_of_ms):
                return position.rebuild_context_from_records(part, symbol=symbol,
                    account_ref=account_ref, as_of_ms=as_of_ms, evidence=evidence)
            partition = self.verified_public.partition_at(as_of_ms)
            descriptor = legacy = None
            if partition['status'] == 'INSTALLED':
                from live.execution.demo_market_partition import rebuild_contexts
                from live.execution.demo_market_partition_runtime import _descriptor
                contexts = rebuild_contexts(state=state, candidate=partition['record']['candidate'],
                    as_of_ms=as_of_ms, replayer=replay)
                context, legacy = contexts['target'], contexts['legacy']
                require(context is not None, 'HISTORICAL_TARGET_EXECUTION_REQUIRED')
                descriptor = _descriptor(partition['record'])
            else:
                require(partition['status'] == 'VERIFIED_ABSENT', 'HISTORICAL_PARTITION_RECORDS_REQUIRED')
                context = replay(state, symbol='XAUUSDT',
                    account_ref=self.public['account_ref_sha256'], as_of_ms=as_of_ms)

            by_hash = {row['record_hash']: row for row in state['reservations']}
            sources, records = [], {}
            for source in context['sources']:
                reservation = by_hash[source['reservation_sha256']]
                sources.append((deepcopy(reservation), deepcopy(source)))
                plan = self.maps['plans'][source['plan_sha256']]
                if source['manifest_hash'] is None:
                    _unproven('HISTORICAL_NATIVE_JOURNAL_REQUIRED')
                imported = self.maps['imports'][plan['import_sha256']]
                journal = evidence.open_journal(plan, imported, stack)
                _, tail, _ = journal.read()
                require(journal.manifest_hash == source['manifest_hash'] == plan['manifest_hash']
                    and tail == source['journal_tail'] == plan['journal_tail'], 'HISTORICAL_JOURNAL_CHANGED')
                records[source['plan_sha256']] = dict(plan=deepcopy(plan),
                    proof=deepcopy(self.maps['proofs'][plan['proposal_sha256']]),
                    journal=dict(manifest=deepcopy(journal.manifest),
                        manifest_sha256=journal.manifest_hash, tail=tail))
            self.recheck()
            yield dict(readonly_stores=stores, execution=dict(state=state, context=context,
                reservation_sources=sources, records=records),
                market_partition=descriptor, legacy_context=legacy)
            self.recheck()

    def _project(self, records):
        from live.execution.oms import reduce_order_events
        from live.execution.position_ledger import LiveLedger
        # Only the original nonfinancial OMS read-model is written, privately.
        projections, _ = reduce_order_events(records)
        ledger = LiveLedger(':memory:', create=True)
        try:
            for projection in projections:
                fill = projection.fill
                ledger.apply_oms_projection(projection.record,
                    fill_id='' if fill is None else fill.fill_id,
                    fill_quantity=0.0 if fill is None else fill.quantity,
                    fill_price=0.0 if fill is None else fill.price)
            return ledger.orders(), ledger.fills()
        finally:
            ledger.close()

    def _verify_grant(self, reservation):
        from services.validation.demo_submission import validate_registry, registry_sha256, verify
        registry = validate_registry(self.public['registry'])
        payload = reservation['payload']; wanted = payload['context']['registry_sha256']
        # Only an exact append prefix whose original full digest matches is
        # admissible. Never change old key status, dates or any registry field.
        candidates = [dict(registry, keys=registry['keys'][:count])
            for count in range(len(registry['keys'])+1)]
        candidates = [value for value in candidates if registry_sha256(value)==wanted]
        if len(candidates)!=1:
            _unproven('HISTORICAL_REGISTRY_PREFIX_REQUIRED')
        verify(payload['envelope'],registry=candidates[0],expected_context=payload['context'],
            orders=payload['orders'],now_ms=payload['reserved_at_ms'])

    def _plan_events(self, plan, reservation):
        from live.execution.order_event_store import create_order_event, OrderEventSource
        from quantos.contracts.hashing import stable_hash
        from quantos.contracts.trading import OrderSide
        proof = self.maps['proofs'][plan['proposal_sha256']]
        require(_same(proof['context'],reservation['payload']['context']) and
            _same(proof['orders'],reservation['payload']['orders']), 'HISTORICAL_PLAN_PROOF')
        fixed = {order['newClientOrderId']:order for order in proof['orders']}
        result = {}
        for row in plan['orders']:
            order = fixed[row['client_order_id']]
            common = dict(client_order_id=row['client_order_id'],instrument_id=order['symbol'],
                side=OrderSide(order['side']),order_quantity=float(Decimal(order['quantity'])),
                strategy_id=proof['strategy_id'],order_type=order['type'],limit_price=float(Decimal(order['price'])))
            identity = 'demo-reservation:'+reservation['record_hash']+':'+row['client_order_id']
            command = create_order_event(**common,source=OrderEventSource.COMMAND,source_sequence=0,
                venue_event_id=identity,venue_order_id='',venue_status='PENDING_NEW',cumulative_filled_qty=0.,
                avg_fill_price=0.,observed_at_ms=reservation['payload']['reserved_at_ms'],
                venue_payload_hash=stable_hash(dict(kind='OMS_COMMAND',command_event_id=identity,
                    client_order_id=row['client_order_id'])))
            result[(command.source.value,identity)] = command
            for value in row['events']:
                identity = 'demo-receipt:'+value['receipt_sha256']
                event = create_order_event(**common,source=OrderEventSource.USER_STREAM,
                    source_sequence=value['source_sequence'],venue_event_id=identity,
                    venue_order_id=row['venue_order_id'],venue_status=value['status'],
                    cumulative_filled_qty=float(Decimal(value['filled_quantity'])),
                    avg_fill_price=float(Decimal(value['average_price'])),observed_at_ms=value['received_at_ms'],
                    venue_payload_hash=value['receipt_sha256'])
                key = (event.source.value,identity)
                require(key not in result,'HISTORICAL_RECEIPT_EVENT_DUPLICATE')
                result[key] = event
        return result

    def _state(self, proof, expected):
        state = {key:deepcopy(proof['state'][key]) for key in CORE}
        current = self.current
        for row in state['reservations']:
            self._verify_grant(row)
        for key in ('reservations','wire'):
            require(_same(state[key],current[key][:len(state[key])]), 'HISTORICAL_'+key.upper()+'_PREFIX')
        require(state['recovery'], 'HISTORICAL_RECOVERY_CUT_REQUIRED')
        last = max(row['seq'] for row in state['recovery'])
        require(type(last) is int and 0 < last <= len(current['recovery']), 'HISTORICAL_RECOVERY_CUT')
        recovered = {}
        for row in current['recovery'][:last]:
            recovered[row['client_order_id']] = row
        require(_same(sorted(recovered.values(),key=lambda row:row['seq']), state['recovery']),
            'HISTORICAL_RECOVERY_PREFIX')
        # Anchor the OMS prefix to the actual COMMAND and receipt identities in
        # the exact context sources. No observed-at time filtering is used.
        required = {}
        for source in expected['sources']:
            plan = self.maps['plans'].get(source['plan_sha256'])
            require(plan is not None and plan['reservation_sha256'] == source['reservation_sha256'],
                'HISTORICAL_REFERENCED_PLAN_MISSING')
            reserved = [row for row in state['reservations'] if row['record_hash']==source['reservation_sha256']]
            require(len(reserved)==1,'HISTORICAL_PLAN_RESERVATION')
            queries = [row for row in state['wire'] if row['record_hash'] in
                {order['query_record_sha256'] for order in plan['orders']}]
            require(len(queries)==len(plan['orders']),'HISTORICAL_PLAN_QUERY_CUT')
            dispatch_cut = dict(reservations=current['reservations'][:reserved[0]['seq']],
                wire=current['wire'][:max(row['seq'] for row in queries)])
            if _digest(dispatch_cut)!=plan['dispatch_evidence_sha256']:
                _unproven('HISTORICAL_DISPATCH_CUT_UNPROVEN')
            if plan['schema'] != 'DEMO_OMS_RECEIPT_RESOLUTION_V1':
                _unproven('HISTORICAL_REPORTED_EVENT_CUT_UNPROVEN')
            events = self._plan_events(plan,reserved[0])
            require(not required.keys() & events.keys(),'HISTORICAL_RECEIPT_EVENT_DUPLICATE')
            required.update(events)
        matched = [row for row in current['events'] if (row.event.source.value,row.event.venue_event_id) in required]
        require(len(matched) == len(required) and matched, 'HISTORICAL_EVENT_CUT_REQUIRED')
        require(all(row.event==required[(row.event.source.value,row.event.venue_event_id)] for row in matched),
            'HISTORICAL_RECEIPT_EVENT_BINDING')
        event_cut = max(row.seq for row in matched)
        orders, _ = self._project(current['events'][:event_cut])
        require(_same(orders,state['oms']), 'HISTORICAL_EVENT_STATE_DIFFERENCE')
        cuts = dict(reservations=len(state['reservations']),wire=len(state['wire']),recovery=last,order_events=event_cut)
        return state, cuts

    def replay_trigger(self, *, trigger_sha256, runtime_sha256):
        from live.execution import demo_bar_exit_trigger as native
        from tools.gold_pilot_retention import validate_retained_result
        from services.validation.demo_strategy_constraints import check_proposal_binding
        key = (trigger_sha256, runtime_sha256)
        if key in self.results:
            self.recheck(); return deepcopy(self.results[key])
        trigger = self.triggers.get(trigger_sha256)
        require(trigger is not None, 'HISTORICAL_TRIGGER_REQUIRED')
        native.validate(trigger, trigger['binding'], trigger['observed_at_ms'])
        require(self.retention is not None and self.retention.read_only and self.retention.verify_history
            and self.retention.role=='executor' and self.retention.uid==self.uid
            and self.retention.binding['source_commit']==self.public['source_commit']
            and self.retention.binding['account_ref_sha256']==self.public['account_ref_sha256']
            and _same(self.retention.binding['base_runtime'],self.public['base_runtime']),
            'HISTORICAL_AUDITED_RETENTION_REQUIRED')
        claim = self.retention.reference('origin', trigger['origin_sha256'])
        require(claim['bundle_sha256'] == runtime_sha256, 'HISTORICAL_RUNTIME_BINDING')
        decision_bytes = self.retention.read_claim(claim,'decision')
        result_bytes = self.retention.read_claim(claim,'result')
        require(decision_bytes is not None and result_bytes is not None, 'HISTORICAL_TRIGGER_FRAME')
        decision = decode(decision_bytes)
        require(_same(decision, validate_retained_result(decode(result_bytes),claim,self.retention.binding)),
            'HISTORICAL_TRIGGER_FRAME')
        draft = decision['draft']
        require(draft['status']=='BAR_EXIT_DRAFT' and draft['exit_trigger_sha256']==trigger_sha256
            and draft['position_context_sha256']==trigger['position_context_sha256']
            and decision['origin_sha256']==trigger['origin_sha256']
            and decision['observed_at_ms']==trigger['observed_at_ms'], 'HISTORICAL_TRIGGER_FRAME')
        candidates = []
        for proof_hash, proof in self.maps['proofs'].items():
            value = proof.get('state')
            if type(value) is dict and set(CORE) <= value.keys() and 'position_context' in value:
                state = {name:value[name] for name in CORE}
                if _digest(state)==decision['execution_state_sha256'] and _digest(value['position_context'])==trigger['position_context_sha256']:
                    candidates.append((proof_hash,proof))
        if not candidates:
            _unproven('HISTORICAL_STATE_CUT_UNPROVEN')
        # Different proposals can legitimately consume an identical historical
        # state; their exact committed state/context must be identical.
        require(len({_digest(row[1]['state']['position_context']) for row in candidates})==1,
            'HISTORICAL_STATE_AMBIGUOUS')
        _, proof = candidates[0]
        check_proposal_binding(proof)
        require(_digest(proof['state'])==proof['context']['recovery_sha256'], 'HISTORICAL_PROOF_STATE_BINDING')
        matching = [row for row in self.current['reservations'] if
            _same(row['payload']['context'],proof['context']) and _same(row['payload']['orders'],proof['orders'])]
        require(len(matching)==1,'HISTORICAL_ORIGINAL_PROPOSAL_REQUIRED')
        self._verify_grant(matching[0])
        expected = proof['state']['position_context']
        require(expected['account_ref_sha256']==self.public['account_ref_sha256']
            and expected['symbol']=='XAUUSDT','HISTORICAL_CONTEXT_SCOPE')
        if self.verified_public is None:
            _unproven('HISTORICAL_PARTITION_RECORDS_REQUIRED')
        partition = self.verified_public.partition_at(trigger['observed_at_ms'])
        if not self.installed_partitions and (partition['status']!='VERIFIED_ABSENT'
                or 'market_partition' in proof['state']):
            _unproven('HISTORICAL_PARTITION_RECORDS_REQUIRED')
        state, cut = self._state(proof,expected)
        partition_summary=None
        if partition['status']=='INSTALLED':
            from live.execution.demo_market_partition import rebuild_contexts, partition_state
            from live.execution.demo_market_partition_runtime import _descriptor
            record=partition['record'];descriptor=_descriptor(record)
            require(_same(proof['state'].get('market_partition'),descriptor),
                'HISTORICAL_PARTITION_DESCRIPTOR')
            split=partition_state(state=state,candidate=record['candidate'],as_of_ms=trigger['observed_at_ms'])
            def replay_partition(part,*,symbol,account_ref,as_of_ms):
                return position.rebuild_context_from_records(part,symbol=symbol,account_ref=account_ref,
                    as_of_ms=as_of_ms,evidence=_Evidence(self,expected if symbol=='XAUUSDT' else None))
            contexts=rebuild_contexts(state=state,candidate=record['candidate'],as_of_ms=trigger['observed_at_ms'],
                replayer=replay_partition)
            context=contexts['target']
            partition_summary=dict(descriptor=descriptor,legacy_state_sha256=_digest(split['legacy']),
                legacy_context_sha256=_digest(contexts['legacy']),target_state_sha256=_digest(split['target']),
                legacy_reservations=len(split['legacy']['reservations']),legacy_wire=len(split['legacy']['wire']),
                event_coverage='COMPLETE_NATIVE_EVENT_CHAIN',collateral_conversion='NOT_PERFORMED')
        else:
            require(partition['status']=='VERIFIED_ABSENT' and 'market_partition' not in proof['state'],
                'HISTORICAL_PARTITION_DESCRIPTOR')
            context = position.rebuild_context_from_records(state,symbol='XAUUSDT',
                account_ref=self.public['account_ref_sha256'],as_of_ms=trigger['observed_at_ms'],evidence=_Evidence(self,expected))
        require(_same(context,expected), 'HISTORICAL_CONTEXT_DIFFERENCE')
        chain_rows = dict(reservations=self.current['reservations'],wire=self.current['wire'],
            recovery=self.current['recovery'],order_events=self.current['events'])
        heads = {}
        for name,count in cut.items():
            row = chain_rows[name][count-1] if count else None
            head = '' if row is None else row.event_hash if name=='order_events' else row['record_hash']
            heads[name]=dict(records=count,head_sha256=head)
        summary = dict(schema=SCHEMA,status='HISTORICAL_CONTEXT_VERIFIED',source_commit=self.public['source_commit'],
            account_ref_sha256=self.public['account_ref_sha256'],runtime_sha256=runtime_sha256,trigger_sha256=trigger_sha256,
            claim_sha256=_digest(claim),decision_sha256=_digest(decision),origin_sha256=trigger['origin_sha256'],
            observed_at_ms=trigger['observed_at_ms'],execution_state_sha256=_digest(state),position_context_sha256=_digest(context),
            cut=heads,sources=deepcopy(context['sources']),partition=partition_summary,submission_enabled=False,economic_authority=False,
            mutation_permitted=False)
        self.recheck()
        self.results[key] = dict(context=context, summary=summary)
        return deepcopy(self.results[key])

    def recheck(self):
        if self.verified_public is not None:
            self.verified_public.recheck()
        for path, fact in self.facts.items():
            require((not path.exists() and not path.is_symlink()) if fact is None else _stat(path)==fact,
                'HISTORICAL_SOURCE_CHANGED')
        for path, fact in self.directories.items():
            require((not path.exists() and not path.is_symlink()) if fact is None else
                (_stat(path)[:6],tuple(sorted(p.name for p in path.iterdir())))==fact, 'HISTORICAL_SOURCE_CHANGED')


@contextmanager
def open_history(root, *, public, uid, retention, original_root=None, original_entries=None,
                 verified_public=None, installed_partitions=True):
    """Open fixed native originals or an already index-verified isolated cut.

    Original path/index arguments are trusted checkpoint inputs, never a runtime
    reader identity map; saved plan bytes are neither changed nor re-signed.
    """
    with ExitStack() as stack:
        book = HistoricalBook(root,public=public,uid=uid,retention=retention,
            original_root=original_root,original_entries=original_entries,stack=stack,
            verified_public=verified_public,installed_partitions=installed_partitions)
        yield book
        book.recheck()
