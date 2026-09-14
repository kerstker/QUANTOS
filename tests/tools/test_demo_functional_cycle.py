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


def runtime_fixture(tmp_path,monkeypatch,*,cash_ready=True):
    from tests.tools.test_demo_operator_setup import inputs,stop_bytes,NOW
    setup=tool.demo_operator_setup;origin,registry,private,spec=inputs(tmp_path)
    approval={'schema':'DEMO_OPERATOR_ENROLLMENT_V1','spec_sha256':setup.digest(spec),
        'operator_id':'isolated-human-operator','approved_at_ms':NOW,'purpose':setup.PURPOSE}
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
        def start(self,operation,payload):calls.append(operation);return self
        def poll(self):return 1 if not cash_ready else None
        def finish(self,*a):calls.append('cash_complete');return {'status':'CAPTURED'}
        def invoke(self,operation,payload=None,timeout=155):
            calls.append(operation)
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
        def close(self):calls.append('cleanup')
    monkeypatch.setattr(tool,'Runtime',Runtime)
    def marker(*a):
        if not cash_ready:raise FileNotFoundError()
        calls.append('opening_retained');return {'account_ref_sha256':spec['account_ref_sha256']}
    monkeypatch.setattr(tool,'_opening_marker',marker)
    def execute(**kwargs):calls.append('execute');return {'status':'EXECUTOR_RESULT_RECORDED'}
    monkeypatch.setattr(tool.demo_task,'run',execute)
    return state,calls


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


def test_read_only_recovery_works_after_policy_expiry_and_preserves_original_claim(tmp_path,monkeypatch):
    from live.execution import demo_authority
    from live.execution.demo_issuer_policy import current_key
    from tools import demo_issuer
    state,calls=runtime_fixture(tmp_path,monkeypatch)
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
