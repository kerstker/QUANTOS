"""Bounded public Kraken observation, append-only evidence and restart-safe PIT ingestion.

Only allowlisted public GET endpoints; no account, credentials, order or transfer surface.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.parse
import urllib.request

from services.data import kraken_public as model
from services.data.market_provider import ingest_provider_rows
from services.data.persistent_store import SqlitePITStore
from services.governance.json_integrity import load_json_without_duplicate_keys

MAX_BYTES = 4*1024*1024
BASE = 'https://api.kraken.com/0/public/'


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('KRAKEN_REDIRECT_REFUSED')


def public_get(endpoint, params):
    allowed={'AssetPairs': {'pair','aclass_base'}, 'OHLC': {'pair','interval','asset_class'}, 'Assets': {'asset','aclass'}, 'Ticker': {'pair','asset_class'}}
    model.require(endpoint in allowed and set(params)<=allowed[endpoint], 'KRAKEN_PUBLIC_ENDPOINT_ONLY')
    url=BASE+endpoint+'?'+urllib.parse.urlencode(params)
    request=urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'QUANTOS-public-research/1'},method='GET')
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=15) as response:
        model.require(response.status==200 and response.geturl()==url,'KRAKEN_HTTP_RESPONSE')
        body=response.read(MAX_BYTES+1)
    model.require(len(body)<=MAX_BYTES,'KRAKEN_RESPONSE_SIZE')
    return body


def collect(*, pair, asset_class, interval=60, get=None, clock_ms=None):
    get=get or public_get;clock_ms=clock_ms or (lambda:time.time_ns()//1_000_000)
    model.require(asset_class in ('currency','tokenized_asset') and interval in model.INTERVALS,'KRAKEN_REQUEST')
    started=clock_ms();reads=[]
    report={'schema':'KRAKEN_PUBLIC_CAPTURE_V1','pair':pair,'asset_class':asset_class,'interval':interval,
            'observed_at_ms':started,'reads':reads,'status':'FAILED','instrument':None,'rows':[],
            'failure':None,'submission_enabled':False,'authoritative':False}
    def read(endpoint, params):
        sent=clock_ms();body=get(endpoint,params);received=clock_ms()
        model.require(isinstance(body,bytes) and len(body)<=MAX_BYTES,'KRAKEN_RESPONSE_SIZE')
        model.require(started<=sent<=received,'KRAKEN_LOCAL_CLOCK')
        reads.append({'endpoint':endpoint,'params':params,'requested_at_ms':sent,'received_at_ms':received,
                      'body':body.decode('utf-8'),'body_sha256':hashlib.sha256(body).hexdigest()})
        return load_json_without_duplicate_keys(body),received
    try:
        payload,_=read('AssetPairs',{'pair':pair.replace('/',''),'aclass_base':asset_class})
        spec=model.instrument(payload,pair=pair,asset_class=asset_class)
        params={'pair':spec['altname'],'interval':interval}
        if asset_class=='tokenized_asset':params['asset_class']=asset_class
        payload,received=read('OHLC',params)
        rows=model.closed_rows(payload,spec=spec,interval=interval,received_at_ms=received)
        report.update(status='CAPTURED',instrument=spec,rows=rows)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        report['failure']={'code':'PUBLIC_CAPTURE_FAILED','error_type':type(exc).__name__}
    report['completed_at_ms']=clock_ms()
    model.require(report['completed_at_ms']>=started,'KRAKEN_LOCAL_CLOCK')
    report['result_hash']=digest(report)
    return report


def validate(raw):
    unsigned={k:v for k,v in raw.items() if k!='result_hash'}
    model.require(raw.get('result_hash')==digest(unsigned) and raw.get('schema')=='KRAKEN_PUBLIC_CAPTURE_V1', 'KRAKEN_CAPTURE_HASH')
    model.require(raw.get('authoritative') is False and raw.get('submission_enabled') is False,'KRAKEN_CAPTURE_BOUNDARY')
    model.require(type(raw.get('observed_at_ms')) is int and type(raw.get('completed_at_ms')) is int and
                  0<raw['observed_at_ms']<=raw['completed_at_ms'],'KRAKEN_CAPTURE_CLOCK')
    model.require(isinstance(raw.get('reads'),list) and len(raw['reads'])<=2,'KRAKEN_CAPTURE_READS')
    previous=raw['observed_at_ms']
    for read in raw['reads']:
        body=read['body'].encode('utf-8')
        model.require(len(body)<=MAX_BYTES and read['body_sha256']==hashlib.sha256(body).hexdigest(),'KRAKEN_RAW_HASH')
        model.require(previous<=read['requested_at_ms']<=read['received_at_ms']<=raw['completed_at_ms'],'KRAKEN_CAPTURE_CLOCK')
        previous=read['received_at_ms']
    if raw['status']=='CAPTURED':
        model.require(len(raw['reads'])==2 and raw['failure'] is None,'KRAKEN_CAPTURE_READS')
        catalog,market=raw['reads']
        model.require(catalog['endpoint']=='AssetPairs' and catalog['params']=={
            'pair':raw['pair'].replace('/',''),'aclass_base':raw['asset_class']},'KRAKEN_CATALOG_REQUEST')
        spec=model.instrument(load_json_without_duplicate_keys(catalog['body']),pair=raw['pair'],asset_class=raw['asset_class'])
        expected={'pair':spec['altname'],'interval':raw['interval']}
        if raw['asset_class']=='tokenized_asset':expected['asset_class']=raw['asset_class']
        model.require(market['endpoint']=='OHLC' and market['params']==expected,'KRAKEN_MARKET_REQUEST')
        rows=model.closed_rows(load_json_without_duplicate_keys(market['body']),spec=spec,
                               interval=raw['interval'],received_at_ms=market['received_at_ms'])
        model.require(raw['instrument']==spec and raw['rows']==rows,'KRAKEN_CAPTURE_REPLAY')
    else:
        model.require(raw['status']=='FAILED' and raw['instrument'] is None and raw['rows']==[] and
                      isinstance(raw['failure'],dict),'KRAKEN_FAILURE_SHAPE')
    return raw


def atomic_json(path, value):
    temporary=path.with_name(path.name+'.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(value,stream,sort_keys=True,allow_nan=False);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)


def persist(root, raw):
    validate(raw)
    root=Path(root).absolute()
    model.require(root.resolve()==root,'KRAKEN_STATE_SYMLINK')
    root.mkdir(parents=True,exist_ok=True)
    for name in ('market.db','market.db-wal','market.db-shm','market.db-journal','captures','config.json','latest-success.json','latest-attempt.json'):
        model.require(not (root/name).is_symlink(),'KRAKEN_STATE_SYMLINK')
    fd=os.open(root/'.capture.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        config={k:raw[k] for k in ('pair','asset_class','interval')}
        config_path=root/'config.json'
        if config_path.exists():
            model.require(load_json_without_duplicate_keys(config_path.read_bytes())==config,'KRAKEN_STATE_IDENTITY')
        else:atomic_json(config_path,config)
        archive=root/'captures';archive.mkdir(exist_ok=True)
        target=archive/(raw['result_hash']+'.json')
        if target.exists():
            model.require(not target.is_symlink() and load_json_without_duplicate_keys(target.read_bytes())==raw,'KRAKEN_ARCHIVE_CONFLICT')
        else:atomic_json(target,raw)
        inserted=0
        if raw['status']=='CAPTURED':
            store=SqlitePITStore(str(root/'market.db'),create=True)
            try:
                key=raw['instrument']['instrument_id']
                prior={bar.event_time:bar for bar in store.as_of(key,raw['completed_at_ms'])}
                fields=('open','high','low','close','volume')
                rows=[row for row in raw['rows'] if row['event_time'] not in prior or
                      any(getattr(prior[row['event_time']],field)!=row[field] for field in fields)]
                if rows:
                    rep=ingest_provider_rows(rows,store,now=raw['completed_at_ms'],dataset_id=key,
                                             version=raw['result_hash'],time_unit='ms',strict=True)
                    model.require(not rep.rejected,'KRAKEN_INGEST_REJECTED');inserted=rep.bars_ingested
            finally:store._conn.close()
        summary={k:raw[k] for k in ('schema','status','pair','asset_class','completed_at_ms','result_hash','failure','submission_enabled','authoritative')}
        summary.update(bars_observed=len(raw['rows']),bars_inserted=inserted)
        def publish(name):
            path=root/name
            if path.exists():
                previous=load_json_without_duplicate_keys(path.read_bytes())
                if previous['completed_at_ms']>=summary['completed_at_ms']:return
            atomic_json(path,summary)
        publish('latest-attempt.json')
        if raw['status']=='CAPTURED':publish('latest-success.json')
        return summary


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root',required=True,type=Path)
    parser.add_argument('--pair',required=True)
    parser.add_argument('--asset-class',required=True,choices=('currency','tokenized_asset'))
    parser.add_argument('--interval',type=int,default=60,choices=model.INTERVALS)
    args=parser.parse_args(argv)
    summary=persist(args.state_root,collect(pair=args.pair,asset_class=args.asset_class,interval=args.interval))
    print(json.dumps(summary,sort_keys=True));return 0 if summary['status']=='CAPTURED' else 3


if __name__=='__main__':
    raise SystemExit(main())
