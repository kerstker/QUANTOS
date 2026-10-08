"""Gold original-ID cancellation uses issuer history and one DELETE claim."""
import json
from urllib.parse import parse_qs

import pytest

from live.execution import demo_pre_submit as prep, demo_prepared_execution as sender
from live.execution import demo_issuer as issuer, demo_cancellation as cancel
from live.execution.demo_batch_risk import _digest
from live.execution.demo_cancel_store import DemoCancelStore
from live.execution.demo_wire_protocol import outcome_for_query
from live.exchange import binance_demo_exact as transport
from services.validation.demo_submission import GOLD_BATCH_SCHEMA
from tests.live.test_gold_demo_issuer import case as case
from tests.live.test_demo_pre_submit import NOW


@pytest.mark.parametrize('mode', ['canceled', 'timeout', 'fill_race'])
def test_gold_cancel_original_order_once_without_oms_completion(case, tmp_path, monkeypatch, mode):
    private, kwargs, handoff, registry, policy, log = case
    grant = issuer.issue_submission(handoff, policy=policy, registry=registry, key=private, store=log, now_ms=NOW+1)
    prepared = prep.prepare(json.dumps(grant['envelope']).encode(), verification_now_ms=NOW+1,
                            evidence_directory=tmp_path/'evidence', **kwargs)
    monkeypatch.setattr(sender, '_require_state_custody', lambda *a, **k: None)
    monkeypatch.setattr(cancel, '_require_cancel_custody', lambda *a, **k: None)
    monkeypatch.setattr(sender, 'now_ms', lambda: NOW+2)
    monkeypatch.setattr(cancel, 'now_ms', lambda: NOW+5)
    monkeypatch.setattr(transport, '_credentials', lambda _: ('synthetic-key', 'synthetic-secret'))
    order = kwargs['orders'][0]
    monkeypatch.setattr(transport, '_http_once', lambda *a: (200, json.dumps({
        'symbol': 'XAUUSDT', 'clientOrderId': order['newClientOrderId'], 'orderId': 123}).encode()))
    stores = {k: kwargs[k] for k in ('dispatch', 'recovery', 'oms')}
    sender.execute(prepared, **stores, **{k: kwargs[k] for k in ('risk_inputs', 'mandate', 'attestations')})
    native = {'symbol': 'XAUUSDT', 'orderId': 123, 'clientOrderId': order['newClientOrderId'],
              'side': order['side'], 'origQty': order['quantity'], 'price': order['price'],
              'positionSide': 'BOTH', 'reduceOnly': False, 'timeInForce': order['timeInForce'],
              'type': 'LIMIT', 'status': 'NEW', 'executedQty': '0', 'time': NOW+2, 'updateTime': NOW+2}
    kwargs['dispatch'].record_query_result(reservation_sha256=prepared['reservation_sha256'],
        client_order_id=order['newClientOrderId'], requested_at_ms=NOW+3, now_ms=NOW+4,
        outcome=outcome_for_query(order, 200, json.dumps(native).encode(), schema=GOLD_BATCH_SCHEMA))
    with DemoCancelStore(tmp_path/'cancel.db', create=True) as cancel_store:
        args = dict(stores, cancel_store=cancel_store)
        proposal = cancel.proposal(prepared, **args)
        cancel_handoff = {'schema': 'DEMO_CANCELLATION_SESSION_PROPOSAL_V1',
            'proposal_sha256': _digest(proposal), 'proposal_path': '/isolated/cancel.json',
            'proposal': proposal, 'status': 'WAITING_INDEPENDENT_GRANT', 'submission_enabled': False}
        cancellation = issuer.issue_cancellation(cancel_handoff, policy=policy, registry=registry,
                                                key=private, store=log, now_ms=NOW+5)
        assert cancellation['envelope']['schema'] == 'DEMO_GOLD_SIGNED_CANCELLATION_V1'
        calls = []
        def delete(query, headers):
            assert [r['kind'] for r in cancel_store.snapshot()] == ['GRANT', 'CLAIM']
            params = parse_qs(query.decode())
            assert params['symbol'] == ['XAUUSDT'] and params['orderId'] == ['123']
            assert params['origClientOrderId'] == [order['newClientOrderId']]
            assert 'quantity' not in params
            calls.append(params)
            if mode == 'timeout':
                raise TimeoutError('synthetic timeout')
            return 200, json.dumps(dict(native, status='FILLED' if mode == 'fill_race' else 'CANCELED',
                                        executedQty=order['quantity'] if mode == 'fill_race' else '0')).encode()
        monkeypatch.setattr(transport, '_http_delete_once', delete)
        monkeypatch.setattr(transport, '_http_once', lambda *a: pytest.fail('new order during cancellation'))
        before = (kwargs['oms'].orders(), kwargs['recovery'].latest_records(), kwargs['dispatch'].evidence_snapshot())
        raw = json.dumps(cancellation['envelope']).encode()
        result = cancel.execute(prepared, raw, **args)
        assert result['status'] == 'ORIGINAL_ID_QUERY_RECOVERY_REQUIRED' and len(calls) == 1
        assert not result['cash_reconciled'] and kwargs['oms'].fills() == []
        assert before == (kwargs['oms'].orders(), kwargs['recovery'].latest_records(), kwargs['dispatch'].evidence_snapshot())
        outcome = result['orders'][0]['outcome']
        assert outcome['status'] == ('UNKNOWN' if mode == 'timeout' else 'ORDER_STATE_OBSERVED')
        if mode == 'fill_race':
            assert outcome['venue_status'] == 'FILLED'
        with pytest.raises(cancel.DemoCancellationStopped):
            cancel.execute(prepared, raw, **args)
        assert len(calls) == 1
