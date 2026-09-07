"""Offline causal model experiments. No transport, authority or account writes."""
from __future__ import annotations
import numpy as np
from services.research.ml.stacker import fit_stacker

HOUR=3600000
FEATURES=('return_1h','return_6h','return_24h','volatility_24h','taker_imbalance')


def candidates():
    return [{'id':i,'model':model,'features':list(cols),'threshold_bps':threshold}
            for i,(model,cols,threshold) in enumerate(
                (m,c,t) for t in (20,30) for c in ((0,1,2,3),(0,1,2,3,4),(4,))
                for m in ('rule','ridge','gbm'))]


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
    if spec not in candidates():raise ValueError('unknown candidate')
    x=np.asarray(train_x,dtype=float); y=np.asarray(train_y,dtype=float); z=np.asarray(predict_x,dtype=float)
    if x.ndim!=2 or x.shape[1]!=5 or z.ndim!=2 or z.shape[1]!=5 or y.shape!=(len(x),) or len(x)<32:
        raise ValueError('invalid model panel')
    if not all(np.isfinite(v).all() for v in (x,y,z)):raise ValueError('nonfinite model panel')
    cols=spec['features']; mean=x[:,cols].mean(axis=0); scale=x[:,cols].std(axis=0);scale[scale==0]=1
    if spec['model']=='rule':
        # Fixed directional baseline scaled by training-label volatility; no fitted test statistic.
        column=cols[0]
        return np.sign(z[:,column])*float(np.std(y))*.25
    fitted=fit_stacker((x[:,cols]-mean)/scale,y,model=spec['model'],seed=17)
    return fitted.predict((z[:,cols]-mean)/scale)


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
    if set(config)!={'attempt','mode','objective','budget'}:raise ValueError('invalid factory configuration fields')
    if type(config['attempt']) is not int or not 1<=config['attempt']<=1000:raise ValueError('invalid attempt')
    if type(config['budget']) is not int or not 3<=config['budget']<=18:raise ValueError('budget must be 3..18')
    if config['mode'] not in ('baseline','ai'):raise ValueError('invalid research mode')
    if not isinstance(config['objective'],str) or not 1<=len(config['objective'].strip())<=2000:raise ValueError('invalid research objective')
    return dict(config)
