"""Original issuer transactions preserve an explicitly reserved exit slice."""
from copy import deepcopy
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from live.execution import demo_issuer as issuer
from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import review_submission, validate_policy
from live.execution.demo_issuer_store import IssuerStore
from tests.live.test_gold_strategy_constraints import case as case, NOW
from tests.live.test_gold_strategy_budget_group import grouped, handoff

SCHEMA = 'DEMO_GOLD_STRATEGY_EXIT_BUDGET_POLICY_V1'


def budget(case, dimension='grants'):
    args = grouped(case, limit='unlimited')
    policy = args['issuer_policy']
    policy['schema'] = SCHEMA
    group = policy['budget_group']
    group.update(max_grants=2 if dimension == 'grants' else 20,
                 max_orders=2 if dimension == 'orders' else 20,
                 max_total_notional='240' if dimension == 'notional' else '2400')
    group['exit_reserve'] = dict(grants=1, orders=1, notional='120')
    return args


@pytest.mark.parametrize('dimension', ['grants', 'orders', 'notional'])
def test_opening_cannot_consume_reserved_exit_after_restart(case, tmp_path, dimension):
    private, _, _, registry, _, log = case
    args = budget(case, dimension)
    first, first_policy = handoff(args, tmp_path, 1)
    issuer.issue_submission(first, policy=first_policy, registry=registry, key=private, store=log, now_ms=NOW+11)
    second, second_policy = handoff(args, tmp_path, 2)
    before = log.snapshot()
    with closing(IssuerStore(log.path)) as reopened:
        with pytest.raises(ValueError, match='ISSUER_EXIT_RESERVE'):
            issuer.issue_submission(second, policy=second_policy, registry=registry, key=private, store=reopened, now_ms=NOW+12)
        assert reopened.snapshot() == before


def stored_plan(args, tmp_path, index, registry, *, reduce_only=False):
    request, policy = handoff(args, tmp_path, index)
    plan = review_submission(request, policy=policy, registry=registry, now_ms=NOW+11)
    # Store-level adversarial fixture: this is not a risk-approved reduction.
    # A real exit must independently traverse original position/OMS gates.
    if reduce_only:
        from services.validation.demo_submission import batch_sha256, GOLD_BATCH_SCHEMA
        plan['orders'][0]['reduceOnly'] = True
        plan['context']['batch_sha256'] = batch_sha256(plan['orders'], schema=GOLD_BATCH_SCHEMA)
        plan['proposal_sha256'] = _digest(plan)
    return plan, policy


@pytest.mark.parametrize('dimension', ['grants', 'orders', 'notional'])
def test_native_store_allows_exit_slice_after_opening_slice_used(case, tmp_path, dimension):
    _, _, _, registry, _, log = case
    args = budget(case, dimension)
    opening, policy = stored_plan(args, tmp_path, 1, registry)
    reduction, exit_policy = stored_plan(args, tmp_path, 2, registry, reduce_only=True)
    log.reserve(opening, policy=policy, registry=registry)
    log.reserve(reduction, policy=exit_policy, registry=registry)
    assert len(log.snapshot()) == 2
    with closing(IssuerStore(log.path, read_only=True)) as reopened:
        assert reopened.snapshot() == log.snapshot()


def test_signing_crash_keeps_opening_slice_spent(case, tmp_path, monkeypatch):
    private, _, _, registry, _, log = case
    args = budget(case)
    first, policy = handoff(args, tmp_path, 1)
    def interrupted(*args):
        raise RuntimeError('isolated crash after reservation')
    monkeypatch.setattr(issuer, '_sign', interrupted)
    with pytest.raises(RuntimeError, match='isolated crash'):
        issuer.issue_submission(first, policy=policy, registry=registry, key=private, store=log, now_ms=NOW+11)
    second, later = handoff(args, tmp_path, 2)
    with pytest.raises(ValueError, match='ISSUER_EXIT_RESERVE'):
        issuer.issue_submission(second, policy=later, registry=registry, key=private, store=log, now_ms=NOW+12)
    assert len(log.snapshot()) == 1


@pytest.mark.parametrize('dimension', ['grants', 'orders', 'notional'])
def test_a_previous_exit_does_not_make_room_for_an_unfunded_new_opening(case, tmp_path, dimension):
    _, _, _, registry, _, log = case
    args = budget(case, dimension)
    group = args['issuer_policy']['budget_group']
    if dimension == 'grants': group['max_grants'] = 3
    elif dimension == 'orders': group['max_orders'] = 3
    else: group['max_total_notional'] = '360'
    for index, reduction in ((1, False), (2, True)):
        plan, policy = stored_plan(args, tmp_path, index, registry, reduce_only=reduction)
        log.reserve(plan, policy=policy, registry=registry)
    plan, policy = stored_plan(args, tmp_path, 3, registry)
    before = log.snapshot()
    with pytest.raises(ValueError, match='ISSUER_EXIT_RESERVE'):
        log.reserve(plan, policy=policy, registry=registry)
    assert log.snapshot() == before


def test_two_connections_cannot_spend_last_opening_slot(case, tmp_path):
    _, _, _, registry, _, log = case
    args = budget(case)
    plans = [stored_plan(args, tmp_path, i, registry) for i in (1, 2)]
    connections = [IssuerStore(log.path), IssuerStore(log.path)]
    barrier = Barrier(2)
    def reserve(index):
        plan, policy = plans[index]
        barrier.wait(timeout=5)
        try:
            connections[index].reserve(plan, policy=policy, registry=registry)
            return 'RESERVED'
        except ValueError as exc:
            assert str(exc) == 'ISSUER_EXIT_RESERVE_COUNT_BUDGET'
            return 'EXIT_RESERVED'
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(reserve, (0, 1))) == ['EXIT_RESERVED', 'RESERVED']
        assert len(log.snapshot()) == 1
    finally:
        for connection in connections:
            connection.close()


@pytest.mark.parametrize('damage', ['zero', 'bool', 'float', 'extra', 'all_grants', 'all_orders', 'all_notional'])
def test_malformed_or_exhaustive_reserve_rejected_before_any_reservation(case, damage):
    policy = budget(case)['issuer_policy']
    reserve = policy['budget_group']['exit_reserve']
    if damage == 'zero': reserve['grants'] = 0
    elif damage == 'bool': reserve['orders'] = True
    elif damage == 'float': reserve['notional'] = 120.0
    elif damage == 'extra': reserve['renew'] = True
    elif damage == 'all_grants': reserve['grants'] = policy['budget_group']['max_grants']
    elif damage == 'all_orders': reserve['orders'] = policy['budget_group']['max_orders']
    else: reserve['notional'] = policy['budget_group']['max_total_notional']
    with pytest.raises(ValueError): validate_policy(policy)
    assert case[-1].snapshot() == []


def test_same_group_cannot_remove_or_shrink_reserve(case, tmp_path):
    _, _, _, registry, _, log = case
    args = budget(case)
    first, policy = stored_plan(args, tmp_path, 1, registry)
    log.reserve(first, policy=policy, registry=registry)
    changed = dict(args, issuer_policy=deepcopy(args['issuer_policy']))
    changed['issuer_policy']['budget_group']['exit_reserve']['notional'] = '60'
    second, second_policy = stored_plan(changed, tmp_path, 2, registry)
    with pytest.raises(ValueError, match='ISSUER_GROUP_ID_REBOUND'):
        log.reserve(second, policy=second_policy, registry=registry)
    assert len(log.snapshot()) == 1
