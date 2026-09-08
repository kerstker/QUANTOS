"""Read-only lifecycle workbench across real research artifacts."""
import os
import sqlite3
import time
from services.research.ai_factory_store import FactoryStore
from services.research.ai_factory_sources import SOURCES
from services.research.ai_factory_lifecycle import model_diagnostics,paired_models,research_trigger
from services.console.api.forward_dashboard import _read


CAPABILITIES=[
    {'id':'R0','title':'来源与研究目标','status':'AVAILABLE','detail':'36 个公开对象；来源与研究计划绑定，公开方法不等于盈利证据'},
    {'id':'R1','title':'AI 假设与搜索','status':'AVAILABLE','detail':'受限 QWEN 提案、72 个组合、内层记忆与完整尝试；尚非任意代码生成'},
    {'id':'R2','title':'数据、训练与验证','status':'PARTIAL','detail':'因果特征与成本后技术回放已接通；历史接收时间仍未知'},
    {'id':'R3','title':'持续研究与模型演进','status':'RESEARCH_ONLY','detail':'有界计划与观察诊断；新模型是挑战者，不能自动成为合格实盘策略'},
    {'id':'R4','title':'执行与实际反馈','status':'BLOCKED','detail':'当前工厂没有已核实的生产等价 Paper/真实账户整链证据，独立权威未接入'},
    {'id':'R5','title':'组合与容量','status':'PARTIAL','detail':'共享账户加入/替换已实现；两个合格策略、实际容量与净增量仍不足'},
    {'id':'R6','title':'运行与交付','status':'PARTIAL','detail':'研究心跳与检查点可用；正式生产恢复与发布按实际验收记录'}]


def lifecycle_payload(root):
    errors=[];base=root/'data/cache/ai_factory'
    try:
        snapshot=FactoryStore.snapshot(base/'factory.db')
        programs=FactoryStore.read_campaigns(base/'factory.db')
    except (OSError,ValueError,sqlite3.Error):
        snapshot={'sessions':[]};programs={'campaigns':[],'rounds':[]};errors.append('FACTORY_STORE_UNAVAILABLE')
    models=[];histories={};forwards={}
    now=time.time_ns()//1000000
    for row in snapshot['sessions']:
        if not row['result'].get('model_hash'):continue
        item={'attempt':row['attempt'],'model_hash':row['result']['model_hash'],'spec':row['result'].get('selected'),
              'state':'NOT_OBSERVING','diagnostics':model_diagnostics([]),'labels':0,'predictions':0,'updated_ms':None}
        path=base/str(row['attempt'])/'forward/status.json'
        if path.exists():
            try:
                forward=_read(path)
                if forward.get('authoritative') is not False or forward.get('account_pnl_computed') is not False:raise ValueError('invalid observation type')
                if forward.get('attempt')!=row['attempt']:raise ValueError('observation identity mismatch')
                labels=forward.get('recent_labels',[])
                if any(r.get('model_hash')!=item['model_hash'] or r.get('authoritative') is not False or r.get('account_pnl_computed') is not False or type(r.get('available_at_ms')) is not int or r['available_at_ms']>now for r in labels):raise ValueError('label identity or time mismatch')
                item.update(state=forward['status'],diagnostics=model_diagnostics(labels),labels=forward.get('labels',0),
                    predictions=forward.get('predictions',0),updated_ms=forward.get('observed_at_ms'))
                histories[row['attempt']]=labels;forwards[row['attempt']]=forward
                at=forward.get('observed_at_ms')
                if type(at) is not int or not 0<=now-at<=180000:item['state']='STALE'
            except (OSError,ValueError,TypeError,KeyError):item['state']='UNKNOWN';errors.append(f"MODEL_{row['attempt']}_UNAVAILABLE")
        models.append(item)
    worker=None
    path=base/'campaign-worker.json'
    if path.exists():
        try:
            worker=_read(path)
            if worker.get('authoritative') is not False:raise ValueError('invalid worker')
            if type(worker.get('observed_at_ms')) is not int or not 0<=now-worker['observed_at_ms']<=120000:worker={**worker,'status':'STALE'}
        except (OSError,ValueError,TypeError):errors.append('CAMPAIGN_WORKER_UNAVAILABLE')
    for campaign in programs['campaigns']:
        rounds=[r for r in programs['rounds'] if r['campaign_id']==campaign['campaign_id']]
        last=rounds[0] if rounds else None
        due=(last['finished_ms']+campaign['config']['interval_hours']*3600000) if last and last['finished_ms'] is not None else None
        trigger=research_trigger(campaign['config'],forwards.get(campaign['config'].get('champion_attempt')),now=now)
        campaign['next_due_ms']=due
        campaign['wait_reason']=(campaign['state'] if campaign['state']!='RUNNING' else
            'ROUND_IN_PROGRESS' if last and last['state']=='RESERVED' else
            'BUDGET_EXHAUSTED' if len(rounds)>=campaign['config']['max_cycles'] else
            'COOLDOWN' if due is not None and now<due else trigger['reason'])
    comparisons=[]
    for i,left in enumerate(models[:8]):
        for right in models[:8][i+1:]:
            try:comparison=paired_models(histories.get(left['attempt'],[]),histories.get(right['attempt'],[]))
            except (ValueError,TypeError,KeyError):comparison={'status':'LABEL_MISMATCH','shared_labels':0,'mse_delta':None,'economic_eligible':False}
            comparisons.append({'left':left['attempt'],'right':right['attempt'],**comparison})
    return {**programs,'worker':worker,'models':models,'comparisons':comparisons,'sources':[{'id':key,**value} for key,value in SOURCES.items()],
            'capabilities':CAPABILITIES,'scheduler_enabled':os.getenv('QUANTOS_FACTORY_CAMPAIGNS_ENABLED','false')=='true',
            'source':'BACKEND_COMPUTED_SNAPSHOT','data_provenance':'REAL_AI_FACTORY_DIAGNOSTIC','snapshot_status':'REAL' if snapshot['sessions'] else 'NO_SNAPSHOT',
            'authoritative':False,'read_only':True,'api_version':'0.1.0','as_of_ms':time.time_ns()//1000000,'errors':errors}
