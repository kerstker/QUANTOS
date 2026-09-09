"""Bounded public outcome capture for a preregistered forward replay window."""
from decimal import Decimal
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import time
import numpy as np
from tools.profitability_market_capture import bounded_public_json
from tools.profitability_study import write_new

DAY=86400000
MINUTE=60000
# Coverage-only tolerance for venue settlement clock jitter; never round ledger events.
FUNDING_CLOCK_TOLERANCE_MS=1000


def market_from_receipts(receipts,*,start,end):
    arrays={};funding=[]
    for kind in ('klines','markPriceKlines'):
        rows=[]
        for record in receipts:
            if record['kind']!=kind:continue
            for r in record['payload']:
                if (not isinstance(r,list) or len(r)<8 or type(r[0]) is not int or type(r[6]) is not int
                        or r[0]%MINUTE or r[6]!=r[0]+MINUTE-1 or r[6]>=record['received_ms']):
                    raise ValueError('incomplete or invalid observed minute')
                rows.append(r)
        rows.sort(key=lambda r:r[0])
        times=[r[0] for r in rows]
        lo=start//DAY*DAY-DAY if kind=='klines' else start
        if times!=list(range(lo,end+MINUTE,MINUTE)):raise ValueError('missing, duplicate or extra market minutes')
        values=np.asarray([[r[0],float(r[1]),float(r[7])] for r in rows])
        if not np.isfinite(values).all() or (values[:,1]<=0).any() or (values[:,2]<0).any():
            raise ValueError('invalid public market values')
        arrays[kind]=(rows,values)
    records=[r for r in receipts if r['kind']=='fundingRate']
    if len(records)!=1:raise ValueError('one complete funding response required')
    previous=-1
    for r in records[0]['payload']:
        at=r['fundingTime'];rate=Decimal(r['fundingRate']);mark=Decimal(r['markPrice'])
        if (r['symbol']!='BTCUSDC' or type(at) is not int or not previous<at<end
                or not rate.is_finite() or abs(rate)>1 or not mark.is_finite() or not 0<mark<Decimal('1e12') or at>records[0]['received_ms']):
            raise ValueError('invalid ordered settled funding')
        previous=at;funding.append({'time':at,'rate':rate,'mark':mark})
    if (not funding or funding[0]['time']>start-8*3600000+FUNDING_CLOCK_TOLERANCE_MS or funding[-1]['time']<end-8*3600000-FUNDING_CLOCK_TOLERANCE_MS
            or any(b['time']-a['time']>8*3600000+FUNDING_CLOCK_TOLERANCE_MS for a,b in zip(funding,funding[1:]))):
        raise ValueError('funding coverage unavailable')
    rows,trade=arrays['klines'];mark=arrays['markPriceKlines'][1]
    volume={}
    for day in range(start//DAY*DAY-DAY,end//DAY*DAY,DAY):
        values=[Decimal(str(r[7])) for r in rows if day<=r[0]<day+DAY]
        if len(values)!=1440:raise ValueError('previous-day capacity coverage incomplete')
        volume[day//DAY+1]=sum(values,Decimal(0))
    trade=trade[trade[:,0]>=start]
    if not np.array_equal(trade[:,0],mark[:,0]):raise ValueError('unaligned execution/mark series')
    return {'times':trade[:,0].astype(np.int64),'prices':trade[:,1],'marks':mark[:,1],
            'funding':funding,'start':start,'end':end,'daily_volume':volume,'maximum_quantity':Decimal('50')}


def capture_market(output,*,start,end,fetch=bounded_public_json,clock=lambda:time.time_ns()//1000000):
    if type(start) is not int or type(end) is not int or not 0<end-start<=14*DAY or clock()<=end+MINUTE:
        raise ValueError('bounded completed forward market window required')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    requests=[]
    for kind,lo in [('klines',start//DAY*DAY-DAY),('markPriceKlines',start)]:
        for at in range(lo,end+MINUTE,1000*MINUTE):
            last=min(end,at+999*MINUTE)
            requests.append((kind,f'https://fapi.binance.com/fapi/v1/{kind}?symbol=BTCUSDC&interval=1m&startTime={at}&endTime={last+59999}&limit=1000'))
    requests.append(('fundingRate',f'https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDC&startTime={start-DAY}&endTime={end-1}&limit=1000'))
    write_new(output/'request-plan.json',{'start_ms':start,'end_ms':end,'requests':requests,'economic_eligible':False})
    def receive(item):
        kind,url=item
        payload=fetch(url)
        if not isinstance(payload,list) or len(payload)>1000:raise ValueError('bounded public response required')
        return {'kind':kind,'url':url,'received_ms':clock(),'payload':payload}
    with ThreadPoolExecutor(max_workers=3) as pool:
        # Preserve each successful response even if another request fails.
        futures=[pool.submit(receive,r) for r in requests];receipts=[];errors=[]
        for i,future in enumerate(futures):
            try:
                row=future.result();receipts.append(row);write_new(output/f'{i}.json',row)
            except Exception as exc:
                error={'request':requests[i],'reason':type(exc).__name__};errors.append(error)
                write_new(output/f'{i}.failure.json',error)
    if errors:raise ValueError('public outcome capture incomplete; response failures retained')
    market=market_from_receipts(receipts,start=start,end=end)
    return market,receipts
