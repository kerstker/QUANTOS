"""Installed partitions retain original cash and guard both private roles."""
from copy import deepcopy
import json
from pathlib import Path
import pytest

from live.execution.demo_batch_risk import _digest
from tests.live.test_demo_market_partition import book as book, candidate, NOW


def shift(value,delta):
    if isinstance(value,dict):return {key:shift(item,delta) for key,item in value.items()}
    if isinstance(value,list):return [shift(item,delta) for item in value]
    return value+delta if type(value) is int and value>=NOW else value


@pytest.fixture
def installed(book,tmp_path,monkeypatch):
    from live.execution import demo_market_partition_runtime as runtime
    from tests.live.test_demo_submission_verification import material
    state,context,values,stores,events=book
    sealed=candidate(book);legacy=deepcopy(values['registry'])
    private,new_registry,*_=material();key=deepcopy(new_registry['keys'][0])
    key.update(authority_id='gold-fixture-authority',key_id='gold-fixture-key',
        valid_from_ms=NOW+130002,valid_until_ms=NOW+300000)
    registry=deepcopy(legacy);registry['keys'].append(key)
    approval=dict(schema='DEMO_PARTITION_LOCAL_OPERATOR_APPROVAL_V1',candidate_sha256=_digest(sealed),
        operator_id='isolated-operator',approved_at_ms=NOW+130001,
        purpose='DEMO_MARKET_PARTITION_INSTALL_ONLY')
    record=dict(schema='DEMO_INSTALLED_MARKET_PARTITION_V1',source_commit='c'*40,
        candidate=sealed,candidate_sha256=_digest(sealed),legacy_registry=legacy,
        operator_approval=approval,installed_at_ms=NOW+130001,revalidation_sha256='e'*64,
        installed=True,submission_enabled=False,economic_authority=False)
    folder=tmp_path/'protected-records';folder.mkdir()
    executor=folder/'executor.json';issuer=folder/'issuer.json';activation=folder/'activation.json'
    for path in (executor,issuer):path.write_text(json.dumps(record))
    certificate=dict(schema='DEMO_MARKET_PARTITION_ACTIVATION_V1',source_commit='c'*40,
        installed_record_sha256=_digest(record),candidate_sha256=_digest(sealed),
        ready_result_sha256='f'*64,activated_at_ms=NOW+130001,
        installed=True,submission_enabled=False,economic_authority=False)
    activation.write_text(json.dumps(certificate))
    monkeypatch.setattr(runtime,'EXECUTOR_RECORD',executor)
    monkeypatch.setattr(runtime,'ISSUER_RECORD',issuer)
    monkeypatch.setattr(runtime,'ACTIVATION',activation)
    # Only host identity/custody are substituted. Files, source material and
    # the retained original V2 replay are real isolated artifacts.
    monkeypatch.setattr(runtime,'_read_record',lambda path,**kw:path.read_bytes())
    monkeypatch.setattr(runtime,'executing_source_commit',lambda:'c'*40)
    monkeypatch.setattr(runtime,'_role_identity',lambda *a:None)
    from live.execution import demo_authority
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:deepcopy(registry))
    fresh=shift(values['gold_origin'],NOW+140000-values['gold_origin']['completed_at_ms']-1)
    return dict(runtime=runtime,record=record,registry=registry,private=private,key=key,
        state=state,context=context,origin=fresh,stores=stores,events=events,
        executor=executor,issuer=issuer,activation=activation,legacy=legacy)


def test_no_gold_history_is_none_with_the_full_legacy_loss_retained(installed):
    values=installed;runtime=values['runtime'];before=deepcopy(values['state'])
    context,descriptor=runtime.resolve_context(state=values['state'],origin=values['origin'],as_of_ms=NOW+140000)
    assert context is None and descriptor['installed_record_sha256']==_digest(values['record'])
    from live.execution.demo_position_context import load_context
    old=load_context(values['state'],symbol='BTCUSDC',account_ref=values['origin']['account_ref_sha256'],as_of_ms=NOW+140000)
    assert old==values['context'] and old['initial_equity']=='5000' and old['canonical_wallet']=='4999.6801'
    assert values['record']['candidate']['binding']['gold_initial_wallet']=='4200'
    assert values['state']==before


@pytest.mark.parametrize('damage',['activation_missing','activation_digest','record_source','record_hash',
    'old_key_changed','old_key_dropped','new_key_too_early','no_new_key','candidate_changed',
    'approval','clock','authority_flag'])
def test_invalid_or_partial_installations_never_admit_gold(installed,monkeypatch,damage):
    v=installed;runtime=v['runtime'];record=deepcopy(v['record']);registry=deepcopy(v['registry'])
    if damage=='activation_missing':v['activation'].unlink()
    elif damage=='activation_digest':
        cert=json.loads(v['activation'].read_bytes());cert['installed_record_sha256']='e'*64
        v['activation'].write_text(json.dumps(cert))
    elif damage=='record_source':record['source_commit']='d'*40
    elif damage=='record_hash':record['candidate_sha256']='e'*64
    elif damage=='old_key_changed':registry['keys'][0]['status']='REVOKED'
    elif damage=='old_key_dropped':registry['keys']=registry['keys'][1:]
    elif damage=='new_key_too_early':registry['keys'][-1]['valid_from_ms']=NOW+129999
    elif damage=='no_new_key':registry=v['legacy']
    elif damage=='candidate_changed':record['candidate']['binding']['gold_initial_wallet']='4300'
    elif damage=='approval':record['operator_approval']['purpose']='AUTOMATIC_TRADING_APPROVAL'
    elif damage=='clock':record['installed_at_ms']=NOW+200000
    else:record['submission_enabled']=True
    v['executor'].write_text(json.dumps(record))
    from live.execution import demo_authority
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:registry)
    with pytest.raises(ValueError):runtime.resolve_context(state=v['state'],origin=v['origin'],as_of_ms=NOW+140000)


@pytest.mark.parametrize('damage',['old_cash','new_cash','extra_asset','old_position','old_state'])
def test_new_gold_cash_does_not_hide_changes_to_the_legacy_account(installed,damage):
    v=installed;origin=deepcopy(v['origin']);state=deepcopy(v['state'])
    if damage in ('old_cash','new_cash'):
        for read in origin['reads']:
            if read['kind'].startswith('account'):
                asset=next(a for a in read['data']['assets'] if a['asset']==('USDC' if damage=='old_cash' else 'USDT'))
                for key in ('walletBalance','crossWalletBalance','availableBalance','marginBalance'):asset[key]='4999'
    elif damage=='extra_asset':
        for read in origin['reads']:
            if read['kind'].startswith('account'):
                read['data']['assets'].append(dict(asset='BUSD',walletBalance='1',crossWalletBalance='1',availableBalance='1',marginBalance='1',unrealizedProfit='0'))
    elif damage=='old_position':
        for read in origin['reads']:
            if read['kind']=='positions':read['data']=[dict(symbol='BTCUSDC',positionAmt='0.002')]
    else:state['oms'][0]['quantity']+=0.001
    with pytest.raises(ValueError):v['runtime'].resolve_context(state=state,origin=origin,as_of_ms=NOW+140000)


def proposal(v):
    context,descriptor=v['runtime'].resolve_context(state=v['state'],origin=v['origin'],as_of_ms=NOW+140000)
    state=deepcopy(v['state']);state['market_partition']=descriptor
    if context is not None:state['position_context']=context
    return dict(state=state,origin=v['origin'])


def test_issuer_mirror_requires_the_exact_executor_partition_descriptor(installed):
    v=installed;p=proposal(v)
    policy=dict(executor_release_commit='c'*40,authority_id=v['key']['authority_id'],key_id=v['key']['key_id'])
    v['runtime'].check_proposal(p,role='issuer',registry=v['registry'],policy=policy,as_of_ms=NOW+140000)
    p['state']['market_partition']['installed_record_sha256']='e'*64
    with pytest.raises(ValueError):v['runtime'].check_proposal(p,role='issuer',registry=v['registry'],policy=policy,as_of_ms=NOW+140000)


def test_issuer_missing_local_mirror_cannot_consume_a_supplied_descriptor(installed):
    v=installed;p=proposal(v);v['issuer'].unlink()
    with pytest.raises(ValueError):v['runtime'].check_proposal(p,role='issuer',registry=v['registry'],as_of_ms=NOW+140000)


def gold_values(v,tmp_path,monkeypatch):
    from dataclasses import replace
    from live.execution import demo_pre_submit as prep
    from tests.live.test_gold_demo_preparation import setup
    (tmp_path/'proposal').mkdir()
    _,_,values=setup(tmp_path/'proposal',monkeypatch)
    monkeypatch.setattr(v['runtime'],'EXECUTOR_RECORD',v['executor'])
    monkeypatch.setattr(v['runtime'],'ISSUER_RECORD',v['issuer'])
    monkeypatch.setattr(v['runtime'],'ACTIVATION',v['activation'])
    from live.execution import demo_authority
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:deepcopy(v['registry']))
    values['origin']=v['origin'];values['now_ms']=NOW+140000
    values['orders'][0]['newClientOrderId']='QDgoldpartition001'
    risk=values['risk_inputs'];risk['now_ms']=values['now_ms']
    risk['account'].update(account_ref_sha256=v['origin']['account_ref_sha256'],cash='4200',equity='4200',peak_equity='4200')
    risk['native_limits']=replace(risk['native_limits'],allowed_accounts=frozenset({v['origin']['account_ref_sha256']}))
    data={row['kind']:row['data'] for row in v['origin']['reads']}
    risk['market'].update(observed_at_ms=min(data['mark']['time'],data['book']['time']))
    values['mandate']=replace(values['mandate'],issued_at=NOW+130002,expires_at=NOW+160000)
    return values


def test_first_gold_proposal_binds_the_original_book_in_the_signature_context(installed,tmp_path,monkeypatch):
    from live.execution import demo_pre_submit as prep
    v=installed;values=gold_values(v,tmp_path,monkeypatch)
    stores=v['stores'];result=prep.proposal(**values,dispatch=stores['dispatch'],recovery=stores['recovery'],oms=stores['oms'])
    assert result['state']['market_partition']['installed_record_sha256']==_digest(v['record'])
    assert 'position_context' not in result['state']
    assert result['state']['reservations']==v['state']['reservations']
    assert result['context']['recovery_sha256']==_digest(result['state'])


def signed_batch(v,tmp_path,monkeypatch):
    from io import StringIO
    import os
    import threading
    from live.execution import demo_pre_submit as prep
    from live.execution.demo_issuer_store import IssuerStore
    from tests.live.test_demo_issuer import policy_for
    from tools import demo_issuer as cli
    values=gold_values(v,tmp_path,monkeypatch);stores=v['stores']
    proof=prep.proposal(**values,dispatch=stores['dispatch'],recovery=stores['recovery'],oms=stores['oms'])
    policy=policy_for(proof,v['registry'])
    policy.update(schema='DEMO_GOLD_ISSUER_POLICY_V1',authority_id=v['key']['authority_id'],key_id=v['key']['key_id'],
        valid_from_ms=NOW+130002,valid_until_ms=NOW+160000)
    policy['scope']['symbol']='XAUUSDT'
    handoff=dict(schema='DEMO_EXECUTION_SESSION_PROPOSAL_V1',proposal_sha256=_digest(proof),
        proposal_path='/isolated/proposal.json',proposal=proof,status='WAITING_INDEPENDENT_GRANT',submission_enabled=False)
    issuer_root=tmp_path/'issuer-state';issuer_root.mkdir()
    log=IssuerStore(issuer_root/'issuance.sqlite3',create=True);log.close()
    monkeypatch.setattr(cli,'configuration',lambda _: (issuer_root,v['registry'],policy))
    monkeypatch.setattr(cli,'private_pipe',lambda _:None)
    monkeypatch.setattr(cli,'load_key',lambda *a:v['private'])
    monkeypatch.setattr(cli,'now_ms',lambda:NOW+140001)
    monkeypatch.setattr(cli,'_result',lambda root,registry,value:cli._retain(root/'results',value))
    class Output(StringIO):
        def fileno(self):return 1
    output=Output();reader,writer=os.pipe()
    body=json.dumps(handoff).encode()+b'\n'
    def send():
        try:
            remaining=memoryview(body)
            while remaining:remaining=remaining[os.write(writer,remaining):]
        finally:os.close(writer)
    sender=threading.Thread(target=send);sender.start()
    try:
        assert cli.run(source_commit='c'*40,input_fd=reader,output=output)==0,output.getvalue()
    finally:os.close(reader);sender.join(timeout=1)
    grant=json.loads(output.getvalue())
    batch=prep.prepare(json.dumps(grant['envelope']).encode(),verification_now_ms=NOW+140001,
        evidence_directory=stores['dispatch'].path.parent/'evidence',**values,
        dispatch=stores['dispatch'],recovery=stores['recovery'],oms=stores['oms'])
    return batch,values


def execution_values(v,values):
    return dict(**{key:v['stores'][key] for key in ('dispatch','recovery','oms')},
        **{key:values[key] for key in ('risk_inputs','mandate','attestations')})


def test_installed_partition_reaches_original_independent_signer_and_one_mock_send(installed,tmp_path,monkeypatch):
    from live.execution import demo_prepared_execution as executor
    from live.exchange import binance_demo_exact as transport
    v=installed;batch,values=signed_batch(v,tmp_path,monkeypatch);calls=[]
    monkeypatch.setattr(executor,'_require_state_custody',lambda *a:None)
    monkeypatch.setattr(executor,'now_ms',lambda:NOW+140002)
    monkeypatch.setattr(transport,'_credentials',lambda _:('isolated-fixture-key','isolated-fixture-secret'))
    monkeypatch.setattr(transport,'_http_once',lambda *args:(calls.append(1) or 200,json.dumps(dict(
        symbol='XAUUSDT',clientOrderId='QDgoldpartition001',orderId=21)).encode()))
    result=executor.execute(batch,**execution_values(v,values))
    assert result['status']=='WIRE_ATTEMPTS_RECORDED' and calls==[1]
    assert len(v['stores']['dispatch'].snapshot())==2 and len(v['stores']['recovery'].pending())==1
    assert v['stores']['dispatch'].snapshot()[0]==v['state']['reservations'][0]
    with pytest.raises(executor.DemoExecutionStopped):executor.execute(batch,**execution_values(v,values))
    assert calls==[1]


@pytest.mark.parametrize('damage',['activation_deleted','executor_deleted','executor_changed','issuer_changed_before_signing'])
def test_partition_change_stops_the_original_boundary_before_new_privileged_effects(installed,tmp_path,monkeypatch,damage):
    from live.execution import demo_prepared_execution as executor
    from live.exchange import binance_demo_exact as transport
    v=installed
    if damage=='issuer_changed_before_signing':
        bad=deepcopy(v['record']);bad['revalidation_sha256']='a'*64
        v['issuer'].write_text(json.dumps(bad))
        with pytest.raises(AssertionError):signed_batch(v,tmp_path,monkeypatch)
        assert len(v['stores']['dispatch'].snapshot())==1
        return
    batch,values=signed_batch(v,tmp_path,monkeypatch)
    if damage=='activation_deleted':v['activation'].unlink()
    elif damage=='executor_deleted':v['executor'].unlink()
    else:
        bad=deepcopy(v['record']);bad['revalidation_sha256']='a'*64
        v['executor'].write_text(json.dumps(bad))
    monkeypatch.setattr(executor,'_require_state_custody',lambda *a:None)
    monkeypatch.setattr(executor,'now_ms',lambda:NOW+140002)
    monkeypatch.setattr(transport,'_credentials',lambda *_:pytest.fail('credential access after partition changed'))
    monkeypatch.setattr(transport,'_http_once',lambda *_:pytest.fail('network after partition changed'))
    with pytest.raises(executor.DemoExecutionStopped):executor.execute(batch,**execution_values(v,values))
    assert v['stores']['dispatch'].wire_snapshot()==v['state']['wire']
    assert len(v['stores']['recovery'].pending())==1


def test_partition_change_after_credentials_keeps_original_unknown_send_and_no_http(installed,tmp_path,monkeypatch):
    from live.execution import demo_prepared_execution as executor
    from live.exchange import binance_demo_exact as transport
    v=installed;batch,values=signed_batch(v,tmp_path,monkeypatch);calls=[]
    monkeypatch.setattr(executor,'_require_state_custody',lambda *a:None)
    monkeypatch.setattr(executor,'now_ms',lambda:NOW+140002)
    def credentials(_):
        calls.append('credentials');v['activation'].unlink()
        return 'isolated-fixture-key','isolated-fixture-secret'
    monkeypatch.setattr(transport,'_credentials',credentials)
    monkeypatch.setattr(transport,'_http_once',lambda *_:pytest.fail('HTTP after changed partition'))
    result=executor.execute(batch,**execution_values(v,values))
    assert result['status']=='QUERY_RECOVERY_REQUIRED' and calls==['credentials']
    assert result['orders'][0]['outcome']['status']=='UNKNOWN'
    with pytest.raises(executor.DemoExecutionStopped):executor.execute(batch,**execution_values(v,values))
    assert calls==['credentials']


def test_real_mixed_signed_receipts_rebuild_each_original_collateral_without_reset(installed,tmp_path,monkeypatch):
    import hashlib
    from decimal import Decimal
    from live.execution import demo_prepared_execution as executor,demo_reported_recovery,demo_oms_reconciliation
    from live.exchange import binance_demo_exact as transport
    from live.execution.demo_pre_submit import _state,_retain
    from live.execution.demo_wire_protocol import outcome_for_query
    from tests.research.test_gold_demo_account_recovery import inputs
    from tests.research.test_binance_order_receipts import receipt
    v=installed;batch,values=signed_batch(v,tmp_path,monkeypatch);stores=v['stores']
    monkeypatch.setattr(executor,'_require_state_custody',lambda *a:None)
    monkeypatch.setattr(executor,'now_ms',lambda:NOW+140002)
    monkeypatch.setattr(transport,'_credentials',lambda _:('isolated-fixture-key','isolated-fixture-secret'))
    monkeypatch.setattr(transport,'_http_once',lambda *_:(503,b'{}'))
    result=executor.execute(batch,**execution_values(v,values))
    assert result['status']=='QUERY_RECOVERY_REQUIRED'
    material=tmp_path/'target-account-material';material.mkdir()
    original=inputs(material);account=shift(original,140000)
    account['opening']=deepcopy(v['origin']);account['system_orders']=deepcopy(values['orders'])
    for read in account['closing']['reads']:
        if read['account_ref_sha256'] is not None:read['account_ref_sha256']=v['origin']['account_ref_sha256']
        if read['kind'].startswith('account'):
            for asset in read['data']['assets']:
                if asset['asset']=='USDT':
                    for key in ('walletBalance','crossWalletBalance','availableBalance','marginBalance'):asset[key]='4199.6801'
    account['closing']['account_ref_sha256']=v['origin']['account_ref_sha256']
    for current,old in zip(account['windows'],original['windows']):
        current['account_ref_sha256']=v['origin']['account_ref_sha256']
        rows=[]
        for message in old['messages']:
            event=shift(json.loads(message['raw_utf8']),140000)
            if event['o']['i']==21:event['o'].update(c='QDgoldpartition001',f=values['orders'][0]['timeInForce'])
            rows.append(receipt(event,message['received_at_ms']+140000))
        current['messages']=rows
    for statement in account['statements']:
        for read in statement['reads']:
            if read.get('account_ref_sha256') is not None:read['account_ref_sha256']=v['origin']['account_ref_sha256']
    risk=values['risk_inputs']
    account['adv_notional']=str(Decimal(str(risk['market']['daily_volume_base']))*Decimal(str(risk['market']['reference_price'])))
    account['participation_rate']=str(risk['native_limits'].max_participation_rate)
    order=values['orders'][0]
    raw=dict(symbol='XAUUSDT',clientOrderId=order['newClientOrderId'],orderId=21,side=order['side'],
        origQty=order['quantity'],price=order['price'],positionSide='BOTH',reduceOnly=False,
        timeInForce=order['timeInForce'],type='LIMIT',status='FILLED',executedQty='0.002',
        time=NOW+140001,updateTime=NOW+150000)
    clock_raw=json.dumps(dict(serverTime=NOW+229999))
    clock=dict(schema='DEMO_ORIGINAL_QUERY_CLOCK_V1',requested_at_ms=NOW+229999,received_at_ms=NOW+229999,
        server_time_ms=NOW+229999,response_sha256=hashlib.sha256(clock_raw.encode()).hexdigest(),source_sha256='a'*64,raw_utf8=clock_raw)
    stores['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],client_order_id=order['newClientOrderId'],
        requested_at_ms=NOW+230000,outcome=outcome_for_query(order,200,json.dumps(raw).encode(),schema='DEMO_GOLD_EXACT_ORDER_BATCH_V1'),
        now_ms=NOW+230001,clock=clock)
    plan=demo_reported_recovery.prepare(batch,dispatch=stores['dispatch'],inputs=account,as_of_ms=NOW+240000)
    assert plan['account_report']['status']=='REPORTED_ACCOUNT_MATCHED',plan['account_report']['issues']
    outcome=demo_oms_reconciliation._project_resolution(plan,prepared=batch,dispatch=stores['dispatch'],recovery=stores['recovery'],
        oms=stores['oms'],event_store=v['events'],now_ms=NOW+240000)
    root=stores['dispatch'].path.parent
    _retain(root/'receipt-reconciliation/plans',plan);_retain(root/'receipt-reconciliation/outcomes',outcome)
    mixed=_state(stores['dispatch'],stores['recovery'],stores['oms'])
    from live.execution.demo_position_context import load_context
    old=load_context(mixed,symbol='BTCUSDC',account_ref=v['origin']['account_ref_sha256'],as_of_ms=NOW+250000)
    gold=load_context(mixed,symbol='XAUUSDT',account_ref=v['origin']['account_ref_sha256'],as_of_ms=NOW+250000)
    assert old==v['context'] and old['initial_equity']=='5000' and old['canonical_wallet']=='4999.6801'
    assert gold['initial_equity']=='4200' and gold['canonical_wallet']=='4199.6801' and gold['quantity']=='0'
    assert gold['sources'][0]['reservation_sha256']==batch['reservation_sha256']
    assert mixed['reservations'][0]==v['state']['reservations'][0]
    pairs=v['runtime'].context_reservations(state=mixed,context=gold,as_of_ms=NOW+250000)
    assert len(pairs)==1 and pairs[0][0]['record_hash']==batch['reservation_sha256']
    changed=deepcopy(mixed);changed['oms'][0]['quantity']+=0.001
    with pytest.raises(ValueError):v['runtime'].context_reservations(state=changed,context=gold,as_of_ms=NOW+250000)
    # Exercise the original next-day tool up to its public account capture.
    # Selection must contain the original gold proof, never the closed BTC book.
    from tools import demo_day_checkpoint as day
    class ProofsChecked(Exception):pass
    def capture(root,account_ref,source_commit,**kwargs):
        assert kwargs['opening']['symbol']=='XAUUSDT' and kwargs['system_orders']==batch['orders']
        raise ProofsChecked
    (root/'resident-recovery').mkdir(exist_ok=True)
    monkeypatch.setattr(day,'require_custody',lambda:root)
    monkeypatch.setattr(day,'fixed_account',lambda:(root,v['origin']['account_ref_sha256']))
    monkeypatch.setattr(day,'_open',lambda *a:dict(stores,stream=object()))
    monkeypatch.setattr(day,'now_ms',lambda:NOW+86400000+250000)
    monkeypatch.setattr(day,'capture_inputs',capture)
    monkeypatch.setattr(day,'_read',lambda path,*a:json.loads(path.read_bytes()))
    with pytest.raises(ProofsChecked):day.run(source_commit='c'*40,symbol='XAUUSDT')


def test_market_subset_requires_the_complete_installed_cut(installed):
    from tests.live.test_demo_market_partition import append_target
    v=installed
    for path in (v['executor'],v['issuer'],v['activation']):path.unlink()
    mixed=append_target(v['state'])
    with pytest.raises(ValueError):v['runtime'].context_reservations(state=mixed,context=v['context'],as_of_ms=NOW+140000)
