"""Human-operated Kraken balance/open-order export; never run by an AI with real keys.

Credentials are entered through getpass, held in memory, never exported. The
transport is structurally limited to allowlisted read-only account endpoints. This is
not a live adapter, credential installer, or trading-approval mechanism.
"""
import argparse
import base64
from decimal import Context, localcontext
import fcntl
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import urllib.parse
import urllib.request

from services.data import kraken_account as model
from services.data import kraken_order_query as order_query
from services.data import kraken_trade_query as trade_query
from services.data import kraken_cash_reconciliation as cash_model
from services.data import kraken_fees as fee_model
from services.data import kraken_client_order_lookup as lookup_model
from services.data.kraken_public import require
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools.kraken_public_capture import NoRedirect, atomic_json, digest

ENDPOINTS=('BalanceEx','OpenOrders','ClosedOrders','QueryOrders','QueryTrades','Ledgers','TradeVolume')


def private_root(root):
    root=Path(root).absolute();require(root.resolve()==root,'KRAKEN_ACCOUNT_STATE_SYMLINK')
    root.mkdir(parents=True,mode=0o700,exist_ok=True)
    info=root.stat()
    require(info.st_uid==os.getuid() and stat.S_IMODE(info.st_mode)&0o077==0,'KRAKEN_ACCOUNT_STATE_PERMISSIONS')
    return root


def transport(url,body,headers):
    require(url in tuple('https://api.kraken.com/0/private/'+name for name in ENDPOINTS),'KRAKEN_READ_ONLY_ENDPOINT')
    request=urllib.request.Request(url,data=body,headers=headers,method='POST')
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=15) as response:
        require(response.status==200 and response.geturl()==url,'KRAKEN_ACCOUNT_HTTP')
        payload=response.read(model.MAX_BYTES+1)
    require(len(payload)<=model.MAX_BYTES,'KRAKEN_ACCOUNT_RESPONSE_SIZE')
    return payload


class ReadOnlyClient:
    def __init__(self,key,secret,root,*,transport=None,clock_ms=None):
        require(isinstance(key,str) and bool(re.fullmatch(r'[A-Za-z0-9+/=]{16,256}',key)),'KRAKEN_KEY_FORMAT')
        self._secret=base64.b64decode(secret,validate=True)
        require(16<=len(self._secret)<=256,'KRAKEN_SECRET_FORMAT')
        self._key=key;self.root=private_root(root)
        self.account_ref_sha256=hashlib.sha256(key.encode()).hexdigest()
        self._transport=transport or globals()['transport']
        self._clock=clock_ms or (lambda:time.time_ns()//1_000_000)

    def read(self,endpoint,params):
        if endpoint=='ClosedOrders' or (endpoint=='OpenOrders' and 'cl_ord_id' in params):
            lookup_model.validate_params(endpoint,params)
            params=dict(params)
        elif endpoint=='TradeVolume':
            fee_model.validate_params(params)
            params=json.loads(json.dumps(params,allow_nan=False))
        elif endpoint=='Ledgers':
            require(set(params)=={'asset','aclass','type','start','end','ofs','rebase_multiplier'} and
                    params['asset']=='all' and params['aclass'] in ('currency','tokenized_asset') and
                    params['type']=='all' and params['rebase_multiplier']=='base','KRAKEN_LEDGER_SCOPE')
            require(all(type(params[k]) is int for k in ('start','end','ofs')) and
                    0<params['start']<params['end']<=params['start']+86401 and
                    0<=params['ofs']<5000 and params['ofs']%50==0,'KRAKEN_LEDGER_BOUNDS')
        elif endpoint in ('QueryOrders','QueryTrades'):
            require(set(params)=={'txid','trades','rebase_multiplier'} and params['trades']==('true' if endpoint=='QueryOrders' else 'false') and
                    params['rebase_multiplier']=='base','KRAKEN_READ_ONLY_ENDPOINT')
            ids=order_query.identifiers(params['txid'])
            require(endpoint!='QueryTrades' or len(ids)<=20,'KRAKEN_TRADES_BATCH_LIMIT')
        else:
            require(endpoint in ('BalanceEx','OpenOrders') and params=={'rebase_multiplier':'base'},'KRAKEN_READ_ONLY_ENDPOINT')
        # Serialize allocation AND request dispatch, not only nonce increment.
        lock_path=self.root/(self.account_ref_sha256+'.lock')
        fd=os.open(lock_path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            path=self.root/(self.account_ref_sha256+'.nonce.json')
            require(not path.is_symlink(),'KRAKEN_NONCE_SYMLINK')
            previous=0
            if path.exists():
                record=load_json_without_duplicate_keys(path.read_bytes())
                require(isinstance(record,dict) and set(record)=={'nonce'} and type(record['nonce']) is int,'KRAKEN_NONCE_STATE')
                previous=record['nonce']
                require(0<previous<2**63,'KRAKEN_NONCE_STATE')
            now=self._clock();require(type(now) is int and 0<now<2**63,'KRAKEN_NONCE_CLOCK')
            nonce=max(previous+1,now);require(0<nonce<2**63,'KRAKEN_NONCE_RANGE')
            atomic_json(path,{'nonce':nonce})
            if endpoint=='TradeVolume':
                body=json.dumps({'nonce':nonce,**params},separators=(',',':'),allow_nan=False).encode()
                content_type='application/json'
            else:
                body=urllib.parse.urlencode({'nonce':str(nonce),**params}).encode()
                content_type='application/x-www-form-urlencoded'
            uri='/0/private/'+endpoint
            message=uri.encode()+hashlib.sha256(str(nonce).encode()+body).digest()
            signature=base64.b64encode(hmac.new(self._secret,message,hashlib.sha512).digest()).decode()
            return self._transport('https://api.kraken.com'+uri,body,
                {'API-Key':self._key,'API-Sign':signature,'Content-Type':content_type,
                 'Accept':'application/json','User-Agent':'QUANTOS-account-observation/1'})


def collect(client,*,clock_ms=None):
    clock_ms=clock_ms or (lambda:time.time_ns()//1_000_000)
    reads=[];started=clock_ms()
    try:
        for endpoint in ('BalanceEx','OpenOrders','BalanceEx','OpenOrders'):
            params={'rebase_multiplier':'base'};sent=clock_ms();body=client.read(endpoint,params);received=clock_ms()
            require(isinstance(body,bytes) and len(body)<=model.MAX_BYTES,'KRAKEN_ACCOUNT_RESPONSE_SIZE')
            model.payload(body.decode('utf-8'))
            reads.append({'endpoint':endpoint,'params':params,'requested_at_ms':sent,'received_at_ms':received,
                          'body':body.decode('utf-8'),'account_ref_sha256':client.account_ref_sha256})
        report=model.assess(reads,as_of_ms=clock_ms())
    except (OSError,ValueError,KeyError,TypeError) as exc:
        report={'schema':'KRAKEN_ACCOUNT_OBSERVATION_V1','status':'FAILED','observed_at_ms':clock_ms(),
                'started_at_ms':started,'account_ref_sha256':client.account_ref_sha256,
                'failure':{'code':'ACCOUNT_READ_FAILED','error_type':type(exc).__name__,'endpoint':endpoint},
                'submission_enabled':False,'authoritative':False}
    report['result_hash']=digest(report)
    return report


def save(root,report):
    require(report.get('result_hash')==digest({k:v for k,v in report.items() if k!='result_hash'}),'KRAKEN_ACCOUNT_REPORT_HASH')
    require(report.get('schema')=='KRAKEN_ACCOUNT_OBSERVATION_V1' and report.get('submission_enabled') is False and
            report.get('authoritative') is False,'KRAKEN_ACCOUNT_REPORT_BOUNDARY')
    root=private_root(root)
    fd=os.open(root/'.export.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        for name in ('observations','latest.json','account-reference.json'):require(not (root/name).is_symlink(),'KRAKEN_ACCOUNT_EXPORT_SYMLINK')
        identity={'account_ref_sha256':report['account_ref_sha256']}
        path=root/'account-reference.json'
        if path.exists():require(load_json_without_duplicate_keys(path.read_bytes())==identity,'KRAKEN_ACCOUNT_NAMESPACE_CHANGED')
        else:atomic_json(path,identity)
        directory=root/'observations';directory.mkdir(mode=0o700,exist_ok=True)
        target=directory/(report['result_hash']+'.json')
        if target.exists():require(not target.is_symlink() and load_json_without_duplicate_keys(target.read_bytes())==report,'KRAKEN_ACCOUNT_EXPORT_CONFLICT')
        else:atomic_json(target,report)
        path=root/'latest.json'
        previous=load_json_without_duplicate_keys(path.read_bytes()) if path.exists() else None
        if previous is None or previous['observed_at_ms']<report['observed_at_ms'] or (
            previous['observed_at_ms']==report['observed_at_ms'] and report['status']=='FAILED' and previous['status']!='FAILED'):
            atomic_json(path,report)







def collect_cash_bridge(client, root, opening, closing, *, clock_ms=None, sleep_fn=None):
    root=private_root(root)
    clock_ms=clock_ms or (lambda:time.time_ns()//1_000_000)
    fd=os.open(root/'.cash.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for item in (opening,closing):
            require(item.get('result_hash')==digest({k:v for k,v in item.items() if k!='result_hash'}),'KRAKEN_CASH_INPUT_HASH')
            require(item['account_ref_sha256']==client.account_ref_sha256,'KRAKEN_CASH_ACCOUNT_CHANGED')
        path=root/'cash-reconciliation.json'
        require(not path.is_symlink(),'KRAKEN_CASH_SYMLINK')
        pending={'schema':'KRAKEN_BALANCE_BRIDGE_V1','status':'UNRESOLVED',
            'account_ref_sha256':client.account_ref_sha256,'as_of_ms':clock_ms(),
            'opening_account_hash':opening['result_hash'],'closing_account_hash':closing['result_hash'],
            'failure':{'error_type':'REFRESH_IN_PROGRESS'},'submission_enabled':False,'authoritative':False,
            'next_cycle_allowed':False,'trade_linkage_verified':False}
        atomic_json(path,{**pending,'result_hash':digest(pending)})
        try:
            return _collect_cash_bridge(client,root,opening,closing,clock_ms=clock_ms,sleep_fn=sleep_fn)
        except (OSError,ValueError,KeyError,TypeError) as exc:
            pending.update(as_of_ms=clock_ms(),failure={'error_type':type(exc).__name__})
            pending['result_hash']=digest(pending)
            atomic_json(path,pending)
            return pending


def _collect_cash_bridge(client, root, opening, closing, *, clock_ms=None, sleep_fn=None):
    clock_ms=clock_ms or (lambda:time.time_ns()//1_000_000);sleep_fn=sleep_fn or time.sleep
    require(opening['account_ref_sha256']==closing['account_ref_sha256']==client.account_ref_sha256,'KRAKEN_CASH_ACCOUNT_CHANGED')
    for item in (opening,closing):
        require(item.get('schema')=='KRAKEN_ACCOUNT_OBSERVATION_V1' and item.get('status')=='OBSERVED_STABLE' and
                item.get('quantity_basis')=='base' and not item['issues'] and item.get('submission_enabled') is False and
                item.get('authoritative') is False,'KRAKEN_CASH_ACCOUNT')
    require(opening['reads'][0]['requested_at_ms']<=opening['observed_at_ms']<closing['reads'][0]['requested_at_ms'] and
            closing['reads'][0]['requested_at_ms']<=closing['observed_at_ms']<=clock_ms() and
            closing['observed_at_ms']-opening['reads'][0]['requested_at_ms']<=86400000,'KRAKEN_CASH_WINDOW')
    start=opening['reads'][0]['requested_at_ms']//1000;end=(closing['observed_at_ms']+999)//1000
    require(0<start<end<=start+86401,'KRAKEN_CASH_WINDOW')
    delay=end*1000-clock_ms()
    if delay>0:
        require(delay<=1000,'KRAKEN_CASH_FUTURE_SNAPSHOT');sleep_fn(delay/1000)
    reads=[]
    for cls in ('currency','tokenized_asset'):
        offset=0
        while True:
            params={'asset':'all','aclass':cls,'type':'all','start':start,'end':end,'ofs':offset,'rebase_multiplier':'base'}
            sent=clock_ms();body=client.read('Ledgers',params);received=clock_ms()
            require(isinstance(body,bytes) and len(body)<=model.MAX_BYTES,'KRAKEN_LEDGER_SIZE')
            value=model.payload(body.decode('utf-8'));count=value.get('count')
            require(type(count) is int and 0<=count<=5000,'KRAKEN_LEDGER_COUNT')
            reads.append({'endpoint':'Ledgers','params':params,'requested_at_ms':sent,'received_at_ms':received,
                          'account_ref_sha256':client.account_ref_sha256,'body':body.decode('utf-8')})
            offset+=50
            if offset>=count:break
    report=cash_model.reconcile(opening,closing,reads,as_of_ms=clock_ms())
    report.update(opening_account_hash=opening['result_hash'],closing_account_hash=closing['result_hash'])
    evidence={'opening':opening,'closing':closing,'reads':reads,'report':report};key=digest(evidence)
    root=private_root(root);directory=root/'cash-windows'
    require(not directory.is_symlink() and not (root/'cash-reconciliation.json').is_symlink(),'KRAKEN_CASH_SYMLINK')
    directory.mkdir(mode=0o700,exist_ok=True);target=directory/(key+'.json')
    require(not target.is_symlink(),'KRAKEN_CASH_SYMLINK')
    if target.exists():require(load_json_without_duplicate_keys(target.read_bytes())==evidence,'KRAKEN_CASH_CONFLICT')
    else:atomic_json(target,evidence)
    report['evidence_hash']=key;report['result_hash']=digest(report)
    atomic_json(root/'cash-reconciliation.json',report)
    return report


def query_trades(client, root, orders, *, clock_ms):
    ids=sorted({t for order in orders['orders'] for t in order['trade_ids']})
    require(len(ids)<=1000,'KRAKEN_TRADES_LIMIT')
    reads=[]
    for offset in range(0,len(ids),20):
        params={'txid':','.join(ids[offset:offset+20]),'trades':'false','rebase_multiplier':'base'}
        sent=clock_ms();body=client.read('QueryTrades',params);received=clock_ms()
        require(isinstance(body,bytes) and len(body)<=model.MAX_BYTES,'KRAKEN_TRADE_QUERY_SIZE')
        model.payload(body.decode('utf-8'))
        reads.append({'endpoint':'QueryTrades','params':params,'requested_at_ms':sent,'received_at_ms':received,
                      'account_ref_sha256':client.account_ref_sha256,'body':body.decode('utf-8')})
    report=trade_query.assess(reads,orders=orders,as_of_ms=clock_ms())
    for name in ('trade-queries','trade-observations'):
        require(not (root/name).is_symlink(),'KRAKEN_TRADE_ARCHIVE_SYMLINK')
        (root/name).mkdir(mode=0o700,exist_ok=True)
    evidence={'reads':reads,'report':report};key=digest(evidence)
    target=root/'trade-queries'/(key+'.json')
    require(not target.is_symlink(),'KRAKEN_TRADE_ARCHIVE_SYMLINK')
    if target.exists():require(load_json_without_duplicate_keys(target.read_bytes())==evidence,'KRAKEN_TRADE_ARCHIVE_CONFLICT')
    else:atomic_json(target,evidence)
    for trade in report['trades']:
        record={'account_ref_sha256':client.account_ref_sha256,'quantity_basis':'base','trade':trade}
        target=root/'trade-observations'/(trade['trade_id']+'.json')
        require(not target.is_symlink(),'KRAKEN_TRADE_ARCHIVE_SYMLINK')
        if target.exists():require(load_json_without_duplicate_keys(target.read_bytes())==record,'KRAKEN_TRADE_REPLAY_CONFLICT')
        else:atomic_json(target,record)
    return key,report


def cash_order_hints(client, root, cash, *, clock_ms):
    """Discover IDs only; QueryOrders/QueryTrades still verify all financial fields."""
    from tools.kraken_settlement_review import replay_cash
    bridge=replay_cash(root,cash)
    require(bridge['account_ref_sha256']==client.account_ref_sha256 and
            bridge['status']=='BALANCE_MOVEMENTS_MATCHED' and
            0<=clock_ms()-bridge['as_of_ms']<=120000,'KRAKEN_CASH_DISCOVERY_SOURCE')
    start,end=bridge['movement_start_exclusive_ms'],bridge['movement_end_exclusive_ms']
    with localcontext(Context(prec=160)):
        ids=sorted({row['refid'] for row in bridge['ledger_entries'] if row['type']=='trade' and
                    start<model.number(row['time_seconds'])*1000<end})
    require(len(ids)<=1000,'KRAKEN_CASH_DISCOVERY_LIMIT')
    archive=root/'cash-order-discovery'
    require(not archive.is_symlink(),'KRAKEN_CASH_DISCOVERY_SYMLINK')
    archive.mkdir(mode=0o700,exist_ok=True)
    previous=bridge['as_of_ms']
    for offset in range(0,len(ids),20):
        batch=ids[offset:offset+20];order_query.identifiers(','.join(batch))
        params={'txid':','.join(batch),'trades':'false','rebase_multiplier':'base'}
        sent=clock_ms();body=client.read('QueryTrades',params);received=clock_ms()
        require(previous<=sent<=received and received-sent<=30000 and
                received-bridge['as_of_ms']<=120000,'KRAKEN_CASH_DISCOVERY_TIME')
        previous=received
        require(isinstance(body,bytes) and len(body)<=model.MAX_BYTES,'KRAKEN_CASH_DISCOVERY_SIZE')
        rows=model.payload(body.decode('utf-8'))
        require(set(rows)==set(batch),'KRAKEN_CASH_DISCOVERY_MISSING')
        discovered=set()
        for row in rows.values():
            oid=row['ordertxid']
            require(isinstance(oid,str) and re.fullmatch(r'[A-Za-z0-9-]{1,80}',oid),
                    'KRAKEN_CASH_DISCOVERY_ORDER_ID')
            discovered.add(oid)
        evidence={'account_ref_sha256':client.account_ref_sha256,'cash_source_hash':cash['result_hash'],
            'endpoint':'QueryTrades','params':params,'requested_at_ms':sent,'received_at_ms':received,
            'body':body.decode('utf-8')}
        target=archive/(digest(evidence)+'.json')
        require(not target.is_symlink(),'KRAKEN_CASH_DISCOVERY_SYMLINK')
        if target.exists():require(load_json_without_duplicate_keys(target.read_bytes())==evidence,'KRAKEN_CASH_DISCOVERY_CONFLICT')
        else:atomic_json(target,evidence)
        yield discovered


def track_orders(client, root, open_orders, *, clock_ms=None, cash=None, client_order_requests=None):
    """Retain known IDs before querying; unknown/missing orders never become empty."""
    root=private_root(root);clock_ms=clock_ms or (lambda:time.time_ns()//1_000_000)
    fd=os.open(root/'.order-query.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        from tools.kraken_settlement_checkpoint import load as checkpoint_state
        checkpoint=checkpoint_state(root,client.account_ref_sha256)
        retired=set(checkpoint['retired_ids']) if checkpoint else set()
        require(not retired&{row['order_id'] for row in open_orders},'KRAKEN_CHECKPOINT_RETIRED_ORDER_REAPPEARED')
        for name in ('order-tracking.json','order-query-latest.json','order-recovery.json','order-queries'):
            require(not (root/name).is_symlink(),'KRAKEN_ORDER_TRACKING_SYMLINK')
        path=root/'order-tracking.json';ids=set()
        if path.exists():
            prior=load_json_without_duplicate_keys(path.read_bytes())
            require(prior['account_ref_sha256']==client.account_ref_sha256,'KRAKEN_ORDER_TRACKING_ACCOUNT')
            ids.update(prior['order_ids'])
        recovery_path=root/'order-recovery.json'
        if recovery_path.exists():
            prior=load_json_without_duplicate_keys(recovery_path.read_bytes())
            require(prior.get('result_hash')==digest({k:v for k,v in prior.items() if k!='result_hash'}),
                    'KRAKEN_ORDER_RECOVERY_HASH')
            require(prior['account_ref_sha256']==client.account_ref_sha256,'KRAKEN_ORDER_TRACKING_ACCOUNT')
            ids.update(prior['order_ids'])
        ids.update(row['order_id'] for row in open_orders)
        ids.difference_update(retired)
        ordered=sorted(ids)
        if ordered:order_query.identifiers(','.join(ordered))
        result={'schema':'KRAKEN_ORDER_RECOVERY_V1','account_ref_sha256':client.account_ref_sha256,
                'status':'NO_TRACKED_ORDERS' if not ids else 'UNRESOLVED','order_ids':ordered,
                'observed_at_ms':clock_ms(),'query_hash':None,'failure':None,'cash_reconciled':False,
                'trade_status':'NOT_OBSERVED','trade_query_hash':None,
                'full_history_observed':False,'next_cycle_allowed':False,'submission_enabled':False,'authoritative':False}
        # Invalidate previous success before any archive mutation or remote read.
        # A killed process must leave a visible unresolved refresh, not fresh old success.
        pending={**result,'status':'UNRESOLVED','failure':{'error_type':'REFRESH_IN_PROGRESS'}}
        pending['result_hash']=digest(pending)
        atomic_json(root/'order-recovery.json',pending)
        atomic_json(path,{'account_ref_sha256':client.account_ref_sha256,'order_ids':ordered})
        try:
            from tools.kraken_client_order_lookup import hints
            for discovered in hints(client,root,requests=client_order_requests,retired=retired,clock_ms=clock_ms):
                ids.update(discovered);ordered=sorted(ids)
                result.update(order_ids=ordered,status='UNRESOLVED')
                pending={**result,'failure':{'error_type':'REFRESH_IN_PROGRESS'}}
                pending['result_hash']=digest(pending)
                atomic_json(root/'order-recovery.json',pending)
                atomic_json(path,{'account_ref_sha256':client.account_ref_sha256,'order_ids':ordered})
                order_query.identifiers(','.join(ordered))
        except (OSError,ValueError,KeyError,TypeError) as exc:
            result.update(status='UNRESOLVED',failure={'error_type':type(exc).__name__})
            result['result_hash']=digest(result);atomic_json(root/'order-recovery.json',result)
            return result
        if cash is not None:
            try:
                for discovered in cash_order_hints(client,root,cash,clock_ms=clock_ms):
                    require(not retired&discovered,'KRAKEN_CHECKPOINT_RETIRED_ORDER_REAPPEARED')
                    ids.update(discovered);ordered=sorted(ids)
                    result.update(order_ids=ordered,status='UNRESOLVED')
                    pending={**result,'failure':{'error_type':'REFRESH_IN_PROGRESS'}}
                    pending['result_hash']=digest(pending)
                    atomic_json(root/'order-recovery.json',pending)
                    atomic_json(path,{'account_ref_sha256':client.account_ref_sha256,'order_ids':ordered})
                    order_query.identifiers(','.join(ordered))
            except (OSError,ValueError,KeyError,TypeError) as exc:
                result.update(status='UNRESOLVED',failure={'error_type':type(exc).__name__})
                result['result_hash']=digest(result)
                atomic_json(root/'order-recovery.json',result)
                return result
        if ids:
            try:
                params={'txid':','.join(ordered),'trades':'true','rebase_multiplier':'base'}
                sent=clock_ms();body=client.read('QueryOrders',params);received=clock_ms()
                require(isinstance(body,bytes) and len(body)<=model.MAX_BYTES,'KRAKEN_ORDER_QUERY_SIZE')
                read={'endpoint':'QueryOrders','params':params,'requested_at_ms':sent,'received_at_ms':received,
                      'account_ref_sha256':client.account_ref_sha256,'body':body.decode('utf-8')}
                report=order_query.assess(read,as_of_ms=clock_ms())
                archive=root/'order-queries';archive.mkdir(mode=0o700,exist_ok=True)
                evidence={'read':read,'report':report};query_hash=digest(evidence)
                target=archive/(query_hash+'.json')
                require(not target.is_symlink(),'KRAKEN_ORDER_TRACKING_SYMLINK')
                if target.exists():require(load_json_without_duplicate_keys(target.read_bytes())==evidence,'KRAKEN_ORDER_QUERY_CONFLICT')
                else:atomic_json(target,evidence)
                result['query_hash']=query_hash
                latest=root/'order-query-latest.json'
                if latest.exists():
                    previous=load_json_without_duplicate_keys(latest.read_bytes())
                    previous={**previous,'orders':[o for o in previous['orders'] if o['order_id'] not in retired]}
                    order_query.check_progress(previous,report)
                atomic_json(latest,report)
                trade_hash,trades=query_trades(client,root,report,clock_ms=clock_ms)
                result.update(status='ORDERS_OBSERVED',observed_at_ms=trades['as_of_ms'],
                    trade_status=trades['status'],trade_query_hash=trade_hash)
            except (OSError,ValueError,KeyError,TypeError) as exc:
                result['failure']={'error_type':type(exc).__name__}
        result['result_hash']=digest(result)
        atomic_json(root/'order-recovery.json',result)
        return result


def validate_cash_opening(opening, account_ref):
    require(opening.get('result_hash')==digest({k:v for k,v in opening.items() if k!='result_hash'}),
            'KRAKEN_CASH_INPUT_HASH')
    require(opening.get('schema')=='KRAKEN_ACCOUNT_OBSERVATION_V1' and
            opening.get('account_ref_sha256')==account_ref and opening.get('status')=='OBSERVED_STABLE' and
            not opening.get('issues') and opening.get('quantity_basis')=='base' and
            opening.get('submission_enabled') is False and opening.get('authoritative') is False,'KRAKEN_CASH_ACCOUNT')


def run_session(client, root, *, cycles=1, poll_seconds=60, collect_fn=None, sleep_fn=None, on_report=None,
                track_known_orders=False, ledger_opening=None, clock_ms=None, auto_cash=False,
                settlement_market_root=None, fee_market_root=None, client_order_requests=None):
    """Bounded human-started refresh; any transport/API failure ends the session."""
    require(type(cycles) is int and 1<=cycles<=1440,'KRAKEN_ACCOUNT_CYCLE_LIMIT')
    require(type(poll_seconds) is int and 60<=poll_seconds<=3600,'KRAKEN_ACCOUNT_POLL_INTERVAL')
    collect_fn=collect_fn or collect;sleep_fn=sleep_fn or time.sleep
    require(type(auto_cash) is bool and not (auto_cash and ledger_opening is not None),'KRAKEN_CASH_SESSION_MODE')
    require(settlement_market_root is None or auto_cash,'KRAKEN_CHECKPOINT_REQUIRES_AUTO_CASH')
    if settlement_market_root is not None:
        settlement_market_root=Path(settlement_market_root).absolute()
        require(settlement_market_root.resolve()==settlement_market_root and settlement_market_root.is_dir(),
                'KRAKEN_CHECKPOINT_MARKET_ROOT')
    if client_order_requests is not None:
        require(isinstance(client_order_requests,list) and 1<=len(client_order_requests)<=32,'KRAKEN_CLIENT_LOOKUP_REQUEST_LIMIT')
        for request in client_order_requests:lookup_model.requests(request)
    track_known_orders=track_known_orders or auto_cash or client_order_requests is not None
    if ledger_opening is not None:
        validate_cash_opening(ledger_opening,client.account_ref_sha256)
    root=private_root(root)
    track_known_orders=track_known_orders or any((root/name).exists() or (root/name).is_symlink() for name in
        ('order-tracking.json','order-recovery.json',
         'client-order-requests.json','client-order-recovery-required.json','client-order-resolutions'))
    fd=os.open(root/'.session.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        reference=root/'account-reference.json'
        require(not reference.is_symlink(),'KRAKEN_ACCOUNT_EXPORT_SYMLINK')
        if reference.exists():
            require(load_json_without_duplicate_keys(reference.read_bytes())=={
                'account_ref_sha256':client.account_ref_sha256},'KRAKEN_ACCOUNT_NAMESPACE_CHANGED')
        baseline=root/'cash-baseline.json'
        require(not baseline.is_symlink(),'KRAKEN_CASH_BASELINE_SYMLINK')
        if baseline.exists():
            require(auto_cash,'KRAKEN_CASH_SESSION_MODE')
            ledger_opening=load_json_without_duplicate_keys(baseline.read_bytes())
            validate_cash_opening(ledger_opening,client.account_ref_sha256)
        elif auto_cash:
            require(not (root/'cash-reconciliation.json').exists() and not (root/'cash-reconciliation.json').is_symlink(),
                    'KRAKEN_CASH_BASELINE_MISSING')
        from tools import kraken_settlement_checkpoint as checkpoints
        checkpoints.recover(root)
        if ledger_opening is not None and settlement_market_root is not None:
            checkpoints.initialise(root,ledger_opening,market_root=settlement_market_root)
        checkpoint=checkpoints.load(root,client.account_ref_sha256)
        require(checkpoint is None or auto_cash,'KRAKEN_CHECKPOINT_REQUIRES_AUTO_CASH')
        from tools import kraken_fee_cycle as fee_cycle
        fee_config=fee_cycle.configure(root,client.account_ref_sha256,fee_market_root)
        fee_clock=clock_ms or (lambda:time.time_ns()//1_000_000)
        fee_status='NOT_REQUESTED'
        completed=0;failures=0;stop_reason='CYCLE_LIMIT';status=None;bridge_status='NOT_REQUESTED'
        for index in range(cycles):
            if index:sleep_fn(poll_seconds)
            if fee_config is not None:
                fee_cycle.pending(root,client.account_ref_sha256,as_of_ms=fee_clock())
                fee_status='UNRESOLVED'
            checkpoint=checkpoints.load(root,client.account_ref_sha256)
            if checkpoint is not None:ledger_opening=checkpoint['opening']
            report=collect_fn(client)
            require(report.get('account_ref_sha256')==client.account_ref_sha256,'KRAKEN_ACCOUNT_NAMESPACE_CHANGED')
            require(report.get('status') in ('OBSERVED_STABLE','UNRESOLVED','STALE','FAILED'),'KRAKEN_ACCOUNT_SESSION_STATUS')
            save(root,report);completed+=1;status=report['status'];failures+=status!='OBSERVED_STABLE'
            recovery=None
            bootstrap_blocked=auto_cash and ledger_opening is None and (status!='OBSERVED_STABLE' or report['open_orders'])
            if track_known_orders and status!='FAILED' and not bootstrap_blocked:
                recovery=track_orders(client,root,report['open_orders'],clock_ms=clock_ms,
                                      client_order_requests=client_order_requests if index==0 else None)
                if recovery['status']=='UNRESOLVED':
                    failures+=status=='OBSERVED_STABLE';stop_reason='ORDER_QUERY_UNRESOLVED'
            if auto_cash and ledger_opening is None:
                bridge_status='NOT_OBSERVED'
                if status=='OBSERVED_STABLE' and not report['open_orders'] and recovery is not None and recovery['status']=='NO_TRACKED_ORDERS':
                    validate_cash_opening(report,client.account_ref_sha256)
                    atomic_json(baseline,report);ledger_opening=report
                    pending={'schema':'KRAKEN_BALANCE_BRIDGE_V1','status':'BASELINE_ONLY',
                        'account_ref_sha256':client.account_ref_sha256,'as_of_ms':report['observed_at_ms'],
                        'opening_account_hash':report['result_hash'],'closing_account_hash':report['result_hash'],
                        'submission_enabled':False,'authoritative':False,'next_cycle_allowed':False,
                        'trade_linkage_verified':False}
                    atomic_json(root/'cash-reconciliation.json',{**pending,'result_hash':digest(pending)})
                    bridge_status='BASELINE_ONLY'
                    if settlement_market_root is not None:
                        checkpoints.initialise(root,report,market_root=settlement_market_root)
                else:
                    failures+=status=='OBSERVED_STABLE' and stop_reason!='ORDER_QUERY_UNRESOLVED'
                    stop_reason='CASH_BASELINE_REQUIRES_SETTLED_ACCOUNT'
            elif ledger_opening is not None:
                bridge_status='NOT_OBSERVED'
                if status=='OBSERVED_STABLE' and (recovery is None or recovery['status']!='UNRESOLVED'):
                    bridge=collect_cash_bridge(client,root,ledger_opening,report,clock_ms=clock_ms)
                    bridge_status=bridge['status']
                    if bridge_status!='BALANCE_MOVEMENTS_MATCHED':
                        failures+=1;stop_reason='CASH_QUERY_UNRESOLVED'
                    elif auto_cash:
                        recovery=track_orders(client,root,report['open_orders'],clock_ms=clock_ms,cash=bridge)
                        if recovery['status']=='UNRESOLVED':
                            failures+=1;stop_reason='ORDER_QUERY_UNRESOLVED'
                        elif checkpoint is not None and not report['open_orders'] and (
                                recovery['order_ids'] or report['observed_at_ms']-ledger_opening['observed_at_ms']>=3600000):
                            point=checkpoints.refresh(root,bridge,recovery,market_root=checkpoint['market_root'],
                                as_of_ms=clock_ms() if clock_ms else time.time_ns()//1_000_000)
                            if point is None:
                                failures+=1;stop_reason='SETTLEMENT_CHECKPOINT_UNRESOLVED'
                                bridge_status='UNRESOLVED'
                elif status!='FAILED' and stop_reason!='ORDER_QUERY_UNRESOLVED':
                    stop_reason='CASH_ACCOUNT_UNSTABLE'
            if fee_config is not None and stop_reason=='CYCLE_LIMIT':
                if status=='OBSERVED_STABLE' and (recovery is None or recovery['status']!='UNRESOLVED'):
                    try:
                        fee_status=fee_cycle.refresh(client,root,fee_config,report,clock_ms=fee_clock)['status']
                    except (OSError,ValueError,KeyError,TypeError):
                        failures+=1;stop_reason='FEE_QUERY_UNRESOLVED'
                else:
                    stop_reason='ACCOUNT_READ_FAILED' if status=='FAILED' else 'FEE_ACCOUNT_UNRESOLVED'
            if on_report is not None:
                on_report({'cycle':completed,'status':status,'report':str(root/'latest.json'),
                           'result_hash':report['result_hash'],'order_recovery_status':recovery['status'] if recovery else 'NOT_REQUESTED',
                           'balance_bridge_status':bridge_status,
                           'fee_observation_status':fee_status,
                           'submission_enabled':False})
            if stop_reason in ('CASH_QUERY_UNRESOLVED','CASH_ACCOUNT_UNSTABLE','CASH_BASELINE_REQUIRES_SETTLED_ACCOUNT',
                               'SETTLEMENT_CHECKPOINT_UNRESOLVED','FEE_QUERY_UNRESOLVED','FEE_ACCOUNT_UNRESOLVED'):break
            if recovery is not None and recovery['status']=='UNRESOLVED':break
            if status=='FAILED':
                stop_reason='ACCOUNT_READ_FAILED';break
        return {'cycles_completed':completed,'non_stable_cycles':failures,'last_status':status,
                'stop_reason':stop_reason,'balance_bridge_status':bridge_status,
                'fee_observation_status':fee_status,'submission_enabled':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root',required=True,type=Path)
    parser.add_argument('--cycles',type=int,default=1)
    parser.add_argument('--poll-seconds',type=int,default=60)
    parser.add_argument('--track-orders',action='store_true',help='Query and retain observed order IDs across restarts')
    parser.add_argument('--ledger-opening',type=Path,help='Fixed earlier account checkpoint; reconcile each cycle, stop on mismatch (24h maximum window)')
    parser.add_argument('--auto-cash',action='store_true',help='Persist first stable no-order checkpoint and reconcile subsequent cycles (24h maximum window)')
    parser.add_argument('--settlement-market-root',type=Path,
        help='With --auto-cash, use archived public market evidence to advance settled checkpoints and retire known orders')
    parser.add_argument('--fee-market-root',type=Path,help='Persist public observer directory and refresh account fee evidence each cycle')
    parser.add_argument('--client-order-requests',type=Path,help='Register bounded client-ID recovery requests; never resubmit missing orders')
    parser.add_argument('--portfolio-config',type=Path,help='Refresh public markets, account, fees and portfolio review in one read-only session')
    parser.add_argument('--portfolio-policy',type=Path,help='Optional explicit portfolio policy; requires --portfolio-config')
    parser.add_argument('--valuation-quote',help='Optional portfolio valuation quote; requires --portfolio-config')
    args=parser.parse_args(argv)
    require(1<=args.cycles<=1440 and 60<=args.poll_seconds<=3600,'KRAKEN_ACCOUNT_SESSION_LIMIT')
    require(not (args.auto_cash and args.ledger_opening is not None),'KRAKEN_CASH_SESSION_MODE')
    require(args.settlement_market_root is None or args.auto_cash,'KRAKEN_CHECKPOINT_REQUIRES_AUTO_CASH')
    require(args.portfolio_config is not None or (args.portfolio_policy is None and args.valuation_quote is None),
            'KRAKEN_PORTFOLIO_CONFIG_REQUIRED')
    require(args.portfolio_config is None or (args.ledger_opening is None and args.settlement_market_root is None and args.fee_market_root is None),
            'KRAKEN_PORTFOLIO_SESSION_SCOPE')
    require(sys.stdin.isatty(),'INTERACTIVE_OPERATOR_REQUIRED')
    root=private_root(args.state_root)
    portfolio_config=None
    if args.portfolio_config is not None:
        from tools import kraken_portfolio_session as portfolio_session
        from tools.kraken_inventory_report import load
        portfolio_config=load(args.portfolio_config)
        portfolio_session.prepare(root,portfolio_config,policy_path=args.portfolio_policy,valuation_quote=args.valuation_quote)
    elif any((root/name).exists() or (root/name).is_symlink() for name in
             ('portfolio-session-config.json','portfolio-session-status.json','portfolio')):
        require(args.ledger_opening is None and args.settlement_market_root is None and args.fee_market_root is None,
                'KRAKEN_PORTFOLIO_SESSION_SCOPE')
        from tools import kraken_portfolio_session as portfolio_session
        binding=portfolio_session.restore(root)
        portfolio_config=binding['market']
        args.portfolio_policy=binding['policy_path']
        args.valuation_quote=binding['valuation_quote']
    client_requests=None
    if args.client_order_requests is not None:
        from tools.kraken_inventory_report import load
        client_requests=load(args.client_order_requests)
        require(isinstance(client_requests,list) and 1<=len(client_requests)<=32,'KRAKEN_CLIENT_LOOKUP_REQUEST_LIMIT')
        for request in client_requests:lookup_model.requests(request)
    opening=load_json_without_duplicate_keys(args.ledger_opening.read_bytes()) if args.ledger_opening is not None else None
    print('仅查询账户、订单与成交；不下单、不撤单、不转账。凭证仅保留在本进程内存。')
    key=getpass.getpass('Kraken API public key（不回显）: ')
    secret=getpass.getpass('Kraken API secret（不回显）: ')
    client=ReadOnlyClient(key,secret,root);key=secret=None
    if portfolio_config is not None:
        result=portfolio_session.run_session(client,root,portfolio_config,cycles=args.cycles,
            policy_path=args.portfolio_policy,valuation_quote=args.valuation_quote,
            on_report=lambda event:print(json.dumps(event),flush=True),client_order_requests=client_requests)
        print(json.dumps(result),flush=True)
        return 0 if result['non_stable_cycles']==0 and result['portfolio_status'] in ('WAITING_POLICY','REVIEW_ONLY') else 3
    result=run_session(client,root,cycles=args.cycles,poll_seconds=args.poll_seconds,
        on_report=lambda event:print(json.dumps(event),flush=True),track_known_orders=args.track_orders,
        ledger_opening=opening,auto_cash=args.auto_cash,settlement_market_root=args.settlement_market_root,
        fee_market_root=args.fee_market_root,client_order_requests=client_requests)
    print(json.dumps(result),flush=True)
    return 0 if result['non_stable_cycles']==0 else 3


if __name__=='__main__':
    try:raise SystemExit(main())
    except KeyboardInterrupt:
        print('ACCOUNT_SESSION_STOPPED',file=sys.stderr)
        raise SystemExit(130)
    except (OSError,ValueError,TypeError):
        print('ACCOUNT_EXPORT_FAILED: check input format, private state permissions and read-only API access.',file=sys.stderr)
        raise SystemExit(3)
