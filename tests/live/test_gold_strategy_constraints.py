import json

import pytest

from live.execution.demo_batch_risk import _digest
from live.execution import demo_request_inputs as builder, demo_issuer as issuer
from live.execution import demo_prepared_execution as executor
from live.execution.demo_execution_session import ExecutionSession
from live.exchange import binance_demo_exact as transport
from tests.live.test_gold_demo_request_session import case as case, request_inputs, NOW


def constrained(case):
    args = request_inputs(case)
    constraint = dict(schema='DEMO_GOLD_STRATEGY_CONSTRAINTS_V1',
        strategy_definition_sha256='a'*64, decision_sha256='b'*64,
        orders_sha256=_digest(args['orders']), not_before_ms=NOW, valid_until_ms=NOW+100)
    args['execution_constraints'] = constraint
    args['issuer_policy']['schema'] = 'DEMO_GOLD_STRATEGY_ISSUER_POLICY_V1'
    args['issuer_policy']['bindings']['execution_constraints_sha256'] = _digest(constraint)
    return args


@pytest.mark.parametrize('expire_during_credentials', [False, True])
def test_constraints_traverse_original_signature_and_final_send_checks(case, tmp_path, monkeypatch, expire_during_credentials):
    private, values, _, registry, policy, log = case
    args = constrained(case)
    built = builder.build(**args)
    session = ExecutionSession(**{k: values[k] for k in ('dispatch','recovery','oms')},
        evidence_directory=tmp_path/'bounded-session')
    handoff = session.propose(json.dumps(built['request']).encode(), now_ms=NOW+10)
    grant = issuer.issue_submission(handoff, policy=policy, registry=registry,
                                    key=private, store=log, now_ms=NOW+11)
    assert grant['envelope']['payload']['expires_at_ms'] == NOW+100
    calls = []
    clock = [NOW+12]
    monkeypatch.setattr(executor, 'now_ms', lambda: clock[0])
    monkeypatch.setattr(executor, '_require_state_custody', lambda *a, **k: None)
    def credentials(_):
        if expire_during_credentials: clock[0] = NOW+100
        return 'synthetic-key', 'synthetic-secret'
    monkeypatch.setattr(transport, '_credentials', credentials)
    monkeypatch.setattr(transport, '_http_once', lambda *a: (calls.append(1) or 200,
        json.dumps(dict(symbol='XAUUSDT', orderId=123,
                        clientOrderId=args['orders'][0]['newClientOrderId'])).encode()))
    result = session.submit(grant['proposal_sha256'], json.dumps(grant['envelope']).encode())
    if expire_during_credentials:
        assert calls == []
    else:
        assert calls == [1]
    assert result['cash_reconciled'] is False


@pytest.mark.parametrize('damage', ['legacy_policy','orders','deadline','definition','missing'])
def test_constraints_reject_downgrade_or_mismatch_before_order_intents(case, damage):
    args = constrained(case)
    if damage == 'legacy_policy':
        args['issuer_policy']['schema'] = 'DEMO_GOLD_ISSUER_POLICY_V1'
        args['issuer_policy']['bindings'].pop('execution_constraints_sha256')
    elif damage == 'orders': args['orders'][0]['quantity'] = '0.001'
    elif damage == 'deadline': args['execution_constraints']['valid_until_ms'] = NOW+10
    elif damage == 'definition': args['execution_constraints']['strategy_definition_sha256'] = 'c'*64
    else: args.pop('execution_constraints')
    with pytest.raises(ValueError): builder.build(**args)
    assert args['dispatch'].snapshot() == []


@pytest.mark.parametrize('bar', [False, True])
def test_reviewed_draft_requires_exact_independently_declared_scope(case, bar):
    from tools.gold_strategy_request import prepare, constraints_from_draft
    args = request_inputs(case)
    draft = dict(schema='GOLD_DEMO_STRATEGY_ENTRY_DRAFT_V1', status='STRATEGY_ENTRY_DRAFT',
        environment='BINANCE_USDM_DEMO', observed_at_ms=NOW, valid_until_ms=NOW+100,
        strategy_definition_sha256='a'*64, origin_sha256=_digest(args['origin']),
        orders=args.pop('orders'), submission_enabled=False, economic_authority=False, risk_status='NOT_RUN')
    if bar:
        draft.update(schema='GOLD_BAR_DEMO_ENTRY_DRAFT_V1', status='BAR_ENTRY_DRAFT',
            definition_hash=draft.pop('strategy_definition_sha256'), signal_valid_until_ms=NOW+100,
            pilot_control=dict(status='ACTIVE', configuration_sha256='e'*64, submission_enabled=False))
    draft['result_hash'] = _digest(draft)
    constraints = constraints_from_draft(draft, now_ms=NOW+10)
    with pytest.raises(ValueError): prepare(draft, **args)
    args['issuer_policy']['schema'] = 'DEMO_GOLD_STRATEGY_ISSUER_POLICY_V1'
    args['issuer_policy']['bindings']['execution_constraints_sha256'] = _digest(constraints)
    result = prepare(draft, **args)
    assert result['precheck_status'] == 'RISK_CHECKS_PASSED'
    assert result['request']['execution_constraints'] == constraints
    assert result['request']['schema'] == 'DEMO_GOLD_STRATEGY_SESSION_INPUT_V1'
    assert args['dispatch'].snapshot() == []


def test_strategy_entry_reserves_capacity_for_its_eventual_reduction(case):
    args = constrained(case)
    # Opening is below existing single-order and venue limits, but the original
    # conservative reduction ceiling would consume more than the whole budget.
    rules = next(row['data'] for row in args['origin']['reads'] if row['kind'] == 'rules')
    for group in (rules['terms']['filters'], rules['venue_filters']):
        for item in group:
            if item['filterType'] == 'PRICE_FILTER': item['maxPrice'] = '1000000000000'
    with pytest.raises(ValueError, match='DEMO_STRATEGY_EXIT_CAPACITY'):
        builder.build(**args)
    assert args['dispatch'].snapshot() == [] and args['oms'].orders() == []


@pytest.mark.parametrize('damage', ['pilot_missing', 'pilot_stopped', 'entry_as_reduction', 'extended_deadline'])
def test_bar_bridge_rejects_rehashed_incompatible_control(case, damage):
    from tools.gold_strategy_request import constraints_from_draft
    args = request_inputs(case)
    draft = dict(schema='GOLD_BAR_DEMO_ENTRY_DRAFT_V1', status='BAR_ENTRY_DRAFT',
        environment='BINANCE_USDM_DEMO', observed_at_ms=NOW, valid_until_ms=NOW+100,
        signal_valid_until_ms=NOW+100, definition_hash='a'*64, orders=args['orders'],
        submission_enabled=False, economic_authority=False, risk_status='NOT_RUN',
        pilot_control=dict(status='ACTIVE', configuration_sha256='e'*64, submission_enabled=False))
    if damage == 'pilot_missing':
        draft.pop('pilot_control')
    elif damage == 'pilot_stopped':
        draft['pilot_control']['status'] = 'STOPPED'
    elif damage == 'entry_as_reduction':
        draft['orders'][0]['reduceOnly'] = True
    else:
        draft['valid_until_ms'] += 1
    draft['result_hash'] = _digest(draft)
    with pytest.raises(ValueError):
        constraints_from_draft(draft, now_ms=NOW+10)
    assert args['dispatch'].snapshot() == []
