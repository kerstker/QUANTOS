"""A concrete Demo setup proposal is separate from operator enrollment."""
from copy import deepcopy
import hashlib
import json

import pytest

from tools import demo_operator_setup as tool
from tests.tools.test_binance_demo_execution_origin import collect, NOW
from tests.live.test_demo_submission_verification import material


def inputs(tmp_path):
    origin,_=collect(tmp_path)
    private,registry,*_=material();registry['keys']=[]
    spec=tool.propose(origin=origin,registry=registry,source_commit='c'*40,now_ms=NOW)
    return origin,registry,private,spec


def test_proposal_does_not_create_authority_and_has_a_bounded_quote_only_plan(tmp_path):
    origin,registry,private,spec=inputs(tmp_path)
    assert spec['status']=='AWAITING_OPERATOR_ENROLLMENT'
    assert spec['environment']=='BINANCE_USDM_DEMO' and spec['symbol']=='BTCUSDC'
    assert spec['max_order_notional']=='250' and spec['max_total_notional']=='750'
    assert spec['steps']==['OPEN_IOC','REDUCE_ONLY_IOC','RECONCILE']
    assert spec['economic_authority'] is False and spec['orders_submitted'] is False
    assert 'approved_by' not in json.dumps(spec) and 'private_key' not in json.dumps(spec)
    order=tool.opening_order(origin,spec,client_order_id='QDfunctional000001',now_ms=NOW)
    assert order['quantity']=='0.002' and order['timeInForce']=='IOC'
    assert order['reduceOnly'] is False
    assert not registry['keys']


@pytest.mark.parametrize('change',['stale','held','wrong_quote','insufficient_wallet'])
def test_proposal_rejects_unusable_first_execution_inputs(tmp_path,change):
    origin,registry,private,spec=inputs(tmp_path)
    at=NOW
    if change=='stale':at+=30001
    elif change=='wrong_quote':origin['symbol']='BTCUSDT'
    elif change=='held':
        held=tmp_path/'held';held.mkdir();origin,_=collect(held,conditional=True)
    else:
        for row in origin['reads']:
            if row['kind'].startswith('account'):
                asset=next(x for x in row['data']['assets'] if x['asset']=='USDC')
                for key in ('walletBalance','crossWalletBalance','availableBalance','marginBalance'):asset[key]='10'
    with pytest.raises(ValueError):tool.propose(origin=origin,registry=registry,source_commit='c'*40,now_ms=at)


def stop_bytes(tmp_path):
    from services.risk.pre_trade.kill_switch_store import KillSwitchStateStore
    from quantos.contracts.killswitch import KillSwitchState
    path=tmp_path/'existing-stop.sqlite3'
    store=KillSwitchStateStore(str(path),create=True)
    try:store.bootstrap(KillSwitchState(),recorded_at=0)
    finally:store.close()
    return path.read_bytes()


def test_operator_enrollment_builds_files_accepted_by_original_installer(tmp_path):
    origin,registry,private,spec=inputs(tmp_path);stop=stop_bytes(tmp_path)
    approval={'schema':'DEMO_OPERATOR_ENROLLMENT_V1','spec_sha256':tool.digest(spec),
        'operator_id':'isolated-human-operator','approved_at_ms':NOW,'purpose':'DEMO_FUNCTIONAL_TEST_ONLY'}
    manifest,files=tool.assemble(spec=spec,approval=approval,origin=origin,registry=registry,
        private_key=private,stop_image=stop,now_ms=NOW)
    from tools.demo_operating_bundle import validate_materials
    reviewed=validate_materials(manifest=manifest,files=files,existing_registry=registry,
        source_commit='c'*40,account_ref=origin['account_ref_sha256'],now_ms=NOW)
    assert files['kill-switch.sqlite3']==stop and len(reviewed['tasks'])==1
    assert reviewed['profile']['mandate']['approved_by']==['isolated-human-operator']
    assert reviewed['profile']['attestations'][reviewed['profile']['strategy']['strategy_id']]['kind']=='human_override'
    assert reviewed['policy']['scope']['symbol']=='BTCUSDC'


@pytest.mark.parametrize('change',['none','wrong_hash','future','expired','agent','purpose','source','limits'])
def test_missing_or_changed_operator_scope_cannot_produce_installable_files(tmp_path,change):
    origin,registry,private,spec=inputs(tmp_path);stop=stop_bytes(tmp_path)
    approval={'schema':'DEMO_OPERATOR_ENROLLMENT_V1','spec_sha256':tool.digest(spec),
        'operator_id':'isolated-human-operator','approved_at_ms':NOW,'purpose':'DEMO_FUNCTIONAL_TEST_ONLY'}
    if change=='none':approval=None
    elif change=='wrong_hash':approval['spec_sha256']='f'*64
    elif change=='future':approval['approved_at_ms']+=1
    elif change=='expired':approval['approved_at_ms']-=3600001
    elif change=='agent':approval['operator_id']='AI'
    elif change=='purpose':approval['purpose']='MAINNET'
    elif change=='source':spec['source_commit']='d'*40
    elif change=='limits':spec['max_order_notional']='1000000';approval['spec_sha256']=tool.digest(spec)
    with pytest.raises(ValueError):tool.assemble(spec=spec,approval=approval,origin=origin,registry=registry,
        private_key=private,stop_image=stop,now_ms=NOW)


def test_enrolled_profile_runs_original_risk_and_independent_signing_session(tmp_path,monkeypatch):
    from live.execution import demo_request_inputs, demo_authority, demo_current_kill, demo_issuer
    from live.execution.demo_execution_session import ExecutionSession
    from live.execution.demo_issuer_store import IssuerStore
    from quantos.contracts.killswitch import KillSwitchState
    from tests.live.test_demo_pre_submit import stores
    from tests.tools.test_binance_demo_market_inputs import capture
    origin,registry,private,spec=inputs(tmp_path);stop=stop_bytes(tmp_path)
    approval={'schema':'DEMO_OPERATOR_ENROLLMENT_V1','spec_sha256':tool.digest(spec),
        'operator_id':'isolated-human-operator','approved_at_ms':NOW,'purpose':tool.PURPOSE}
    manifest,files=tool.assemble(spec=spec,approval=approval,origin=origin,registry=registry,
        private_key=private,stop_image=stop,now_ms=NOW)
    task=json.loads(files[manifest['tasks'][0]+'.json']);policy=json.loads(files['policy.json'])
    registry=json.loads(files['registry.json'])
    monkeypatch.setattr(demo_authority,'_read_root_owned_json',lambda _:files['registry.json'])
    monkeypatch.setattr(demo_authority.os,'geteuid',lambda:registry['executor_uid'])
    monkeypatch.setattr(demo_current_kill,'_PATH',tmp_path/'existing-stop.sqlite3')
    monkeypatch.setattr(demo_current_kill,'_custody',lambda:None)
    opened=stores(tmp_path);market,_=capture()
    try:
        result=demo_request_inputs.build(orders=task['orders'],origin=origin,market_inputs=market,
            profile=task['profile'],issuer_policy=policy,kill_state=KillSwitchState(),
            dispatch=opened[0],recovery=opened[1],oms=opened[2],now_ms=NOW+10010)
        session=ExecutionSession(dispatch=opened[0],recovery=opened[1],oms=opened[2],evidence_directory=tmp_path/'evidence')
        handoff=session.propose(tool.canonical(result['request']),now_ms=NOW+10010)
        log=IssuerStore(tmp_path/'issuer.sqlite3',create=True)
        try:
            grant=demo_issuer.issue_submission(handoff,policy=policy,registry=registry,key=private,store=log,now_ms=NOW+10011)
            assert grant['proposal_sha256']==handoff['proposal_sha256']
            assert handoff['proposal']['risk']['status']=='RISK_CHECKS_PASSED'
            from live.execution import demo_prepared_execution
            from live.exchange import binance_demo_exact
            calls=[]
            monkeypatch.setattr(demo_prepared_execution,'now_ms',lambda:NOW+10012)
            monkeypatch.setattr(demo_prepared_execution,'_require_state_custody',lambda *a,**k:None)
            monkeypatch.setattr(binance_demo_exact,'_credentials',lambda _:('isolated-key','isolated-secret'))
            monkeypatch.setattr(binance_demo_exact,'_http_once',lambda *a:(calls.append(1) or 200,json.dumps({
                'symbol':'BTCUSDC','clientOrderId':task['orders'][0]['newClientOrderId'],'orderId':123}).encode()))
            sent=session.submit(grant['proposal_sha256'],tool.canonical(grant['envelope']))
            assert sent['execution']['status']=='WIRE_ATTEMPTS_RECORDED' and calls==[1]
            with pytest.raises(ValueError):session.submit(grant['proposal_sha256'],tool.canonical(grant['envelope']))
            assert calls==[1]
        finally:log.close()
        assert len(opened[0].snapshot())==1 and opened[2].fills()==[]
    finally:
        for value in opened:value.close()


def test_noninteractive_setup_cannot_invent_an_operator_record(tmp_path,monkeypatch):
    import sys
    _,_,_,spec=inputs(tmp_path)
    monkeypatch.setattr(sys.stdin,'isatty',lambda:False)
    monkeypatch.setattr('builtins.input',lambda *_:pytest.fail('noninteractive enrollment prompt'))
    with pytest.raises(ValueError,match='SETUP_LOCAL_OPERATOR_REQUIRED'):tool.operator_confirmation(spec)
