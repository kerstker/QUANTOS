"""Bind a reported-account replay to the executor's original intent and query.

This is a separate accounting recovery proof. It preserves private stream gaps
and manual order attribution; it cannot certify an automatic functional cycle.
The caller must establish protected capture and store custody.
"""
from decimal import Decimal

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import require
from live.execution.demo_prepared_execution import _load_proof
from services.research.demo_account_recovery import reconcile
from services.research.demo_user_stream import normalize_message
from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context
from quantos.contracts.binance_demo_clock import validate_query_clock, INTERIOR_DRIFT_MS, QUANTIZATION_MS

SCHEMA = 'DEMO_REPORTED_ACCOUNT_RESOLUTION_V1'


@ledger_decimal_context()
def prepare(prepared, *, dispatch, inputs, as_of_ms):
    """Validate exact immutable originals before producing an OMS recovery plan."""
    proof = _load_proof(prepared)
    snapshot = dispatch.evidence_snapshot()
    own = [r for r in snapshot['reservations'] if r['record_hash'] == prepared['reservation_sha256']]
    require(len(own) == 1 and own[0]['payload']['orders'] == proof['orders'] and
            own[0]['payload']['context'] == proof['context'], 'ACCOUNT_RESOLUTION_RESERVATION')
    require(inputs['opening'] == proof['origin'] and inputs['system_orders'] == proof['orders'] and
            inputs['opening']['account_ref_sha256'] == proof['context']['account_ref_sha256'],
            'ACCOUNT_RESOLUTION_ORIGINAL_INPUTS')
    risk = proof['risk']['input_snapshot']
    require(Decimal(inputs['adv_notional']) == Decimal(str(risk['market']['daily_volume_base'])) *
            Decimal(str(risk['market']['reference_price'])) and
            Decimal(inputs['participation_rate']) == Decimal(str(risk['native_limits']['max_participation_rate'])),
            'ACCOUNT_RESOLUTION_ORIGINAL_CAPACITY')
    report, ledger = reconcile(**inputs)
    require(report['status'] == 'REPORTED_ACCOUNT_MATCHED' and ledger is not None,
            'ACCOUNT_RESOLUTION_ACCOUNT_UNRESOLVED')
    # This initial continuation supports reported fills only. Other account
    # cash flows may be diagnosed by the research replay but cannot enter this
    # execution prefix under the existing fills-only risk contract.
    quote = inputs['opening']['symbol'][3:]
    require(not any(r['asset'] == quote and r['incomeType'] in ('TRANSFER', 'FUNDING_FEE')
                    for r in inputs['statements'][0]['income']), 'ACCOUNT_RESOLUTION_CASH_POLICY')
    available = max(inputs['closing']['completed_at_ms'], inputs['statements'][-1]['completed_at_ms'],
                    max(w['end_ms'] for w in inputs['windows']))
    require(type(as_of_ms) is int and available <= as_of_ms, 'ACCOUNT_RESOLUTION_FUTURE_INPUT')
    native = {}
    for window in inputs['windows']:
        for row in window['messages']:
            native.setdefault(row['response_sha256'], row)
    orders = []
    for fixed in proof['orders']:
        client = fixed['newClientOrderId']
        claims = [r for r in snapshot['wire'] if r['reservation_sha256'] == prepared['reservation_sha256']
                  and r['client_order_id'] == client and r['kind'] == 'SEND_CLAIM']
        require(len(claims) == 1, 'ACCOUNT_RESOLUTION_ORIGINAL_SEND')
        rows = sorted((r for r in native.values() if normalize_message(r['raw_utf8'])['payload']['o']['c'] == client),
                      key=lambda r: (r['received_at_ms'], r['response_sha256']))
        require(rows, 'ACCOUNT_RESOLUTION_ORIGINAL_NATIVE')
        terminal = normalize_message(rows[-1]['raw_utf8'])['payload']['o']
        require(terminal['X'] == 'FILLED' and str(terminal['i']) in report['system_order_ids'],
                'ACCOUNT_RESOLUTION_ORIGINAL_TERMINAL')
        queries = [r for r in snapshot['wire'] if r['kind'] == 'QUERY_RESULT' and
                   r['reservation_sha256'] == prepared['reservation_sha256'] and r['client_order_id'] == client]
        require(queries, 'ACCOUNT_RESOLUTION_ORIGINAL_QUERY')
        query = queries[-1]; payload = query['payload']
        require(payload['outcome']['status'] == 'FOUND', 'ACCOUNT_RESOLUTION_QUERY_UNKNOWN')
        q = payload['outcome']['order']
        clock = validate_query_clock(payload.get('clock'), query_requested_at_ms=payload['requested_at_ms'])
        upper = query['at_ms'] + clock['server_time_ms'] - clock['requested_at_ms'] + QUANTIZATION_MS + INTERIOR_DRIFT_MS
        require(q['orderId'] == str(terminal['i']) and q['status'] == terminal['X'] and
                Decimal(q['executedQty']) == Decimal(terminal['z']) and
                payload['requested_at_ms'] >= rows[-1]['received_at_ms'] and
                terminal['T'] <= q['updateTime'] <= upper and
                query['at_ms'] <= as_of_ms, 'ACCOUNT_RESOLUTION_QUERY_DIFFERENCE')
        quantity = Decimal(0); notional = Decimal(0); events = []
        for i, row in enumerate(rows, 1):
            o = normalize_message(row['raw_utf8'])['payload']['o']
            require(row['received_at_ms'] >= claims[0]['at_ms'], 'ACCOUNT_RESOLUTION_PRE_SEND_RECEIPT')
            if o['x'] == 'TRADE':
                quantity += Decimal(o['l']); notional += Decimal(o['l']) * Decimal(o['L'])
            require(quantity == Decimal(o['z']), 'ACCOUNT_RESOLUTION_CUMULATIVE_QUANTITY')
            events.append({'source_sequence': i, 'receipt_sha256': row['response_sha256'],
                           'status': o['X'], 'filled_quantity': canonical_decimal(quantity),
                           'average_price': canonical_decimal(notional / quantity) if quantity else '0',
                           'received_at_ms': row['received_at_ms']})
        orders.append({'client_order_id': client, 'venue_order_id': q['orderId'],
                       'terminal_status': q['status'], 'filled_quantity': canonical_decimal(quantity),
                       'query_native_status': q['status'], 'query_record_sha256': query['record_hash'], 'events': events})
        available = max(available, query['at_ms'])
    return {'schema': SCHEMA, 'status': 'READY_FOR_OMS_PROJECTION', 'issues': [],
            'reservation_sha256': prepared['reservation_sha256'], 'proposal_sha256': prepared['proposal_sha256'],
            'dispatch_evidence_sha256': _digest(snapshot), 'resolution_at_ms': available, 'orders': orders,
            'account_report': report, 'inputs_sha256': _digest(inputs), 'account_inputs': inputs,
            'stream_coverage_repaired': False, 'automatic_cycle_completed': False,
            'submission_enabled': False, 'source_verified': False, 'economic_authority': False,
            'cash_reconciled': True, 'oms_policy': 'FLOAT_ORDER_READ_MODEL_ONLY_NO_CASH_AUTHORITY_V1'}
