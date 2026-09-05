"""QUANTOS bounded autonomous perpetual research, diagnostic only, frozen inputs."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from services.intelligence.search_space import HypothesisSpace
from services.intelligence.search_strategy import UCBSearch
from services.research.perpetual_alpha import DAY, MINUTE, hypothesis_target, replay
from services.research.spot_alpha import target
from services.research.trial_ledger import TrialLedger, TrialRecord
from tools.binance_spot_archive_capture import verify_archive
from tools.btc_usdc_research import alpha_hac, load_capture
from tools.perpetual_replay_research import source_identity
from tools.public_spot_alpha_research import decode_archive, ridge_predictions, timestamp

INPUT=Path('data/cache/perpetual_alpha/input')
OUTPUT=Path('data/cache/perpetual_alpha/results')
TRIALS=Path('data/cache/perpetual_alpha/trials.db')
ROOT=Path(__file__).resolve().parents[1]
PLAN=ROOT/'docs/engineering/plans/2026-09-06-perpetual-alpha-workflow.md'


def digest(value):
    return hashlib.sha256(value if isinstance(value,bytes) else json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()


def load_data(*,include_intrabar=False):
    start=timestamp('2025-01-01')+5*MINUTE;end=timestamp('2026-08-01')+5*MINUTE
    _,funding,funding_capture=load_capture(INPUT/'funding-capture.json')
    settlement=8*3600000
    if (not funding or funding_capture['end_ms']<end
            or funding[0]['time']//settlement>(start-14*DAY)//settlement
            or funding[-1]['time']//settlement<(end-1)//settlement):
        raise ValueError('funding capture does not cover research window')
    manifests={};arrays={}
    for kind in ('klines','markPriceKlines'):
        pieces=[];records=[]
        for path in sorted((INPUT/kind).glob('BTCUSDC-1m-*.zip')):
            if path.is_symlink():raise ValueError('symlink input forbidden')
            raw=path.read_bytes();meta=json.loads(path.with_suffix('.zip.json').read_text())
            expected=f'https://data.binance.vision/data/futures/um/'
            if (meta.get('market')!='usdm' or meta.get('kind')!=kind or meta.get('symbol')!='BTCUSDC'
                    or not meta['url'].startswith(expected) or f'/{kind}/BTCUSDC/1m/' not in meta['url']):
                raise ValueError('wrong perpetual archive identity')
            if verify_archive(raw,meta['official_checksum'],path.name)!=meta['sha256']:raise ValueError('archive digest drift')
            pieces.append(decode_archive(raw,market='usdm'));records.append(meta)
        if not pieces:raise ValueError('missing perpetual archive')
        array=np.concatenate(pieces)
        array=array[np.argsort(array[:,0],kind='stable')]
        if (np.diff(array[:,0])<=0).any():raise ValueError('duplicate or unordered partition')
        arrays[kind]=array;manifests[kind]=records
    trade=arrays['klines'];mark=arrays['markPriceKlines']
    times,trade_ids,mark_ids=np.intersect1d(trade[:,0].astype(np.int64),mark[:,0].astype(np.int64),return_indices=True)
    lo,hi=map(int,np.searchsorted(times,[start,end]))
    if hi>=len(times) or times[lo]!=start or times[hi]!=end or (np.diff(times[lo:hi+1])!=MINUTE).any():
        raise ValueError('trade/mark minute evaluation coverage incomplete')
    for values,ids in ((trade,trade_ids),(mark,mark_ids)):
        selected=values[ids[lo:hi]]
        if (selected[:,6]!=selected[:,0]+MINUTE-1).any():raise ValueError('truncated evaluation bar')
    days=np.arange(timestamp('2024-01-01'),timestamp('2026-08-01'),DAY,dtype=np.int64)
    daily=np.full((len(days),5),np.nan);refs=np.full(len(days),np.nan);volumes={}
    for i,day in enumerate(days):
        a,b=np.searchsorted(trade[:,0],[day,day+DAY]);rows=trade[a:b]
        if len(rows)==1440 and np.all(np.diff(rows[:,0])==MINUTE) and np.all(rows[:,6]==rows[:,0]+MINUTE-1):
            daily[i]=rows[0,1],rows[:,2].max(),rows[:,3].min(),rows[-1,4],rows[:,5].sum()
            volumes[int(day//DAY+1)]=sum((D(str(x)) for x in rows[:,7]),D(0))
        at=day+DAY+5*MINUTE;idx=int(np.searchsorted(trade[:,0],at))
        if idx<len(trade) and trade[idx,0]==at:refs[i]=trade[idx,1]
    funding_factor=np.zeros(len(days))
    for f in funding:
        i=int(np.searchsorted(days,f['time']//DAY*DAY))
        if i<len(days) and days[i]==f['time']//DAY*DAY:funding_factor[i]+=float(f['rate'])
    rules_raw=(INPUT/'exchange-info.json').read_bytes()
    rules=next(r for r in json.loads(rules_raw)['symbols'] if r['symbol']=='BTCUSDC')
    filters={f['filterType']:f for f in rules['filters']}
    if (rules['contractType']!='PERPETUAL' or rules['marginAsset']!='USDC' or rules['quoteAsset']!='USDC'
            or rules['status']!='TRADING' or D(filters['LOT_SIZE']['stepSize'])!=D('.001')
            or D(filters['LOT_SIZE']['minQty'])!=D('.001') or D(filters['MARKET_LOT_SIZE']['stepSize'])!=D('.001')
            or D(filters['MARKET_LOT_SIZE']['minQty'])!=D('.001')
            or D(filters['PRICE_FILTER']['tickSize'])!=D('.1') or D(filters['MIN_NOTIONAL']['notional'])!=D(50)):
        raise ValueError('unsupported current rule scenario')
    allowed={'PRICE_FILTER','LOT_SIZE','MARKET_LOT_SIZE','MAX_NUM_ORDERS','MIN_NOTIONAL','PERCENT_PRICE','POSITION_RISK_CONTROL'}
    if set(filters)-allowed or filters.get('POSITION_RISK_CONTROL',{}).get('positionControlSide')!='NONE':
        raise ValueError('unsupported filter/position control')
    provenance={'archives':manifests,'rules_sha256':digest(rules_raw),
                'funding_capture_sha256':digest((INPUT/'funding-capture.json').read_bytes()),
                'unmatched_trade_minutes':len(trade)-len(times),'unmatched_mark_minutes':len(mark)-len(times),
                'incomplete_signal_days':int(np.isnan(daily[:,0]).sum()),'aligned_minutes':len(times)}
    market=dict(times=times,prices=trade[trade_ids,1],marks=mark[mark_ids,1],funding=funding,start=start,end=end,
                daily_volume=volumes,maximum_quantity=D(filters['MARKET_LOT_SIZE']['maxQty']))
    if include_intrabar:
        market.update(trade_highs=trade[trade_ids,2],trade_lows=trade[trade_ids,3],trade_closes=trade[trade_ids,4])
    return market,days,daily,refs,funding_factor,provenance


def metrics(result):
    values=np.array([float(r['equity']) for r in result['daily']]);returns=values[1:]/values[:-1]-1
    periods={}
    for year in (2025,2026):
        indices=[i for i,r in enumerate(result['daily'][:-1]) if datetime.fromtimestamp(r['time']//1000,timezone.utc).year==year]
        periods[str(year)]=float(values[indices[-1]+1]-values[indices[0]]) if indices else None
    return {'net_pnl':float(values[-1]-1500),'annual_pnl':periods,'fills':len(result['fills']),
            'max_drawdown':result['minute_max_drawdown'],'exposure':result['average_exposure'],
            'fees':result['reconciliation']['fee'],'funding':result['reconciliation']['funding'],
            'daily_returns':returns.tolist(),'daily_pnl':np.diff(values).tolist(),
            'sharpe':float(returns.mean()/returns.std(ddof=1)*np.sqrt(365)) if returns.std(ddof=1)>0 else None}


def run_alpha(params):
    if set(params)!={'attempt'} or type(params['attempt']) is not int or not 1<=params['attempt']<=1000:
        raise ValueError('bounded attempt identity required')
    destination=OUTPUT/str(params['attempt'])
    if destination.exists():raise ValueError('attempt exists; inspect immutable result before using a new attempt')
    release=source_identity();plan=digest(PLAN.read_bytes())
    print('DATA_VALIDATION_STARTED',flush=True)
    market,days,daily,refs,funding_factor,provenance=load_data()
    predictions,fits,calibration=ridge_predictions(days,daily,refs)
    provenance['ridge_fits']=fits;provenance['ridge_calibration']=calibration
    print('DATA_VALIDATED',provenance['aligned_minutes'],'RIDGE_FITS',len(fits),flush=True)
    data_identity=digest({k:v for k,v in provenance.items() if k not in ('ridge_fits','ridge_calibration')})
    identity={'source':release,'plan':plan,'dataset':data_identity,'attempt':params['attempt'],
              'fee_bps':4,'slippage_bps':[2,5,10],'initial_collateral':'1500','risk_stop':'150'}
    destination.mkdir(parents=True)
    (destination/'input-identity.json').write_text(json.dumps(identity,indent=2))
    ledger=TrialLedger(str(TRIALS));results={};trials=[];comparison={}
    space=HypothesisSpace(('close_return','settled_funding'),('trend','fade'),(3,14))
    search=UCBSearch(space)

    def run_candidate(name,hypothesis=None):
        definition={'name':name,'hypothesis':hypothesis,'identity':identity}
        cases={}
        # The complete cost/risk family is durable before any case is evaluated.
        for slip in (2,5,10):
            for stop in (True,False):
                case={'definition':definition,'slip':slip,'stop':stop}
                trial=digest(case);trials.append(trial)
                family='TREND' if name in ('A','B') else 'RIDGE' if name=='C' else str(hypothesis[0]) if hypothesis else 'BASELINE'
                record=dict(trial_id=trial,alpha_id=digest(name),family_id=digest(family),exact_definition_id=digest(case),validation_policy_digest=plan)
                ledger.append(TrialRecord(**record,status='PLANNED',event_index=0,at=time.time_ns()//1000000))
                cases[(slip,stop)]=(trial,record)
        results[name]={}
        for (slip,stop),(trial,record) in cases.items():
            ledger.append(TrialRecord(**record,status='RUNNING',event_index=1,at=time.time_ns()//1000000))
            state=0.
            def decide(at,held):
                nonlocal state
                i=int(np.searchsorted(days,at-5*MINUTE-DAY))
                if i>=len(days) or days[i]!=at-5*MINUTE-DAY:raise ValueError('signal day absent')
                if hypothesis is not None:
                    factor=funding_factor[:i+1] if hypothesis[0]=='settled_funding' else np.r_[np.nan,daily[1:i+1,3]/daily[:i,3]-1]
                    return hypothesis_target(hypothesis,factor)
                if name=='C' and not np.isfinite(predictions[i]):return None
                if name in ('A','B') and not np.isfinite(daily[i-(28 if name=='A' else 55):i+1,3]).all():return None
                prediction=float(predictions[i]) if np.isfinite(predictions[i]) else None
                state=target(name,daily[:i+1,3],held,state,prediction,.0004,slip/10000)
                return state
            try:
                result=replay(**market,decision=decide,slip_bps=slip,stop=stop)
                stats=metrics(result)
                artifact={'status':'TECHNICAL_VALIDATION_ONLY','identity':identity,'trial_id':trial,
                          'candidate':name,'hypothesis':hypothesis,'slip_bps':slip,'stop':stop,'metrics':stats,'replay':result}
                terminal='PASSED'
            except Exception as exc:
                artifact={'status':'FAILED','identity':identity,'trial_id':trial,'candidate':name,'hypothesis':hypothesis,
                          'slip_bps':slip,'stop':stop,'error':type(exc).__name__+': '+str(exc)};terminal='FAILED'
            with (destination/(trial+'.json')).open('x') as handle:json.dump(artifact,handle,allow_nan=False,indent=2)
            ledger.append(TrialRecord(**record,status=terminal,event_index=2,at=time.time_ns()//1000000))
            results[name][f'{slip}/{int(stop)}']={k:v for k,v in artifact.items() if k not in ('identity','replay')}
            print('CASE',name,slip,stop,artifact.get('metrics',{}).get('net_pnl',artifact.get('error')),flush=True)
        if name not in ('cash','passive25'):
            center=results[name]['5/1'];baseline=results['passive25']['5/0']
            if center['status']=='FAILED' or baseline['status']=='FAILED':
                comparison[name]={'worth_further_review':False,'reason':'FAILED_COMPUTATION'};return
            m=center['metrics'];reg=alpha_hac(m['daily_returns'],baseline['metrics']['daily_returns'])
            checks={'both_years_positive':all(v is not None and v>0 for v in m['annual_pnl'].values()),
                    'all_cost_and_risk_cases_positive':all(r['status']!='FAILED' and r['metrics']['net_pnl']>0 for r in results[name].values()),
                    'ten_fills':m['fills']>=10,'drawdown_within_ten_percent':m['max_drawdown']<=.1,
                    'positive_hac_alpha':reg['daily_alpha'] is not None and reg['daily_alpha']>0 and reg['hac_t'] is not None and reg['hac_t']>2.4,
                    'not_top_five_days_only':sum(m['daily_pnl'])-sum(sorted(m['daily_pnl'])[-5:])>0}
            comparison[name]={'checks':checks,'regression':reg,'worth_further_review':all(checks.values()),
                              'authority_admitted':False,'paper_admitted':False,'live_eligible':False}
    for name in ('cash','passive25','A','B','C'):run_candidate(name)
    if not any(comparison[n]['worth_further_review'] for n in ('A','B','C')):
        while proposed:=search.propose(1):
            hypothesis=proposed[0];name=space.to_definition(*hypothesis).alpha_id
            run_candidate(name,hypothesis)
            r=results[name]['5/1'];reward=r['metrics']['annual_pnl']['2025']/1500 if r['status']!='FAILED' else -1.
            search.update(hypothesis,reward)
    output={'schema':'QUANTOS_PERPETUAL_ALPHA_RESEARCH_V1','status':'TECHNICAL_VALIDATION_ONLY',
            'authoritative':False,'live_eligible':False,'identity':identity,'data_quality':provenance,
            'results':results,'comparison':comparison,'trial_ids':trials,'trial_ledger_verified':ledger.verify(),
            'search_snapshot':search.snapshot(),'hypothesis_source':'BOUNDED_QUANTOS_GRAMMAR_NOT_LLM_INFERENCE',
            'stage_status':{'data_cleaning':'VALIDATED_FROZEN_INPUT','signal_generation':'COMPLETED',
                'backtest':'COMPLETED_WITH_PER_CASE_STATUS','candidate_search':'COMPLETED_BOUNDED_FAMILY',
                'statistical_admission':'NOT_ESTABLISHED','paper':'BLOCKED_NO_SIGNED_AUTHORITY',
                'live':'BLOCKED_HUMAN_GATES_AND_READINESS'},
            'limitations':['SEEN_HISTORY_NOT_UNTOUCHED_OOS','CURRENT_RULE_SCENARIO_ONLY',
                'HYPOTHETICAL_MARGIN_PROFILE','MINUTE_OPEN_EXECUTION_PROXY_NO_MAKER_QUEUE',
                'RETROSPECTIVE_BAR_AVAILABILITY_ASSUMPTION','FULL_DSR_PBO_AND_FORWARD_NOT_COMPLETED']}
    output['result_hash']=digest(output)
    with (destination/'result.json').open('x') as handle:json.dump(output,handle,allow_nan=False,indent=2)
    return output
