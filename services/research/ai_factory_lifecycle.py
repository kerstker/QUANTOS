"""Descriptive model diagnostics and research recommendations, never promotion."""
import math


def model_diagnostics(labels,*,minimum=168):
    if not labels:return {'status':'WAITING_SAMPLES','count':0,'recommendation':'KEEP_OBSERVING','rmse':None,'zero_baseline_rmse':None,'mse_delta':None,'daily':[]}
    slots=set();errors=[];baseline=[];daily={}
    for row in labels:
        slot=row['slot_ms'];prediction=row['prediction'];actual=row['realized_return']
        if type(slot) is not int or slot in slots or any(type(v) not in (float,int) or not math.isfinite(v) for v in (prediction,actual)):
            raise ValueError('invalid or duplicate mature label')
        slots.add(slot);error=(prediction-actual)**2;errors.append(error);baseline.append(actual**2)
        day=slot//86400000*86400000
        d=daily.setdefault(day,{'time':day,'count':0,'squared_error':0.,'baseline_squared_error':0.})
        d['count']+=1;d['squared_error']+=error;d['baseline_squared_error']+=actual**2
    mse=sum(errors)/len(errors);zero=sum(baseline)/len(baseline)
    return {'status':'DESCRIPTIVE_ONLY' if len(labels)>=minimum else 'WAITING_SAMPLES','count':len(labels),
            'recommendation':'RESEARCH_CHALLENGER' if len(labels)>=minimum and mse>=zero else 'KEEP_OBSERVING',
            'rmse':math.sqrt(mse),'zero_baseline_rmse':math.sqrt(zero),'mse_delta':mse-zero,
            'daily':[{'time':d['time'],'count':d['count'],'rmse':math.sqrt(d['squared_error']/d['count']),
                      'baseline_rmse':math.sqrt(d['baseline_squared_error']/d['count'])} for _,d in sorted(daily.items())],
            'overlapping_labels':True,'economic_eligible':False}


def paired_models(left,right):
    a={r['slot_ms']:r for r in left};b={r['slot_ms']:r for r in right}
    shared=sorted(set(a)&set(b));deltas=[]
    for slot in shared:
        if a[slot]['realized_return']!=b[slot]['realized_return']:raise ValueError('model labels disagree')
        y=a[slot]['realized_return']
        deltas.append((a[slot]['prediction']-y)**2-(b[slot]['prediction']-y)**2)
    return {'shared_labels':len(shared),'mse_delta':sum(deltas)/len(deltas) if deltas else None,
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
