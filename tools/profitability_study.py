"""Read frozen research inputs and write a separate, immutable diagnostic study."""
from pathlib import Path
import argparse
import json
import time

import numpy as np

from services.research.ai_factory import HOUR, fit_predict, hourly_panel, partition
from services.research.ai_factory_store import digest
from tools.ai_quant_factory import evaluate_market
from tools.btc_factory_research import seal, snapshot, verified
from tools.btc_mechanism_study import load_verified_minutes
from tools.perpetual_alpha_research import load_data
from tools.perpetual_replay_research import source_identity


def write_new(path, payload):
    """Never overwrite prior attempts, including incomplete/failed experiments."""
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(seal(payload), stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')


def frozen_inputs(experiment, directory):
    plan = verified(experiment/'plan.json'); model = verified(experiment/'model.json')
    original = verified(experiment/'result.json')
    if model['identity_hash'] != digest(plan['identity']) or model['result_hash'] != original['model_hash']:
        raise ValueError('frozen model/result binding mismatch')
    if digest(snapshot(directory)) != plan['identity']['inputs']['input_hash']:
        raise ValueError('frozen input changed')
    minutes, _ = load_verified_minutes(directory)
    t, x, y = hourly_panel(minutes); market, *_ = load_data(directory=directory)
    keep = (t+300000 >= market['start']) & (t+4*HOUR+300000 <= market['end'])
    t, x, y = t[keep], x[keep], y[keep]
    if digest([t.tolist(), x.tolist(), y.tolist()]) != plan['identity']['panel_hash']:
        raise ValueError('frozen panel changed')
    return plan, model, original, t, x, y, market


def run_p0(experiment, directory, output):
    experiment=Path(experiment).resolve();directory=Path(directory).resolve();output=Path(output).resolve()
    if output == experiment or experiment in output.parents or output == directory or directory in output.parents:
        raise ValueError('use separate study output; frozen artifacts must remain unchanged')
    output.mkdir(parents=True, exist_ok=False)
    spec={'schema':'QUANTOS_PROFITABILITY_P0_PLAN_V1','created_at_ms':time.time_ns()//1000000,
          'source':source_identity(), 'experiment':str(experiment),
          'scope':'ALREADY_OBSERVED_FROZEN_HISTORY_DIAGNOSTIC', 'selection_allowed':False,
          'fee_bps':4,'slippage_bps':5,'initial_equity':'1500','loss_budget':'150',
          'attempts':1,'economic_eligible':False}
    write_new(output/'preregistration.json',spec)
    try:
        plan,model,original,t,x,y,market=frozen_inputs(experiment,directory)
        _,_,test=partition(t,t+4*HOUR+300000)
        prediction=fit_predict(model['spec'],model['training_x'],model['training_y'],x[test])
        variance={int(at):float(row[3]**2*4) for at,row in zip(t,x)}
        stats=evaluate_market(market,model['spec'],t[test],prediction,
                              cost_aware=model['decision_policy']=='DISCRETE_COST_RISK_V1',variance_by_slot=variance)
        original_outer=original['outer']
        if any(stats[k] != original_outer[k] for k in ('net_pnl','fees','funding','fills','start_ms','end_ms')):
            raise ValueError('frozen result does not reproduce; do not reinterpret profitability')
        report=stats['execution_feedback']['profitability_attribution']
        result={'stage':'P0','status':report['status'],'metrics':stats,'model_hash':model['result_hash'],
                'input_hash':plan['identity']['inputs']['input_hash'],
                'original_result_hash':original['result_hash'],'prediction_hash':digest(prediction.tolist()),
                'preregistration_hash':digest(spec),'attempts':1,'authoritative':False,
                'decision':'P1_BOUNDED_INNER_COMPARISON_ALLOWED' if report['status']=='RECONCILED_TECHNICAL_ONLY' else 'STOP_REPAIR_EVIDENCE',
                'economic_eligible':False,'historical_out_of_sample_claim':False}
        write_new(output/'result.json',result)
        return result
    except Exception as exc:
        write_new(output/'failure.json',{'stage':'P0','status':'FAILED','reason':type(exc).__name__,
                                        'detail':str(exc),'preregistration_hash':digest(spec),'economic_eligible':False})
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment',type=Path,required=True)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=run_p0(args.experiment,args.input,args.output)
    print(json.dumps({'status':result['status'],'decision':result['decision'],'output':str(args.output)}))


if __name__=='__main__':main()
