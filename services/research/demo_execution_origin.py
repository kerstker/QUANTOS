"""Offline validation of one Demo account namespace and bounded execution inputs.

A flat quote observation defines no historical profit or settlement rounding
policy. Raw account V1 contracts remain unchanged and retain their old identity.
"""
from decimal import Decimal
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from quantos.contracts.binance_demo_account import (
    AccountRead, DemoAccountCapture, READ_KINDS as ACCOUNT_KINDS,
    normalize_account_read, _bool, _decimal, _int, _rows, _side, _symbol, _text,
)
from services.research.demo_account_baseline import build_baseline
from services.research.demo_statement import normalize as normalize_terms, identity
from services.governance.json_integrity import load_json_without_duplicate_keys

SCHEMA='DEMO_EXECUTION_ORIGIN_V1'
TERMS_SCHEMA='DEMO_EXECUTION_ORIGIN_V2'
FULL_TERMS_SCHEMA='DEMO_EXECUTION_ORIGIN_V3'
KINDS=('clock_before',*ACCOUNT_KINDS[:5],'algo_orders','rules','fees','brackets','mark','book',*ACCOUNT_KINDS[5:],'clock_after')
PUBLIC=frozenset(('clock_before','clock_after','rules','mark','book'))
MAX_BYTES=8_000_000
REASONS=('REQUEST_FAILED','AUTHENTICATION_REJECTED','RATE_LIMITED','RESPONSE_INVALID','CLOCK_UNRELIABLE',
         'CAPTURE_BUDGET_EXHAUSTED','RESPONSE_TOO_LARGE')


def fields(value,names):
    if type(value) is not dict or set(value)!=set(names.split()):raise ValueError('ORIGIN_FIELDS')


def sha(value):
    if not isinstance(value,str) or re.fullmatch('[a-f0-9]{64}',value) is None:raise ValueError('ORIGIN_HASH')


def venue_filters(value):
    """Retain bounded public filter scalars, including unknown future filters."""
    if type(value) is not list or not 1<=len(value)<=32:raise ValueError('ORIGIN_FILTERS')
    seen=set()
    for row in value:
        if type(row) is not dict or not 1<=len(row)<=32:raise ValueError('ORIGIN_FILTERS')
        kind=_text(row['filterType'])
        if kind in seen:raise ValueError('ORIGIN_FILTERS')
        seen.add(kind)
        for key,v in row.items():
            _text(key,r'[A-Za-z][A-Za-z0-9_]{0,63}')
            if type(v) is int:_int(v)
            elif type(v) is str:_text(v,r'[A-Za-z0-9_.+-]{1,128}')
            elif type(v) is not bool:raise ValueError('ORIGIN_FILTER_SCALAR')
    return json.loads(json.dumps(value,allow_nan=False))


def normalize(kind,raw,symbol):
    if kind in ACCOUNT_KINDS:return normalize_account_read(kind,raw)
    if kind.startswith('clock'):return {'serverTime':_int(raw['serverTime'])}
    if kind=='algo_orders':
        rows=_rows(raw,{'algoId':identity,'clientAlgoId':lambda v:_text(v,r'[.A-Z:/a-z0-9_-]{1,36}'),
            'algoType':_text,'orderType':_text,'symbol':_symbol,'side':_text,'positionSide':_side,'quantity':_decimal,
            'algoStatus':_text,'reduceOnly':_bool,'closePosition':_bool,'createTime':_int,'updateTime':_int},('symbol','algoId'))
        if any(int(r['algoId'])<=0 or r['side'] not in ('BUY','SELL') or r['algoType']!='CONDITIONAL' or
               Decimal(r['quantity'])<0 or r['updateTime']<r['createTime'] for r in rows):
            raise ValueError('ORIGIN_ALGO_ORDER')
        return rows
    if kind=='rules':
        chosen=[r for r in raw['symbols'] if r['symbol']==symbol]
        if len(chosen)!=1:raise ValueError('ORIGIN_RULES_MISSING')
        r=chosen[0]
        return {'terms':normalize_terms('exchange',{'symbols':chosen},[symbol])[0],
                'precision':{k:_int(r[k]) for k in ('pricePrecision','quantityPrecision','baseAssetPrecision','quotePrecision')},
                'order_types':[_text(v) for v in r['orderTypes']], 'time_in_forces':[_text(v) for v in r['timeInForce']],
                'filter_types':[_text(f['filterType']) for f in r['filters']],
                'venue_filters':venue_filters(r['filters']),
                'order_count_limits':{f['filterType']:_int(f['limit']) for f in r['filters']
                                      if f['filterType'] in ('MAX_NUM_ORDERS','MAX_NUM_ALGO_ORDERS')}}
    if kind=='fees':
        result=normalize_terms('commission',raw,[symbol])
        if not -1<=Decimal(result['makerCommissionRate'])<=1 or not 0<=Decimal(result['takerCommissionRate'])<=1:
            raise ValueError('ORIGIN_FEES')
        return result
    if kind=='brackets':
        result=normalize_terms('brackets',raw,[symbol])
        if len(result)!=1 or not 1<=len(result[0]['brackets'])<=20:raise ValueError('ORIGIN_BRACKETS')
        return result
    if kind in ('mark','book'):
        if raw['symbol']!=symbol:raise ValueError('ORIGIN_MARKET_SYMBOL')
        amounts=('markPrice','indexPrice','lastFundingRate') if kind=='mark' else ('bidPrice','askPrice','bidQty','askQty')
        return {'symbol':symbol,**{k:_decimal(raw[k]) for k in amounts},'time':_int(raw['time']),
                **({'nextFundingTime':_int(raw['nextFundingTime'])} if kind=='mark' else {})}
    raise ValueError('ORIGIN_KIND')


def _check_data(read,symbol,schema):
    kind,data=read['kind'],read['data']
    if kind in ACCOUNT_KINDS:
        AccountRead(kind=kind,started_at_ms=read['requested_at_ms'],received_at_ms=read['received_at_ms'],
                    response_sha256=read['response_sha256'],data=data)
    elif kind=='rules':
        fields(data,'terms precision order_types time_in_forces filter_types'+(' order_count_limits' if schema in (TERMS_SCHEMA,FULL_TERMS_SCHEMA) else '')+(' venue_filters' if schema==FULL_TERMS_SCHEMA else ''))
        fields(data['precision'],'pricePrecision quantityPrecision baseAssetPrecision quotePrecision')
        if any(_int(v)>24 for v in data['precision'].values()):raise ValueError('ORIGIN_PRECISION')
        for name in ('order_types','time_in_forces','filter_types'):
            values=data[name]
            if type(values) is not list or not 1<=len(values)<=32 or len(set(values))!=len(values):raise ValueError('ORIGIN_RULE_LIST')
            for v in values:_text(v)
        if schema in (TERMS_SCHEMA,FULL_TERMS_SCHEMA):
            caps=data['order_count_limits']
            if type(caps) is not dict or set(caps)!=set(data['filter_types']) & {'MAX_NUM_ORDERS','MAX_NUM_ALGO_ORDERS'}:
                raise ValueError('ORIGIN_ORDER_COUNT_LIMITS')
            for value in caps.values():_int(value)
        if schema==FULL_TERMS_SCHEMA:
            complete=venue_filters(data['venue_filters'])
            if ([r['filterType'] for r in complete]!=data['filter_types'] or
                {r['filterType']:_int(r['limit']) for r in complete if r['filterType'] in ('MAX_NUM_ORDERS','MAX_NUM_ALGO_ORDERS')}!=data['order_count_limits'] or
                normalize_terms('exchange',{'symbols':[dict(data['terms'],filters=complete)]},[symbol])!=[data['terms']]):
                raise ValueError('ORIGIN_FILTER_PROJECTION')
        if normalize_terms('exchange',{'symbols':[data['terms']]},[symbol])!=[data['terms']]:raise ValueError('ORIGIN_RULE_SHAPE')
    elif normalize(kind,data,symbol)!=data:raise ValueError('ORIGIN_NONCANONICAL_DATA')


def validate(raw):
    fields(raw,'schema status symbol observed_at_ms completed_at_ms account_ref_sha256 source_sha256 reads failures')
    if raw['schema'] not in (SCHEMA,TERMS_SCHEMA,FULL_TERMS_SCHEMA) or raw['symbol'] not in ('BTCUSDT','BTCUSDC') or raw['status'] not in ('NOT_REQUESTED','CAPTURED','FAILED'):
        raise ValueError('ORIGIN_SCHEMA')
    start,end=_int(raw['observed_at_ms']),_int(raw['completed_at_ms'])
    if not 0<start<=end<=start+120000:raise ValueError('ORIGIN_WINDOW')
    sha(raw['source_sha256'])
    if raw['account_ref_sha256'] is not None:sha(raw['account_ref_sha256'])
    reads,failures=raw['reads'],raw['failures']
    if type(reads) is not list or len(reads)>len(KINDS) or type(failures) is not list or len(failures)>1:raise ValueError('ORIGIN_BOUNDS')
    previous=start;offsets=[]
    for i,read in enumerate(reads):
        fields(read,'kind requested_at_ms received_at_ms account_ref_sha256 response_sha256 data')
        if read['kind']!=KINDS[i]:raise ValueError('ORIGIN_READ_ORDER')
        sent,received=_int(read['requested_at_ms']),_int(read['received_at_ms'])
        if not previous<=sent<=received<=min(end,sent+10000):raise ValueError('ORIGIN_READ_CLOCK')
        previous=received;sha(read['response_sha256'])
        expected=None if read['kind'] in PUBLIC else raw['account_ref_sha256']
        if read['account_ref_sha256']!=expected or read['kind'] not in PUBLIC and expected is None:raise ValueError('ORIGIN_ACCOUNT_IDENTITY')
        _check_data(read,raw['symbol'],raw['schema'])
        if read['kind'].startswith('clock'):
            offset=read['data']['serverTime']-(sent+received)//2
            if received-sent>5000 or abs(offset)>2000:raise ValueError('ORIGIN_CLOCK_UNRELIABLE')
            offsets.append(offset)
    if len(offsets)==2 and abs(offsets[0]-offsets[1])>500:raise ValueError('ORIGIN_CLOCK_DRIFT')
    for f in failures:
        fields(f,'kind requested_at_ms received_at_ms reason response_sha256 server_time_ms')
        if f['kind'] not in (*KINDS,'credentials','worker') or f['reason'] not in REASONS:raise ValueError('ORIGIN_FAILURE')
        expected=KINDS[len(reads)] if len(reads)<len(KINDS) else None
        if f['kind']!='worker' and f['kind']!=expected and not (f['kind']=='credentials' and not reads):raise ValueError('ORIGIN_FAILURE_ORDER')
        if f['response_sha256'] is not None:sha(f['response_sha256'])
        if f['server_time_ms'] is not None:
            _int(f['server_time_ms'])
            if not f['kind'].startswith('clock') or f['response_sha256'] is None:raise ValueError('ORIGIN_FAILURE_CLOCK_EVIDENCE')
        if not previous<=_int(f['requested_at_ms'])<=_int(f['received_at_ms'])<=end:raise ValueError('ORIGIN_FAILURE_CLOCK')
    if raw['status']=='CAPTURED' and (len(reads)!=len(KINDS) or failures):raise ValueError('ORIGIN_INCOMPLETE')
    if raw['status']=='FAILED' and not failures:raise ValueError('ORIGIN_FAILURE_MISSING')
    if raw['status']=='NOT_REQUESTED' and (reads or failures or raw['account_ref_sha256'] is not None):raise ValueError('ORIGIN_NOT_OFFLINE')
    return raw


def account_capture(raw):
    """Reuse the unchanged V1 account model, with this capture's source identity."""
    validate(raw)
    reads=[AccountRead(**{k:v for k,v in r.items() if k not in ('account_ref_sha256','requested_at_ms')},
                       started_at_ms=r['requested_at_ms']) for r in raw['reads'] if r['kind'] in ACCOUNT_KINDS]
    complete=len(reads)==len(ACCOUNT_KINDS)
    return DemoAccountCapture(observed_at_ms=raw['observed_at_ms'],completed_at_ms=raw['completed_at_ms'],
        source_sha256=raw['source_sha256'],status='CAPTURED' if complete else 'FAILED',
        reason='CAPTURE_COMPLETE' if complete else 'REQUEST_FAILED',reads=reads)


def assess(raw, *, as_of_ms):
    validate(raw)
    if _int(as_of_ms)<raw['completed_at_ms']:raise ValueError('ORIGIN_FUTURE')
    issues=[];data={r['kind']:r['data'] for r in raw['reads']};quote=raw['symbol'][3:]
    # Separate historical bracket consistency from the current freshness test.
    baseline=build_baseline(account_capture(raw),as_of_ms=raw['completed_at_ms'])
    if raw['status']!='CAPTURED':issues.append('CAPTURE_INCOMPLETE')
    if as_of_ms-raw['observed_at_ms']>30000:issues.append('ORIGIN_STALE')
    if baseline['inventory_status']!='STABLE_OBSERVATIONS':issues.append('ACCOUNT_NOT_STABLE')
    config=data.get('config_after',{})
    if config.get('canTrade') is not True:issues.append('ACCOUNT_TRADING_UNAVAILABLE')
    if config.get('dualSidePosition') is not False:issues.append('HEDGE_MODE_UNSUPPORTED')
    if config.get('multiAssetsMargin') is not False:issues.append('MULTI_ASSET_MARGIN_UNSUPPORTED')
    target=next((s for s in data.get('symbols_after',[]) if s['symbol']==raw['symbol']),None)
    if not target or target['marginType']!='CROSSED':issues.append('TARGET_CROSS_MARGIN_REQUIRED')
    if data.get('orders'):issues.append('PREEXISTING_ORDINARY_ORDERS')
    if data.get('algo_orders'):issues.append('PREEXISTING_ALGO_ORDERS')
    positions=[p for p in baseline['positions'] if p['margin_asset']==quote]
    if positions:issues.append('QUOTE_BOOK_NOT_FLAT')
    asset=next((a for a in baseline['assets'] if a['asset']==quote),None)
    if not asset or Decimal(asset['observed_wallet'])<=0 or any(Decimal(asset[k])!=Decimal(asset['observed_wallet']) for k in ('observed_cross_wallet','observed_available','observed_margin_balance')) or Decimal(asset['observed_unrealized'])!=0:
        issues.append('QUOTE_WALLET_NOT_FREE_AND_FLAT')
    rules=data.get('rules',{}).get('terms',{})
    if rules.get('status')!='TRADING' or rules.get('contractType')!='PERPETUAL' or rules.get('marginAsset')!=quote or rules.get('quoteAsset')!=quote:issues.append('TARGET_RULES_UNAVAILABLE')
    for kind in ('book','mark'):
        value=data.get(kind)
        if not value or not as_of_ms-30000<=value['time']<=as_of_ms+5000:issues.append('MARKET_TIME_UNAVAILABLE')
    book=data.get('book',{})
    if book and (any(Decimal(book[k])<=0 for k in ('bidPrice','askPrice','bidQty','askQty')) or Decimal(book['bidPrice'])>=Decimal(book['askPrice'])):issues.append('BOOK_NOT_EXECUTABLE')
    mark=data.get('mark',{})
    if mark and (Decimal(mark['markPrice'])<=0 or Decimal(mark['indexPrice'])<=0 or mark['nextFundingTime']<=as_of_ms):issues.append('MARK_UNAVAILABLE')
    inventory_known=baseline['inventory_status'] not in ('CAPTURE_FAILED','NOT_REQUESTED','DATA_BLOCKED')
    return {'schema':raw['schema'],'environment':'BINANCE_USDM_DEMO','status':'UNRESOLVED' if issues else 'OBSERVED_FLAT_QUOTE',
        'symbol':raw['symbol'],'quote_asset':quote,'wallet':asset['observed_wallet'] if asset else None,
        'other_quote_positions':len(baseline['positions'])-len(positions) if inventory_known else None,
        'quote_positions':len(positions) if inventory_known else None,
        'ordinary_orders':len(data.get('orders',[])) if 'orders' in data else None,
        'algo_orders':len(data.get('algo_orders',[])) if 'algo_orders' in data else None,
        'account_ref_sha256':raw['account_ref_sha256'],'observed_at_ms':raw['observed_at_ms'],'completed_at_ms':raw['completed_at_ms'],
        'source_sha256':raw['source_sha256'],'read_count':len(raw['reads']),'failures':raw['failures'],
        'issues':sorted(set(issues)),'settlement_rounding':'UNVERIFIED','source_verified':False,
        'atomic_snapshot_verified':False,'submission_enabled':False,'strategy_pnl':None}


def load(path):
    path=Path(path)
    if path.is_symlink():raise ValueError('ORIGIN_SYMLINK')
    with path.open('rb') as f:data=f.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES:raise ValueError('ORIGIN_SIZE')
    if path.stem!=hashlib.sha256(data).hexdigest():raise ValueError('ORIGIN_DIGEST')
    return validate(load_json_without_duplicate_keys(data))


def save(directory, raw):
    """Append immutable evidence. This does not install an execution origin."""
    validate(raw)
    directory=Path(directory)
    directory.mkdir(parents=True,mode=0o700,exist_ok=True)
    data=(json.dumps(raw,sort_keys=True,indent=2)+'\n').encode()
    if len(data)>MAX_BYTES:raise ValueError('ORIGIN_SIZE')
    path=directory/(hashlib.sha256(data).hexdigest()+'.json')
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    except FileExistsError:
        if path.is_symlink() or path.read_bytes()!=data:raise ValueError('ORIGIN_COLLISION') from None
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',dir=directory,delete=False) as f:
            temporary=Path(f.name);json.dump({'sha256':path.stem},f);f.flush();os.fsync(f.fileno())
        os.replace(temporary,directory/'latest.json')
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return path


def load_latest(directory):
    directory=Path(directory);path=directory/'latest.json'
    if path.is_symlink():raise ValueError('ORIGIN_SYMLINK')
    with path.open('rb') as f:data=f.read(1025)
    if len(data)>1024:raise ValueError('ORIGIN_POINTER_SIZE')
    pointer=load_json_without_duplicate_keys(data);fields(pointer,'sha256');sha(pointer['sha256'])
    return load(directory/(pointer['sha256']+'.json'))
