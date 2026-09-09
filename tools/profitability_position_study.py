"""P1: finite, registered inner-only policy comparison on frozen research inputs."""
from pathlib import Path
import argparse
import json
import time

import numpy as np

from services.research.ai_factory import HOUR, fit_predict, partition
from services.research.ai_factory_store import digest
from services.research.ai_factory_diagnostics import summarize_returns,search_bootstrap
from services.research.profitability_decision import POLICY_IDS
from tools.ai_quant_factory import evaluate_market
from tools.btc_factory_research import verified
from tools.perpetual_replay_research import source_identity
from tools.profitability_study import frozen_inputs, write_new
from tools.profitability_trial_receipts import registered_trial

COST_CASES = ((4,2),(4,5),(4,10),(8,5),(8,10))


def stability(metrics):
    equity=np.asarray([float(r['equity']) for r in metrics['equity']])
    changes=np.diff(equity); returns=equity[1:]/equity[:-1]-1
    positive=changes[changes>0]
    return {'daily':summarize_returns(returns),
            'best_day_share_of_positive_pnl':float(positive.max()/positive.sum()) if len(positive) else None,
            'period_net_changes':[float(part.sum()) for part in np.array_split(changes,3)],
            'effective_independent_samples':None,'economic_eligible':False}


def run_p1(p0_path, directory, output):
    p0_path=Path(p0_path).resolve(); output=Path(output).resolve()
    p0=verified(p0_path/'result.json'); p0_plan=verified(p0_path/'preregistration.json')
    if p0['stage']!='P0' or p0['decision']!='P1_BOUNDED_INNER_COMPARISON_ALLOWED':
        raise ValueError('reconciled P0 required')
    experiment=Path(p0_plan['experiment']).resolve(); directory=Path(directory).resolve()
    if any(output==p or p in output.parents for p in (experiment,directory,p0_path)):
        raise ValueError('use separate study output')
    output.mkdir(parents=True,exist_ok=False)
    registration={'schema':'QUANTOS_PROFITABILITY_P1_PLAN_V1','created_at_ms':time.time_ns()//1000000,
                  'source':source_identity(),'p0_hash':p0['result_hash'],'policies':list(POLICY_IDS),
                  'inner_selection':'MAX_NET_PNL_MINUS_INITIAL_EQUITY_TIMES_DRAWDOWN_BASELINE_WINS_TIES',
                  'outer_policies':'BASELINE_AND_INNER_WINNER_ONLY','cost_cases':[list(c) for c in COST_CASES],
                  'continuation':'STOP_IF_NO_INNER_INCREMENT_OR_NO_OUTER_ACTIVITY_OR_ANY_COST_PAIR_WORSE',
                  'initial_equity':'1500','loss_budget':'150','outer_feedback_allowed':False,
                  'history':'ALREADY_OBSERVED_NOT_NEW_OOS','economic_eligible':False}
    write_new(output/'preregistration.json',registration)
    trials=[]
    try:
        plan,model,original,t,x,y,market=frozen_inputs(experiment,directory)
        if model['result_hash']!=p0['model_hash'] or plan['identity']['inputs']['input_hash']!=p0['input_hash']:
            raise ValueError('P0 identity mismatch')
        if model['decision_policy']!='DISCRETE_COST_RISK_V1':raise ValueError('cost utility baseline required')
        train,valid,test=partition(t,t+4*HOUR+300000);train=train[-2048:]
        variance={int(at):float(row[3]**2*4) for at,row in zip(t,x)}
        # Fit once before any policy comparison. All inner policies share predictions.
        inner_prediction=fit_predict(model['spec'],x[train],y[train],x[valid])
        prediction_hash=digest(inner_prediction.tolist())
        for policy in POLICY_IDS:
            write_new(output/f'{policy}.started.json',{'policy':policy,'prediction_hash':prediction_hash,
                      'scope':'INNER_ONLY','preregistration_hash':digest(registration)})
            try:
                with registered_trial(output,registration,{'scope':'INNER','policy':policy}) as trial_id:
                    metrics=evaluate_market(market,model['spec'],t[valid],inner_prediction,cost_aware=True,
                                            variance_by_slot=variance,position_policy=policy)
                row={'policy':policy,'status':'COMPLETED','metrics':metrics,'prediction_hash':prediction_hash,'trial_id':trial_id}
            except Exception as exc:
                row={'policy':policy,'status':'FAILED','reason':type(exc).__name__,'detail':str(exc)}
            write_new(output/f'{policy}.json',row);trials.append(row)
        successful=[row for row in trials if row['status']=='COMPLETED']
        baseline=next((r for r in successful if r['policy']==POLICY_IDS[0]),None)
        if baseline is None:raise ValueError('baseline failed; policy selection unavailable')
        best=max(successful,key=lambda row:row['metrics']['score'])
        histories=[]
        for row in successful:
            equity=np.asarray([float(v['equity']) for v in row['metrics']['equity']])
            histories.append(equity[1:]/equity[:-1]-1)
        search_diagnostics=search_bootstrap(histories,trials_attempted=len(trials))
        write_new(output/'selection.json',{'policy':best['policy'],'scope':'INNER_ONLY',
                  'trials_attempted':len(trials),'prediction_hash':prediction_hash,
                  'preregistration_hash':digest(registration)})
        # Persist choice BEFORE calculating any outer predictions or outcomes.
        outer_prediction=fit_predict(model['spec'],model['training_x'],model['training_y'],x[test])
        if digest(outer_prediction.tolist())!=p0['prediction_hash']:raise ValueError('outer predictions changed')
        cases=[]
        for policy in dict.fromkeys((POLICY_IDS[0],best['policy'])):
            for fee,slip in COST_CASES:
                path=output/f'outer-{policy}-{fee}-{slip}.json'
                write_new(path.with_suffix('.started.json'),{'policy':policy,'fee':fee,'slippage':slip,
                          'preregistration_hash':digest(registration)})
                with registered_trial(output,registration,{'scope':'OUTER_COST_DIAGNOSTIC','policy':policy,'fee':fee,'slip':slip}) as trial_id:
                    metrics=evaluate_market(market,model['spec'],t[test],outer_prediction,cost_aware=True,
                                            variance_by_slot=variance,position_policy=policy,fee_bps=fee,slip_bps=slip)
                case={'policy':policy,'fee_bps':fee,'slippage_bps':slip,'metrics':metrics,'stability':stability(metrics),'trial_id':trial_id}
                write_new(path,case);cases.append(case)
        selected_cases=[r for r in cases if r['policy']==best['policy']]
        baseline_cases={(r['fee_bps'],r['slippage_bps']):r for r in cases if r['policy']==POLICY_IDS[0]}
        decision=('STOP_NO_INNER_INCREMENT' if best['policy']==POLICY_IDS[0] else
                  'STOP_NO_ACTIVE_OUTER_EVIDENCE' if not any(r['metrics']['fills'] for r in selected_cases) else
                  'STOP_COST_UNSTABLE_INCREMENT' if any(r['metrics']['net_pnl']<baseline_cases[(r['fee_bps'],r['slippage_bps'])]['metrics']['net_pnl'] for r in selected_cases) else
                  'FREEZE_FOR_FORWARD_NO_EXPANSION')
        result={'stage':'P1','status':'COMPLETED_TECHNICAL_ONLY','selected_policy':best['policy'],
                'trials':trials,'trials_attempted':len(trials),'cases':cases,
                'search_diagnostics':search_diagnostics,
                'p0_hash':p0['result_hash'],'preregistration_hash':digest(registration),
                'decision':decision,
                'authoritative':False,'economic_eligible':False,'historical_out_of_sample_claim':False,
                'limitations':['ALREADY_OBSERVED_HISTORY','FOUR_FIXED_POLICIES_NOT_INDEPENDENT_ALPHAS',
                               'FAILED_TRIALS_RETAINED','NO_FORMAL_MULTIPLE_TESTING_CERTIFICATION']}
        write_new(output/'result.json',result);return result
    except Exception as exc:
        write_new(output/'failure.json',{'stage':'P1','status':'FAILED','reason':type(exc).__name__,
                  'detail':str(exc),'trials_attempted':len(trials),'preregistration_hash':digest(registration)})
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('p0','input','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();result=run_p1(args.p0,args.input,args.output)
    print(json.dumps({'status':result['status'],'selected':result['selected_policy'],'decision':result['decision']}))


if __name__=='__main__':main()
