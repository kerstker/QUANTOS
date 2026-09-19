"""Offline inventory from an operator account export and archived public observer cycle."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time

from services.data.kraken_inventory import reconcile
from services.data.kraken_quotes import parse as parse_quote
from services.portfolio.kraken_valuation import value as value_inventory
from services.portfolio.kraken_allocation import prepare as prepare_allocation, validate_policy
from services.data.kraken_public import require
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools import kraken_public_capture as capture


def load(path):
    with Path(path).open('rb') as stream:body=stream.read(16*1024*1024+1)
    require(len(body)<=16*1024*1024,'KRAKEN_INVENTORY_INPUT_SIZE')
    return load_json_without_duplicate_keys(body)


def sealed(report):
    require(isinstance(report,dict) and report.get('result_hash')==capture.digest(
        {k:v for k,v in report.items() if k!='result_hash'}),'KRAKEN_INVENTORY_HASH')
    require(report.get('submission_enabled') is False and report.get('authoritative') is False,
            'KRAKEN_INVENTORY_BOUNDARY')
    return report


def build(account_path, observer_root, *, as_of_ms, valuation_quote=None, policy=None):
    if policy is not None:
        validate_policy(policy)
        require(valuation_quote is None or valuation_quote==policy['quote_asset'],'KRAKEN_POLICY_QUOTE_MISMATCH')
        valuation_quote=policy['quote_asset']
    account=sealed(load(account_path));root=Path(observer_root)
    observer=sealed(load(root/'status.json'))
    require(observer.get('schema') in ('KRAKEN_PORTFOLIO_OBSERVER_V2','KRAKEN_PORTFOLIO_OBSERVER_V3'),'KRAKEN_INVENTORY_OBSERVER_VERSION')
    require(type(observer.get('observed_at_ms')) is int and observer['observed_at_ms']<=as_of_ms,
            'KRAKEN_INVENTORY_FUTURE_OBSERVER')
    markets=[];issues=[];quotes=[];quote_issues=[]
    if observer['status']!='CAPTURED':issues.append('MARKET_OBSERVATION_PARTIAL')
    if as_of_ms-observer['observed_at_ms']>120000:issues.append('MARKET_OBSERVATION_STALE')
    for record in observer['instruments']:
        if record['status']!='CAPTURED':continue
        key=record['result_hash']
        require(isinstance(key,str) and re.fullmatch('[a-f0-9]{64}',key),'KRAKEN_INVENTORY_CAPTURE_KEY')
        item={k:record[k] for k in ('pair','asset_class')}
        raw=capture.validate(load(root/'instruments'/capture.digest(item)/'captures'/(key+'.json')))
        require(raw['status']=='CAPTURED' and raw['result_hash']==key and
                raw['completed_at_ms']<=observer['observed_at_ms'] and
                all(raw[k]==v for k,v in item.items()),'KRAKEN_INVENTORY_CAPTURE_BINDING')
        spec=raw['instrument'];read=record['units_read'];body=read['body'].encode()
        require(read['endpoint']=='Assets' and read['params']=={'asset':spec['base'],'aclass':spec['asset_class']} and
                read['body_sha256']==hashlib.sha256(body).hexdigest(),'KRAKEN_INVENTORY_UNITS_BINDING')
        require(type(read['requested_at_ms']) is int and type(read['received_at_ms']) is int and
                observer['started_at_ms']<=read['requested_at_ms']<=read['received_at_ms']<=observer['observed_at_ms'],
                'KRAKEN_INVENTORY_UNITS_CLOCK')
        markets.append({'spec':spec,'payload':load_json_without_duplicate_keys(body),
                        'received_at_ms':read['received_at_ms']})
        if valuation_quote is not None:
            try:
                read=record['quote_read'];body=read['body'].encode();params={'pair':spec['altname']}
                if spec['asset_class']=='tokenized_asset':params['asset_class']='tokenized_asset'
                require(read['endpoint']=='Ticker' and read['params']==params and
                    read['body_sha256']==hashlib.sha256(body).hexdigest(),'KRAKEN_VALUE_QUOTE_BINDING')
                require(type(read['requested_at_ms']) is int and type(read['received_at_ms']) is int and
                    observer['started_at_ms']<=read['requested_at_ms']<=read['received_at_ms']<=observer['observed_at_ms'],
                    'KRAKEN_VALUE_QUOTE_CLOCK')
                quotes.append(parse_quote(load_json_without_duplicate_keys(body),spec=spec,
                    received_at_ms=read['received_at_ms'],as_of_ms=as_of_ms))
            except (KeyError,ValueError,TypeError):quote_issues.append('QUOTE_UNAVAILABLE:'+spec['instrument_id'])
    report=reconcile(account,markets,as_of_ms=as_of_ms)
    report['issues']=sorted(set(report['issues']+issues))
    if report['issues']:report['status']='UNRESOLVED'
    report.update(account_source_hash=account['result_hash'],observer_source_hash=observer['result_hash'])
    if valuation_quote is not None:
        valuation=value_inventory(report,quotes,quote_asset=valuation_quote,as_of_ms=as_of_ms)
        if quote_issues:
            valuation['issues']=sorted(set(valuation['issues']+quote_issues))
            valuation.update(status='INCOMPLETE',observed_spot_mid_value=None,observed_spot_bid_value=None)
        report['indicative_valuation']=valuation
        report['planning_quotes']=quotes
    if policy is not None:
        report['allocation_review']=prepare_allocation(report,policy,as_of_ms=as_of_ms)
        report['allocation_policy_sha256']=capture.digest(policy)
    report['result_hash']=capture.digest(report)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account',type=Path,required=True)
    parser.add_argument('--observer-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--valuation-quote',help='Exact Kraken quote asset identifier, e.g. ZUSD')
    parser.add_argument('--policy',type=Path,help='Explicit allocation policy; no defaults or execution permission')
    args=parser.parse_args(argv)
    report=build(args.account,args.observer_root,as_of_ms=time.time_ns()//1_000_000,valuation_quote=args.valuation_quote,policy=load(args.policy) if args.policy else None)
    # No replacement of earlier evidence; exported balances are private.
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(report,stream,sort_keys=True,allow_nan=False);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    print(json.dumps({'status':report['status'],'valuation_status':report.get('indicative_valuation',{}).get('status'),
                      'allocation_status':report.get('allocation_review',{}).get('status'),
                      'issues':report['issues'],'output':str(args.output),
                      'submission_enabled':False}))
    return 0 if report['status']=='INVENTORY_MAPPED' and report.get('indicative_valuation',{}).get('status','INDICATIVE')=='INDICATIVE' and report.get('allocation_review',{}).get('status','REVIEW_ONLY')=='REVIEW_ONLY' else 3


if __name__=='__main__':raise SystemExit(main())
