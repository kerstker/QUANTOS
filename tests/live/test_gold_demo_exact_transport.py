"""Explicit gold protocol preserves exact bytes and one-attempt Demo transport."""
import hashlib
import hmac
from urllib.parse import parse_qs

import pytest

from live.exchange import binance_demo_exact as transport
from services.validation.demo_submission import DemoSubmissionRejected
from tests.live.test_gold_demo_submission_verification import gold_material, BATCH
from tests.live.test_demo_submission_verification import NOW


def order():
    return gold_material()[2][0]


def test_gold_payload_is_exact_and_default_scope_rejects_before_credentials(monkeypatch):
    value = order()
    value.update(quantity='0.002000000000000001', price='4000.000000000000000001')
    body = transport.request_payload(value, NOW, schema=BATCH)
    fields = parse_qs(body.decode())
    assert fields['quantity'] == ['0.002000000000000001']
    assert fields['price'] == ['4000.000000000000000001']
    assert fields['symbol'] == ['XAUUSDT'] and fields['reduceOnly'] == ['false']
    expected = hashlib.sha256(b'POST\0https://demo-fapi.binance.com/fapi/v1/order\0'+body).hexdigest()
    assert transport.request_sha256(value, NOW, schema=BATCH) == expected
    monkeypatch.setattr(transport, '_credentials', lambda _: pytest.fail('credentials'))
    with pytest.raises(DemoSubmissionRejected, match='ORDER_SCOPE'):
        transport._submit_claimed(value, timestamp_ms=NOW, account_ref='a'*64, final_check=lambda: None)


@pytest.mark.parametrize('failure', [None, 'final_check', 'timeout'])
def test_gold_submit_checks_after_credentials_and_never_retries(monkeypatch, failure):
    seen = []
    def credentials(account):
        assert account == 'a'*64
        seen.append('credentials')
        return 'synthetic-key', 'synthetic-secret'

    def final_check():
        seen.append('final_check')
        if failure == 'final_check':
            raise ValueError('fixture risk changed')

    def http(body, headers):
        seen.append('http')
        raw, signature = body.rsplit(b'&signature=', 1)
        assert signature == hmac.new(b'synthetic-secret', raw, hashlib.sha256).hexdigest().encode()
        assert headers['X-MBX-APIKEY'] == 'synthetic-key'
        if failure == 'timeout':
            raise TimeoutError('fixture network timeout')
        return 200, b'{}'

    monkeypatch.setattr(transport, '_credentials', credentials)
    monkeypatch.setattr(transport, '_http_once', http)
    kwargs = dict(timestamp_ms=NOW, account_ref='a'*64, final_check=final_check, schema=BATCH)
    if failure:
        with pytest.raises(ValueError if failure == 'final_check' else TimeoutError):
            transport._submit_claimed(order(), **kwargs)
    else:
        assert transport._submit_claimed(order(), **kwargs) == (200, b'{}')
    assert seen == (['credentials', 'final_check'] if failure == 'final_check'
                    else ['credentials', 'final_check', 'http'])


def test_gold_query_uses_original_client_id_only(monkeypatch):
    value = order()
    calls = []
    monkeypatch.setattr(transport, '_credentials', lambda _: ('synthetic-key', 'synthetic-secret'))
    monkeypatch.setattr(transport, '_http_once', lambda *a: pytest.fail('POST during recovery'))

    def get(path, query, headers):
        calls.append((path, parse_qs(query.decode()), headers))
        return 200, b'{}'

    monkeypatch.setattr(transport, '_http_get_once', get)
    assert transport._query_reserved(value, timestamp_ms=NOW, account_ref='a'*64, schema=BATCH) == (200, b'{}')
    assert len(calls) == 1
    path, params, _ = calls[0]
    assert path == '/fapi/v1/order'
    assert params['origClientOrderId'] == [value['newClientOrderId']]
    assert params['symbol'] == ['XAUUSDT']
    assert set(params) == {'symbol', 'origClientOrderId', 'timestamp', 'recvWindow', 'signature'}


def test_gold_reserved_query_uses_bound_protocol_and_preserves_empty_oms(tmp_path, monkeypatch):
    import json
    from live.execution import demo_prepared_execution as executor
    from live.execution.demo_pre_submit import _retain
    from live.execution.demo_batch_risk import _digest
    from tests.live.test_demo_pre_submit import stores
    from tests.live.test_gold_demo_dispatch import configured, reserve

    orders, context, envelope = configured(monkeypatch)
    ds, recovery, oms = stores(tmp_path)
    # Isolated recovery after reservation, before OMS intent persistence.
    # Source custody is mocked; proof hash and reservation bindings are real.
    proof = {'schema': 'DEMO_PRE_SUBMISSION_PROPOSAL_V1', 'orders': orders, 'context': context,
             'origin': {'reads': [{'data': {'serverTime': NOW},
                                  'requested_at_ms': NOW, 'received_at_ms': NOW}]}}
    path = _retain(tmp_path/'evidence', proof)
    monkeypatch.setattr(executor, '_require_state_custody', lambda *a, **k: None)
    monkeypatch.setattr(executor, 'now_ms', lambda: NOW+10)
    monkeypatch.setattr(transport, '_credentials', lambda _: ('synthetic-key', 'synthetic-secret'))
    monkeypatch.setattr(transport, '_http_once', lambda *a: pytest.fail('POST during query'))
    calls = []

    def get(path, query, headers):
        calls.append(path)
        if path == '/fapi/v1/time':
            return 200, json.dumps({'serverTime': NOW+10}).encode()
        value = orders[0]
        return 200, json.dumps({'symbol': 'XAUUSDT', 'orderId': 123,
            'clientOrderId': value['newClientOrderId'], 'status': 'FILLED', 'side': value['side'],
            'origQty': value['quantity'], 'price': value['price'], 'positionSide': 'BOTH',
            'reduceOnly': False, 'timeInForce': 'GTX', 'type': 'LIMIT',
            'executedQty': value['quantity'], 'time': NOW+1, 'updateTime': NOW+2}).encode()

    monkeypatch.setattr(transport, '_http_get_once', get)
    try:
        row = reserve(ds, orders, context, envelope)
        prepared = {'schema': 'DEMO_PREPARED_BATCH_V1', 'status': 'INTENTS_PERSISTED',
                    'proposal_sha256': _digest(proof), 'proposal_path': str(path),
                    'reservation_sha256': row['record_hash'], 'orders': orders, 'context': context,
                    'submission_enabled': False, 'economic_authority': False}
        result = executor.query_reserved(prepared, dispatch=ds, recovery=recovery, oms=oms)
        assert result['status'] == 'ORDER_SNAPSHOTS_OBSERVED'
        assert calls == ['/fapi/v1/time', '/fapi/v1/order']
        assert ds.wire_snapshot()[0]['payload']['outcome']['status'] == 'FOUND'
        assert oms.orders() == [] and oms.fills() == [] and recovery.latest_records() == ()
    finally:
        for store in (ds, recovery, oms):
            store.close()
