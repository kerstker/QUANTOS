"""Causal, explicitly reconstructed order-block hypothesis; not the supplied engine."""
from __future__ import annotations
import numpy as np

BAR_MS=14400000


def signals(times,ohlc):
    times=np.asarray(times,dtype=np.int64);rows=np.asarray(ohlc,dtype=float)
    if rows.shape!=(len(times),4) or len(times)<5 or (np.diff(times)!=BAR_MS).any():
        raise ValueError('invalid 4h signal clock/shape')
    blocks=[];active=[];entries=[];touches=[]
    pivot_high=pivot_low=None
    ema=None;consecutive=0;reset_index=0
    for i,(o,h,l,c) in enumerate(rows):
        if not np.isfinite(rows[i]).all():
            active=[];pivot_high=pivot_low=None;ema=None;consecutive=0;reset_index=i+1
            continue
        if l<=0 or l>min(o,c) or h<max(o,c):raise ValueError('invalid 4h OHLC')
        ema=c if ema is None else ema+(c-ema)*2/101
        consecutive+=1
        remaining=[];matched=[];consumed=[]
        for block in active:
            side=block['side']
            if (side==1 and c<block['low']) or (side==-1 and c>block['high']):continue
            if l<=block['high'] and h>=block['low']:
                consumed.append(block['id'])
                approach=rows[i-1,3]>block['high'] if side==1 else rows[i-1,3]<block['low']
                trend=c>ema if side==1 else c<ema
                if approach and trend and consecutive>=100:matched.append(block)
            else:remaining.append(block)
        active=remaining
        if consumed:touches.append({'bar_index':i,'block_ids':consumed})
        directions={b['side'] for b in matched}
        if len(directions)==1:
            entries.append({'bar_index':i,'available_time':int(times[i])+BAR_MS+300000,
                            'side':directions.pop(),'block_ids':[b['id'] for b in matched]})
        if i-reset_index<4:continue
        center=i-2;window=rows[i-4:i+1]
        if rows[center,1]>max(window[[0,1,3,4],1]):pivot_high=(center,rows[center,1])
        if rows[center,2]<min(window[[0,1,3,4],2]):pivot_low=(center,rows[center,2])
        bull=pivot_high is not None and c>pivot_high[1]
        bear=pivot_low is not None and c<pivot_low[1]
        if bull:pivot_high=None
        if bear:pivot_low=None
        side=1 if bull and l>rows[i-2,1] and rows[i-1,3]>rows[i-1,0] else -1 if bear and h<rows[i-2,2] and rows[i-1,3]<rows[i-1,0] else 0
        if not side:continue
        anchors=[j for j in range(max(reset_index,i-12),i) if (rows[j,3]-rows[j,0])*side<0]
        if not anchors:continue
        j=anchors[-1]
        block={'id':len(blocks),'anchor_index':j,'confirmed_index':i,
               'confirmed_at':int(times[i])+BAR_MS,'side':side,'low':float(rows[j,2]),'high':float(rows[j,1])}
        blocks.append(block);active.append(block)
    return {'blocks':blocks,'signals':entries,'touches':touches}
