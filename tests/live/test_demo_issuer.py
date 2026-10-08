"""Independent issuer software uses isolated material, never deployment keys."""
from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from live.execution import demo_issuer as issuer
from live.execution.demo_batch_risk import _digest, _json_value
from live.execution.demo_issuer_store import IssuerStore
from live.execution.demo_execution_session import ExecutionSession
from live.execution import demo_authority, demo_prepared_execution
from live.exchange import binance_demo_exact
from services.validation import demo_submission
from tests.live.test_demo_pre_submit import setup, stores, NOW
from tests.live.test_demo_execution_session import request


def policy_for(proposal, registry):
    return {'schema':'DEMO_ISSUER_POLICY_V1','environment':'BINANCE_USDM_DEMO',
        'policy_id':'isolated-test-policy','deployment_sha256':registry['deployment_sha256'],
        'registry_sha256':demo_submission.registry_sha256(registry),
        'executor_release_commit':'c'*40,'account_ref_sha256':proposal['context']['account_ref_sha256'],
        'authority_id':registry['keys'][0]['authority_id'],'key_id':registry['keys'][0]['key_id'],
        'valid_from_ms':NOW-1000,'valid_until_ms':NOW+30000,'grant_ttl_ms':10000,
        'max_grants':2,'max_orders':4,'max_total_notional':'300',
        'max_cancel_grants':2,'max_cancel_orders':4,
        'scope':{'symbol':'BTCUSDC','sides':['BUY','SELL'],'time_in_force':['GTX','IOC'],
            'allow_opening':True,'max_quantity_per_order':'0.003',
            'max_notional_per_order':'150','max_notional_per_grant':'250'},
        'bindings':{'strategy_id':proposal['strategy_id'],
            'mandate_sha256':proposal['context']['mandate_sha256'],
            'validation_sha256':proposal['context']['validation_sha256'],
            'native_limits_sha256':_digest(proposal['risk']['input_snapshot']['native_limits']),
            'strategy_approval_sha256':_digest(proposal['risk']['input_snapshot']['strategy'])}}


def fixture(tmp_path,monkeypatch):
    private,_,values=setup(tmp_path,monkeypatch)
    opened=stores(tmp_path)
    session=ExecutionSession(dispatch=opened[0],recovery=opened[1],oms=opened[2],evidence_directory=tmp_path/'evidence')
    handoff=session.propose(request(values),now_ms=NOW)
    registry=demo_authority.load_registry_for_executor()
    policy=policy_for(handoff['proposal'],registry)
    log=IssuerStore(tmp_path/'issuer.sqlite3',create=True)
    return private,values,opened,session,handoff,registry,policy,log


def test_coherently_replayed_risk_cannot_invent_positions_in_a_flat_origin(tmp_path,monkeypatch):
    from live.execution.demo_batch_risk import screen
    from live.execution.demo_pre_submit import _risk_with_history
    private,values,opened,session,handoff,registry,policy,log=fixture(tmp_path,monkeypatch)
    inputs=deepcopy(values['risk_inputs'])
    inputs['account']['positions']=[{'symbol':'BTCUSDC','quantity':'0.0001','mark_price':'60000'}]
    p=handoff['proposal']
    p['risk']=screen(p['orders'],**_risk_with_history(inputs,p['state']['oms'],NOW))
    assert p['risk']['status']=='RISK_CHECKS_PASSED'
    p['context']['risk_sha256']=p['risk']['risk_sha256'];handoff['proposal_sha256']=_digest(p)
    try:
        with pytest.raises(ValueError,match='ISSUER_POSITION_BINDING'):
            issuer.issue_submission(handoff,policy=policy,registry=registry,key=private,store=log,now_ms=NOW+1)
        assert log.snapshot()==[]
    finally:
        log.close()
        for store in opened:store.close()


def test_real_session_signature_handoff_keeps_original_exact_once_execution(tmp_path,monkeypatch):
    private,values,opened,session,handoff,registry,policy,log=fixture(tmp_path,monkeypatch)
    calls=[]
    monkeypatch.setattr(demo_prepared_execution,'now_ms',lambda:NOW+2)
    monkeypatch.setattr(demo_prepared_execution,'_require_state_custody',lambda *a,**k:None)
    monkeypatch.setattr(binance_demo_exact,'_credentials',lambda _:('test-key','test-secret'))
    monkeypatch.setattr(binance_demo_exact,'_http_once',lambda *a:(calls.append(1) or 200,json.dumps({
        'symbol':'BTCUSDC','clientOrderId':values['orders'][0]['newClientOrderId'],'orderId':123}).encode()))
    try:
        grant=issuer.issue_submission(handoff,policy=policy,registry=registry,key=private,store=log,now_ms=NOW+1)
        assert grant['schema']=='DEMO_EXECUTION_SESSION_GRANT_V1'
        proof=demo_submission.verify(grant['envelope'],registry=registry,
            expected_context=handoff['proposal']['context'],orders=values['orders'],now_ms=NOW+1)
        assert proof['signature_verified'] and not proof['one_time_consumed'] and not proof['economic_authority']
        result=session.submit(grant['proposal_sha256'],json.dumps(grant['envelope']).encode())
        assert result['execution']['status']=='WIRE_ATTEMPTS_RECORDED' and calls==[1]
        assert opened[2].fills()==[]
        with pytest.raises(ValueError):
            issuer.issue_submission(handoff,policy=policy,registry=registry,key=private,store=log,now_ms=NOW+2)
        assert calls==[1] and len(log.snapshot())==2
    finally:
        log.close()
        for store in opened:store.close()


@pytest.mark.parametrize('change',['account','environment','registry','policy_expired','mandate','validation',
    'native_limits','strategy','opening','notional','quantity','risk_digest','risk_result','origin',
    'recovery','handoff','float_cap','extra','future','revoked'])
def test_policy_and_all_original_bindings_reject_before_reserving_or_signing(tmp_path,monkeypatch,change):
    private,values,opened,session,handoff,registry,policy,log=fixture(tmp_path,monkeypatch)
    at=NOW+1
    if change=='account':policy['account_ref_sha256']='f'*64
    elif change=='environment':policy['environment']='BINANCE_USDM_MAINNET'
    elif change=='registry':policy['registry_sha256']='f'*64
    elif change=='policy_expired':at=NOW+30000
    elif change in ('mandate','validation'):policy['bindings'][change+'_sha256']='f'*64
    elif change=='native_limits':policy['bindings']['native_limits_sha256']='f'*64
    elif change=='strategy':policy['bindings']['strategy_approval_sha256']='f'*64
    elif change=='opening':policy['scope']['allow_opening']=False
    elif change=='notional':policy['scope']['max_notional_per_order']='119.999999999999999999'
    elif change=='quantity':policy['scope']['max_quantity_per_order']='0.001999999999999999'
    elif change=='risk_digest':handoff['proposal']['risk']['risk_sha256']='f'*64
    elif change=='risk_result':handoff['proposal']['risk']['checks'][0]['reasons']=['DENIED']
    elif change=='origin':handoff['proposal']['origin']['account_ref_sha256']='f'*64
    elif change=='recovery':handoff['proposal']['state']['extra']='forged'
    elif change=='handoff':handoff['proposal_sha256']='f'*64
    elif change=='float_cap':policy['max_total_notional']=300.0
    elif change=='extra':policy['approved_by']='AI'
    elif change=='future':at=NOW-1
    elif change=='revoked':registry['keys'][0]['status']='REVOKED'
    if change in ('risk_digest','risk_result','origin','recovery'):
        handoff['proposal_sha256']=_digest(handoff['proposal'])
    monkeypatch.setattr(issuer,'_sign',lambda *a:pytest.fail('signer reached on rejected input'))
    try:
        with pytest.raises(ValueError):issuer.issue_submission(handoff,policy=policy,registry=registry,key=private,store=log,now_ms=at)
        assert log.snapshot()==[]
    finally:
        log.close()
        for store in opened:store.close()


def test_crash_after_reservation_consumes_budget_and_restart_never_resigns(tmp_path,monkeypatch):
    private,values,opened,session,handoff,registry,policy,log=fixture(tmp_path,monkeypatch)
    def crash(*args):raise SystemExit(71)
    monkeypatch.setattr(issuer,'_sign',crash)
    try:
        with pytest.raises(SystemExit):issuer.issue_submission(handoff,policy=policy,registry=registry,key=private,store=log,now_ms=NOW+1)
        assert len(log.snapshot())==1
        log.close();log=IssuerStore(tmp_path/'issuer.sqlite3')
        monkeypatch.setattr(issuer,'_sign',lambda *a:pytest.fail('restart resigned reserved proposal'))
        with pytest.raises(ValueError):issuer.issue_submission(handoff,policy=policy,registry=registry,key=private,store=log,now_ms=NOW+2)
        assert len(log.snapshot())==1
    finally:
        log.close()
        for store in opened:store.close()


def test_two_issuer_connections_cannot_overspend_one_installed_policy(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    private,values,opened,session,handoff,registry,policy,log=fixture(tmp_path,monkeypatch)
    second_values=deepcopy(values);second_values['orders'][0]['newClientOrderId']='QDtest000002'
    second=ExecutionSession(dispatch=opened[0],recovery=opened[1],oms=opened[2],evidence_directory=tmp_path/'evidence')
    second_handoff=second.propose(request(second_values),now_ms=NOW)
    policy['max_grants']=1;barrier=threading.Barrier(2)
    def worker(value):
        own=IssuerStore(tmp_path/'issuer.sqlite3')
        try:
            barrier.wait(timeout=5)
            try:return issuer.issue_submission(value,policy=policy,registry=registry,key=private,store=own,now_ms=NOW+1)
            except ValueError:return None
        finally:own.close()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(worker,[handoff,second_handoff]))
        assert sum(result is not None for result in results)==1 and len(log.snapshot())==2
    finally:
        log.close()
        for store in opened:store.close()


def cancel_handoff(handoff):
    from services.validation import demo_cancellation as auth
    original=handoff['proposal'];ctx=original['context'];order=original['orders'][0]
    commands=[{'symbol':order['symbol'],'origClientOrderId':order['newClientOrderId'],'orderId':'123'}]
    context={key:ctx[key] for key in ('deployment_sha256','executor_boot_sha256','account_ref_sha256','registry_sha256')}
    context.update(reservation_sha256='d'*64,original_batch_sha256=ctx['batch_sha256'],
        cancel_batch_sha256=auth.batch_sha256(commands),query_snapshot_sha256='e'*64)
    proposal={'schema':'DEMO_CANCEL_PROPOSAL_V1','context':context,'commands':commands,
        'query_record_hashes':['f'*64],'new_order_submission_enabled':False,'economic_authority':False}
    return {'schema':'DEMO_CANCELLATION_SESSION_PROPOSAL_V1','proposal_sha256':_digest(proposal),
        'proposal_path':'/isolated/cancel.json','proposal':proposal,'status':'WAITING_INDEPENDENT_GRANT','submission_enabled':False}


def test_cancel_uses_its_own_budget_and_only_previously_issued_original_ids(tmp_path,monkeypatch):
    from services.validation import demo_cancellation as auth
    private,values,opened,session,handoff,registry,policy,log=fixture(tmp_path,monkeypatch)
    policy['max_grants']=1
    try:
        issuer.issue_submission(handoff,policy=policy,registry=registry,key=private,store=log,now_ms=NOW+1)
        cancellation=cancel_handoff(handoff)
        grant=issuer.issue_cancellation(cancellation,policy=policy,registry=registry,key=private,store=log,now_ms=NOW+2)
        proof=auth.verify(grant['envelope'],registry=registry,expected_context=cancellation['proposal']['context'],
            commands=cancellation['proposal']['commands'],now_ms=NOW+2)
        assert proof['signature_verified'] and not proof['economic_authority'] and len(log.snapshot())==4
        with pytest.raises(ValueError):issuer.issue_cancellation(cancellation,policy=policy,registry=registry,key=private,store=log,now_ms=NOW+3)
    finally:
        log.close()
        for store in opened:store.close()


@pytest.mark.parametrize('change',['no_original','unsigned_original','foreign_order','wrong_batch','changed_query'])
def test_cancel_cannot_expand_original_issued_scope_or_repeat_under_a_new_digest(tmp_path,monkeypatch,change):
    from services.validation import demo_cancellation as auth
    private,values,opened,session,handoff,registry,policy,log=fixture(tmp_path,monkeypatch)
    try:
        if change=='unsigned_original':
            monkeypatch.setattr(issuer,'_sign',lambda *_:(_ for _ in ()).throw(SystemExit(71)))
            with pytest.raises(SystemExit):issuer.issue_submission(handoff,policy=policy,registry=registry,key=private,store=log,now_ms=NOW+1)
        elif change!='no_original':issuer.issue_submission(handoff,policy=policy,registry=registry,key=private,store=log,now_ms=NOW+1)
        cancellation=cancel_handoff(handoff);p=cancellation['proposal']
        if change=='foreign_order':
            p['commands'][0]['origClientOrderId']='QDforeign0001';p['context']['cancel_batch_sha256']=auth.batch_sha256(p['commands'])
        elif change=='wrong_batch':p['context']['original_batch_sha256']='0'*64
        elif change=='changed_query':
            issuer.issue_cancellation(cancellation,policy=policy,registry=registry,key=private,store=log,now_ms=NOW+2)
            p['context']['query_snapshot_sha256']='0'*64
        cancellation['proposal_sha256']=_digest(p);before=log.snapshot()
        monkeypatch.setattr(issuer,'_sign',lambda *a:pytest.fail('rejected cancellation signed'))
        with pytest.raises(ValueError):issuer.issue_cancellation(cancellation,policy=policy,registry=registry,key=private,store=log,now_ms=NOW+3)
        assert log.snapshot()==before
    finally:
        log.close()
        for store in opened:store.close()


def test_signing_that_finishes_after_expiry_keeps_reservation_without_returning_a_grant(tmp_path,monkeypatch):
    private,values,opened,session,handoff,registry,policy,log=fixture(tmp_path,monkeypatch)
    try:
        with pytest.raises(ValueError):issuer.issue_submission(handoff,policy=policy,registry=registry,key=private,
            store=log,now_ms=NOW+1,clock_ms=lambda:NOW+40000)
        assert [row['kind'] for row in log.snapshot()]==['RESERVED']
    finally:
        log.close()
        for store in opened:store.close()
