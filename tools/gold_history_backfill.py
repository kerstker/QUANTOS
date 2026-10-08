"""Authorized read-only history import, isolated from the live event journal."""
import argparse
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
import uuid

from services.research.gold_trade_observation import _require,load_document,normalize_binance_trades
from services.research.gold_account_history import normalize_income,normalize_orders
from tools.gold_account_history import fetch_window,signed_reader,HistoryRequestFailure
from tools.gold_account_monitor import atomic_json
from tools.gold_account_capture import collect
from tools.gold_credentials import get_credentials
from tools.gold_trade_observation import _write_private,_encode

KINDS={'fills':'/fapi/v1/userTrades','income':'/fapi/v1/income','orders':'/fapi/v1/allOrders','algos':'/fapi/v1/allAlgoOrders'}
DAY=86400000


def envelope(account_ref,start,end,at,rows,native=False):
    return dict(schema='GOLD_BINANCE_USER_TRADES_V1' if native else 'GOLD_EXTERNAL_FILLS_V1',venue='BINANCE',
                environment='MAINNET',symbol='XAUUSDT',account_ref=account_ref,start_ms=start,end_ms=end,exported_at_ms=at,fills=rows)


def backfill(root,*,account_ref,start_ms,end_ms,reader_factory,clock_ms=None,publish=None):
    clock_ms=clock_ms or (lambda:time.time_ns()//1000000);publish=publish or (lambda d:None)
    _require(0<=start_ms<end_ms<=clock_ms() and end_ms-start_ms<=90*DAY,'INVALID_BACKFILL_RANGE')
    root=Path(root).absolute();_require(root.resolve()==root,'LINKED_ARCHIVE')
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    _require(root.stat().st_mode&0o777==0o700,'PRIVATE_ARCHIVE_REQUIRED')
    tasks=root/'tasks';tasks.mkdir(mode=0o700,exist_ok=True);_require(not tasks.is_symlink(),'LINKED_ARCHIVE')
    plan={'account_ref':account_ref,'start_ms':start_ms,'end_ms':end_ms}
    index_path=root/'index.json'
    if index_path.exists():
        index=load_document(index_path.read_bytes());_require(index['plan']==plan,'BACKFILL_IDENTITY_MISMATCH')
    else:index={'plan':plan,'completed':{}}
    windows=[];hi=end_ms
    while hi>start_ms:
        lo=max(start_ms,hi-7*DAY);windows.append((lo,hi));hi=lo
    cache={}
    for key,ref in index['completed'].items():
        name=ref['file'];_require(Path(name).name==name,'INVALID_ARCHIVE_PATH')
        path=tasks/name;_require(not path.is_symlink(),'LINKED_ARCHIVE')
        raw=path.read_bytes();_require(hashlib.sha256(raw).hexdigest()==ref['sha256'],'ARCHIVE_HASH_MISMATCH')
        cache[key]=load_document(raw)
    failures=[]
    def summary():
        fills={};income={};orders={};algos={};unqueried=[]
        for key,value in cache.items():
            if value.get('query_start_ms',value['start_ms'])>value['start_ms']:
                unqueried.append(dict(kind=value['kind'],start_ms=value['start_ms'],end_ms=value['query_start_ms'],reason='RETENTION_BOUNDARY'))
            kind=value['kind'];target={'fills':fills,'income':income,'orders':orders,'algos':algos}[kind]
            for row in value['rows']:
                ident=row['trade_id'] if kind=='fills' else (row['incomeType'],row['tranId']) if kind=='income' else row['orderId' if kind=='orders' else 'algoId']
                if kind in ('fills','income'):_require(ident not in target or target[ident]==row,'CONFLICTING_ARCHIVE_RECORD')
                # Orders are state observations; retain the latest update, not inferred fills.
                if ident not in target or kind in ('fills','income') or row['updateTime']>=target[ident]['updateTime']:target[ident]=row
            _require(len(target)<=20000,'BACKFILL_ROW_BUDGET')
        return dict(schema='GOLD_ACCOUNT_BACKFILL_V1',status='QUERIED' if len(cache)==len(windows)*4 else 'PARTIAL',
            start_ms=start_ms,end_ms=end_ms,observed_at_ms=clock_ms(),completed_tasks=len(cache),total_tasks=len(windows)*4,
            fills=envelope(account_ref,start_ms,end_ms,clock_ms(),sorted(fills.values(),key=lambda r:(r['time_ms'],r['trade_id']))),
            income=list(income.values()),orders=list(orders.values()),algos=list(algos.values()),failures=failures,unqueried_ranges=unqueried,
            history_complete=False,order_lifecycle_complete=False,net_pnl=None,submission_enabled=False)
    for lo,hi in windows:
        for kind,path in KINDS.items():
            key=f'{lo}-{hi}-{kind}'
            if key in cache:continue
            try:
                read=reader_factory();query_lo=lo
                for attempt in range(2):
                    passes=[];proofs=[]
                    try:
                        for _ in range(2):
                            rows,pages=fetch_window(read,path,query_lo,hi);proofs.extend(pages)
                            if kind=='fills':rows=normalize_binance_trades(envelope(account_ref,query_lo,hi,clock_ms(),rows,True))['fills'];rows=sorted({r['trade_id']:r for r in rows}.values(),key=lambda r:r['trade_id'])
                            elif kind=='income':rows=normalize_income(rows,start_ms=query_lo,end_ms=hi)
                            else:rows=normalize_orders(rows,algo=kind=='algos')
                            passes.append(rows)
                        break
                    except HistoryRequestFailure as exc:
                        if attempt or kind not in ('orders','algos') or exc.diagnostic.get('exchange_code')!=-4166:raise
                        query_lo=max(lo,clock_ms()-90*DAY+60000)
                        _require(lo<query_lo<hi,'RETENTION_WINDOW_UNAVAILABLE')
                _require(passes[0]==passes[1],'HISTORY_CHANGED_DURING_READ')
                value={'kind':kind,'start_ms':lo,'end_ms':hi,'query_start_ms':query_lo,'rows':passes[-1],'pages':proofs}
                raw=_encode(value);_require(len(raw)<=8000000,'BACKFILL_FILE_BUDGET')
                name=key+'-'+uuid.uuid4().hex+'.json';_write_private(tasks/name,raw)
                index['completed'][key]={'file':name,'sha256':hashlib.sha256(raw).hexdigest()}
                atomic_json(index_path,index);cache[key]=value
            except (ValueError,KeyError,TypeError,OSError) as exc:
                reason='HISTORY_CHANGED_DURING_READ' if str(exc)=='HISTORY_CHANGED_DURING_READ' else 'HISTORY_READ_FAILED'
                failure={'kind':kind,'start_ms':lo,'end_ms':hi,'reason':reason}
                if isinstance(exc,HistoryRequestFailure):failure['request_error']=exc.diagnostic
                failures.append(failure)
                _write_private(root/('failure-'+uuid.uuid4().hex+'.json'),_encode(failure))
            result=summary();publish(result)
    result=summary();publish(result);return result


def run(state):
    state=Path(state).absolute();_require(state.resolve()==state and state.is_dir(),'INVALID_STATE_ROOT')
    root=state/'history-backfill';root.mkdir(mode=0o700,exist_ok=True);_require(not root.is_symlink(),'LINKED_ARCHIVE')
    descriptor=os.open(root/'writer.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        key,secret=get_credentials(prompt=lambda:(_ for _ in ()).throw(ValueError('CREDENTIAL_FILE_UNAVAILABLE')))
        identity=load_document((state/'identity.json').read_bytes())
        expected=hmac.new(key.encode(),identity['salt'].encode(),hashlib.sha256).hexdigest()
        _require(hmac.compare_digest(expected,identity['key_binding']),'ACCOUNT_BINDING_MISMATCH')
        preflight=root/('preflight-'+uuid.uuid4().hex);preflight.mkdir(mode=0o700)
        report=collect(preflight,api_key=key,api_secret=secret)
        if report['status']!='STABLE_OBSERVATIONS':
            print(json.dumps({'status':'PREFLIGHT_INCOMPLETE','reason':report.get('reason'),'request_error':report.get('request_error')}),flush=True);return 75
        if (root/'index.json').exists():plan=load_document((root/'index.json').read_bytes())['plan']
        else:
            end=time.time_ns()//1000000-10000;plan={'start_ms':end-90*DAY,'end_ms':end,'account_ref':identity['account_ref']}
            atomic_json(root/'index.json',{'plan':plan,'completed':{}})
        _require(plan['account_ref']==identity['account_ref'],'ACCOUNT_BINDING_MISMATCH')
        def publish(value):
            raw=_encode(value);_require(len(raw)<=8000000,'BACKFILL_FILE_BUDGET')
            atomic_json(state/'account-history.json',value)
            print(json.dumps({k:value[k] for k in ('status','completed_tasks','total_tasks','submission_enabled')}),flush=True)
        result=backfill(root,**plan,reader_factory=lambda:signed_reader(key,secret),publish=publish)
        return 0 if result['status']=='QUERIED' else 75
    finally:os.close(descriptor)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',action='store_true');parser.add_argument('--state-dir',type=Path)
    args=parser.parse_args()
    if not args.run:print(json.dumps({'status':'OFFLINE_DEFAULT','submission_enabled':False}));return 0
    if args.state_dir is None:parser.error('--state-dir required')
    try:return run(args.state_dir)
    except Exception:print(json.dumps({'status':'BACKFILL_VALIDATION_FAILED','submission_enabled':False}));return 78

if __name__=='__main__':raise SystemExit(main())
