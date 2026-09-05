"""Bounded, keyless Binance public observations for offline technical replay.

Only fixed GET market-data endpoints are reachable. No account, SDK, credential,
private endpoint, or trading action is supported. Capture files are append-once.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
from pathlib import Path
import time
import urllib.request
from services.execution.backtest_authority import exact_identity_hash

HOST='https://fapi.binance.com'
DAY_MS=86_400_000
SCHEMA='BINANCE_PUBLIC_REPLAY_CAPTURE_V2'


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('public capture redirects are forbidden')


def capture_public(*, samples: int=8, interval_seconds: int=1) -> dict:
    if type(samples) is not int or not 3<=samples<=60:
        raise ValueError('samples must be between 3 and 60')
    if type(interval_seconds) is not int or not 1<=interval_seconds<=10:
        raise ValueError('interval must be between 1 and 10 seconds')
    def fetch(kind,path):
        requested=int(time.time()*1000)
        request=urllib.request.Request(HOST+path,headers={'Accept':'application/json'},method='GET')
        with urllib.request.build_opener(_NoRedirect()).open(request,timeout=15) as response:
            if not response.geturl().startswith(HOST+'/fapi/v1/'):
                raise ValueError('unexpected public-data redirect')
            raw=response.read(10_000_001)
        received=int(time.time()*1000)
        if len(raw)>10_000_000: raise ValueError('public response exceeds size budget')
        return {'kind':kind,'requested_at':requested,'received_at':received,
                'endpoint':path,'payload':json.loads(raw),
                'response_sha256':hashlib.sha256(raw).hexdigest(),
                'response_base64':base64.b64encode(raw).decode('ascii')}
    day=int(time.time()*1000)//DAY_MS*DAY_MS
    bootstrap={
        'exchange':fetch('exchange','/fapi/v1/exchangeInfo'),
        'funding':fetch('funding','/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1'),
        'candles':fetch('candles',f'/fapi/v1/klines?symbol=BTCUSDT&interval=1d&startTime={day-30*DAY_MS}&endTime={day-1}&limit=30'),
    }
    observations=[]
    for index in range(samples):
        if index: time.sleep(interval_seconds)
        observations.append({
            'mark':fetch('mark','/fapi/v1/premiumIndex?symbol=BTCUSDT'),
            'depth':fetch('depth','/fapi/v1/depth?symbol=BTCUSDT&limit=5'),
        })
    after=fetch('exchange','/fapi/v1/exchangeInfo')
    document={'schema_version':SCHEMA,'symbol':'BTCUSDT','bootstrap':bootstrap,
              'samples':observations,'exchange_after':after,'max_gap_ms':30_000,
              'declared_interval_ms':interval_seconds*1000}
    document['capture_id']=exact_identity_hash(document,name='public_replay_capture')
    return document


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--samples',type=int,default=8)
    args=parser.parse_args(argv)
    if args.output.exists(): raise ValueError('refusing to replace frozen capture')
    data=capture_public(samples=args.samples)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as handle: json.dump(data,handle,sort_keys=True,allow_nan=False)
    print(json.dumps({'capture_id':data['capture_id'],'samples':len(data['samples']),'output':str(args.output)}))
    return 0


if __name__=='__main__': raise SystemExit(main())
