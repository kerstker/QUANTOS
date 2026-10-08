"""Native delegated session and recovery; custody/key/venue remain isolated.

Strategy replay, risk, mandate, ledger reserve, signatures and the final send
gates are real. The clock is fixed for functional tests, not latency evidence.
"""
from contextlib import ExitStack, closing
from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from live.execution import demo_gold_delegation_enable as enabling
from live.execution import demo_issuer, demo_prepared_execution as executor
from live.execution import demo_resident_recovery as resident
from live.execution.demo_batch_risk import _json_value
from live.execution.demo_batch_risk import _digest
from live.execution.demo_dispatch_store import DemoDispatchStore
from live.execution.demo_execution_session import ExecutionSession, SessionRejected
from live.execution.demo_issuer_store import IssuerStore
from live.execution.order_recovery import SqliteOrderRecoveryStore
from live.execution.position_ledger import LiveLedger
from live.exchange import binance_demo_exact as transport
from tests.live.test_demo_gold_delegated_issuer import native_issuer_case
from tests.live.test_gold_strategy_constraints import case as case
from tests.tools.test_gold_runtime_evidence import original_evidence as original_evidence


def task_material(f):
    from tests.live.test_demo_request_inputs import profile
    from tools import binance_demo_market_inputs as capture
    from tools.gold_strategy_request import task_from_draft
    at = f.now_ms - 10
    end = at//60000*60000-1
    bars = []
    for index in range(61):
        start = end+1-(61-index)*60000
        price = str(4000+index)
        bars.append([start,price,price,price,price,'2',start+59999,'8000',3,'1','4000','0'])
    ticks = iter(range(at,at+100))
    def get(kind, **kwargs):
        if kind.startswith('clock'):
            value = {'serverTime':at+(6 if kind == 'clock_after' else 0)}
        elif kind == 'ticker':
            value = dict(symbol='XAUUSDT',openTime=at-86400000,closeTime=at,
                volume='100000',quoteVolume='400000000')
        else: value = bars
        return json.dumps(value).encode()
    market = capture.collect(symbol='XAUUSDT',get=get,clock_ms=lambda:next(ticks))
    assert market['status'] == 'CAPTURED'
    declared = profile(f.proposal_values,market)
    declared['symbol'] = 'XAUUSDT'
    # This isolated operator declares the concrete profile before this task is
    # built. No installed material or signed policy is changed in production.
    f.enable['original_binding']['profile_sha256'] = _digest(declared)
    f.enable['operator_confirmation']['candidate_sha256'] = _digest(enabling.candidate(f.enable))
    f.current_enable = deepcopy(f.enable)
    f.proof['enable_sha256'] = _digest(f.enable)
    context = {'registry':f.registry,'enable':f.enable}
    task = task_from_draft(f.proof['result']['decision']['draft'], profile=declared,
        issuer_policy=f.base_policy,kill_state=f.proposal_values['risk_inputs']['kill_switch'].state,
        source_commit='c'*40,now_ms=f.now_ms,delegated_admission=f.proof,delegation_context=context)
    return task, market, context


@pytest.fixture
def pipeline(case, original_evidence, tmp_path, monkeypatch):
    f = native_issuer_case(case, original_evidence, tmp_path, monkeypatch)
    f.clock = [f.now_ms]
    f.current_enable = deepcopy(f.enable)
    f.enable_reads = []
    def load_enabled(**kwargs):
        f.enable_reads.append(kwargs['role'])
        assert kwargs.pop('role') == 'executor'
        return deepcopy(enabling.validate(f.current_enable, **kwargs))
    monkeypatch.setattr(enabling, 'load_enabled', load_enabled)
    monkeypatch.setattr(executor, 'now_ms', lambda: f.clock[0])
    monkeypatch.setattr(executor, '_require_state_custody', lambda *a, **kw: None)
    with ExitStack() as stack:
        f.stores = dict(dispatch=stack.enter_context(closing(DemoDispatchStore(f.root/'dispatch.db'))),
            recovery=stack.enter_context(closing(SqliteOrderRecoveryStore(str(f.root/'recovery.db')))),
            oms=stack.enter_context(closing(LiveLedger(str(f.root/'oms.db')))))
        f.log = stack.enter_context(closing(IssuerStore(f.ledger)))
        f.evidence = f.root/'pipeline-evidence'
        f.session = ExecutionSession(**f.stores, evidence_directory=f.evidence)
        values = f.proposal_values
        risk = values['risk_inputs']
        f.request = dict(schema='DEMO_GOLD_DELEGATED_SESSION_INPUT_V1',
            orders=values['orders'], origin=values['origin'],
            risk=dict(account=risk['account'], market=risk['market'],
                native_limits=_json_value(asdict(risk['native_limits'])),
                strategy=risk['strategy'].model_dump(mode='json'),
                kill_switch=risk['kill_switch'].state.model_dump(mode='json'),
                plan_turnover=0),
            mandate=_json_value(asdict(values['mandate'])),
            attestations=_json_value({sid:asdict(a) for sid,a in values['attestations'].items()}),
            execution_constraints=values['execution_constraints'], delegated_admission=f.proof)
        f.credentials, f.posts = [], []
        monkeypatch.setattr(transport, '_credentials', lambda ref:
            (f.credentials.append(ref) or ('isolated-key', 'isolated-secret')))
        def post(body, headers):
            assert len(f.stores['dispatch'].wire_snapshot()) == 1
            assert len(f.stores['recovery'].pending()) == 1
            f.posts.append(body)
            return 200, json.dumps(dict(symbol='XAUUSDT', orderId=123,
                clientOrderId=f.request['orders'][0]['newClientOrderId'])).encode()
        monkeypatch.setattr(transport, '_http_once', post)
        yield f
        f.session.close()


def grant(f):
    handoff = f.session.propose(json.dumps(f.request).encode(), now_ms=f.clock[0])
    signed = demo_issuer.issue_delegated_submission(handoff, policy=f.base_policy,
        registry=f.registry, public_material=f.current_enable, key=f.private,
        store=f.log, now_ms=f.clock[0], clock_ms=lambda:f.clock[0])
    return handoff, signed


def test_native_session_rechecks_proof_and_sends_once(pipeline):
    f = pipeline
    handoff, signed = grant(f)
    outcome = f.session.submit(signed['proposal_sha256'], json.dumps(signed['envelope']).encode())
    assert outcome['execution']['status'] == 'WIRE_ATTEMPTS_RECORDED'
    assert len(f.posts) == len(f.credentials) == 1
    assert len(f.enable_reads) >= 4
    assert outcome['prepared']['proposal_sha256'] == handoff['proposal_sha256']
    assert len(f.stores['dispatch'].snapshot()) == 1 and f.stores['oms'].fills() == []
    assert [row['kind'] for row in f.log.snapshot()] == ['RESERVED', 'RESERVED', 'SIGNED']
    with pytest.raises(SessionRejected):
        f.session.submit(signed['proposal_sha256'], json.dumps(signed['envelope']).encode())
    assert len(f.posts) == 1


@pytest.mark.parametrize('change', ['revoke', 'expire'])
def test_change_during_credential_lookup_prevents_post_and_keeps_original_reserve(pipeline, monkeypatch, change):
    f = pipeline
    _, signed = grant(f)
    def credentials(ref):
        f.credentials.append(ref)
        if change == 'revoke': f.current_enable['consumer_enabled'] = False
        else: f.clock[0] = f.request['execution_constraints']['valid_until_ms']
        return 'isolated-key', 'isolated-secret'
    monkeypatch.setattr(transport, '_credentials', credentials)
    outcome = f.session.submit(signed['proposal_sha256'], json.dumps(signed['envelope']).encode())
    assert f.credentials and not f.posts
    assert outcome['cash_reconciled'] is False
    assert len(f.log.snapshot()) == 3 and len(f.stores['dispatch'].snapshot()) == 1
    assert len(f.stores['recovery'].pending()) == 1


def test_expired_revoked_delegation_keeps_read_only_original_recovery(pipeline, monkeypatch):
    f = pipeline
    handoff, signed = grant(f)
    outcome = f.session.submit(signed['proposal_sha256'], json.dumps(signed['envelope']).encode())
    f.clock[0] = f.request['execution_constraints']['valid_until_ms'] + 1
    f.current_enable['consumer_enabled'] = False
    def no_permission_reads(**kw): pytest.fail('historical recovery tried to renew delegation')
    monkeypatch.setattr(enabling, 'load_enabled', no_permission_reads)
    before = f.stores['dispatch'].evidence_snapshot(), f.log.snapshot()
    plan = resident.discover(**f.stores, evidence_directory=f.evidence)
    assert plan['status'] == 'RECOVERY_REQUIRED'
    prepared = plan['pending'][0]['prepared']
    assert prepared == outcome['prepared']
    assert prepared['proposal_sha256'] == handoff['proposal_sha256']
    assert plan['new_order_submission_enabled'] is False
    assert before == (f.stores['dispatch'].evidence_snapshot(), f.log.snapshot())
    assert len(f.posts) == 1


def test_native_draft_task_observed_request_to_original_session(pipeline):
    from live.execution import demo_request_inputs as builder
    from tools.demo_task import same_declarations
    f = pipeline
    task, market, context = task_material(f)
    built = builder.build(orders=task['orders'],origin=f.proof['origin'],market_inputs=market,
        profile=task['profile'],issuer_policy=f.base_policy,
        kill_state=f.proposal_values['risk_inputs']['kill_switch'].state,
        now_ms=f.now_ms,execution_constraints=task['execution_constraints'],
        delegated_admission=task['delegated_admission'],delegation_context=context,**f.stores)
    assert built['issuer_policy_sha256'] == _digest(f.base_policy)
    same_declarations(built['request'],task)
    f.request = built['request']
    _, signed = grant(f)
    result = f.session.submit(signed['proposal_sha256'],json.dumps(signed['envelope']).encode())
    assert result['execution']['status'] == 'WIRE_ATTEMPTS_RECORDED'
    assert len(f.posts) == 1


@pytest.mark.parametrize('after_credentials', [False, True])
def test_replay_crossing_original_deadline_stops_at_end_of_preflight(pipeline, monkeypatch, after_credentials):
    from live.execution import demo_delegated_inputs
    f = pipeline
    _, signed = grant(f)
    native = demo_delegated_inputs.executor_review
    def replay(*args, **kwargs):
        result = native(*args, **kwargs)
        if f.stores['dispatch'].snapshot() and (not after_credentials or f.credentials):
            f.clock[0] = f.request['execution_constraints']['valid_until_ms']
        return result
    monkeypatch.setattr(demo_delegated_inputs, 'executor_review', replay)
    try:
        f.session.submit(signed['proposal_sha256'], json.dumps(signed['envelope']).encode())
    except SessionRejected:
        pass
    assert not f.posts
    if not after_credentials:
        assert not f.credentials and f.stores['dispatch'].wire_snapshot() == []
    assert len(f.log.snapshot()) == 3 and len(f.stores['recovery'].pending()) == 1


def test_delegated_task_preserves_the_independently_enabled_full_profile(pipeline):
    from live.execution import demo_request_inputs as builder
    f = pipeline
    task, market, context = task_material(f)
    task['profile']['initial_day_pnl'] = '999'
    with pytest.raises(ValueError, match='DELEGATED_PROFILE'):
        builder.task_check(task,policy=f.base_policy,source_commit='c'*40,
            now_ms=f.now_ms,delegation_context=context)
    with pytest.raises(ValueError, match='DELEGATED_PROFILE'):
        builder.build(orders=task['orders'],origin=f.proof['origin'],market_inputs=market,
            profile=task['profile'],issuer_policy=f.base_policy,
            kill_state=f.proposal_values['risk_inputs']['kill_switch'].state,
            now_ms=f.now_ms,execution_constraints=task['execution_constraints'],
            delegated_admission=task['delegated_admission'],delegation_context=context,**f.stores)
    assert f.stores['dispatch'].snapshot() == [] and len(f.log.snapshot()) == 1
