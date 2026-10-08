"""Shared research metrics for the pure canonical sleeve replay."""
from collections import Counter
from services.research.ai_factory_sleeves import CONSTRUCTION
from services.research.ai_factory_sleeve_replay import replay_sleeves
from services.research.ai_factory_execution_feedback import execution_feedback
from tools.perpetual_alpha_research import metrics


def evaluate_sleeves(market,specs,quarters,t,p,*,variance_by_slot=None,fee_bps=4,slip_bps=5,window=None):
    result=replay_sleeves(market,specs,quarters,t,p,variance_by_slot=variance_by_slot,
        fee_bps=fee_bps,slip_bps=slip_bps,window=window)
    start=result['daily'][0]['time'];end=result['daily'][-1]['time']
    reason_counts=Counter(c['reason'] for row in result['sleeve_execution']['decisions'] for c in row['choices'])
    stats=metrics(result)
    return {'score':stats['net_pnl']-1500*stats['max_drawdown'],'net_pnl':stats['net_pnl'],
        'drawdown':stats['max_drawdown'],'fees':stats['fees'],'funding':stats['funding'],
        'fills':stats['fills'],'equity':result['daily'],'start_ms':start,'end_ms':end,
        'execution_feedback':execution_feedback(result),'decision_counts':dict(reason_counts),
        'decision_policy':'INDEPENDENT_SLEEVE_COST_RISK_V1',
        'order_status_counts':dict(Counter(r['status'] for r in result['orders'])),
        'sleeve_execution':result['sleeve_execution']}
