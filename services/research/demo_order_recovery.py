"""Read-only comparison of fixed Demo receipts and separate REST observations."""
import json
import os
from decimal import Decimal
from pathlib import Path
import tempfile

from quantos.contracts.binance_demo import MAX_AGE_MS
from quantos.contracts.binance_demo_orders import QueryPlan, QueryCapture, fixed_order_bindings, query_binding_hash
from quantos.contracts.order import OrderState, TERMINAL_STATES, assert_snapshot_transition
from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.ai_factory_store import digest
from services.research.demo_execution_journal import replay_report

MAX_CAPTURE_BYTES = 128_000
_STATES = {'ACK': OrderState.SUBMITTED, 'NEW': OrderState.SUBMITTED,
           'PARTIALLY_FILLED': OrderState.PARTIALLY_FILLED, 'FILLED': OrderState.FILLED,
           'CANCELED': OrderState.CANCELLED, 'CANCELLED': OrderState.CANCELLED,
           'EXPIRED': OrderState.EXPIRED, 'EXPIRED_IN_MATCH': OrderState.EXPIRED}


def build_query_plan(journal):
    report = replay_report(journal)
    manifest = journal.manifest
    bindings = {b['order_id']: b for b in manifest['native']['bindings']}
    if set(bindings) != {a['order_id'] for a in manifest['actions']}:
        raise ValueError('ALL_FIXED_ORDERS_REQUIRE_BINDINGS')
    return QueryPlan(manifest_hash=report['manifest_hash'], journal_tail=report['journal_tail'],
        account_ref_sha256=manifest['native']['account_ref_sha256'], symbol=report['symbol'],
        receipt_observed_at_ms=report['observed_at_ms'],
        orders=fixed_order_bindings(manifest['actions'],manifest['native']['bindings']))


def _read(path, limit):
    with Path(path).open('rb') as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('CAPTURE_TOO_LARGE')
    return load_json_without_duplicate_keys(raw.decode())


def load_capture(directory):
    directory = Path(directory)
    pointer = _read(directory / 'latest.json', 256)
    if not isinstance(pointer, dict) or set(pointer) != {'sha256'}:
        raise ValueError('CAPTURE_POINTER_INVALID')
    identity = pointer['sha256']
    if not isinstance(identity, str) or len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity):
        raise ValueError('CAPTURE_POINTER_INVALID')
    body = _read(directory / (identity + '.json'), MAX_CAPTURE_BYTES)
    capture = QueryCapture.model_validate(body)
    if digest(capture.model_dump()) != identity:
        raise ValueError('CAPTURE_HASH_MISMATCH')
    return capture


def save_capture(directory, capture):
    """Keep immutable attempts; publish only a content-addressed latest pointer."""
    capture = QueryCapture.model_validate(capture.model_dump())
    body = capture.model_dump()
    identity = digest(body)
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    if len(encoded) > MAX_CAPTURE_BYTES:
        raise ValueError('CAPTURE_TOO_LARGE')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (identity + '.json')
    # A crash can leave an unpublished file; readers only follow the pointer.
    try:
        with destination.open('xb') as out:
            out.write(encoded); out.flush(); os.fsync(out.fileno())
    except FileExistsError:
        if digest(_read(destination, MAX_CAPTURE_BYTES)) != identity:
            raise ValueError('CAPTURE_HISTORY_CONFLICT')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=directory, delete=False) as out:
            temporary = Path(out.name)
            json.dump({'sha256':identity},out);out.flush();os.fsync(out.fileno())
        os.replace(temporary, directory/'latest.json')
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    return identity


@ledger_decimal_context()
def compare_orders(receipts, capture, *, as_of_ms):
    capture = QueryCapture.model_validate(capture.model_dump())
    plan = capture.plan
    result = {'schema':'DEMO_ORDER_RECOVERY_V1', 'status':'ACTION_REQUIRED',
              'scope':'TECHNICAL_VALIDATION_ONLY', 'source_verified':False, 'new_command_allowed':False,
              'capture_hash':digest(capture.model_dump()), 'manifest_hash':plan.manifest_hash,
              'journal_tail':plan.journal_tail, 'provenance':capture.provenance,
              'observed_at_ms':capture.started_at_ms, 'orders':[]}
    if capture.completed_at_ms > as_of_ms:
        return result | {'status':'DATA_BLOCKED'}
    if receipts is None:
        return result | {'status':'NO_JOURNAL'}
    if (plan.manifest_hash != receipts['manifest_hash'] or plan.symbol != receipts['symbol']
        or query_binding_hash([o.model_dump() for o in plan.orders],plan.account_ref_sha256) != receipts['query_binding_hash']):
        return result | {'status':'IDENTITY_MISMATCH'}
    if plan.journal_tail != receipts['journal_tail']:
        return result | {'status':'JOURNAL_ADVANCED'}
    if receipts['observed_at_ms'] is not None and receipts['observed_at_ms'] > capture.started_at_ms:
        return result | {'status':'DATA_BLOCKED'}
    if as_of_ms-capture.started_at_ms > MAX_AGE_MS:
        return result | {'status':'STALE'}
    by_id = {o['order_id']:o for o in receipts['orders']}
    for fixed, query in zip(plan.orders, capture.queries):
        local = by_id.get(fixed.order_id)
        obs = query.observation
        status, gap = 'QUERY_UNRESOLVED', None
        if obs is not None:
            if local is None or local['last_event_at_ms'] is None:
                status = 'RECEIPTS_MISSING'
            elif local['side'] != fixed.side or Decimal(local['quantity']) != Decimal(fixed.quantity):
                status = 'IDENTITY_MISMATCH'
            else:
                old, new = _STATES[local['state']], _STATES[obs.status]
                gap = canonical_decimal(Decimal(obs.executed_quantity)-Decimal(local['filled_quantity']))
                if obs.updated_at_ms < local['last_event_at_ms']:
                    status = 'OLDER_VENUE_OBSERVATION'
                elif Decimal(gap) < 0:
                    status = 'QUANTITY_REGRESSION'
                elif old in TERMINAL_STATES and old != new:
                    status = 'TERMINAL_CONFLICT'
                else:
                    try:
                        assert_snapshot_transition(old, new)
                        status = 'BACKFILL_REQUIRED' if Decimal(gap)>0 else 'STATE_UPDATE_REQUIRED' if old!=new else 'MATCHED_OBSERVATIONS'
                    except ValueError:
                        status = 'STATE_CONFLICT'
        result['orders'].append({'order_id':fixed.order_id, 'venue_order_id':fixed.venue_order_id,
            'receipt_state':local['state'] if local else 'UNKNOWN', 'query_state':obs.status if obs else None,
            'receipt_quantity':local['filled_quantity'] if local and local['last_event_at_ms'] is not None else None,
            'query_quantity':obs.executed_quantity if obs else None, 'quantity_gap':gap,
            'status':status, 'reason':query.reason, 'received_at_ms':query.received_at_ms})
    if all(o['status']=='MATCHED_OBSERVATIONS' for o in result['orders']):
        result['status']='MATCHED_OBSERVATIONS'
    return result
