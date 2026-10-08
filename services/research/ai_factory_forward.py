"""Causal hourly observations for frozen factory models; no synthetic account PnL."""
from __future__ import annotations
import re
import numpy as np
from services.research.ai_factory import HOUR, targets
from services.research.ai_factory_store import digest


def _hash(value):
    if not isinstance(value,str) or re.fullmatch('[0-9a-f]{64}',value) is None:
        raise ValueError('invalid evidence hash')


def forward_features(rows,slot):
    if len(rows)!=25 or any(len(r)<11 for r in rows):
        raise ValueError('25 complete hourly bars required')
    if any(int(r[0])!=slot-(25-i)*HOUR or int(r[6])!=slot-(24-i)*HOUR-1 for i,r in enumerate(rows)):
        raise ValueError('hourly observation clock mismatch')
    close=np.asarray([float(r[4]) for r in rows]);volume=float(rows[-1][7]);buy=float(rows[-1][10])
    if not np.isfinite(close).all() or (close<=0).any() or not np.isfinite([volume,buy]).all() or not 0<=buy<=volume or volume<=0:
        raise ValueError('invalid observed price or flow')
    ret=close[1:]/close[:-1]-1
    return [float(ret[-1]),float(close[-1]/close[-7]-1),float(close[-1]/close[0]-1),float(ret.std()),2*buy/volume-1]


def forecast_document(*,slot,available,issued,activated,model_hash,input_hash,features,prediction,threshold_bps,
                      decision_policy='FIXED_THRESHOLD_V1'):
    _hash(model_hash);_hash(input_hash)
    if any(type(t) is not int for t in (slot,available,issued,activated)) or slot%HOUR or not 0<activated<=slot<=available<=issued<slot+300000:
        raise ValueError('forecast must precede execution and follow activation and input receipt')
    if len(features)!=5 or not np.isfinite(features).all() or not np.isfinite(prediction):
        raise ValueError('invalid forecast values')
    if decision_policy not in ('FIXED_THRESHOLD_V1','DISCRETE_COST_RISK_V1','INDEPENDENT_SLEEVE_COST_RISK_V1'):raise ValueError('unknown forecast decision policy')
    return {'slot_ms':slot,'available_at_ms':available,'issued_at_ms':issued,'entry_ms':slot+300000,
            'exit_ms':slot+300000+4*HOUR,'model_hash':model_hash,'input_hash':input_hash,
            'features':list(features),'prediction':float(prediction),
            'target_weight':targets([prediction],threshold_bps)[0] if decision_policy=='FIXED_THRESHOLD_V1' else None,
            **({'decision_policy':decision_policy} if decision_policy!='FIXED_THRESHOLD_V1' else {}),
            'horizon_ms':4*HOUR,'unit':'simple_return','authoritative':False,'account_pnl_computed':False}


def label_document(forecast,entry,exit,*,now):
    for row,at in ((entry,forecast['entry_ms']),(exit,forecast['exit_ms'])):
        _hash(row['source_hash'])
        if row['event_ms']!=at or not at+60000<=row['available_at_ms']<=now:
            raise ValueError('label reference unavailable or misaligned')
        if not np.isfinite(row['open']) or row['open']<=0:raise ValueError('invalid label reference')
    realized=exit['open']/entry['open']-1
    return {'forecast_hash':digest(forecast),'slot_ms':forecast['slot_ms'],'model_hash':forecast['model_hash'],
            'realized_return':realized,'prediction':forecast['prediction'],'residual':realized-forecast['prediction'],
            'available_at_ms':max(entry['available_at_ms'],exit['available_at_ms']), 'entry':entry,'exit':exit,
            'authoritative':False,'account_pnl_computed':False}


def diagnostics(labels):
    if not labels:return {'status':'WAITING_LABELS','count':0,'rmse':None,'mean_error':None,'direction_accuracy':None,'drift':'UNKNOWN'}
    error=np.asarray([r['residual'] for r in labels]);p=np.asarray([r['prediction'] for r in labels]);y=np.asarray([r['realized_return'] for r in labels])
    # Overlapping four-hour labels are not independent. No Sharpe/p-value claims.
    return {'status':'DESCRIPTIVE_ONLY','count':len(labels),'rmse':float(np.sqrt(np.mean(error**2))),
            'mean_error':float(error.mean()),'direction_accuracy':float(np.mean(np.sign(p)==np.sign(y))),
            'drift':'INSUFFICIENT_HISTORY' if len(labels)<168 else ('RESEARCH_REVIEW_SUGGESTED' if np.mean(error[-24:]**2)>2*np.mean(error[:-24]**2) else 'NO_MSE_ALERT'),
            'overlapping_labels':True}
