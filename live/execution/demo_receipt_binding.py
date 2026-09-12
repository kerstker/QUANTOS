"""Bind observed venue IDs to original reserved Demo intents without dispatch.

The source stores stay append-only. This produces a new technical input binding;
it neither edits the pre-wire proposal nor resolves OMS/recovery or cash itself.
"""
from decimal import Decimal
from copy import deepcopy
import hashlib
from pathlib import Path
import re

from live.execution.demo_prepared_execution import _load_proof
from live.execution.demo_pre_submit import _digest, _retain
from services.execution.passive_queue import Action
from services.research.binance_order_receipts import OPTIONS_SCHEMA, normalize_order_receipts
from services.research.ai_factory_store import digest
from services.research.demo_execution_origin import assess as assess_origin
from services.research.demo_execution_origin import account_capture
from services.research.demo_origin_contract import contract_from_origin
from services.research.demo_opening_projection import bind_opening
from services.research.demo_execution_journal import DemoExecutionJournal, replay_report
from services.research.demo_user_stream import normalize_message
from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context

SCHEMA = 'DEMO_RESERVED_RECEIPT_BINDING_V1'
BINDING_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def bind_reserved_receipts(prepared, *, dispatch, stream, start_ms, end_ms):
    """Read-only, bounded binding; all monetary numbers remain exact strings.

    Local claim time and raw venue time must currently fit the existing common
    millisecond clock contract. No offset/latency is fabricated to make it pass.
    """
    if (type(start_ms) is not int or type(end_ms) is not int or not 0 <= start_ms < end_ms
            or not 2 <= end_ms-start_ms <= 150000 or start_ms//86400000 != end_ms//86400000):
        raise ValueError('DEMO_BINDING_WINDOW_INVALID')
    prepared = deepcopy(prepared)
    proof = _load_proof(prepared)
    snapshot = dispatch.evidence_snapshot()
    found = [r for r in snapshot['reservations'] if r['record_hash'] == prepared['reservation_sha256']]
    if len(found) != 1:
        raise ValueError('DEMO_BINDING_RESERVATION_MISSING')
    reservation = found[0]; context = proof['context']; planned = proof['orders']
    if (reservation['payload']['orders'] != planned or reservation['payload']['context'] != context or
            context['origin_sha256'] != _digest(proof['origin'])):
        raise ValueError('DEMO_BINDING_FROZEN_INPUT_MISMATCH')
    origin = assess_origin(proof['origin'], as_of_ms=reservation['payload']['reserved_at_ms'])
    if origin['status'] != 'OBSERVED_FLAT_QUOTE' or origin['account_ref_sha256'] != context['account_ref_sha256']:
        raise ValueError('DEMO_BINDING_ORIGIN_UNRESOLVED')
    exported = stream.export_order_messages(start_ms=start_ms, end_ms=end_ms)
    wire = [r for r in snapshot['wire'] if r['reservation_sha256'] == reservation['record_hash']]
    result = {'schema': SCHEMA, 'environment': 'BINANCE_USDM_DEMO', 'scope': 'TECHNICAL_VALIDATION_ONLY',
        'status': 'UNRESOLVED', 'issues': [], 'reservation_sha256': reservation['record_hash'],
        'proposal_sha256': prepared['proposal_sha256'], 'account_ref_sha256': context['account_ref_sha256'],
        'origin_sha256': context['origin_sha256'], 'binding_source_sha256': BINDING_SOURCE_SHA256,
        'dispatch_evidence_sha256': digest(snapshot), 'stream_export_sha256': digest(exported),
        'wire_tail': snapshot['wire'][-1]['record_hash'] if snapshot['wire'] else None,
        'stream_integrity': exported['integrity'], 'window': {'start_ms':start_ms,'end_ms':end_ms},
        'symbol': origin['symbol'], 'quote_asset': origin['quote_asset'], 'orders': [],
        'messages_count': len(exported['messages']), 'bound_messages_count': 0,
        'excluded_other_quote_messages': 0, 'excluded_message_sha256': [],
        'stream_gaps': exported['gaps'], 'preceding_stream_gap': exported['preceding_gap'],
        'native': None, 'actions': [], 'trades_count': 0, 'filled_quantity': None,
        'source_verified': False, 'coverage_verified': False, 'submission_enabled': False,
        'authoritative': False, 'strategy_pnl': None,
        'clock_policy': 'RAW_COMMON_MILLISECOND_DOMAIN_ONLY',
        'limitations': ['RECEIPT_BINDING_IS_NOT_SUBMISSION_AUTHORITY','STREAM_COVERAGE_UNVERIFIED',
                        'NO_OMS_TERMINAL_OR_CASH_MUTATION','NO_CLOCK_OFFSET_OR_LATENCY_INFERRED']}
    issues = result['issues']
    if exported['account_ref_sha256'] != context['account_ref_sha256']:
        issues.append('STREAM_ACCOUNT_NAMESPACE_MISMATCH')
    if exported['gaps']:
        issues.append('STREAM_GAPS_IN_WINDOW')
    by_client = {o['newClientOrderId']: o for o in planned}
    ids = {client: set() for client in by_client}
    claims = {r['client_order_id']: r for r in wire if r['kind']=='SEND_CLAIM'}
    sources = {client: [] for client in by_client}
    for record in wire:
        if record['kind'] not in ('SEND_RESULT', 'QUERY_RESULT'):
            continue
        outcome = record['payload']['outcome']
        if outcome['status'] not in ('ACKNOWLEDGED', 'FOUND'):
            continue
        client = record['client_order_id']
        ids[client].add(outcome['order']['orderId'])
        sources[client].append({'kind': record['kind'], 'record_sha256': record['record_hash']})
    selected = []
    for record in exported['messages']:
        message = normalize_message(record['raw_utf8']); order = message['payload']['o']
        if message['response_sha256'] != record['response_sha256']:
            raise ValueError('DEMO_BINDING_STREAM_DIGEST')
        client = order['c']
        if client not in by_client:
            if order['s'].endswith(origin['quote_asset']):
                issues.append('FOREIGN_SAME_QUOTE_ORDER')
            else:
                result['excluded_other_quote_messages'] += 1
                result['excluded_message_sha256'].append(record['response_sha256'])
            continue
        if order['s'] != by_client[client]['symbol']:
            issues.append('STREAM_ORDER_SYMBOL_MISMATCH')
        ids[client].add(str(order['i']))
        sources[client].append({'kind':'PRIVATE_STREAM','record_sha256':record['response_sha256']})
        selected.append(record)
    bindings = []
    for order in planned:
        client = order['newClientOrderId']; claim = claims.get(client)
        venue = next(iter(ids[client])) if len(ids[client])==1 else None
        if len(ids[client]) > 1: issues.append('VENUE_ORDER_ID_CONFLICT')
        if venue is None: issues.append('VENUE_ORDER_ID_UNRESOLVED')
        if claim is None:
            issues.append('PREWIRE_SEND_CLAIM_MISSING')
        elif not start_ms <= claim['at_ms'] <= end_ms:
            issues.append('CLAIM_OUTSIDE_RECEIPT_WINDOW')
        if not any(s['kind']=='PRIVATE_STREAM' for s in sources[client]):
            issues.append('PRIVATE_RECEIPTS_MISSING')
        name = 'demo-'+digest([reservation['record_hash'],client])[:24]
        result['orders'].append({'order_id':name,'client_order_id':client,'venue_order_id':venue,
            'claim_sha256':claim['record_hash'] if claim else None,
            'claimed_at_ms':claim['at_ms'] if claim else None, 'sources':sources[client]})
        if claim:
            result['actions'].append({'timestamp':claim['at_ms'],'kind':'ORDER','order_id':name,
                'side':order['side'],'quantity':order['quantity'],'price':order['price']})
        if venue:
            bindings.append({'order_id':name,'venue_order_id':venue,'client_order_id':client,
                'time_in_force':order['timeInForce'],'reduce_only':order['reduceOnly']})
    if len({b['venue_order_id'] for b in bindings}) != len(bindings):
        issues.append('VENUE_ORDER_ID_REUSED')
    result['bound_messages_count'] = len(selected)
    if not issues:
        native = {'schema': OPTIONS_SCHEMA, 'symbol':origin['symbol'], 'account_ref_sha256':context['account_ref_sha256'],
            'action_hash':digest(result['actions']), 'provenance':'IMPORTED_UNVERIFIED',
            'coverage':{'start_ms':start_ms,'end_ms':end_ms,'gaps':[]},'bindings':bindings,'messages':selected}
        try:
            actions = tuple(Action(**(a | {'quantity':Decimal(a['quantity']),'price':Decimal(a['price'])})) for a in result['actions'])
            reduce_only = frozenset(b['order_id'] for b in bindings if b['reduce_only'])
            _, evidence = normalize_order_receipts(native, actions, origin['symbol'], native['action_hash'], reduce_only_ids=reduce_only)
            result.update(status='BOUND_RECEIPT_INPUTS',native=native,trades_count=evidence['trades_count'],
                          filled_quantity=evidence['reported_filled_quantity'])
        except ValueError:
            issues.append('RECEIPT_INTENT_OR_CLOCK_CONFLICT')
    result['issues'] = sorted(set(issues))
    return result


@ledger_decimal_context()
def import_reserved_receipts(prepared, *, dispatch, stream, start_ms, end_ms, source_commit, output_directory):
    """Retain the derived binding and import its raw facts into the old journal.

    This isolated technical projection never mutates the executor OMS/recovery,
    grants, original proposal or private stream. Repeated imports deduplicate in
    the same manifest-addressed journal; unsupported terms keep their evidence.
    """
    if type(source_commit) is not str or re.fullmatch('[a-f0-9]{40}',source_commit) is None:
        raise ValueError('DEMO_RECEIPT_SOURCE_COMMIT_REQUIRED')
    prepared = deepcopy(prepared)
    binding = bind_reserved_receipts(prepared, dispatch=dispatch, stream=stream, start_ms=start_ms, end_ms=end_ms)
    directory = Path(output_directory)
    binding_path = _retain(directory/'bindings', binding)
    result = {'schema':'DEMO_RESERVED_RECEIPT_IMPORT_V1','status':'UNRESOLVED','issues':list(binding['issues']),
        'source_commit':source_commit,'binding_sha256':_digest(binding),'binding_path':str(binding_path),
        'reservation_sha256':binding['reservation_sha256'],'proposal_sha256':binding['proposal_sha256'],
        'journal_path':None,'manifest_hash':None,'contract_projection':None,'capacity_projection':None,'replay':None,
        'submission_enabled':False,'authoritative':False,'source_verified':False,'strategy_pnl':None}
    if binding['status'] != 'BOUND_RECEIPT_INPUTS':
        _retain(directory/'imports',result)
        return result
    try:
        proof = _load_proof(prepared)
        raw_spec, contract_facts = contract_from_origin(proof['origin'])
        result['contract_projection'] = contract_facts
        risk = proof['risk']['input_snapshot']
        if (_digest(risk) != proof['context']['risk_sha256'] or risk['orders'] != proof['orders'] or
                risk['market']['symbol'] != binding['symbol']):
            raise ValueError('DEMO_RECEIPT_CAPACITY_BINDING')
        volume = Decimal(risk['market']['daily_volume_base'])
        reference = Decimal(risk['market']['reference_price'])
        participation = Decimal(str(risk['native_limits']['max_participation_rate']))
        if not volume.is_finite() or volume <= 0 or not reference.is_finite() or reference <= 0:
            raise ValueError('DEMO_RECEIPT_CAPACITY_UNAVAILABLE')
        adv_notional = canonical_decimal(volume*reference)
        result['capacity_projection'] = {'policy':'DAILY_BASE_VOLUME_AT_FROZEN_REFERENCE_V1',
            'risk_sha256':proof['context']['risk_sha256'],'daily_volume_base':canonical_decimal(volume),
            'reference_price':canonical_decimal(reference),'adv_notional':adv_notional,
            'participation_rate':canonical_decimal(participation),'actual_quote_volume_verified':False}
        native = deepcopy(binding['native']); messages = native.pop('messages')
        manifest = {'schema':'DEMO_EXECUTION_REPLAY_V1','environment':'BINANCE_USDM_DEMO','source_commit':source_commit,
            'initial_collateral':origin_wallet(proof['origin']),'initial_position':'0','contract':raw_spec,
            'adv_notional':adv_notional,'participation_rate':canonical_decimal(participation),
            'actions':deepcopy(binding['actions']),'native':native,'expected_end_position':None}
        manifest = bind_opening(manifest,account_capture(proof['origin']))
        manifest_hash = digest(manifest)
        path = directory/'journals'/(manifest_hash+'.sqlite3')
        with DemoExecutionJournal(path,manifest=manifest) as journal:
            journal.append(messages)
            report = replay_report(journal)
        result.update(status='IMPORTED_TECHNICAL_RECEIPTS',journal_path=str(path),manifest_hash=manifest_hash,replay=report)
    except (ValueError, TypeError, ArithmeticError, RuntimeError) as exc:
        result['issues'].append('RECEIPT_IMPORT_'+type(exc).__name__.upper())
    _retain(directory/'imports',result)
    return result


def origin_wallet(origin):
    quote = origin['symbol'][3:]
    return next(a['walletBalance'] for r in origin['reads'] if r['kind']=='account_after'
                for a in r['data']['assets'] if a['asset']==quote)
