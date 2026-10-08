"""Causal, fixed-budget BTC factory policy. No economic ledger or selection authority."""
from __future__ import annotations

from datetime import datetime, timezone
import numpy as np

DAY = 86_400_000
MINUTE = 60_000
MODELS = ('PRICE', 'PRICE_FLOW', 'PRICE_FUNDING', 'PRICE_FLOW_FUNDING')
POLICIES = ('CASH', 'PASSIVE25', 'FROZEN_PRICE', *MODELS, 'FACTORY')
THRESHOLD_BPS = 2 * (3.6 + 5) + 2


def select_schedule(days, predictions, labels):
    """Choose only from previously issued, naturally mature prediction errors."""
    days=np.asarray(days)
    p=np.asarray(predictions,dtype=float); y=np.asarray(labels,dtype=float)
    if (days.ndim!=1 or not np.issubdtype(days.dtype,np.integer)
            or p.shape!=(len(days),4) or y.shape!=(len(days),)
            or (np.diff(days)!=DAY).any()):
        raise ValueError('invalid factory panel')
    choices=[];updates=[];month=None;selected='CASH'
    available=days+2*DAY+5*MINUTE
    valid=np.isfinite(p).all(axis=1)&np.isfinite(y)
    for i,day in enumerate(days):
        decision=int(day+DAY)
        current=datetime.fromtimestamp(decision/1000,timezone.utc).strftime('%Y-%m')
        if current!=month:
            ids=np.flatnonzero(valid&(available<=decision))[-90:]
            selected='CASH';scores={};zero=None
            if len(ids)>=60:
                errors=np.mean((p[ids]-y[ids,None])**2,axis=0)
                zero=float(np.mean(y[ids]**2))
                scores={name:float(value) for name,value in zip(MODELS,errors)}
                best=int(np.argmin(errors))
                if errors[best]<zero:selected=MODELS[best]
            updates.append({'decision_ms':decision,'selected':selected,'n':len(ids),
                            'latest_label_ms':int(available[ids[-1]]) if len(ids) else None,
                            'mse':scores,'zero_mse':zero})
            month=current
        choices.append(selected)
    return choices,updates


def prediction_target(value):
    if not np.isfinite(value):raise ValueError('unavailable prediction')
    return float(np.sign(value))*.25 if abs(value)*10000>THRESHOLD_BPS else 0.


def frozen_price_predictions(days, features, fits, start):
    eligible=[row for row in fits['PRICE'] if row['decision_ms']<=start]
    if not eligible:raise ValueError('frozen model training unavailable')
    fit=eligible[-1]
    if fit['latest_label_ms']>start:raise ValueError('frozen model has immature labels')
    x=np.asarray(features,dtype=float)[:,:3]
    scaled=(x-np.asarray(fit['mean']))/np.asarray(fit['scale'])
    return np.column_stack((np.ones(len(x)),scaled))@np.asarray(fit['beta']),fit


def policy_targets(days, predictions, labels, features, fits, start):
    choices,updates=select_schedule(days,predictions,labels)
    frozen,fit=frozen_price_predictions(days,features,fits,start)
    targets={name:[] for name in POLICIES}
    for i in range(len(days)):
        for name in POLICIES:
            model=choices[i] if name=='FACTORY' else name
            if model=='CASH':weight=0.
            elif model=='PASSIVE25':weight=.25
            else:
                value=frozen[i] if model=='FROZEN_PRICE' else predictions[i,MODELS.index(model)]
                weight=prediction_target(value) if np.isfinite(value) else None
            targets[name].append(weight)
    return targets,updates,fit
