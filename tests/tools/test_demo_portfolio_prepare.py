import json
import os
from concurrent.futures import ThreadPoolExecutor

from tools import demo_portfolio_prepare as worker
from tools import demo_request_prepare as common
from tests.live.test_demo_request_inputs import inputs
from tests.live.test_demo_portfolio_task import declarations
from tests.tools.test_demo_portfolio_sources import receipt
from live.execution.demo_batch_risk import _digest
from live.execution.demo_portfolio_task import strategy_id
from services.research.ai_factory import HOUR


def test_executor_pipe_prepares_bound_portfolio_and_rejects_changed_config_before_capture(tmp_path,monkeypatch):
    kwargs,opened,log=inputs(tmp_path,monkeypatch)
    try:
        kwargs.pop('orders');allocation,forecast,_=declarations(kwargs,.01)
        binding={'schema':'DEMO_PORTFOLIO_SOURCE_BINDING_V1','attempt':1,
            'freeze_sha256':'a'*64,'observer_source_sha256':'b'*64}
        allocation.update(schema='DEMO_PORTFOLIO_ALLOCATION_V2',source_binding_sha256=_digest(binding))
        profile=kwargs['profile'];policy=kwargs['issuer_policy'];old=profile['strategy']['strategy_id'];sid=strategy_id(allocation)
        profile['strategy']['strategy_id']=sid;profile['attestations'][sid]=profile['attestations'].pop(old)
        profile['attestations'][sid]['strategy_id']=sid
        policy['bindings'].update(strategy_id=sid,strategy_approval_sha256=_digest(profile['strategy']),
            validation_sha256=_digest(profile['attestations']))
        bundle={'allocation':allocation,'binding':binding,'forecast':forecast,'profile':profile,'policy':policy,
            'kill_switch':kwargs['kill_state'].model_dump(mode='json')}
        now=kwargs['now_ms'];last=(now-1)//(8*HOUR)*(8*HOUR)
        funds=receipt(worker.sources.funding_path(now-2),[{'symbol':'BTCUSDC','fundingTime':last-i*8*HOUR,
            'fundingRate':'0','markPrice':'60000'} for i in (2,1,0)],now-1)
        captures=[]
        monkeypatch.setattr(common,'verify_source',lambda _:None)
        monkeypatch.setattr(common,'fixed_account',lambda:(tmp_path,policy['account_ref_sha256']))
        monkeypatch.setattr(common.os,'getegid',lambda:2001);monkeypatch.setattr(common.os,'getgroups',lambda:[2001])
        monkeypatch.setattr(common,'private_pipe',lambda _:None)
        monkeypatch.setattr(common,'now_ms',lambda:now)
        monkeypatch.setattr(common,'_open',lambda *a:{k:v for k,v in zip(('dispatch','recovery','oms'),opened)})
        monkeypatch.setattr(common,'capture_inputs',lambda **k:(captures.append(1) or (kwargs['origin'],kwargs['market_inputs'])))
        monkeypatch.setattr(worker.sources,'capture_funding',lambda:funds)
        def call(operation='prepare'):
            read,write=os.pipe();out_read,out_write=os.pipe()
            def drain():
                with os.fdopen(out_read,'rb') as channel:return channel.read()
            try:
                os.write(write,json.dumps(bundle).encode()+b'\n');os.close(write)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future=pool.submit(drain)
                    with os.fdopen(out_write,'w') as output:
                        code=worker.run(source_commit='c'*40,input_fd=read,output=output,operation=operation)
                    return code,json.loads(future.result(timeout=3))
            finally:os.close(read)
        code,value=call()
        assert code==0 and value['status']=='TASK_PREPARED'
        assert value['build']['request_build']['precheck_status']=='RISK_CHECKS_PASSED'
        assert value['build']['task']['orders'][0]['side']=='BUY'
        allocation['capital']='600'
        code,value=call()
        assert code==3 and value['status']=='INPUTS_UNRESOLVED' and captures==[1]
        allocation['capital']='500'
        now=policy['valid_until_ms']+1
        monkeypatch.setattr(worker.functional,'settle',lambda *a,**k:{'status':'SETTLED','quantity':'0','fill_count':2})
        cash_calls=[]
        def cash(*a,**k):
            cash_calls.append(k['allocation'])
            return {'status':'MATCHED_REPORTED_INPUTS','final_quantity':'0','fill_count':2,
                'wallet_residual':'0','report_sha256':'e'*64,'economic_authority':False}
        monkeypatch.setattr(worker.cash_tool,'run_portfolio',cash)
        code,value=call('reconcile')
        assert code==0 and value['cash_reconciled'] is True and cash_calls==[allocation]
        assert captures==[1]
        code,value=call()
        assert code==3 and value['cash_reconciled'] is False and captures==[1]
        assert opened[0].snapshot()==[] and opened[2].orders()==[] and log.snapshot()==[]
    finally:
        log.close()
        for store in opened:store.close()
