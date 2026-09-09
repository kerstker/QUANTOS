"""P4 six registered information contrasts with canonical cost-aware evaluation."""
from pathlib import Path
import argparse
import json
import time

import numpy as np

from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.ai_factory import HOUR,partition
from services.research.ai_factory_store import digest
from services.research.ai_factory_diagnostics import search_bootstrap
from services.research.profitability_information import (
    controlled_specs,information_panel,fit_information,INFORMATION_COLUMNS,matched_state_diagnostic,
    basis_specs,basis_panel,BASIS_COLUMNS,
)
from tools.ai_quant_factory import evaluate_market
from tools.btc_factory_research import verified
from tools.perpetual_replay_research import source_identity
from tools.profitability_study import frozen_inputs,write_new
from tools.profitability_position_study import stability
from tools.profitability_trial_receipts import registered_trial


def run_p4(p0_path,information,directory,output,*,prior_trials_attempted=0):
    return _run_information(p0_path,information,directory,output,stage="P4",prior_trials_attempted=prior_trials_attempted)


def run_p3(p0_path,information,directory,output,*,prior_trials_attempted=0):
    return _run_information(p0_path,information,directory,output,stage="P3",prior_trials_attempted=prior_trials_attempted)


def _run_information(p0_path,information,directory,output,*,stage,prior_trials_attempted):
    if type(prior_trials_attempted) is not int or not 0<=prior_trials_attempted<=100:raise ValueError("bounded prior attempt count required")
    p0_path=Path(p0_path).resolve();information=Path(information).resolve();output=Path(output).resolve()
    p0=verified(p0_path/'result.json');prior=verified(p0_path/'preregistration.json')
    if p0['decision']!='P1_BOUNDED_INNER_COMPARISON_ALLOWED':raise ValueError('reconciled P0 required')
    experiment=Path(prior['experiment']).resolve();directory=Path(directory).resolve()
    if any(output==p or p in output.parents for p in (p0_path,experiment,directory,information)):
        raise ValueError('separate output required')
    output.mkdir(parents=True,exist_ok=False)
    specs=basis_specs() if stage=="P3" else controlled_specs()
    columns_contract=BASIS_COLUMNS if stage=="P3" else INFORMATION_COLUMNS
    registration={'stage':stage,'created_at_ms':time.time_ns()//1000000,'source':source_identity(),
                  'p0_hash':p0['result_hash'],'prior_input_revision_trials':prior_trials_attempted,'specs':specs,'cost_cases':[[4,5],[4,10],[8,5]],
                  'model':'RIDGE_L2_1_TRAIN_SCALE_ONLY','threshold_bps':20,
                  'selection':'INNER_NET_PNL_MINUS_1500_DRAWDOWN_BASELINE_WINS_TIES',
                  'history':'ALREADY_OBSERVED_HISTORY',
                  'continuation':'STOP_IF_BASELINE_SELECTED_NO_ACTIVITY_OR_ANY_COST_PAIR_WORSE',
                  'economic_eligible':False}
    write_new(output/'preregistration.json',registration);trials=[]
    try:
        if not information.is_file() or information.stat().st_size>16000000:
            raise ValueError('verified causal joint information file unavailable')
        raw=information.read_bytes();data=load_json_without_duplicate_keys(raw)
        if not isinstance(data,dict) or set(data)!={'quote','observations'} or data['quote']!='USDC':
            raise ValueError('USDC ledger needs separately evaluated USDC information')
        plan,model,_,t,x,y,market=frozen_inputs(experiment,directory)
        if model['result_hash']!=p0['model_hash']:raise ValueError('P0 model identity changed')
        panel=basis_panel if stage=='P3' else information_panel
        info=panel(t,x,data['observations'],quote=data['quote'])
        write_new(output/'input-identity.json',{'information_hash':digest(data),
                  'price_input_hash':plan['identity']['inputs']['input_hash'],
                  'provenance':'IMPORTED_SOURCE_RECEIPTS_NOT_INDEPENDENTLY_CERTIFIED'})
        return evaluate_information_panel(output,registration,t,x,y,info,market,information_hash=digest(data),trials=trials)
    except Exception as exc:
        write_new(output/'failure.json',{'stage':stage,'status':'FAILED','reason':type(exc).__name__,
                  'detail':str(exc),'trials_attempted':len(trials),'economic_eligible':False})
        raise


def evaluate_information_panel(output,registration,t,x,y,info,market,*,information_hash,trials):
    """Shared bounded inner/outer evaluation; caller freezes input clock and provenance."""
    stage=registration['stage'];specs=registration['specs']
    columns_contract=BASIS_COLUMNS if stage=='P3' else INFORMATION_COLUMNS
    prior_trials_attempted=registration['prior_input_revision_trials']
    train,valid,test=partition(t,t+4*HOUR+300000);train=train[-2048:]
    variance={int(at):float(row[3]**2*4) for at,row in zip(t,x)}
    def predict(spec,fit,indices):
        columns=[columns_contract.index(name) for name in spec['features']]
        # Identical trailing volatility control in every candidate, derived before decisions.
        features=np.column_stack((info[:,columns],x[:,3]))
        return fit_information(features[fit],y[fit],features[indices])
    for spec in specs:
        write_new(output/f'inner-{spec["id"]}.started.json',{'spec':spec,'scope':'INNER_ONLY'})
        try:
            with registered_trial(output,registration,{'scope':'INNER','spec':spec}) as trial_id:
                p=predict(spec,train,valid)
                metrics=evaluate_market(market,{'threshold_bps':20},t[valid],p,cost_aware=True,variance_by_slot=variance)
            row={'id':spec['id'],'status':'COMPLETED','metrics':metrics,'prediction_hash':digest(p.tolist()),'trial_id':trial_id}
        except Exception as exc:row={'id':spec['id'],'status':'FAILED','reason':type(exc).__name__}
        write_new(output/f'inner-{spec["id"]}.json',row);trials.append(row)
    success=[r for r in trials if r['status']=='COMPLETED']
    if not success or trials[0]['status']!='COMPLETED':raise ValueError('price baseline failed')
    best=max(success,key=lambda r:r['metrics']['score'])
    histories=[]
    for row in success:
        equity=np.asarray([float(v['equity']) for v in row['metrics']['equity']])
        histories.append(equity[1:]/equity[:-1]-1)
    search_diagnostics=search_bootstrap(histories,trials_attempted=len(trials)+prior_trials_attempted)
    write_new(output/'selection.json',{'selected_id':best['id'],'trials_attempted':len(trials),'scope':'INNER_ONLY'})
    cases=[];fit=np.r_[train,valid][-2048:]
    for identifier in dict.fromkeys((0,best['id'])):
        p=predict(specs[identifier],fit,test)
        controls=matched_state_diagnostic(t[test],x[test],p,y[test])
        for fee,slip in registration['cost_cases']:
            name=f'outer-{identifier}-{fee}-{slip}'
            write_new(output/f'{name}.started.json',{'selected_id':identifier,'preregistration_hash':digest(registration)})
            with registered_trial(output,registration,{'scope':'OUTER_COST_DIAGNOSTIC','id':identifier,'fee':fee,'slip':slip}) as trial_id:
                metrics=evaluate_market(market,{'threshold_bps':20},t[test],p,cost_aware=True,
                                        variance_by_slot=variance,fee_bps=fee,slip_bps=slip)
            case={'id':identifier,'fee_bps':fee,'slippage_bps':slip,'metrics':metrics,
                  'state_controls':controls,'stability':stability(metrics),'trial_id':trial_id}
            write_new(output/f'{name}.json',case);cases.append(case)
    result={'stage':stage,'status':'COMPLETED_TECHNICAL_ONLY','trials':trials,'trials_attempted':len(trials),
            'p0_hash':registration['p0_hash'],'preregistration_hash':digest(registration),'information_hash':information_hash,
            'search_diagnostics':search_diagnostics,'all_input_revision_trials_attempted':len(trials)+prior_trials_attempted,
            'selected_id':best['id'],'cases':cases,'decision':continuation_decision(best['id'],cases),
            'economic_eligible':False,'authoritative':False,'causal_increment_established':False,
            'limitations':['ALL_TRIALS_RETAINED_NO_FORMAL_ADAPTIVE_SEARCH_CERTIFICATION',
                           'WITHIN_STATE_ASSOCIATION_NOT_CAUSAL_PROOF',registration['history']]}
    write_new(output/'result.json',result);return result


def continuation_decision(selected,cases):
    if selected==0:return 'STOP_NO_INCREMENT'
    winners=[r for r in cases if r['id']==selected]
    if not winners or not any(r['metrics']['fills'] for r in winners):return 'STOP_NO_ACTIVE_OUTER_EVIDENCE'
    baseline={(r['fee_bps'],r['slippage_bps']):r['metrics']['net_pnl'] for r in cases if r['id']==0}
    if any(r['metrics']['net_pnl']<baseline[(r['fee_bps'],r['slippage_bps'])] for r in winners):
        return 'STOP_COST_UNSTABLE_INCREMENT'
    return 'FREEZE_FOR_FORWARD_NO_EXPANSION'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('p0','information','input','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--stage',choices=('P3','P4'),default='P4')
    args=parser.parse_args();runner=run_p3 if args.stage=='P3' else run_p4
    result=runner(args.p0,args.information,args.input,args.output)
    print(json.dumps({'status':result['status'],'decision':result['decision']}))


if __name__=='__main__':main()
