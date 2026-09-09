"""P3 public-only current capture; historical bars receive today's knowledge time."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse
import json
import time
import subprocess
import sys

from services.research.profitability_information import synchronized_basis,basis_change
from tools.profitability_study import write_new
from tools.perpetual_replay_research import source_identity


def bounded_public_json(url):
    # A socket timeout is not a total deadline across DNS addresses and retries.
    # Keep the approved public collector in a child with a real wall-clock limit.
    if not url.startswith(('https://api.binance.com/api/v3/klines?',
                           'https://fapi.binance.com/fapi/v1/klines?',
                           'https://fapi.binance.com/fapi/v1/markPriceKlines?',
                           'https://fapi.binance.com/fapi/v1/fundingRate?',
                           'https://fapi.binance.com/fapi/v1/openInterest?',
                           'https://fapi.binance.com/fapi/v1/indexPriceKlines?')):
        raise ValueError('unregistered public source')
    code='import json,sys; from tools.binance_realtime_market import _http_get_json; print(json.dumps(_http_get_json(sys.argv[1])))'
    result=subprocess.run([sys.executable,'-c',code,url],cwd=Path(__file__).resolve().parents[1],
                          capture_output=True,text=True,timeout=45,check=True)
    return json.loads(result.stdout)


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
