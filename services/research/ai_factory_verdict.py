"""Evidence-bound research explanation; no generated numbers or promotion decision."""
import math
from services.research.ai_factory_store import digest
from services.research.ai_factory_context import is_hash

SCHEMA='QUANTOS_RESEARCH_VERDICT_V1'


def research_verdict(primary,portfolio):
    for value in (primary,portfolio):
        if value is not None and (value.get('result_hash')!=digest({k:v for k,v in value.items() if k!='result_hash'})
                or value.get('authoritative') is not False or not is_hash(value.get('model_hash'))):
            raise ValueError('research evidence identity rejected')
    facts=[]
    def fact(identity,source,pointer,unit):
        value=source
        try:
            for key in pointer.strip('/').split('/'):value=value[int(key)] if isinstance(value,list) else value[key]
            if type(value) not in (int,float,str) or not math.isfinite(float(value)):raise ValueError()
        except (ValueError,TypeError,KeyError,IndexError):value=None
        facts.append({'id':identity,'value':value,'unit':unit,'status':'MEASURED' if value is not None else 'UNKNOWN',
            'evidence_hash':source['result_hash'] if source else None,'pointer':pointer})
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
    return {'schema':SCHEMA,'scope':'OUTER_EXPLANATION','decision':decision,'facts':facts,'cost_scenarios':comparisons,
        'source_hashes':[v['result_hash'] for v in (primary,portfolio) if v is not None],
        'model_hashes':[v['model_hash'] for v in (primary,portfolio) if v is not None],
        'prediction_edge':'UNKNOWN','profitability_established':False,'economic_eligible':False,'authoritative':False,
        'search_feedback_allowed':False,'numeric_source':'IMMUTABLE_RESEARCH_TOOL_RESULTS',
        'limitations':['USED_HISTORY_NOT_NEW_OOS','NO_VERIFIED_VENUE_COSTS','NATURAL_FORWARD_REQUIRED',
                       'CASH_OUTPERFORMANCE_IS_NOT_ACTIVE_ALPHA']}
