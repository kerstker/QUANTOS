"""Gold proposals retain original risk, mandate, signature and intent barriers."""
from dataclasses import replace
import json

import pytest

from live.execution import demo_pre_submit as prep, demo_prepared_execution as executor
from live.exchange import binance_demo_exact as transport
from tests.live.test_demo_pre_submit import setup as btc_setup, stores, signed, NOW
from tests.tools.test_gold_demo_execution_origin import capture


def setup(tmp_path, monkeypatch):
    private, envelope, values = btc_setup(tmp_path, monkeypatch)
    origin, _ = capture()
    values['origin'] = origin
    values['orders'][0]['symbol'] = 'XAUUSDT'
    risk = values['risk_inputs']
    ref = origin['account_ref_sha256']
    risk['account'].update(account_ref_sha256=ref, quote_asset='USDT')
    risk['market']['symbol'] = 'XAUUSDT'
    risk['native_limits'] = replace(risk['native_limits'],
        allowed_instruments=frozenset({'XAUUSDT'}), allowed_accounts=frozenset({ref}))
    values['mandate'] = replace(values['mandate'], allowed_instruments=frozenset({'XAUUSDT'}))
    envelope['schema'] = 'DEMO_GOLD_SIGNED_SUBMISSION_V1'
    envelope['payload']['schema'] = 'DEMO_GOLD_SUBMISSION_GRANT_V1'
    return private, envelope, values


def test_gold_approved_fixture_reaches_durable_intents_and_one_mock_ack(tmp_path, monkeypatch):
    private, envelope, values = setup(tmp_path, monkeypatch)
    ds, recovery, oms = stores(tmp_path)
    kwargs = dict(values, dispatch=ds, recovery=recovery, oms=oms)
    try:
        proposal = prep.proposal(**kwargs)
        assert proposal['risk']['schema'] == 'DEMO_GOLD_EXACT_BATCH_RISK_V1'
        assert proposal['risk']['status'] == 'RISK_CHECKS_PASSED'
        batch = prep.prepare(signed(private, envelope, proposal),
                             evidence_directory=tmp_path/'evidence', **kwargs)
        assert len(ds.snapshot()) == len(recovery.pending()) == len(oms.orders()) == 1
        monkeypatch.setattr(executor, '_require_state_custody', lambda *a, **k: None)
        monkeypatch.setattr(executor, 'now_ms', lambda: NOW+1)
        monkeypatch.setattr(transport, '_credentials', lambda _: ('synthetic-key', 'synthetic-secret'))
        calls = []

        def http(body, headers):
            assert ds.wire_snapshot()[0]['kind'] == 'SEND_CLAIM'
            calls.append(body)
            return 200, json.dumps({'symbol': 'XAUUSDT', 'orderId': 123,
                'clientOrderId': values['orders'][0]['newClientOrderId']}).encode()

        monkeypatch.setattr(transport, '_http_once', http)
        args = {k: v for k, v in kwargs.items() if k not in ('orders', 'origin', 'now_ms')}
        result = executor.execute(batch, **args)
        assert result['status'] == 'WIRE_ATTEMPTS_RECORDED' and len(calls) == 1
        assert oms.fills() == [] and len(recovery.pending()) == 1
        with pytest.raises(executor.DemoExecutionStopped):
            executor.execute(batch, **args)
        assert len(calls) == 1
    finally:
        for store in (ds, recovery, oms):
            store.close()


@pytest.mark.parametrize('damage', ['strategy', 'mandate', 'validation', 'risk', 'account'])
def test_gold_missing_approval_or_risk_stops_before_intents(tmp_path, monkeypatch, damage):
    _, _, values = setup(tmp_path, monkeypatch)
    risk = values['risk_inputs']
    if damage == 'strategy':
        risk['strategy'] = risk['strategy'].model_copy(update={'approved': False})
    elif damage == 'mandate':
        values['mandate'] = replace(values['mandate'], allowed_instruments=frozenset({'BTCUSDC'}))
    elif damage == 'validation':
        values['attestations'] = {}
    elif damage == 'risk':
        risk['native_limits'] = replace(risk['native_limits'], max_order_notional=100)
    else:
        risk['account']['account_ref_sha256'] = 'f'*64
    ds, recovery, oms = stores(tmp_path)
    try:
        with pytest.raises(prep.DemoPreparationRejected):
            prep.proposal(**values, dispatch=ds, recovery=recovery, oms=oms)
        assert ds.snapshot() == [] and recovery.latest_records() == () and oms.orders() == []
    finally:
        for store in (ds, recovery, oms):
            store.close()


def test_gold_risk_change_after_credentials_retains_unknown_without_http(tmp_path, monkeypatch):
    private, envelope, values = setup(tmp_path, monkeypatch)
    ds, recovery, oms = stores(tmp_path)
    kwargs = dict(values, dispatch=ds, recovery=recovery, oms=oms)
    try:
        proposal = prep.proposal(**kwargs)
        batch = prep.prepare(signed(private, envelope, proposal),
                             evidence_directory=tmp_path/'evidence', **kwargs)
        monkeypatch.setattr(executor, '_require_state_custody', lambda *a, **k: None)
        monkeypatch.setattr(executor, 'now_ms', lambda: NOW+1)
        calls = []

        def credentials(_):
            calls.append('credentials')
            values['risk_inputs']['account']['day_pnl'] = '-100'
            return 'synthetic-key', 'synthetic-secret'

        monkeypatch.setattr(transport, '_credentials', credentials)
        monkeypatch.setattr(transport, '_http_once', lambda *a: pytest.fail('risk changed before HTTP'))
        args = {k: v for k, v in kwargs.items() if k not in ('orders', 'origin', 'now_ms')}
        result = executor.execute(batch, **args)
        assert result['status'] == 'QUERY_RECOVERY_REQUIRED'
        assert result['orders'][0]['outcome']['status'] == 'UNKNOWN'
        assert calls == ['credentials']
        assert len(ds.snapshot()) == len(recovery.pending()) == 1
        assert oms.fills() == []
        with pytest.raises(executor.DemoExecutionStopped):
            executor.execute(batch, **args)
        assert calls == ['credentials']
    finally:
        for store in (ds, recovery, oms):
            store.close()


def test_gold_risk_requires_explicit_protocol_and_binds_it(tmp_path, monkeypatch):
    from live.execution.demo_batch_risk import screen, _digest
    from services.validation.demo_submission import GOLD_BATCH_SCHEMA
    _, _, values = setup(tmp_path, monkeypatch)
    assert screen(values['orders'], **values['risk_inputs'])['status'] == 'REJECTED'
    result = screen(values['orders'], schema=GOLD_BATCH_SCHEMA, **values['risk_inputs'])
    assert result['status'] == 'RISK_CHECKS_PASSED'
    assert result['risk_sha256'] == _digest(result['input_snapshot'])
    assert result['input_snapshot']['schema'] == 'DEMO_GOLD_EXACT_BATCH_RISK_V1'
