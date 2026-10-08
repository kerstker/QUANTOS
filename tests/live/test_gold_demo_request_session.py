"""Gold observed inputs traverse the original request, session and issuer flow."""
import json

import pytest

from live.execution import demo_request_inputs as builder, demo_issuer as issuer
from live.execution import demo_prepared_execution as executor
from live.execution.demo_execution_session import ExecutionSession, SessionRejected
from live.exchange import binance_demo_exact as transport
from tests.live.test_gold_demo_issuer import case as case
from tests.live.test_demo_request_inputs import profile
from tests.tools.test_gold_demo_market_inputs import capture, NOW


def request_inputs(case):
    _, values, _, _, policy, _ = case
    market, _ = capture()
    declared = profile(values, market)
    declared['symbol'] = 'XAUUSDT'
    return dict(orders=values['orders'], origin=values['origin'], market_inputs=market,
                profile=declared, issuer_policy=policy, kill_state=values['risk_inputs']['kill_switch'].state,
                **{k: values[k] for k in ('dispatch', 'recovery', 'oms')}, now_ms=NOW+10)


def test_gold_observation_request_to_session_issuer_and_mock_execution(case, tmp_path, monkeypatch):
    private, values, _, registry, policy, log = case
    inputs = request_inputs(case)
    built = builder.build(**inputs)
    assert built['precheck_status'] == 'RISK_CHECKS_PASSED'
    assert values['dispatch'].snapshot() == [] and values['oms'].orders() == []
    session = ExecutionSession(**{k: values[k] for k in ('dispatch', 'recovery', 'oms')},
                               evidence_directory=tmp_path/'session-evidence')
    handoff = session.propose(json.dumps(built['request']).encode(), now_ms=NOW+10)
    grant = issuer.issue_submission(handoff, policy=policy, registry=registry,
                                   key=private, store=log, now_ms=NOW+11)
    calls = []
    monkeypatch.setattr(executor, '_require_state_custody', lambda *a, **k: None)
    monkeypatch.setattr(executor, 'now_ms', lambda: NOW+12)
    monkeypatch.setattr(transport, '_credentials', lambda _: ('synthetic-key', 'synthetic-secret'))
    monkeypatch.setattr(transport, '_http_once', lambda *a: (calls.append(1) or 200,
        json.dumps({'symbol': 'XAUUSDT', 'orderId': 123,
                    'clientOrderId': values['orders'][0]['newClientOrderId']}).encode()))
    result = session.submit(grant['proposal_sha256'], json.dumps(grant['envelope']).encode())
    assert result['execution']['status'] == 'WIRE_ATTEMPTS_RECORDED' and calls == [1]
    assert not result['cash_reconciled'] and values['oms'].fills() == []
    with pytest.raises(SessionRejected):
        session.submit(grant['proposal_sha256'], json.dumps(grant['envelope']).encode())
    assert calls == [1]


@pytest.mark.parametrize('damage', ['legacy_policy', 'limits', 'source', 'expired', 'stale'])
def test_gold_request_rejects_changed_policy_or_observation_before_intents(case, damage):
    inputs = request_inputs(case)
    if damage == 'legacy_policy':
        inputs['issuer_policy']['schema'] = 'DEMO_ISSUER_POLICY_V1'
    elif damage == 'limits':
        inputs['profile']['native_limits']['max_order_notional'] = 100000
    elif damage == 'source':
        inputs['profile']['market_source_sha256'] = 'f'*64
    elif damage == 'expired':
        inputs['issuer_policy']['valid_until_ms'] = NOW
    else:
        inputs['now_ms'] += 30001
    with pytest.raises(ValueError):
        builder.build(**inputs)
    assert inputs['dispatch'].snapshot() == [] and inputs['oms'].orders() == []


def test_gold_task_validation_binds_policy_profile_orders_and_release(case):
    from live.execution.demo_batch_risk import _digest
    inputs = request_inputs(case)
    policy = inputs['issuer_policy']
    task = {'schema': 'DEMO_REQUEST_TASK_V1', 'source_commit': policy['executor_release_commit'],
            'valid_from_ms': NOW, 'valid_until_ms': NOW+1000,
            'issuer_policy_sha256': _digest(policy), 'profile': inputs['profile'],
            'orders': inputs['orders'], 'kill_switch': inputs['kill_state'].model_dump(mode='json')}
    assert builder.task_check(task, policy=policy, source_commit=policy['executor_release_commit'],
                              now_ms=NOW+10) == task
    with pytest.raises(ValueError):
        builder.task_check(task, policy=policy, source_commit='f'*40, now_ms=NOW+10)
    task['orders'][0]['symbol'] = 'BTCUSDT'
    with pytest.raises(ValueError):
        builder.task_check(task, policy=policy, source_commit=policy['executor_release_commit'], now_ms=NOW+10)
