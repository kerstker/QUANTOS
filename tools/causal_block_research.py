"""Fixed-path causal order-block study through QUANTOS's existing research ledger."""
from __future__ import annotations
from collections import Counter
from decimal import Decimal as D
import json
from pathlib import Path
import time
import numpy as np

from services.research.orderblock import BAR_MS, signals
from services.research.perpetual_alpha import replay, MINUTE
from services.research.trial_ledger import TrialLedger, TrialRecord
from tools import perpetual_alpha_research as shared
from tools.btc_usdc_research import alpha_hac
from tools.perpetual_replay_research import source_identity

OUTPUT=Path('data/cache/orderblock_research')
PLAN=Path(__file__).resolve().parents[1]/'docs/engineering/plans/2026-09-06-causal-orderblock-study.md'


def run_study(params):
    if set(params)!={'attempt'} or type(params['attempt']) is not int or not 1<=params['attempt']<=1000:
        raise ValueError('invalid bounded study attempt')
    destination=OUTPUT/str(params['attempt'])
    if destination.exists():raise ValueError('immutable attempt already exists')
    release=source_identity();plan_hash=shared.digest(PLAN.read_bytes())
    market,_,_,_,_,provenance=shared.load_data(include_intrabar=True)
    closes=market.pop('trade_closes');times=market['times']
    bar_times=np.arange(int(times[0]//BAR_MS*BAR_MS),int(times[-1]//BAR_MS*BAR_MS)+BAR_MS,BAR_MS,dtype=np.int64)
    bars=np.full((len(bar_times),4),np.nan)
    for i,t in enumerate(bar_times):
        a,b=np.searchsorted(times,[t,t+BAR_MS])
        if b-a==240 and np.all(np.diff(times[a:b])==MINUTE):
            bars[i]=market['prices'][a],market['trade_highs'][a:b].max(),market['trade_lows'][a:b].min(),closes[b-1]
    signal_result=signals(bar_times,bars)
    signal_map={r['available_time']:r['side'] for r in signal_result['signals']}
    identity={'source':release,'plan_sha256':plan_hash,'data_sha256':shared.digest(provenance),
              'signal_sha256':shared.digest(signal_result),'attempt':params['attempt'],
              'maker_fee_bps':'0','taker_fee_bps':'3.6','fee_source':'USER_REPORTED_ACCOUNT_RATE',
              'fill_model':'TAKER_NEXT_MINUTE_OPEN_PROXY','venue':'BINANCE_BTCUSDC_PERPETUAL'}
    destination.mkdir(parents=True)
    (destination/'signals.json').write_text(json.dumps(signal_result,indent=2))
    (destination/'input-identity.json').write_text(json.dumps(identity,indent=2))
    ledger=TrialLedger(str(OUTPUT/'trials.db'));records={};trial_ids=[];results={};comparison={}
    names=('cash','passive25','orderblock_2x','orderblock_5x')
    for name in names:
        for slip in (2,5,10):
            for stop in (True,False):
                definition={'identity':identity,'candidate':name,'slip':slip,'stop':stop}
                trial=shared.digest(definition);trial_ids.append(trial)
                record=dict(trial_id=trial,alpha_id=shared.digest(name),family_id=shared.digest('ORDERBLOCK' if name.startswith('orderblock') else 'BASELINE'),exact_definition_id=trial,validation_policy_digest=plan_hash)
                records[name,slip,stop]=record
                ledger.append(TrialRecord(**record,status='PLANNED',event_index=0,at=time.time_ns()//1000000))
    for name in names:
        results[name]={}
        for slip in (2,5,10):
            for stop in (True,False):
                record=records[name,slip,stop]
                ledger.append(TrialRecord(**record,status='RUNNING',event_index=1,at=time.time_ns()//1000000))
                try:
                    is_ob=name.startswith('orderblock')
                    leverage=D(2 if name=='orderblock_2x' else 5)
                    weight=leverage/D(2)
                    def decision(at,held):
                        if not is_ob:return 0. if name=='cash' else .25
                        if held:return None
                        side=signal_map.get(at)
                        return None if side is None else float(weight)*side
                    profile=dict(taker_fee_bps=D('3.6'))
                    if is_ob:
                        profile.update(decision_interval_ms=BAR_MS,target_weight=weight,max_position_weight=weight*D('1.05'),
                            initial_margin=D(1)/leverage,maintenance_margin=D('.1'),price_stop=D('.005'),max_holding_ms=80*3600000)
                    result=replay(**market,decision=decision,slip_bps=slip,stop=stop,**profile)
                    stats=shared.metrics(result)
                    if is_ob:
                        exits=Counter(f['reason'] for f in result['fills'] if f['reason']!='DAILY_TARGET')
                        stats['completed_trades']=sum(exits.values());stats['exit_reasons']=dict(exits)
                    artifact={'status':'TECHNICAL_VALIDATION_ONLY','identity':identity,'trial_id':record['trial_id'],
                              'candidate':name,'slip_bps':slip,'stop':stop,'metrics':stats,'replay':result}
                    terminal='PASSED'
                except Exception as exc:
                    artifact={'status':'FAILED','identity':identity,'trial_id':record['trial_id'],
                              'candidate':name,'slip_bps':slip,'stop':stop,'error':type(exc).__name__+': '+str(exc)}
                    terminal='FAILED'
                with (destination/(record['trial_id']+'.json')).open('x') as f:json.dump(artifact,f,indent=2,allow_nan=False)
                ledger.append(TrialRecord(**record,status=terminal,event_index=2,at=time.time_ns()//1000000))
                results[name][f'{slip}/{int(stop)}']={k:v for k,v in artifact.items() if k not in ('identity','replay')}
                print(name,slip,stop,artifact.get('metrics',{}).get('net_pnl',artifact.get('error')),flush=True)
        if name.startswith('orderblock'):
            center=results[name]['5/1'];baseline=results['passive25']['5/0']
            if center['status']=='FAILED' or baseline['status']=='FAILED':
                comparison[name]={'worth_further_review':False,'reason':'FAILED_COMPUTATION'}
            else:
                m=center['metrics'];reg=alpha_hac(m['daily_returns'],baseline['metrics']['daily_returns'])
                checks={'both_years_positive':all(x is not None and x>0 for x in m['annual_pnl'].values()),
                        'all_cost_and_stop_cases_positive':all(r['status']!='FAILED' and r['metrics']['net_pnl']>0 for r in results[name].values()),
                        'twenty_completed_trades':m['completed_trades']>=20,'drawdown_within_ten_percent':m['max_drawdown']<=.1,
                        'positive_hac_alpha':reg['daily_alpha'] is not None and reg['daily_alpha']>0 and reg['hac_t'] is not None and reg['hac_t']>2.4,
                        'not_top_five_days_only':sum(m['daily_pnl'])-sum(sorted(m['daily_pnl'])[-5:])>0}
                comparison[name]={'checks':checks,'regression':reg,'worth_further_review':all(checks.values()),'authority_admitted':False}
    output={'schema':'QUANTOS_PERPETUAL_ALPHA_RESEARCH_V1','status':'TECHNICAL_VALIDATION_ONLY',
        'authoritative':False,'live_eligible':False,'identity':identity,'data_quality':provenance,
        'signal_counts':{'blocks':len(signal_result['blocks']),'signals_in_evaluation':sum(market['start']<=t<market['end'] for t in signal_map)},
        'results':results,'comparison':comparison,'trial_ids':trial_ids,'trial_ledger_verified':ledger.verify(),
        'hypothesis_source':'FROZEN_CAUSAL_RECONSTRUCTION_NOT_ORIGINAL_ENGINE',
        'stage_status':{'data_cleaning':'VERIFIED','signal_generation':'CAUSAL_RECONSTRUCTION','backtest':'COMPLETED_WITH_PER_CASE_STATUS',
                        'paper':'BLOCKED_NO_SIGNED_AUTHORITY','live':'BLOCKED'},
        'limitations':['NOT_ORIGINAL_ORDERBLOCK_ENGINE','BTCUSDC_NOT_ORIGINAL_BTCUSDT','SEEN_HISTORY_NOT_OOS',
                       'HYPOTHETICAL_MARGIN_CURRENT_RULE_SCENARIO','TAKER_PROXY_NO_MAKER_QUEUE',
                       'MINUTE_CLOSED_WICK_STOP_NEXT_OPEN_NOT_TICK_EXECUTION','NO_DSR_PBO_OR_FORWARD_ADMISSION']}
    output['result_hash']=shared.digest(output)
    with (destination/'result.json').open('x') as f:json.dump(output,f,indent=2,allow_nan=False)
    return output
