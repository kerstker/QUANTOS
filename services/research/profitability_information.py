"""P3/P4 input and contrast contracts; no claim of a profitable information edge."""
import math
import re
import numpy as np


def synchronized_basis(rows, *, decision_ms):
    if type(decision_ms) is not int or decision_ms<0 or len(rows)!=3:
        raise ValueError('three synchronized observations required')
    selected={r['market']:r for r in rows}
    if set(selected)!={'spot','perpetual','index'}:raise ValueError('spot/perpetual/index required')
    if len({(r['base'],r['quote']) for r in rows})!=1 or rows[0]['base']!='BTC' or rows[0]['quote'] not in ('USDT','USDC'):
        raise ValueError('evaluate BTC USDT and USDC separately')
    if len({r['event_ms'] for r in rows})!=1:raise ValueError('unsynchronized price observations')
    for r in rows:
        if any(type(r[k]) is not int for k in ('event_ms','available_ms')) or not 0<=r['event_ms']<=r['available_ms']<=decision_ms:
            raise ValueError('decision-time availability unverified')
        if decision_ms-r['event_ms']>300000:raise ValueError('price observations stale')
        if type(r['price']) not in (int,float) or not math.isfinite(r['price']) or not 0<r['price']<1e12:
            raise ValueError('invalid price')
    spot=selected['spot']['price'];perp=selected['perpetual']['price'];index=selected['index']['price']
    return {'quote':rows[0]['quote'],'decision_ms':decision_ms,'event_ms':rows[0]['event_ms'],
            'basis':perp/spot-1,'index_premium':perp/index-1,
            'spot_price':spot,'perpetual_price':perp,'economic_eligible':False}


def basis_change(previous,current):
    if previous['quote']!=current['quote'] or previous['event_ms']>=current['event_ms']:
        raise ValueError('ordered same-currency basis observations required')
    return {'basis_change':current['basis']-previous['basis'],
            'relative_return':current['perpetual_price']/previous['perpetual_price']-current['spot_price']/previous['spot_price'],
            'economic_eligible':False}


def controlled_specs():
    groups=(('price',),('price','flow'),('price','flow','funding'),('price','flow','oi_change'),
            ('price','flow','funding','oi_change'),('price','flow','funding','oi_change','funding_x_oi','flow_x_oi'))
    return [{'id':i,'features':list(features),'controls':['UTC_DAY','TRAILING_VOLATILITY','TRAILING_PRICE_STATE'],
             'oi_direction_assumption':'NONE','outer_feedback_allowed':False,
             'selection_scope':'INNER_ONLY','economic_eligible':False} for i,features in enumerate(groups)]


INFORMATION_COLUMNS=('price','flow','funding','oi_change','funding_x_oi','flow_x_oi')


def information_panel(times,price_features,observations,*,quote):
    """No imputation, currency conversion, or OI directional interpretation."""
    t=np.asarray(times);x=np.asarray(price_features,dtype=float)
    if quote not in ('USDC','USDT') or t.ndim!=1 or x.shape!=(len(t),5) or not np.isfinite(x).all():
        raise ValueError('aligned price panel and separate currency required')
    rows={}
    for row in observations:
        slot=row['slot_ms'];available=row['available_ms']
        if type(slot) is not int or type(available) is not int or slot in rows or not 0<=available<=slot:
            raise ValueError('causal unique information slots required')
        if not isinstance(row['source_hash'],str) or not re.fullmatch('[0-9a-f]{64}',row['source_hash']):
            raise ValueError('information receipt identity required')
        if any(type(row[k]) not in (int,float) or not math.isfinite(row[k]) or abs(row[k])>1e6 for k in ('flow','funding','oi_change')):
            raise ValueError('bounded finite information required')
        if not -1<=row['flow']<=1 or row['oi_change'] < -1:
            raise ValueError('flow ratio or OI fractional change unit mismatch')
        if any(type(row[k]) is not int for k in ('funding_event_ms','funding_interval_ms','oi_event_ms','oi_previous_ms')):
            raise ValueError('settlement and OI clocks required')
        if (not 0<=row['funding_event_ms']<=available or not 0<=row['oi_previous_ms']<row['oi_event_ms']<=available
                or row['funding_interval_ms'] not in (3600000,14400000,28800000)):
            raise ValueError('only settled funding and causal OI intervals accepted')
        rows[slot]=row
    if set(rows)!=set(t.tolist()):raise ValueError('missing or extra information slots')
    values=[]
    for i,at in enumerate(t):
        row=rows[int(at)];funding=row['funding']*14400000/row['funding_interval_ms']
        values.append([float(x[i,0]),row['flow'],funding,row['oi_change'],funding*row['oi_change'],row['flow']*row['oi_change']])
    return np.asarray(values)


def fit_information(train_x,train_y,predict_x):
    x=np.asarray(train_x,dtype=float);y=np.asarray(train_y,dtype=float);test=np.asarray(predict_x,dtype=float)
    if x.ndim!=2 or test.ndim!=2 or x.shape[1]!=test.shape[1] or y.shape!=(len(x),) or len(x)<8:
        raise ValueError('aligned finite training panel required')
    if not all(np.isfinite(a).all() for a in (x,y,test)):raise ValueError('nonfinite model input')
    mean=x.mean(axis=0);scale=x.std(axis=0);scale=np.where(scale>1e-12,scale,1.)
    design=np.column_stack((np.ones(len(x)),(x-mean)/scale))
    penalty=np.eye(design.shape[1]);penalty[0,0]=0
    beta=np.linalg.solve(design.T@design+penalty,design.T@y)
    prediction=np.column_stack((np.ones(len(test)),(test-mean)/scale))@beta
    if not np.isfinite(prediction).all():raise ValueError('nonfinite predictions')
    return prediction


def matched_state_diagnostic(times,price_features,predictions,labels):
    """Post-selection descriptive within-state association, not causal certification."""
    groups={}
    for at,x,p,y in zip(times,price_features,predictions,labels):
        key=(int(at)//86400000,int(np.searchsorted([.002,.005,.01,.02],x[3])),
             int(np.searchsorted([-.01,-.002,0.,.002,.01],x[0])))
        groups.setdefault(key,[]).append((float(p),float(y)))
    pairs=[]
    for group in groups.values():
        if len(group)<4:continue
        a=np.asarray(group);pairs.extend((a-a.mean(axis=0)).tolist())
    covariance=float(np.mean(np.prod(pairs,axis=1))) if pairs else None
    return {'status':'DESCRIPTIVE_ONLY' if pairs else 'INSUFFICIENT_MATCHED_STATES',
            'matched_rows':len(pairs),'within_state_covariance':covariance,'causal_increment_established':False,
            'controls':['UTC_DAY','TRAILING_VOLATILITY','TRAILING_PRICE_STATE']}
