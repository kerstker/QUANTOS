"""Offline, fixed BTCUSDC predictive ablation; no economic ledger or promotion."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import time
import zipfile

import numpy as np

from quantos.runtime_paths import runtime_state_root
from services.research.trial_ledger import TrialLedger, TrialRecord
from tools.binance_spot_archive_capture import verify_archive
from tools.btc_usdc_research import load_capture
from tools.perpetual_replay_research import source_identity
from tools.public_spot_alpha_research import decode_archive, timestamp

DAY=86_400_000
MINUTE=60_000
MODELS={'PRICE':(0,1,2),'PRICE_FLOW':(0,1,2,3),
        'PRICE_FUNDING':(0,1,2,4),'PRICE_FLOW_FUNDING':(0,1,2,3,4)}
PLAN=Path(__file__).resolve().parents[1]/'docs/engineering/plans/2026-09-06-btc-mechanism-study.md'


def digest(value):
    raw=value if isinstance(value,bytes) else json.dumps(value,sort_keys=True,allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def imbalance(total,buy):
    if (total.shape!=buy.shape or not len(total) or not np.isfinite(total).all()
            or not np.isfinite(buy).all() or (total<0).any() or (buy<0).any()
            or (buy>total).any() or total.sum()<=0):
        raise ValueError('invalid quote taker volume')
    return float(2*buy.sum()/total.sum()-1)


def features(close,flow,funding):
    if not len(close)==len(flow)==len(funding):raise ValueError('unaligned features')
    out=np.full((len(close),5),np.nan)
    returns=np.r_[np.nan,close[1:]/close[:-1]-1]
    for i in range(28,len(close)):
        out[i]=returns[i],close[i]/close[i-7]-1,np.std(returns[i-27:i+1],ddof=1),flow[i],np.sum(funding[i-2:i+1])
    return out


def forecasts(days,x,y):
    n=len(days)
    if x.shape!=(n,5) or y.shape!=(n,) or (np.diff(days)!=DAY).any():raise ValueError('invalid daily panel')
    predictions=np.full((n,4),np.nan);fits={name:[] for name in MODELS}
    valid=np.isfinite(x).all(axis=1)&np.isfinite(y)
    last_month=None;fitted={}
    for i in range(n):
        decision=int(days[i]+DAY)
        month=datetime.fromtimestamp(decision/1000,timezone.utc).strftime('%Y-%m')
        if not np.isfinite(x[i]).all():continue
        if month!=last_month:
            ids=np.flatnonzero(valid&(days+2*DAY+5*MINUTE<=decision))
            if len(ids)<180:continue
            fitted={}
            for name,cols in MODELS.items():
                train=x[ids][:,cols];mean=train.mean(axis=0);scale=train.std(axis=0)
                scale[scale==0]=1
                design=np.column_stack((np.ones(len(ids)),(train-mean)/scale))
                beta,_,rank,_=np.linalg.lstsq(design,y[ids],rcond=None)
                fitted[name]=(mean,scale,beta)
                fits[name].append({'decision_ms':decision,'train_n':len(ids),
                    'first_signal_day_ms':int(days[ids[0]]),'last_signal_day_ms':int(days[ids[-1]]),
                    'latest_label_ms':int(days[ids[-1]]+2*DAY+5*MINUTE),'rank':int(rank),
                    'mean':mean.tolist(),'scale':scale.tolist(),'beta':beta.tolist()})
            last_month=month
        for k,(name,cols) in enumerate(MODELS.items()):
            mean,scale,beta=fitted[name]
            predictions[i,k]=np.r_[1.,(x[i,list(cols)]-mean)/scale]@beta
    return predictions,fits


def hac_mean(values):
    values=np.asarray(values,dtype=float);n=len(values)
    if n<10 or not np.isfinite(values).all():raise ValueError('insufficient/nonfinite HAC sample')
    mean=float(values.mean());center=values-mean
    variance=float(center@center)
    for lag in range(1,min(8,n)):
        variance+=2*(1-lag/8)*float(center[lag:]@center[:-lag])
    se=math.sqrt(max(0,variance))/n
    z=mean/se if se>0 else None
    return {'mean':mean,'se':se,'z':z,'p_two_sided_normal':math.erfc(abs(z)/math.sqrt(2)) if z is not None else None,
            'ci95':[mean-1.96*se,mean+1.96*se]}


def load_verified_minutes(directory):
    pieces=[];manifests=[]
    for path in sorted((directory/'klines').glob('BTCUSDC-1m-*.zip')):
        if path.is_symlink():raise ValueError('symlink archive')
        raw=path.read_bytes();meta=json.loads(path.with_suffix('.zip.json').read_text())
        prefix='https://data.binance.vision/data/futures/um/'
        if (meta.get('market')!='usdm' or meta.get('symbol')!='BTCUSDC' or meta.get('kind')!='klines'
                or not meta['url'].startswith(prefix) or '/klines/BTCUSDC/1m/' not in meta['url']):
            raise ValueError('wrong market identity')
        if verify_archive(raw,meta['official_checksum'],path.name)!=meta['sha256']:raise ValueError('archive hash drift')
        basic=decode_archive(raw,market='usdm')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            with archive.open(archive.namelist()[0]) as stream:
                first=stream.readline();stream.seek(0)
                extra=np.loadtxt(stream,delimiter=',',usecols=(0,10),ndmin=2,skiprows=int(first.startswith(b'open_time,')))
        if not np.array_equal(basic[:,0],extra[:,0]):raise ValueError('taker alignment drift')
        imbalance(basic[:,7],extra[:,1])
        pieces.append(np.column_stack((basic,extra[:,1])));manifests.append(meta)
    if not pieces:raise ValueError('missing archives')
    array=np.concatenate(pieces);array=array[np.argsort(array[:,0],kind='stable')]
    if (np.diff(array[:,0])<=0).any():raise ValueError('duplicate minute partition')
    return array,manifests


def load_panel(directory):
    array,manifests=load_verified_minutes(directory)
    days=np.arange(timestamp('2024-01-01'),timestamp('2026-08-01'),DAY,dtype=np.int64)
    close=np.full(len(days),np.nan);flow=close.copy();refs=close.copy();counts=np.zeros(len(days),dtype=int)
    for i,day in enumerate(days):
        a,b=np.searchsorted(array[:,0],[day,day+DAY]);rows=array[a:b];counts[i]=len(rows)
        if (len(rows)==1440 and np.all(np.diff(rows[:,0])==MINUTE)
                and np.all(rows[:,6]==rows[:,0]+MINUTE-1)):
            close[i]=rows[-1,4];flow[i]=imbalance(rows[:,7],rows[:,8])
        at=day+DAY+5*MINUTE;j=np.searchsorted(array[:,0],at)
        if j<len(array) and array[j,0]==at:refs[i]=array[j,1]
    _,funding,capture=load_capture(directory/'funding-capture.json')
    if capture['end_ms']<timestamp('2026-08-01'):raise ValueError('funding capture ends too early')
    f=np.full(len(days),np.nan)
    for event in funding:
        day=event['time']//DAY*DAY;i=int(np.searchsorted(days,day))
        if i<len(days) and days[i]==day:
            f[i]=(f[i] if np.isfinite(f[i]) else 0)+float(event['rate'])
    x=features(close,flow,f);y=np.r_[refs[1:]/refs[:-1]-1,np.nan]
    mask=(days+DAY>=timestamp('2025-01-01'))&(days+DAY<timestamp('2026-08-01'))
    if not np.isfinite(x[mask]).all() or not np.isfinite(y[mask]).all():raise ValueError('evaluation missing data')
    return days,x,y,{'archives':manifests,'funding_sha256':digest((directory/'funding-capture.json').read_bytes()),
                    'incomplete_days':int((counts!=1440).sum()),'total_minutes':len(array),
                    'historical_receive_times':'UNKNOWN','oi':'NOT_INCLUDED','basis':'NOT_INCLUDED'}


def summarize(days,y,p):
    execution=days+DAY+5*MINUTE
    windows={'all':(timestamp('2025-01-01'),timestamp('2026-08-01')),
             '2025':(timestamp('2025-01-01'),timestamp('2026-01-01')),
             '2026':(timestamp('2026-01-01'),timestamp('2026-08-01'))}
    results={}
    for label,(start,end) in windows.items():
        ids=(execution>=start)&(execution<end)
        if not np.isfinite(p[ids]).all():raise ValueError('missing evaluation predictions')
        errors=(p[ids]-y[ids,None])**2;base=errors[:,0].mean();zero=np.mean(y[ids]**2)
        results[label]={}
        for k,name in enumerate(MODELS):
            mse=float(errors[:,k].mean())
            results[label][name]={'n':int(ids.sum()),'mse':mse,'rmse_bps':math.sqrt(mse)*10000,
                'r2_vs_price':float(1-mse/base),'r2_vs_zero':float(1-mse/zero),
                'paired_improvement':hac_mean(errors[:,0]-errors[:,k]) if k else None}
    pvalues=[(results['all'][name]['paired_improvement']['p_two_sided_normal'],name) for name in list(MODELS)[1:]]
    running=0.
    for rank,(value,name) in enumerate(sorted(pvalues,key=lambda pair:pair[0] if pair[0] is not None else 1)):
        running=max(running,min(1,(3-rank)*(value if value is not None else 1)))
        results['all'][name]['holm_p']=running
        results['all'][name]['research_support']=bool(running<.05 and all(results[w][name]['r2_vs_price']>0 for w in windows))
    return results


def run(directory,output,attempt):
    if type(attempt) is not int or not 1<=attempt<=1000:raise ValueError('invalid attempt')
    dest=output/str(attempt);dest.mkdir(parents=True,exist_ok=False)
    input_paths=sorted((directory/'klines').glob('BTCUSDC-1m-*.zip*'))+[directory/'funding-capture.json']
    snapshot={str(p.relative_to(directory)):digest(p.read_bytes()) if p.is_file() else 'MISSING' for p in input_paths}
    identity={'source':source_identity(),'input_snapshot_sha256':digest(snapshot),'spec_sha256':digest(PLAN.read_bytes()),'attempt':attempt,
              'models':MODELS,'status':'EXPLORATORY_PREDICTION_ONLY'}
    ledger=TrialLedger(str(output/'trials.db'));records={}
    # Register every planned comparison before loading or inspecting its new outcomes.
    for name in MODELS:
        record=dict(trial_id=digest({'name':name,'identity':identity}),alpha_id=digest(name),
                    family_id=digest('BTC_DAILY_INFORMATION_ABLATION'),exact_definition_id=digest({'name':name,'identity':identity}),
                    validation_policy_digest=identity['spec_sha256'])
        records[name]=record;ledger.append(TrialRecord(**record,status='PLANNED',event_index=0,at=time.time_ns()//1000000))
        ledger.append(TrialRecord(**record,status='RUNNING',event_index=1,at=time.time_ns()//1000000))
    try:
        days,x,y,data=load_panel(directory)
        identity['data_sha256']=digest(data)
        if snapshot!={str(p.relative_to(directory)):digest(p.read_bytes()) for p in input_paths}:
            raise ValueError('input changed during read')
        (dest/'input-snapshot.json').write_text(json.dumps(snapshot,indent=2))
        p,fits=forecasts(days,x,y);result=summarize(days,y,p)
        with (dest/'predictions.csv').open('w',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['signal_day_ms','decision_ms','execution_ms','label_available_ms','r1','r7','v28','taker_imbalance','funding_3d','label',*MODELS])
            for i,day in enumerate(days):
                values=[int(day),int(day+DAY),int(day+DAY+5*MINUTE),int(day+2*DAY+5*MINUTE),*x[i],y[i],*p[i]]
                writer.writerow(['' if isinstance(v,(float,np.floating)) and not math.isfinite(v) else v for v in values])
        document={'schema':'QUANTOS_BTC_MECHANISM_V1','identity':identity,'data':data,'fits':fits,'results':result,
                  'trial_ids':[r['trial_id'] for r in records.values()],
                  'authoritative':False,'account_pnl_computed':False,
                  'predictions_sha256':digest((dest/'predictions.csv').read_bytes())}
        status='PASSED'
    except Exception as exc:
        (dest/'failure.json').write_text(json.dumps({'identity':identity,'trial_ids':[r['trial_id'] for r in records.values()],'error':str(exc)}));status='FAILED'
        raise
    finally:
        for record in records.values():
            ledger.append(TrialRecord(**record,status=locals().get('status','FAILED'),event_index=2,at=time.time_ns()//1000000))
        verified=ledger.verify()
        ledger.close()
    document['trial_ledger_verified']=verified
    document['result_hash']=digest(document)
    (dest/'result.json').write_text(json.dumps(document,indent=2,allow_nan=False))
    print(json.dumps({'attempt':attempt,'hash':document['result_hash'],'results':result['all']},indent=2))

    return document


def run_study(params):
    if set(params) != {'attempt'} or type(params['attempt']) is not int or not 1 <= params['attempt'] <= 1000:
        raise ValueError('bounded attempt identity required')
    root = runtime_state_root() / 'data/cache'
    return run(root / 'perpetual_alpha/input', root / 'btc_mechanism/results', params['attempt'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--attempt',type=int,required=True)
    args=parser.parse_args();run(args.input,args.output,args.attempt)
