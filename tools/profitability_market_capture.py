"""P3 public-only current capture; historical bars receive today's knowledge time."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse
import json
import time
import subprocess
import sys
import math

from services.research.profitability_information import synchronized_basis,basis_change
from tools.profitability_study import write_new
from tools.perpetual_replay_research import source_identity
from tools.profitability_public_http import PREFIXES


class PublicRequestError(RuntimeError):
    def __init__(self, category, *, http_status=None, retry_after_seconds=None):
        self.category=category
        self.http_status=http_status
        self.retry_after_seconds=retry_after_seconds
        self.retryable=category in ('TIMEOUT','TRANSPORT_ERROR') or (
            category=='HTTP_ERROR' and (http_status==429 or (http_status is not None and 500<=http_status<=599)))
        super().__init__(category if http_status is None else f'{category}_{http_status}')


def bounded_public_json(url, *, timeout_seconds=45, retry_transport=True):
    if type(retry_transport) is not bool:raise ValueError('explicit transport retry policy required')
    if type(timeout_seconds) not in (int,float) or not math.isfinite(timeout_seconds) or not 0<timeout_seconds<=45:
        raise ValueError('bounded positive request deadline required')
    started=time.monotonic()
    for ordinal in range(4 if retry_transport else 1):
        budget=timeout_seconds if ordinal==0 else timeout_seconds-(time.monotonic()-started)
        if budget<=0:raise PublicRequestError('TIMEOUT')
        try:return _single_public_json(url,timeout_seconds=budget)
        except PublicRequestError as exc:
            # Preserve bounded socket retries for existing basis/forward callers.
            # The causal collector owns its separately recorded retry budget.
            if not retry_transport or ordinal==3 or exc.category not in ('TIMEOUT','TRANSPORT_ERROR'):raise
            delay=(.25,.75,1.5)[ordinal]
            if timeout_seconds-(time.monotonic()-started)<=delay:raise
            time.sleep(delay)
    raise AssertionError('bounded transport retry exhausted')


def _single_public_json(url, *, timeout_seconds=45):
    # A socket timeout is not a total deadline across DNS addresses and retries.
    # Keep the approved public collector in a child with a real wall-clock limit.
    if not isinstance(url,str) or not url.startswith(PREFIXES):
        raise ValueError('unregistered public source')
    if type(timeout_seconds) not in (int,float) or not math.isfinite(timeout_seconds) or not 0<timeout_seconds<=45:
        raise ValueError('bounded positive request deadline required')
    try:
        result=subprocess.run([sys.executable,'-m','tools.profitability_public_http',url,str(min(10,timeout_seconds))],
            cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=timeout_seconds,check=True)
    except subprocess.CalledProcessError:
        raise PublicRequestError('CHILD_PROCESS_FAILED') from None
    try:
        response=json.loads(result.stdout)
        if response.get('ok') is True:return response['payload']
        if response.get('ok') is not False:raise ValueError()
        category=response['category'];status=response.get('http_status');retry=response.get('retry_after_seconds')
        if category not in ('HTTP_ERROR','RESPONSE_TOO_LARGE','INVALID_RESPONSE','TLS_VERIFICATION_FAILED','TRANSPORT_ERROR','TIMEOUT'):
            raise ValueError()
        if status is not None and (type(status) is not int or not 100<=status<=599):raise ValueError()
        if retry is not None and (type(retry) is not int or retry<0):raise ValueError()
    except (ValueError,KeyError,TypeError,AttributeError):
        raise PublicRequestError('INVALID_CHILD_RESPONSE') from None
    raise PublicRequestError(category,http_status=status,retry_after_seconds=retry)


def capture_basis(output, *, fetch=bounded_public_json, clock=lambda:time.time_ns()//1000000):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cutoff=clock()
    requests=[]
    for quote in ('USDC','USDT'):
        symbol='BTC'+quote
        for market,url in (
            ('spot',f'https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&limit=3'),
            ('perpetual',f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1m&limit=3'),
            ('index',f'https://fapi.binance.com/fapi/v1/indexPriceKlines?pair={symbol}&interval=1m&limit=3')):
            requests.append((quote,market,url))
    write_new(output/'preregistration.json',{'stage':'P3','source':source_identity(),'cutoff_ms':cutoff,
              'urls':[r[2] for r in requests],'historical_pit_claim':False,'economic_eligible':False})
    def receive(request):
        quote,market,url=request
        try:
            payload=fetch(url);received=clock()
            if not isinstance(payload,list) or not 1<=len(payload)<=3:raise ValueError('bounded kline response required')
            bars={}
            for r in payload:
                if not isinstance(r,list) or len(r)<7 or type(r[6]) is not int or type(r[0]) is not int or r[6]-r[0]!=59999:
                    raise ValueError('invalid minute clock')
                if r[6] in bars:raise ValueError('duplicate bar')
                if r[6]<cutoff:bars[r[6]]=float(r[4])
            return {'quote':quote,'market':market,'url':url,'received_ms':received,'bars':bars,'raw':payload}
        except Exception as exc:
            return {'quote':quote,'market':market,'url':url,'reason':type(exc).__name__}
    with ThreadPoolExecutor(max_workers=3) as executor:receipts=list(executor.map(receive,requests))
    write_new(output/'source-receipts.json',{'receipts':receipts,'historical_pit_claim':False})
    quotes=[];failures=[{k:v for k,v in r.items() if k!='raw'} for r in receipts if 'reason' in r]
    for quote in ('USDC','USDT'):
        selected=[r for r in receipts if r['quote']==quote and 'reason' not in r]
        if len(selected)!=3:continue
        common=set.intersection(*(set(r['bars']) for r in selected))
        if not common:
            failures.append({'quote':quote,'reason':'NO_SHARED_COMPLETED_BAR'});continue
        event=max(common);decision=clock()
        try:
            feature=synchronized_basis([{'market':r['market'],'base':'BTC','quote':quote,'price':r['bars'][event],
                    'event_ms':event,'available_ms':r['received_ms']} for r in selected],decision_ms=decision)
            changes=None
            if len(common)>1:
                previous_event=sorted(common)[-2]
                previous=synchronized_basis([{'market':r['market'],'base':'BTC','quote':quote,'price':r['bars'][previous_event],
                    'event_ms':previous_event,'available_ms':r['received_ms']} for r in selected],decision_ms=decision)
                changes=basis_change(previous,feature)
            quotes.append({'quote':quote,'feature':feature,'changes':changes})
        except ValueError as exc:failures.append({'quote':quote,'reason':str(exc)})
    result={'stage':'P3','status':'INPUT_CONTRACT_VALIDATED' if len(quotes)==2 else 'WAITING_VALID_INPUT',
            'quotes':quotes,'failures':failures,'historical_pit_claim':False,'economic_eligible':False,
            'research_status':'INSUFFICIENT_CAUSAL_HISTORY_FOR_INCREMENT_TEST',
            'limitations':['CURRENT_PUBLIC_CAPTURE_ONLY','NO_RETROACTIVE_KNOWLEDGE_TIMES','NO_VALIDATED_ALPHA']}
    write_new(output/'result.json',result);return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();r=capture_basis(args.output)
    print(json.dumps({'status':r['status'],'failures':r['failures'],'quotes':r['quotes']}))


if __name__=='__main__':main()
