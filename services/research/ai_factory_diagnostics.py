"""Descriptive dependent-return diagnostics, never admission or profitability proof."""
import numpy as np


def _samples(n,replications=256,block=7):
    rng=np.random.default_rng(17)
    starts=rng.integers(0,n,size=(replications,(n+block-1)//block))
    return ((starts[:,:,None]+np.arange(block))%n).reshape(replications,-1)[:,:n]


def summarize_returns(values):
    a=np.asarray(values,dtype=float)
    if a.ndim!=1 or not np.isfinite(a).all() or len(a)>20000:raise ValueError('invalid return history')
    base={'count':len(a),'authority':False,'method':'MOVING_BLOCK_BOOTSTRAP_7D'}
    if len(a)<30:return {**base,'status':'INSUFFICIENT_HISTORY','mean_ci':None}
    means=a[_samples(len(a))].mean(axis=1)
    return {**base,'status':'DESCRIPTIVE_ONLY','mean':float(a.mean()),'mean_ci':np.quantile(means,[.025,.975]).tolist(),
            'worst_day':float(a.min()),'tail_mean_5pct':float(np.sort(a)[:max(1,int(len(a)*.05))].mean()),
            'limitations':['STATIONARITY_AND_BLOCK_LENGTH_ASSUMPTIONS','NO_ECONOMIC_AUTHORITY']}


def search_bootstrap(panel,*,trials_attempted):
    a=np.asarray(panel,dtype=float)
    if a.ndim!=2 or not 1<=len(a)<=18 or a.shape[1]>20000 or not np.isfinite(a).all() or type(trials_attempted) is not int or trials_attempted<len(a):
        raise ValueError('invalid search diagnostic panel')
    base={'successful_models':len(a),'trials_attempted':trials_attempted,'adaptive_search_certified':False,
          'method':'COMMON_BLOCK_RESAMPLE_MAX_MEAN','authority':False}
    if a.shape[1]<30:return {**base,'status':'INSUFFICIENT_HISTORY','family_tail_fraction':None}
    centered=a-a.mean(axis=1,keepdims=True);indices=_samples(a.shape[1])
    winners=centered[:,indices].mean(axis=2).max(axis=0)
    observed=float(a.mean(axis=1).max())
    return {**base,'status':'DIAGNOSTIC_ONLY','observed_max_daily_mean':observed,
            'family_tail_fraction':float((1+(winners>=observed).sum())/(len(winners)+1)),
            'limitations':['FIXED_OBSERVED_CANDIDATE_SET_ONLY','FAILED_AND_ADAPTIVE_SEARCH_NOT_CALIBRATED']}
