"""P2 fixed immediate/wait comparison; total inventory opportunity is included."""
from pathlib import Path
from decimal import Decimal
import argparse
import json
import time

from services.research.ai_factory import HOUR, fit_predict, partition
from services.research.ai_factory_store import digest
from services.execution.perpetual_ledger_v2 import ledger_decimal_context
from tools.ai_quant_factory import evaluate_market
from tools.btc_factory_research import verified
from tools.perpetual_replay_research import source_identity
from tools.profitability_study import frozen_inputs,write_new
from tools.profitability_position_study import stability
from tools.profitability_trial_receipts import registered_trial


@ledger_decimal_context()
def paired_effect(immediate, delayed):
    a=immediate['execution_feedback']['profitability_attribution']
    b=delayed['execution_feedback']['profitability_attribution']
    if any(r['status']!='RECONCILED_TECHNICAL_ONLY' for r in (a,b)):
        return {'status':'UNKNOWN','reason':'INCOMPLETE_ATTRIBUTION'}
    delta={k:str(Decimal(b[k])-Decimal(a[k])) for k in
           ('net_pnl','gross_reference_pnl','embedded_slippage','fees','funding','turnover_notional')}
    trace=delayed['timing_trace']
    return {'status':'TECHNICAL_PAIRED_DIAGNOSTIC','delta_wait_minus_immediate':delta,
            'wait_decisions':sum(r['reason']=='WAIT_ADVERSE_SHORT_SIGNAL' for r in trace),
            'timeouts':sum(r['reason']=='WAIT_TIMEOUT' for r in trace),
            'cancelled_targets':sum(r['cancelled_target'] is not None for r in trace),
            'pending_at_end':delayed['timing_pending_at_end'],
            'missed_market_and_inventory_effect':delta['gross_reference_pnl'],
            'per_fill_price_improvement':None,
            'limitations':['GROSS_DELTA_INCLUDES_MISSED_MOVES_AND_CHANGED_INVENTORY',
                           'NOT_A_MATCHED_VENUE_FILL_PRICE_ESTIMATE'],'economic_eligible':False}


def run_p2(p0_path,p1_path,directory,output):
    p0_path=Path(p0_path).resolve();p1_path=Path(p1_path).resolve();output=Path(output).resolve()
    p0=verified(p0_path/'result.json');p1=verified(p1_path/'result.json');registration=verified(p0_path/'preregistration.json')
    if p1['p0_hash']!=p0['result_hash'] or p1['status']!='COMPLETED_TECHNICAL_ONLY':raise ValueError('P0/P1 binding required')
    experiment=Path(registration['experiment']).resolve();directory=Path(directory).resolve()
    if any(output==p or p in output.parents for p in (p0_path,p1_path,experiment,directory)):raise ValueError('separate output required')
    output.mkdir(parents=True,exist_ok=False)
    plan={'stage':'P2','created_at_ms':time.time_ns()//1000000,'source':source_identity(),
          'p0_hash':p0['result_hash'],'p1_hash':p1['result_hash'],'policy':p1['selected_policy'],
          'wait_ms':HOUR,'short_signal':'PAST_ONE_HOUR_MINUTE_OPEN_RETURN',
          'cost_cases':[[4,5],[4,10],[8,5]],'search_budget':0,'economic_eligible':False,
          'scope':'ALREADY_OBSERVED_FROZEN_HISTORY_DIAGNOSTIC'}
    write_new(output/'preregistration.json',plan)
    try:
        _,model,_,t,x,y,market=frozen_inputs(experiment,directory)
        if model['result_hash']!=p0['model_hash']:raise ValueError('frozen model changed')
        _,_,test=partition(t,t+4*HOUR+300000)
        prediction=fit_predict(model['spec'],model['training_x'],model['training_y'],x[test])
        if digest(prediction.tolist())!=p0['prediction_hash']:raise ValueError('frozen predictions changed')
        variance={int(at):float(row[3]**2*4) for at,row in zip(t,x)}
        pairs=[]
        for fee,slip in plan['cost_cases']:
            cases=[]
            for waiting in (False,True):
                name=f'{fee}-{slip}-{int(waiting)}'
                write_new(output/f'{name}.started.json',{'waiting':waiting,'preregistration_hash':digest(plan)})
                with registered_trial(output,plan,{'scope':'PAIRED_TIMING','waiting':waiting,'fee':fee,'slip':slip}) as trial_id:
                    stats=evaluate_market(market,model['spec'],t[test],prediction,cost_aware=True,
                           variance_by_slot=variance,position_policy=plan['policy'],timing_wait=waiting,
                           fee_bps=fee,slip_bps=slip)
                write_new(output/f'{name}.json',{'metrics':stats,'stability':stability(stats),'waiting':waiting,'trial_id':trial_id})
                cases.append(stats)
            effect=paired_effect(*cases)
            pairs.append({'fee_bps':fee,'slippage_bps':slip,'immediate':cases[0],
                          'delayed':{k:v for k,v in cases[1].items() if k!='timing_trace'},'effect':effect})
        active=any(pair['effect'].get('wait_decisions',0) for pair in pairs)
        result={'stage':'P2','status':'COMPLETED_TECHNICAL_ONLY','pairs':pairs,
                'decision':'FREEZE_TIMING_FOR_FORWARD_NO_EXPANSION' if active else 'STOP_NO_TIMING_OPPORTUNITIES',
                'attempts':6,'preregistration_hash':digest(plan),'p1_hash':p1['result_hash'],
                'authoritative':False,'economic_eligible':False,'historical_out_of_sample_claim':False}
        write_new(output/'result.json',result);return result
    except Exception as exc:
        write_new(output/'failure.json',{'stage':'P2','reason':type(exc).__name__,'detail':str(exc),
                                        'preregistration_hash':digest(plan),'economic_eligible':False})
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('p0','p1','input','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();r=run_p2(args.p0,args.p1,args.input,args.output)
    print(json.dumps({'status':r['status'],'decision':r['decision']}))


if __name__=='__main__':main()
