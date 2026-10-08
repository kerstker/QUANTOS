"""Evidence-bound research explanation; no generated numbers or promotion decision."""
import math
from services.research.ai_factory_store import digest
from services.research.ai_factory_context import is_hash

SCHEMA='QUANTOS_RESEARCH_VERDICT_V1'


def research_verdict(primary,portfolio,*,sleeves=None,sleeve_process=None):
    for value in (primary,portfolio,sleeves):
        if value is not None and (value.get('result_hash')!=digest({k:v for k,v in value.items() if k!='result_hash'})
                or value.get('authoritative') is not False or not is_hash(value.get('model_hash'))):
            raise ValueError('research evidence identity rejected')
    if sleeve_process is not None and (sleeve_process.get('schema')!='QUANTOS_SLEEVE_PROCESS_COMPARISON_V1'
            or sleeve_process.get('result_hash')!=digest({k:v for k,v in sleeve_process.items() if k!='result_hash'})
            or any(sleeve_process.get(k) is not False for k in ('authoritative','economic_eligible'))
            or len(sleeve_process.get('rows',[]))>12):
        raise ValueError('sleeve process evidence identity rejected')
    facts=[]
    def fact(identity,source,pointer,unit,label=None):
        value=source
        try:
            for key in pointer.strip('/').split('/'):value=value[int(key)] if isinstance(value,list) else value[key]
            if type(value) not in (int,float,str) or not math.isfinite(float(value)):raise ValueError()
        except (ValueError,TypeError,KeyError,IndexError):value=None
        facts.append({'id':identity,'value':value,'unit':unit,'status':'MEASURED' if value is not None else 'UNKNOWN',
            'evidence_hash':source['result_hash'] if source else None,'pointer':pointer,**({'label':label} if label else {})})
    for key,unit in (('net_pnl','USDC'),('fees','USDC'),('drawdown','fraction'),('fills','count')):
        fact('primary.'+key,primary,'/outer/'+key,unit)
    comparisons=[]
    if portfolio:
        if len(portfolio.get('comparisons',[]))>3:raise ValueError('bounded comparison evidence required')
        for i,row in enumerate(portfolio.get('comparisons',[])):
            comparisons.append({'fee_bps':row['fee_bps'],'slippage_bps':row['slippage_bps']})
            for key in ('portfolio_net_pnl','baseline_net_pnl','net_pnl_delta','portfolio_fills'):
                fact(f'portfolio.{i}.{key}',portfolio,f'/comparisons/{i}/{key}','count' if key.endswith('fills') else 'USDC')
    facts.append({'id':'actual_venue_costs','value':None,'unit':'USDC','status':'UNKNOWN','evidence_hash':None,'pointer':None})
    decision=portfolio.get('decision','INCOMPLETE_EVIDENCE') if portfolio else 'PORTFOLIO_EVIDENCE_MISSING'
    if decision not in ('STOP_NO_ACTIVE_PORTFOLIO_EVIDENCE','STOP_NO_STABLE_NET_INCREMENT','STOP_UNRESOLVED_COMPARISON',
                        'NATURAL_FORWARD_REQUIRED','PORTFOLIO_EVIDENCE_MISSING'):
        decision='INCOMPLETE_EVIDENCE'
    document={'schema':SCHEMA,'scope':'OUTER_EXPLANATION','decision':decision,'facts':facts,'cost_scenarios':comparisons,
        'source_hashes':[v['result_hash'] for v in (primary,portfolio) if v is not None],
        'model_hashes':[v['model_hash'] for v in (primary,portfolio) if v is not None],
        'prediction_edge':'UNKNOWN','profitability_established':False,'economic_eligible':False,'authoritative':False,
        'search_feedback_allowed':False,'numeric_source':'IMMUTABLE_RESEARCH_TOOL_RESULTS',
        'limitations':['USED_HISTORY_NOT_NEW_OOS','NO_VERIFIED_VENUE_COSTS','NATURAL_FORWARD_REQUIRED',
                       'CASH_OUTPERFORMANCE_IS_NOT_ACTIVE_ALPHA']}
    if sleeves is not None or sleeve_process is not None:
        document['scope_decisions']={'forecast_portfolio':decision}
    if sleeves is not None:
        if len(sleeves.get('comparisons',[]))>3:raise ValueError('bounded sleeve evidence required')
        document['source_hashes'].append(sleeves['result_hash']);document['model_hashes'].append(sleeves['model_hash'])
        document['scope_decisions']['sleeves']=sleeves.get('decision','INCOMPLETE_EVIDENCE')
        labels={'portfolio_net_pnl':'净损益','baseline_net_pnl':'单策略净损益','net_pnl_delta':'净增量','portfolio_fills':'成交数'}
        for i,row in enumerate(sleeves.get('comparisons',[])):
            for key,label in labels.items():
                fact(f'sleeves.{i}.{key}',sleeves,f'/comparisons/{i}/{key}',
                    'count' if key.endswith('fills') else 'USDC',f"独立策略 · {label} · 费用/滑点 {row['fee_bps']} / {row['slippage_bps']} bp")
    if sleeve_process is not None:
        document['source_hashes'].append(sleeve_process['result_hash'])
        fact('sleeve_process.failed_groups',sleeve_process,'/failed_groups','count','独立策略三折 · 含失败的组数')
        rows=sleeve_process['rows'];names={'baseline':'固定顺序','random':'随机搜索','ai':'单次 AI','committee':'三角色'}
        for i,row in enumerate(rows):
            portfolio_row=row.get('portfolio') or {}
            if len(portfolio_row.get('comparisons',[]))>3:raise ValueError('bounded process evidence required')
            for j,cost in enumerate(portfolio_row.get('comparisons',[])):
                label=f"独立策略三折 · {row['fold']+1}/{names.get(row['group'],row['group'])} · 净增量 · {cost['fee_bps']} / {cost['slippage_bps']} bp"
                fact(f'sleeve_process.{i}.{j}.net_pnl_delta',sleeve_process,f'/rows/{i}/portfolio/comparisons/{j}/net_pnl_delta','USDC',label)
        decisions=[(r.get('portfolio') or {}).get('decision') for r in rows]
        result='STOP_UNRESOLVED_COMPARISON'
        if len(rows) in (6,12) and not sleeve_process['failed_groups'] and all(d is not None for d in decisions):
            if all(d=='STOP_NO_ACTIVE_PORTFOLIO_EVIDENCE' for d in decisions):result='STOP_NO_ACTIVE_PORTFOLIO_EVIDENCE'
            elif all(d=='NATURAL_FORWARD_REQUIRED' for d in decisions):result='NATURAL_FORWARD_REQUIRED'
            else:result='STOP_NO_STABLE_NET_INCREMENT'
        document['scope_decisions']['sleeve_process']=result
    return document
