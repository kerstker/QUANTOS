"""Restart-safe, bounded multi-asset public observer. Not an execution controller."""
import argparse
import fcntl
import json
import hashlib
import os
from pathlib import Path
import re
import time

from services.data.kraken_public import INTERVALS, require
from services.data.kraken_units import asset_units
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools import kraken_public_capture as capture


def validate_config(config):
    require(isinstance(config,dict) and set(config)=={'schema','interval','poll_seconds','instruments'},'KRAKEN_OBSERVER_CONFIG_FIELDS')
    require(config['schema']=='KRAKEN_OBSERVER_CONFIG_V1','KRAKEN_OBSERVER_CONFIG_VERSION')
    require(type(config['interval']) is int and config['interval'] in INTERVALS,'KRAKEN_OBSERVER_INTERVAL')
    require(type(config['poll_seconds']) is int and 60<=config['poll_seconds']<=86400,'KRAKEN_OBSERVER_POLL')
    items=config['instruments']
    require(isinstance(items,list) and 1<=len(items)<=32,'KRAKEN_OBSERVER_UNIVERSE')
    seen=set()
    for item in items:
        require(isinstance(item,dict) and set(item)=={'pair','asset_class'},'KRAKEN_OBSERVER_INSTRUMENT')
        require(item['asset_class'] in ('currency','tokenized_asset') and isinstance(item['pair'],str) and
                bool(re.fullmatch(r'[A-Za-z0-9.]{1,20}/[A-Za-z0-9.]{1,20}',item['pair'])),'KRAKEN_OBSERVER_PRODUCT')
        key=(item['pair'],item['asset_class']);require(key not in seen,'KRAKEN_OBSERVER_DUPLICATE');seen.add(key)
    return config


def run_once(root, config, *, collect=None, clock_ms=None, metadata_get=None):
    validate_config(config);collect=collect or capture.collect
    clock_ms=clock_ms or (lambda:time.time_ns()//1_000_000)
    root=Path(root).absolute();require(root.resolve()==root,'KRAKEN_OBSERVER_SYMLINK')
    root.mkdir(parents=True,exist_ok=True)
    for name in ('config.json','status.json','cycles','instruments'):
        require(not (root/name).is_symlink(),'KRAKEN_OBSERVER_SYMLINK')
    fd=os.open(root/'.observer.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        path=root/'config.json'
        if path.exists():require(load_json_without_duplicate_keys(path.read_bytes())==config,'KRAKEN_OBSERVER_CONFIG_CHANGED')
        else:capture.atomic_json(path,config)
        started=clock_ms();records=[]
        metadata_get=metadata_get or capture.public_get
        for item in config['instruments']:
            record=dict(item,status='FAILED',failure=None,bars_observed=0,bars_inserted=0)
            try:
                raw=collect(**item,interval=config['interval'])
                require(all(raw[k]==v for k,v in item.items()) and raw['interval']==config['interval'],'KRAKEN_OBSERVER_CAPTURE_IDENTITY')
                summary=capture.persist(root/'instruments'/capture.digest(item),raw)
                record.update(summary)
                if raw['status']=='CAPTURED':
                    spec=raw['instrument'];params={'asset':spec['base'],'aclass':spec['asset_class']}
                    sent=clock_ms();body=metadata_get('Assets',params);received=clock_ms()
                    require(isinstance(body,bytes) and len(body)<=capture.MAX_BYTES,'KRAKEN_UNITS_SIZE')
                    require(started<=sent<=received,'KRAKEN_UNITS_CLOCK')
                    record['units_read']={'endpoint':'Assets','params':params,'requested_at_ms':sent,
                        'received_at_ms':received,'body':body.decode('utf-8'),
                        'body_sha256':hashlib.sha256(body).hexdigest()}
                    record['units']=asset_units(load_json_without_duplicate_keys(body),spec=spec,
                        received_at_ms=received,as_of_ms=clock_ms())
                    at=clock_ms();last_close=max(r['event_time'] for r in raw['rows'])+config['interval']*60000
                    record.update(last_closed_at_ms=last_close,market_status=raw['instrument']['status'],
                                  instrument_id=raw['instrument']['instrument_id'])
                    if not 0<=at-raw['completed_at_ms']<=max(120000,config['poll_seconds']*2000) or not 0<=at-last_close<=config['interval']*120000:
                        record['status']='STALE'
                    elif raw['instrument']['status']!='online':record['status']='MARKET_UNAVAILABLE'
            except (OSError,ValueError,TypeError,KeyError) as exc:
                record.update(status='FAILED',failure={'code':'OBSERVATION_FAILED','error_type':type(exc).__name__})
            records.append(record)
        completed=clock_ms();require(completed>=started,'KRAKEN_OBSERVER_CLOCK')
        for record in records:
            if record['status']=='CAPTURED' and not 0<=completed-record['units']['received_at_ms']<=120000:
                record['status']='STALE'
        path=root/'status.json';generation=1
        if path.exists():
            previous=load_json_without_duplicate_keys(path.read_bytes())
            require(completed>=previous['observed_at_ms'],'KRAKEN_OBSERVER_CLOCK_ROLLBACK')
            generation=previous['generation']+1
        report={'schema':'KRAKEN_PORTFOLIO_OBSERVER_V2','generation':generation,'started_at_ms':started,
                'observed_at_ms':completed,'config_sha256':capture.digest(config),'instruments':records,
                'status':'CAPTURED' if all(r['status']=='CAPTURED' for r in records) else 'PARTIAL',
                'scope':'PUBLIC_MARKET_OBSERVATION_ONLY','account_status':'NOT_CONNECTED',
                'strategy_status':'NOT_CONFIGURED','portfolio_management_ready':False,
                'submission_enabled':False,'authoritative':False}
        report['result_hash']=capture.digest(report)
        archive=root/'cycles';archive.mkdir(exist_ok=True)
        capture.atomic_json(archive/(report['result_hash']+'.json'),report)
        capture.atomic_json(path,report)
        return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--state-root',required=True,type=Path)
    parser.add_argument('--cycles',type=int,default=1)
    args=parser.parse_args(argv)
    require(1<=args.cycles<=1440,'KRAKEN_OBSERVER_CYCLE_LIMIT')
    config=validate_config(load_json_without_duplicate_keys(args.config.read_bytes()))
    failures=0
    for index in range(args.cycles):
        if index:time.sleep(config['poll_seconds'])
        report=run_once(args.state_root,config)
        failures+=report['status']!='CAPTURED'
        print(json.dumps({'generation':report['generation'],'status':report['status'],
                          'result_hash':report['result_hash'],'submission_enabled':False}),flush=True)
    return 3 if failures else 0


if __name__=='__main__':raise SystemExit(main())
