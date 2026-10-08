"""Independent gold policy replay and issuer budget with isolated test keys."""
from copy import deepcopy
import json

import pytest

from live.execution import demo_issuer as issuer, demo_authority, demo_pre_submit as prep
from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_store import IssuerStore
from services.validation import demo_submission as auth
from tests.live.test_gold_demo_preparation import setup
from tests.live.test_demo_pre_submit import stores, NOW
from tests.live.test_demo_issuer import policy_for


@pytest.fixture
def case(tmp_path, monkeypatch):
    private, _, values = setup(tmp_path, monkeypatch)
    opened = stores(tmp_path)
    kwargs = dict(values, dispatch=opened[0], recovery=opened[1], oms=opened[2])
    proposal = prep.proposal(**kwargs)
    handoff = {'schema': 'DEMO_EXECUTION_SESSION_PROPOSAL_V1',
               'proposal_sha256': _digest(proposal), 'proposal_path': '/isolated/proposal.json',
               'proposal': proposal, 'status': 'WAITING_INDEPENDENT_GRANT', 'submission_enabled': False}
    registry = demo_authority.load_registry_for_executor()
    policy = policy_for(proposal, registry)
    policy.update(schema='DEMO_GOLD_ISSUER_POLICY_V1')
    policy['scope']['symbol'] = 'XAUUSDT'
    log = IssuerStore(tmp_path/'issuer.db', create=True)
    try:
        yield private, kwargs, handoff, registry, policy, log
    finally:
        log.close()
        for store in opened:
            store.close()


def issue(case, *, at=NOW+1):
    private, _, handoff, registry, policy, log = case
    return issuer.issue_submission(handoff, policy=policy, registry=registry,
                                   key=private, store=log, now_ms=at)


def test_pilot_stop_in_bound_proposal_rejects_before_issuer_reservation(case):
    _,_,handoff,_,_,log=case
    proposal=handoff['proposal']
    proposal['state']['pilot_control']=dict(status='STOPPED',configuration_sha256='a'*64,submission_enabled=False)
    proposal['context']['recovery_sha256']=_digest(proposal['state'])
    handoff['proposal_sha256']=_digest(proposal)
    before=log.snapshot()
    with pytest.raises(ValueError,match='PILOT_STOPPED'):issue(case)
    assert log.snapshot()==before


def test_gold_issuer_replays_risk_signs_and_original_prepare_accepts(case, tmp_path, monkeypatch):
    _, kwargs, handoff, registry, _, log = case
    grant = issue(case)
    envelope = grant['envelope']
    assert envelope['schema'] == 'DEMO_GOLD_SIGNED_SUBMISSION_V1'
    assert auth.verify(envelope, registry=registry, expected_context=handoff['proposal']['context'],
                       orders=kwargs['orders'], now_ms=NOW+1)['signature_verified']
    batch = prep.prepare(json.dumps(envelope).encode(), verification_now_ms=NOW+1,
                         evidence_directory=tmp_path/'evidence', **kwargs)
    assert batch['status'] == 'INTENTS_PERSISTED'
    from live.execution import demo_prepared_execution as executor
    from live.exchange import binance_demo_exact as transport
    calls = []
    monkeypatch.setattr(executor, '_require_state_custody', lambda *a, **k: None)
    monkeypatch.setattr(executor, 'now_ms', lambda: NOW+2)
    monkeypatch.setattr(transport, '_credentials', lambda _: ('synthetic-key', 'synthetic-secret'))
    monkeypatch.setattr(transport, '_http_once', lambda *a: (calls.append(1) or 200,
        json.dumps({'symbol': 'XAUUSDT', 'orderId': 123,
                    'clientOrderId': kwargs['orders'][0]['newClientOrderId']}).encode()))
    args = {k: v for k, v in kwargs.items() if k not in ('orders', 'origin', 'now_ms')}
    result = executor.execute(batch, **args)
    assert result['status'] == 'WIRE_ATTEMPTS_RECORDED' and calls == [1]
    assert kwargs['oms'].fills() == []
    snapshot = log.snapshot()
    assert len(snapshot) == 2
    reopened = IssuerStore(log.path, read_only=True)
    try:
        assert reopened.snapshot() == snapshot
    finally:
        reopened.close()
    with pytest.raises(ValueError):
        issue(case, at=NOW+2)
    assert log.snapshot() == snapshot


@pytest.mark.parametrize('damage', ['legacy_policy', 'btc_scope', 'cap', 'risk', 'origin', 'expired'])
def test_gold_issuer_rejects_wrong_scope_or_evidence_before_reserving(case, damage):
    _, _, handoff, _, policy, log = case
    at = NOW+1
    if damage == 'legacy_policy':
        policy['schema'] = 'DEMO_ISSUER_POLICY_V1'
    elif damage == 'btc_scope':
        policy['scope']['symbol'] = 'BTCUSDT'
    elif damage == 'cap':
        policy['scope']['max_notional_per_order'] = '119.999999999999999999'
    elif damage == 'risk':
        handoff['proposal']['risk']['checks'][0]['notional'] = '1'
        handoff['proposal_sha256'] = _digest(handoff['proposal'])
    elif damage == 'origin':
        handoff['proposal']['origin']['account_ref_sha256'] = 'f'*64
        handoff['proposal_sha256'] = _digest(handoff['proposal'])
    else:
        at = policy['valid_until_ms']
    with pytest.raises(ValueError):
        issue(case, at=at)
    assert log.snapshot() == []


def test_gold_grant_budget_counts_previous_signature(case):
    private, kwargs, handoff, registry, policy, log = case
    policy['max_grants'] = 1
    issue(case)
    changed = deepcopy(kwargs['orders'])
    changed[0]['newClientOrderId'] = 'QDgoldsecond001'
    proposal = prep.proposal(**dict(kwargs, orders=changed))
    second = dict(handoff, proposal=proposal, proposal_sha256=_digest(proposal))
    with pytest.raises(ValueError):
        issuer.issue_submission(second, policy=policy, registry=registry, key=private, store=log, now_ms=NOW+2)
    assert len(log.snapshot()) == 2
