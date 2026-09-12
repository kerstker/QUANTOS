"""Resident recovery of original Demo identities, with no submission capability.

This is trusted executor orchestration, not an agent or research mutation API.
It can query reservations after a lost return or restart, including reservations
that never reached their first OMS write. It never manufactures an absent-order
proof, resubmits an order, cancels an order, signs approval or posts cash.
The deployment entrypoint must establish process and filesystem custody first.
"""
from contextlib import contextmanager
from decimal import Decimal
import fcntl
import os
from pathlib import Path
import re
import stat

from live.execution import demo_authority, demo_prepared_execution as executor
from live.execution.demo_pre_submit import _digest, _retain, _preparation_lease
from services.governance.json_integrity import load_json_without_duplicate_keys

SCHEMA = 'DEMO_RESIDENT_RECOVERY_V1'
ATTEMPT_SCHEMA = 'DEMO_RESIDENT_QUERY_ATTEMPT_V1'
QUERY_INTERVAL_MS = 30000
MAX_PROOFS = 1000
MAX_ATTEMPTS = 10000
MAX_SCAN_BYTES = 64 * 1024 * 1024
_SHA = re.compile('[a-f0-9]{64}')


def _records(directory, *, count_limit, byte_limit):
    """Read hash-named immutable facts, bounded across the entire directory."""
    directory = Path(directory)
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('RESIDENT_DIRECTORY_INVALID')
    result = []
    size = 0
    with os.scandir(directory) as entries:
        for entry in entries:
            # Subdirectories contain the existing query/cancellation reports;
            # only the original flat proof namespace is considered here.
            if not entry.name.endswith('.json'):
                continue
            if len(result) >= count_limit or _SHA.fullmatch(entry.name[:-5]) is None:
                raise ValueError('RESIDENT_RECORD_BUDGET_OR_NAME')
            fd = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as handle:
                before = os.fstat(handle.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise ValueError('RESIDENT_RECORD_TYPE')
                raw = handle.read(min(16_000_001, byte_limit-size+1))
                after = os.fstat(handle.fileno())
            size += len(raw)
            if (len(raw) > 16_000_000 or size > byte_limit or len(raw) != before.st_size or
                    any(getattr(before, k) != getattr(after, k) for k in
                        ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))):
                raise ValueError('RESIDENT_RECORD_CHANGED_OR_BUDGET')
            value = load_json_without_duplicate_keys(raw)
            if _digest(value) != entry.name[:-5]:
                raise ValueError('RESIDENT_RECORD_HASH')
            result.append((Path(entry.path), value))
    return result


def discover(*, dispatch, recovery, oms, evidence_directory):
    """Reconstruct read/query handles from originals; never creates intents."""
    with _preparation_lease(dispatch):
        snapshot = dispatch.evidence_snapshot()
        recovered = {r.client_order_id: r for r in recovery.latest_records()}
        models = {o['client_order_id']: o for o in oms.orders()}
        result = {'schema': SCHEMA, 'status': 'NO_RESERVATIONS', 'pending': [],
            'completed_batches': 0, 'reservations': len(snapshot['reservations']),
            'new_order_submission_enabled': False, 'cash_reconciled': False, 'economic_authority': False}
        try:
            proofs = _records(evidence_directory, count_limit=MAX_PROOFS, byte_limit=MAX_SCAN_BYTES)
            proof_error = False
        except (ValueError, OSError):
            proofs = []; proof_error = True
        for row in snapshot['reservations']:
            body = row['payload']; orders = body['orders']; context = body['context']
            clients = [o['newClientOrderId'] for o in orders]
            terminal = True
            for order in orders:
                prior = recovered.get(order['newClientOrderId']); model = models.get(order['newClientOrderId'])
                if (prior is None or model is None or prior.state.value != 'RESOLVED' or
                    prior.deployment_id != context['deployment_sha256'] or prior.instrument_id != order['symbol'] or
                    prior.side != order['side'] or prior.quantity != float(Decimal(order['quantity'])) or
                    model['status'] not in ('FILLED', 'CANCELED', 'REJECTED', 'EXPIRED') or
                    model['status'] != prior.observed_status or not prior.exchange_order_id or
                    model['venue_order_id'] != prior.exchange_order_id):
                    terminal = False
            if terminal:
                result['completed_batches'] += 1
                continue
            own = [r for r in snapshot['wire'] if r['reservation_sha256'] == row['record_hash']]
            claims = [r for r in own if r['kind'] == 'SEND_CLAIM']
            item = {'reservation_sha256': row['record_hash'], 'client_order_ids': clients, 'prepared': None,
                'send_claims': len(claims), 'first_send_claim_at_ms': min((r['at_ms'] for r in claims), default=None),
                'reason': None}
            matching = [(path, proof) for path, proof in proofs if isinstance(proof, dict) and
                proof.get('schema') == 'DEMO_PRE_SUBMISSION_PROPOSAL_V1' and
                proof.get('context') == context and proof.get('orders') == orders]
            if proof_error:
                item['reason'] = 'RESIDENT_PROOF_INVALID'
            elif len(matching) != 1:
                item['reason'] = 'RESIDENT_PROOF_MISSING' if not matching else 'RESIDENT_PROOF_AMBIGUOUS'
            else:
                path, proof = matching[0]
                prepared = {'schema': 'DEMO_PREPARED_BATCH_V1', 'status': 'INTENTS_PERSISTED',
                    'reservation_sha256': row['record_hash'], 'proposal_sha256': _digest(proof),
                    'proposal_path': str(path), 'orders': orders, 'context': context,
                    'submission_enabled': False, 'economic_authority': False}
                try:
                    executor._load_proof(prepared)
                    item['prepared'] = prepared
                except Exception:
                    item['reason'] = 'RESIDENT_PROOF_INVALID'
            result['pending'].append(item)
        if result['pending']:
            result['status'] = 'INPUTS_UNRESOLVED' if any(r['prepared'] is None for r in result['pending']) else 'RECOVERY_REQUIRED'
        elif result['reservations']:
            result['status'] = 'OMS_TERMINAL_CASH_SEPARATE'
        return result


@contextmanager
def _cycle_lease(directory):
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd = os.open(directory/'cycle.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


class RecoveryLoop:
    """One bounded pass at a time; durable pacing survives a process crash."""
    def __init__(self, *, dispatch, recovery, oms, evidence_directory, output_directory,
                 stream=None, event_store=None, source_commit=None, clock=executor.now_ms):
        self.stores = dict(dispatch=dispatch, recovery=recovery, oms=oms)
        self.evidence = Path(evidence_directory)
        self.output = Path(output_directory)
        self.clock = clock
        self.stream, self.event_store, self.source_commit = stream, event_store, source_commit
        self.pacing = {'attempt_count': None, 'last_attempt_at_ms': None, 'next_query_at_ms': None}

    def tick(self):
        try:
            with _cycle_lease(self.output):
                return self._tick()
        except BlockingIOError:
            return self._report('RESIDENT_BUSY', 'RESIDENT_CYCLE_IN_PROGRESS')

    def _report(self, status, reason=None):
        return {'schema': SCHEMA, 'status': status, 'reason': reason, 'observed_at_ms': self.clock(),
            'query': None, 'resolution': None, 'reservation_sha256': None,
            **self.pacing,
            'new_order_submission_enabled': False, 'cash_reconciled': False, 'economic_authority': False}

    def _tick(self):
        at = self.clock()
        self.pacing = {'attempt_count': None, 'last_attempt_at_ms': None, 'next_query_at_ms': None}
        if type(at) is not int or at <= 0:
            return self._report('INPUTS_UNRESOLVED', 'RESIDENT_CLOCK_INVALID')
        try:
            attempts = _records(self.output/'attempts', count_limit=MAX_ATTEMPTS, byte_limit=MAX_SCAN_BYTES)
            for _, attempt in attempts:
                if (type(attempt) is not dict or set(attempt) != {'schema','at_ms','reservation_sha256','executor_boot_sha256'} or
                    attempt['schema'] != ATTEMPT_SCHEMA or type(attempt['at_ms']) is not int or attempt['at_ms'] <= 0 or
                    any(type(attempt[k]) is not str or _SHA.fullmatch(attempt[k]) is None for k in
                        ('reservation_sha256','executor_boot_sha256'))):
                    raise ValueError('RESIDENT_ATTEMPT_INVALID')
            last = max((v['at_ms'] for _, v in attempts), default=0)
        except (ValueError, OSError):
            return self._report('INPUTS_UNRESOLVED', 'RESIDENT_SCHEDULE_INVALID')
        if at < last:
            return self._report('INPUTS_UNRESOLVED', 'RESIDENT_CLOCK_REGRESSION')
        self.pacing = {'attempt_count': len(attempts), 'last_attempt_at_ms': last or None,
            'next_query_at_ms': last+QUERY_INTERVAL_MS if last else None}
        if len(attempts) >= MAX_ATTEMPTS:
            return self._report('INPUTS_UNRESOLVED', 'RESIDENT_ATTEMPT_BUDGET_EXHAUSTED')
        if last and at-last < QUERY_INTERVAL_MS:
            return self._report('WAITING_QUERY_INTERVAL')
        try:
            plan = discover(**self.stores, evidence_directory=self.evidence)
        except Exception:
            return self._report('INPUTS_UNRESOLVED', 'RESIDENT_ORIGINAL_STORES_INVALID')
        if plan['status'] != 'RECOVERY_REQUIRED':
            report = self._report(plan['status'], 'RESIDENT_ORIGINAL_PROOF_REQUIRED' if plan['pending'] else None)
            report['pending_client_order_ids'] = [c for row in plan['pending'] for c in row['client_order_ids']]
            return report
        # Oldest last-attempt first; a persistently unresolved batch cannot starve
        # another original reservation after an interrupted preparation.
        tried = {}
        for _, value in attempts:
            key = value['reservation_sha256']; tried[key] = max(tried.get(key, 0), value['at_ms'])
        item = min(plan['pending'], key=lambda row: tried.get(row['reservation_sha256'], 0))
        _retain(self.output/'attempts', {'schema': ATTEMPT_SCHEMA, 'at_ms': at,
            'reservation_sha256': item['reservation_sha256'], 'executor_boot_sha256': demo_authority.executor_boot_sha256()})
        self.pacing = {'attempt_count': len(attempts)+1, 'last_attempt_at_ms': at, 'next_query_at_ms': at+QUERY_INTERVAL_MS}
        report = self._report('QUERY_RECOVERY_REQUIRED')
        report['reservation_sha256'] = item['reservation_sha256']
        try:
            report['query'] = executor.query_reserved(item['prepared'], **self.stores)
            if report['query']['status'] == 'ORDER_SNAPSHOTS_OBSERVED':
                report['status'] = 'QUERY_OBSERVED_RECEIPTS_REQUIRED'
                first = item['first_send_claim_at_ms']
                if first is None:
                    report['reason'] = 'RESIDENT_SEND_CLAIM_MISSING'
                elif self.stream is None or self.event_store is None or self.source_commit is None:
                    report['reason'] = 'RESIDENT_RECEIPT_STORES_REQUIRED'
                elif not 2 <= at-1-first <= 150000 or first//86400000 != (at-1)//86400000:
                    report['reason'] = 'RESIDENT_RECEIPT_WINDOW_UNRESOLVED'
                else:
                    report['resolution'] = executor.reconcile_reserved(item['prepared'], **self.stores,
                        stream=self.stream, event_store=self.event_store, source_commit=self.source_commit,
                        start_ms=first, end_ms=at-1)
                    if report['resolution']['status'] == 'OMS_TERMINAL_PROJECTED':
                        report['status'] = 'OMS_TERMINAL_CASH_SEPARATE'
        except Exception:
            report['reason'] = 'RESIDENT_QUERY_OR_PROJECTION_STOPPED'
        report['observed_at_ms'] = self.clock()
        _retain(self.output/'reports', report)
        return report
