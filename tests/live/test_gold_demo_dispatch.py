"""Gold grant consumption and wire recovery use one original durable journal."""
import json

import pytest

from live.execution import demo_authority as boundary, demo_wire_protocol as wire
from live.execution.demo_dispatch_store import DemoDispatchStore, DemoDispatchConflict
from services.validation import demo_submission as auth
from tests.live.test_gold_demo_submission_verification import gold_material, BATCH
from tests.live.test_demo_submission_verification import encoded, NOW


def configured(monkeypatch):
    private, registry, orders, context, envelope = gold_material()
    context['executor_boot_sha256'] = boundary.executor_boot_sha256()
    envelope['signature_base64url'] = encoded(private.sign(auth.signing_payload(envelope)))
    monkeypatch.setattr(boundary, '_read_root_owned_json', lambda _: json.dumps(registry).encode())
    monkeypatch.setattr(boundary.os, 'geteuid', lambda: 2001)
    return orders, context, envelope


def reserve(store, orders, context, envelope):
    return store.reserve(json.dumps(envelope).encode(), expected_context=context, orders=orders, now_ms=NOW)


def test_gold_reservation_reopens_and_never_refunds_approval(tmp_path, monkeypatch):
    orders, context, envelope = configured(monkeypatch)
    path = tmp_path/'dispatch.db'
    with DemoDispatchStore(path, create=True) as store:
        row = reserve(store, orders, context, envelope)
    with DemoDispatchStore(path) as store:
        assert store.snapshot() == [row]
        with pytest.raises(DemoDispatchConflict):
            reserve(store, orders, context, envelope)
        assert store.snapshot() == [row]


def test_gold_unknown_send_then_query_survives_restart_without_resend(tmp_path, monkeypatch):
    orders, context, envelope = configured(monkeypatch)
    order = orders[0]
    path = tmp_path/'dispatch.db'
    with DemoDispatchStore(path, create=True) as store:
        row = reserve(store, orders, context, envelope)
        claim = store.claim_send(reservation_sha256=row['record_hash'],
            client_order_id=order['newClientOrderId'], request_sha256='d'*64,
            preflight_sha256='e'*64, now_ms=NOW+1)
        unknown = wire.outcome_for_response(order, None, None, schema=BATCH)
        store.record_wire_result(claim_sha256=claim['record_hash'], outcome=unknown, now_ms=NOW+2)
    with DemoDispatchStore(path) as store:
        with pytest.raises(DemoDispatchConflict):
            store.claim_send(reservation_sha256=row['record_hash'],
                client_order_id=order['newClientOrderId'], request_sha256='d'*64,
                preflight_sha256='e'*64, now_ms=NOW+3)
        body = {'symbol': 'XAUUSDT', 'orderId': 123, 'clientOrderId': order['newClientOrderId'],
                'status': 'FILLED', 'side': order['side'], 'origQty': order['quantity'],
                'price': order['price'], 'positionSide': 'BOTH', 'reduceOnly': False,
                'timeInForce': 'GTX', 'type': 'LIMIT', 'executedQty': order['quantity'],
                'time': NOW+1, 'updateTime': NOW+3}
        found = wire.outcome_for_query(order, 200, json.dumps(body).encode(), schema=BATCH)
        assert found['status'] == 'FOUND'
        record = store.record_query_result(reservation_sha256=row['record_hash'],
            client_order_id=order['newClientOrderId'], requested_at_ms=NOW+4, outcome=found, now_ms=NOW+5)
        assert store.record_query_result(reservation_sha256=row['record_hash'],
            client_order_id=order['newClientOrderId'], requested_at_ms=NOW+4,
            outcome=found, now_ms=NOW+5) == record
        snapshot = store.wire_snapshot()
        assert len(snapshot) == 3 and snapshot[1]['payload']['outcome']['status'] == 'UNKNOWN'
    with DemoDispatchStore(path) as store:
        assert store.wire_snapshot() == snapshot


def test_gold_ack_identity_requires_explicit_protocol_and_exact_order():
    _, _, orders, _, _ = gold_material()
    order = orders[0]
    body = {'symbol': 'XAUUSDT', 'clientOrderId': order['newClientOrderId'], 'orderId': 123}
    response = json.dumps(body).encode()
    assert wire.outcome_for_response(order, 200, response, schema=BATCH)['status'] == 'ACKNOWLEDGED'
    assert wire.outcome_for_response(order, 200, response)['status'] == 'UNKNOWN'
    body['symbol'] = 'BTCUSDT'
    assert wire.outcome_for_response(order, 200, json.dumps(body).encode(), schema=BATCH)['reason'] == 'IDENTITY_MISMATCH'


def test_two_writers_cannot_consume_same_gold_approval(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    orders, context, envelope = configured(monkeypatch)
    path = tmp_path/'dispatch.db'
    with DemoDispatchStore(path, create=True):
        pass

    def attempt(_):
        with DemoDispatchStore(path) as store:
            try:
                reserve(store, orders, context, envelope)
                return 'RESERVED'
            except DemoDispatchConflict:
                return 'REJECTED'

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ['REJECTED', 'RESERVED']
    with DemoDispatchStore(path) as store:
        assert len(store.snapshot()) == 1


def test_invalid_gold_signature_cannot_reserve(tmp_path, monkeypatch):
    orders, context, envelope = configured(monkeypatch)
    envelope['signature_base64url'] = encoded(bytes(64))
    with DemoDispatchStore(tmp_path/'dispatch.db', create=True) as store:
        with pytest.raises(auth.DemoSubmissionRejected, match='SIGNATURE_INVALID'):
            reserve(store, orders, context, envelope)
        assert store.snapshot() == []
