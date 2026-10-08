"""Compare canonical simulation with independently executed common-model references."""
from __future__ import annotations
import argparse
from decimal import Decimal as D
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from services.execution.authoritative_perpetual_backtest import CapacitySnapshot,FundingPolicySnapshot,HistoricalContractRule
from services.execution.perpetual_ledger_v2 import (
    BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID,CapacityAction,CapacityPolicy,ContractSpec,MarginBracket,MarkEvent,
)
from services.execution.perpetual_simulation import (
    ExecutableQuote,OrderIntent,OrderRuleProfile,SimulationCostModel,SimulationFrame,run_linear_perpetual_simulation,
)
from tools.perpetual_replay_research import source_identity

DAY=86_400_000
START=20_000*DAY+10_000


def simulate_case(case,fixture,release):
    spec=ContractSpec('BTCUSDT','LINEAR_USDT_PERPETUAL','USDT',D('1'),D('.001'),D('.001'),D('5'),
        D('.01'),D('1e-24'),(MarginBracket(D('0'),None,D('.1'),D('.05'),D('0')),))
    contract=HistoricalContractRule('fixture',spec,START-1,START+200_000,START-1,START-1,START-1,'synthetic-reference')
    profile=OrderRuleProfile('fixture','fixture','BTCUSDT','BINANCE_USDM',START-1,START+200_000,
        START-1,START-1,START-1,'synthetic-reference',D('.1'),D('.1'),D('100000'),D('10'),D('10'),D('10000'),'ORIGINAL_ORDER')
    funding=FundingPolicySnapshot('BTCUSDT','BINANCE_USDM','fixture',START-1,START+100_000,
        8*3_600_000,(START+100_000,),START-1,START-1,START-1,'synthetic-reference')
    cap=CapacityPolicy('BINANCE_USDM','fixture',BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID,20_000,D('1000000000'),D('.1'),CapacityAction.REJECT)
    capacity=CapacitySnapshot('BTCUSDT',cap,cap.required_adv_window_start,cap.required_adv_window_end,
        START-1,START-1,'synthetic-reference')
    frames=[]
    for index,value in enumerate(case['prices']):
        time=START+index*1000;p=D(value);size=D(case.get('depth',['1']*len(case['prices']))[index])
        frames.append(SimulationFrame(time,(ExecutableQuote('BTCUSDT',time,time,p,p,size,size),),
                                      (MarkEvent(time,'BTCUSDT',p),),(capacity,)))
    def strategy(ctx):
        index=(ctx.timestamp-START)//1000;size=D(case['intents'][index])
        return () if size==0 else (OrderIntent('intent-'+str(index),'BTCUSDT',size),)
    result=run_linear_perpetual_simulation(release_identity=release,dataset_identity={'case':case},
        strategy_identity={'version':'FIXED_REFERENCE_INTENTS_V1'},contract_rules=(contract,),
        funding_policies=(funding,),order_rule_profiles=(profile,),initial_collateral=D(fixture['initial_collateral']),
        frames=frames,strategy=strategy,costs=SimulationCostModel(fee_rate=D(fixture['fee_rate'])))
    rows=[]
    for decision in result['decision_events']:
        timestamp=decision['timestamp']
        events=[e for e in result['ledger_events'] if e['event_type']=='TRADE' and e['timestamp']<=timestamp]
        rows.append({'equity':decision['equity'],
            'position':decision['positions'].get('BTCUSDT',{}).get('quantity','0'),
            'fees':str(sum((D(e['applied_fee']) for e in events),D('0'))),
            'fill':str(sum((D(e['filled_quantity']) for e in events if e['timestamp']==timestamp),D('0')))})
    return result,rows


def compare(fixture,reference,release):
    if set(reference['cases'])!={c['name'] for c in fixture['cases']}: raise ValueError('reference case set differs')
    tolerance=Fraction(0) if reference['engine']=='fraction' else Fraction('0.000000001')
    reports=[]
    for case in fixture['cases']:
        result,rows=simulate_case(case,fixture,release)
        expected=reference['cases'][case['name']]
        if len(rows)!=len(expected): raise ValueError('reference length mismatch')
        differences=[]
        for i,(actual,wanted) in enumerate(zip(rows,expected,strict=True)):
            for key in ('equity','position','fees'):
                delta=abs(Fraction(actual[key])-Fraction(wanted[key]))
                if delta>tolerance: differences.append({'index':i,'field':key,'difference':str(delta)})
            if Fraction(actual['fill'])!=Fraction(case['fills'][i]):
                differences.append({'index':i,'field':'fill','actual':actual['fill'],'expected':case['fills'][i]})
        reports.append({'case':case['name'],'differences':differences,'result_hash':result['result_hash'],
                        'reconciliation_residual':result['reconciliation']['residual']})
    return {'schema_version':'PERPETUAL_DIFFERENTIAL_V1','reference_engine':reference['engine'],
        'reference_dependencies':reference['dependencies'],'release_identity':release,
        'tolerance':str(tolerance),'cases':reports,'authoritative':False,
        'verdict':'PASS' if all(not r['differences'] and r['reconciliation_residual']=='0' for r in reports) else 'FAIL',
        'not_applicable':fixture['not_applicable_to_vectorbt']}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=Path,required=True)
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    result=compare(json.loads(args.cases.read_text()),json.loads(args.reference.read_text()),source_identity())
    result['reference_sha256']=hashlib.sha256(args.reference.read_bytes()).hexdigest()
    result['fixture_sha256']=hashlib.sha256(args.cases.read_bytes()).hexdigest()
    with args.output.open('x') as f: json.dump(result,f,sort_keys=True,indent=2)
    print(json.dumps({'verdict':result['verdict'],'cases':len(result['cases']),'reference_engine':result['reference_engine']}))
    return 0 if result['verdict']=='PASS' else 1


if __name__=='__main__': raise SystemExit(main())
