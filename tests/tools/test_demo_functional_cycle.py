"""A functional cycle needs actual fills, flat final exposure and matching cash."""
from tools import demo_functional_cycle as tool
import json

import pytest


def harness(positions,cash_status='MATCHED_REPORTED_INPUTS'):
    calls=[];remaining=iter(positions)
    def execute(task):calls.append(('execute',task));return {'status':'EXECUTOR_RESULT_RECORDED'}
    def settle():calls.append(('settle',None));return next(remaining)
    def reduce(position,index):
        task={'quantity':position['quantity'],'reduceOnly':True,'index':index}
        calls.append(('prepare_reduce',task));return task
    def finish():
        calls.append(('cash_finish',None))
        return {'status':cash_status,'final_quantity':positions[-1]['quantity'],'fill_count':positions[-1]['fill_count']}
    result=tool.drive(opening_task={'opening':True},execute=execute,settle=settle,prepare_reduce=reduce,
        cash_finish=finish,progress=lambda phase:calls.append(('phase',phase)))
    return result,calls


def test_partial_opening_reduces_only_confirmed_quantity_then_requires_cash():
    result,calls=harness([{'status':'SETTLED','quantity':'0.001','fill_count':1},
                         {'status':'SETTLED','quantity':'0','fill_count':2}])
    assert result['status']=='DEMO_CYCLE_COMPLETED'
    assert [v['quantity'] for k,v in calls if k=='prepare_reduce']==['0.001']
    assert len([1 for k,v in calls if k=='execute'])==2
    assert result['cash_reconciled'] and result['final_quantity']=='0'
    assert result['economic_authority'] is False


def test_unknown_order_stops_new_submissions_and_retains_cash_capture():
    result,calls=harness([{'status':'UNRESOLVED','quantity':None,'fill_count':None}])
    assert result['status']=='RECOVERY_REQUIRED'
    assert len([1 for k,v in calls if k=='execute'])==1
    assert not any(k=='prepare_reduce' for k,v in calls)
    assert any(k=='cash_finish' for k,v in calls)
    assert result['cash_reconciled'] is False


def test_expired_unfilled_order_does_not_count_as_a_completed_trade():
    result,calls=harness([{'status':'SETTLED','quantity':'0','fill_count':0}])
    assert result['status']=='NO_TRADING_EVIDENCE' and result['cash_reconciled'] is False
    assert len([1 for k,v in calls if k=='execute'])==1


def test_flat_position_without_matching_cash_is_not_closed():
    result,calls=harness([{'status':'SETTLED','quantity':'0.002','fill_count':1},
                         {'status':'SETTLED','quantity':'0','fill_count':2}],cash_status='UNRESOLVED')
    assert result['status']=='CASH_RECONCILIATION_REQUIRED' and result['cash_reconciled'] is False


def test_closing_attempts_are_finite_and_remaining_exposure_is_reported():
    result,calls=harness([{'status':'SETTLED','quantity':'0.003','fill_count':1},
                         {'status':'SETTLED','quantity':'0.002','fill_count':2},
                         {'status':'SETTLED','quantity':'0.001','fill_count':3}])
    assert result['status']=='REDUCTION_INCOMPLETE'
    assert result['final_quantity']=='0.001' and result['orders_attempted']==3


def test_existing_fills_or_changed_final_cash_position_cannot_complete_new_cycle():
    result,_=harness([{'status':'SETTLED','quantity':'0','fill_count':2}])
    assert result['status']!='DEMO_CYCLE_COMPLETED'
    calls=[]
    result=tool.drive(opening_task={},execute=lambda _: {'status':'EXECUTOR_RESULT_RECORDED'},
        settle=iter([{'status':'SETTLED','quantity':'0.001','fill_count':1},
                     {'status':'SETTLED','quantity':'0','fill_count':2}]).__next__,
        prepare_reduce=lambda *a: {},cash_finish=lambda: {'status':'MATCHED_REPORTED_INPUTS',
            'final_quantity':'0.001','fill_count':2},progress=calls.append)
    assert result['status']!='DEMO_CYCLE_COMPLETED' and not result['cash_reconciled']


def test_lost_submit_response_recovers_original_ids_before_reducing():
    calls=[];positions=iter([{'status':'SETTLED','quantity':'0.001','fill_count':1},
                           {'status':'SETTLED','quantity':'0','fill_count':2}])
    def execute(task):
        calls.append(task)
        if len(calls)==1:raise OSError('isolated lost response')
        return {'status':'EXECUTOR_RESULT_RECORDED'}
    result=tool.drive(opening_task={'opening':True},execute=execute,settle=positions.__next__,
        prepare_reduce=lambda p,i:{'reduceOnly':True,'quantity':p['quantity']},
        cash_finish=lambda:{'status':'MATCHED_REPORTED_INPUTS','final_quantity':'0','fill_count':2},progress=lambda _:None)
    assert result['status']=='DEMO_CYCLE_COMPLETED'
    assert calls==[{'opening':True},{'reduceOnly':True,'quantity':'0.001'}]


def runtime_fixture(tmp_path,monkeypatch,*,cash_ready=True,symbol='BTCUSDC',research=False,delivery_fail=False):
    from tests.tools.test_demo_operator_setup import inputs,stop_bytes,NOW
    setup=tool.demo_operator_setup;origin,registry,private,spec=inputs(tmp_path)
    if symbol=='XAUUSDT':
        from tests.tools.test_gold_demo_execution_origin import capture
        origin,_=capture()
        from tests.research.test_gold_feedback_trigger import feedback
        spec=setup.propose(origin=origin,registry=registry,source_commit='c'*40,now_ms=NOW,symbol=symbol,
            research_identity=feedback()['research_identity'] if research else None)
    approval={'schema':'DEMO_OPERATOR_ENROLLMENT_V1','spec_sha256':setup.digest(spec),
        'operator_id':'isolated-human-operator','approved_at_ms':NOW,
        'purpose':'DEMO_GOLD_FUNCTIONAL_TEST_ONLY' if symbol=='XAUUSDT' else setup.PURPOSE}
    manifest,files=setup.assemble(spec=spec,approval=approval,origin=origin,registry=registry,
        private_key=private,stop_image=stop_bytes(tmp_path),now_ms=NOW)
    stage=tmp_path/'setup';stage.mkdir();state=tmp_path/'controller';state.mkdir()
    (stage/'spec.json').write_bytes(setup.canonical(spec))
    installed={'status':'INSTALLED_PENDING_CYCLE','source_commit':'c'*40,
        'spec_sha256':setup.digest(spec),'task_sha256':manifest['tasks'][0]}
    (stage/'result.json').write_bytes(setup.canonical(installed));(state/'tasks').mkdir()
    for name,body in files.items():
        if name==manifest['tasks'][0]+'.json':(state/'tasks'/name).write_bytes(body)
    monkeypatch.setattr(tool,'SETUP',stage);monkeypatch.setattr(tool.supervisor,'STATE',state)
    monkeypatch.setattr(tool.supervisor,'now_ms',lambda:NOW+10)
    monkeypatch.setattr(tool.supervisor,'configuration',lambda _: (json.loads(files['registry.json']),json.loads(files['policy.json'])))
    monkeypatch.setattr(tool.supervisor,'_root_private_directory',lambda _:None)
    monkeypatch.setattr(tool.supervisor,'read_document',lambda p,h:(p/(h+'.json')).read_bytes())
    from live.execution import demo_authority
    monkeypatch.setattr(demo_authority,'_read_root_owned_json',lambda p:p.read_bytes())
    monkeypatch.setattr(tool.supervisor,'_read_root_owned_json',lambda p:p.read_bytes())
    def write(path,body):
        with path.open('xb') as stream:stream.write(body)
    monkeypatch.setattr('tools.demo_operating_bundle.write_new',write)
    calls=[];task=json.loads(files[manifest['tasks'][0]+'.json'])
    class Runtime:
        def __init__(self,*a):self.count=0
        def start(self,operation,payload):
            assert payload.get('symbol','BTCUSDC')==symbol
            calls.append(operation);return self
        def poll(self):return 1 if not cash_ready else None
        def finish(self,*a):calls.append('cash_complete');return {'status':'CAPTURED'}
        def invoke(self,operation,payload=None,timeout=155):
            calls.append(operation)
            if operation=='feedback':
                assert payload['research_identity']==spec['research_identity']
                return {'status':'ROUNDTRIP_FEEDBACK_RETAINED','feedback_sha256':'a'*64,'artifact_sha256':'b'*64}
            if operation=='settle':
                self.count+=1
                return {'status':'SETTLED','quantity':'0.002' if self.count==1 else '0','fill_count':self.count}
            if operation=='close-cash':return {'status':'CLOSE_REQUESTED'}
            if operation=='resume-check':return {'status':'ORIGINAL_OPENING_RETAINED'}
            if operation=='resume-cash':return {'status':'CAPTURED'}
            if operation=='reduce':
                value=json.loads(setup.canonical(task));value['orders'][0].update(side='SELL',reduceOnly=True,newClientOrderId='QDclose001')
                return {'status':'REDUCTION_TASK_PREPARED','task':value}
            return {'status':'MATCHED_REPORTED_INPUTS','final_quantity':'0','fill_count':2}
        def deliver_feedback(self,exported):
            calls.append('deliver_feedback')
            if delivery_fail:raise ValueError('private diagnostic')
            return {'status':'RESEARCH_FEEDBACK_DELIVERED','feedback_sha256':exported['feedback_sha256'],
                'source_verified':False,'submission_enabled':False,'economic_authority':False}
        def close(self):calls.append('cleanup')
    monkeypatch.setattr(tool,'Runtime',Runtime)
    def marker(*a):
        if not cash_ready:raise FileNotFoundError()
        calls.append('opening_retained');return {'account_ref_sha256':spec['account_ref_sha256']}
    monkeypatch.setattr(tool,'_opening_marker',marker)
    def execute(**kwargs):calls.append('execute');return {'status':'EXECUTOR_RESULT_RECORDED'}
    monkeypatch.setattr(tool.demo_task,'run',execute)
    return state,calls


def test_gold_installed_scope_reaches_original_cycle_with_exact_worker_market(tmp_path,monkeypatch):
    state,calls=runtime_fixture(tmp_path,monkeypatch,symbol='XAUUSDT')
    assert tool.main(['--source-commit','c'*40])==0
    result=json.loads((state/'functional-cycle/result.json').read_bytes())
    assert result['status']=='DEMO_CYCLE_COMPLETED' and calls.count('execute')==2


def test_main_retains_first_cash_then_tasks_and_final_view_and_cannot_repeat(tmp_path,monkeypatch):
    state,calls=runtime_fixture(tmp_path,monkeypatch)
    assert tool.main(['--source-commit','c'*40])==0
    assert calls==['cash','opening_retained','execute','settle','reduce','execute','settle',
        'close-cash','cash_complete','reconcile','cleanup']
    root=state/'functional-cycle';before={p.name:p.read_bytes() for p in (root/'views').iterdir()}
    assert json.loads((root/'result.json').read_bytes())['status']=='DEMO_CYCLE_COMPLETED'
    assert tool.main(['--source-commit','c'*40])==3
    assert calls.count('execute')==2
    assert {p.name:p.read_bytes() for p in (root/'views').iterdir()}==before


def test_missing_opening_wallet_stops_before_first_task_and_publishes_failure(tmp_path,monkeypatch):
    state,calls=runtime_fixture(tmp_path,monkeypatch,cash_ready=False)
    assert tool.main(['--source-commit','c'*40])==3
    assert 'execute' not in calls and calls[-1]=='cleanup'
    value=tool.demo_functional_status.load_latest(state/'functional-cycle/views')
    assert value['phase']=='CYCLE_NOT_COMPLETED' and not value['cash_reconciled']


def test_real_controller_restart_uses_retained_attempt_and_original_cash_prefix(tmp_path,monkeypatch):
    state,calls=runtime_fixture(tmp_path,monkeypatch)
    tasks=[]
    def crash(**kwargs):
        tasks.append(kwargs['task_sha256'])
        raise KeyboardInterrupt()
    monkeypatch.setattr(tool.demo_task,'run',crash)
    with pytest.raises(KeyboardInterrupt):tool.main(['--source-commit','c'*40])
    area=state/'functional-cycle'
    original=(area/'attempts/0.json').read_bytes()
    claim=(area/'claim.json').read_bytes()
    assert not (area/'result.json').exists()
    monkeypatch.setattr(tool.demo_task,'run',lambda **kwargs:tasks.append(kwargs['task_sha256']))
    assert tool.main(['--source-commit','c'*40,'--resume'])==0
    assert len(tasks)==2 and tasks[0]!=tasks[1]
    assert calls.count('cash')==1 and calls.count('resume-cash')==1
    assert (area/'attempts/0.json').read_bytes()==original and (area/'claim.json').read_bytes()==claim
    assert len(list((area/'recovery-results').iterdir()))==1
    assert tool.demo_functional_status.load_latest(area/'views')['phase']=='DEMO_CYCLE_COMPLETED'


def test_expired_authorization_stops_resume_before_worker_or_new_claim(tmp_path,monkeypatch):
    state,calls=runtime_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(tool.supervisor,'configuration',lambda _:(_ for _ in ()).throw(ValueError('ISSUER_POLICY_EXPIRED')))
    assert tool.main(['--source-commit','c'*40,'--resume'])==3
    assert calls==[] and not (state/'functional-cycle').exists()


@pytest.mark.parametrize('symbol',['BTCUSDC','XAUUSDT'])
def test_read_only_recovery_works_after_policy_expiry_and_preserves_original_claim(tmp_path,monkeypatch,symbol):
    from live.execution import demo_authority
    from live.execution.demo_issuer_policy import current_key
    from tools import demo_issuer
    state,calls=runtime_fixture(tmp_path,monkeypatch,symbol=symbol,research=symbol=='XAUUSDT')
    def crash(**kwargs):raise KeyboardInterrupt()
    monkeypatch.setattr(tool.demo_task,'run',crash)
    with pytest.raises(KeyboardInterrupt):tool.main(['--source-commit','c'*40])
    registry,policy=tool.supervisor.configuration('c'*40)
    expired=policy['valid_until_ms']+1000
    with pytest.raises(ValueError):current_key(policy,registry,expired)
    records={str(tool.supervisor.EXECUTOR_REGISTRY):registry,str(tool.supervisor.ISSUER_REGISTRY):registry,
        str(tool.supervisor.ISSUER_POLICY):policy}
    def read(path):return json.dumps(records[str(path)]).encode() if str(path) in records else path.read_bytes()
    monkeypatch.setattr(demo_authority,'_read_root_owned_json',read)
    monkeypatch.setattr(tool.supervisor,'_read_root_owned_json',read)
    monkeypatch.setattr(demo_issuer,'verify_source',lambda _:None)
    monkeypatch.setattr(tool.os,'geteuid',lambda:0)
    monkeypatch.setattr(tool.supervisor,'now_ms',lambda:expired)
    monkeypatch.setattr(tool.supervisor,'configuration',lambda _:pytest.fail('active issuer configuration during recovery'))
    monkeypatch.setattr(tool.demo_task,'run',lambda **kwargs:pytest.fail('send during read-only recovery'))
    before=(state/'functional-cycle/claim.json').read_bytes();calls.clear()
    assert tool.main(['--source-commit','d'*40,'--recover'])==2
    report=tool.demo_functional_status.load_latest(state/'functional-cycle/views')
    assert report['phase']=='REDUCTION_AUTHORIZATION_REQUIRED' and report['final_quantity']=='0.002'
    assert calls==['settle','reconcile','cleanup']
    assert (state/'functional-cycle/claim.json').read_bytes()==before
    assert len(list((state/'functional-cycle/attempts').iterdir()))==1


@pytest.mark.parametrize('failure',[None,'incomplete','identity','export','delivery'])
def test_feedback_handoff_requires_completed_bound_cycle(failure):
    from tests.research.test_gold_feedback_trigger import feedback
    identity=feedback()['research_identity'];spec={'schema':tool.demo_operator_setup.GOLD_SCHEMA,'research_identity':identity}
    template={'profile':{'strategy':{'strategy_id':identity['strategy_id']}}}
    result={'status':'DEMO_CYCLE_COMPLETED','cash_reconciled':True}
    if failure=='incomplete':result['cash_reconciled']=False
    if failure=='identity':template['profile']['strategy']['strategy_id']='wrong'
    calls=[]
    class Runtime:
        def invoke(self,operation,payload):
            calls.append(operation);assert payload=={'research_identity':identity}
            return {'status':'UNRESOLVED' if failure=='export' else 'ROUNDTRIP_FEEDBACK_RETAINED',
                'feedback_sha256':'a'*64,'artifact_sha256':'b'*64}
        def deliver_feedback(self,exported):
            calls.append('deliver')
            if failure=='delivery':raise ValueError('private diagnostic')
            return {'status':'RESEARCH_FEEDBACK_DELIVERED','feedback_sha256':'a'*64,
                'submission_enabled':False,'economic_authority':False,'source_verified':False}
    if failure in ('incomplete','identity','export','delivery'):
        with pytest.raises(ValueError):tool.complete_feedback(spec,template,result,Runtime(),now_ms=200)
        assert calls==([] if failure in ('incomplete','identity') else ['feedback'] if failure=='export' else ['feedback','deliver'])
    else:
        value=tool.complete_feedback(spec,template,result,Runtime(),now_ms=200)
        assert value['status']=='RESEARCH_FEEDBACK_DELIVERED' and calls==['feedback','deliver']


@pytest.mark.parametrize('failure',[False,True])
def test_controller_delivers_after_cash_and_records_failure_without_new_orders(tmp_path,monkeypatch,failure):
    state,calls=runtime_fixture(tmp_path,monkeypatch,symbol='XAUUSDT',research=True,delivery_fail=failure)
    assert tool.main(['--source-commit','c'*40])==(2 if failure else 0)
    result=json.loads((state/'functional-cycle/result.json').read_bytes())
    assert result['status']=='DEMO_CYCLE_COMPLETED' and result['cash_reconciled']
    assert result['research_feedback']['status']==('RESEARCH_FEEDBACK_DELIVERY_FAILED' if failure else 'RESEARCH_FEEDBACK_DELIVERED')
    assert calls.index('reconcile')<calls.index('feedback')<calls.index('deliver_feedback')
    assert calls.count('execute')==2 and len(list((state/'functional-cycle/feedback-results').iterdir()))==1
    assert 'private diagnostic' not in json.dumps(result)


@pytest.mark.parametrize('failure',[False,True])
def test_feedback_only_rechecks_completed_cycle_without_execution(tmp_path,monkeypatch,failure,capsys):
    from tools import demo_issuer
    from live.execution import demo_authority
    state,calls=runtime_fixture(tmp_path,monkeypatch,symbol='XAUUSDT',research=True,delivery_fail=failure)
    assert tool.main(['--source-commit','c'*40])==(2 if failure else 0)
    original=(state/'functional-cycle/result.json').read_bytes()
    registry,policy=tool.supervisor.configuration('c'*40)
    registry_path=tmp_path/'registry.json';registry_path.write_text(json.dumps(registry))
    policy_path=tmp_path/'policy.json';policy_path.write_text(json.dumps(policy))
    monkeypatch.setattr(tool.supervisor,'EXECUTOR_REGISTRY',registry_path)
    monkeypatch.setattr(tool.supervisor,'ISSUER_REGISTRY',registry_path)
    monkeypatch.setattr(tool.supervisor,'ISSUER_POLICY',policy_path)
    monkeypatch.setattr(demo_issuer,'verify_source',lambda _:None)
    monkeypatch.setattr(tool.os,'geteuid',lambda:0)
    monkeypatch.setattr(tool.supervisor,'configuration',lambda _:pytest.fail('feedback must not require new trading authorization'))
    monkeypatch.setattr(tool.demo_task,'run',lambda **kw:pytest.fail('feedback must never execute orders'))
    # Expired execution approval cannot authorize another order, but does not
    # prevent rechecking and delivering historical technical feedback.
    monkeypatch.setattr(tool.supervisor,'now_ms',lambda:policy['valid_until_ms']+1)
    if not failure:
        calls.clear()
        history=state/'functional-cycle/feedback-results'
        before={p.name:p.read_bytes() for p in history.iterdir()}
        assert tool.main(['--source-commit','c'*40,'--feedback-pending'])==0
        assert calls==[] and {p.name:p.read_bytes() for p in history.iterdir()}==before
        assert 'RESEARCH_FEEDBACK_ALREADY_DELIVERED' in capsys.readouterr().out
    calls.clear()
    assert tool.main(['--source-commit','c'*40,'--feedback-only'])==(2 if failure else 0)
    assert calls==['feedback','deliver_feedback','cleanup']
    assert (state/'functional-cycle/result.json').read_bytes()==original
    assert 'private diagnostic' not in capsys.readouterr().out
    incomplete=json.loads(original);incomplete['cash_reconciled']=False;incomplete['status']='RECOVERY_REQUIRED'
    (state/'functional-cycle/result.json').write_text(json.dumps(incomplete))
    preserved=(state/'functional-cycle/result.json').read_bytes()
    recovered=dict(json.loads(original),observed_at_ms=incomplete['observed_at_ms']+1,
        mode='RECOVERY_ONLY',original_source_commit='c'*40)
    tool._retain(state/'functional-cycle/recovery-results',recovered);calls.clear()
    assert tool.main(['--source-commit','c'*40,'--feedback-only'])==(2 if failure else 0)
    assert calls==['feedback','deliver_feedback','cleanup']
    assert (state/'functional-cycle/result.json').read_bytes()==preserved
    tool._retain(state/'functional-cycle/recovery-results',dict(incomplete,observed_at_ms=policy['valid_until_ms']))
    calls.clear()
    assert tool.main(['--source-commit','c'*40,'--feedback-only'])==3
    assert calls==[]


@pytest.mark.parametrize('damage',[None,'late_failure','wrong_cycle','corrupt','tie'])
def test_feedback_uses_latest_verified_recovery_result_without_rewriting_initial(tmp_path,monkeypatch,damage):
    from live.execution import demo_authority
    monkeypatch.setattr(demo_authority,'_read_root_owned_json',lambda p:p.read_bytes())
    monkeypatch.setattr(tool.supervisor,'_root_private_directory',lambda p:None)
    original={'schema':'DEMO_FUNCTIONAL_CYCLE_RESULT_V1','status':'RECOVERY_REQUIRED',
        'cycle_sha256':'a'*64,'source_commit':'c'*40,'observed_at_ms':100,
        'cash_reconciled':False,'final_quantity':None,'fill_count':None,'orders_attempted':2,
        'cash_status':'UNRESOLVED','economic_authority':False,'retry_submission':False}
    path=tmp_path/'result.json';path.write_text(json.dumps(original));before=path.read_bytes()
    recovered=dict(original,status='DEMO_CYCLE_COMPLETED',observed_at_ms=200,cash_reconciled=True,
        final_quantity='0',fill_count=2,cash_status='MATCHED_REPORTED_INPUTS',mode='RECOVERY_ONLY',original_source_commit='c'*40)
    if damage=='wrong_cycle':recovered['cycle_sha256']='b'*64
    saved=tool._retain(tmp_path/'recovery-results',recovered)
    if damage=='corrupt':saved.write_text('{}')
    if damage in ('late_failure','tie'):
        tool._retain(tmp_path/'recovery-results',dict(original,observed_at_ms=300 if damage=='late_failure' else 200))
    if damage:
        with pytest.raises(ValueError):tool.feedback_completion(tmp_path,cycle='a'*64,source_commit='c'*40,now_ms=400)
    else:
        assert tool.feedback_completion(tmp_path,cycle='a'*64,source_commit='c'*40,now_ms=400)==recovered
    assert path.read_bytes()==before


def test_background_receipt_requires_latest_matching_completion(tmp_path,monkeypatch):
    from live.execution import demo_authority
    monkeypatch.setattr(demo_authority,'_read_root_owned_json',lambda p:p.read_bytes())
    monkeypatch.setattr(tool.supervisor,'_root_private_directory',lambda p:None)
    identity='a'*64;cycle='b'*64;source='c'*40
    receipt={'status':'RESEARCH_FEEDBACK_DELIVERED','feedback_sha256':'d'*64,'cycle_sha256':cycle,
        'source_commit':source,'completion_sha256':identity,'observed_at_ms':100,
        'source_verified':False,'economic_authority':False,'submission_enabled':False}
    assert tool.delivered_feedback(tmp_path,completion=identity,cycle=cycle,source_commit=source,now_ms=300) is None
    tool._retain(tmp_path,receipt)
    assert tool.delivered_feedback(tmp_path,completion=identity,cycle=cycle,source_commit=source,now_ms=300)==receipt
    assert tool.delivered_feedback(tmp_path,completion='f'*64,cycle=cycle,source_commit=source,now_ms=300) is None
    tool._retain(tmp_path,dict(receipt,status='RESEARCH_FEEDBACK_DELIVERY_FAILED',observed_at_ms=200))
    assert tool.delivered_feedback(tmp_path,completion=identity,cycle=cycle,source_commit=source,now_ms=300) is None
