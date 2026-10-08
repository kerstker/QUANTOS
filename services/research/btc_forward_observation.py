"""Frozen BTC forecasts and naturally mature labels, using the existing shadow event store.

These events contain predictive errors only: never account returns, paper fills or promotion.
"""
from __future__ import annotations

import math
import re
import numpy as np
from quantos.contracts.hashing import stable_hash

DAY=86_400_000
MINUTE=60_000
COLUMNS={'PRICE':(0,1,2),'PRICE_FLOW':(0,1,2,3),
         'PRICE_FUNDING':(0,1,2,4),'PRICE_FLOW_FUNDING':(0,1,2,3,4)}
FLAGS={'paper_only':True,'promotion_eligible':False,'live_allowed':False,'order_submission_allowed':False}


def _hash(value):
    if not isinstance(value,str) or re.fullmatch('[0-9a-f]{64}',value) is None:
        raise ValueError('invalid source hash')


def _clock(value):
    if type(value) is not int or value<0:raise ValueError('invalid observation clock')


def _chain(store):
    if not store.chain_valid():raise ValueError('observation chain is invalid')


def freeze_models(store,fits,*,source_hash,now_ms):
    _chain(store);_hash(source_hash);_clock(now_ms)
    if set(fits)!=set(COLUMNS):raise ValueError('frozen model set mismatch')
    identities={}
    # Validate the entire group before the first durable admission.
    for name,columns in COLUMNS.items():
        fit=fits[name];n=len(columns)
        if (len(fit['mean'])!=n or len(fit['scale'])!=n or len(fit['beta'])!=n+1
                or not np.isfinite([*fit['mean'],*fit['scale'],*fit['beta']]).all()
                or any(v<=0 for v in fit['scale']) or fit['latest_label_ms']>now_ms
                or type(fit['train_n']) is not int or fit['train_n']<180):
            raise ValueError('invalid or immature frozen model')
        identities[name]={'schema':'BTC_FROZEN_FORECAST_V1','model':name,'fit':fit,
                          'source_hash':source_hash,'frozen_at_ms':now_ms,'units':'fractional_return',
                          'horizon_ms':DAY,'uncertainty':'UNCALIBRATED','account_pnl_computed':False}
    ids={name:stable_hash(identity) for name,identity in identities.items()}
    for name,identity in identities.items():store.admit(ids[name],identity,admitted_at_ms=now_ms)
    return ids


def record_forecast(store,candidate_id,*,features,information_end_ms,available_at_ms,now_ms,source_hash):
    _chain(store);_hash(source_hash)
    for value in (information_end_ms,available_at_ms,now_ms):_clock(value)
    admission=store.admission(candidate_id)
    if admission is None:raise ValueError('missing model freeze')
    identity=admission['payload']['identity']
    slot=now_ms//DAY*DAY
    if (information_end_ms!=slot or available_at_ms<information_end_ms or available_at_ms>now_ms
            or not identity['frozen_at_ms']<=now_ms<slot+5*MINUTE):
        raise ValueError('missed window or future information; forward backfill forbidden')
    values=np.asarray(features,dtype=float)
    if values.shape!=(5,) or not np.isfinite(values).all():raise ValueError('invalid forward features')
    entry=slot+5*MINUTE
    if any(row['payload']['entry_time_ms']==entry for row in store.decisions(candidate_id)):
        return False
    fit=identity['fit'];x=values[list(COLUMNS[identity['model']])]
    prediction=float(np.r_[1.,(x-np.asarray(fit['mean']))/np.asarray(fit['scale'])]@np.asarray(fit['beta']))
    if not math.isfinite(prediction):raise ValueError('nonfinite forecast')
    payload={**FLAGS,'candidate_id':candidate_id,'decision_time_ms':now_ms,'entry_time_ms':entry,
             'exit_time_ms':entry+DAY,'information_end_ms':information_end_ms,'available_at_ms':available_at_ms,
             'source_hash':source_hash,'prediction':prediction,'features':values.tolist(),
             'units':'fractional_return','uncertainty':'UNCALIBRATED','account_pnl_computed':False}
    return store.record_decision(candidate_id,payload,recorded_at_ms=now_ms)


def record_outcome(store,candidate_id,decision,*,entry,exit,now_ms):
    _chain(store);_clock(now_ms)
    originals=[r['payload'] for r in store.decisions(candidate_id)
               if r['payload']['decision_time_ms']==decision['decision_time_ms']]
    if originals!=[decision]:raise ValueError('unknown or changed prediction')
    if now_ms<decision['exit_time_ms']+MINUTE:raise ValueError('label not mature')
    for row,event in ((entry,decision['entry_time_ms']),(exit,decision['exit_time_ms'])):
        if (row['event_ms']!=event or not event+MINUTE<=row['available_at_ms']<=now_ms
                or isinstance(row['open'],bool) or not math.isfinite(row['open']) or row['open']<=0):
            raise ValueError('reference unavailable or wrong time')
    actual=float(exit['open']/entry['open']-1)
    payload={**FLAGS,'candidate_id':candidate_id,'decision_time_ms':decision['decision_time_ms'],
             'as_of_ms':decision['exit_time_ms'],'label':actual,'prediction':decision['prediction'],
             'squared_error':(actual-decision['prediction'])**2,
             'entry_reference':entry,'exit_reference':exit,'account_pnl_computed':False}
    # Re-observing an already realized label must not create a second outcome.
    old=[r['payload'] for r in store.realizations(candidate_id)
         if r['payload']['decision_time_ms']==decision['decision_time_ms']]
    if old:
        if old[0]['label']!=actual:raise ValueError('reference correction conflicts with frozen realization')
        return False
    return store.record_realization(candidate_id,payload,recorded_at_ms=now_ms)


def observation_summary(store,candidate_id):
    _chain(store)
    admission=store.admission(candidate_id)
    if admission is None:raise ValueError('missing model freeze')
    rows=store.realizations(candidate_id)
    return {'candidate_id':candidate_id,'model':admission['payload']['identity']['model'],
            'frozen_at_ms':admission['identity_time_ms'],'decisions':len(store.decisions(candidate_id)),
            'completed':len(rows),'pending':len(store.pending(candidate_id)),
            'rmse_bps':math.sqrt(sum(r['payload']['squared_error'] for r in rows)/len(rows))*10000 if rows else None,
            'account_pnl_computed':False}


def _mature_panel(rows, cutoff_ms):
    _clock(cutoff_ms)
    eligible=[];seen=set()
    for row in rows:
        _clock(row['entry_time_ms']);_clock(row['available_at_ms'])
        if row['entry_time_ms'] in seen:raise ValueError('duplicate forward day')
        seen.add(row['entry_time_ms'])
        if row['available_at_ms']>cutoff_ms:continue
        if row['available_at_ms']<row['entry_time_ms']+DAY+MINUTE:
            raise ValueError('immature online label')
        if not math.isfinite(row['label']):raise ValueError('nonfinite online label')
        eligible.append(row)
    return sorted(eligible,key=lambda row:row['entry_time_ms'])


def online_month_fit(rows, cutoff_ms):
    """Expanding OLS from actually received forward labels; no archive bootstrap."""
    rows=_mature_panel(rows,cutoff_ms)
    if len(rows)<180:return {}
    x=np.asarray([row['features'] for row in rows],dtype=float)
    y=np.asarray([row['label'] for row in rows],dtype=float)
    if x.shape!=(len(rows),5) or not np.isfinite(x).all():raise ValueError('invalid online features')
    fits={}
    for name,columns in COLUMNS.items():
        train=x[:,columns];mean=train.mean(axis=0);scale=train.std(axis=0);scale[scale==0]=1
        design=np.column_stack((np.ones(len(rows)),(train-mean)/scale))
        beta,_,rank,_=np.linalg.lstsq(design,y,rcond=None)
        fits[name]={'decision_ms':cutoff_ms,'train_n':len(rows),'rank':int(rank),
                    'latest_label_ms':max(row['available_at_ms'] for row in rows),
                    'mean':mean.tolist(),'scale':scale.tolist(),'beta':beta.tolist()}
    return fits


def online_month_selection(rows, cutoff_ms):
    rows=_mature_panel(rows,cutoff_ms)[-90:]
    result={'selected':'CASH','n':len(rows),'mse':{},'zero_mse':None,
            'latest_label_ms':max((r['available_at_ms'] for r in rows),default=None)}
    if len(rows)<60:return result
    if any(set(row['predictions'])!=set(COLUMNS) for row in rows):
        raise ValueError('incomplete online prediction group')
    p=np.asarray([[row['predictions'][name] for name in COLUMNS] for row in rows])
    y=np.asarray([row['label'] for row in rows])
    if not np.isfinite(p).all():raise ValueError('nonfinite online predictions')
    errors=np.mean((p-y[:,None])**2,axis=0);zero=float(np.mean(y*y))
    best=int(np.argmin(errors))
    result.update(mse={name:float(error) for name,error in zip(COLUMNS,errors)},zero_mse=zero)
    if errors[best]<zero:result['selected']=list(COLUMNS)[best]
    return result


def forward_training_rows(store, candidate_id):
    """Join original decisions with durable receipt times, never event time alone."""
    _chain(store)
    decisions={r['payload']['decision_time_ms']:r['payload'] for r in store.decisions(candidate_id)}
    rows=[]
    for event in store.realizations(candidate_id):
        outcome=event['payload'];decision=decisions[outcome['decision_time_ms']]
        rows.append({'entry_time_ms':decision['entry_time_ms'],'features':decision['features'],
                     'label':outcome['label'],'prediction':decision['prediction'],
                     'available_at_ms':max(event['recorded_at_ms'],outcome['entry_reference']['available_at_ms'],
                                           outcome['exit_reference']['available_at_ms']),
                     'decision_hash':stable_hash(decision),'outcome_hash':event['row_hash']})
    return rows
