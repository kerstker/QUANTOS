"""Descriptive model diagnostics and research recommendations, never promotion."""
import math


def _validated_labels(labels):
    """Validate every input, including unmatched rows; never collapse duplicate slots."""
    slots=set()
    for row in labels:
        slot=row['slot_ms'];prediction=row['prediction'];actual=row['realized_return']
        try:
            valid_numbers=all(type(v) in (float,int) and math.isfinite(v) for v in (prediction,actual))
        except OverflowError:
            valid_numbers=False
        if type(slot) is not int or slot<0 or slot in slots or not valid_numbers:
            raise ValueError('invalid or duplicate mature label')
        try:
            finite=all(math.isfinite(v) for v in ((prediction-actual)**2,actual**2))
        except OverflowError:
            finite=False
        if not finite:raise ValueError('label error exceeds finite diagnostic range')
        slots.add(slot)
    return sorted(labels,key=lambda r:r['slot_ms'])


def model_diagnostics(labels,*,minimum=168):
    rows=_validated_labels(labels)
    empty={'status':'WAITING_SAMPLES','count':0,'recommendation':'KEEP_OBSERVING',
           'rmse':None,'zero_baseline_rmse':None,'mse_delta':None,'daily':[],
           'mae':None,'bias':None,'directional_accuracy':None,'directional_count':0,
           'calibration':[],'observations':[],'overlapping_labels':True,'economic_eligible':False}
    if not rows:return empty
    errors=[];baseline=[];daily={};observations=[];direction=[]
    # Fixed return bins (not fitted to realized outcomes), in decimal return units.
    edges=[-.005,-.002,0.,.002,.005]
    bins=[[] for _ in range(len(edges)+1)]
    for row in rows:
        slot=row['slot_ms'];prediction=row['prediction'];actual=row['realized_return']
        residual=actual-prediction;error=residual**2;errors.append(error);baseline.append(actual**2)
        observations.append({'time':slot,'prediction':prediction,'actual':actual,'residual':residual})
        if prediction!=0 and actual!=0:direction.append((prediction>0)==(actual>0))
        bins[sum(prediction>=edge for edge in edges)].append((prediction,actual,error))
        day=slot//86400000*86400000
        d=daily.setdefault(day,{'time':day,'errors':[],'baseline':[]})
        d['errors'].append(error);d['baseline'].append(actual**2)
    # Divide before summing to avoid overflow in otherwise finite mean errors.
    mean=lambda values:math.fsum(v/len(values) for v in values)
    mse=mean(errors);zero=mean(baseline)
    calibration=[]
    for i,group in enumerate(bins):
        calibration.append({'lower':edges[i-1] if i else None,'upper':edges[i] if i<len(edges) else None,
            'count':len(group),'prediction':mean([r[0] for r in group]) if group else None,
            'actual':mean([r[1] for r in group]) if group else None,
            'rmse':math.sqrt(mean([r[2] for r in group])) if group else None})
    return {**empty,'status':'DESCRIPTIVE_ONLY' if len(rows)>=minimum else 'WAITING_SAMPLES','count':len(rows),
            'recommendation':'RESEARCH_CHALLENGER' if len(rows)>=minimum and mse>=zero else 'KEEP_OBSERVING',
            'rmse':math.sqrt(mse),'zero_baseline_rmse':math.sqrt(zero),'mse_delta':mse-zero,
            'mae':mean([abs(r['residual']) for r in observations]),'bias':mean([-r['residual'] for r in observations]),
            'directional_count':len(direction),'directional_accuracy':sum(direction)/len(direction) if direction else None,
            'calibration':calibration,'observations':observations,
            'daily':[{'time':d['time'],'count':len(d['errors']),'rmse':math.sqrt(mean(d['errors'])),
                      'baseline_rmse':math.sqrt(mean(d['baseline']))} for _,d in sorted(daily.items())]}


def paired_models(left,right):
    a={r['slot_ms']:r for r in _validated_labels(left)};b={r['slot_ms']:r for r in _validated_labels(right)}
    shared=sorted(set(a)&set(b));deltas=[]
    for slot in shared:
        if a[slot]['realized_return']!=b[slot]['realized_return']:raise ValueError('model labels disagree')
        y=a[slot]['realized_return']
        deltas.append((a[slot]['prediction']-y)**2-(b[slot]['prediction']-y)**2)
    return {'shared_labels':len(shared),'mse_delta':math.fsum(d/len(deltas) for d in deltas) if deltas else None,
            'status':'DESCRIPTIVE_ONLY' if shared else 'NO_PAIRED_LABELS','economic_eligible':False}


def research_trigger(config,forward,*,now):
    """Decide whether a drift campaign can consume another bounded research round."""
    if config.get('trigger','schedule')=='schedule':return {'ready':True,'reason':'SCHEDULE','key':None}
    if not forward:return {'ready':False,'reason':'WAITING_OBSERVATION','key':None}
    at=forward.get('observed_at_ms')
    if type(at) is not int or not 0<=now-at<=180000:return {'ready':False,'reason':'STALE_OBSERVATION','key':None}
    labels=forward.get('recent_labels',[])
    if any(type(r.get('available_at_ms')) is not int or r['available_at_ms']>now for r in labels):return {'ready':False,'reason':'LABEL_TIME_UNVERIFIED','key':None}
    diagnostics=model_diagnostics(labels)
    ready=diagnostics['recommendation']=='RESEARCH_CHALLENGER'
    return {'ready':ready,'reason':'MODEL_UNDERPERFORMS_ZERO_BASELINE' if ready else 'WAITING_MATURE_DIAGNOSTIC',
            'key':str(max(r['slot_ms'] for r in labels)) if ready else None}
