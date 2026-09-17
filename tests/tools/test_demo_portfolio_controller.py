import json
import os
import pytest
from copy import deepcopy

from tools import demo_portfolio_controller as controller
from tools import demo_portfolio_checkpoint as checkpoint
from tests.live.test_demo_request_inputs import inputs
from tests.live.test_demo_portfolio_task import declarations
from live.execution import demo_portfolio_task
from live.execution.demo_batch_risk import _digest


def test_controller_retains_exact_task_and_only_original_executor_can_claim_it(tmp_path,monkeypatch):
    kwargs,opened,log=inputs(tmp_path,monkeypatch)
    try:
        kwargs.pop('orders');allocation,forecast,funding=declarations(kwargs,.01)
        binding={'schema':'DEMO_PORTFOLIO_SOURCE_BINDING_V1','attempt':1,
            'freeze_sha256':'a'*64,'observer_source_sha256':'b'*64}
        allocation.update(schema='DEMO_PORTFOLIO_ALLOCATION_V2',source_binding_sha256=_digest(binding))
        profile=kwargs['profile'];policy=kwargs['issuer_policy'];old=profile['strategy']['strategy_id']
        sid=demo_portfolio_task.strategy_id(allocation)
        profile['strategy']['strategy_id']=sid;profile['attestations'][sid]=profile['attestations'].pop(old)
        profile['attestations'][sid]['strategy_id']=sid
        policy['bindings'].update(strategy_id=sid,strategy_approval_sha256=_digest(profile['strategy']),
            validation_sha256=_digest(profile['attestations']))
        policy.update(valid_from_ms=kwargs['now_ms'],valid_until_ms=kwargs['now_ms']+3600000,max_orders=3,max_grants=3)
        built=demo_portfolio_task.build(allocation=allocation,forecast=forecast,funding=funding,**kwargs)
        config={'schema':'DEMO_PORTFOLIO_RUNTIME_V1','source_commit':'c'*40,'research_root':str(tmp_path),
            'allocation':allocation,'binding':binding,'kill_switch':kwargs['kill_state'].model_dump(mode='json')}
        profile=kwargs['profile'];policy=kwargs['issuer_policy'];now=kwargs['now_ms']
        state=tmp_path/'controller';state.mkdir();(state/'task-claims').mkdir()
        monkeypatch.setattr(controller.supervisor,'STATE',state)
        monkeypatch.setattr(controller,'configuration',lambda _:({},policy,profile,config))
        monkeypatch.setattr(controller.supervisor,'now_ms',lambda:now)
        monkeypatch.setattr(controller.supervisor,'_root_private_directory',lambda _:None)
        monkeypatch.setattr(controller.supervisor,'_read_root_owned_json',lambda p:p.read_bytes())
        monkeypatch.setattr(controller.sources,'load_forecast',lambda **k:forecast)
        monkeypatch.setattr(controller.os,'geteuid',lambda:0)
        writer=checkpoint.bundle.write_new
        monkeypatch.setattr(checkpoint.bundle,'write_new',lambda p,b:writer(p,b,uid=os.getuid(),gid=os.getgid()))
        prepared=[];sent=[]
        def prepare(bundle,**kwargs):
            prepared.append(1)
            return {'schema':'DEMO_PORTFOLIO_PREPARATION_V1','source_commit':'c'*40,
                'bundle_sha256':_digest(bundle),'status':built['status'],'build':built,
                'submission_enabled':False,'economic_authority':False}
        monkeypatch.setattr(controller,'prepare',prepare)
        def execute(**kwargs):
            sent.append(kwargs['task_sha256'])
            (state/'task-claims'/f"{kwargs['task_sha256']}.json").write_text('{}')
            return {'status':'EXECUTOR_RESULT_RECORDED'}
        monkeypatch.setattr(controller.task_tool,'run',execute)
        first=controller.run(source_commit='c'*40)
        assert first['status']=='TASK_READY' and sent==[]
        second=controller.run(source_commit='c'*40,execute=True)
        assert second['status']=='EXECUTION_ATTEMPT_RECORDED' and sent==[built['task_sha256']]
        third=controller.run(source_commit='c'*40,execute=True)
        assert third['status']=='QUERY_ONLY' and prepared==[1] and len(sent)==1
        assert not third['cash_reconciled'] and not third['economic_authority']
        monkeypatch.setattr(controller,'reconciliation_configuration',lambda _:({},policy,profile,config))
        monkeypatch.setattr(controller.sources,'load_forecast',lambda **k:pytest.fail('cash recovery read a forecast'))
        def reconcile(bundle,**kwargs):
            assert kwargs['operation']=='reconcile'
            return {'status':'MATCHED_REPORTED_INPUTS','cash_reconciled':True,
                'cash':{'status':'MATCHED_REPORTED_INPUTS','final_quantity':'0','wallet_residual':'0',
                    'report_sha256':'e'*64,'economic_authority':False}}
        now=policy['valid_until_ms']-240000
        close_calls=[]
        def closing(bundle,**kwargs):
            if kwargs['operation']=='reconcile':return reconcile(bundle,**kwargs)
            assert kwargs['operation']=='close' and bundle['forecast'] is None
            index=bundle['close_index'];close_calls.append(index)
            closing_build={'schema':'DEMO_PORTFOLIO_CLOSE_BUILD_V1','allocation_sha256':_digest(allocation),
                'policy_sha256':_digest(policy),'index':index,'submission_enabled':False,'economic_authority':False,
                'status':'ALREADY_FLAT','current_quantity':'0','task':None}
            if index==1:
                assert bundle['previous_close'] is None
                task=deepcopy(built['task']);task.update(valid_from_ms=now,valid_until_ms=now+30000)
                task['orders'][0].update(side='SELL',reduceOnly=True,
                    newClientOrderId='QDC'+checkpoint.close_identity(allocation,policy,index)[:30])
                request=deepcopy(built['request_build']);request['request']['orders']=deepcopy(task['orders'])
                closing_build.update(status='CLOSE_TASK_PREPARED',current_quantity='0.002',task=task,
                    task_sha256=_digest(task),request_build=request)
            else:assert bundle['previous_close']['orders'][0]['reduceOnly'] is True
            return {'status':closing_build['status'],'build':closing_build}
        monkeypatch.setattr(controller,'prepare',closing)
        assert controller.run(source_commit='c'*40,tick=True)['status']=='EXECUTION_ATTEMPT_RECORDED'
        assert len(sent)==2 and close_calls==[1]
        assert controller.run(source_commit='c'*40,execute=True)['status']=='CLOSING_REQUIRED'
        assert controller.run_cycle(source_commit='c'*40)['status']=='CASH_RECONCILED'
        assert close_calls==[1,2] and len(sent)==2
        now=policy['valid_until_ms']+1
        monkeypatch.setattr(controller,'prepare',reconcile)
        result=controller.run(source_commit='c'*40,reconcile=True)
        assert result['status']=='CASH_RECONCILED' and result['cash_reconciled'] is True and len(sent)==2
        assert controller.run(source_commit='c'*40,reconcile=True,execute=True)['status']=='INPUTS_UNRESOLVED'
        assert controller.run(source_commit='c'*40,tick=True,expected_policy_sha256='f'*64)['status']=='INPUTS_UNRESOLVED'
        # A policy crossing midnight must enter closing before the UTC boundary,
        # even though its operator approval still has time remaining.
        day_end=(policy['valid_from_ms']//86400000+1)*86400000
        policy.update(valid_from_ms=day_end-1800000,valid_until_ms=day_end+1800000)
        now=day_end-240000
        assert controller.run(source_commit='c'*40,execute=True)['status']=='CLOSING_REQUIRED'
    finally:
        log.close()
        for store in opened:store.close()
