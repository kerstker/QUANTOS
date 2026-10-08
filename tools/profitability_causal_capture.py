"""Twelve public sources, bounded retries and immutable receipt clocks per hour."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
import threading
import subprocess

from services.research.profitability_causal_information import observation
from tools.profitability_market_capture import bounded_public_json, PublicRequestError
from tools.profitability_study import write_new
from tools.btc_factory_research import verified

HOUR=3600000


def capture_information(output,*,slot,fetch=bounded_public_json,clock=lambda:time.time_ns()//1000000,
                        sleep=time.sleep,monotonic=time.monotonic):
    now=clock()
    output=Path(output);resuming=output.exists()
    if type(slot) is not int or slot%HOUR or not slot<=now<slot+(295000 if resuming else 240000):
        raise ValueError('capture must begin within the first four minutes of its hour')
    if (output/'result.json').exists():
        saved=verified(output/'result.json');return saved['rows'],saved['failures']
    output.mkdir(parents=True,exist_ok=True)
    if resuming:
        registration=verified(output/'requests.json')
        if registration['slot_ms']!=slot:raise ValueError('capture slot changed')
        now=registration['started_ms']
    started=monotonic();end=started+max(0,(slot+295000-clock())/1000)
    def remaining():return min(end-monotonic(),(slot+295000-clock())/1000)
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
    if resuming:
        if registration['requests']!=[list(r) for r in requests]:raise ValueError('frozen public requests changed')
    else:
        write_new(output/'requests.json',{'slot_ms':slot,'started_ms':now,'requests':requests,
            'request_policy':{'maximum_attempts_per_source':3,'maximum_request_seconds':45,'freeze_before_ms':slot+295000},
            'economic_eligible':False})
    attempts=output/'attempts';reservations=attempts/'reservations';reservations.mkdir(parents=True,exist_ok=True)
    cooldown=[0.0];throttle=threading.Lock()
    for path in attempts.glob('*.json'):
        record=verified(path)
        if record.get('http_status')==429:
            wait=record.get('retry_after_seconds')
            wait=60 if wait is None else wait
            cooldown[0]=max(cooldown[0],monotonic()+max(0,(record['received_ms']+wait*1000-clock())/1000))
    def receive(item):
        quote,kind,url=item
        source=output/f'{quote}-{kind}.json';failure=output/f'{quote}-{kind}.failure.json'
        if source.exists():
            saved=verified(source)
            if saved['url']!=url:raise ValueError('source URL changed')
            return quote,kind,{k:v for k,v in saved.items() if k!='result_hash'},None
        if failure.exists():return quote,kind,None,verified(failure)
        error={'quote':quote,'kind':kind,'reason':'PublicRequestError','category':'ATTEMPTS_EXHAUSTED'}
        for ordinal in range(1,4):
            name=f'{quote}-{kind}-{ordinal}.json'
            if (reservations/name).exists():continue
            while True:
                wait=max(0,cooldown[0]-monotonic())
                if remaining()<=wait or remaining()<=0 or not wait:break
                sleep(wait)
            if remaining()<=wait or remaining()<=0:
                error.update(category='DEADLINE_EXCEEDED');break
            issued=clock();began=monotonic()
            write_new(reservations/name,{'quote':quote,'kind':kind,'ordinal':ordinal,'started_ms':issued,'url':url})
            try:
                payload=fetch(url,timeout_seconds=min(45,remaining()),retry_transport=False) if fetch is bounded_public_json else fetch(url)
                received=clock()
                if kind=='oi':
                    if not isinstance(payload,dict) or len(payload)>10:raise ValueError('bounded OI response required')
                elif not isinstance(payload,list) or len(payload)>(25 if kind=='hourly' else 2):
                    raise ValueError('bounded source response required')
                result={'url':url,'payload':payload,'received_ms':received}
            except Exception as exc:
                if isinstance(exc,PublicRequestError):category=exc.category;retryable=exc.retryable
                elif isinstance(exc,(TimeoutError,subprocess.TimeoutExpired)):category='TIMEOUT';retryable=True
                elif isinstance(exc,OSError):category='TRANSPORT_ERROR';retryable=True
                else:category='INVALID_RESPONSE';retryable=False
                error={'quote':quote,'kind':kind,'reason':type(exc).__name__,'category':category,
                       'http_status':getattr(exc,'http_status',None),'retry_after_seconds':getattr(exc,'retry_after_seconds',None)}
                write_new(attempts/name,{**error,'ordinal':ordinal,'started_ms':issued,'received_ms':clock(),
                    'status':'FAILED','elapsed_ms':round((monotonic()-began)*1000)})
                delay=(.25,.75,0)[ordinal-1]
                if error['http_status']==429:
                    delay=max(delay,error['retry_after_seconds'] if error['retry_after_seconds'] is not None else 60)
                    with throttle:cooldown[0]=max(cooldown[0],monotonic()+delay)
                if not retryable or ordinal==3:break
                if remaining()<=delay:
                    error={**error,'category':'DEADLINE_EXCEEDED'};break
                if error['http_status']!=429:sleep(delay)
                continue
            # Persistence failures abort this capture; they are not transport failures.
            if remaining()<=0:
                write_new(output/f'{quote}-{kind}.late.json',result)
                error={'quote':quote,'kind':kind,'reason':'PublicRequestError','category':'DEADLINE_EXCEEDED'}
                write_new(attempts/name,{**error,'ordinal':ordinal,'started_ms':issued,
                    'received_ms':received,'status':'FAILED','elapsed_ms':round((monotonic()-began)*1000)})
                break
            write_new(source,result)
            write_new(attempts/name,{'quote':quote,'kind':kind,'ordinal':ordinal,'started_ms':issued,
                'received_ms':received,'status':'SUCCEEDED','elapsed_ms':round((monotonic()-began)*1000)})
            return quote,kind,result,None
        write_new(failure,error)
        return quote,kind,None,error
    with ThreadPoolExecutor(max_workers=3) as pool:received=list(pool.map(receive,requests))
    rows=[];failures=[r[3] for r in received if r[3]]
    for quote in ('USDC','USDT'):
        sources={kind:r for q,kind,r,error in received if q==quote and error is None}
        try:
            path=output/f'{quote}.json'
            previous=verified(path) if path.exists() else None
            row=observation(sources,slot=slot,quote=quote,issued_ms=previous['issued_ms'] if previous else clock())
            if previous:
                if {k:v for k,v in previous.items() if k!='result_hash'}!=row:raise ValueError('frozen observation changed')
            else:write_new(path,row)
            rows.append(row)
        except (ValueError,KeyError,TypeError,IndexError,OverflowError) as exc:
            failures.append({'quote':quote,'reason':type(exc).__name__,'detail':str(exc)})
    history=[verified(p) for p in attempts.glob('*.json')]
    successes={(r['quote'],r['kind']) for r in history if r['status']=='SUCCEEDED'}
    recovered={(r['quote'],r['kind']) for r in history if r['status']=='FAILED'} & successes
    write_new(output/'result.json',{'rows':rows,'failures':failures,'economic_eligible':False,
        'request_attempts':len(list(reservations.glob('*.json'))),'recovered_requests':len(recovered),
        'elapsed_ms':round((monotonic()-started)*1000),'slot_ms':slot})
    return rows,failures
