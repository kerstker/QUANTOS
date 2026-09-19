"""Human-operated Kraken balance/open-order export; never run by an AI with real keys.

Credentials are entered through getpass, held in memory, never exported. The
transport is structurally limited to allowlisted read-only account endpoints. This is
not a live adapter, credential installer, or trading-approval mechanism.
"""
import argparse
import base64
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
from services.data.kraken_public import require
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools.kraken_public_capture import NoRedirect, atomic_json, digest

ENDPOINTS=('BalanceEx','OpenOrders','QueryOrders','QueryTrades','Ledgers')


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
        if endpoint=='Ledgers':
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
            body=urllib.parse.urlencode({'nonce':str(nonce),**params}).encode()
            uri='/0/private/'+endpoint
            message=uri.encode()+hashlib.sha256(str(nonce).encode()+body).digest()
            signature=base64.b64encode(hmac.new(self._secret,message,hashlib.sha512).digest()).decode()
            return self._transport('https://api.kraken.com'+uri,body,
                {'API-Key':self._key,'API-Sign':signature,'Content-Type':'application/x-www-form-urlencoded',
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


def track_orders(client, root, open_orders, *, clock_ms=None):
    """Retain known IDs before querying; unknown/missing orders never become empty."""
    root=private_root(root);clock_ms=clock_ms or (lambda:time.time_ns()//1_000_000)
    fd=os.open(root/'.order-query.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
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
                if latest.exists():order_query.check_progress(load_json_without_duplicate_keys(latest.read_bytes()),report)
                atomic_json(latest,report)
                trade_hash,trades=query_trades(client,root,report,clock_ms=clock_ms)
                result.update(status='ORDERS_OBSERVED',observed_at_ms=trades['as_of_ms'],
                    trade_status=trades['status'],trade_query_hash=trade_hash)
            except (OSError,ValueError,KeyError,TypeError) as exc:
                result['failure']={'error_type':type(exc).__name__}
        result['result_hash']=digest(result)
        atomic_json(root/'order-recovery.json',result)
        return result


def run_session(client, root, *, cycles=1, poll_seconds=60, collect_fn=None, sleep_fn=None, on_report=None,
                track_known_orders=False, ledger_opening=None, clock_ms=None):
    """Bounded human-started refresh; any transport/API failure ends the session."""
    require(type(cycles) is int and 1<=cycles<=1440,'KRAKEN_ACCOUNT_CYCLE_LIMIT')
    require(type(poll_seconds) is int and 60<=poll_seconds<=3600,'KRAKEN_ACCOUNT_POLL_INTERVAL')
    collect_fn=collect_fn or collect;sleep_fn=sleep_fn or time.sleep
    if ledger_opening is not None:
        require(ledger_opening.get('result_hash')==digest({k:v for k,v in ledger_opening.items() if k!='result_hash'}),
                'KRAKEN_CASH_INPUT_HASH')
        require(ledger_opening.get('account_ref_sha256')==client.account_ref_sha256 and
                ledger_opening.get('status')=='OBSERVED_STABLE' and not ledger_opening.get('issues') and
                ledger_opening.get('quantity_basis')=='base','KRAKEN_CASH_ACCOUNT')
    root=private_root(root)
    fd=os.open(root/'.session.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        reference=root/'account-reference.json'
        require(not reference.is_symlink(),'KRAKEN_ACCOUNT_EXPORT_SYMLINK')
        if reference.exists():
            require(load_json_without_duplicate_keys(reference.read_bytes())=={
                'account_ref_sha256':client.account_ref_sha256},'KRAKEN_ACCOUNT_NAMESPACE_CHANGED')
        completed=0;failures=0;stop_reason='CYCLE_LIMIT';status=None;bridge_status='NOT_REQUESTED'
        for index in range(cycles):
            if index:sleep_fn(poll_seconds)
            report=collect_fn(client)
            require(report.get('account_ref_sha256')==client.account_ref_sha256,'KRAKEN_ACCOUNT_NAMESPACE_CHANGED')
            require(report.get('status') in ('OBSERVED_STABLE','UNRESOLVED','STALE','FAILED'),'KRAKEN_ACCOUNT_SESSION_STATUS')
            save(root,report);completed+=1;status=report['status'];failures+=status!='OBSERVED_STABLE'
            recovery=None
            if track_known_orders and status!='FAILED':
                recovery=track_orders(client,root,report['open_orders'])
                if recovery['status']=='UNRESOLVED':
                    failures+=status=='OBSERVED_STABLE';stop_reason='ORDER_QUERY_UNRESOLVED'
            if ledger_opening is not None:
                bridge_status='NOT_OBSERVED'
                if status=='OBSERVED_STABLE' and (recovery is None or recovery['status']!='UNRESOLVED'):
                    bridge=collect_cash_bridge(client,root,ledger_opening,report,clock_ms=clock_ms)
                    bridge_status=bridge['status']
                    if bridge_status!='BALANCE_MOVEMENTS_MATCHED':
                        failures+=1;stop_reason='CASH_QUERY_UNRESOLVED'
                elif status!='FAILED' and stop_reason!='ORDER_QUERY_UNRESOLVED':
                    stop_reason='CASH_ACCOUNT_UNSTABLE'
            if on_report is not None:
                on_report({'cycle':completed,'status':status,'report':str(root/'latest.json'),
                           'result_hash':report['result_hash'],'order_recovery_status':recovery['status'] if recovery else 'NOT_REQUESTED',
                           'balance_bridge_status':bridge_status,
                           'submission_enabled':False})
            if stop_reason in ('CASH_QUERY_UNRESOLVED','CASH_ACCOUNT_UNSTABLE'):break
            if recovery is not None and recovery['status']=='UNRESOLVED':break
            if status=='FAILED':
                stop_reason='ACCOUNT_READ_FAILED';break
        return {'cycles_completed':completed,'non_stable_cycles':failures,'last_status':status,
                'stop_reason':stop_reason,'balance_bridge_status':bridge_status,'submission_enabled':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root',required=True,type=Path)
    parser.add_argument('--cycles',type=int,default=1)
    parser.add_argument('--poll-seconds',type=int,default=60)
    parser.add_argument('--track-orders',action='store_true',help='Query and retain observed order IDs across restarts')
    parser.add_argument('--ledger-opening',type=Path,help='Fixed earlier account checkpoint; reconcile each cycle, stop on mismatch (24h maximum window)')
    args=parser.parse_args(argv)
    require(1<=args.cycles<=1440 and 60<=args.poll_seconds<=3600,'KRAKEN_ACCOUNT_SESSION_LIMIT')
    require(sys.stdin.isatty(),'INTERACTIVE_OPERATOR_REQUIRED')
    root=private_root(args.state_root)
    opening=load_json_without_duplicate_keys(args.ledger_opening.read_bytes()) if args.ledger_opening is not None else None
    print('仅查询账户、订单与成交；不下单、不撤单、不转账。凭证仅保留在本进程内存。')
    key=getpass.getpass('Kraken API public key（不回显）: ')
    secret=getpass.getpass('Kraken API secret（不回显）: ')
    client=ReadOnlyClient(key,secret,root);key=secret=None
    result=run_session(client,root,cycles=args.cycles,poll_seconds=args.poll_seconds,
        on_report=lambda event:print(json.dumps(event),flush=True),track_known_orders=args.track_orders,
        ledger_opening=opening)
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
