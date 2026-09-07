"""Purged chronological folds for replaying the complete bounded selection process."""
import numpy as np


def folds(times,available):
    t=np.asarray(times);a=np.asarray(available)
    if t.ndim!=1 or len(t)<600 or a.shape!=t.shape or (np.diff(t)<=0).any() or (a<=t).any():raise ValueError('invalid walk-forward panel')
    result=[]
    for i in range(3):
        start=int(len(t)*(.55+.15*i));end=min(len(t),int(len(t)*(.70+.15*i)))
        inner=start-int(len(t)*.15)
        train=np.flatnonzero((t<t[inner])&(a<t[inner]))[-2048:]
        valid=np.flatnonzero((t>=t[inner])&(t<t[start])&(a<t[start]))
        test=np.arange(start,end)
        if min(len(train),len(valid),len(test))<32:raise ValueError('insufficient fold history')
        result.append((train,valid,test))
    return result
