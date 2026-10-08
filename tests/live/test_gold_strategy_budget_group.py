from copy import deepcopy
from decimal import Decimal
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from live.execution import demo_issuer as issuer, demo_request_inputs as builder
from live.execution.demo_execution_session import ExecutionSession
from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_store import IssuerStore
from tests.live.test_gold_strategy_constraints import case, constrained, NOW


def grouped(case, *, limit='grants'):
    args = constrained(case)
    policy = args['issuer_policy']
    policy['schema'] = 'DEMO_GOLD_STRATEGY_BUDGET_POLICY_V1'
    notional = sum(Decimal(o['quantity'])*Decimal(o['price']) for o in args['orders'])
    policy['budget_group'] = dict(group_id='synthetic-one-strategy-budget',
        strategy_definition_sha256='a'*64, valid_from_ms=policy['valid_from_ms'],
        valid_until_ms=policy['valid_until_ms'], max_grants=1 if limit == 'grants' else 10,
        max_orders=len(args['orders']) if limit == 'orders' else 20,
        max_total_notional=str(notional if limit == 'notional' else notional*20),
        max_cancel_grants=10, max_cancel_orders=20)
    return args


def handoff(args, tmp_path, index):
    value = dict(args, issuer_policy=deepcopy(args['issuer_policy']), orders=deepcopy(args['orders']),
                 execution_constraints=deepcopy(args['execution_constraints']))
    for offset, order in enumerate(value['orders']): order['newClientOrderId'] = 'QD'+f'{index*10+offset:032x}'
    value['execution_constraints'].update(orders_sha256=_digest(value['orders']), decision_sha256=f'{index:064x}')
    value['issuer_policy']['policy_id'] = 'synthetic-decision-'+str(index)
    value['issuer_policy']['bindings']['execution_constraints_sha256'] = _digest(value['execution_constraints'])
    built = builder.build(**value)
    session = ExecutionSession(**{k:value[k] for k in ('dispatch','recovery','oms')}, evidence_directory=tmp_path/'evidence')
    return session.propose(json.dumps(built['request']).encode(), now_ms=NOW+10), value['issuer_policy']


@pytest.mark.parametrize('limit', ['grants', 'orders', 'notional'])
def test_different_exact_policies_share_original_issuer_budget_across_restart(case, tmp_path, limit):
    private, _, _, registry, _, log = case
    args = grouped(case, limit=limit)
    first, first_policy = handoff(args, tmp_path, 1)
    issuer.issue_submission(first, policy=first_policy, registry=registry, key=private, store=log, now_ms=NOW+11)
    second, second_policy = handoff(args, tmp_path, 2)
    reopened = IssuerStore(log.path)
    try:
        with pytest.raises(ValueError, match='ISSUER_GROUP_.*BUDGET'):
            issuer.issue_submission(second, policy=second_policy, registry=registry, key=private,
                                    store=reopened, now_ms=NOW+12)
        assert len(reopened.snapshot()) == 2
    finally: reopened.close()
    assert args['dispatch'].snapshot() == []


def test_budget_group_cannot_be_rebound_to_larger_limits(case, tmp_path):
    private, _, _, registry, _, log = case
    args = grouped(case)
    first, policy = handoff(args, tmp_path, 1)
    issuer.issue_submission(first, policy=policy, registry=registry, key=private, store=log, now_ms=NOW+11)
    args['issuer_policy']['budget_group']['max_grants'] = 10
    second, changed = handoff(args, tmp_path, 2)
    with pytest.raises(ValueError, match='ISSUER_GROUP_ID_REBOUND'):
        issuer.issue_submission(second, policy=changed, registry=registry, key=private, store=log, now_ms=NOW+12)
    assert len(log.snapshot()) == 2


def test_unsigned_group_reservation_remains_spent_after_restart(case, tmp_path, monkeypatch):
    private, _, _, registry, _, log = case
    args = grouped(case)
    first, policy = handoff(args, tmp_path, 1)
    signs = []
    def interrupted(*a, **kw):
        signs.append(1)
        raise RuntimeError('synthetic interruption after reservation')
    monkeypatch.setattr(issuer, '_sign', interrupted)
    with pytest.raises(RuntimeError, match='synthetic interruption'):
        issuer.issue_submission(first, policy=policy, registry=registry, key=private, store=log, now_ms=NOW+11)
    assert len(log.snapshot()) == 1
    second, later = handoff(args, tmp_path, 2)
    reopened = IssuerStore(log.path)
    try:
        with pytest.raises(ValueError, match='ISSUER_GROUP_COUNT_BUDGET'):
            issuer.issue_submission(second, policy=later, registry=registry, key=private, store=reopened, now_ms=NOW+12)
        assert signs == [1] and len(reopened.snapshot()) == 1
    finally: reopened.close()


def test_two_issuer_connections_cannot_spend_one_group_slot_twice(case, tmp_path):
    private, _, _, registry, _, log = case
    args = grouped(case)
    proposals = [handoff(args, tmp_path, index) for index in (1,2)]
    stores = [IssuerStore(log.path), IssuerStore(log.path)]
    barrier = Barrier(2)
    def attempt(index):
        proposal, policy = proposals[index]
        barrier.wait(timeout=5)
        try:
            issuer.issue_submission(proposal, policy=policy, registry=registry, key=private,
                                    store=stores[index], now_ms=NOW+11)
            return 'SIGNED'
        except ValueError as error:
            assert str(error) == 'ISSUER_GROUP_COUNT_BUDGET'
            return 'BUDGET_REJECTED'
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, (0,1)))
        assert sorted(outcomes) == ['BUDGET_REJECTED', 'SIGNED']
        assert len(log.snapshot()) == 2
    finally:
        for store in stores: store.close()
