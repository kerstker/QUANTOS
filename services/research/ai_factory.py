"""Offline causal model experiments. No transport, authority or account writes."""
from __future__ import annotations
import numpy as np
from services.research.ml.stacker import fit_stacker

HOUR=3600000
FEATURES=('return_1h','return_6h','return_24h','volatility_24h','taker_imbalance')


def candidates(version=1):
    if type(version) is not int or version not in (1,2):raise ValueError("unknown search version")
    base = [{'id':i,'model':model,'features':list(cols),'threshold_bps':threshold}
            for i,(model,cols,threshold) in enumerate(
                (m,c,t) for t in (20,30) for c in ((0,1,2,3),(0,1,2,3,4),(4,))
                for m in ('rule','ridge','gbm'))]

    if version==1:return base
    expanded=list(base)
    for transform in ('flow_interaction','volatility_scaled','reversal'):
        for spec in base:
            expanded.append({**spec,'id':len(expanded),'transform':transform,'feature_version':2})
    return expanded


def feature_matrix(values,spec):
    """Row-local typed feature composition shared by training and natural inference."""
    x=np.asarray(values,dtype=float);cols=spec['features'];z=x[:,cols].copy()
    transform=spec.get('transform')
    if transform=='flow_interaction':z=np.c_[z,x[:,0]*x[:,4],x[:,1]*x[:,4]]
    elif transform=='volatility_scaled':z=np.c_[z,np.clip(x[:,0]/np.maximum(abs(x[:,3]),1e-6),-20,20)]
    elif transform=='reversal':z=np.c_[-z,x[:,0]*abs(x[:,0])]
    return z


def candidate_pool(config):
    plan=config.get('search_plan',{})
    return [s for s in candidates(plan.get('version',1)) if s['id'] not in plan.get('excluded_ids',[])]


def partition(decision_times,label_times):
    t=np.asarray(decision_times); a=np.asarray(label_times)
    if t.ndim!=1 or len(t)<400 or a.shape!=t.shape or (np.diff(t)<=0).any() or (a<=t).any():
        raise ValueError('insufficient or invalid causal panel')
    first,second=int(len(t)*.65),int(len(t)*.82)
    train=np.flatnonzero((t<t[first])&(a<t[first]))
    valid=np.flatnonzero((t>=t[first])&(t<t[second])&(a<t[second]))
    test=np.flatnonzero(t>=t[second])
    if min(len(train),len(valid),len(test))<32:raise ValueError('insufficient partition')
    return train,valid,test


def fit_predict(spec,train_x,train_y,predict_x):
    if spec not in candidates(2):raise ValueError('unknown candidate')
    x=np.asarray(train_x,dtype=float); y=np.asarray(train_y,dtype=float); z=np.asarray(predict_x,dtype=float)
    if x.ndim!=2 or x.shape[1]!=5 or z.ndim!=2 or z.shape[1]!=5 or y.shape!=(len(x),) or len(x)<32:
        raise ValueError('invalid model panel')
    if not all(np.isfinite(v).all() for v in (x,y,z)):raise ValueError('nonfinite model panel')
    cols=spec['features'];train=feature_matrix(x,spec);predict=feature_matrix(z,spec)
    mean=train.mean(axis=0);scale=train.std(axis=0);scale[scale==0]=1
    if spec['model']=='rule':
        # Fixed directional baseline scaled by training-label volatility; no fitted test statistic.
        column=cols[0]
        return np.sign(predict.mean(axis=1) if spec.get('transform') else z[:,column])*float(np.std(y))*.25
    fitted=fit_stacker((train-mean)/scale,y,model=spec['model'],seed=17)
    return fitted.predict((predict-mean)/scale)


def targets(predictions,threshold_bps):
    p=np.asarray(predictions,dtype=float)
    if not np.isfinite(p).all() or threshold_bps not in (20,30):raise ValueError('invalid decision input')
    return np.where(np.abs(p)*10000>threshold_bps,np.sign(p)*.25,0.).tolist()


def hourly_panel(minutes):
    """Verified minute rows: time, OHLC, volume, close-time, quote-volume, taker-quote.

    Hourly feature closes precede the decision; entry/label use +5-minute open prices.
    Historical receive clocks are UNKNOWN, so this is technical historical research only.
    """
    a=np.asarray(minutes,dtype=float)
    if a.ndim!=2 or len(a)<60 or a.shape[1]!=9 or not np.isfinite(a).all() or (np.diff(a[:,0])<=0).any():
        raise ValueError('invalid minute panel')
    hours=[]; closes=[]; flows=[]
    for t in range(int(a[0,0])//HOUR*HOUR,int(a[-1,0])//HOUR*HOUR,HOUR):
        lo,hi=np.searchsorted(a[:,0],[t,t+HOUR]);r=a[lo:hi]
        if len(r)!=60 or r[0,0]!=t or (np.diff(r[:,0])!=60000).any() or (r[:,6]!=r[:,0]+59999).any():continue
        if (r[:,7]<0).any() or (r[:,8]<0).any() or (r[:,8]>r[:,7]).any():raise ValueError('invalid flow')
        if r[:,7].sum()==0:continue
        hours.append(t+HOUR);closes.append(r[-1,4]);flows.append(2*r[:,8].sum()/r[:,7].sum()-1)
    times=[];xs=[];ys=[]
    for i in range(24,len(hours)):
        if hours[i]-hours[i-24]!=24*HOUR:continue
        entry=hours[i]+300000;end=entry+4*HOUR
        l,r=np.searchsorted(a[:,0],[entry,end])
        if r>=len(a) or a[l,0]!=entry or a[r,0]!=end:continue
        c=np.asarray(closes[i-24:i+1]);ret=c[1:]/c[:-1]-1
        if (c<=0).any() or min(a[l,1],a[r,1])<=0:raise ValueError('invalid price')
        xs.append([ret[-1],c[-1]/c[-7]-1,c[-1]/c[0]-1,float(ret.std()),flows[i]])
        ys.append(a[r,1]/a[l,1]-1);times.append(hours[i])
    return np.asarray(times,dtype=np.int64),np.asarray(xs),np.asarray(ys)


def validate_config(config):
    if set(config)-{'search_plan'}!={'attempt','mode','objective','budget'}:raise ValueError('invalid factory configuration fields')
    if type(config['attempt']) is not int or not 1<=config['attempt']<=1000:raise ValueError('invalid attempt')
    if type(config['budget']) is not int or not 3<=config['budget']<=18:raise ValueError('budget must be 3..18')
    if config['mode'] not in ('baseline','ai'):raise ValueError('invalid research mode')
    if not isinstance(config['objective'],str) or not 1<=len(config['objective'].strip())<=2000:raise ValueError('invalid research objective')
    if 'search_plan' in config:
        from services.research.ai_factory_sources import SOURCES
        plan=config['search_plan']
        if not isinstance(plan,dict) or set(plan)-{'memory'}!={'version','excluded_ids','source_ids'} or type(plan['version']) is not int or plan['version']!=2:
            raise ValueError('invalid typed search plan')
        excluded=plan['excluded_ids'];sources=plan['source_ids']
        if not isinstance(excluded,list) or any(type(i) is not int or not 0<=i<72 for i in excluded) or len(set(excluded))!=len(excluded):raise ValueError('invalid exclusions')
        if not isinstance(sources,list) or not 1<=len(sources)<=8 or any(not isinstance(i,str) or i not in SOURCES for i in sources) or len(set(sources))!=len(sources):raise ValueError('unknown research source')
        memory=plan.get('memory',[])
        if not isinstance(memory,list) or len(memory)>72:raise ValueError('invalid inner memory')
        for item in memory:
            if not isinstance(item,dict) or set(item)!={'candidate_id','score','status'}:raise ValueError('only inner memory accepted')
            if type(item['candidate_id']) is not int or item['candidate_id'] not in excluded:raise ValueError('unbound memory')
            if item['status'] not in ('PASSED','FAILED') or (item['score'] is not None and (type(item['score']) not in (int,float) or not np.isfinite(item['score']))):raise ValueError('invalid memory value')
        if len(candidate_pool(config))<config['budget']:raise ValueError('search space exhausted')
    return dict(config)
