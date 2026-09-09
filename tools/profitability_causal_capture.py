"""Twelve bounded public requests per hour, retaining actual receipt clocks."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

from services.research.profitability_causal_information import observation
from tools.profitability_market_capture import bounded_public_json
from tools.profitability_study import write_new

HOUR=3600000


def capture_information(output,*,slot,fetch=bounded_public_json,clock=lambda:time.time_ns()//1000000):
    now=clock()
    if type(slot) is not int or slot%HOUR or not slot<=now<slot+240000:
        raise ValueError('capture must begin within the first four minutes of its hour')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    requests=[]
    for quote in ('USDC','USDT'):
        symbol='BTC'+quote;futures='https://fapi.binance.com/fapi/v1/'
        requests.extend((quote,kind,url) for kind,url in (
            ('hourly',f'{futures}klines?symbol={symbol}&interval=1h&startTime={slot-25*HOUR}&endTime={slot-1}&limit=25'),
            ('spot',f'https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&startTime={slot-120000}&endTime={slot-1}&limit=2'),
            ('perpetual',f'{futures}klines?symbol={symbol}&interval=1m&startTime={slot-120000}&endTime={slot-1}&limit=2'),
            ('index',f'{futures}indexPriceKlines?pair={symbol}&interval=1m&startTime={slot-120000}&endTime={slot-1}&limit=2'),
            ('funding',f'{futures}fundingRate?symbol={symbol}&endTime={now-1}&limit=2'),
            ('oi',f'{futures}openInterest?symbol={symbol}')))
    write_new(output/'requests.json',{'slot_ms':slot,'started_ms':now,'requests':requests,'economic_eligible':False})
    def receive(item):
        quote,kind,url=item
        try:
            payload=fetch(url);received=clock()
            if kind=='oi':
                if not isinstance(payload,dict) or len(payload)>10:raise ValueError('bounded OI response required')
            elif not isinstance(payload,list) or len(payload)>(25 if kind=='hourly' else 2):
                raise ValueError('bounded source response required')
            result={'url':url,'payload':payload,'received_ms':received}
            write_new(output/f'{quote}-{kind}.json',result)
            return quote,kind,result,None
        except Exception as exc:
            error={'quote':quote,'kind':kind,'reason':type(exc).__name__}
            write_new(output/f'{quote}-{kind}.failure.json',error)
            return quote,kind,None,error
    with ThreadPoolExecutor(max_workers=3) as pool:received=list(pool.map(receive,requests))
    rows=[];failures=[r[3] for r in received if r[3]]
    for quote in ('USDC','USDT'):
        sources={kind:r for q,kind,r,error in received if q==quote and error is None}
        try:
            row=observation(sources,slot=slot,quote=quote,issued_ms=clock())
            write_new(output/f'{quote}.json',row);rows.append(row)
        except (ValueError,KeyError,TypeError,IndexError,OverflowError) as exc:
            failures.append({'quote':quote,'reason':type(exc).__name__,'detail':str(exc)})
    write_new(output/'result.json',{'rows':rows,'failures':failures,'economic_eligible':False})
    return rows,failures
